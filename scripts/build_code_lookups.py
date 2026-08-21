"""Regenerate data/naics_codes.json and data/psc_codes.json from authoritative sources.

These two maps decode the SAM.gov CSV's `NaicsCode` and `ClassificationCode`
columns into human-readable labels before the text goes to the eligibility
gate. Bare codes ("236220", "Y1JZ") are weak tokens; the decoded titles are the
strongest structured signal we have about a solicitation's scope of work.

Sources (both are the official publishers, both are plain HTTPS downloads):
  NAICS  Census Bureau, 2022 NAICS US structure, 2-6 digit codes
  PSC    acquisition.gov, Product and Service Code Manual (April 2025)

Deliberately no openpyxl/pandas dependency -- an .xlsx is a zip of XML, so
stdlib zipfile + ElementTree reads it fine and requirements.txt stays put.

Run:  python scripts/build_code_lookups.py
"""
from __future__ import annotations

import json
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path
from typing import Dict, List

NAICS_URL = "https://www.census.gov/naics/2022NAICS/2-6%20digit_2022_Codes.xlsx"
PSC_URL = "https://www.acquisition.gov/sites/default/files/manual/PSC%20April%202025.xlsx"

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# acquisition.gov blocks the default urllib agent.
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ai-lead-gen code-lookup builder)"}


def _download(url: str) -> zipfile.ZipFile:
    request = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(request, timeout=180) as response:
        return zipfile.ZipFile(BytesIO(response.read()))


def _read_sheet(book: zipfile.ZipFile, sheet: int = 1) -> List[List[str]]:
    """Return a worksheet as rows of strings, resolving the shared-string table."""
    shared: List[str] = []
    if "xl/sharedStrings.xml" in book.namelist():
        root = ET.fromstring(book.read("xl/sharedStrings.xml"))
        shared = [
            "".join(node.text or "" for node in si.iter(f"{NS}t"))
            for si in root.findall(f"{NS}si")
        ]

    rows: List[List[str]] = []
    root = ET.fromstring(book.read(f"xl/worksheets/sheet{sheet}.xml"))
    for row in root.iter(f"{NS}row"):
        cells: List[str] = []
        for cell in row.iter(f"{NS}c"):
            value = cell.find(f"{NS}v")
            if value is None or value.text is None:
                cells.append("")
            elif cell.get("t") == "s":
                cells.append(shared[int(value.text)])
            else:
                cells.append(value.text)
        rows.append(cells)
    return rows


def _clean(text: str) -> str:
    """Collapse whitespace and drop the non-breaking spaces Census embeds."""
    return " ".join(str(text).replace(" ", " ").split())


def _normalize_code(raw: str) -> str:
    """Excel stores numeric codes as floats -- '1005.0' must become '1005'.

    Alphanumeric codes ('Y1JZ', '7A20') arrive as plain strings and pass
    through untouched.
    """
    code = _clean(raw)
    if code.endswith(".0") and code[:-2].isdigit():
        return code[:-2]
    return code


def build_naics() -> Dict[str, str]:
    """All 2- through 6-digit 2022 NAICS codes.

    Keeps every level, not just the 1,012 six-digit industries: ~1% of SAM
    rows carry a truncated code (we have seen '23', '3361', '31199'), and
    those must still decode.
    """
    rows = _read_sheet(_download(NAICS_URL), sheet=1)
    codes: Dict[str, str] = {}
    for row in rows:
        if len(row) < 3:
            continue
        code, title = _normalize_code(row[1]), _clean(row[2])
        # Skip the header and the blank spacer rows.
        if code.isdigit() and 2 <= len(code) <= 6 and title:
            codes[code] = title
    return dict(sorted(codes.items()))


def build_psc() -> Dict[str, str]:
    """Active Product and Service Codes, with retired codes as fallback.

    The manual is a change log, not a snapshot: a code recurs once per
    revision, and only the row with a blank END DATE is current. Taking the
    first match would sometimes yield a retired label, so active rows are
    loaded last and win. Retired codes are kept only where no active row
    exists, so an older notice still decodes instead of falling through.
    """
    rows = _read_sheet(_download(PSC_URL), sheet=1)[1:]  # sheet2 is a notice

    active: Dict[str, str] = {}
    retired: Dict[str, str] = {}
    for row in rows:
        if len(row) < 4:
            continue
        code = _normalize_code(row[0])
        if not code:
            continue
        # Column E ("FULL NAME") is mixed-case and reads better than the
        # all-caps column B; fall back to B, which is always populated.
        label = _clean(row[4]) if len(row) > 4 and _clean(row[4]) else _clean(row[1])
        if not label:
            continue
        (retired if _clean(row[3]) else active)[code] = label

    merged = {**retired, **active}
    return dict(sorted(merged.items()))


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)

    naics = build_naics()
    psc = build_psc()

    for name, mapping in (("naics_codes.json", naics), ("psc_codes.json", psc)):
        path = DATA_DIR / name
        with path.open("w", encoding="utf-8") as handle:
            json.dump(mapping, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
        print(f"{path.relative_to(DATA_DIR.parent)}: {len(mapping)} codes")

    six_digit = sum(1 for code in naics if len(code) == 6)
    print(f"  NAICS 6-digit industries: {six_digit}")


if __name__ == "__main__":
    main()
