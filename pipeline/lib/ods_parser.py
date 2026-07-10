"""
Tolerant parser for the NE hen harrier tracking update .ods spreadsheet.

Reads content.xml directly via stdlib zipfile + ElementTree rather than a
higher-level library, so we control tolerant row/header detection ourselves
- the doc explicitly warns the column names/format have drifted across the
programme's history, and live inspection of the June 2026 file (verified
2026-07-10) found the underlying data is more polymorphic than the doc's own
research surfaced: "Date last contact" holds either a DD/MM/YYYY date, or,
for currently-alive birds, free text like "Transmitting June 2026".
"""

import re
import zipfile
import xml.etree.ElementTree as ET

from .location_parser import classify_location

NS = {
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
}

_REPEAT_ATTR = f"{{{NS['table']}}}number-columns-repeated"

# Canonical field name -> acceptable (lowercased, stripped) header aliases.
# The doc warns column names/format drift release to release - match
# tolerantly rather than requiring an exact header string.
COLUMN_ALIASES = {
    "tag_type": ["tag type"],
    "tag_id": ["tag id", "tag no", "tag number"],
    "sex": ["sex"],
    "nest_area": ["nest", "nest area"],
    "name": ["tag code or name", "name", "tag name"],
    "date_fitted": ["date fitted"],
    "date_last_contact": ["date last contact", "date of last contact"],
    "location_text": ["location of last contact", "location"],
    "os_reference": ["os reference", "os grid reference", "grid reference"],
    "status": ["status"],
    "notes_on_loss": ["notes on loss"],
    "notes_on_life_history": ["notes on life history", "life history notes"],
}
REQUIRED_FIELDS = ["tag_id", "status"]

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_DDMMYYYY_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_TRANSMITTING_RE = re.compile(r"transmitting\s+([a-z]+)\s+(\d{4})", re.IGNORECASE)
_TAG_ID_RE = re.compile(r"^(\d+[a-z]?)(\*{1,3})?$", re.IGNORECASE)
_STATUS_MAP = {"alive": "alive", "dead": "dead", "missing fate unknown": "missing_fate_unknown"}


class OdsParseError(RuntimeError):
    pass


def _cell_text(cell):
    parts = ["".join(p.itertext()) for p in cell.findall("text:p", NS)]
    return "\n".join(parts).strip()


def _row_to_cells(row):
    cells = []
    for cell in row.findall("table:table-cell", NS):
        repeat = int(cell.get(_REPEAT_ATTR, "1"))
        text = _cell_text(cell)
        if text == "" and repeat > 10:
            # Trailing padding cells (ODS pads rows out to the sheet's full
            # column count) - not real data, and not worth expanding.
            continue
        cells.extend([text] * repeat)
    return cells


def extract_table_rows(ods_bytes):
    """
    Extract the first sheet's rows as lists of cell-text strings.

    Returns (header_cells, data_rows). Stops collecting data_rows at the
    first entirely-blank row, or a row whose first cell is "Notes" (both
    signal the end of per-bird data and the start of NE's own footnote/
    legend block, observed at the end of the real file).
    """
    with zipfile.ZipFile(ods_bytes) as z:
        content = z.read("content.xml")

    root = ET.fromstring(content)
    table = root.find(".//table:table", NS)
    if table is None:
        return [], []

    rows = table.findall("table:table-row", NS)
    if not rows:
        return [], []

    header = _row_to_cells(rows[0])
    data_rows = []
    for row in rows[1:]:
        cells = _row_to_cells(row)
        if not any(c.strip() for c in cells):
            break
        if cells and cells[0].strip().lower() == "notes":
            break
        data_rows.append(cells)
    return header, data_rows


def _map_columns(header_cells):
    column_index = {}
    for idx, raw_name in enumerate(header_cells):
        key = raw_name.strip().lower()
        for field, aliases in COLUMN_ALIASES.items():
            if field not in column_index and key in aliases:
                column_index[field] = idx
    missing = [f for f in REQUIRED_FIELDS if f not in column_index]
    if missing:
        raise OdsParseError(
            f"Could not find required column(s) {missing} in header {header_cells!r} - "
            "the spreadsheet schema may have changed."
        )
    return column_index


def normalize_ddmmyyyy(raw):
    """Parse a strict DD/MM/YYYY date string into ISO 'YYYY-MM-DD'. Returns None if it doesn't match."""
    m = _DDMMYYYY_RE.match((raw or "").strip())
    if not m:
        return None
    day, month, year = m.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


def parse_contact_date(raw):
    """
    Parse the polymorphic "Date last contact" field: either a DD/MM/YYYY
    date, or free text like "Transmitting June 2026" for currently-alive
    birds (observed live in the real June 2026 file - not just a hypothetical
    edge case). Returns {"date": iso_str_or_None, "is_exact_date": bool, "raw": str}.
    """
    text = (raw or "").strip()
    iso = normalize_ddmmyyyy(text)
    if iso:
        return {"date": iso, "is_exact_date": True, "raw": text}
    m = _TRANSMITTING_RE.search(text)
    if m:
        month = _MONTHS.get(m.group(1).lower())
        if month:
            return {"date": f"{m.group(2)}-{month:02d}-01", "is_exact_date": False, "raw": text}
    return {"date": None, "is_exact_date": False, "raw": text}


def parse_tag_id(raw):
    """Split a raw Tag ID cell into (bird_id incl. any reuse-letter suffix, privately_funded flag)."""
    text = (raw or "").strip()
    m = _TAG_ID_RE.match(text)
    if not m:
        return {"bird_id": text, "privately_funded": False, "parse_ok": False}
    bird_id, funded_marker = m.groups()
    return {"bird_id": bird_id, "privately_funded": bool(funded_marker), "parse_ok": True}


def parse_status(raw):
    """Normalize the Status field, stripping the '^' footnote marker (see Notes legend in the source file)."""
    text = (raw or "").strip()
    footnotes = []
    base = text
    if base.endswith("^"):
        footnotes.append("^")
        base = base[:-1].strip()
    normalized = _STATUS_MAP.get(base.lower(), "unknown")
    return {"status_raw": text, "status": normalized, "status_footnotes": footnotes}


def build_bird_record(row_cells, column_index, row_index):
    def get(field):
        idx = column_index.get(field)
        if idx is None or idx >= len(row_cells):
            return ""
        return row_cells[idx]

    tag_id_info = parse_tag_id(get("tag_id"))
    contact_date_info = parse_contact_date(get("date_last_contact"))
    location_info = classify_location(get("os_reference"))
    status_info = parse_status(get("status"))

    coords = location_info["coordinates"]
    coordinates = None
    if coords is not None:
        coordinates = {"lat": coords["lat"], "lon": coords["lon"], "precision_m": location_info["precision_m"]}

    return {
        "bird_id": tag_id_info["bird_id"],
        "tag_id_raw": get("tag_id"),
        "tag_id_parse_ok": tag_id_info["parse_ok"],
        "tag_type": get("tag_type"),
        "privately_funded": tag_id_info["privately_funded"],
        "sex": get("sex"),
        "nest_area": get("nest_area"),
        "name": get("name"),
        "date_fitted_raw": get("date_fitted"),
        "date_fitted": normalize_ddmmyyyy(get("date_fitted")),
        "date_last_contact_raw": contact_date_info["raw"],
        "date_last_contact": contact_date_info["date"],
        "date_last_contact_is_exact_date": contact_date_info["is_exact_date"],
        "location_text": get("location_text"),
        "location_raw": get("os_reference"),
        "location_status": location_info["location_status"],
        "coordinates": coordinates,
        "status_raw": status_info["status_raw"],
        "status": status_info["status"],
        "status_footnotes": status_info["status_footnotes"],
        "notes_on_loss": get("notes_on_loss"),
        "notes_on_life_history": get("notes_on_life_history"),
        "row_index_in_source": row_index,
    }


def parse_birds_from_ods(ods_path_or_fileobj):
    """
    Parse an NE hen harrier .ods file into a list of bird record dicts.
    Raises OdsParseError if required columns can't be located in the header.
    """
    header, data_rows = extract_table_rows(ods_path_or_fileobj)
    if not header:
        raise OdsParseError("No table/header row found in the .ods file - it may be empty or corrupt.")
    column_index = _map_columns(header)

    records = []
    for row_index, cells in enumerate(data_rows, start=1):
        tag_idx = column_index["tag_id"]
        tag_id_cell = cells[tag_idx] if tag_idx < len(cells) else ""
        if not tag_id_cell.strip():
            continue  # not a real bird row (stray formatting/blank row)
        records.append(build_bird_record(cells, column_index, row_index))
    return records
