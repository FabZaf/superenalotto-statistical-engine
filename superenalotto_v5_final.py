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
# DOWNLOAD BOOTSTRAP
# ============================================================

def download_bootstrap():
    log("=======================================================")
    log("DOWNLOAD ARCHIVIO BOOTSTRAP")
    log("=======================================================")
    log(f"Fonte: {BOOTSTRAP_URL}")

    last_error = None

    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            log(
                f"Tentativo {attempt}/{HTTP_RETRIES}..."
            )

            response = SESSION.get(
                BOOTSTRAP_URL,
                timeout=HTTP_TIMEOUT,
            )
            response.raise_for_status()

            content = response.content

            if not content:
                raise ValueError(
                    "Risposta HTTP vuota."
                )

            # Decodifica robusta per CSV italiani.
            text = None

            for encoding in (
                "utf-8-sig",
                "utf-8",
                "cp1252",
                "latin-1",
            ):
                try:
                    text = content.decode(
                        encoding,
                        errors="strict",
                    )
                    break
                except UnicodeDecodeError:
                    continue

            if text is None:
                text = content.decode(
                    "latin-1",
                    errors="replace",
                )

            text = text.strip()

            if not text:
                raise ValueError(
                    "Testo CSV vuoto dopo la decodifica."
                )

            log(
                "Download completato: "
                f"{len(content)} byte / "
                f"{len(text)} caratteri."
            )

            return text

        except Exception as exc:
            last_error = exc
            log(
                f"Fallito tentativo {attempt}: {exc}"
            )

            if attempt < HTTP_RETRIES:
                time.sleep(HTTP_RETRY_SLEEP)

    raise RuntimeError(
        "Impossibile scaricare il bootstrap dopo "
        f"{HTTP_RETRIES} tentativi: {last_error}"
    )


# ============================================================
# LETTURA CSV DIFENSIVA
# ============================================================

def detect_separator(csv_text):
    sample = csv_text[:10000]

    candidates = {
        ",": sample.count(","),
        ";": sample.count(";"),
        "\t": sample.count("\t"),
        "|": sample.count("|"),
    }

    separator = max(
        candidates,
        key=candidates.get,
    )

    if candidates[separator] == 0:
        return None

    return separator


def read_raw_csv(csv_text):
    """
    Tenta più modalità di lettura.
    Restituisce il DataFrame con il maggior numero
    di colonne plausibili.
    """
    attempts = []

    detected = detect_separator(csv_text)

    if detected is not None:
        attempts.append(detected)

    for sep in [",", ";", "\t", "|"]:
        if sep not in attempts:
            attempts.append(sep)

    # Prima prova con header.
    best_df = None
    best_score = -1

    for sep in attempts:
        try:
            df = pd.read_csv(
                StringIO(csv_text),
                sep=sep,
                engine="python",
                dtype=str,
            )

            if df.empty:
                continue

            score = len(df.columns)

            column_text = " ".join(
                normalize_col_name(c)
                for c in df.columns
            )

            keywords = [
                "concorso",
                "data",
                "n1",
                "n2",
                "n3",
                "n4",
                "n5",
                "n6",
                "numero",
            ]

            score += sum(
                10
                for word in keywords
                if word in column_text
            )

            if score > best_score:
                best_score = score
                best_df = df

        except Exception:
            continue

    if best_df is None:
        raise ValueError(
            "Impossibile interpretare il bootstrap "
            "come CSV."
        )

    return best_df


# ============================================================
# IDENTIFICAZIONE COLONNE
# ============================================================

def find_number_columns(df):
    columns = list(df.columns)

    explicit = {}

    for number in range(1, NUMBERS_PER_DRAW + 1):
        patterns = [
            rf"^n{number}$",
            rf"^num{number}$",
            rf"^numero{number}$",
            rf"^estratto{number}$",
            rf"^estrazion{number}$",
            rf"^pallina{number}$",
        ]

        found = find_column(
            columns,
            patterns,
        )

        if found is not None:
            explicit[number] = found

    if len(explicit) == NUMBERS_PER_DRAW:
        return [
            explicit[i]
            for i in range(1, NUMBERS_PER_DRAW + 1)
        ]

    # Ricerca di colonne chiaramente dedicate ai sei numeri.
    candidates = []

    excluded_patterns = [
        "jolly",
        "superstar",
        "star",
        "premio",
        "jackpot",
        "vincita",
        "euro",
        "categoria",
        "quota",
        "punti",
        "id",
        "concorso",
        "data",
        "anno",
        "mese",
    ]

    for col in columns:
        name = normalize_col_name(col)

        if any(
            token in name
            for token in excluded_patterns
        ):
            continue

        if re.search(
            r"(numero|num|n|estratto|pallina)",
            name,
        ):
            candidates.append(col)

    if len(candidates) >= NUMBERS_PER_DRAW:
        return candidates[:NUMBERS_PER_DRAW]

    # Fallback: colonne numeriche con prevalenza di valori 1..90.
    numeric_candidates = []

    for col in columns:
        name = normalize_col_name(col)

        if any(
            token in name
            for token in excluded_patterns
        ):
            continue

        parsed = df[col].map(parse_integer)

        valid = parsed.between(
            VALID_NUMBERS_MIN,
            VALID_NUMBERS_MAX,
            inclusive="both",
        )

        ratio = (
            float(valid.mean())
            if len(valid)
            else 0.0
        )

        if ratio >= 0.90:
            numeric_candidates.append(
                (col, ratio)
            )

    numeric_candidates.sort(
        key=lambda item: item[1],
        reverse=True,
    )

    if len(numeric_candidates) >= NUMBERS_PER_DRAW:
        return [
            item[0]
            for item in numeric_candidates[
                :NUMBERS_PER_DRAW
            ]
        ]

    raise ValueError(
        "Impossibile identificare con certezza le "
        "sei colonne dei numeri principali."
    )


def find_contest_column(df):
    patterns = [
        r"\bconcorso\b",
        r"^contest$",
        r"^contestno$",
        r"^numeroestrazione$",
        r"^numconcorso$",
        r"^nconcorso$",
    ]

    return find_column(
        df.columns,
        patterns,
    )


def find_date_column(df):
    patterns = [
        r"\bdata\b",
        r"^date$",
        r"^dataestrazione$",
        r"^datadellestrazione$",
    ]

    return find_column(
        df.columns,
        patterns,
    )


# ============================================================
# PARSING E NORMALIZZAZIONE
# ============================================================

def parse_csv_data(csv_text):
    log("Parsing e normalizzazione CSV...")

    raw_df = read_raw_csv(csv_text)

    log(
        "CSV interpretato: "
        f"{len(raw_df)} righe, "
        f"{len(raw_df.columns)} colonne."
    )

    log(
        "Colonne originali: "
        + ", ".join(str(c) for c in raw_df.columns)
    )

    contest_col = find_contest_column(raw_df)
    date_col = find_date_column(raw_df)

    if contest_col is None:
        raise ValueError(
            "Colonna concorso non identificata."
        )

    if date_col is None:
        raise ValueError(
            "Colonna data non identificata."
        )

    number_cols = find_number_columns(raw_df)

    log(
        f"Colonna concorso: {contest_col}"
    )
    log(
        f"Colonna data: {date_col}"
    )
    log(
        "Colonne numeri principali: "
        + ", ".join(str(c) for c in number_cols)
    )

    parsed_rows = []

    for _, row in raw_df.iterrows():
        contest = parse_contest_value(
            row[contest_col]
        )

        date_value = parse_date_value(
            row[date_col]
        )

        if contest is None or pd.isna(date_value):
            continue

        numbers = [
            parse_integer(row[col])
            for col in number_cols
        ]

        if any(
            value is None
            for value in numbers
        ):
            continue

        if len(numbers) != NUMBERS_PER_DRAW:
            continue

        if any(
            value < VALID_NUMBERS_MIN
            or value > VALID_NUMBERS_MAX
            for value in numbers
        ):
            continue

        if len(set(numbers)) != NUMBERS_PER_DRAW:
            continue

        parsed_rows.append(
            {
                "data": date_value,
                "concorso": int(contest),
                "n1": int(numbers[0]),
                "n2": int(numbers[1]),
                "n3": int(numbers[2]),
                "n4": int(numbers[3]),
                "n5": int(numbers[4]),
                "n6": int(numbers[5]),
            }
        )

    if not parsed_rows:
        raise ValueError(
            "Il parser non ha prodotto alcuna "
            "estrazione valida."
        )

    parsed_df = pd.DataFrame(
        parsed_rows,
        columns=[
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

    parsed_df["data"] = pd.to_datetime(
        parsed_df["data"],
        errors="coerce",
    )

    parsed_df["year"] = (
        parsed_df["data"].dt.year.astype("Int64")
    )

    parsed_df = parsed_df[
        parsed_df["year"].between(
            REQUIRED_START_YEAR,
            CURRENT_YEAR,
        )
    ].copy()

    parsed_df["year"] = (
        parsed_df["year"].astype(int)
    )

    parsed_df = parsed_df.sort_values(
        ["concorso", "data"]
    ).reset_index(drop=True)

    log(
        f"Righe valide dopo normalizzazione: "
        f"{len(parsed_df)}"
    )

    return parsed_df


# ============================================================
# RISOLUZIONE DUPLICATI
# ============================================================

def resolve_duplicates_strict(df):
    log(
        "Verifica unicità della chiave "
        "(anno, concorso)..."
    )

    initial_len = len(df)

    # Duplicati perfettamente identici:
    # sono innocui e vengono eliminati.
    df_dedup = df.drop_duplicates(
        subset=[
            "year",
            "concorso",
            "data",
            "n1",
            "n2",
            "n3",
            "n4",
            "n5",
            "n6",
        ]
    ).copy()

    removed_exact = (
        initial_len - len(df_dedup)
    )

    if removed_exact > 0:
        log(
            f"Rimosse {removed_exact} righe "
            "duplicate identiche."
        )

    conflicts = df_dedup[
        df_dedup.duplicated(
            subset=["year", "concorso"],
            keep=False,
        )
    ].copy()

    if not conflicts.empty:
        log(
            "ERRORE FATALE: conflitto di dati "
            "sullo stesso anno e concorso:"
        )

        log(
            conflicts[
                [
                    "year",
                    "concorso",
                    "data",
                    "n1",
                    "n2",
                    "n3",
                    "n4",
                    "n5",
                    "n6",
                ]
            ].to_string(index=False)
        )

        return None, (
            "Conflitto dati irreconciliabile "
            f"su {len(conflicts)} righe."
        )

    return (
        df_dedup
        .sort_values(
            ["concorso", "data"]
        )
        .reset_index(drop=True),
        None,
    )


# ============================================================
# AUDIT STRUTTURALE
# ============================================================

def audit_structure(df):
    errors = []
    warnings = []

    required_columns = [
        "data",
        "concorso",
        "n1",
        "n2",
        "n3",
        "n4",
        "n5",
        "n6",
        "year",
    ]

    missing = [
        col
        for col in required_columns
        if col not in df.columns
    ]

    if missing:
        errors.append(
            "Colonne mancanti: "
            + ", ".join(missing)
        )
        return errors, warnings

    if df.empty:
        errors.append(
            "Dataset vuoto."
        )
        return errors, warnings

    if len(df) < MIN_TOTAL_RECORDS:
        errors.append(
            "Numero totale di estrazioni insufficiente: "
            f"{len(df)} < {MIN_TOTAL_RECORDS}."
        )

    if df["concorso"].isna().any():
        errors.append(
            "Presenti concorsi nulli."
        )

    if (
        df["concorso"] <= 0
    ).any():
        errors.append(
            "Presenti numeri di concorso <= 0."
        )

    if df["data"].isna().any():
        errors.append(
            "Presenti date non valide."
        )

    if (
        df["year"] < REQUIRED_START_YEAR
    ).any():
        errors.append(
            "Presenti record antecedenti al "
            f"{REQUIRED_START_YEAR}."
        )

    if (
        df["year"] > CURRENT_YEAR
    ).any():
        errors.append(
            "Presenti record successivi "
            "all'anno corrente."
        )

    # Controllo dei sei numeri.
    for col in [
        "n1",
        "n2",
        "n3",
        "n4",
        "n5",
        "n6",
    ]:
        invalid = (
            ~df[col].between(
                VALID_NUMBERS_MIN,
                VALID_NUMBERS_MAX,
            )
        )

        if invalid.any():
            errors.append(
                f"Valori fuori range 1..90 "
                f"nella colonna {col}: "
                f"{int(invalid.sum())}."
            )

    # Nessun doppione nella stessa estrazione.
    duplicate_numbers = []

    for index, row in df.iterrows():
        numbers = [
            int(row[f"n{i}"])
            for i in range(1, 7)
        ]

        if len(set(numbers)) != 6:
            duplicate_numbers.append(index)

    if duplicate_numbers:
        errors.append(
            "Presenti estrazioni con numeri "
            "duplicati internamente: "
            f"{len(duplicate_numbers)}."
        )

    # Controllo coerenza anno/data.
    date_year_mismatch = (
        df["data"].dt.year != df["year"]
    )

    if date_year_mismatch.any():
        errors.append(
            "Incoerenza tra colonna year e data: "
            f"{int(date_year_mismatch.sum())}."
        )

    return errors, warnings


# ============================================================
# AUDIT COPERTURA TEMPORALE
# ============================================================

def audit_coverage(df):
    errors = []
    warnings = []

    years_present = set(
        int(year)
        for year in df["year"].unique()
    )

    expected_years = set(
        range(
            REQUIRED_START_YEAR,
            CURRENT_YEAR + 1,
        )
    )

    missing_years = sorted(
        expected_years - years_present
    )

    if missing_years:
        errors.append(
            "Anni completamente mancanti "
            "nell'archivio: "
            + ", ".join(
                str(year)
                for year in missing_years
            )
        )

    yearly_counts = (
        df.groupby("year")
        .size()
        .to_dict()
    )

    # Gli anni storici completi devono avere almeno
    # 80 estrazioni. L'anno corrente è escluso da questo
    # requisito perché è ancora in corso.
    for year in range(
        REQUIRED_START_YEAR,
        CURRENT_YEAR,
    ):
        count = int(
            yearly_counts.get(year, 0)
        )

        if count < MIN_HISTORICAL_DRAWS_PER_YEAR:
            errors.append(
                f"Anno {year}: solo {count} "
                "estrazioni; minimo richiesto "
                f"{MIN_HISTORICAL_DRAWS_PER_YEAR}."
            )

    current_count = int(
        yearly_counts.get(CURRENT_YEAR, 0)
    )

    if current_count <= 0:
        errors.append(
            f"Nessuna estrazione presente "
            f"per l'anno corrente {CURRENT_YEAR}."
        )

    # Verifica che le finestre sperimentali siano
    # realmente coperte.
    windows = [
        (
            "training",
            TRAINING_START_YEAR,
            TRAINING_END_YEAR,
        ),
        (
            "discovery",
            DISCOVERY_START_YEAR,
            DISCOVERY_END_YEAR,
        ),
        (
            "confirmation",
            CONFIRMATION_START_YEAR,
            CURRENT_YEAR,
        ),
    ]

    for name, start, end in windows:
        window_df = df[
            df["year"].between(start, end)
        ]

        if window_df.empty:
            errors.append(
                f"Finestra {name} completamente vuota."
            )

    # Controllo della sequenza dei concorsi.
    contests = (
        df["concorso"]
        .dropna()
        .astype(int)
        .sort_values()
        .tolist()
    )

    if contests:
        gaps = []

        previous = contests[0]

        for current in contests[1:]:
            if current > previous + 1:
                gaps.append(
                    (
                        previous,
                        current,
                        current - previous - 1,
                    )
                )

            previous = current

        if gaps:
            preview = gaps[:20]

            warnings.append(
                "Rilevati buchi nella sequenza dei "
                "numeri di concorso. Prime occorrenze: "
                + "; ".join(
                    f"{a}->{b} "
                    f"(mancano {missing})"
                    for a, b, missing in preview
                )
            )

    # Controllo date monotone rispetto al concorso.
    ordered = df.sort_values(
        "concorso"
    )

    if not ordered["data"].is_monotonic_increasing:
        warnings.append(
            "La data non è perfettamente monotona "
            "rispetto al numero di concorso."
        )

    return errors, warnings


# ============================================================
# AUDIT DUPLICATI GLOBALI
# ============================================================

def audit_global_contest_consistency(df):
    errors = []
    warnings = []

    grouped = df.groupby(
        "concorso",
        dropna=False,
    )

    cross_year_conflicts = []

    for contest, group in grouped:
        years = group["year"].unique()

        if len(years) > 1:
            cross_year_conflicts.append(
                int(contest)
            )

    if cross_year_conflicts:
        errors.append(
            "Lo stesso numero di concorso compare "
            "in anni differenti: "
            + ", ".join(
                str(x)
                for x in cross_year_conflicts[:50]
            )
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
