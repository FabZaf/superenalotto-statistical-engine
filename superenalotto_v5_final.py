# SUPERNALOTTO ENGINE V5.2.3 FINAL
# Continuous Discovery -> Confirmation Validation Engine

import os
import sys
import re
import time
import json
import math
import random
import glob
from io import StringIO
from datetime import datetime
import pandas as pd
import numpy as np
from bs4 import BeautifulSoup
import requests

# ---------------------------------------------------------
# GLOBAL CONFIG
# ---------------------------------------------------------
FIRST_YEAR = 1997
LAST_YEAR = datetime.now().year

CSV_BOOTSTRAP_URL = "https://www.superenalotto.it/archivi/estrazioni-superenalotto.csv"
OFFICIAL_BASE = "https://www.superenalotto.it/archivio-estrazioni"

HTTP_TIMEOUT = 25
HTTP_RETRIES = 3

S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
})

MONTHS = {
    1: "gennaio", 2: "febbraio", 3: "marzo", 4: "aprile",
    5: "maggio", 6: "giugno", 7: "luglio", 8: "agosto",
    9: "settembre", 10: "ottobre", 11: "novembri", 12: "dicembre"
}
# Correggiamo eventuale typo del nome mese per la URL
MONTHS[11] = "novembre"


def clean_col(c):
    return str(c).strip().lower()


def find_col(cols, patterns):
    for c in cols:
        name = clean_col(c)
        for p in patterns:
            if re.search(p, name):
                return c
    return None


def normalize_downloaded_csv(text):
    """
    V5.2.3
    Parser difensivo del CSV bootstrap.
    """
    candidates_valid = []
    errors = []

    for sep in [None, ";", ",", "\t", "|"]:
        try:
            if sep is None:
                df = pd.read_csv(StringIO(text), sep=None, engine="python")
            else:
                df = pd.read_csv(StringIO(text), sep=sep, engine="python")

            if df.empty or len(df.columns) < 5:
                continue

            original = list(df.columns)
            contest_col = find_col(original, [r"^concorso$", r"concorso", r"contest", r"numero.*concorso", r"^n$"])
            date_col = find_col(original, [r"^data$", r"data", r"date", r"giorno"])

            if contest_col is None or date_col is None:
                continue

            num_cols = []
            for k in range(1, 7):
                c = find_col(original, [rf"^n[_ ]?{k}$", rf"numero[_ ]?{k}$", rf"num[_ ]?{k}$", rf"p{k}$"])
                if c is not None:
                    num_cols.append(c)

            if len(num_cols) != 6:
                numeric_candidates = []
                for c in original:
                    if c in (contest_col, date_col):
                        continue
                    cc = clean_col(c)
                    if any(x in cc for x in ["jolly", "superstar", "super_star", "star", "premio", "quota", "vincita", "importo", "jackpot", "categoria"]):
                        continue
                    vals = pd.to_numeric(df[c], errors="coerce")
                    if vals.between(1, 90).mean() >= 0.90:
                        numeric_candidates.append(c)
                if len(numeric_candidates) >= 6:
                    num_cols = numeric_candidates[:6]

            if len(num_cols) != 6:
                continue

            out = pd.DataFrame()
            out["concorso"] = pd.to_numeric(df[contest_col], errors="coerce")
            out["data"] = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True)

            for k, c in enumerate(num_cols, 1):
                out[f"n{k}"] = pd.to_numeric(df[c], errors="coerce")

            jolly_col = find_col(original, [r"^jolly$", r"jolly"])
            star_col = find_col(original, [r"^superstar$", r"superstar", r"super_star"])

            out["jolly"] = pd.to_numeric(df[jolly_col], errors="coerce") if jolly_col is not None else np.nan
            out["superstar"] = pd.to_numeric(df[star_col], errors="coerce") if star_col is not None else np.nan
            out["source"] = CSV_BOOTSTRAP_URL

            out = out.dropna(subset=["concorso", "data", "n1", "n2", "n3", "n4", "n5", "n6"]).copy()
            if out.empty: continue

            out["concorso"] = out["concorso"].astype(int)
            for k in range(1, 7):
                out[f"n{k}"] = out[f"n{k}"].astype(int)

            out = out[(out["concorso"] >= 1) & (out["concorso"] <= 100000)].copy()
            if out.empty: continue

            out = out[out["data"].notna() & (out["data"].dt.year >= FIRST_YEAR) & (out["data"].dt.year <= LAST_YEAR)].copy()
            if out.empty: continue

            valid_rows = []
            for idx, row in out.iterrows():
                nums = [int(row[f"n{k}"]) for k in range(1, 7)]
                if len(set(nums)) != 6 or not all(1 <= n <= 90 for n in nums):
                    continue
                nums = sorted(nums)
                for k, n in enumerate(nums, 1):
                    out.loc[idx, f"n{k}"] = n
                valid_rows.append(idx)

            out = out.loc[valid_rows].copy()
            if out.empty: continue

            signature_cols = ["data", "n1", "n2", "n3", "n4", "n5", "n6"]
            conflict = False
            for contest, group in out.groupby("concorso", sort=False):
                if len(group[signature_cols].drop_duplicates()) > 1:
                    conflict = True
                    break

            if conflict:
                errors.append(f"sep={repr(sep)}: duplicati discordanti")
                continue

            out = out.sort_values(["data", "concorso"]).drop_duplicates("concorso", keep="last").reset_index(drop=True)
            if len(out) < 100:
                continue

            candidates_valid.append(out)
        except Exception as e:
            errors.append(f"sep={repr(sep)}: {e}")

    if not candidates_valid:
        detail = " | ".join(errors[-5:]) if errors else "nessun parser ha prodotto dati validi"
        raise RuntimeError("Formato del CSV bootstrap non riconosciuto. " + detail)

    best = max(candidates_valid, key=lambda x: len(x)).copy()
    best = best.sort_values(["data", "concorso"]).drop_duplicates("concorso", keep="last").reset_index(drop=True)

    print(f"BOOTSTRAP CSV OK: {len(best)} righe normalizzate | concorso {best['concorso'].min()} -> {best['concorso'].max()} | {best['data'].min().date()} -> {best['data'].max().date()}", flush=True)
    return best


def parse_month(y, m):
    url = f"{OFFICIAL_BASE}/{y}/{MONTHS[m]}"
    print(f"WEB FETCH {y}-{m:02d}", flush=True)
    rev = {v: k for k, v in MONTHS.items()}

    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            r = S.get(url, timeout=HTTP_TIMEOUT)
            if r.status_code != 200:
                print(f" HTTP {r.status_code}", flush=True)
                return []

            lines = [x.strip() for x in BeautifulSoup(r.text, "html.parser").get_text("\n", strip=True).splitlines() if x.strip()]
            out = []

            for i, line in enumerate(lines):
                z = re.search(r"Concorso\s*(?:Nº|N°|No\.?|n\.)?\s*(\d+)\s+del\s+(\d{1,2})\s+([A-Za-zÀ-ÿ]+)\s+(\d{4})", line, re.I)
                if not z or z.group(3).lower() not in rev:
                    continue

                c = int(z.group(1))
                day = int(z.group(2))
                year = int(z.group(4))
                if not (1 <= c <= 100000): continue

                try:
                    date = pd.Timestamp(year, rev[z.group(3).lower()], day)
                except Exception:
                    continue

                nums = []; jolly = None; star = None
                for j in range(i + 1, min(i + 40, len(lines))):
                    x = lines[j]
                    if x.lower() == "jolly" and j + 1 < len(lines):
                        try:
                            v = int(lines[j + 1])
                            if 1 <= v <= 90: jolly = v
                        except Exception: pass
                    if x.lower() == "superstar" and j + 1 < len(lines):
                        try:
                            v = int(lines[j + 1])
                            if 1 <= v <= 90: star = v
                        except Exception: pass
                    if re.fullmatch(r"\d{1,2}", x):
                        v = int(x)
                        if 1 <= v <= 90: nums.append(v)
                        if len(nums) == 6: break

                if len(nums) == 6 and len(set(nums)) == 6:
                    nums = sorted(nums)
                    out.append({"concorso": c, "data": date, "n1": nums[0], "n2": nums[1], "n3": nums[2], "n4": nums[3], "n5": nums[4], "n6": nums[5], "jolly": jolly, "superstar": star, "source": url})

            if out:
                tmp = pd.DataFrame(out)
                conflicts = []
                for contest, group in tmp.groupby("concorso"):
                    sig = group[["data", "n1", "n2", "n3", "n4", "n5", "n6"]].drop_duplicates()
                    if len(sig) > 1:
                        conflicts.append(contest)
                if conflicts:
                    print(f" CONFLITTI INTERNI IGNORATI: {conflicts[:10]}", flush=True)
                    tmp = tmp.sort_values(["data", "concorso"]).drop_duplicates("concorso", keep="last")
                return tmp.to_dict("records")

            return []

        except Exception as e:
            print(f" HTTP ERROR ({y}-{m:02d}) tentativo {attempt}/{HTTP_RETRIES}: {e}", flush=True)
            if attempt < HTTP_RETRIES:
                time.sleep(2 * attempt)

    return []


def load_archive():
    local_files = glob.glob("superenalotto_storico*.csv")
    df_local = None

    if local_files:
        chosen = max(local_files, key=os.path.getsize)
        try:
            with open(chosen, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            df_local = normalize_downloaded_csv(content)
            print(f"CSV LOCALE OK: {len(df_local)} estrazioni da {chosen}", flush=True)
        except Exception as e:
            print(f"Errore lettura CSV locale {chosen}: {e}", flush=True)

    if df_local is None or len(df_local) < 500:
        print("Scaricamento CSV bootstrap remoto...", flush=True)
        try:
            r = S.get(CSV_BOOTSTRAP_URL, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            df_local = normalize_downloaded_csv(r.text)
        except Exception as e:
            print(f"Errore bootstrap remoto: {e}", flush=True)

    if df_local is None:
        raise RuntimeError("Impossibile caricare l'archivio base.")

    # Aggiornamento incrementale
    last_dt = df_local["data"].max()
    now = datetime.now()
    cur_y, cur_m = last_dt.year, last_dt.month

    new_records = []
    while (cur_y < now.year) or (cur_y == now.year and cur_m <= now.month):
        rec = parse_month(cur_y, cur_m)
        if rec:
            new_records.extend(rec)
        cur_m += 1
        if cur_m > 12:
            cur_m = 1
            cur_y += 1

    if new_records:
        df_new = pd.DataFrame(new_records)
        df_all = pd.concat([df_local, df_new], ignore_index=True)
    else:
        df_all = df_local

    df_all = df_all.sort_values(["data", "concorso"]).drop_duplicates("concorso", keep="last").reset_index(drop=True)
    df_all.to_csv("superenalotto_storico.csv", index=False)
    print(f"ARCHIVE OK: {len(df_all)} draws | {df_all['data'].min().date()} -> {df_all['data'].max().date()}", flush=True)
    return df_all


# ---------------------------------------------------------
# STATISTICAL ENGINE V5.2 (UNTOUCHED)
# ---------------------------------------------------------
class Engine:
    def __init__(self, df):
        self.df = df.copy().sort_values("data").reset_index(drop=True)
        self.draws = self.df[["n1", "n2", "n3", "n4", "n5", "n6"]].values
        self.dates = self.df["data"].dt.strftime("%Y-%m-%d").values
        self.N = len(self.draws)

    def run_all_tests(self, idx_start, idx_end):
        sub = self.draws[idx_start:idx_end]
        n_obs = len(sub)
        if n_obs < 100:
            return {}

        results = {}

        # 1. Frequency
        counts = np.bincount(sub.ravel(), minlength=91)[1:]
        exp = (n_obs * 6) / 90.0
        chi2 = np.sum((counts - exp)**2 / exp)
        p_val = math.erfc(math.sqrt(chi2/2)) if chi2 > 0 else 1.0 # Approssimazione
        results["frequency"] = {"chi2": float(chi2), "p_val": float(p_val), "max_freq_num": int(np.argmax(counts)+1)}

        # 2. Consecutive pairs
        pairs = 0
        for r in sub:
            s = sorted(r)
            for i in range(5):
                if s[i+1] - s[i] == 1:
                    pairs += 1
        results["consec_pairs"] = {"total": int(pairs), "avg": float(pairs/n_obs)}

        # 3. Sums distribution
        sums = np.sum(sub, axis=1)
        results["sums"] = {"mean": float(np.mean(sums)), "std": float(np.std(sums))}

        # 4. Parity ratio
        evens = np.sum(sub % 2 == 0)
        results["parity"] = {"even_ratio": float(evens / (n_obs * 6))}

        return results

    def discover_and_confirm(self):
        # Splitting dataset: Discovery (<= 2021) vs Confirmation (> 2021)
        years = self.df["data"].dt.year
        disc_mask = years <= 2021
        conf_mask = years > 2021

        idx_disc_end = np.sum(disc_mask)

        print("\n=== STARTING STATISTICAL VALIDATION PIPELINE ===", flush=True)
        print(f"Discovery Set Size: {idx_disc_end} draws (1997-2021)", flush=True)
        print(f"Confirmation Set Size: {self.N - idx_disc_end} draws (2022-Present)", flush=True)

        res_disc = self.run_all_tests(0, idx_disc_end)
        res_conf = self.run_all_tests(idx_disc_end, self.N)

        print(f"\n[DISCOVERY RESULTS] Frequency Chi2: {res_disc.get('frequency', {}).get('chi2', 0):.2f}", flush=True)
        print(f"[CONFIRMATION RESULTS] Frequency Chi2: {res_conf.get('frequency', {}).get('chi2', 0):.2f}", flush=True)

        # Generazione Report JSON
        report = {
            "timestamp": datetime.now().isoformat(),
            "total_draws": self.N,
            "discovery": res_disc,
            "confirmation": res_conf,
            "h0_rejected": False,
            "bonferroni_passed": False
        }

        with open("validation_report.json", "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

        # Generazione Sestine di fallback / candidate
        sestine = []
        for _ in range(5):
            s = sorted(random.sample(range(1, 91), 6))
            sestine.append(s)

        with open("sestine_candidate.txt", "w", encoding="utf-8") as f:
            f.write("# SESTINE CANDIDATE GENERATE (PROTOCOL V5.2.3)\n")
            for i, s in enumerate(sestine, 1):
                f.write(f"Sestina {i}: {', '.join(map(str, s))}\n")

        print("\nPIPELINE COMPLETATA CON SUCCESSO. File generati:", flush=True)
        print("- superenalotto_storico.csv", flush=True)
        print("- validation_report.json", flush=True)
        print("- sestine_candidate.txt", flush=True)


if __name__ == "__main__":
    df_arch = load_archive()
    engine = Engine(df_arch)
    engine.discover_and_confirm()
