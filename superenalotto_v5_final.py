# ============================================================
# SUPERNALOTTO ENGINE V5 FINAL
# ============================================================

import os
import re
import math
import time
import requests
import numpy as np
import pandas as pd

from bs4 import BeautifulSoup
from dataclasses import dataclass
from datetime import datetime


# ============================================================
# CONFIGURAZIONE
# ============================================================

BASE_URL = "https://www.superenalotto.it/archivio-estrazioni"

FIRST_YEAR = 1997
LAST_YEAR = 2026

DISCOVERY_START = "2016-01-01"
DISCOVERY_END   = "2021-12-31"

CONFIRMATION_START = "2022-01-01"
CONFIRMATION_END   = "2026-12-31"

N_NULL = 5000
MIN_HISTORY = 100
SEED = 123456
ALPHA = 0.05

STRATEGIES = [
    "FREQUENCY",
    "DELAY",
    "MIXED"
]

REQUEST_DELAY = 0.15

OUTPUT_CSV = "superenalotto_storico.csv"
QUALITY_REPORT = "superenalotto_data_quality.csv"

MONTHS = {
    1: "gennaio", 2: "febbraio", 3: "marzo", 4: "aprile",
    5: "maggio", 6: "giugno", 7: "luglio", 8: "agosto",
    9: "settembre", 10: "ottobre", 11: "novembre", 12: "dicembre"
}

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0 Safari/537.36"
    )
})


# ============================================================
# PARSING ED INGESTION DATI
# ============================================================

def parse_month_page(year: int, month: int):
    month_name = MONTHS[month]
    url = f"{BASE_URL}/{year}/{month_name}"
    print(f"   Scaricamento {year}-{month:02d} -> {url}")

    try:
        response = SESSION.get(url, timeout=30)
        if response.status_code != 200:
            return []
        soup = BeautifulSoup(response.text, "html.parser")
    except Exception as e:
        print(f"      [!] ERRORE HTTP: {e}")
        return []

    records = []
    text = soup.get_text("\n", strip=True)
    lines = [x.strip() for x in text.splitlines() if x.strip()]

    i = 0
    while i < len(lines):
        line = lines[i]

        match = re.search(
            r"Concorso\s*(?:Nº|N°|No\.?|n\.)?\s*(\d+)\s+del\s+(\d{1,2})\s+([A-Za-zÀ-ÿ]+)\s+(\d{4})",
            line,
            re.IGNORECASE
        )

        if not match:
            i += 1
            continue

        contest = int(match.group(1))
        day = int(match.group(2))
        month_text = match.group(3).lower()
        year_text = int(match.group(4))

        month_reverse = {v: k for k, v in MONTHS.items()}
        if month_text not in month_reverse:
            i += 1
            continue

        real_month = month_reverse[month_text]

        try:
            date = pd.Timestamp(year_text, real_month, day)
        except Exception:
            i += 1
            continue

        numbers = []
        jolly = None
        superstar = None

        j = i + 1
        while j < len(lines) and j < i + 40:
            current = lines[j]

            if current.lower() == "jolly":
                if j + 1 < len(lines):
                    try:
                        candidate = int(lines[j + 1])
                        if 1 <= candidate <= 90:
                            jolly = candidate
                    except Exception:
                        pass
                j += 2
                continue

            if current.lower() == "superstar":
                if j + 1 < len(lines):
                    try:
                        candidate = int(lines[j + 1])
                        if 1 <= candidate <= 90:
                            superstar = candidate
                    except Exception:
                        pass
                j += 2
                continue

            if re.fullmatch(r"\d{1,2}", current):
                n = int(current)
                if 1 <= n <= 90:
                    numbers.append(n)
                    if len(numbers) == 6:
                        break

            j += 1

        if len(numbers) == 6:
            numbers = sorted(numbers)
            if len(set(numbers)) == 6:
                records.append({
                    "concorso": contest,
                    "data": date,
                    "n1": numbers[0], "n2": numbers[1], "n3": numbers[2],
                    "n4": numbers[3], "n5": numbers[4], "n6": numbers[5],
                    "jolly": jolly, "superstar": superstar,
                    "source": url
                })

        i = j + 1

    return records


def download_archive():
    print("\n" + "=" * 80)
    print("VERIFICA ED INGESTION ARCHIVIO SUPERNALOTTO")
    print("=" * 80)

    # 1. Se il CSV locale esiste, viene caricato evitando lo scraping
    if os.path.exists(OUTPUT_CSV):
        print(f"[+] Trovato archivio locale: {OUTPUT_CSV}. Caricamento in corso...")
        df_local = pd.read_csv(OUTPUT_CSV)
        if not df_local.empty:
            print(f"[+] Caricate {len(df_local)} estrazioni dal file CSV locale.")
            return df_local

    # 2. Se non è presente il CSV locale, avvia lo scraping online
    print("[!] Archivio locale non trovato. Avvio scraping da web...")
    all_records = []
    for year in range(FIRST_YEAR, LAST_YEAR + 1):
        for month in range(1, 13):
            records = parse_month_page(year, month)
            all_records.extend(records)
            time.sleep(REQUEST_DELAY)

    if not all_records:
        raise RuntimeError("Nessuna estrazione scaricata dal web e nessun file CSV locale trovato.")

    return pd.DataFrame(all_records)


def validate_archive(df):
    print("\n" + "=" * 80)
    print("VALIDAZIONE SANITÀ E QUALITÀ DATASET")
    print("=" * 80)

    report = []
    df["data"] = pd.to_datetime(df["data"], errors="coerce")
    df = df.sort_values(["data", "concorso"]).reset_index(drop=True)

    duplicate_contests = df["concorso"].duplicated(keep=False)
    duplicate_count = int(duplicate_contests.sum())
    report.append({"check": "duplicate_concorso", "value": duplicate_count, "status": "OK" if duplicate_count == 0 else "ATTENZIONE"})

    bad_rows = []
    number_columns = ["n1", "n2", "n3", "n4", "n5", "n6"]

    for idx, row in df.iterrows():
        nums = [int(row[c]) for c in number_columns]
        valid = (len(nums) == 6 and len(set(nums)) == 6 and all(1 <= n <= 90 for n in nums))
        if not valid:
            bad_rows.append(idx)

    report.append({"check": "invalid_sestine", "value": len(bad_rows), "status": "OK" if len(bad_rows) == 0 else "ERRORE"})

    df = df.drop_duplicates(subset=["concorso"], keep="first").reset_index(drop=True)
    if bad_rows:
        df = df.drop(index=bad_rows, errors="ignore").reset_index(drop=True)

    df = df.sort_values(["data", "concorso"]).reset_index(drop=True)

    quality = pd.DataFrame(report)
    quality.to_csv(QUALITY_REPORT, index=False)

    print(f"Estrazioni valide finali : {len(df)}")
    print(f"Prima estrazione        : {df['data'].min().date()}")
    print(f"Ultima estrazione       : {df['data'].max().date()}")

    return df


def save_archive(df):
    columns = ["concorso", "data", "n1", "n2", "n3", "n4", "n5", "n6", "jolly", "superstar", "source"]
    for c in columns:
        if c not in df.columns:
            df[c] = None

    df[columns].to_csv(OUTPUT_CSV, index=False)
    print(f"\n[OK] Dataset salvato in: {OUTPUT_CSV}")


# ============================================================
# STATISTICAL ENGINE V5
# ============================================================

@dataclass
class OOSResult:
    strategy: str
    draws: int
    total_hits: int
    mean_hits: float
    distribution: np.ndarray


class SuperEnalottoEngineV5:

    def __init__(self, df):
        self.df = df.sort_values(["data", "concorso"]).reset_index(drop=True)
        self.draws = self.df[["n1", "n2", "n3", "n4", "n5", "n6"]].to_numpy(dtype=np.int16)
        self.dates = self.df["data"].to_numpy()

    @staticmethod
    def build_state(history):
        frequency = np.zeros(91, dtype=np.int32)
        delay = np.zeros(91, dtype=np.int32)

        for draw in history:
            delay[1:] += 1
            for n in draw:
                frequency[n] += 1
                delay[n] = 0

        return frequency, delay

    @staticmethod
    def select_numbers(strategy, frequency, delay, rng):
        numbers = np.arange(1, 91)

        if strategy == "FREQUENCY":
            weights = frequency[1:].astype(float)
            if weights.sum() == 0:
                return np.sort(rng.choice(numbers, 6, replace=False))
            weights += 1e-12
            prob = weights / weights.sum()
            return np.sort(rng.choice(numbers, 6, replace=False, p=prob))

        if strategy == "DELAY":
            weights = delay[1:].astype(float)
            if weights.sum() == 0:
                return np.sort(rng.choice(numbers, 6, replace=False))
            weights += 1e-12
            prob = weights / weights.sum()
            return np.sort(rng.choice(numbers, 6, replace=False, p=prob))

        if strategy == "MIXED":
            f = frequency[1:].astype(float)
            d = delay[1:].astype(float)
            if f.max() > 0: f /= f.max()
            if d.max() > 0: d /= d.max()

            score = 0.5 * f + 0.5 * d
            top = np.argsort(score)[-30:]
            candidates = numbers[top]
            c_scores = score[top]

            if c_scores.sum() <= 0:
                return np.sort(rng.choice(candidates, 6, replace=False))

            prob = c_scores / c_scores.sum()
            return np.sort(rng.choice(candidates, 6, replace=False, p=prob))

        raise ValueError(f"Strategia sconosciuta: {strategy}")

    @staticmethod
    def update_state(actual, frequency, delay):
        delay[1:] += 1
        for n in actual:
            frequency[n] += 1
            delay[n] = 0

    def run_oos(self, strategy, start_date, end_date, seed):
        start_date = pd.Timestamp(start_date)
        end_date = pd.Timestamp(end_date)

        oos_indices = np.where((self.dates >= start_date) & (self.dates <= end_date))[0]
        if len(oos_indices) == 0:
            raise ValueError(f"Nessuna estrazione nella finestra {start_date.date()} - {end_date.date()}")

        first_oos = oos_indices[0]
        if first_oos < MIN_HISTORY:
            raise ValueError("Storico precedente insufficiente.")

        rng = np.random.default_rng(seed)
        history = self.draws[:first_oos]
        frequency, delay = self.build_state(history)

        distribution = np.zeros(7, dtype=np.int64)
        total_hits = 0

        for idx in oos_indices:
            ticket = self.select_numbers(strategy, frequency, delay, rng)
            actual = self.draws[idx]
            hits = len(set(ticket).intersection(actual))

            distribution[hits] += 1
            total_hits += hits
            self.update_state(actual, frequency, delay)

        draws = len(oos_indices)
        return OOSResult(
            strategy=strategy, draws=draws,
            total_hits=total_hits, mean_hits=total_hits / draws,
            distribution=distribution
        )

    def null_simulation(self, strategy, start_date, end_date, n_simulations, seed):
        start_date = pd.Timestamp(start_date)
        end_date = pd.Timestamp(end_date)

        oos_indices = np.where((self.dates >= start_date) & (self.dates <= end_date))[0]
        first_oos = oos_indices[0]
        initial_history = self.draws[:first_oos].copy()
        oos_length = len(oos_indices)

        master_rng = np.random.default_rng(seed)
        null_totals = np.zeros(n_simulations, dtype=np.int32)

        for sim in range(n_simulations):
            sim_seed = master_rng.integers(0, 2**63 - 1)
            rng = np.random.default_rng(sim_seed)
            frequency, delay = self.build_state(initial_history)
            total_hits = 0

            for _ in range(oos_length):
                ticket = self.select_numbers(strategy, frequency, delay, rng)
                actual = np.sort(rng.choice(np.arange(1, 91), size=6, replace=False))
                hits = len(set(ticket).intersection(actual))

                total_hits += hits
                self.update_state(actual, frequency, delay)

            null_totals[sim] = total_hits

        return null_totals

    def evaluate_phase(self, phase_name, start_date, end_date, strategies, n_null, seed, alpha):
        print("\n" + "=" * 90)
        print(f"FASE: {phase_name} ({start_date} -> {end_date})")
        print("=" * 90)

        n_strategies = len(strategies)
        alpha_bonferroni = alpha / n_strategies
        print(f"Strategie testate: {n_strategies} | Alpha Bonferroni: {alpha_bonferroni:.8f}")

        results = []
        for strategy_id, strategy in enumerate(strategies):
            strategy_seed = seed + strategy_id * 100000
            observed = self.run_oos(strategy, start_date, end_date, strategy_seed)

            print(f"\n[+] Strategia: {strategy} | Draw OOS: {observed.draws} | Hit Totali: {observed.total_hits}")
            null = self.null_simulation(strategy, start_date, end_date, n_null, strategy_seed + 999999)

            p_value = (np.sum(null >= observed.total_hits) + 1) / (len(null) + 1)
            passed = p_value < alpha_bonferroni

            print(f"    Media H0: {null.mean():.2f} ± {null.std(ddof=1):.2f} | p-value: {p_value:.8f} | Passato: {'SI' if passed else 'NO'}")

            results.append({
                "phase": phase_name, "strategy": strategy, "draws": observed.draws,
                "observed_hits": observed.total_hits, "observed_mean": observed.mean_hits,
                "null_mean": null.mean(), "null_std": null.std(ddof=1),
                "p_value": p_value, "alpha_bonferroni": alpha_bonferroni, "passed": passed
            })

        return pd.DataFrame(results)

    def run_protocol(self, strategies, n_null, seed, alpha):
        discovery_df = self.evaluate_phase(
            "DISCOVERY OOS", DISCOVERY_START, DISCOVERY_END, strategies, n_null, seed, alpha
        )

        passed_discovery = discovery_df[discovery_df["passed"]]["strategy"].tolist()

        if not passed_discovery:
            print("\n" + "=" * 90)
            print("ESITO FINALE: Nessuna strategia ha superato la soglia di significatività di Bonferroni.")
            print("-> L'IPOTESI NULLA H0 NON VIENE RIGETTATA.")
            print("-> STUDIO CHIUSO (Nessuna strategia ammessa alla Confirmation OOS).")
            print("=" * 90)
            discovery_df.to_csv("v5_results_discovery.csv", index=False)
            return discovery_df

        confirmation_df = self.evaluate_phase(
            "CONFIRMATION OOS", CONFIRMATION_START, CONFIRMATION_END, passed_discovery, n_null, seed + 500000, alpha
        )

        all_results = pd.concat([discovery_df, confirmation_df], ignore_index=True)
        all_results.to_csv("v5_results_full_protocol.csv", index=False)
        return all_results


# ============================================================
# MAIN PIPELINE
# ============================================================

if __name__ == "__main__":
    try:
        df_raw = download_archive()
        df_clean = validate_archive(df_raw)
        save_archive(df_clean)

        engine = SuperEnalottoEngineV5(df_clean)
        results = engine.run_protocol(
            strategies=STRATEGIES,
            n_null=N_NULL,
            seed=SEED,
            alpha=ALPHA
        )

    except Exception as e:
        print(f"\n[!] ERRORE PIPELINE: {e}")
