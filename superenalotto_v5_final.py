# ============================================================
# SUPERNALOTTO PIPELINE V5.2.5 — STRICT DATA INTEGRITY AUDIT
# ENGINE DISABLED
#
# Scopo:
#   1) scaricare l'archivio storico CSV
#   2) normalizzarlo in modo difensivo
#   3) verificare integrità, numeri, date e duplicati
#   4) verificare copertura 1997 -> anno corrente
#   5) esportare il CSV SOLO se l'audit passa
#
# Nessuna previsione, nessuna strategia e nessuna generazione
# di numeri viene eseguita in questa versione.
# ============================================================

import json
import os
import re
import time
from datetime import datetime
from io import StringIO
from html.parser import HTMLParser

import pandas as pd
import requests


# ============================================================
# CONFIGURAZIONE RIGIDA
# ============================================================

REQUIRED_START_YEAR = 1997

TRAINING_START_YEAR = 1997
TRAINING_END_YEAR = 2015

DISCOVERY_START_YEAR = 2016
DISCOVERY_END_YEAR = 2021

CONFIRMATION_START_YEAR = 2022
CURRENT_YEAR = datetime.now().year

OUTPUT_CSV = "superenalotto_storico.csv"
AUDIT_REPORT = "integrity_report.json"

BOOTSTRAP_URL = (
    "https://www.estrazioni.it/"
    "index.php?formato=csv&p=download&tipo=superenalotto"
)

HTTP_TIMEOUT = 30
HTTP_RETRIES = 3
HTTP_RETRY_SLEEP = 3

MIN_HISTORICAL_DRAWS_PER_YEAR = 80
MIN_TOTAL_RECORDS = 1000

VALID_NUMBERS_MIN = 1
VALID_NUMBERS_MAX = 90
NUMBERS_PER_DRAW = 6


# ============================================================
# SESSIONE HTTP
# ============================================================

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (compatible; "
            "SuperEnalotto-Statistical-Audit/5.2.5)"
        )
    }
)


# ============================================================
# LOG
# ============================================================

def log(message):
    print(
        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
        f"{message}",
        flush=True,
    )


# ============================================================
# FAIL-CLOSED
# ============================================================

def remove_stale_dataset():
    """
    Elimina il dataset precedente prima dell'audit.
    Se non è possibile eliminarlo, l'esecuzione viene bloccata.
    """
    if os.path.exists(OUTPUT_CSV):
        try:
            os.remove(OUTPUT_CSV)
            log(
                f"Dataset precedente '{OUTPUT_CSV}' "
                "eliminato con successo."
            )
        except Exception as exc:
            raise RuntimeError(
                "FAIL-CLOSED FATALE: impossibile eliminare il "
                f"dataset precedente '{OUTPUT_CSV}': {exc}"
            )


# ============================================================
# REPORT
# ============================================================

def write_report(status, total_records, errors, warnings, df=None):
    report = {
        "timestamp": datetime.now().isoformat(),
        "status": status,
        "total_records": int(total_records),
        "required_start_year": REQUIRED_START_YEAR,
        "current_year": CURRENT_YEAR,
        "windows": {
            "training": [
                TRAINING_START_YEAR,
                TRAINING_END_YEAR,
            ],
            "discovery": [
                DISCOVERY_START_YEAR,
                DISCOVERY_END_YEAR,
            ],
            "confirmation": [
                CONFIRMATION_START_YEAR,
                CURRENT_YEAR,
            ],
        },
        "errors": list(errors),
        "warnings": list(warnings),
    }

    if df is not None and not df.empty:
        report["date_range"] = {
            "min": str(df["data"].min()),
            "max": str(df["data"].max()),
        }

        report["contest_range"] = {
            "min": int(df["concorso"].min()),
            "max": int(df["concorso"].max()),
        }

        yearly_counts = (
            df.groupby("year")
            .size()
            .astype(int)
            .to_dict()
        )

        report["yearly_counts"] = {
            str(k): int(v)
            for k, v in yearly_counts.items()
        }

        report["window_counts"] = {
            "training_1997_2015": int(
                (
                    (df["year"] >= TRAINING_START_YEAR)
                    & (df["year"] <= TRAINING_END_YEAR)
                ).sum()
            ),
            "discovery_2016_2021": int(
                (
                    (df["year"] >= DISCOVERY_START_YEAR)
                    & (df["year"] <= DISCOVERY_END_YEAR)
                ).sum()
            ),
            "confirmation_2022_current": int(
                (
                    (df["year"] >= CONFIRMATION_START_YEAR)
                    & (df["year"] <= CURRENT_YEAR)
                ).sum()
            ),
        }

    try:
        with open(
            AUDIT_REPORT,
            "w",
            encoding="utf-8",
        ) as handle:
            json.dump(
                report,
                handle,
                indent=2,
                ensure_ascii=False,
            )

        log(
            f"Report di integrità generato in "
            f"'{AUDIT_REPORT}'."
        )

    except Exception as exc:
        log(
            "ERRORE CRITICO nella scrittura del report "
            f"'{AUDIT_REPORT}': {exc}"
        )


# ============================================================
# UTILITY PARSING
# ============================================================

def clean_col(name):
    return str(name).strip().lower()


def normalize_col_name(name):
    text = clean_col(name)
    text = (
        text.replace("à", "a")
        .replace("è", "e")
        .replace("é", "e")
        .replace("ì", "i")
        .replace("ò", "o")
        .replace("ù", "u")
    )
    return re.sub(r"[^a-z0-9]+", "", text)


def find_column(columns, patterns):
    for col in columns:
        name = clean_col(col)
        normalized = normalize_col_name(col)

        for pattern in patterns:
            if re.search(pattern, name):
                return col

            if re.search(pattern, normalized):
                return col

    return None


def parse_integer(value):
    if value is None:
        return None

    if pd.isna(value):
        return None

    text = str(value).strip()

    if not text or text.lower() in {"nan", "none", "null"}:
        return None

    match = re.search(r"(?<!\d)(\d{1,3})(?!\d)", text)

    if not match:
        return None

    try:
        return int(match.group(1))
    except Exception:
        return None


def parse_date_value(value):
    if value is None or pd.isna(value):
        return pd.NaT

    text = str(value).strip()

    if not text or text.lower() in {"nan", "none", "null"}:
        return pd.NaT

    # Primo tentativo: parser Pandas.
    parsed = pd.to_datetime(
        text,
        errors="coerce",
        dayfirst=True,
    )

    if not pd.isna(parsed):
        return parsed.normalize()

    # Secondo tentativo: date italiane esplicite.
    match = re.search(
        r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})",
        text,
    )

    if match:
        day = int(match.group(1))
        month = int(match.group(2))
        year = int(match.group(3))

        try:
            return pd.Timestamp(
                year=year,
                month=month,
                day=day,
            )
        except Exception:
            return pd.NaT

    return pd.NaT


def parse_contest_value(value):
    if value is None or pd.isna(value):
        return None

    text = str(value).strip()

    # Gestisce "Concorso Nº 123", "123", ecc.
    match = re.search(r"(\d+)", text)

    if not match:
        return None

    try:
        return int(match.group(1))
    except Exception:
        return None


# ============================================================
# ANNUAL ARCHIVE ACQUISITION
# ============================================================

OFFICIAL_ARCHIVE_BASE = "https://www.estrazioni.it/superenalotto/?anno={year}"


class VisibleTextParser(HTMLParser):
    """Dependency-free HTML visible-text extractor."""
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        text = " ".join(data.split())
        if text:
            self.parts.append(text)


def html_to_text_lines(html):
    parser = VisibleTextParser()
    parser.feed(html)
    parser.close()
    return parser.parts


def fetch_year_page(year):
    url = OFFICIAL_ARCHIVE_BASE.format(year=year)
    last_error = None

    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            log(f"WEB FETCH {year} (tentativo {attempt}/{HTTP_RETRIES})")
            response = SESSION.get(url, timeout=HTTP_TIMEOUT)
            response.raise_for_status()
            if not response.content:
                raise RuntimeError("Risposta HTTP vuota.")
            response.encoding = response.apparent_encoding or "utf-8"
            html = response.text
            if len(html.strip()) < 1000:
                raise RuntimeError("Pagina HTML anormalmente corta.")
            return html
        except Exception as exc:
            last_error = exc
            log(f"FETCH {year} fallito: {exc}")
            if attempt < HTTP_RETRIES:
                time.sleep(HTTP_RETRY_SLEEP)

    raise RuntimeError(f"Impossibile recuperare l'archivio {year}: {last_error}")


CONTEST_RE = re.compile(r"Concorso\s*(?:n\.?|N\.?|№)\s*(\d+)", re.IGNORECASE)
DATE_RE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b")
NUMBER_RE = re.compile(r"(?<!\d)(\d{1,2})(?!\d)")


def parse_annual_page(html, year):
    """Parse one explicit annual SuperEnalotto archive page."""
    lines = html_to_text_lines(html)
    if not lines:
        raise RuntimeError(f"Anno {year}: nessun testo visibile estratto.")

    positions = []
    for idx, line in enumerate(lines):
        m = CONTEST_RE.search(line)
        if m:
            positions.append((idx, int(m.group(1))))

    if not positions:
        raise RuntimeError(f"Anno {year}: nessun 'Concorso n.' riconosciuto.")

    records = []
    for pos, (start, contest) in enumerate(positions):
        end = positions[pos + 1][0] if pos + 1 < len(positions) else len(lines)
        block = " ".join(lines[start:end])

        date_match = DATE_RE.search(block)
        if not date_match:
            log(f"WARNING: anno {year}, concorso {contest}: data non riconosciuta.")
            continue

        date_value = pd.to_datetime(date_match.group(1), dayfirst=True, errors="coerce")
        if pd.isna(date_value) or date_value.year != year:
            log(f"WARNING: anno {year}, concorso {contest}: data non valida.")
            continue

        main_text = block[date_match.end():]
        jolly_match = re.search(r"\bJolly\b", main_text, re.IGNORECASE)
        if jolly_match:
            main_text = main_text[:jolly_match.start()]

        numbers = [int(x) for x in NUMBER_RE.findall(main_text)]
        if len(numbers) != 6:
            log(f"WARNING: anno {year}, concorso {contest}: trovati {len(numbers)} numeri principali; record scartato.")
            continue
        if any(x < 1 or x > 90 for x in numbers) or len(set(numbers)) != 6:
            log(f"WARNING: anno {year}, concorso {contest}: combinazione non valida; record scartato.")
            continue

        records.append({
            "data": pd.Timestamp(date_value).normalize(),
            "year": int(year),
            "concorso": int(contest),
            "n1": numbers[0], "n2": numbers[1], "n3": numbers[2],
            "n4": numbers[3], "n5": numbers[4], "n6": numbers[5],
        })

    if not records:
        raise RuntimeError(f"Anno {year}: nessuna estrazione valida prodotta dal parser.")

    df = pd.DataFrame(records)
    dup = df.duplicated(subset=["year", "concorso"], keep=False)
    if dup.any():
        conflict = df.loc[dup].drop_duplicates()
        if len(conflict) > 1:
            raise RuntimeError(f"Anno {year}: conflitto interno sul medesimo numero di concorso.")
        df = df.drop_duplicates(subset=["year", "concorso"])

    return df.sort_values(["data", "concorso"]).reset_index(drop=True)


def download_and_build_archive():
    frames = []
    source_years = list(range(REQUIRED_START_YEAR, CURRENT_YEAR + 1))

    for year in source_years:
        html = fetch_year_page(year)
        frame = parse_annual_page(html, year)
        log(
            f"Anno {year}: {len(frame)} estrazioni valide; "
            f"concorsi {int(frame['concorso'].min())}->{int(frame['concorso'].max())}."
        )
        frames.append(frame)

    if not frames:
        raise RuntimeError("Nessun anno recuperato.")

    return pd.concat(frames, ignore_index=True), source_years


def normalize_archive(df):
    required = ["data", "year", "concorso", "n1", "n2", "n3", "n4", "n5", "n6"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise RuntimeError("Colonne mancanti dopo parsing: " + ", ".join(missing))

    df = df.copy()
    df["data"] = pd.to_datetime(df["data"], errors="coerce")
    for col in ["year", "concorso", "n1", "n2", "n3", "n4", "n5", "n6"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=required).copy()
    for col in ["year", "concorso", "n1", "n2", "n3", "n4", "n5", "n6"]:
        df[col] = df[col].astype(int)
    df = df[df["year"].between(REQUIRED_START_YEAR, CURRENT_YEAR)].copy()
    return df.sort_values(["year", "data", "concorso"]).reset_index(drop=True)


# ============================================================
# RISOLUZIONE DUPLICATI
# ============================================================

def resolve_duplicates_strict(df):
    key = ["year", "concorso"]
    full = ["year", "concorso", "data", "n1", "n2", "n3", "n4", "n5", "n6"]
    initial_len = len(df)
    exact = df.drop_duplicates(subset=full).copy()
    removed_exact = initial_len - len(exact)
    if removed_exact:
        log(f"Rimosse {removed_exact} duplicazioni perfettamente identiche.")

    conflicts = exact[exact.duplicated(subset=key, keep=False)].copy()
    if not conflicts.empty:
        log("ERRORE FATALE: conflitto sullo stesso (anno, concorso):")
        log(conflicts[full].to_string(index=False))
        return None, f"Conflitto dati irreconciliabile su {len(conflicts)} righe."

    return exact.sort_values(["year", "data", "concorso"]).reset_index(drop=True), None


# ============================================================
# AUDIT STRUTTURALE
# ============================================================

def audit_structure(df):
    errors, warnings = [], []
    required = ["data", "year", "concorso", "n1", "n2", "n3", "n4", "n5", "n6"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        errors.append("Colonne mancanti: " + ", ".join(missing))
        return errors, warnings
    if df.empty:
        errors.append("Dataset vuoto.")
        return errors, warnings
    if len(df) < MIN_TOTAL_RECORDS:
        errors.append(f"Numero totale di estrazioni insufficiente: {len(df)} < {MIN_TOTAL_RECORDS}.")
    if df["concorso"].le(0).any():
        errors.append("Presenti numeri di concorso <= 0.")
    if df["data"].isna().any():
        errors.append("Presenti date non valide.")
    if (~df["year"].between(REQUIRED_START_YEAR, CURRENT_YEAR)).any():
        errors.append(f"Presenti anni fuori dall'intervallo {REQUIRED_START_YEAR}-{CURRENT_YEAR}.")

    for col in ["n1", "n2", "n3", "n4", "n5", "n6"]:
        invalid = ~df[col].between(1, 90)
        if invalid.any():
            errors.append(f"Valori fuori range 1..90 in {col}: {int(invalid.sum())}.")

    duplicate_numbers = []
    for index, row in df.iterrows():
        values = [int(row[f"n{i}"]) for i in range(1, 7)]
        if len(set(values)) != 6:
            duplicate_numbers.append(index)
    if duplicate_numbers:
        errors.append(f"Estrazioni con numeri principali duplicati: {len(duplicate_numbers)}.")

    mismatch = df["data"].dt.year != df["year"]
    if mismatch.any():
        errors.append(f"Incoerenza tra anno e data: {int(mismatch.sum())} record.")

    if df.duplicated(subset=["year", "concorso"], keep=False).any():
        errors.append("Chiave (anno, concorso) non univoca.")

    return errors, warnings


# ============================================================
# AUDIT COPERTURA TEMPORALE
# ============================================================

def audit_coverage(df):
    errors, warnings = [], []
    years_present = set(int(v) for v in df["year"].unique())
    expected_years = set(range(REQUIRED_START_YEAR, CURRENT_YEAR + 1))
    missing_years = sorted(expected_years - years_present)
    if missing_years:
        errors.append("Anni completamente mancanti: " + ", ".join(map(str, missing_years)))

    yearly_counts = df.groupby("year").size().to_dict()
    for year in range(REQUIRED_START_YEAR, CURRENT_YEAR):
        count = int(yearly_counts.get(year, 0))
        if count < MIN_HISTORICAL_DRAWS_PER_YEAR:
            errors.append(
                f"Anno {year}: solo {count} estrazioni; minimo richiesto {MIN_HISTORICAL_DRAWS_PER_YEAR}."
            )

    current_count = int(yearly_counts.get(CURRENT_YEAR, 0))
    if current_count <= 0:
        errors.append(f"Nessuna estrazione presente per l'anno corrente {CURRENT_YEAR}.")

    windows = [
        ("training", TRAINING_START_YEAR, TRAINING_END_YEAR),
        ("discovery", DISCOVERY_START_YEAR, DISCOVERY_END_YEAR),
        ("confirmation", CONFIRMATION_START_YEAR, CURRENT_YEAR),
    ]
    for name, start, end in windows:
        if df[df["year"].between(start, end)].empty:
            errors.append(f"Finestra {name} completamente vuota.")

    # Contest numbering is annual on this archive. Check gaps within each year only.
    for year, group in df.groupby("year"):
        contests = sorted(group["concorso"].astype(int).tolist())
        gaps = []
        for previous, current in zip(contests, contests[1:]):
            if current > previous + 1:
                gaps.append((previous, current, current - previous - 1))
        if gaps:
            warnings.append(
                f"Anno {int(year)}: buchi nella sequenza dei concorsi: "
                + "; ".join(f"{a}->{b} (mancano {n})" for a, b, n in gaps[:10])
            )
        if not group.sort_values("concorso")["data"].is_monotonic_increasing:
            errors.append(
                f"Anno {int(year)}: data non monotona rispetto al numero di concorso."
            )

    return errors, warnings


# ============================================================
# AUDIT CROSS-YEAR
# ============================================================

def audit_global_contest_consistency(df):
    errors, warnings = [], []
    repeated = df.groupby("concorso")["year"].nunique()
    if (repeated > 1).any():
        warnings.append(
            "Numeri di concorso ricorrenti in anni diversi: "
            "comportamento atteso perché la numerazione è annuale."
        )
    return errors, warnings


# ============================================================
# SALVATAGGIO DATASET
# ============================================================

def export_dataset(df):
    export_df = df[
        [
            "data",
            "concorso",
            "n1",
            "n2",
            "n3",
            "n4",
            "n5",
            "n6",
        ]
    ].copy()

    export_df["data"] = export_df[
        "data"
    ].dt.strftime("%Y-%m-%d")

    export_df.to_csv(
        OUTPUT_CSV,
        index=False,
        encoding="utf-8",
    )

    if not os.path.exists(OUTPUT_CSV):
        raise RuntimeError(
            "Export fallito: il file CSV non esiste."
        )

    if os.path.getsize(OUTPUT_CSV) <= 0:
        raise RuntimeError(
            "Export fallito: il file CSV è vuoto."
        )

    log(
        f"Dataset validato esportato in "
        f"'{OUTPUT_CSV}'."
    )


# ============================================================
# PIPELINE PRINCIPALE — AUDIT ONLY
# ============================================================

def run_strict_data_audit():
    log("")
    log("=======================================================")
    log(
        "SUPERNALOTTO PIPELINE V5.2.5 "
        "— STRICT DATA INTEGRITY AUDIT"
    )
    log("ENGINE DISABLED")
    log("=======================================================")
    log(
        f"Anno richiesto iniziale: "
        f"{REQUIRED_START_YEAR}"
    )
    log(
        f"Anno corrente rilevato: "
        f"{CURRENT_YEAR}"
    )
    log(
        "Training: "
        f"{TRAINING_START_YEAR}-{TRAINING_END_YEAR}"
    )
    log(
        "Discovery: "
        f"{DISCOVERY_START_YEAR}-{DISCOVERY_END_YEAR}"
    )
    log(
        "Confirmation: "
        f"{CONFIRMATION_START_YEAR}-{CURRENT_YEAR}"
    )
    log("=======================================================")

    errors = []
    warnings = []
    df = None

    # Fail-closed: nessun vecchio dataset deve poter
    # essere scambiato per un dataset appena validato.
    try:
        remove_stale_dataset()
    except Exception as exc:
        errors.append(str(exc))

        write_report(
            "FAILED",
            0,
            errors,
            warnings,
            None,
        )

        raise

    # Download.
    try:
        csv_text = download_bootstrap()
    except Exception as exc:
        errors.append(
            f"Download bootstrap fallito: {exc}"
        )

        write_report(
            "FAILED",
            0,
            errors,
            warnings,
            None,
        )

        raise

    # Parsing.
    try:
        df = parse_csv_data(csv_text)
    except Exception as exc:
        errors.append(
            f"Parsing/normalizzazione fallita: {exc}"
        )

        write_report(
            "FAILED",
            0,
            errors,
            warnings,
            None,
        )

        raise

    # Duplicati.
    try:
        df, duplicate_error = (
            resolve_duplicates_strict(df)
        )

        if duplicate_error:
            errors.append(duplicate_error)

    except Exception as exc:
        errors.append(
            f"Controllo duplicati fallito: {exc}"
        )
        df = None

    if df is not None:
        structure_errors, structure_warnings = (
            audit_structure(df)
        )

        coverage_errors, coverage_warnings = (
            audit_coverage(df)
        )

        global_errors, global_warnings = (
            audit_global_contest_consistency(df)
        )

        errors.extend(structure_errors)
        errors.extend(coverage_errors)
        errors.extend(global_errors)

        warnings.extend(structure_warnings)
        warnings.extend(coverage_warnings)
        warnings.extend(global_warnings)

    total_records = (
        len(df)
        if df is not None
        else 0
    )

    if errors:
        status = "FAILED"

        write_report(
            status,
            total_records,
            errors,
            warnings,
            df,
        )

        log("")
        log("=======================================================")
        log("AUDIT FALLITO — FAIL-CLOSED")
        log("=======================================================")

        for error in errors:
            log(f"ERRORE: {error}")

        if warnings:
            log("")
            for warning in warnings:
                log(f"WARNING: {warning}")

        log("")
        log(
            "Nessun nuovo dataset validato è stato "
            "esportato."
        )
        log(
            "ENGINE DISABLED — nessuna analisi "
            "statistica eseguita."
        )

        raise RuntimeError(
            "Audit di integrità fallito. "
            "Consultare integrity_report.json."
        )

    # Se non ci sono errori, warnings consentiti.
    if warnings:
        status = "VALID_WITH_WARNINGS"
    else:
        status = "VERIFIED_VALID"

    try:
        export_dataset(df)
    except Exception as exc:
        errors.append(
            f"Esportazione dataset fallita: {exc}"
        )

        write_report(
            "FAILED",
            total_records,
            errors,
            warnings,
            df,
        )

        raise

    write_report(
        status,
        total_records,
        errors,
        warnings,
        df,
    )

    log("")
    log("=======================================================")
    log(f"AUDIT COMPLETATO: {status}")
    log("=======================================================")
    log(
        f"Totale estrazioni valide: "
        f"{total_records}"
    )
    log(
        f"Intervallo date: "
        f"{df['data'].min().date()} -> "
        f"{df['data'].max().date()}"
    )
    log(
        f"Concorso: "
        f"{int(df['concorso'].min())} -> "
        f"{int(df['concorso'].max())}"
    )

    if warnings:
        log("")
        for warning in warnings:
            log(f"WARNING: {warning}")

    log("")
    log(
        "ENGINE DISABLED — audit dati completato. "
        "Nessuna generazione di numeri."
    )
    log("=======================================================")


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    run_strict_data_audit()
