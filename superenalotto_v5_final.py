# ============================================================
# SUPERNALOTTO PIPELINE V5.2.5 (DEFINITIVA - AUDIT RIGIDO)
# STRICT DATA INTEGRITY AUDIT — ENGINE DISABLED
#
# SCOPO:
#   Audit rigoroso dell'archivio storico SuperEnalotto.
#
# ENGINE:
#   COMPLETAMENTE DISABILITATO.
# ============================================================

import os
import re
import sys
import json
import time
from io import StringIO
from datetime import datetime

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

CURRENT_YEAR = datetime.now().year  # 2026

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

# ============================================================

# SESSIONE HTTP

# ============================================================

SESSION = requests.Session()
SESSION.headers.update({
"User-Agent": (
"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
"AppleWebKit/537.36 (KHTML, like Gecko) "
"Chrome/120.0 Safari/537.36"
),
"Accept": (
"text/csv,text/plain,text/html,"
"application/xhtml+xml,application/xml;q=0.9,/;q=0.8"
),
"Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
})

# ============================================================

# LOG & FAIL-CLOSED PREVENTIVO

# ============================================================

de
def log(message):
    print(
        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}",
        flush=True
    )
def remove_stale_dataset():
    """
    Fail-Closed Rigido: Se il dataset precedente esiste e non può essere eliminato,
    l'esecuzione DEVE bloccarsi immediatamente.
    """
    if os.path.exists(OUTPUT_CSV):
        try:
            os.remove(OUTPUT_CSV)
            log(f"Dataset precedente '{OUTPUT_CSV}' eliminato con successo.")
        except Exception as exc:
            raise RuntimeError(
                f"FAIL-CLOSED FATALE: Impossibile eliminare il dataset precedente "
                f"'{OUTPUT_CSV}': {exc}"
            )

def write_report(status, total_records, errors, warnings, df=None):
    report = {
        "timestamp": datetime.now().isoformat(),
        "status": status,
        "total_records": int(total_records),
        "required_start_year": REQUIRED_START_YEAR,
        "current_year": CURRENT_YEAR,
        "windows": {
            "training": [TRAINING_START_YEAR, TRAINING_END_YEAR],
            "discovery": [DISCOVERY_START_YEAR, DISCOVERY_END_YEAR],
            "confirmation": [CONFIRMATION_START_YEAR, CURRENT_YEAR]
        },
        "errors": list(errors),
        "warnings": list(warnings),
    }

    if df is not None and not df.empty:
        report["date_range"] = {
            "min": str(df["data"].min()),
            "max": str(df["data"].max())
        }

        report["contest_range"] = {
            "min": int(df["concorso"].min()),
            "max": int(df["concorso"].max())
        }

        yearly_counts = df.groupby("year").size().astype(int).to_dict()
        report["yearly_counts"] = {
            str(k): int(v) for k, v in yearly_counts.items()
        }

        report["window_counts"] = {
            "training_1997_2015": int(
                ((df["year"] >= TRAINING_START_YEAR) &
                 (df["year"] <= TRAINING_END_YEAR)).sum()
            ),
            "discovery_2016_2021": int(
                ((df["year"] >= DISCOVERY_START_YEAR) &
                 (df["year"] <= DISCOVERY_END_YEAR)).sum()
            ),
            "confirmation_2022_current": int(
                ((df["year"] >= CONFIRMATION_START_YEAR) &
                 (df["year"] <= CURRENT_YEAR)).sum()
            )
        }

    try:
        with open(AUDIT_REPORT, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False)

        log(f"Report di integrità generato in '{AUDIT_REPORT}'.")

    except Exception as exc:
        log(
            f"ERRORE CRITICO nella scrittura del report "
            f"'{AUDIT_REPORT}': {exc}"
        )
try:  
    with open(AUDIT_REPORT, "w", encoding="utf-8") as handle:  
        json.dump(report, handle, indent=2, ensure_ascii=False)  
    log(f"Report di integrità generato in '{AUDIT_REPORT}'.")  
except Exception as exc:  
    log(f"ERRORE CRITICO nella scrittura del report '{AUDIT_REPORT}': {exc}")

# ============================================================

PARSING & UTILITIES

# ============================================================

def clean_col(name):
    return str(name).strip().lower()

def find_column(columns, patterns):
    for col in columns:
        name = clean_col(col)
        for pattern in patterns:
            if re.search(pattern, name):
                return col
    return None
return None

def parse_integer(value):
if value is None:
return None
text = str(value).strip()
if not text or text.lower() == "nan":
return None
match = re.search(r"\d+", text)
if not match:
return None
try:
return int(match.group(0))
except Exception:
return None

def download_bootstrap():
log("=======================================================")
log("DOWNLOAD ARCHIVIO BOOTSTRAP")
log("=======================================================")
log(f"Fonte: {BOOTSTRAP_URL}")

last_error = None  
for attempt in range(1, HTTP_RETRIES + 1):  
    try:  
        log(f"Tentativo {attempt}/{HTTP_RETRIES}...")  
        response = SESSION.get(BOOTSTRAP_URL, timeout=HTTP_TIMEOUT)  
        response.raise_for_status()  

        text = response.text.strip()  
        if not text:  
            raise ValueError("Risposta vuota dal server HTTP.")  

        log(f"Download completato ({len(text)} caratteri).")  
        return text  
    except Exception as exc:  
        last_error = exc  
        log(f"Fallito tentativo {attempt}: {exc}")  
        if attempt < HTTP_RETRIES:  
            time.sleep(HTTP_RETRY_SLEEP)  

raise RuntimeError(  
    f"Impossibile scaricare il bootstrap dopo {HTTP_RETRIES} tentativi: {last_error}"  
)

def parse_csv_data(csv_text):
log("Parsing e normalizzazione CSV...")

sample = csv_text[:4096]  
sep = ';' if sample.count(';') > sample.count(',') else ','  

df = pd.read_csv(  
    StringIO(csv_text),  
    sep=sep,  
    dtype=str,  
    encoding='latin-1'  
)  

columns = list(df.columns)  
col_data = find_column(columns, [r"data", r"date"])  
col_concorso = find_column(columns, [r"conc", r"n°", r"num", r"estraz"])  

col_n1 = find_column(columns, [r"^n1$", r"estratto_1", r"1°", r"^1$"])  
col_n2 = find_column(columns, [r"^n2$", r"estratto_2", r"2°", r"^2$"])  
col_n3 = find_column(columns, [r"^n3$", r"estratto_3", r"3°", r"^3$"])  
col_n4 = find_column(columns, [r"^n4$", r"estratto_4", r"4°", r"^4$"])  
col_n5 = find_column(columns, [r"^n5$", r"estratto_5", r"5°", r"^5$"])  
col_n6 = find_column(columns, [r"^n6$", r"estratto_6", r"6°", r"^6$"])  

if not col_data or not col_concorso or not all([col_n1, col_n2, col_n3, col_n4, col_n5, col_n6]):  
    raise ValueError(f"Mappatura colonne fallita. Colonne identificate: {columns}")  

rows = []  
for _, row in df.iterrows():  
    raw_date = row.get(col_data)  
    if pd.isna(raw_date):  
        continue  

    date_obj = pd.to_datetime(raw_date, dayfirst=True, errors='coerce')  
    if pd.isna(date_obj):  
        continue  

    conc_val = parse_integer(row.get(col_concorso))  
    if conc_val is None:  
        continue  

    n1 = parse_integer(row.get(col_n1))  
    n2 = parse_integer(row.get(col_n2))  
    n3 = parse_integer(row.get(col_n3))  
    n4 = parse_integer(row.get(col_n4))  
    n5 = parse_integer(row.get(col_n5))  
    n6 = parse_integer(row.get(col_n6))  

    nums = [n1, n2, n3, n4, n5, n6]  
    if any(x is None for x in nums) or not all(1 <= x <= 90 for x in nums):  
        continue  
    if len(set(nums)) != 6:  
        continue  

    rows.append({  
        "data": date_obj.strftime("%Y-%m-%d"),  
        "year": int(date_obj.year),  
        "concorso": int(conc_val),  
        "n1": n1, "n2": n2, "n3": n3,  
        "n4": n4, "n5": n5, "n6": n6  
    })  

parsed_df = pd.DataFrame(rows)  
if parsed_df.empty:  
    raise ValueError("Nessun record valido estratto dal file CSV.")  

return parsed_df

def resolve_duplicates_strict(df):
log("Verifica unicità della chiave (anno, concorso)...")
initial_len = len(df)

df_dedup = df.drop_duplicates(  
    subset=["year", "concorso", "data", "n1", "n2", "n3", "n4", "n5", "n6"]  
)  
removed_exact = initial_len - len(df_dedup)  
if removed_exact > 0:  
    log(f"Rimosse {removed_exact} righe duplicate identiche.")  

conflicts = df_dedup[df_dedup.duplicated(subset=["year", "concorso"], keep=False)]  
if not conflicts.empty:  
    log("ERRORE FATALE: Conflitto di estratti su medesimo anno e concorso:")  
    log(conflicts[["year", "concorso", "data", "n1", "n2", "n3", "n4", "n5", "n6"]].to_string())  
    return None, f"Conflitto dati irreconciliabile su {len(conflicts)} righe."  

return df_dedup, None

# ============================================================

SECUENTIALLY CONTROLLED AUDIT (FAIL-CLOSED)

# ============================================================

def run_strict_data_audit():
log("=======================================================")
log("   SUPERNALOTTO PIPELINE V5.2.5 - STRICT DATA AUDIT")
log("=======================================================")

# 1. Rimuovi vecchio dataset (interrompe l'esecuzione se fallisce)  
remove_stale_dataset()  

errors = []  
warnings = []  
df_clean = None  

# 2. Download e parsing  
try:  
    csv_text = download_bootstrap()  
    df_parsed = parse_csv_data(csv_text)  
    df_clean, conflict_err = resolve_duplicates_strict(df_parsed)  

    if conflict_err:  
        errors.append(conflict_err)  

except Exception as exc:  
    log(f"ERRORE AUDIT BOOTSTRAP: {exc}")  
    errors.append(str(exc))  

if errors or df_clean is None or df_clean.empty:  
    write_report("FAILED", 0, errors, warnings)  
    log("=======================================================")  
    log("   STATO AUDIT DATA LAYER: FAILED (Errore caricamento sorgente)")  
    log("=======================================================")  
    sys.exit(1)  

df_clean = df_clean.sort_values(["data", "concorso"]).reset_index(drop=True)  

# 3. Controllo volume minimo assoluto  
if len(df_clean) < MIN_TOTAL_RECORDS:  
    errors.append(  
        f"ERRORE VOLUME TOTALE: Trovate solo {len(df_clean)} estrazioni (minimo richiesto: {MIN_TOTAL_RECORDS})."  
    )  

min_year = int(df_clean["year"].min())  
max_year = int(df_clean["year"].max())  
years_present = set(df_clean["year"].unique())  

# 4. Verifica Finestra Training (1997-2015)  
if min_year > REQUIRED_START_YEAR:  
    errors.append(  
        f"ERRORE COPERTURA START: L'archivio parte dal {min_year}, ma il punto d'inizio "  
        f"obbligatorio è il {REQUIRED_START_YEAR}."  
    )  

training_years_missing = set(range(TRAINING_START_YEAR, TRAINING_END_YEAR + 1)) - years_present  
if training_years_missing:  
    errors.append(  
        f"ERRORE TRAINING (1997-2015): Anni completamente mancanti: {sorted(list(training_years_missing))}"  
    )  

# 5. Verifica Finestra Discovery (2016-2021)  
discovery_years_missing = set(range(DISCOVERY_START_YEAR, DISCOVERY_END_YEAR + 1)) - years_present  
if discovery_years_missing:  
    errors.append(  
        f"ERRORE DISCOVERY (2016-2021): Anni completamente mancanti: {sorted(list(discovery_years_missing))}"  
    )  

# 6. Verifica Finestra Confirmation (2022-2026)  
confirmation_years_missing = set(range(CONFIRMATION_START_YEAR, CURRENT_YEAR + 1)) - years_present  
if confirmation_years_missing:  
    errors.append(  
        f"ERRORE CONFIRMATION (2022-{CURRENT_YEAR}): Anni mancanti nell'intervallo corrente: {sorted(list(confirmation_years_missing))}"  
    )  

# 7. Verifica anno di arrivo tassativo (2026)  
if max_year < CURRENT_YEAR:  
    errors.append(  
        f"ERRORE COPERTURA CURRENT: L'archivio si arresta al {max_year}, "  
        f"ma deve arrivare tassativamente fino all'anno corrente {CURRENT_YEAR}."  
    )  

# 8. Controlli densità per ciascun anno storico concluso  
for y in sorted(list(years_present)):  
    if y < CURRENT_YEAR:  
        count = len(df_clean[df_clean["year"] == y])  
        if count < MIN_HISTORICAL_DRAWS_PER_YEAR:  
            errors.append(  
                f"ERRORE DENSITA HISTORICAL: Anno {y} ha solo {count} estrazioni "  
                f"(minimo richiesto: {MIN_HISTORICAL_DRAWS_PER_YEAR})."  
            )  

# 9. Controllo lacune numeriche concorsi  
total_gaps = 0  
for y, group in df_clean.groupby("year"):  
    conc_list = group["concorso"].sort_values().values  
    if len(conc_list) > 1:  
        diffs = conc_list[1:] - conc_list[:-1]  
        gaps = (diffs > 1).sum()  
        total_gaps += gaps  

if total_gaps > 0:  
    warnings.append(f"Trovate {total_gaps} lacune nella sequenza progressiva dei concorsi.")  

# 10. Esito finale Audit  
if errors:  
    status = "FAILED"  
elif warnings:  
    status = "VALID_WITH_WARNINGS"  
else:  
    status = "VERIFIED_VALID"  

# 11. Scrittura condizionata del dataset (Fail-Closed)  
if status in ("VERIFIED_VALID", "VALID_WITH_WARNINGS"):  
    df_clean.to_csv(OUTPUT_CSV, index=False)  
    log(f"Dataset storico validato ed esportato in '{OUTPUT_CSV}' ({len(df_clean)} estrazioni).")  
else:  
    log("FAIL-CLOSED ATTIVO: Nessun file CSV generato a causa di errori bloccanti sull'archivio sorgente.")  

write_report(status, len(df_clean), errors, warnings, df_clean)  

log("=======================================================")  
log(f"   STATO AUDIT DATA LAYER: {status}")  
log("=======================================================")  

if status == "FAILED":  
    sys.exit(1)

if name == "main":
run_strict_data_audit()
