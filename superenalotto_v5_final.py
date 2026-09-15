# SUPERNALOTTO PIPELINE V5.2.5
# STRICT DATA INTEGRITY AUDIT — ENGINE DISABLED
#
# SCOPO:
#   1. Recuperare l'archivio storico.
#   2. Normalizzarlo.
#   3. Controllare l'integrità delle sestine.
#   4. Controllare l'univocità dei concorsi.
#   5. Controllare la copertura temporale.
#   6. Generare:
#        - superenalotto_storico.csv
#        - integrity_report.json
#
# NON:
#   - genera numeri da giocare
#   - esegue strategie
#   - esegue Monte Carlo
#   - modifica parametri statistici
#
# FAIL-CLOSED:
#   qualsiasi conflitto reale sui dati blocca l'esperimento.

import os
import re
import sys
import json
import time
from io import StringIO
from datetime import datetime

import pandas as pd
import numpy as np
import requests


# ============================================================
# CONFIGURAZIONE
# ============================================================

REQUIRED_START_YEAR = 1997
CURRENT_YEAR = datetime.now().year

OUTPUT_CSV = "superenalotto_storico.csv"
AUDIT_REPORT = "integrity_report.json"

# Endpoint CSV dell'archivio SuperEnalotto di estrazioni.it
BOOTSTRAP_URL = (
    "https://www.estrazioni.it/"
    "index.php?formato=csv&p=download&tipo=superenalotto"
)

HTTP_TIMEOUT = 30
HTTP_RETRIES = 3

# Non imponiamo un numero arbitrario di estrazioni per l'anno
# corrente, perché l'anno corrente è incompleto.
#
# Per gli anni storici, tuttavia, un archivio con pochissime
# estrazioni è chiaramente sospetto.
MIN_HISTORICAL_DRAWS_PER_YEAR = 80


# ============================================================
# SESSIONE HTTP
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    ),
    "Accept": (
        "text/csv,text/plain,text/html,"
        "application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
})


# ============================================================
# LOG
# ============================================================

def log(msg):
    print(
        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}",
        flush=True
    )


# ============================================================
# UTILITÀ
# ============================================================

def clean_col(name):
    return str(name).strip().lower()


def normalize_text(value):
    value = str(value)
    value = value.strip().lower()
    value = re.sub(r"\s+", " ", value)
    return value


def find_column(columns, patterns):
    """
    Cerca una colonna utilizzando regex ordinate.
    """
    for col in columns:
        name = clean_col(col)

        for pattern in patterns:
            if re.search(pattern, name):
                return col

    return None


def parse_integer(value):
    """
    Estrae un intero da un campo.
    Restituisce None se non esiste un numero.
    """
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

            log(f"Tentativo {attempt}/{HTTP_RETRIES}...")

            response = SESSION.get(
                BOOTSTRAP_URL,
                timeout=HTTP_TIMEOUT
