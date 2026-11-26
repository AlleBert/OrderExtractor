import argparse
import json
import math
import os
import platform
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

from order_extractor.core import Revision, validate
from order_extractor.extraction import extract
from scripts.fixtures import ROWS, corpus

MEASURED_FIELDS = ("article", "lot", "description", "quantity", "unit_price")


def benchmark():
    results = []
    with tempfile.TemporaryDirectory(prefix="oe-benchmark-") as scratch:
        scratch = Path(scratch)
        for path, case in corpus(scratch / "corpus").items():
            render = scratch / "render"
            render.mkdir(exist_ok=True)
            marks = []
            start = time.perf_counter()
            result = extract(path, render, lambda *_: marks.append(time.perf_counter()))
            elapsed = time.perf_counter() - start
            durations = [round(end - begin, 4) for begin, end in zip(marks, marks[1:])]
            expected = []
            for p in range(2 if case["multipage"] or case["mixed"] else 1):
                for n, (_, article, lot, description, quantity, price) in enumerate(ROWS):
                    if n == 0 and p == 0 and case["multiline"]:
                        description += " Finitura opaca"
                    expected.append((article, lot, description, quantity, price))
            actual = [tuple(row[field] for field in MEASURED_FIELDS) for row in result["rows"]]
            critical = sum(all(a[i] == e[i] for i in (0, 3, 4)) for a, e in zip(actual, expected))
            matches = {field: sum(a[i] == e[i] for a, e in zip(actual, expected))
                       for i, field in enumerate(MEASURED_FIELDS)}
            conflicts = sum(len(row.get("ocr_alternatives", {})) for row in result["rows"])
            revision = Revision(revision=0, rows=result["rows"])
            category = "mixed" if case["mixed"] else "scan" if case["scan"] else "digital"
            results.append(dict(file=path.name, category=category, pages=result["page_count"], seconds=round(elapsed, 4),
                                page_seconds=durations,
                                rows_expected=len(expected), rows_found=len(actual), critical_matches=critical,
                                perfect=actual == expected, matches=matches, ocr_conflicts=conflicts,
                                validation_issues=len(validate(revision)), engine=result["engines"]))
    groups = {}
    for name in ("digital", "scan", "mixed"):
        subset = [case for case in results if case["category"] == name]
        latency = sorted(seconds for case in subset for seconds in case["page_seconds"])
        groups[name] = {"documents": len(subset), "perfect": sum(case["perfect"] for case in subset),
                         "critical_matches": sum(case["critical_matches"] for case in subset),
                         "rows_expected": sum(case["rows_expected"] for case in subset),
                         "matches": {field: sum(case["matches"][field] for case in subset) for field in MEASURED_FIELDS},
                         "ocr_conflicts": sum(case["ocr_conflicts"] for case in subset),
                         "p50_seconds_per_page": round(statistics.median(latency), 3),
                         "p95_seconds_per_page": round(latency[math.ceil(len(latency) * .95) - 1], 3)}
    executable = os.environ.get("OE_TESSERACT", "tesseract")
    ocr_version = subprocess.check_output([executable, "--version"], text=True).splitlines()[0]
    return {"environment": platform.platform(), "python": platform.python_version(), "ocr": ocr_version,
            "ocr_languages": os.environ.get("OE_OCR_LANGS", "eng"),
            "percentile_method": "P95 nearest rank across individual page timings. Field matches use row order.",
            "groups": groups, "cases": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = benchmark()
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["groups"], indent=2))
