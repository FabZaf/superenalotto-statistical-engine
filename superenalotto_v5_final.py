#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SUPERNALOTTO STATISTICAL ENGINE
V5.4 — STRICT HISTORICAL DATA BUILDER

FASE ATTUALE
------------
Solo acquisizione + verifica integrità dello storico.

Il motore statistico è DISABILITATO.

NON:
- predice numeri
- genera giocate
- seleziona numeri futuri
- interpreta anomalie come previsioni

OBIETTIVO
---------
Costruire uno storico verificato 1997 -> anno corrente.

Fonte:
https://www.estrazioni.it/superenalotto/?anno=YYYY
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
# CONFIG
# ============================================================================

VERSION = "V5.4"

SOURCE_URL = "https://www.estrazioni.it/superenalotto/?anno={year}"

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
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}", flush=True)


# ============================================================================
# TEXT NORMALIZATION
# ============================================================================

def normalize_unicode(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)

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
        text = text.replace(old, new)

    return text


def clean_line(text: str) -> str:
    text = normalize_unicode(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ============================================================================
# HTML PARSER
# ============================================================================

class VisibleLineParser(HTMLParser):
    """
    Estrae il testo visibile mantenendo la separazione logica delle righe.

    Questo è fondamentale perché la struttura reale dell'archivio è:

        intestazione estrazione
        6 numeri principali
        Jolly / SuperStar
        intestazione estrazione
        6 numeri principali
        ...

    Non cerchiamo quindi più numeri in un blocco HTML generico.
    """

    SKIP_TAGS = {
        "script",
        "style",
        "noscript",
        "svg",
        "template",
    }

    BLOCK_TAGS = {
        "br",
        "p",
        "div",
        "li",
        "tr",
        "section",
        "article",
        "header",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()

        if tag in self.SKIP_TAGS:
            self.skip_depth += 1
            return

        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag in self.SKIP_TAGS:
            if self.skip_depth > 0:
                self.skip_depth -= 1
            return

        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.skip_depth == 0 and data:
            self.parts.append(data)

    def get_lines(self) -> list[str]:
        raw = "".join(self.parts)
        raw = normalize_unicode(raw)

        lines = []

        for raw_line in raw.splitlines():
            line = clean_line(raw_line)

            if line:
                lines.append(line)

        return lines


def html_to_lines(html: str) -> list[str]:
    parser = VisibleLineParser()
    parser.feed(html)
    parser.close()
    return parser.get_lines()


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
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
})


def fetch_year(year: int) -> str:
    url = SOURCE_URL.format(year=year)

    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):

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
                    f"HTML troppo corto: {len(response.text)} byte"
                )

            return response.text

        except Exception as exc:

            last_error = exc

            log(
                f"WARNING: fetch {year} fallito: {exc}"
            )

            if attempt < MAX_RETRIES:
                time.sleep(RETRY_SLEEP)

    raise RuntimeError(
        f"Impossibile scaricare l'anno {year}: "
        f"{last_error}"
    )


# ============================================================================
# PATTERN
# ============================================================================

DECLARED_COUNT_RE = re.compile(
    r"(\d+)\s+estrazioni\s+nel\s+(\d{4})",
    re.IGNORECASE,
)

HISTORICAL_HEADER_RE = re.compile(
    r"^Concorso\s+n\.?\s*(\d+)\s+"
    r"(\d{1,2}/\d{1,2}/\d{4})$",
    re.IGNORECASE,
)

MODERN_HEADER_RE = re.compile(
    r"^SuperEnalotto\s+"
    r"(\d{1,2}/\d{1,2}/\d{4})$",
    re.IGNORECASE,
)

NUMBER_ONLY_RE = re.compile(
    r"^\s*(\d{1,2})"
    r"(?:\s+(\d{1,2}))"
    r"(?:\s+(\d{1,2}))"
    r"(?:\s+(\d{1,2}))"
    r"(?:\s+(\d{1,2}))"
    r"(?:\s+(\d{1,2}))\s*$"
)


# ============================================================================
# VALIDATION
# ============================================================================

def validate_date(
    date_text: str,
    year: int,
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

    if dt.year != year:
        raise RuntimeError(
            f"Data {date_text} incompatibile con anno {year}"
        )

    return dt.strftime("%Y-%m-%d")


def parse_six_numbers(
    line: str,
    year: int,
    contest: int | None,
    date_text: str,
) -> tuple[int, ...]:

    match = NUMBER_ONLY_RE.fullmatch(line)

    if not match:
        raise RuntimeError(
            "La riga successiva all'intestazione "
            "non contiene esattamente 6 numeri principali: "
            f"'{line}'"
        )

    numbers = tuple(
        int(x)
        for x in match.groups()
    )

    if len(numbers) != 6:
        raise RuntimeError(
            f"Numero numeri principali inatteso: {numbers}"
        )

    if any(
        n < MIN_NUMBER or n > MAX_NUMBER
        for n in numbers
    ):
        raise RuntimeError(
            f"Numero fuori intervallo 1-90: {numbers}"
        )

    if len(set(numbers)) != 6:
        raise RuntimeError(
            f"Numeri principali duplicati: {numbers}"
        )

    return numbers


# ============================================================================
# DECLARED COUNT
# ============================================================================

def get_declared_count(
    lines: list[str],
    year: int,
) -> int:

    pattern = re.compile(
        rf"(\d+)\s+estrazioni\s+nel\s+{year}\b",
        re.IGNORECASE,
    )

    for line in lines:

        match = pattern.search(line)

        if match:
            return int(match.group(1))

    raise RuntimeError(
        f"Anno {year}: conteggio "
        f"'X estrazioni nel {year}' non trovato."
    )


# ============================================================================
# PARSER ANNUALE
# ============================================================================

def parse_annual_page(
    html: str,
    year: int,
) -> tuple[pd.DataFrame, dict]:

    lines = html_to_lines(html)

    if not lines:
        raise RuntimeError(
            f"Anno {year}: nessuna riga visibile."
        )

    declared_count = get_declared_count(
        lines,
        year,
    )

    records = []

    historical_count = 0
    modern_count = 0

    diagnostic_failures = []

    # ------------------------------------------------------------------------
    # Scansione sequenziale.
    #
    # Non cerchiamo più "sei numeri in un blocco".
    #
    # Cerchiamo:
    #
    # HEADER
    #   ↓
    # RIGA IMMEDIATAMENTE SUCCESSIVA
    #   ↓
    # ESATTAMENTE 6 NUMERI
    # ------------------------------------------------------------------------

    i = 0

    while i < len(lines):

        line = lines[i]

        historical = HISTORICAL_HEADER_RE.match(line)
        modern = MODERN_HEADER_RE.match(line)

        if not historical and not modern:
            i += 1
            continue

        # --------------------------------------------------------------
        # HEADER STORICO
        # --------------------------------------------------------------

        if historical:

            historical_count += 1

            contest = int(
                historical.group(1)
            )

            date_text = historical.group(2)

            mode = "CONCORSO"

        # --------------------------------------------------------------
        # HEADER MODERNO
        # --------------------------------------------------------------

        else:

            modern_count += 1

            contest = None

            date_text = modern.group(1)

            mode = "MODERNO"

        # --------------------------------------------------------------
        # DATA
        # --------------------------------------------------------------

        date_iso = validate_date(
            date_text,
            year,
        )

        # --------------------------------------------------------------
        # RIGA SUCCESSIVA
        # --------------------------------------------------------------

        next_index = i + 1

        if next_index >= len(lines):

            raise RuntimeError(
                f"Anno {year}, data {date_text}: "
                "nessuna riga disponibile dopo l'intestazione."
            )

        number_line = lines[next_index]

        try:

            numbers = parse_six_numbers(
                number_line,
                year,
                contest,
                date_text,
            )

        except Exception as exc:

            diagnostic_failures.append({
                "index": i,
                "mode": mode,
                "contest": contest,
                "date": date_text,
                "header": line,
                "next_line": number_line,
                "error": str(exc),
            })

            raise RuntimeError(
                f"Anno {year}, "
                f"{'concorso ' + str(contest) if contest else 'data ' + date_text}: "
                f"riga numeri non valida: {exc}"
            ) from exc

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

        # --------------------------------------------------------------
        # Saltiamo:
        #
        # i      header
        # i + 1  sei numeri
        #
        # La riga Jolly viene semplicemente ignorata.
        # --------------------------------------------------------------

        i += 2

    # =========================================================================
    # NESSUN RECORD
    # =========================================================================

    if not records:

        raise RuntimeError(
            f"Anno {year}: nessuna estrazione riconosciuta."
        )

    # =========================================================================
    # CONTEGGIO
    # =========================================================================

    if len(records) != declared_count:

        raise RuntimeError(
            f"Anno {year}: parser ha prodotto "
            f"{len(records)} estrazioni, "
            f"ma la pagina dichiara "
            f"{declared_count}."
        )

    # =========================================================================
    # MODALITÀ
    # =========================================================================

    has_historical = historical_count > 0
    has_modern = modern_count > 0

    if has_historical and has_modern:

        raise RuntimeError(
            f"Anno {year}: pagina contiene contemporaneamente "
            "formato storico e moderno. "
            "Struttura ambigua."
        )

    # =========================================================================
    # CONCORSI
    # =========================================================================

    if has_historical:

        contests = [
            int(r["concorso"])
            for r in records
        ]

        # La pagina è in ordine decrescente.
        expected_desc = list(
            range(
                max(contests),
                min(contests) - 1,
                -1,
            )
        )

        if contests != expected_desc:

            raise RuntimeError(
                f"Anno {year}: sequenza concorsi inattesa. "
                f"Letta={contests[:10]}..."
            )

        # Caso speciale 1997.
        if year == 1997:

            expected = list(
                range(95, 86, -1)
            )

            if contests != expected:

                raise RuntimeError(
                    "Anno 1997: attesa sequenza "
                    "95 -> 87."
                )

    else:

        # ------------------------------------------------------------------
        # Formato moderno.
        #
        # La pagina non espone il numero concorso, ma è ordinata
        # dall'ultima estrazione alla prima.
        #
        # Se la pagina dichiara N estrazioni, la prima riga è concorso N,
        # l'ultima è concorso 1.
        # ------------------------------------------------------------------

        for idx, record in enumerate(records):

            record["concorso"] = (
                declared_count - idx
            )

    # =========================================================================
    # DATA
    # =========================================================================

    parsed_dates = [
        pd.Timestamp(r["data"])
        for r in records
    ]

    # La pagina è decrescente.
    if parsed_dates != sorted(
        parsed_dates,
        reverse=True,
    ):

        raise RuntimeError(
            f"Anno {year}: ordine cronologico "
            "della pagina inatteso."
        )

    # =========================================================================
    # DATAFRAME
    # =========================================================================

    df = pd.DataFrame(records)

    columns = [
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

    df = df[columns]

    # =========================================================================
    # CHIAVE UNIVOCA
    # =========================================================================

    if df.duplicated(
        subset=["year", "concorso"],
        keep=False,
    ).any():

        raise RuntimeError(
            f"Anno {year}: chiave "
            "(year, concorso) duplicata."
        )

    # =========================================================================
    # LOG
    # =========================================================================

    first_contest = int(
        df["concorso"].min()
    )

    last_contest = int(
        df["concorso"].max()
    )

    log(
        f"Anno {year}: "
        f"{len(df)} estrazioni valide; "
        f"concorsi {last_contest}->{first_contest}; "
        f"parser={'CONCORSO' if has_historical else 'MODERNO'}."
    )

    metadata = {
        "year": year,
        "declared_count": declared_count,
        "parsed_count": len(df),
        "parser": (
            "CONCORSO"
            if has_historical
            else "MODERNO"
        ),
        "first_contest": first_contest,
        "last_contest": last_contest,
        "diagnostic_failures": diagnostic_failures,
    }

    return df, metadata


# ============================================================================
# ARCHIVIO COMPLETO
# ============================================================================

def download_and_build_archive():

    frames = []
    metadata = []

    for year in range(
        START_YEAR,
        END_YEAR + 1,
    ):

        html = fetch_year(year)

        df_year, meta = parse_annual_page(
            html,
            year,
        )

        frames.append(df_year)
        metadata.append(meta)

    if not frames:

        raise RuntimeError(
            "Nessun dato acquisito."
        )

    df = pd.concat(
        frames,
        ignore_index=True,
    )

    # ------------------------------------------------------------------------
    # Tipi
    # ------------------------------------------------------------------------

    df["year"] = df["year"].astype(int)
    df["concorso"] = df["concorso"].astype(int)

    for col in [
        "n1",
        "n2",
        "n3",
        "n4",
        "n5",
        "n6",
    ]:
        df[col] = df[col].astype(int)

    df["data"] = pd.to_datetime(
        df["data"],
        errors="raise",
    ).dt.strftime("%Y-%m-%d")

    # ------------------------------------------------------------------------
    # Ordinamento cronologico
    # ------------------------------------------------------------------------

    df = df.sort_values(
        ["data", "concorso"],
        ascending=[True, True],
    ).reset_index(drop=True)

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
    # Schema
    # ------------------------------------------------------------------------

    if list(df.columns) != expected_columns:

        errors.append(
            "Schema colonne non conforme."
        )

    # ------------------------------------------------------------------------
    # Righe duplicate
    # ------------------------------------------------------------------------

    if df.duplicated().any():

        errors.append(
            "Esistono righe completamente duplicate."
        )

    # ------------------------------------------------------------------------
    # Chiave primaria
    # ------------------------------------------------------------------------

    if df.duplicated(
        subset=["year", "concorso"],
        keep=False,
    ).any():

        errors.append(
            "Esistono duplicati sulla chiave "
            "(year, concorso)."
        )

    # ------------------------------------------------------------------------
    # Numeri
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
                f"{col}: valori non numerici o mancanti."
            )

        elif not values.between(
            MIN_NUMBER,
            MAX_NUMBER,
        ).all():

            errors.append(
                f"{col}: valore fuori intervallo 1-90."
            )

    # ------------------------------------------------------------------------
    # Sei distinti per riga
    # ------------------------------------------------------------------------

    for idx, row in df.iterrows():

        nums = [
            int(row[col])
            for col in number_columns
        ]

        if len(set(nums)) != 6:

            errors.append(
                f"Riga {idx}: numeri duplicati {nums}."
            )

    # ------------------------------------------------------------------------
    # Date
    # ------------------------------------------------------------------------

    dates = pd.to_datetime(
        df["data"],
        errors="coerce",
    )

    if dates.isna().any():

        errors.append(
            "Esistono date non valide."
        )

    # ------------------------------------------------------------------------
    # Copertura anni
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

    missing_years = (
        expected_years - actual_years
    )

    extra_years = (
        actual_years - expected_years
    )

    if missing_years:

        errors.append(
            f"Anni mancanti: "
            f"{sorted(missing_years)}"
        )

    if extra_years:

        errors.append(
            f"Anni inattesi: "
            f"{sorted(extra_years)}"
        )

    # ------------------------------------------------------------------------
    # Controllo metadati annuali
    # ------------------------------------------------------------------------

    for meta in metadata:

        if (
            meta["declared_count"]
            != meta["parsed_count"]
        ):

            errors.append(
                f"Anno {meta['year']}: "
                f"conteggio dichiarato "
                f"{meta['declared_count']} != "
                f"parsato "
                f"{meta['parsed_count']}."
            )

    # ------------------------------------------------------------------------
    # Controllo date dentro l'anno
    # ------------------------------------------------------------------------

    for year, group in df.groupby("year"):

        group_years = pd.to_datetime(
            group["data"]
        ).dt.year

        if not (group_years == int(year)).all():

            errors.append(
                f"Anno {year}: una o più date "
                "non appartengono all'anno."
            )

    return errors


# ============================================================================
# EXPORT
# ============================================================================

def export_csv(df: pd.DataFrame) -> None:

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
        f"CSV generato: {OUTPUT_CSV} "
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
):

    report = {
        "pipeline_version": VERSION,
        "generated_at": datetime.now().isoformat(),
        "status": status,
        "engine_enabled": ENGINE_ENABLED,
        "source": "estrazioni.it",
        "source_url": SOURCE_URL,
        "start_year": START_YEAR,
        "end_year": END_YEAR,
        "total_records": (
            int(len(df))
            if df is not None
            else 0
        ),
        "metadata": metadata,
        "errors": errors,
        "warnings": warnings,
    }

    Path(REPORT_JSON).write_text(
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

    log("ENGINE DISABLED")

    errors = []
    warnings = []

    df = None
    metadata = []

    try:

        # ====================================================================
        # 1. ACQUISIZIONE
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
        # 2. AUDIT
        # ====================================================================

        errors.extend(
            audit_global(
                df,
                metadata,
            )
        )

        # ====================================================================
        # 3. RISULTATO
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
        # 4. EXPORT
        # ====================================================================

        export_csv(df)

        # ====================================================================
        # 5. REPORT PASS
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
            f"Record validi: {len(df)}"
        )
        log(
            f"Periodo: {df['data'].min()} -> "
            f"{df['data'].max()}"
        )
        log(
            "Schema verificato."
        )
        log(
            "Numeri verificati."
        )
        log(
            "Date verificate."
        )
        log(
            "Chiavi (anno, concorso) verificate."
        )
        log(
            "Conteggi annuali verificati."
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
    sys.exit(main())
