# ============================================================
# SUPERNALOTTO ENGINE V5 FINAL
# ============================================================
#
# 1. Scarica l'archivio ufficiale SuperEnalotto
# 2. Costruisce il dataset storico
# 3. Valida i dati
# 4. Salva CSV + report qualità
# 5. Esegue DISCOVERY OOS 2016-2021
# 6. Esegue CONFIRMATION OOS 2022-2026
#    SOLO se Discovery supera Bonferroni
#
# Fonte:
# https://www.superenalotto.it/archivio-estrazioni
#
# ============================================================

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

BASE_URL = (
    "https://www.superenalotto.it/"
    "archivio-estrazioni"
)

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

OUTPUT_CSV = (
    "superenalotto_storico.csv"
)

QUALITY_REPORT = (
    "superenalotto_data_quality.csv"
)


# ============================================================
# MESI
# ============================================================

MONTHS = {
    1: "gennaio",
    2: "febbraio",
    3: "marzo",
    4: "aprile",
    5: "maggio",
    6: "giugno",
    7: "luglio",
    8: "agosto",
    9: "settembre",
    10: "ottobre",
    11: "novembre",
    12: "dicembre"
}


# ============================================================
# SESSIONE HTTP
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent":
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        Chrome/140.0 Safari/537.36"
})


# ============================================================
# PARSING DI UNA PAGINA MENSILE
# ============================================================

def parse_month_page(
    year,
    month
):

    month_name = MONTHS[month]

    url = (
        f"{BASE_URL}/"
        f"{year}/"
        f"{month_name}"
    )

    print(
        f"   {year}-{month:02d} -> {url}"
    )

    try:

        response = SESSION.get(
            url,
            timeout=30
        )

        if response.status_code != 200:

            print(
                f"      HTTP {response.status_code}"
            )

            return []

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

    except Exception as e:

        print(
            f"      ERRORE HTTP: {e}"
        )

        return []

    records = []

    # --------------------------------------------------------
    # Il sito presenta righe del tipo:
    #
    # Concorso Nº 146 del 11 Settembre 2026
    # 8 13 16 52 64 70
    # Jolly 17
    # SuperStar 21
    #
    # Cerchiamo il testo della pagina e lavoriamo sui blocchi.
    # --------------------------------------------------------

    text = soup.get_text(
        "\n",
        strip=True
    )

    lines = [
        x.strip()
        for x in text.splitlines()
        if x.strip()
    ]

    i = 0

    while i < len(lines):

        line = lines[i]

        # ----------------------------------------------------
        # Riconoscimento concorso
        # ----------------------------------------------------

        match = re.search(
            r"Concorso\s*(?:Nº|N°|No\.?|n\.)?\s*"
            r"(\d+)\s+del\s+"
            r"(\d{1,2})\s+"
            r"([A-Za-zÀ-ÿ]+)\s+"
            r"(\d{4})",
            line,
            re.IGNORECASE
        )

        if not match:

            i += 1
            continue

        contest = int(
            match.group(1)
        )

        day = int(
            match.group(2)
        )

        month_text = (
            match.group(3)
            .lower()
        )

        year_text = int(
            match.group(4)
        )

        # ----------------------------------------------------
        # conversione mese
        # ----------------------------------------------------

        month_reverse = {
            v: k
            for k, v in MONTHS.items()
        }

        if month_text not in month_reverse:

            i += 1
            continue

        real_month = (
            month_reverse[month_text]
        )

        try:

            date = pd.Timestamp(
                year_text,
                real_month,
                day
            )

        except Exception:

            i += 1
            continue

        # ----------------------------------------------------
        # cerchiamo le 6 sestine successive
        # ----------------------------------------------------

        numbers = []

        jolly = None
        superstar = None

        j = i + 1

        while (
            j < len(lines)
            and j < i + 40
        ):

            current = lines[j]

            # ----------------------------------------------
            # Jolly
            # ----------------------------------------------

            if current.lower() == "jolly":

                if j + 1 < len(lines):

                    try:

                        candidate = int(
                            lines[j + 1]
                        )

                        if 1 <= candidate <= 90:
                            jolly = candidate

                    except Exception:
                        pass

                j += 2
                continue

            # ----------------------------------------------
            # SuperStar
            # ----------------------------------------------

            if current.lower() == "superstar":

                if j + 1 < len(lines):

                    try:

                        candidate = int(
                            lines[j + 1]
                        )

                        if 1 <= candidate <= 90:
                            superstar = candidate

                    except Exception:
                        pass

                j += 2
                continue

            # ----------------------------------------------
            # Numeri singoli
            # ----------------------------------------------

            if re.fullmatch(
                r"\d{1,2}",
                current
            ):

                n = int(current)

                if 1 <= n <= 90:

                    numbers.append(n)

                    if len(numbers) == 6:
                        break

            j += 1

        # ----------------------------------------------------
        # validazione sestina
        # ----------------------------------------------------

        if len(numbers) == 6:

            numbers = sorted(
                numbers
            )

            if len(set(numbers)) == 6:

                records.append({

                    "concorso":
                        contest,

                    "data":
                        date,

                    "n1":
                        numbers[0],

                    "n2":
                        numbers[1],

                    "n3":
                        numbers[2],

                    "n4":
                        numbers[3],

                    "n5":
                        numbers[4],

                    "n6":
                        numbers[5],

                    "jolly":
                        jolly,

                    "superstar":
                        superstar,

                    "source":
                        url
                })

        i = j + 1

    return records


# ============================================================
# DOWNLOAD ARCHIVIO
# ============================================================

def download_archive():

    print()
    print("=" * 80)
    print(
        "DOWNLOAD ARCHIVIO UFFICIALE SUPERNALOTTO"
    )
    print("=" * 80)

    all_records = []

    for year in range(
        FIRST_YEAR,
        LAST_YEAR + 1
    ):

        print()
        print(
            f"[ANNO {year}]"
        )

        for month in range(
            1,
            13
        ):

            records = parse_month_page(
                year,
                month
            )

            all_records.extend(
                records
            )

            time.sleep(
                REQUEST_DELAY
            )

    if not all_records:

        raise RuntimeError(
            "Nessuna estrazione scaricata."
        )

    df = pd.DataFrame(
        all_records
    )

    return df


# ============================================================
# VALIDAZIONE ARCHIVIO
# ============================================================

def validate_archive(df):

    print()
    print("=" * 80)
    print(
        "VALIDAZIONE DATASET"
    )
    print("=" * 80)

    report = []

    # --------------------------------------------------------
    # conversione data
    # --------------------------------------------------------

    df["data"] = pd.to_datetime(
        df["data"],
        errors="coerce"
    )

    # --------------------------------------------------------
    # ordinamento
    # --------------------------------------------------------

    df = (
        df
        .sort_values(
            ["data", "concorso"]
        )
        .reset_index(drop=True)
    )

    # --------------------------------------------------------
    # duplicati concorso
    # --------------------------------------------------------

    duplicate_contests = (
        df["concorso"]
        .duplicated(
            keep=False
        )
    )

    duplicate_count = int(
        duplicate_contests.sum()
    )

    print(
        f"Duplicati numero concorso: "
        f"{duplicate_count}"
    )

    report.append({
        "check":
            "duplicate_concorso",
        "value":
            duplicate_count,
        "status":
            "OK"
            if duplicate_count == 0
            else "ATTENZIONE"
    })

    # --------------------------------------------------------
    # duplicati data
    #
    # NON sono automaticamente un errore.
    # --------------------------------------------------------

    duplicate_dates = (
        df["data"]
        .duplicated(
            keep=False
        )
    )

    duplicate_date_count = int(
        duplicate_dates.sum()
    )

    print(
        f"Date duplicate: "
        f"{duplicate_date_count}"
    )

    report.append({
        "check":
            "duplicate_date",
        "value":
            duplicate_date_count,
        "status":
            "INFORMATIVO"
    })

    # --------------------------------------------------------
    # validazione sestine
    # --------------------------------------------------------

    bad_rows = []

    number_columns = [
        "n1",
        "n2",
        "n3",
        "n4",
        "n5",
        "n6"
    ]

    for idx, row in df.iterrows():

        nums = [
            int(row[c])
            for c in number_columns
        ]

        valid = (
            len(nums) == 6
            and
            len(set(nums)) == 6
            and
            all(
                1 <= n <= 90
                for n in nums
            )
        )

        if not valid:

            bad_rows.append(
                idx
            )

    print(
        f"Righe con sestina non valida: "
        f"{len(bad_rows)}"
    )

    report.append({
        "check":
            "invalid_sestine",
        "value":
            len(bad_rows),
        "status":
            "OK"
            if len(bad_rows) == 0
            else "ERRORE"
    })

    # --------------------------------------------------------
    # date mancanti
    # --------------------------------------------------------

    missing_dates = int(
        df["data"].isna().sum()
    )

    print(
        f"Date non valide/mancanti: "
        f"{missing_dates}"
    )

    report.append({
        "check":
            "invalid_dates",
        "value":
            missing_dates,
        "status":
            "OK"
            if missing_dates == 0
            else "ERRORE"
    })

    # --------------------------------------------------------
    # elimina SOLO duplicati identici di concorso
    # --------------------------------------------------------

    before = len(df)

    df = (
        df
        .drop_duplicates(
            subset=["concorso"],
            keep="first"
        )
        .reset_index(drop=True)
    )

    removed = (
        before -
        len(df)
    )

    print(
        f"Righe eliminate per duplicato "
        f"di concorso: {removed}"
    )

    # --------------------------------------------------------
    # rimuove righe non valide
    # --------------------------------------------------------

    if bad_rows:

        df = df.drop(
            index=bad_rows,
            errors="ignore"
        )

        df = (
            df
            .reset_index(drop=True)
        )

    # --------------------------------------------------------
    # verifica sequenza
    # --------------------------------------------------------

    df = (
        df
        .sort_values(
            ["data", "concorso"]
        )
        .reset_index(drop=True)
    )

    # --------------------------------------------------------
    # report
    # --------------------------------------------------------

    quality = pd.DataFrame(
        report
    )

    quality.to_csv(
        QUALITY_REPORT,
        index=False
    )

    print()
    print(
        f"Estrazioni valide finali: "
        f"{len(df)}"
    )

    print(
        f"Prima estrazione: "
        f"{df['data'].min().date()}"
    )

    print(
        f"Ultima estrazione: "
        f"{df['data'].max().date()}"
    )

    return df


# ============================================================
# SALVATAGGIO
# ============================================================

def save_archive(df):

    columns = [
        "concorso",
        "data",
        "n1",
        "n2",
        "n3",
        "n4",
        "n5",
        "n6",
        "jolly",
        "superstar",
        "source"
    ]

    for c in columns:

        if c not in df.columns:

            df[c] = None

    df[columns].to_csv(
        OUTPUT_CSV,
        index=False
    )

    print()
    print(
        f"[OK] Dataset salvato: "
        f"{OUTPUT_CSV}"
    )


# ============================================================
# ENGINE V5
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

        self.df = (
            df
            .sort_values(
                ["data", "concorso"]
            )
            .reset_index(drop=True)
        )

        self.draws = self.df[
            [
                "n1",
                "n2",
                "n3",
                "n4",
                "n5",
                "n6"
            ]
        ].to_numpy(
            dtype=np.int16
        )

        self.dates = (
            self.df["data"]
            .to_numpy()
        )

    # ========================================================
    # STATE
    # ========================================================

    @staticmethod
    def build_state(history):

        frequency = np.zeros(
            91,
            dtype=np.int32
        )

        delay = np.zeros(
            91,
            dtype=np.int32
        )

        for draw in history:

            delay[1:] += 1

            for n in draw:

                frequency[n] += 1
                delay[n] = 0

        return frequency, delay

    # ========================================================
    # SELECT
    # ========================================================

    @staticmethod
    def select_numbers(
        strategy,
        frequency,
        delay,
        rng
    ):

        numbers = np.arange(
            1,
            91
        )

        # ----------------------------------------------------
        # FREQUENCY
        # ----------------------------------------------------

        if strategy == "FREQUENCY":

            weights = (
                frequency[1:]
                .astype(float)
            )

            if weights.sum() == 0:

                return np.sort(
                    rng.choice(
                        numbers,
                        6,
                        replace=False
                    )
                )

            weights += 1e-12

            probabilities = (
                weights /
                weights.sum()
            )

            return np.sort(
                rng.choice(
                    numbers,
                    6,
                    replace=False,
                    p=probabilities
                )
            )

        # ----------------------------------------------------
        # DELAY
        # ----------------------------------------------------

        if strategy == "DELAY":

            weights = (
                delay[1:]
                .astype(float)
            )

            if weights.sum() == 0:

                return np.sort(
                    rng.choice(
                        numbers,
                        6,
                        replace=False
                    )
                )

            weights += 1e-12

            probabilities = (
                weights /
                weights.sum()
            )

            return np.sort(
                rng.choice(
                    numbers,
                    6,
                    replace=False,
                    p=probabilities
                )
            )

        # ----------------------------------------------------
        # MIXED
        # ----------------------------------------------------

        if strategy == "MIXED":

            f = (
                frequency[1:]
                .astype(float)
            )

            d = (
                delay[1:]
                .astype(float)
            )

            if f.max() > 0:

                f /= f.max()

            if d.max() > 0:

                d /= d.max()

            score = (
                0.5 * f +
                0.5 * d
            )

            top = np.argsort(
                score
            )[-30:]

            candidates = (
                numbers[top]
            )

            candidate_scores = (
                score[top]
            )

            if candidate_scores.sum() <= 0:

                return np.sort(
                    rng.choice(
                        candidates,
                        6,
                        replace=False
                    )
                )

            probabilities = (
                candidate_scores /
                candidate_scores.sum()
            )

            return np.sort(
                rng.choice(
                    candidates,
                    6,
                    replace=False,
                    p=probabilities
                )
            )

        raise ValueError(
            strategy
        )

    # ========================================================
    # UPDATE
    # ========================================================

    @staticmethod
    def update_state(
        actual,
        frequency,
        delay
    ):

        delay[1:] += 1

        for n in actual:

            frequency[n] += 1
            delay[n] = 0

    # ========================================================
    # REAL OOS
    # ========================================================

    def run_oos(
        self,
        strategy,
        start_date,
        end_date,
        seed
    ):

        start = pd.Timestamp(
            start_date
        )

        end = pd.Timestamp(
            end_date
        )

        indices = np.where(
            (self.dates >= start)
            &
            (self.dates <= end)
        )[0]

        if len(indices) == 0:

            raise ValueError(
                "Nessuna estrazione nella finestra."
            )

        first = indices[0]

        if first < MIN_HISTORY:

            raise ValueError(
                "Storico insufficiente."
            )

        history = (
            self.draws[:first]
        )

        frequency, delay = (
            self.build_state(
                history
            )
        )

        rng = np.random.default_rng(
            seed
        )

        distribution = np.zeros(
            7,
            dtype=np.int64
        )

        total_hits = 0

        for idx in indices:

            ticket = (
                self.select_numbers(
                    strategy,
                    frequency,
                    delay,
                    rng
                )
            )

            actual = (
                self.draws[idx]
            )

            hits = len(
                set(ticket)
                .intersection(actual)
            )

            distribution[hits] += 1

            total_hits += hits

            self.update_state(
                actual,
                frequency,
                delay
            )

        return OOSResult(
            strategy,
            len(indices),
            total_hits,
            total_hits / len(indices),
            distribution
        )

    # ========================================================
    # RANDOM DRAW
    # ========================================================

    @staticmethod
    def random_draw(rng):

        return np.sort(
            rng.choice(
                np.arange(1, 91),
                6,
                replace=False
            )
        )

    # ========================================================
    # NULL MONTE CARLO
    # ========================================================

    def null_simulation(
        self,
        strategy,
        start_date,
        end_date,
        n_simulations,
        seed
    ):

        start = pd.Timestamp(
            start_date
        )

        end = pd.Timestamp(
            end_date
        )

        indices = np.where(
            (self.dates >= start)
            &
            (self.dates <= end)
        )[0]

        first = indices[0]

        initial_history = (
            self.draws[:first]
        )

        oos_length = len(
            indices
        )

        master = np.random.default_rng(
            seed
        )

        totals = np.zeros(
            n_simulations,
            dtype=np.int32
        )

        for sim in range(
            n_simulations
        ):

            rng = np.random.default_rng(
                master.integers(
                    0,
                    2**63 - 1
                )
            )

            frequency, delay = (
                self.build_state(
                    initial_history
                )
            )

            total = 0

            for _ in range(
                oos_length
            ):

                ticket = (
                    self.select_numbers(
                        strategy,
                        frequency,
                        delay,
                        rng
                    )
                )

                actual = (
                    self.random_draw(
                        rng
                    )
                )

                hits = len(
                    set(ticket)
                    .intersection(actual)
                )

                total += hits

                self.update_state(
                    actual,
                    frequency,
                    delay
                )

            totals[sim] = total

        return totals

    # ========================================================
    # THEORETICAL HYPERGEOMETRIC
    # ========================================================

    @staticmethod
    def theoretical_distribution():

        denominator = math.comb(
            90,
            6
        )

        p = []

        for k in range(7):

            numerator = (
                math.comb(6, k)
                *
                math.comb(84, 6-k)
            )

            p.append(
                numerator /
                denominator
            )

        return np.array(p)

    # ========================================================
    # P VALUE
    # ========================================================

    @staticmethod
    def pvalue(
        observed,
        null
    ):

        extreme = np.sum(
            null >= observed
        )

        return (
            extreme + 1
        ) / (
            len(null) + 1
        )

    # ========================================================
    # PHASE
    # ========================================================

    def run_phase(
        self,
        phase_name,
        start_date,
        end_date,
        strategies,
        seed
    ):

        print()
        print("=" * 90)
        print(
            phase_name
        )
        print(
            f"{start_date} -> {end_date}"
        )
        print("=" * 90)

        alpha_bonferroni = (
            ALPHA /
            len(strategies)
        )

        print(
            f"Alpha Bonferroni: "
            f"{alpha_bonferroni:.8f}"
        )

        results = []

        for sid, strategy in enumerate(
            strategies
        ):

            print()
            print(
                f">>> {strategy}"
            )

            s = (
                seed +
                sid * 100000
            )

            observed = self.run_oos(
                strategy,
                start_date,
                end_date,
                s
            )

            print(
                f"Draw OOS: "
                f"{observed.draws}"
            )

            print(
                f"Hit totali: "
                f"{observed.total_hits}"
            )

            print(
                f"Media hit: "
                f"{observed.mean_hits:.6f}"
            )

            print(
                "Distribuzione 0..6:"
            )

            for k in range(7):

                count = (
                    observed.distribution[k]
                )

                pct = (
                    count /
                    observed.draws *
                    100
                )

                print(
                    f"  {k}: "
                    f"{count} "
                    f"({pct:.3f}%)"
                )

            print(
                f"Monte Carlo H0: "
                f"{N_NULL:,}"
            )

            null = self.null_simulation(
                strategy,
                start_date,
                end_date,
                N_NULL,
                s + 999999
            )

            null_mean = (
                null.mean()
            )

            null_low = (
                np.percentile(
                    null,
                    2.5
                )
            )

            null_high = (
                np.percentile(
                    null,
                    97.5
                )
            )

            p = self.pvalue(
                observed.total_hits,
                null
            )

            significant = (
                p <
                alpha_bonferroni
            )

            print(
                f"Media H0: "
                f"{null_mean:.3f}"
            )

            print(
                f"IC 95% H0: "
                f"[{null_low:.1f}, "
                f"{null_high:.1f}]"
            )

            print(
                f"P-value: "
                f"{p:.8f}"
            )

            print(
                f"Significativo: "
                f"{significant}"
            )

            results.append({

                "phase":
                    phase_name,

                "strategy":
                    strategy,

                "oos_start":
                    start_date,

                "oos_end":
                    end_date,

                "oos_draws":
                    observed.draws,

                "total_hits":
                    observed.total_hits,

                "mean_hits":
                    observed.mean_hits,

                "null_mean":
                    null_mean,

                "null_ci_low":
                    null_low,

                "null_ci_high":
                    null_high,

                "p_value":
                    p,

                "alpha_bonferroni":
                    alpha_bonferroni,

                "significant":
                    significant
            })

        return pd.DataFrame(
            results
        )

    # ========================================================
    # COMPLETE PROTOCOL
    # ========================================================

    def run_protocol(self):

        print()
        print("#" * 90)
        print(
            "SUPERNALOTTO V5 FINAL"
        )
        print(
            "DISCOVERY -> CONFIRMATION"
        )
        print("#" * 90)

        # ----------------------------------------------------
        # teoria
        # ----------------------------------------------------

        theoretical = (
            self.theoretical_distribution()
        )

        print()
        print(
            "BASELINE TEORICA 6/90"
        )

        for k, p in enumerate(
            theoretical
        ):

            print(
                f"{k} hit: "
                f"{p*100:.6f}%"
            )

        print(
            "Hit medi teorici: "
            f"{sum(k*theoretical[k] for k in range(7)):.6f}"
        )

        # ----------------------------------------------------
        # DISCOVERY
        # ----------------------------------------------------

        discovery = self.run_phase(

            "DISCOVERY OOS",

            DISCOVERY_START,

            DISCOVERY_END,

            STRATEGIES,

            SEED
        )

        survivors = (
            discovery[
                discovery["significant"]
            ]["strategy"]
            .tolist()
        )

        discovery.to_csv(
            "superenalotto_v5_discovery.csv",
            index=False
        )

        print()
        print("#" * 90)
        print(
            "ESITO DISCOVERY"
        )
        print("#" * 90)

        if not survivors:

            print(
                "NESSUNA STRATEGIA SIGNIFICATIVA."
            )

            print(
                "NON SI RIGETTA H0."
            )

            print(
                "STUDIO CHIUSO."
            )

            return {
                "discovery":
                    discovery,
                "confirmation":
                    None
            }

        # ----------------------------------------------------
        # CONFIRMATION
        # ----------------------------------------------------

        print(
            "Strategie ammesse:"
        )

        for s in survivors:

            print(
                f"  -> {s}"
            )

        confirmation = self.run_phase(

            "CONFIRMATION OOS",

            CONFIRMATION_START,

            CONFIRMATION_END,

            survivors,

            SEED + 5000000
        )

        confirmation.to_csv(
            "superenalotto_v5_confirmation.csv",
            index=False
        )

        # ----------------------------------------------------
        # VERDETTO
        # ----------------------------------------------------

        confirmed = (
            confirmation[
                confirmation["significant"]
            ]["strategy"]
            .tolist()
        )

        print()
        print("#" * 90)
        print(
            "VERDETTO FINALE"
        )
        print("#" * 90)

        if confirmed:

            print(
                "ANOMALIA STATISTICA "
                "REPLICATA."
            )

            print(
                "Strategie:"
            )

            for s in confirmed:

                print(
                    f"  -> {s}"
                )

            print()
            print(
                "DA APPROFONDIRE."
            )

        else:

            print(
                "SEGNALE NON REPLICATO."
            )

            print(
                "NESSUNA EVIDENZA DI "
                "VANTAGGIO ROBUSTO."
            )

        return {
            "discovery":
                discovery,
            "confirmation":
                confirmation
        }


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print(
        "SUPERNALOTTO V5"
    )

    print()
    print(
        "FASE 1: DOWNLOAD ARCHIVIO UFFICIALE"
    )

    raw = download_archive()

    print()
    print(
        f"Record scaricati: {len(raw)}"
    )

    clean = validate_archive(
        raw
    )

    save_archive(
        clean
    )

    # --------------------------------------------------------
    # ENGINE
    # --------------------------------------------------------

    print()
    print(
        "FASE 2: ENGINE V5"
    )

    engine = (
        SuperEnalottoEngineV5(
            clean
        )
    )

    result = (
        engine.run_protocol()
    )

    print()
    print(
        "PROCESSO COMPLETATO."
    )


# ============================================================
# AVVIO
# ============================================================

if __name__ == "__main__":

    main()
