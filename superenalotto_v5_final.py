#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SUPERNALOTTO STATISTICAL ENGINE V5.7

STRICT HISTORICAL DATA ACQUISITION + INTEGRITY AUDIT

Engine statistico DISABILITATO.

La pipeline esegue esclusivamente:
- acquisizione storico
- validazione
- ricostruzione deterministica dei concorsi mancanti
- audit globale
- export CSV
- report JSON

NESSUNA previsione.
NESSUNA generazione di combinazioni.
NESSUN test statistico.
"""

from __future__ import annotations

import json
import re
import sys
import time
import unicodedata
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

import pandas as pd
import requests


# ============================================================================
# CONFIGURAZIONE
# ============================================================================

VERSION = "V5.7"

SOURCE_URL = (
    "https://www.estrazioni.it/superenalotto/?anno={year}"
)

START_YEAR = 1997
END_YEAR = datetime.now().year

OUTPUT_CSV = "superenalotto_history.csv"
REPORT_JSON = "integrity_report.json"

REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_SLEEP = 2

ENGINE_ENABLED = False

MAIN_NUMBERS = 6
MIN_NUMBER = 1
MAX_NUMBER = 90


# ============================================================================
# LOG
# ============================================================================

def log(message: str) -> None:
    stamp = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    print(
        f"[{stamp}] {message}",
        flush=True,
    )


# ============================================================================
# NORMALIZZAZIONE
# ============================================================================

def normalize_text(text: str) -> str:
    text = unicodedata.normalize(
        "NFKC",
        text,
    )

    replacements = {
        "\xa0": " ",
        "\u200b": "",
        "\u200c": "",
        "\u200d": "",
        "\ufeff": "",
        "№": "n.",
        "º": ".",
        "°": ".",
        "ª": ".",
    }

    for old, new in replacements.items():
        text = text.replace(
            old,
            new,
        )

    return re.sub(
        r"\s+",
        " ",
        text,
    ).strip()


# ============================================================================
# HTML -> TESTO
# ============================================================================

class FlatTextParser(HTMLParser):

    SKIP_TAGS = {
        "script",
        "style",
        "noscript",
        "svg",
        "template",
    }

    def __init__(self) -> None:
        super().__init__(
            convert_charrefs=True
        )

        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(
        self,
        tag,
        attrs,
    ):
        if tag.lower() in self.SKIP_TAGS:
            self.skip_depth += 1

    def handle_endtag(
        self,
        tag,
    ):
        if (
            tag.lower() in self.SKIP_TAGS
            and self.skip_depth > 0
        ):
            self.skip_depth -= 1

    def handle_data(
        self,
        data,
    ):
        if (
            self.skip_depth == 0
            and data
            and data.strip()
        ):
            self.parts.append(data)

    def get_text(self) -> str:
        return normalize_text(
            " ".join(self.parts)
        )


def html_to_text(
    html: str,
) -> str:

    parser = FlatTextParser()

    parser.feed(html)
    parser.close()

    return parser.get_text()


# ============================================================================
# HTTP
# ============================================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": (
        "it-IT,it;q=0.9,en;q=0.8"
    ),
})


def fetch_year(
    year: int,
) -> str:

    url = SOURCE_URL.format(
        year=year
    )

    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        log(
            f"WEB FETCH {year} "
            f"(tentativo {attempt}/{MAX_RETRIES})"
        )

        try:

            response = SESSION.get(
                url,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            if len(response.text) < 1000:

                raise RuntimeError(
                    "HTML ricevuto troppo corto: "
                    f"{len(response.text)} byte"
                )

            return response.text

        except Exception as exc:

            last_error = exc

            log(
                f"WARNING: fetch {year} fallito: "
                f"{exc}"
            )

            if attempt < MAX_RETRIES:
                time.sleep(
                    RETRY_SLEEP
                )

    raise RuntimeError(
        f"Impossibile scaricare {year}: "
        f"{last_error}"
    )


# ============================================================================
# PATTERN
# ============================================================================

DECLARED_COUNT_RE = re.compile(
    r"\b(\d+)\s+estrazioni\s+nel\s+(\d{4})\b",
    re.IGNORECASE,
)


DRAW_RE = re.compile(
    r"""
    (?:
        Concorso
        \s+
        n
        \.?
        \s*
        (?P<contest>\d+)
        \s+
    )?

    (?:
        SuperEnalotto
        \s+
    )?

    (?P<date>
        \d{1,2}/\d{1,2}/\d{4}
    )

    \s+

    (?P<numbers>
        (?:\d{1,2}\s+){5}
        \d{1,2}
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


# ============================================================================
# CONTEGGIO DICHIARATO
# ============================================================================

def get_declared_count(
    text: str,
    year: int,
) -> int:

    counts = {
        int(match.group(1))
        for match in DECLARED_COUNT_RE.finditer(
            text
        )
        if int(match.group(2)) == year
    }

    if not counts:

        raise RuntimeError(
            f"Anno {year}: "
            f"conteggio "
            f"'X estrazioni nel {year}' "
            "non trovato."
        )

    if len(counts) != 1:

        raise RuntimeError(
            f"Anno {year}: "
            "conteggi contraddittori: "
            f"{sorted(counts)}"
        )

    return counts.pop()


# ============================================================================
# VALIDAZIONE DATA
# ============================================================================

def validate_date(
    date_text: str,
    expected_year: int,
) -> str:

    try:

        dt = datetime.strptime(
            date_text,
            "%d/%m/%Y",
        )

    except ValueError as exc:

        raise RuntimeError(
            f"Data non valida: "
            f"{date_text}"
        ) from exc

    if dt.year != expected_year:

        raise RuntimeError(
            f"Data {date_text} "
            f"non appartiene all'anno "
            f"{expected_year}."
        )

    return dt.strftime(
        "%Y-%m-%d"
    )


# ============================================================================
# VALIDAZIONE NUMERI
# ============================================================================

def parse_numbers(
    numbers_text: str,
) -> tuple[int, ...]:

    tokens = re.findall(
        r"(?<!\d)\d{1,2}(?!\d)",
        numbers_text,
    )

    if len(tokens) != MAIN_NUMBERS:

        raise RuntimeError(
            "Attesi 6 numeri principali, "
            f"trovati {len(tokens)}: "
            f"{numbers_text!r}"
        )

    numbers = tuple(
        int(token)
        for token in tokens
    )

    if any(
        number < MIN_NUMBER
        or number > MAX_NUMBER
        for number in numbers
    ):

        raise RuntimeError(
            f"Numero fuori intervallo 1-90: "
            f"{numbers}"
        )

    if len(set(numbers)) != MAIN_NUMBERS:

        raise RuntimeError(
            f"Numeri principali duplicati: "
            f"{numbers}"
        )

    return numbers


# ============================================================================
# RICOSTRUZIONE CONCORSI
# ============================================================================

def reconstruct_contests(
    records: list[dict],
    year: int,
    declared_count: int,
) -> None:
    """
    Ricostruzione deterministica.

    CASO A — formato moderno puro
    --------------------------------
    Nessun numero di concorso esplicito.

    Se la pagina dichiara N estrazioni e abbiamo
    esattamente N record, la sequenza viene ricostruita:

        N, N-1, ..., 1

    CASO B — formato misto
    -----------------------
    Esistono alcuni concorsi espliciti.

    Gli anchor espliciti vengono usati per ricostruire
    esclusivamente i record mancanti.

    La distanza tra posizione e concorso deve essere
    perfettamente coerente.

    Nessun numero viene inventato arbitrariamente.
    """

    if not records:

        raise RuntimeError(
            f"Anno {year}: nessun record."
        )

    known = [
        (
            index,
            int(record["concorso"]),
        )
        for index, record in enumerate(records)
        if record["concorso"] is not None
    ]

    # ------------------------------------------------------------------
    # CASO A: NESSUN CONCORSO ESPLICITO
    # ------------------------------------------------------------------

    if not known:

        for index, record in enumerate(
            records
        ):

            record["concorso"] = (
                declared_count - index
            )

        contests = [
            int(record["concorso"])
            for record in records
        ]

        expected = list(
            range(
                declared_count,
                0,
                -1,
            )
        )

        if contests != expected:

            raise RuntimeError(
                f"Anno {year}: "
                "numerazione moderna "
                "ricostruita incoerente."
            )

        return

    # ------------------------------------------------------------------
    # CASO B: ANCHOR ESPLICITI
    # ------------------------------------------------------------------

    for (
        (index_a, contest_a),
        (index_b, contest_b),
    ) in zip(
        known,
        known[1:],
    ):

        record_distance = (
            index_b - index_a
        )

        contest_distance = (
            contest_a - contest_b
        )

        if (
            contest_distance
            != record_distance
        ):

            raise RuntimeError(
                f"Anno {year}: "
                "incoerenza tra concorsi "
                "espliciti: "
                f"posizioni "
                f"{index_a}/{index_b}, "
                f"concorsi "
                f"{contest_a}/{contest_b}."
            )

    # ------------------------------------------------------------------
    # PRIMA DEL PRIMO ANCHOR
    # ------------------------------------------------------------------

    first_index, first_contest = known[0]

    for index in range(
        first_index - 1,
        -1,
        -1,
    ):

        records[index]["concorso"] = (
            first_contest
            + (first_index - index)
        )

    # ------------------------------------------------------------------
    # TRA GLI ANCHOR
    # ------------------------------------------------------------------

    for (
        (index_a, contest_a),
        (index_b, contest_b),
    ) in zip(
        known,
        known[1:],
    ):

        for index in range(
            index_a + 1,
            index_b,
        ):

            records[index]["concorso"] = (
                contest_a
                - (index - index_a)
            )

    # ------------------------------------------------------------------
    # DOPO L'ULTIMO ANCHOR
    # ------------------------------------------------------------------

    last_index, last_contest = known[-1]

    for index in range(
        last_index + 1,
        len(records),
    ):

        records[index]["concorso"] = (
            last_contest
            - (index - last_index)
        )

    # ------------------------------------------------------------------
    # CONTROLLO FINALE
    # ------------------------------------------------------------------

    contests = [
        int(record["concorso"])
        for record in records
    ]

    if any(
        contest is None
        for contest in contests
    ):

        raise RuntimeError(
            f"Anno {year}: "
            "impossibile ricostruire "
            "tutti i concorsi."
        )

    expected = list(
        range(
            contests[0],
            contests[-1] - 1,
            -1,
        )
    )

    if contests != expected:

        raise RuntimeError(
            f"Anno {year}: "
            "sequenza concorsi "
            "ricostruita non consecutiva."
        )


# ============================================================================
# PARSER ANNUALE
# ============================================================================

def parse_annual_page(
    html: str,
    year: int,
) -> tuple[pd.DataFrame, dict]:

    text = html_to_text(
        html
    )

    if not text:

        raise RuntimeError(
            f"Anno {year}: "
            "testo della pagina vuoto."
        )

    declared_count = get_declared_count(
        text,
        year,
    )

    matches = list(
        DRAW_RE.finditer(
            text
        )
    )

    if not matches:

        raise RuntimeError(
            f"Anno {year}: "
            "nessuna estrazione "
            "riconosciuta. "
            f"Anteprima: "
            f"{text[:1000]!r}"
        )

    records = []

    explicit_count = 0
    implicit_count = 0

    for match in matches:

        contest_text = match.group(
            "contest"
        )

        date_text = match.group(
            "date"
        )

        numbers_text = match.group(
            "numbers"
        )

        date_iso = validate_date(
            date_text,
            year,
        )

        numbers = parse_numbers(
            numbers_text
        )

        if contest_text is None:

            contest = None
            implicit_count += 1

        else:

            contest = int(
                contest_text
            )
            explicit_count += 1

        records.append({
            "year": year,
            "data": date_iso,
            "concorso": contest,
            "n1": numbers[0],
            "n2": numbers[1],
            "n3": numbers[2],
            "n4": numbers[3],
            "n5": numbers[4],
            "n6": numbers[5],
        })

    # ------------------------------------------------------------------
    # CONTEGGIO RECORD
    # ------------------------------------------------------------------

    if len(records) != declared_count:

        raise RuntimeError(
            f"Anno {year}: "
            f"{len(records)} record "
            "riconosciuti contro "
            f"{declared_count} "
            "estrazioni dichiarate."
        )

    # ------------------------------------------------------------------
    # DATE
    # ------------------------------------------------------------------

    dates = pd.to_datetime(
        [
            record["data"]
            for record in records
        ],
        errors="raise",
    )

    if not dates.is_monotonic_decreasing:

        raise RuntimeError(
            f"Anno {year}: "
            "ordine cronologico "
            "inatteso."
        )

    # ------------------------------------------------------------------
    # RICOSTRUZIONE CONCORSI
    # ------------------------------------------------------------------

    reconstruct_contests(
        records,
        year,
        declared_count,
    )

    contests = [
        int(record["concorso"])
        for record in records
    ]

    # ------------------------------------------------------------------
    # VALIDITÀ CONCORSI
    # ------------------------------------------------------------------

    if any(
        contest < 1
        for contest in contests
    ):

        raise RuntimeError(
            f"Anno {year}: "
            "numero di concorso "
            f"non valido: {contests}"
        )

    if len(contests) != len(
        set(contests)
    ):

        raise RuntimeError(
            f"Anno {year}: "
            "concorso duplicato."
        )

    expected = list(
        range(
            contests[0],
            contests[-1] - 1,
            -1,
        )
    )

    if contests != expected:

        raise RuntimeError(
            f"Anno {year}: "
            "sequenza concorsi "
            "non consecutiva."
        )

    # ------------------------------------------------------------------
    # CONTROLLO SPECIFICO 1997
    # ------------------------------------------------------------------

    if year == 1997:

        expected_1997 = list(
            range(
                95,
                86,
                -1,
            )
        )

        if contests != expected_1997:

            raise RuntimeError(
                f"Anno 1997: "
                f"sequenza inattesa: "
                f"{contests}"
            )

    # ------------------------------------------------------------------
    # DATAFRAME
    # ------------------------------------------------------------------

    df = pd.DataFrame(
        records,
        columns=[
            "year",
            "data",
            "concorso",
            "n1",
            "n2",
            "n3",
            "n4",
            "n5",
            "n6",
        ],
    )

    # ------------------------------------------------------------------
    # CONTROLLO NUMERI
    # ------------------------------------------------------------------

    number_columns = [
        "n1",
        "n2",
        "n3",
        "n4",
        "n5",
        "n6",
    ]

    for _, row in df.iterrows():

        numbers = [
            int(row[column])
            for column in number_columns
        ]

        if len(set(numbers)) != 6:

            raise RuntimeError(
                f"Anno {year}, "
                f"concorso "
                f"{row['concorso']}: "
                f"numeri duplicati "
                f"{numbers}"
            )

        if not all(
            MIN_NUMBER <= number <= MAX_NUMBER
            for number in numbers
        ):

            raise RuntimeError(
                f"Anno {year}, "
                f"concorso "
                f"{row['concorso']}: "
                f"numero fuori 1-90: "
                f"{numbers}"
            )

    # ------------------------------------------------------------------
    # MODALITÀ
    # ------------------------------------------------------------------

    if implicit_count == 0:

        parser_mode = "ESPLICITO"

    elif explicit_count == 0:

        parser_mode = "MODERNO"

    else:

        parser_mode = "MISTO"

    # ------------------------------------------------------------------
    # LOG
    # ------------------------------------------------------------------

    log(
        f"Anno {year}: "
        f"{len(df)} estrazioni valide; "
        f"concorsi "
        f"{contests[0]}->"
        f"{contests[-1]}; "
        f"parser={parser_mode}; "
        f"espliciti={explicit_count}; "
        f"ricostruiti={implicit_count}."
    )

    metadata = {
        "year": year,
        "declared_count": declared_count,
        "parsed_count": len(df),
        "parser": parser_mode,
        "explicit_contests": explicit_count,
        "reconstructed_contests": implicit_count,
        "first_contest": contests[0],
        "last_contest": contests[-1],
    }

    return df, metadata


# ============================================================================
# COSTRUZIONE ARCHIVIO
# ============================================================================

def download_and_build_archive():

    frames = []
    metadata = []

    for year in range(
        START_YEAR,
        END_YEAR + 1,
    ):

        html = fetch_year(
            year
        )

        df_year, meta = parse_annual_page(
            html,
            year,
        )

        frames.append(
            df_year
        )

        metadata.append(
            meta
        )

    if not frames:

        raise RuntimeError(
            "Nessun dato acquisito."
        )

    df = pd.concat(
        frames,
        ignore_index=True,
    )

    df["year"] = (
        df["year"]
        .astype(int)
    )

    df["concorso"] = (
        df["concorso"]
        .astype(int)
    )

    for column in [
        "n1",
        "n2",
        "n3",
        "n4",
        "n5",
        "n6",
    ]:

        df[column] = (
            df[column]
            .astype(int)
        )

    df["data"] = (
        pd.to_datetime(
            df["data"],
            errors="raise",
        )
        .dt.strftime(
            "%Y-%m-%d"
        )
    )

    # CSV finale in ordine cronologico crescente.
    df = df.sort_values(
        [
            "data",
            "year",
            "concorso",
        ],
        ascending=[
            True,
            True,
            True,
        ],
    ).reset_index(
        drop=True
    )

    return df, metadata


# ============================================================================
# AUDIT GLOBALE
# ============================================================================

def audit_global(
    df: pd.DataFrame,
    metadata: list[dict],
) -> list[str]:

    errors = []

    expected_columns = [
        "year",
        "data",
        "concorso",
        "n1",
        "n2",
        "n3",
        "n4",
        "n5",
        "n6",
    ]

    if list(df.columns) != expected_columns:

        errors.append(
            "Schema colonne non conforme."
        )

    if df.duplicated().any():

        errors.append(
            "Righe completamente duplicate."
        )

    if df.duplicated(
        subset=[
            "year",
            "concorso",
        ],
        keep=False,
    ).any():

        errors.append(
            "Duplicati sulla chiave "
            "(year, concorso)."
        )

    # ------------------------------------------------------------------
    # ANNI
    # ------------------------------------------------------------------

    actual_years = set(
        df["year"].astype(int)
    )

    expected_years = set(
        range(
            START_YEAR,
            END_YEAR + 1,
        )
    )

    missing = (
        expected_years -
        actual_years
    )

    extra = (
        actual_years -
        expected_years
    )

    if missing:

        errors.append(
            f"Anni mancanti: "
            f"{sorted(missing)}"
        )

    if extra:

        errors.append(
            f"Anni inattesi: "
            f"{sorted(extra)}"
        )

    # ------------------------------------------------------------------
    # NUMERI
    # ------------------------------------------------------------------

    number_columns = [
        "n1",
        "n2",
        "n3",
        "n4",
        "n5",
        "n6",
    ]

    for column in number_columns:

        values = pd.to_numeric(
            df[column],
            errors="coerce",
        )

        if values.isna().any():

            errors.append(
                f"{column}: "
                "valori mancanti "
                "o non numerici."
            )

        elif not values.between(
            MIN_NUMBER,
            MAX_NUMBER,
        ).all():

            errors.append(
                f"{column}: "
                "valori fuori "
                "intervallo 1-90."
            )

    for index, row in df.iterrows():

        numbers = [
            int(row[column])
            for column in number_columns
        ]

        if len(set(numbers)) != 6:

            errors.append(
                f"Riga {index}: "
                f"numeri duplicati "
                f"{numbers}."
            )

    # ------------------------------------------------------------------
    # DATE
    # ------------------------------------------------------------------

    dates = pd.to_datetime(
        df["data"],
        errors="coerce",
    )

    if dates.isna().any():

        errors.append(
            "Date non valide."
        )

    # ------------------------------------------------------------------
    # CONTEGGI ANNUALI
    # ------------------------------------------------------------------

    for meta in metadata:

        if (
            meta["declared_count"]
            != meta["parsed_count"]
        ):

            errors.append(
                f"Anno {meta['year']}: "
                f"conteggio dichiarato "
                f"{meta['declared_count']} "
                f"!= parsato "
                f"{meta['parsed_count']}."
            )

    # ------------------------------------------------------------------
    # DATA ↔ ANNO
    # ------------------------------------------------------------------

    for year in sorted(
        actual_years
    ):

        subset = df[
            df["year"] == year
        ]

        date_years = (
            pd.to_datetime(
                subset["data"]
            )
            .dt.year
            .unique()
            .tolist()
        )

        if date_years != [year]:

            errors.append(
                f"Anno {year}: "
                "date appartenenti "
                f"ad anni diversi: "
                f"{date_years}"
            )

        # Nel CSV globale le date sono crescenti,
        # quindi anche i concorsi devono risultare
        # crescenti all'interno dell'anno.
        contests = (
            subset["concorso"]
            .astype(int)
            .tolist()
        )

        if contests != sorted(
            contests
        ):

            errors.append(
                f"Anno {year}: "
                "ordine globale "
                "dei concorsi non crescente."
            )

    return errors


# ============================================================================
# EXPORT
# ============================================================================

def export_csv(
    df: pd.DataFrame,
) -> None:

    columns = [
        "data",
        "concorso",
        "n1",
        "n2",
        "n3",
        "n4",
        "n5",
        "n6",
    ]

    df[columns].to_csv(
        OUTPUT_CSV,
        index=False,
        encoding="utf-8",
    )

    log(
        f"CSV generato: "
        f"{OUTPUT_CSV} "
        f"({len(df)} righe)."
    )


# ============================================================================
# REPORT
# ============================================================================

def write_report(
    status,
    df,
    metadata,
    errors,
    warnings,
) -> None:

    report = {
        "pipeline_version": VERSION,
        "generated_at": (
            datetime.now().isoformat()
        ),
        "status": status,
        "engine_enabled": ENGINE_ENABLED,
        "source": "estrazioni.it",
        "source_url_pattern": SOURCE_URL,
        "start_year": START_YEAR,
        "end_year": END_YEAR,
        "total_records": (
            int(len(df))
            if df is not None
            else 0
        ),
        "years": metadata,
        "errors": errors,
        "warnings": warnings,
    }

    Path(
        REPORT_JSON
    ).write_text(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    log(
        f"Report di integrità generato "
        f"in '{REPORT_JSON}'."
    )


# ============================================================================
# MAIN
# ============================================================================

def main() -> int:

    log(
        f"SUPERNALOTTO PIPELINE {VERSION} "
        f"— STRICT DATA INTEGRITY AUDIT"
    )

    log(
        "ENGINE DISABLED"
    )

    errors = []
    warnings = []

    df = None
    metadata = []

    try:

        df, metadata = (
            download_and_build_archive()
        )

        log(
            f"Acquisizione completata: "
            f"{len(metadata)} anni, "
            f"{len(df)} estrazioni."
        )

        errors.extend(
            audit_global(
                df,
                metadata,
            )
        )

        if errors:

            write_report(
                "FAILED",
                df,
                metadata,
                errors,
                warnings,
            )

            log(
                "=================================================="
            )

            log(
                "AUDIT FAILED — "
                "ARCHIVIO NON UTILIZZABILE"
            )

            for error in errors:

                log(
                    f"ERROR: {error}"
                )

            log(
                "=================================================="
            )

            return 1

        export_csv(
            df
        )

        write_report(
            "PASSED",
            df,
            metadata,
            errors,
            warnings,
        )

        log(
            "=================================================="
        )

        log(
            "AUDIT PASSED"
        )

        log(
            f"Record validi: "
            f"{len(df)}"
        )

        log(
            f"Periodo: "
            f"{df['data'].min()} -> "
            f"{df['data'].max()}"
        )

        log(
            "Conteggi annuali verificati."
        )

        log(
            "Date verificate."
        )

        log(
            "Numeri 1-90 verificati."
        )

        log(
            "Sei numeri distinti verificati."
        )

        log(
            "Chiavi (anno, concorso) "
            "verificate."
        )

        log(
            "Ricostruzioni deterministiche "
            "verificate."
        )

        log(
            "ENGINE DISABLED."
        )

        log(
            "=================================================="
        )

        return 0

    except Exception as exc:

        errors.append(
            f"{type(exc).__name__}: {exc}"
        )

        try:

            write_report(
                "FAILED",
                df,
                metadata,
                errors,
                warnings,
            )

        except Exception:
            pass

        log(
            "=================================================="
        )

        log(
            "AUDIT FAILED — FAIL CLOSED"
        )

        log(
            f"{type(exc).__name__}: {exc}"
        )

        log(
            "=================================================="
        )

        return 1


if __name__ == "__main__":
    sys.exit(
        main()
    )
