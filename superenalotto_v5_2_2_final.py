# SUPERNALOTTO ENGINE V5.2.2 FINAL
# Bootstrap storico da CSV diretto + aggiornamento incrementale.
# Discovery 2016-2021 -> Bonferroni -> optional Confirmation 2022-2026
# -> candidate tickets.
#
# IMPORTANTE:
# - Il test statistico non usa Jolly/SuperStar.
# - Il CSV storico è trattato per concorso, non per sola data.
# - Se esiste superenalotto_storico.csv, NON viene riscaricato lo storico.
# - Se manca, viene tentato un download CSV diretto da una fonte archivistica
#   con archivio SuperEnalotto dal 1997 al 2026; solo in fallback si usa
#   l'archivio mensile ufficiale.
# - Un timeout web non distrugge uno storico locale valido.
# - Il protocollo statistico resta invariato.

import os
import re
import csv
import time
import requests
import numpy as np
import pandas as pd
from io import StringIO
from bs4 import BeautifulSoup

# =========================
# CONFIG
# =========================

OFFICIAL_BASE = "https://www.superenalotto.it/archivio-estrazioni"
CSV_BOOTSTRAP_URL = (
    "https://www.estrazioni.it/index.php"
    "?formato=csv&p=download&tipo=superenalotto"
)

FIRST_YEAR, LAST_YEAR = 2009, 2026

DISCOVERY_START, DISCOVERY_END = "2016-01-01", "2021-12-31"
CONFIRMATION_START, CONFIRMATION_END = "2022-01-01", "2026-12-31"

N_NULL = 2000
MIN_HISTORY = 100
SEED = 123456
ALPHA = 0.05

STRATEGIES = ["FREQUENCY", "DELAY", "MIXED"]
N_TICKETS = 10

OUTPUT = "superenalotto_storico.csv"
QUALITY = "superenalotto_data_quality.csv"
DISC = "v5_results_discovery.csv"
FULL = "v5_results_full_protocol.csv"
OOS = "v5_oos_draws.csv"
TICKETS = "v5_candidate_tickets.csv"

HTTP_TIMEOUT = 12
MONTHS = {
    1: "gennaio", 2: "febbraio", 3: "marzo", 4: "aprile",
    5: "maggio", 6: "giugno", 7: "luglio", 8: "agosto",
    9: "settembre", 10: "ottobre", 11: "novembre", 12: "dicembre"
}

S = requests.Session()
S.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/122 Safari/537.36"
    ),
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/csv,text/plain,text/html,application/xhtml+xml,*/*;q=0.8"
})


# =========================
# DATA HELPERS
# =========================

REQ = ["concorso", "data", "n1", "n2", "n3", "n4", "n5", "n6"]


def clean_col(x):
    x = str(x).strip().lower()
    x = (
        x.replace("à", "a").replace("è", "e").replace("é", "e")
         .replace("ì", "i").replace("ò", "o").replace("ù", "u")
    )
    x = re.sub(r"[^a-z0-9]+", "_", x).strip("_")
    return x


def find_col(columns, patterns):
    for c in columns:
        cc = clean_col(c)
        for p in patterns:
            if re.search(p, cc):
                return c
    return None


def normalize_downloaded_csv(text):
    """
    Tenta di normalizzare il CSV archivistico esterno in:
    concorso,data,n1..n6,jolly,superstar,source
    """
    last_error = None

    for sep in [None, ";", ",", "\t", "|"]:
        try:
            if sep is None:
                df = pd.read_csv(StringIO(text), sep=None, engine="python")
            else:
                df = pd.read_csv(StringIO(text), sep=sep, engine="python")

            if df.empty or len(df.columns) < 5:
                continue

            original = list(df.columns)
            cols = [clean_col(c) for c in original]

            contest_col = find_col(
                original,
                [r"concorso", r"contest", r"numero.*concorso", r"^n$"]
            )
            date_col = find_col(
                original,
                [r"data", r"date", r"giorno"]
            )

            if contest_col is None or date_col is None:
                continue

            # Mapping esplicito n1..n6, se disponibile.
            num_cols = []
            for k in range(1, 7):
                c = find_col(
                    original,
                    [
                        rf"^n[_ ]?{k}$",
                        rf"numero[_ ]?{k}",
                        rf"num[_ ]?{k}",
                        rf"^p{k}$"
                    ]
                )
                if c is not None:
                    num_cols.append(c)

            # Fallback: cerca colonne che sembrano numeri estratti.
            if len(num_cols) != 6:
                candidates = []
                for c in original:
                    cc = clean_col(c)
                    if c in (contest_col, date_col):
                        continue
                    if any(x in cc for x in [
                        "jolly", "superstar", "star", "premio",
                        "quota", "vincita", "importo"
                    ]):
                        continue
                    vals = pd.to_numeric(df[c], errors="coerce")
                    valid = vals.between(1, 90).mean()
                    if valid > 0.90:
                        candidates.append(c)
                if len(candidates) >= 6:
                    num_cols = candidates[:6]

            if len(num_cols) != 6:
                continue

            out = pd.DataFrame()
            out["concorso"] = pd.to_numeric(
                df[contest_col], errors="coerce"
            )
            out["data"] = pd.to_datetime(
                df[date_col], errors="coerce", dayfirst=True
            )

            for k, c in enumerate(num_cols, 1):
                out[f"n{k}"] = pd.to_numeric(df[c], errors="coerce")

            jolly_col = find_col(original, [r"jolly"])
            star_col = find_col(original, [r"superstar", r"super_star"])
            out["jolly"] = (
                pd.to_numeric(df[jolly_col], errors="coerce")
                if jolly_col else np.nan
            )
            out["superstar"] = (
                pd.to_numeric(df[star_col], errors="coerce")
                if star_col else np.nan
            )
            out["source"] = CSV_BOOTSTRAP_URL

            out = out.dropna(subset=REQ)
            out["concorso"] = out["concorso"].astype(int)
            for k in range(1, 7):
                out[f"n{k}"] = out[f"n{k}"].astype(int)

            out = out[
                (out["data"].dt.year >= FIRST_YEAR)
                & (out["data"].dt.year <= LAST_YEAR)
            ]

            if len(out) >= 500:
                print(
                    f"BOOTSTRAP CSV OK: {len(out)} righe normalizzate.",
                    flush=True
                )
                return out

        except Exception as e:
            last_error = e

    raise RuntimeError(
        "Formato del CSV bootstrap non riconosciuto."
        + (f" Dettaglio: {last_error}" if last_error else "")
    )


def parse_month(y, m):
    url = f"{OFFICIAL_BASE}/{y}/{MONTHS[m]}"
    print(f"WEB FETCH {y}-{m:02d}", flush=True)

    try:
        r = S.get(url, timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            print(f" HTTP {r.status_code}", flush=True)
            return []

        lines = [
            x.strip()
            for x in BeautifulSoup(
                r.text, "html.parser"
            ).get_text("\n", strip=True).splitlines()
            if x.strip()
        ]
    except Exception as e:
        print(
            f" HTTP TIMEOUT/ERROR ({y}-{m:02d}): {e}",
            flush=True
        )
        return []

    rev = {v: k for k, v in MONTHS.items()}
    out = []

    for i, line in enumerate(lines):
        z = re.search(
            r"Concorso\s*(?:Nº|N°|No\.?|n\.)?\s*(\d+)"
            r"\s+del\s+(\d{1,2})\s+([A-Za-zÀ-ÿ]+)\s+(\d{4})",
            line,
            re.I
        )

        if not z or z.group(3).lower() not in rev:
            continue

        c = int(z.group(1))
        d = int(z.group(2))
        mt = z.group(3).lower()
        yy = int(z.group(4))

        try:
            date = pd.Timestamp(yy, rev[mt], d)
        except Exception:
            continue

        nums = []
        jolly = star = None

        for j in range(i + 1, min(i + 40, len(lines))):
            x = lines[j]

            if x.lower() == "jolly" and j + 1 < len(lines):
                try:
                    v = int(lines[j + 1])
                    jolly = v if 1 <= v <= 90 else None
                except Exception:
                    pass

            if x.lower() == "superstar" and j + 1 < len(lines):
                try:
                    v = int(lines[j + 1])
                    star = v if 1 <= v <= 90 else None
                except Exception:
                    pass

            if re.fullmatch(r"\d{1,2}", x):
                v = int(x)
                if 1 <= v <= 90:
                    nums.append(v)
                if len(nums) == 6:
                    break

        if len(nums) == 6 and len(set(nums)) == 6:
            nums = sorted(nums)
            out.append({
                "concorso": c,
                "data": date,
                "n1": nums[0], "n2": nums[1], "n3": nums[2],
                "n4": nums[3], "n5": nums[4], "n6": nums[5],
                "jolly": jolly,
                "superstar": star,
                "source": url
            })

    return out


def read_local():
    if not os.path.exists(OUTPUT):
        return pd.DataFrame()

    try:
        df = pd.read_csv(OUTPUT)
        if not all(c in df.columns for c in REQ):
            print("CSV LOCALE PRESENTE MA INCOMPLETO.", flush=True)
            return pd.DataFrame()

        df["data"] = pd.to_datetime(df["data"], errors="coerce")
        df["concorso"] = pd.to_numeric(
            df["concorso"], errors="coerce"
        )
        df = df.dropna(subset=["concorso", "data"]).copy()
        df["concorso"] = df["concorso"].astype(int)

        print(
            f"CSV LOCALE: {len(df)} estrazioni | "
            f"concorso max {df['concorso'].max()} | "
            f"{df['data'].min().date()} -> {df['data'].max().date()}",
            flush=True
        )
        return df

    except Exception as e:
        print(f"ERRORE LETTURA CSV LOCALE: {e}", flush=True)
        return pd.DataFrame()


# =========================
# ARCHIVE
# =========================

def archive():
    existing = read_local()

    # ---------------------------------------------------------
    # A) Se non esiste lo storico: bootstrap CSV diretto.
    #    Evita centinaia di richieste mensili.
    # ---------------------------------------------------------
    if existing.empty:
        print(
            "Nessun CSV storico locale. "
            "Tento bootstrap CSV diretto...",
            flush=True
        )

        try:
            r = S.get(CSV_BOOTSTRAP_URL, timeout=30)
            r.raise_for_status()

            boot = normalize_downloaded_csv(r.text)
            existing = boot.copy()

            print(
                f"BOOTSTRAP COMPLETATO: {len(existing)} estrazioni.",
                flush=True
            )

        except Exception as e:
            print(
                f"BOOTSTRAP CSV FALLITO: {e}",
                flush=True
            )
            print(
                "Fallback: archivio mensile ufficiale 2009-2026.",
                flush=True
            )

            rows = []
            for y in range(FIRST_YEAR, LAST_YEAR + 1):
                for m in range(1, 13):
                    rows.extend(parse_month(y, m))

            if not rows:
                raise RuntimeError(
                    "Nessun archivio disponibile: "
                    "bootstrap CSV e fallback web falliti."
                )

            existing = pd.DataFrame(rows)

    # ---------------------------------------------------------
    # B) Aggiornamento incrementale.
    #    Non riscarichiamo lo storico: controlliamo soltanto
    #    gli ultimi 2 mesi/anno corrente.
    # ---------------------------------------------------------
    existing["data"] = pd.to_datetime(existing["data"], errors="coerce")
    known = set(
        pd.to_numeric(existing["concorso"], errors="coerce")
        .dropna().astype(int)
    )

    now = pd.Timestamp.now()
    rows = []

    # Solo anno corrente e precedente; serve a intercettare
    # eventuali correzioni senza riscaricare tutto il passato.
    for y in range(max(FIRST_YEAR, now.year - 1), now.year + 1):
        maxm = now.month if y == now.year else 12

        for m in range(1, maxm + 1):
            r = parse_month(y, m)

            for row in r:
                if row["concorso"] not in known:
                    rows.append(row)

    if rows:
        print(
            f"NUOVI CONCORSI WEB: {len(rows)}",
            flush=True
        )
        df = pd.concat(
            [existing, pd.DataFrame(rows)],
            ignore_index=True
        )
    else:
        print(
            "Nessun nuovo concorso recuperato; "
            "mantengo lo storico locale/bootstrap.",
            flush=True
        )
        df = existing

    # ---------------------------------------------------------
    # C) Pulizia finale.
    # ---------------------------------------------------------
    df["data"] = pd.to_datetime(df["data"], errors="coerce")
    df["concorso"] = pd.to_numeric(
        df["concorso"], errors="coerce"
    )

    df = df.dropna(subset=["concorso", "data"]).copy()
    df["concorso"] = df["concorso"].astype(int)

    for k in range(1, 7):
        df[f"n{k}"] = pd.to_numeric(
            df[f"n{k}"], errors="coerce"
        )

    df = df.dropna(subset=[f"n{k}" for k in range(1, 7)])

    for k in range(1, 7):
        df[f"n{k}"] = df[f"n{k}"].astype(int)

    # Controllo concorsi discordanti.
    for c, g in df.groupby("concorso"):
        unique = g[
            ["data", "n1", "n2", "n3", "n4", "n5", "n6"]
        ].drop_duplicates()

        if len(unique) > 1:
            raise RuntimeError(
                f"Concorso duplicato con dati discordanti: {c}"
            )

    df = (
        df.sort_values(["data", "concorso"])
          .drop_duplicates("concorso", keep="last")
          .reset_index(drop=True)
    )

    return df


def validate(df):
    if any(c not in df.columns for c in REQ):
        raise ValueError("Colonne mancanti")

    df = df.copy()
    df["data"] = pd.to_datetime(df["data"], errors="coerce")

    if df["data"].isna().any():
        raise ValueError("Date non valide")

    bad = []

    for i, row in df.iterrows():
        ns = [int(row[f"n{k}"]) for k in range(1, 7)]

        if len(set(ns)) != 6 or not all(1 <= n <= 90 for n in ns):
            bad.append(i)

    if bad:
        df = df.drop(index=bad)

    df = (
        df.drop_duplicates("concorso")
          .sort_values(["data", "concorso"])
          .reset_index(drop=True)
    )

    ix = np.where(
        df["data"].to_numpy() >= pd.Timestamp(DISCOVERY_START)
    )[0]

    if len(ix) == 0 or ix[0] < MIN_HISTORY:
        raise ValueError("Storico pre-Discovery insufficiente")

    quality = pd.DataFrame([
        {"check": "rows_final", "value": len(df)},
        {"check": "invalid_removed", "value": len(bad)},
        {"check": "first_date", "value": str(df.data.min().date())},
        {"check": "last_date", "value": str(df.data.max().date())},
        {"check": "max_contest", "value": int(df.concorso.max())},
    ])

    quality.to_csv(QUALITY, index=False)
    df.to_csv(OUTPUT, index=False)

    print(
        f"ARCHIVE OK: {len(df)} draws | "
        f"{df.data.min().date()} -> {df.data.max().date()} | "
        f"ultimo concorso {df.concorso.max()}",
        flush=True
    )

    return df


# =========================
# ENGINE
# =========================

class Engine:

    def __init__(self, df):
        df = (
            df.sort_values(["data", "concorso"])
              .reset_index(drop=True)
        )

        self.df = df
        self.draws = df[
            [f"n{i}" for i in range(1, 7)]
        ].to_numpy(np.int16)

        self.dates = df.data.to_numpy()
        self.contests = df.concorso.to_numpy()

    @staticmethod
    def state(hist):
        f = np.zeros(91, np.int32)
        d = np.zeros(91, np.int32)

        for a in hist:
            d[1:] += 1

            for n in a:
                f[n] += 1
                d[n] = 0

        return f, d

    @staticmethod
    def pick(st, f, d, r):
        nums = np.arange(1, 91)

        if st == "FREQUENCY":
            w = f[1:].astype(float) + 1e-12
            return np.sort(
                r.choice(nums, 6, replace=False, p=w / w.sum())
            )

        if st == "DELAY":
            w = d[1:].astype(float) + 1e-12
            return np.sort(
                r.choice(nums, 6, replace=False, p=w / w.sum())
            )

        if st == "MIXED":
            ff = f[1:].astype(float)
            dd = d[1:].astype(float)

            if ff.max():
                ff /= ff.max()

            if dd.max():
                dd /= dd.max()

            score = 0.5 * ff + 0.5 * dd
            top = np.argsort(score)[-30:]
            candidates = nums[top]
            w = score[top] + 1e-12

            return np.sort(
                r.choice(
                    candidates,
                    6,
                    replace=False,
                    p=w / w.sum()
                )
            )

        raise ValueError(st)

    @staticmethod
    def update(a, f, d):
        d[1:] += 1

        for n in a:
            f[n] += 1
            d[n] = 0

    def oos(self, st, start, end, seed):
        ix = np.where(
            (self.dates >= pd.Timestamp(start))
            & (self.dates <= pd.Timestamp(end))
        )[0]

        if len(ix) == 0:
            raise ValueError(f"Nessun dato OOS per {start} -> {end}")

        first = ix[0]

        if first < MIN_HISTORY:
            raise ValueError("Storico insufficiente")

        r = np.random.default_rng(seed)
        f, d = self.state(self.draws[:first])

        total = 0
        rows = []

        for i in ix:
            ticket = self.pick(st, f, d, r)
            actual = self.draws[i]

            hits = len(set(ticket) & set(actual))
            total += hits

            rows.append({
                "phase": f"{start}_{end}",
                "strategy": st,
                "concorso": int(self.contests[i]),
                "data": str(pd.Timestamp(self.dates[i]).date()),
                "ticket": " ".join(f"{x:02d}" for x in ticket),
                "actual": " ".join(f"{x:02d}" for x in actual),
                "hits": hits,
                "seed": seed
            })

            self.update(actual, f, d)

        return total, len(ix), rows

    def null(self, st, start, end, n, seed):
        """
        H0:
        - la storia pre-OOS resta quella reale;
        - i futuri concorsi sono simulati uniformemente 6/90;
        - la strategia viene rieseguita sequenzialmente;
        - ticket RNG e draw RNG sono separati.
        """
        ix = np.where(
            (self.dates >= pd.Timestamp(start))
            & (self.dates <= pd.Timestamp(end))
        )[0]

        if len(ix) == 0:
            raise ValueError(f"Nessun dato OOS per {start} -> {end}")

        first = ix[0]
        hist = self.draws[:first]
        L = len(ix)

        # Ottimizzazione: lo stato iniziale è costruito una sola volta.
        base_f, base_d = self.state(hist)

        master = np.random.default_rng(seed)
        out = np.empty(n, np.int32)
        nums = np.arange(1, 91)

        t0 = time.time()

        for s in range(n):
            # Copia economica dello stato iniziale.
            f = base_f.copy()
            d = base_d.copy()

            ticket_rng = np.random.default_rng(
                int(master.integers(0, 2**63 - 1))
            )
            draw_rng = np.random.default_rng(
                int(master.integers(0, 2**63 - 1))
            )

            total = 0

            for _ in range(L):
                ticket = self.pick(st, f, d, ticket_rng)

                actual = np.sort(
                    draw_rng.choice(
                        nums,
                        6,
                        replace=False
                    )
                )

                total += len(set(ticket) & set(actual))
                self.update(actual, f, d)

            out[s] = total

            if (s + 1) % 100 == 0:
                elapsed = (time.time() - t0) / 60
                print(
                    f"   {st}: {s+1}/{n} | "
                    f"elapsed {elapsed:.1f} min",
                    flush=True
                )

        return out

    def phase(self, name, start, end, strats, n, seed):
        alpha = 0.05 / len(strats)
        res = []
        logs = []

        for sid, st in enumerate(strats):
            ss = seed + sid * 100000

            obs, L, rows = self.oos(
                st, start, end, ss
            )

            logs += rows

            nul = self.null(
                st,
                start,
                end,
                n,
                ss + 999999
            )

            p = (
                np.sum(nul >= obs) + 1
            ) / (n + 1)

            passed = p < alpha

            print(
                f"[{name}] {st}: "
                f"observed={obs}, "
                f"H0 mean={nul.mean():.2f}, "
                f"p={p:.6f}, "
                f"Bonf={alpha:.6f}, "
                f"PASS={passed}",
                flush=True
            )

            res.append({
                "phase": name,
                "strategy": st,
                "draws": L,
                "observed_hits": obs,
                "observed_mean": obs / L,
                "null_mean": nul.mean(),
                "null_std": nul.std(ddof=1),
                "p_value": p,
                "alpha_bonferroni": alpha,
                "passed": passed
            })

        return pd.DataFrame(res), logs

    def tickets(self, st, n=10, seed=999):
        r = np.random.default_rng(seed)

        f, d = self.state(self.draws)

        seen = set()
        rows = []

        while len(rows) < n:
            ticket = tuple(
                self.pick(st, f, d, r)
            )

            if ticket in seen:
                continue

            seen.add(ticket)

            rows.append({
                "rank": len(rows) + 1,
                "strategy": st,
                "ticket": " ".join(
                    f"{x:02d}" for x in ticket
                ),
                "status": "CANDIDATA - NON GARANTITA"
            })

        return pd.DataFrame(rows)

    def run(self):
        print("\n=== DISCOVERY OOS 2016-2021 ===", flush=True)

        disc, logs1 = self.phase(
            "DISCOVERY OOS",
            DISCOVERY_START,
            DISCOVERY_END,
            STRATEGIES,
            N_NULL,
            SEED
        )

        disc.to_csv(DISC, index=False)

        passed = disc.loc[
            disc.passed, "strategy"
        ].tolist()

        logs = logs1

        if passed:
            print(
                f"\nSTRATEGIE SOPRAVVISSUTE ALLA DISCOVERY: {passed}",
                flush=True
            )

            print(
                "\n=== CONFIRMATION OOS 2022-2026 ===",
                flush=True
            )

            conf, logs2 = self.phase(
                "CONFIRMATION OOS",
                CONFIRMATION_START,
                CONFIRMATION_END,
                passed,
                N_NULL,
                SEED + 500000
            )

            logs += logs2

            pd.concat(
                [disc, conf],
                ignore_index=True
            ).to_csv(FULL, index=False)

            confirmed = conf.loc[
                conf.passed, "strategy"
            ].tolist()

            st = (
                confirmed[0]
                if confirmed
                else passed[0]
            )

            if confirmed:
                print(
                    "\nESITO: SEGNALE STATISTICO "
                    "CONFERMATO - DA APPROFONDIRE",
                    flush=True
                )
            else:
                print(
                    "\nESITO: SEGNALE NON REPLICATO - "
                    "NESSUNA EVIDENZA ROBUSTA",
                    flush=True
                )

        else:
            disc.to_csv(FULL, index=False)
            st = "MIXED"

            print(
                "\nESITO: H0 NON VIENE RIGETTATA - "
                "NESSUNA EVIDENZA STATISTICA "
                "OLTRE IL CASO",
                flush=True
            )

        pd.DataFrame(logs).to_csv(
            OOS,
            index=False
        )

        tickets = self.tickets(
            st,
            N_TICKETS,
            SEED + 900000
        )

        tickets.to_csv(
            TICKETS,
            index=False
        )

        print(
            "\nSESTINE CANDIDATE (NON GARANTITE):",
            flush=True
        )

        print(
            "\n".join(
                f"{int(row['rank']):02d}) {row['ticket']}"
                for _, row in tickets.iterrows()
            ),
            flush=True
        )

        print(
            "\nFILE GENERATI:",
            OUTPUT,
            QUALITY,
            DISC,
            FULL,
            OOS,
            TICKETS,
            flush=True
        )


# =========================
# MAIN
# =========================

if __name__ == "__main__":
    try:
        archive_df = archive()
        clean_df = validate(archive_df)
        Engine(clean_df).run()

    except Exception as e:
        print(
            f"\n[!] ERRORE PIPELINE: {e}",
            flush=True
        )
        raise
