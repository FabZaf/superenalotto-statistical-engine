import csv
import json
import re
import sys
from datetime import datetime
import urllib.request


# ============================================================
# SUPERENALOTTO V5.8 — INDEPENDENT AUDIT
# ============================================================
#
# SCOPO:
#   Verificare indipendentemente l'integrità di
#   superenalotto_history.csv confrontandolo con estrazioni.it.
#
# IMPORTANTE:
#   - NON modifica il CSV
#   - NON modifica V5.7
#   - NON esegue strategie statistiche
#   - NON esegue Monte Carlo
#   - NON genera combinazioni
#   - FAIL-CLOSED: se la fonte non è verificabile -> FAILED
#
# ============================================================

CSV_FILENAME = "superenalotto_history.csv"
REPORT_FILENAME = "independent_audit_report.json"

SOURCE_URL_PATTERN = (
    "https://www.estrazioni.it/superenalotto/?anno={year}"
)

EXPECTED_COLUMNS = [
    "data",
    "concorso",
    "n1",
    "n2",
    "n3",
    "n4",
    "n5",
    "n6",
]

START_YEAR = 1997
CURRENT_YEAR = datetime.now().year


# ============================================================
# UTILITÀ
# ============================================================

def fetch_web_page(url: str) -> str:
    """
    Scarica la pagina HTML dalla fonte.
    Qualunque errore viene propagato come errore di audit.
    """

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "Chrome/120 Safari/537.36 "
                "SuperEnalottoV5.8Audit"
            )
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = getattr(response, "status", 200)

            if status != 200:
                raise RuntimeError(
                    f"HTTP status {status}"
                )

            raw = response.read()

            if not raw:
                raise RuntimeError(
                    "Pagina HTML vuota"
                )

            return raw.decode(
                "utf-8",
                errors="ignore"
            )

    except Exception as exc:
        raise RuntimeError(
            f"Impossibile scaricare {url}: {exc}"
        )


def normalize_html(html: str) -> str:
    """
    Normalizza l'HTML per rendere più affidabile
    l'estrazione testuale.
    """

    # Rimuove script e style
    html = re.sub(
        r"<script\b[^>]*>.*?</script>",
        " ",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    html = re.sub(
        r"<style\b[^>]*>.*?</style>",
        " ",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Trasforma i tag in separatori
    html = re.sub(
        r"<[^>]+>",
        " ",
        html,
    )

    # HTML entities comuni
    replacements = {
        "&nbsp;": " ",
        "&amp;": "&",
        "&ndash;": "-",
        "&mdash;": "-",
        "&#8211;": "-",
        "&#8212;": "-",
        "&quot;": '"',
        "&#39;": "'",
    }

    for old, new in replacements.items():
        html = html.replace(old, new)

    # Normalizzazione spazi
    html = re.sub(
        r"\s+",
        " ",
        html,
    )

    return html.strip()


# ============================================================
# PARSING DELLE ESTRAZIONI
# ============================================================

DRAW_PATTERN = re.compile(
    r"""
    (?:
        Concorso\s+n\s*[.°º]?\s*(?P<contest>\d+)\s*
    )?
    (?:
        SuperEnalotto\s*
    )?
    (?P<date>
        \d{1,2}/\d{1,2}/\d{4}
    )
    \s+
    (?P<numbers>
        \d{1,2}\s+
        \d{1,2}\s+
        \d{1,2}\s+
        \d{1,2}\s+
        \d{1,2}\s+
        \d{1,2}
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def parse_source_draws(html: str):
    """
    Estrae le estrazioni dalla pagina.

    Restituisce record:
        {
            date: YYYY-MM-DD,
            contest: int | None,
            numbers: [n1..n6]
        }

    Il parser supporta:
      - formato storico con "Concorso n. X"
      - formato moderno "SuperEnalotto DATA"
    """

    text = normalize_html(html)

    results = []

    for match in DRAW_PATTERN.finditer(text):

        contest_raw = match.group("contest")
        date_raw = match.group("date")
        numbers_raw = match.group("numbers")

        try:
            date_obj = datetime.strptime(
                date_raw,
                "%d/%m/%Y"
            )
        except ValueError:
            continue

        numbers = [
            int(x)
            for x in numbers_raw.split()
        ]

        if len(numbers) != 6:
            continue

        if any(
            n < 1 or n > 90
            for n in numbers
        ):
            continue

        if len(set(numbers)) != 6:
            continue

        contest = (
            int(contest_raw)
            if contest_raw is not None
            else None
        )

        results.append(
            {
                "data": date_obj.strftime("%Y-%m-%d"),
                "concorso": contest,
                "numbers": numbers,
            }
        )

    # Elimina eventuali duplicati prodotti dal parsing
    unique = {}

    for record in results:

        key = (
            record["data"],
            tuple(record["numbers"]),
            record["concorso"],
        )

        unique[key] = record

    return list(unique.values())


# ============================================================
# LETTURA CSV
# ============================================================

def load_csv():
    errors = []
    rows = []

    try:
        with open(
            CSV_FILENAME,
            mode="r",
            encoding="utf-8",
            newline="",
        ) as f:

            reader = csv.reader(f)

            header = next(reader, None)

            if header != EXPECTED_COLUMNS:
                errors.append(
                    "SCHEMA ERROR: intestazione CSV non valida. "
                    f"Atteso={EXPECTED_COLUMNS}, "
                    f"Trovato={header}"
                )
                return [], errors

            for row_idx, row in enumerate(
                reader,
                start=2
            ):

                if len(row) != 8:
                    errors.append(
                        f"Riga {row_idx}: "
                        f"numero colonne {len(row)} invece di 8"
                    )
                    continue

                rows.append(
                    {
                        "row_num": row_idx,
                        "data": row[0].strip(),
                        "concorso": row[1].strip(),
                        "n1": row[2].strip(),
                        "n2": row[3].strip(),
                        "n3": row[4].strip(),
                        "n4": row[5].strip(),
                        "n5": row[6].strip(),
                        "n6": row[7].strip(),
                    }
                )

    except FileNotFoundError:
        errors.append(
            f"FILE ERROR: "
            f"{CSV_FILENAME} non trovato"
        )

    except Exception as exc:
        errors.append(
            f"FILE ERROR: impossibile leggere "
            f"{CSV_FILENAME}: {exc}"
        )

    return rows, errors


# ============================================================
# VALIDAZIONE CSV
# ============================================================

def validate_csv(rows):

    errors = []
    parsed = []

    seen_full = set()
    seen_keys = set()

    previous_date = None

    for item in rows:

        row_num = item["row_num"]

        date_str = item["data"]
        contest_str = item["concorso"]

        raw_numbers = [
            item[f"n{i}"]
            for i in range(1, 7)
        ]

        # ----------------------------------------------------
        # DUPLICATO COMPLETO
        # ----------------------------------------------------

        full_key = (
            date_str,
            contest_str,
            *raw_numbers,
        )

        if full_key in seen_full:
            errors.append(
                f"Riga {row_num}: "
                "duplicato completo"
            )

        seen_full.add(full_key)

        # ----------------------------------------------------
        # DATA + CONCORSO DUPLICATI
        # ----------------------------------------------------

        key = (
            date_str,
            contest_str,
        )

        if key in seen_keys:
            errors.append(
                f"Riga {row_num}: "
                f"duplicata chiave data/concorso {key}"
            )

        seen_keys.add(key)

        # ----------------------------------------------------
        # DATA
        # ----------------------------------------------------

        try:
            date_obj = datetime.strptime(
                date_str,
                "%Y-%m-%d"
            )
        except ValueError:
            errors.append(
                f"Riga {row_num}: "
                f"data non valida '{date_str}'"
            )
            continue

        if previous_date is not None:

            if date_obj < previous_date:

                errors.append(
                    f"Riga {row_num}: "
                    "ordine cronologico non crescente"
                )

        previous_date = date_obj

        # ----------------------------------------------------
        # CONCORSO
        # ----------------------------------------------------

        try:
            contest = int(contest_str)

            if contest <= 0:
                raise ValueError

        except ValueError:

            errors.append(
                f"Riga {row_num}: "
                f"concorso non valido '{contest_str}'"
            )

            continue

        # ----------------------------------------------------
        # NUMERI
        # ----------------------------------------------------

        numbers = []

        valid_numbers = True

        for idx, raw in enumerate(
            raw_numbers,
            start=1
        ):

            try:
                n = int(raw)

            except ValueError:

                errors.append(
                    f"Riga {row_num}: "
                    f"n{idx} non intero '{raw}'"
                )

                valid_numbers = False
                continue

            if not 1 <= n <= 90:

                errors.append(
                    f"Riga {row_num}: "
                    f"n{idx} fuori range: {n}"
                )

                valid_numbers = False

            numbers.append(n)

        if len(numbers) != 6:

            errors.append(
                f"Riga {row_num}: "
                f"numero numeri diverso da 6"
            )

            valid_numbers = False

        if valid_numbers and len(set(numbers)) != 6:

            errors.append(
                f"Riga {row_num}: "
                f"numeri duplicati {numbers}"
            )

            valid_numbers = False

        parsed.append(
            {
                "row_num": row_num,
                "data": date_str,
                "datetime": date_obj,
                "year": date_obj.year,
                "concorso": contest,
                "numbers": numbers,
            }
        )

    return parsed, errors


# ============================================================
# CONTROLLO SEQUENZE ANNUALI
# ============================================================

def validate_annual_sequences(parsed):

    errors = []
    by_year = {}

    for record in parsed:

        by_year.setdefault(
            record["year"],
            []
        ).append(record)

    expected_years = set(
        range(
            START_YEAR,
            CURRENT_YEAR + 1
        )
    )

    actual_years = set(by_year.keys())

    missing_years = (
        expected_years - actual_years
    )

    if missing_years:

        errors.append(
            "ANNI ERROR: mancano gli anni "
            f"{sorted(missing_years)}"
        )

    # --------------------------------------------------------
    # CONTROLLI PER ANNO
    # --------------------------------------------------------

    for year in sorted(by_year):

        records = sorted(
            by_year[year],
            key=lambda x: x["datetime"]
        )

        contests = [
            r["concorso"]
            for r in records
        ]

        if year == 1997:

            expected = list(
                range(87, 96)
            )

            if contests != expected:

                errors.append(
                    f"CONCORSI {year} ERROR: "
                    f"atteso {expected}, "
                    f"trovato {contests}"
                )

        else:

            if not contests:
                continue

            expected = list(
                range(
                    1,
                    len(contests) + 1
                )
            )

            if contests != expected:

                errors.append(
                    f"CONCORSI {year} ERROR: "
                    "sequenza non consecutiva. "
                    f"Trovato {contests}"
                )

    return by_year, errors


# ============================================================
# CONFRONTO CSV ↔ FONTE
# ============================================================

def compare_year(
    year,
    csv_records,
    source_records,
):

    errors = []

    result = {
        "csv_record_count": len(csv_records),
        "web_source_count": len(source_records),
        "count_match": False,
        "records_checked": 0,
        "records_matched": 0,
        "records_mismatched": 0,
        "explicit_anchors_checked": 0,
        "explicit_anchors_matched": 0,
    }

    # --------------------------------------------------------
    # 1. CONTEGGIO
    # --------------------------------------------------------

    if len(csv_records) != len(source_records):

        errors.append(
            f"AUDIT COUNT MISMATCH {year}: "
            f"CSV={len(csv_records)}, "
            f"WEB={len(source_records)}"
        )

    else:

        result["count_match"] = True

    # --------------------------------------------------------
    # 2. COSTRUZIONE INDICI
    # --------------------------------------------------------

    csv_by_date = {
        r["data"]: r
        for r in csv_records
    }

    web_by_date = {
        r["data"]: r
        for r in source_records
    }

    # --------------------------------------------------------
    # 3. DATE
    # --------------------------------------------------------

    csv_dates = set(csv_by_date)
    web_dates = set(web_by_date)

    missing_dates = sorted(
        web_dates - csv_dates
    )

    extra_dates = sorted(
        csv_dates - web_dates
    )

    if missing_dates:

        errors.append(
            f"AUDIT DATE MISMATCH {year}: "
            f"date presenti WEB ma assenti CSV: "
            f"{missing_dates}"
        )

    if extra_dates:

        errors.append(
            f"AUDIT DATE MISMATCH {year}: "
            f"date presenti CSV ma assenti WEB: "
            f"{extra_dates}"
        )

    # --------------------------------------------------------
    # 4. CONFRONTO SESTINE
    # --------------------------------------------------------

    common_dates = sorted(
        csv_dates & web_dates
    )

    for date_str in common_dates:

        csv_record = csv_by_date[date_str]
        web_record = web_by_date[date_str]

        result["records_checked"] += 1

        if (
            csv_record["numbers"]
            == web_record["numbers"]
        ):

            result["records_matched"] += 1

        else:

            result["records_mismatched"] += 1

            errors.append(
                f"AUDIT NUMBERS MISMATCH {year}: "
                f"{date_str} "
                f"CSV={csv_record['numbers']} "
                f"WEB={web_record['numbers']}"
            )

    # --------------------------------------------------------
    # 5. CONFRONTO CONCORSI ESPLICITI
    # --------------------------------------------------------

    for web_record in source_records:

        if web_record["concorso"] is None:
            continue

        result["explicit_anchors_checked"] += 1

        date_str = web_record["data"]
        contest = web_record["concorso"]

        csv_record = csv_by_date.get(
            date_str
        )

        if csv_record is None:

            errors.append(
                f"AUDIT ANCHOR ERROR {year}: "
                f"fonte indica concorso {contest} "
                f"del {date_str}, "
                "ma la data manca nel CSV"
            )

            continue

        if csv_record["concorso"] != contest:

            errors.append(
                f"AUDIT CONTEST MISMATCH {year}: "
                f"{date_str} "
                f"CSV concorso="
                f"{csv_record['concorso']} "
                f"WEB concorso={contest}"
            )

            continue

        result[
            "explicit_anchors_matched"
        ] += 1

    return result, errors


# ============================================================
# MAIN
# ============================================================

def main():

    report = {
        "audit_version": "V5.8 Independent Audit",
        "generated_at": (
            datetime.utcnow().isoformat()
            + "Z"
        ),
        "status": "PASSED",
        "csv_file": CSV_FILENAME,
        "source": "estrazioni.it",
        "source_url_pattern": SOURCE_URL_PATTERN,
        "start_year": START_YEAR,
        "end_year": CURRENT_YEAR,
        "total_records": 0,
        "annual_results": {},
        "errors": [],
        "warnings": [],
    }

    errors = report["errors"]
    warnings = report["warnings"]

    # ========================================================
    # 1. CARICAMENTO CSV
    # ========================================================

    rows, load_errors = load_csv()

    errors.extend(load_errors)

    if not rows:

        errors.append(
            "RECORD ERROR: "
            "nessuna estrazione presente nel CSV"
        )

        report["status"] = "FAILED"

        with open(
            REPORT_FILENAME,
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                report,
                f,
                indent=2,
                ensure_ascii=False,
            )

        print(
            "=== AUDIT ENGINE V5.8: FAILED ==="
        )

        sys.exit(1)

    report["total_records"] = len(rows)

    # ========================================================
    # 2. VALIDAZIONE INTERNA CSV
    # ========================================================

    parsed, validation_errors = (
        validate_csv(rows)
    )

    errors.extend(validation_errors)

    # ========================================================
    # 3. SEQUENZE ANNUALI
    # ========================================================

    records_by_year, sequence_errors = (
        validate_annual_sequences(parsed)
    )

    errors.extend(sequence_errors)

    # ========================================================
    # 4. CONFRONTO CON FONTE
    # ========================================================

    expected_years = range(
        START_YEAR,
        CURRENT_YEAR + 1
    )

    for year in expected_years:

        csv_records = records_by_year.get(
            year,
            []
        )

        url = SOURCE_URL_PATTERN.format(
            year=year
        )

        print(
            f"[AUDIT] Verifica anno {year}..."
        )

        # ----------------------------------------------------
        # Download fonte
        # ----------------------------------------------------

        try:

            html = fetch_web_page(url)

        except Exception as exc:

            # FAIL-CLOSED
            errors.append(
                f"WEB SOURCE ERROR {year}: "
                f"{exc}"
            )

            report["annual_results"][
                str(year)
            ] = {
                "csv_record_count": len(
                    csv_records
                ),
                "web_source_count": None,
                "count_match": False,
                "records_checked": 0,
                "records_matched": 0,
                "records_mismatched": 0,
                "source_verified": False,
                "error": str(exc),
            }

            continue

        # ----------------------------------------------------
        # Parsing fonte
        # ----------------------------------------------------

        source_records = parse_source_draws(
            html
        )

        # ----------------------------------------------------
        # Fonte non parsata = ERRORE
        # ----------------------------------------------------

        if len(source_records) == 0:

            errors.append(
                f"WEB PARSE ERROR {year}: "
                "nessuna estrazione riconosciuta "
                "nella pagina della fonte"
            )

            report["annual_results"][
                str(year)
            ] = {
                "csv_record_count": len(
                    csv_records
                ),
                "web_source_count": 0,
                "count_match": False,
                "records_checked": 0,
                "records_matched": 0,
                "records_mismatched": 0,
                "source_verified": False,
                "error": (
                    "Nessuna estrazione "
                    "riconosciuta"
                ),
            }

            continue

        # ----------------------------------------------------
        # Confronto
        # ----------------------------------------------------

        result, compare_errors = (
            compare_year(
                year,
                csv_records,
                source_records,
            )
        )

        errors.extend(compare_errors)

        # ----------------------------------------------------
        # Verifica forte dell'anno
        # ----------------------------------------------------

        source_verified = (
            result["count_match"]
            and result["records_checked"]
            == result["records_matched"]
            and result["records_mismatched"]
            == 0
            and len(compare_errors) == 0
        )

        result["source_verified"] = (
            source_verified
        )

        if not source_verified:

            errors.append(
                f"SOURCE VERIFICATION FAILED "
                f"{year}"
            )

        report["annual_results"][
            str(year)
        ] = result

    # ========================================================
    # 5. CONTROLLO FINALE
    # ========================================================

    if errors:

        report["status"] = "FAILED"

    else:

        report["status"] = "PASSED"

    # ========================================================
    # 6. REPORT JSON
    # ========================================================

    with open(
        REPORT_FILENAME,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            report,
            f,
            indent=2,
            ensure_ascii=False,
        )

    # ========================================================
    # 7. OUTPUT
    # ========================================================

    print()
    print(
        "============================================================"
    )
    print(
        f"SUPERENALOTTO V5.8 "
        f"INDEPENDENT AUDIT: {report['status']}"
    )
    print(
        "============================================================"
    )

    print(
        f"Record CSV verificati: "
        f"{report['total_records']}"
    )

    print(
        f"Errori: {len(errors)}"
    )

    print(
        f"Warning: {len(warnings)}"
    )

    print()

    for year in expected_years:

        result = report[
            "annual_results"
        ].get(str(year))

        if not result:
            continue

        csv_count = result.get(
            "csv_record_count"
        )

        web_count = result.get(
            "web_source_count"
        )

        matched = result.get(
            "records_matched",
            0
        )

        checked = result.get(
            "records_checked",
            0
        )

        verified = result.get(
            "source_verified",
            False
        )

        print(
            f"{year}: "
            f"CSV={csv_count} "
            f"WEB={web_count} "
            f"MATCH={matched}/{checked} "
            f"VERIFIED={verified}"
        )

    print()
    print(
        f"Report: {REPORT_FILENAME}"
    )
    print(
        "============================================================"
    )

    if report["status"] == "FAILED":

        print(
            "AUDIT FALLITO — "
            "IL DATABASE NON DEVE ESSERE UTILIZZATO "
            "PER L'ANALISI STATISTICA."
        )

        sys.exit(1)

    print(
        "AUDIT SUPERATO — "
        "NESSUN ERRORE RILEVATO."
    )

    sys.exit(0)


if __name__ == "__main__":
    main()
