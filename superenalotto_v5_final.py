#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SUPERNALOTTO STATISTICAL ENGINE
V5.5
STRICT HISTORICAL DATA ACQUISITION + INTEGRITY AUDIT

FASE ATTUALE:
- acquisizione storico
- validazione dati
- export CSV
- report JSON

ENGINE STATISTICO:
DISABILITATO

Nessuna previsione.
Nessuna generazione di combinazioni.
Nessun test statistico in questa fase.
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

VERSION = "V5.5"

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

def normalize_unicode(text: str) -> str:
    """
    Normalizza Unicode senza alterare il contenuto numerico.
    """

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

    return text


def normalize_text(text: str) -> str:
    """
    Trasforma l'intero documento in una sequenza testuale
    uniforme.

    Questo evita di dipendere dalla struttura dei tag HTML.
    """

    text = normalize_unicode(text)

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


# ============================================================================
# HTML -> TESTO
# ============================================================================

class FlatTextParser(HTMLParser):
    """
    Converte l'HTML in testo piatto.

    A differenza delle versioni precedenti NON tenta di ricostruire
    la struttura delle righe HTML.

    Ogni blocco testuale viene separato da uno spazio.
    """

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
        tag = tag.lower()

        if tag in self.SKIP_TAGS:
            self.skip_depth += 1

    def handle_endtag(
        self,
        tag,
    ):
        tag = tag.lower()

        if tag in self.SKIP_TAGS:
            if self.skip_depth > 0:
                self.skip_depth -= 1

    def handle_data(
        self,
        data,
    ):
        if self.skip_depth == 0:
            if data and data.strip():
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
        "(KHTML, like Gecko) "
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

# Formato storico:
#
# Concorso n. 95 31/12/1997
# 34 36 71 76 86 89
#
# Dopo la normalizzazione HTML non importa più
# quanti tag siano presenti tra i singoli elementi.
HISTORICAL_RE = re.compile(
    r"""
    Concorso
    \s+
    n
    \.?
    \s*
    (\d+)
    \s+
    (\d{1,2}/\d{1,2}/\d{4})
    \s+
    (
        (?:\d{1,2}\s+){5}
        \d{1,2}
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Formato moderno:
#
# SuperEnalotto 31/12/2016
# 12 14 18 22 28 33
#
MODERN_RE = re.compile(
    r"""
    SuperEnalotto
    \s+
    (\d{1,2}/\d{1,2}/\d{4})
    \s+
    (
        (?:\d{1,2}\s+){5}
        \d{1,2}
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


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
            f"Data non valida: {date_text}"
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
    year: int,
    contest: int | None,
    date_text: str,
) -> tuple[int, ...]:

    tokens = re.findall(
        r"(?<!\d)\d{1,2}(?!\d)",
        numbers_text,
    )

    if len(tokens) != MAIN_NUMBERS:

        raise RuntimeError(
            f"Attesi 6 numeri principali, "
            f"trovati {len(tokens)}: "
            f"{numbers_text!r}"
        )

    numbers = tuple(
        int(x)
        for x in tokens
    )

    if any(
        n < MIN_NUMBER or
        n > MAX_NUMBER
        for n in numbers
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
# CONTEGGIO DICHIARATO
# ============================================================================

def get_declared_count(
    text: str,
    year: int,
) -> int:

    matches = list(
        DECLARED_COUNT_RE.finditer(
            text
        )
    )

    matches = [
        m
        for m in matches
        if int(m.group(2)) == year
    ]

    if not matches:

        raise RuntimeError(
            f"Anno {year}: conteggio "
            f"'X estrazioni nel {year}' "
            f"non trovato."
        )

    counts = {
        int(m.group(1))
        for m in matches
    }

    if len(counts) != 1:

        raise RuntimeError(
            f"Anno {year}: conteggi "
            f"contraddittori nella pagina: "
            f"{sorted(counts)}"
        )

    return counts.pop()


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

    # ------------------------------------------------------------------------
    # 1. CONTEGGIO DICHIARATO
    # ------------------------------------------------------------------------

    declared_count = get_declared_count(
        text,
        year,
    )

    # ------------------------------------------------------------------------
    # 2. PARSING FORMATO STORICO
    # ------------------------------------------------------------------------

    historical_matches = list(
        HISTORICAL_RE.finditer(
            text
        )
    )

    # ------------------------------------------------------------------------
    # 3. PARSING FORMATO MODERNO
    # ------------------------------------------------------------------------

    modern_matches = list(
        MODERN_RE.finditer(
            text
        )
    )

    # ------------------------------------------------------------------------
    # IMPORTANTE:
    #
    # Se entrambi i parser trovano record, non scegliamo arbitrariamente.
    # La pagina sarebbe ambigua.
    # ------------------------------------------------------------------------

    if (
        historical_matches and
        modern_matches
    ):

        raise RuntimeError(
            f"Anno {year}: "
            "rilevati contemporaneamente "
            "formato storico e formato moderno."
        )

    records = []

    # =========================================================================
    # FORMATO STORICO
    # =========================================================================

    if historical_matches:

        for match in historical_matches:

            contest = int(
                match.group(1)
            )

            date_text = match.group(2)

            numbers_text = match.group(3)

            date_iso = validate_date(
                date_text,
                year,
            )

            numbers = parse_numbers(
                numbers_text,
                year,
                contest,
                date_text,
            )

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

        parser_mode = "CONCORSO"

    # =========================================================================
    # FORMATO MODERNO
    # =========================================================================

    elif modern_matches:

        temp_records = []

        for match in modern_matches:

            date_text = match.group(1)

            numbers_text = match.group(2)

            date_iso = validate_date(
                date_text,
                year,
            )

            numbers = parse_numbers(
                numbers_text,
                year,
                None,
                date_text,
            )

            temp_records.append({
                "year": year,
                "data": date_iso,
                "concorso": None,
                "n1": numbers[0],
                "n2": numbers[1],
                "n3": numbers[2],
                "n4": numbers[3],
                "n5": numbers[4],
                "n6": numbers[5],
            })

        # La pagina è in ordine cronologico inverso.
        # Il primo record è quindi l'ultimo concorso dell'anno.
        #
        # NON assumiamo 157.
        # Usiamo esclusivamente il conteggio dichiarato dalla pagina.

        if len(temp_records) != declared_count:

            raise RuntimeError(
                f"Anno {year}: formato moderno "
                f"ha prodotto "
                f"{len(temp_records)} record, "
                f"ma la pagina dichiara "
                f"{declared_count}."
            )

        for index, record in enumerate(
            temp_records
        ):

            record["concorso"] = (
                declared_count - index
            )

        records = temp_records

        parser_mode = "MODERNO"

    # =========================================================================
    # NESSUN PARSER
    # =========================================================================

    else:

        # Diagnostica utile senza stampare l'intera pagina.
        sample = text[:1000]

        raise RuntimeError(
            f"Anno {year}: "
            "nessun record riconosciuto. "
            f"Anteprima testo: {sample!r}"
        )

    # =========================================================================
    # CONTEGGIO
    # =========================================================================

    if len(records) != declared_count:

        raise RuntimeError(
            f"Anno {year}: "
            f"{len(records)} record parsati "
            f"contro {declared_count} dichiarati."
        )

    # =========================================================================
    # DATAFRAME
    # =========================================================================

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

    # =========================================================================
    # DUPLICATI
    # =========================================================================

    if df.duplicated(
        subset=[
            "year",
            "concorso",
        ],
        keep=False,
    ).any():

        raise RuntimeError(
            f"Anno {year}: "
            "concorso duplicato."
        )

    # =========================================================================
    # CONTROLLO DATE
    # =========================================================================

    dates = pd.to_datetime(
        df["data"],
        errors="raise",
    )

    if not (
        dates.dt.year == year
    ).all():

        raise RuntimeError(
            f"Anno {year}: "
            "data fuori anno."
        )

    # La fonte presenta le estrazioni dalla più recente
    # alla più vecchia.
    if not dates.is_monotonic_decreasing:

        raise RuntimeError(
            f"Anno {year}: "
            "ordine delle date inatteso."
        )

    # =========================================================================
    # CONTROLLO CONCORSI
    # =========================================================================

    contests = (
        df["concorso"]
        .astype(int)
        .tolist()
    )

    if parser_mode == "CONCORSO":

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
                "sequenza concorsi non consecutiva."
            )

        # Caso storico iniziale.
        if year == 1997:

            expected_1997 = list(
                range(95, 86, -1)
            )

            if contests != expected_1997:

                raise RuntimeError(
                    "Anno 1997: "
                    f"sequenza inattesa: "
                    f"{contests}"
                )

    else:

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
                "numerazione ricostruita "
                "non consecutiva."
            )

    # =========================================================================
    # CONTROLLO NUMERI
    # =========================================================================

    number_columns = [
        "n1",
        "n2",
        "n3",
        "n4",
        "n5",
        "n6",
    ]

    for idx, row in df.iterrows():

        nums = [
            int(row[col])
            for col in number_columns
        ]

        if len(set(nums)) != 6:

            raise RuntimeError(
                f"Anno {year}, "
                f"concorso {row['concorso']}: "
                f"numeri duplicati {nums}"
            )

        if not all(
            MIN_NUMBER <= n <= MAX_NUMBER
            for n in nums
        ):

            raise RuntimeError(
                f"Anno {year}, "
                f"concorso {row['concorso']}: "
                f"numero fuori 1-90: {nums}"
            )

    # =========================================================================
    # LOG
    # =========================================================================

    log(
        f"Anno {year}: "
        f"{len(df)} estrazioni valide; "
        f"concorsi "
        f"{int(df['concorso'].max())}->"
        f"{int(df['concorso'].min())}; "
        f"parser={parser_mode}."
    )

    metadata = {
        "year": year,
        "declared_count": declared_count,
        "parsed_count": len(df),
        "parser": parser_mode,
        "first_contest": int(
            df["concorso"].max()
        ),
        "last_contest": int(
            df["concorso"].min()
        ),
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

    # =========================================================================
    # TIPI
    # =========================================================================

    df["year"] = df["year"].astype(int)

    df["concorso"] = (
        df["concorso"]
        .astype(int)
    )

    for col in [
        "n1",
        "n2",
        "n3",
        "n4",
        "n5",
        "n6",
    ]:

        df[col] = (
            df[col]
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

    # =========================================================================
    # ORDINAMENTO GLOBALE
    # =========================================================================

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

    # ------------------------------------------------------------------------
    # SCHEMA
    # ------------------------------------------------------------------------

    if list(df.columns) != expected_columns:

        errors.append(
            "Schema colonne non conforme."
        )

    # ------------------------------------------------------------------------
    # DUPLICATI COMPLETI
    # ------------------------------------------------------------------------

    if df.duplicated().any():

        errors.append(
            "Righe completamente duplicate."
        )

    # ------------------------------------------------------------------------
    # CHIAVE PRIMARIA
    # ------------------------------------------------------------------------

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

    # ------------------------------------------------------------------------
    # ANNI
    # ------------------------------------------------------------------------

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

    # ------------------------------------------------------------------------
    # NUMERI
    # ------------------------------------------------------------------------

    number_columns = [
        "n1",
        "n2",
        "n3",
        "n4",
        "n5",
        "n6",
    ]

    for col in number_columns:

        values = pd.to_numeric(
            df[col],
            errors="coerce",
        )

        if values.isna().any():

            errors.append(
                f"{col}: valori mancanti "
                "o non numerici."
            )

        elif not values.between(
            MIN_NUMBER,
            MAX_NUMBER,
        ).all():

            errors.append(
                f"{col}: valori fuori "
                "intervallo 1-90."
            )

    # ------------------------------------------------------------------------
    # SEI NUMERI DISTINTI
    # ------------------------------------------------------------------------

    for idx, row in df.iterrows():

        nums = [
            int(row[col])
            for col in number_columns
        ]

        if len(set(nums)) != 6:

            errors.append(
                f"Riga {idx}: "
                f"numeri duplicati {nums}."
            )

    # ------------------------------------------------------------------------
    # DATE
    # ------------------------------------------------------------------------

    dates = pd.to_datetime(
        df["data"],
        errors="coerce",
    )

    if dates.isna().any():

        errors.append(
            "Date non valide."
        )

    # ------------------------------------------------------------------------
    # METADATI
    # ------------------------------------------------------------------------

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
    status: str,
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
        f"Report di integrità generato in "
        f"'{REPORT_JSON}'."
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

        # ====================================================================
        # ACQUISIZIONE
        # ====================================================================

        df, metadata = (
            download_and_build_archive()
        )

        log(
            f"Acquisizione completata: "
            f"{len(metadata)} anni, "
            f"{len(df)} estrazioni."
        )

        # ====================================================================
        # AUDIT
        # ====================================================================

        errors.extend(
            audit_global(
                df,
                metadata,
            )
        )

        # ====================================================================
        # FAIL CLOSED
        # ====================================================================

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
                "AUDIT FAILED — ARCHIVIO NON UTILIZZABILE"
            )

            for error in errors:

                log(
                    f"ERROR: {error}"
                )

            log(
                "=================================================="
            )

            return 1

        # ====================================================================
        # EXPORT
        # ====================================================================

        export_csv(
            df
        )

        # ====================================================================
        # PASS
        # ====================================================================

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
            "Chiavi (anno, concorso) verificate."
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


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    sys.exit(
        main()
    )
