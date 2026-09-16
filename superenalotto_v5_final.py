#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SUPERNALOTTO PIPELINE V5.3
==========================

OBIETTIVO
---------
Costruzione e audit rigoroso dello storico SuperEnalotto.

IMPORTANTE:
Questo programma NON esegue previsioni e NON genera combinazioni da giocare.

Il motore statistico resta DISABILITATO fino a quando l'integrità
dello storico non è dimostrata.

FONTE
-----
Archivio annuale:
https://www.estrazioni.it/superenalotto/?anno=YYYY

PRINCIPI
--------
1. L'anno viene dalla URL annuale.
2. Il conteggio ufficialmente dichiarato nella pagina viene verificato.
3. Ogni estrazione deve avere:
   - anno
   - concorso
   - data
   - 6 numeri principali distinti
4. Jolly e SuperStar NON entrano nei 6 numeri principali.
5. La numerazione del concorso è annuale:
   chiave univoca = (anno, concorso)
6. Nessuna interpolazione.
7. Nessun riempimento automatico.
8. Nessun dato ambiguo viene accettato.
9. Se un anno fallisce, l'intera pipeline fallisce.
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

PIPELINE_VERSION = "V5.3"

SOURCE_BASE = "https://www.estrazioni.it/superenalotto/?anno={year}"

START_YEAR = 1997
END_YEAR = datetime.now().year

OUTPUT_CSV = "superenalotto_history.csv"
REPORT_JSON = "integrity_report.json"

REQUEST_TIMEOUT = 30
RETRIES = 3
RETRY_SLEEP = 2.0

# Il motore statistico resta esplicitamente disabilitato.
ENGINE_ENABLED = False

# Sei numeri principali su 90.
MAIN_NUMBERS = 6
MIN_NUMBER = 1
MAX_NUMBER = 90


# ============================================================================
# LOG
# ============================================================================

def log(message: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


# ============================================================================
# NORMALIZZAZIONE TESTO
# ============================================================================

def normalize_unicode(text: str) -> str:
    """
    Normalizza varianti Unicode che possono comparire nell'HTML:
    ° º № ecc.

    Non modifica i numeri.
    """
    text = unicodedata.normalize("NFKC", text)

    replacements = {
        "\xa0": " ",
        "№": "n.",
        "º": ".",
        "°": ".",
        "ª": ".",
        "\u200b": "",
        "\u200c": "",
        "\u200d": "",
        "\ufeff": "",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    return text


def normalize_space(text: str) -> str:
    text = normalize_unicode(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ============================================================================
# HTML -> TESTO
# ============================================================================

class VisibleTextParser(HTMLParser):
    """
    Estrae esclusivamente testo visibile.

    Non cerca numeri nell'intero HTML grezzo, perché menu, URL,
    attributi HTML e script possono contenere numeri irrilevanti.
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

    def text(self) -> str:
        raw = "".join(self.parts)
        raw = normalize_unicode(raw)

        # Manteniamo i ritorni a capo perché sono utili per diagnosticare
        # la struttura della pagina.
        raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
        raw = re.sub(r"\n[ \t]+", "\n", raw)
        raw = re.sub(r"[ \t]+\n", "\n", raw)

        return raw.strip()


def html_to_visible_text(html: str) -> str:
    parser = VisibleTextParser()
    parser.feed(html)
    parser.close()
    return parser.text()


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


def fetch_year_page(year: int) -> str:
    url = SOURCE_BASE.format(year=year)

    last_error = None

    for attempt in range(1, RETRIES + 1):
        log(f"WEB FETCH {year} (tentativo {attempt}/{RETRIES})")

        try:
            response = SESSION.get(
                url,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            if not response.text or len(response.text) < 1000:
                raise RuntimeError(
                    f"risposta HTML troppo corta ({len(response.text)} byte)"
                )

            return response.text

        except Exception as exc:
            last_error = exc
            log(f"WARNING: fetch {year} fallito: {exc}")

            if attempt < RETRIES:
                time.sleep(RETRY_SLEEP)

    raise RuntimeError(
        f"Impossibile scaricare l'anno {year}: {last_error}"
    )


# ============================================================================
# REGEX
# ============================================================================

DATE_RE = re.compile(
    r"\b(\d{1,2}/\d{1,2}/\d{4})\b"
)

DECLARED_COUNT_RE_TEMPLATE = (
    r"\b(\d+)\s+estrazioni\s+nel\s+{year}\b"
)

# Formati possibili:
#
# Concorso n. 157 31/12/2013
# Concorso n 157 31/12/2013
# Concorso N. 157 31/12/2013
# Concorso № 157 31/12/2013
#
CONTEST_DATE_RE = re.compile(
    r"""
    Concorso
    \s*
    (?:n|N)
    \s*
    [.:]?
    \s*
    (\d+)
    \s+
    (\d{1,2}/\d{1,2}/\d{4})
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Formato moderno osservato nell'archivio:
#
# SuperEnalotto 31/12/2016
#
MODERN_DRAW_RE = re.compile(
    r"""
    SuperEnalotto
    \s+
    (\d{1,2}/\d{1,2}/\d{4})
    """,
    re.IGNORECASE | re.VERBOSE,
)

NUMBER_RE = re.compile(
    r"(?<!\d)(\d{1,2})(?!\d)"
)


# ============================================================================
# CONTEGGIO DICHIARATO DALLA PAGINA
# ============================================================================

def extract_declared_count(text: str, year: int) -> int:
    pattern = re.compile(
        DECLARED_COUNT_RE_TEMPLATE.format(year=year),
        re.IGNORECASE,
    )

    match = pattern.search(normalize_space(text))

    if not match:
        raise RuntimeError(
            f"Anno {year}: impossibile trovare il conteggio "
            f"'X estrazioni nel {year}'."
        )

    return int(match.group(1))


# ============================================================================
# PARSING DI UNA SINGOLA ESTRAZIONE
# ============================================================================

def validate_numbers(
    numbers: list[int],
    year: int,
    contest: int,
    date_text: str,
) -> tuple[int, ...]:
    if len(numbers) != MAIN_NUMBERS:
        raise ValueError(
            f"Anno {year}, concorso {contest}: "
            f"attesi {MAIN_NUMBERS} numeri principali, "
            f"trovati {len(numbers)}."
        )

    if any(
        n < MIN_NUMBER or n > MAX_NUMBER
        for n in numbers
    ):
        raise ValueError(
            f"Anno {year}, concorso {contest}: "
            f"numero fuori intervallo 1-90: {numbers}"
        )

    if len(set(numbers)) != MAIN_NUMBERS:
        raise ValueError(
            f"Anno {year}, concorso {contest}: "
            f"numeri principali duplicati: {numbers}"
        )

    try:
        dt = datetime.strptime(date_text, "%d/%m/%Y")
    except ValueError as exc:
        raise ValueError(
            f"Anno {year}, concorso {contest}: "
            f"data non valida {date_text}"
        ) from exc

    if dt.year != year:
        raise ValueError(
            f"Anno {year}, concorso {contest}: "
            f"data {date_text} non appartiene all'anno {year}."
        )

    return tuple(numbers)


def extract_first_six_main_numbers(text: str) -> tuple[int, ...]:
    """
    ESTREMAMENTE IMPORTANTE.

    La pagina presenta:

        6 numeri principali
        Jolly
        SuperStar

    Il numero del Jolly può apparire PRIMA della parola 'Jolly':

        34 36 71 76 86 89
        82 Jolly

    Quindi NON dobbiamo fare:

        "prendi tutti i numeri prima di Jolly"

    perché otterremmo 7 numeri.

    Prendiamo invece i primi sei numeri validi del corpo
    dell'estrazione.

    La delimitazione del record è già stata fatta dal parser.
    """

    # Se nel corpo è presente "Jolly", manteniamo comunque il corpo:
    # il primo numero del Jolly viene dopo i sei principali.
    #
    # NON tagliamo al termine "Jolly", perché il numero Jolly può
    # precederlo nel testo.
    #
    # Ci interessa esclusivamente la prima sequenza valida di 6 numeri.

    tokens = NUMBER_RE.findall(text)

    values = [int(x) for x in tokens]

    # Cerchiamo la prima sequenza di 6 numeri distinti e compresi 1-90.
    for i in range(0, len(values) - MAIN_NUMBERS + 1):
        candidate = values[i:i + MAIN_NUMBERS]

        if len(candidate) != MAIN_NUMBERS:
            continue

        if not all(
            MIN_NUMBER <= n <= MAX_NUMBER
            for n in candidate
        ):
            continue

        if len(set(candidate)) != MAIN_NUMBERS:
            continue

        return tuple(candidate)

    raise ValueError(
        "Impossibile identificare una sequenza valida "
        "di 6 numeri principali."
    )


# ============================================================================
# PARSER ANNUALE
# ============================================================================

def parse_annual_page(
    html: str,
    year: int,
) -> tuple[pd.DataFrame, dict]:
    """
    Parser annuale fail-closed.

    Restituisce:
        DataFrame
        metadata di audit
    """

    visible = html_to_visible_text(html)

    if not visible:
        raise RuntimeError(
            f"Anno {year}: nessun testo visibile estratto dalla pagina."
        )

    declared_count = extract_declared_count(
        visible,
        year,
    )

    # ----------------------------------------------------------------------
    # PRIMO TENTATIVO:
    # formato storico con "Concorso n."
    # ----------------------------------------------------------------------

    records = []

    contest_matches = list(
        CONTEST_DATE_RE.finditer(visible)
    )

    if contest_matches:
        for idx, match in enumerate(contest_matches):

            contest = int(match.group(1))
            date_text = match.group(2)

            body_start = match.end()

            if idx + 1 < len(contest_matches):
                body_end = contest_matches[idx + 1].start()
            else:
                body_end = len(visible)

            body = visible[body_start:body_end]

            try:
                numbers = extract_first_six_main_numbers(
                    body
                )

                numbers = validate_numbers(
                    list(numbers),
                    year,
                    contest,
                    date_text,
                )

            except Exception as exc:
                raise RuntimeError(
                    f"Anno {year}, concorso {contest}: "
                    f"record non interpretabile: {exc}"
                ) from exc

            records.append({
                "year": year,
                "data": datetime.strptime(
                    date_text,
                    "%d/%m/%Y",
                ).date().isoformat(),
                "concorso": contest,
                "n1": numbers[0],
                "n2": numbers[1],
                "n3": numbers[2],
                "n4": numbers[3],
                "n5": numbers[4],
                "n6": numbers[5],
            })

    # ----------------------------------------------------------------------
    # SECONDO TENTATIVO:
    # formato moderno "SuperEnalotto DD/MM/YYYY"
    #
    # Questo è necessario perché l'archivio 2016, per esempio, non espone
    # "Concorso n." nel testo visibile ma "SuperEnalotto data".
    # ----------------------------------------------------------------------

    if not records:

        modern_matches = list(
            MODERN_DRAW_RE.finditer(visible)
        )

        if not modern_matches:
            raise RuntimeError(
                f"Anno {year}: nessuna estrazione riconosciuta "
                f"né con formato 'Concorso n.' né con "
                f"formato 'SuperEnalotto data'."
            )

        # In questo formato il numero di concorso non è stampato.
        # Deve essere ricostruito dalla posizione cronologica.
        #
        # La pagina è in ordine decrescente:
        # ultimo concorso -> primo concorso.
        #
        # Quindi, dopo aver parsato tutte le date, assegniamo i concorsi
        # in ordine crescente.
        temp_records = []

        for idx, match in enumerate(modern_matches):

            date_text = match.group(1)

            body_start = match.end()

            if idx + 1 < len(modern_matches):
                body_end = modern_matches[idx + 1].start()
            else:
                body_end = len(visible)

            body = visible[body_start:body_end]

            try:
                numbers = extract_first_six_main_numbers(
                    body
                )

                numbers = validate_numbers(
                    list(numbers),
                    year,
                    0,
                    date_text,
                )

            except Exception as exc:
                raise RuntimeError(
                    f"Anno {year}, data {date_text}: "
                    f"record non interpretabile: {exc}"
                ) from exc

            temp_records.append({
                "year": year,
                "data": datetime.strptime(
                    date_text,
                    "%d/%m/%Y",
                ).date().isoformat(),
                "n1": numbers[0],
                "n2": numbers[1],
                "n3": numbers[2],
                "n4": numbers[3],
                "n5": numbers[4],
                "n6": numbers[5],
            })

        # Le pagine sono in ordine decrescente.
        # Le invertiamo per ottenere cronologia crescente.
        temp_records.sort(
            key=lambda x: x["data"]
        )

        if len(temp_records) != declared_count:
            raise RuntimeError(
                f"Anno {year}: parser moderno ha prodotto "
                f"{len(temp_records)} estrazioni, "
                f"ma la pagina dichiara {declared_count}."
            )

        # Assegna 1..N.
        #
        # Caso storico 1997 escluso: quello usa il formato Concorso n.
        #
        for contest_number, record in enumerate(
            temp_records,
            start=1,
        ):
            record["concorso"] = contest_number

        records = temp_records

    # ----------------------------------------------------------------------
    # AUDIT BASE
    # ----------------------------------------------------------------------

    if len(records) != declared_count:
        raise RuntimeError(
            f"Anno {year}: {len(records)} estrazioni parsate "
            f"contro {declared_count} dichiarate dalla fonte."
        )

    df = pd.DataFrame(records)

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
        raise RuntimeError(
            f"Anno {year}: colonne inattese: "
            f"{list(df.columns)}"
        )

    # Nessun concorso duplicato nello stesso anno.
    dup = df.duplicated(
        subset=["year", "concorso"],
        keep=False,
    )

    if dup.any():
        bad = df.loc[
            dup,
            ["year", "concorso", "data"]
        ].to_dict("records")

        raise RuntimeError(
            f"Anno {year}: concorsi duplicati: {bad[:10]}"
        )

    # Tutte le date devono appartenere all'anno.
    years = pd.to_datetime(
        df["data"]
    ).dt.year

    if not (years == year).all():
        raise RuntimeError(
            f"Anno {year}: trovate date appartenenti "
            f"a un altro anno."
        )

    # ----------------------------------------------------------------------
    # CONTROLLO CONCORSI
    # ----------------------------------------------------------------------

    contests = sorted(
        df["concorso"].astype(int).tolist()
    )

    if year == 1997:
        expected = list(range(87, 96))

        if contests != expected:
            raise RuntimeError(
                f"Anno 1997: sequenza concorsi inattesa: "
                f"{contests}; attesa {expected}."
            )

    else:
        expected = list(
            range(
                min(contests),
                max(contests) + 1,
            )
        )

        if contests != expected:
            raise RuntimeError(
                f"Anno {year}: numerazione concorsi "
                f"non consecutiva."
            )

        if len(contests) != declared_count:
            raise RuntimeError(
                f"Anno {year}: numero concorsi "
                f"{len(contests)} diverso da "
                f"conteggio dichiarato {declared_count}."
            )

    # ----------------------------------------------------------------------
    # CONTROLLO DATE
    # ----------------------------------------------------------------------

    parsed_dates = pd.to_datetime(
        df["data"]
    )

    if not parsed_dates.is_monotonic_increasing:
        raise RuntimeError(
            f"Anno {year}: date non monotone."
        )

    log(
        f"Anno {year}: {len(df)} estrazioni valide; "
        f"concorsi {contests[0]}->{contests[-1]}."
    )

    metadata = {
        "year": year,
        "declared_count": declared_count,
        "parsed_count": len(df),
        "first_contest": contests[0],
        "last_contest": contests[-1],
        "parser_mode": (
            "CONCORSO"
            if contest_matches
            else "MODERN_SUPERENALOTTO"
        ),
    }

    return df, metadata


# ============================================================================
# AUDIT GLOBALE
# ============================================================================

def audit_global(
    df: pd.DataFrame,
    metadata: list[dict],
) -> list[str]:
    errors = []

    # ----------------------------------------------------------------------
    # Schema
    # ----------------------------------------------------------------------

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
            "Schema colonne globale non conforme."
        )

    # ----------------------------------------------------------------------
    # Righe duplicate
    # ----------------------------------------------------------------------

    if df.duplicated().any():
        errors.append(
            "Esistono righe completamente duplicate."
        )

    # ----------------------------------------------------------------------
    # Chiave primaria logica
    # ----------------------------------------------------------------------

    if df.duplicated(
        subset=["year", "concorso"]
    ).any():
        errors.append(
            "Chiave (year, concorso) duplicata."
        )

    # ----------------------------------------------------------------------
    # Numeri
    # ----------------------------------------------------------------------

    number_columns = [
        "n1",
        "n2",
        "n3",
        "n4",
        "n5",
        "n6",
    ]

    for col in number_columns:

        if df[col].isna().any():
            errors.append(
                f"{col}: valori mancanti."
            )

        values = pd.to_numeric(
            df[col],
            errors="coerce",
        )

        if values.isna().any():
            errors.append(
                f"{col}: valori non numerici."
            )

        if not values.between(
            MIN_NUMBER,
            MAX_NUMBER,
        ).all():
            errors.append(
                f"{col}: valore fuori intervallo 1-90."
            )

    # ----------------------------------------------------------------------
    # Sei numeri distinti per estrazione
    # ----------------------------------------------------------------------

    for row_idx, row in df.iterrows():

        nums = [
            int(row[col])
            for col in number_columns
        ]

        if len(set(nums)) != MAIN_NUMBERS:
            errors.append(
                "Numeri duplicati nella riga "
                f"{row_idx}: {nums}"
            )

    # ----------------------------------------------------------------------
    # Date
    # ----------------------------------------------------------------------

    dates = pd.to_datetime(
        df["data"],
        errors="coerce",
    )

    if dates.isna().any():
        errors.append(
            "Esistono date non valide."
        )

    # ----------------------------------------------------------------------
    # Controllo anni
    # ----------------------------------------------------------------------

    if not df.empty:

        min_year = int(
            pd.to_datetime(df["data"]).dt.year.min()
        )

        max_year = int(
            pd.to_datetime(df["data"]).dt.year.max()
        )

        if min_year != START_YEAR:
            errors.append(
                f"Primo anno trovato {min_year}, "
                f"atteso {START_YEAR}."
            )

        if max_year != END_YEAR:
            errors.append(
                f"Ultimo anno trovato {max_year}, "
                f"atteso {END_YEAR}."
            )

    # ----------------------------------------------------------------------
    # Controllo copertura annuale
    # ----------------------------------------------------------------------

    meta_by_year = {
        int(x["year"]): x
        for x in metadata
    }

    for year in range(
        START_YEAR,
        END_YEAR + 1,
    ):

        if year not in meta_by_year:
            errors.append(
                f"Anno {year}: assente dai metadati."
            )
            continue

        meta = meta_by_year[year]

        if (
            meta["declared_count"]
            != meta["parsed_count"]
        ):
            errors.append(
                f"Anno {year}: conteggio dichiarato "
                f"{meta['declared_count']} != "
                f"conteggio parsato "
                f"{meta['parsed_count']}."
            )

    # ----------------------------------------------------------------------
    # Ordinamento globale
    # ----------------------------------------------------------------------

    sorted_df = df.sort_values(
        ["data", "year", "concorso"]
    ).reset_index(drop=True)

    if not df.reset_index(drop=True).equals(
        sorted_df
    ):
        # Non è necessariamente errore strutturale perché il download
        # avviene per anno; riordiniamo comunque prima dell'export.
        pass

    return errors


# ============================================================================
# DOWNLOAD + BUILD
# ============================================================================

def download_and_build_archive() -> tuple[
    pd.DataFrame,
    list[dict],
]:
    all_frames = []
    metadata = []

    for year in range(
        START_YEAR,
        END_YEAR + 1,
    ):

        html = fetch_year_page(year)

        try:
            df_year, meta = parse_annual_page(
                html,
                year,
            )

        except Exception as exc:

            log(
                f"ERRORE CRITICO anno {year}: {exc}"
            )

            raise

        all_frames.append(df_year)
        metadata.append(meta)

    if not all_frames:
        raise RuntimeError(
            "Nessun dato prodotto dall'archivio."
        )

    df = pd.concat(
        all_frames,
        ignore_index=True,
    )

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

    # Ordine cronologico definitivo.
    df = df.sort_values(
        ["data", "concorso"]
    ).reset_index(drop=True)

    return df, metadata


# ============================================================================
# REPORT
# ============================================================================

def write_report(
    status: str,
    df: pd.DataFrame | None,
    metadata: list[dict],
    errors: list[str],
    warnings: list[str],
) -> None:

    report = {
        "pipeline_version": PIPELINE_VERSION,
        "generated_at": datetime.now().isoformat(),
        "status": status,
        "engine_enabled": ENGINE_ENABLED,
        "source": "estrazioni.it",
        "source_url_pattern": SOURCE_BASE,
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
# EXPORT
# ============================================================================

def export_csv(df: pd.DataFrame) -> None:

    export_columns = [
        "data",
        "concorso",
        "n1",
        "n2",
        "n3",
        "n4",
        "n5",
        "n6",
    ]

    export_df = df[
        export_columns
    ].copy()

    export_df.to_csv(
        OUTPUT_CSV,
        index=False,
        encoding="utf-8",
    )

    log(
        f"Archivio esportato in '{OUTPUT_CSV}' "
        f"({len(export_df)} righe)."
    )


# ============================================================================
# ENGINE
# ============================================================================

def run_engine_disabled() -> None:
    """
    Placeholder intenzionale.

    Nessun test statistico viene eseguito in questa versione.
    """

    if ENGINE_ENABLED:
        raise RuntimeError(
            "ENGINE_ENABLED=True ma il motore V5.3 "
            "non è autorizzato in questa fase."
        )

    log(
        "ENGINE DISABLED — nessun test statistico eseguito."
    )


# ============================================================================
# MAIN
# ============================================================================

def main() -> int:

    log(
        f"SUPERNALOTTO PIPELINE {PIPELINE_VERSION} "
        f"— STRICT DATA INTEGRITY AUDIT"
    )

    log(
        "ENGINE DISABLED"
    )

    errors: list[str] = []
    warnings: list[str] = []

    df = None
    metadata: list[dict] = []

    try:

        # --------------------------------------------------------------
        # 1. DOWNLOAD + PARSING
        # --------------------------------------------------------------

        df, metadata = download_and_build_archive()

        log(
            f"Acquisizione completata: "
            f"{len(metadata)} anni, "
            f"{len(df)} estrazioni."
        )

        # --------------------------------------------------------------
        # 2. AUDIT GLOBALE
        # --------------------------------------------------------------

        errors.extend(
            audit_global(
                df,
                metadata,
            )
        )

        # --------------------------------------------------------------
        # 3. CHIAVE ANNUO/CONCORSO
        # --------------------------------------------------------------

        duplicates = df[
            df.duplicated(
                subset=[
                    "year",
                    "concorso",
                ],
                keep=False,
            )
        ]

        if not duplicates.empty:

            errors.append(
                "Duplicati sulla chiave "
                "(year, concorso)."
            )

        # --------------------------------------------------------------
        # 4. CONTROLLO NUMERO TOTALE
        # --------------------------------------------------------------

        if len(df) < 1000:

            errors.append(
                f"Archivio globale sospettosamente corto: "
                f"{len(df)} estrazioni."
            )

        # --------------------------------------------------------------
        # 5. STATUS
        # --------------------------------------------------------------

        if errors:

            write_report(
                "FAILED",
                df,
                metadata,
                errors,
                warnings,
            )

            log(
                "AUDIT FAILED — archivio NON utilizzabile."
            )

            for error in errors:
                log(f"ERROR: {error}")

            return 1

        # --------------------------------------------------------------
        # 6. EXPORT
        # --------------------------------------------------------------

        export_csv(df)

        # --------------------------------------------------------------
        # 7. REPORT PASS
        # --------------------------------------------------------------

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
            f"Estrazioni valide: {len(df)}"
        )
        log(
            f"Periodo: {df['data'].min()} -> "
            f"{df['data'].max()}"
        )
        log(
            "Nessuna anomalia strutturale rilevata."
        )
        log(
            "Il motore statistico resta DISABILITATO."
        )
        log(
            "=================================================="
        )

        run_engine_disabled()

        return 0

    except Exception as exc:

        errors.append(
            f"Eccezione fatale: {type(exc).__name__}: {exc}"
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
    sys.exit(main())
