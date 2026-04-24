"""
pdf/extractor.py — Field extraction from raw TN811 ticket PDF text.

Takes the full_text from pdf/parser.py and extracts structured fields
using regex patterns matched against TN811's standard ticket format.

ASSUMPTION: TN811 ticket PDFs follow a consistent labeled-field format:
    Ticket Number: TN20240101-123456
    County: Davidson
    Call Date: 01/01/2024
    ...

If the format differs, add a new extraction strategy below and register it
in _EXTRACTION_STRATEGIES. The first strategy that returns a non-empty
result wins. All strategies are tested against PDF fixture golden files in
tests/fixtures/pdf/.

See docs/parser_assumptions.md for the assumed PDF layout and validation
procedure for verifying these patterns against live TN811 PDFs.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date

logger = logging.getLogger(__name__)


# ── Date parsing helpers ──────────────────────────────────────────────────────

_DATE_PATTERNS = [
    # MM/DD/YYYY
    re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})"),
    # YYYY-MM-DD
    re.compile(r"(\d{4})-(\d{2})-(\d{2})"),
    # MM/DD/YY (portal "Work To Begin" field uses 2-digit year: "04/08/26 AT 9:30 AM")
    re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2})(?!\d)"),
    # Month DD, YYYY
    re.compile(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})"),
]

_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def parse_date(raw: str) -> date | None:
    """Try to parse a date string in common formats."""
    raw = raw.strip()
    for pattern in _DATE_PATTERNS:
        m = pattern.search(raw)
        if not m:
            continue
        g = m.groups()
        try:
            if not g[0].isdigit():  # Month DD, YYYY (e.g. "June 1, 2024")
                month = _MONTH_NAMES.get(g[0].lower())
                if month:
                    return date(int(g[2]), month, int(g[1]))
            elif len(g[0]) == 4:  # YYYY-MM-DD
                return date(int(g[0]), int(g[1]), int(g[2]))
            else:  # MM/DD/YYYY or MM/DD/YY
                year = int(g[2])
                if year < 100:  # 2-digit year → assume 2000s
                    year += 2000
                return date(year, int(g[0]), int(g[1]))
        except ValueError:
            continue
    return None


# ── Field extraction ──────────────────────────────────────────────────────────

@dataclass
class ExtractedFields:
    """Structured fields extracted from PDF text."""
    ticket_number: str | None = None
    county: str | None = None
    call_date: date | None = None
    legal_start_date: date | None = None
    expiration_date: date | None = None
    excavator_name: str | None = None
    caller_name: str | None = None
    caller_phone: str | None = None
    work_type: str | None = None
    location_text: str | None = None
    remarks: str | None = None
    done_for: str | None = None
    utility_references: list[str] = field(default_factory=list)
    is_cancelled: bool = False
    warnings: list[str] = field(default_factory=list)
    extraction_method: str = ""


# Label → regex for "Label: Value" style PDFs
# Each entry: (canonical_field_name, list_of_label_patterns)
_LABEL_PATTERNS: list[tuple[str, list[str]]] = [
    ("ticket_number", [
        r"ticket\s*(?:number|#|no\.?)\s*[:\-]\s*(\S+)",
        r"locate\s*request\s*#?\s*[:\-]\s*(\S+)",
    ]),
    ("county", [
        r"county\s*[:\-]\s*([^\n]+)",
    ]),
    ("call_date", [
        r"call\s*date\s*[:\-]\s*([^\n]+)",
        r"called\s*date\s*[:\-]\s*([^\n]+)",
        r"date\s*called\s*[:\-]\s*([^\n]+)",
    ]),
    ("legal_start_date", [
        r"legal\s*start(?:\s*date)?\s*[:\-]\s*([^\n]+)",
        r"start\s*date\s*[:\-]\s*([^\n]+)",
    ]),
    ("expiration_date", [
        r"expir(?:ation|es?)\s*(?:date)?\s*[:\-]\s*([^\n]+)",
        r"valid\s*until\s*[:\-]\s*([^\n]+)",
    ]),
    ("excavator_name", [
        r"excavat(?:or|ing\s*company)\s*[:\-]\s*([^\n]+)",
        r"company\s*[:\-]\s*([^\n]+)",
        r"contractor\s*[:\-]\s*([^\n]+)",
    ]),
    ("caller_name", [
        r"caller(?:\s*name)?\s*[:\-]\s*([^\n]+)",
        r"contact(?:\s*name)?\s*[:\-]\s*([^\n]+)",
    ]),
    ("caller_phone", [
        r"(?:caller\s*)?phone\s*[:\-]\s*([^\n]+)",
        r"telephone\s*[:\-]\s*([^\n]+)",
    ]),
    ("work_type", [
        r"(?:type\s*of\s*)?work(?:\s*type)?\s*[:\-]\s*([^\n]+)",
        r"work\s*description\s*[:\-]\s*([^\n]+)",
    ]),
    ("location_text", [
        r"location(?:\s*description)?\s*[:\-]\s*([^\n]+(?:\n(?!\w+\s*[:\-])[^\n]+)*)",
        r"address\s*[:\-]\s*([^\n]+)",
        r"street\s*[:\-]\s*([^\n]+)",
    ]),
    ("remarks", [
        r"remarks?\s*[:\-]\s*([^\n]+(?:\n(?!\w+\s*[:\-])[^\n]+)*)",
        r"comments?\s*[:\-]\s*([^\n]+(?:\n(?!\w+\s*[:\-])[^\n]+)*)",
        r"additional\s*(?:information|info)\s*[:\-]\s*([^\n]+(?:\n(?!\w+\s*[:\-])[^\n]+)*)",
        r"notes?\s*[:\-]\s*([^\n]+(?:\n(?!\w+\s*[:\-])[^\n]+)*)",
    ]),
    ("done_for", [
        r"done\s*for\s*[:\-]\s*([^\n]+)",
        r"work\s*(?:done\s*)?for\s*[:\-]\s*([^\n]+)",
        r"for\s*company\s*[:\-]\s*([^\n]+)",
    ]),
]

# Utility response patterns
_UTILITY_PATTERNS = [
    re.compile(r"utility[:\s]+([^\n]+)", re.IGNORECASE),
    re.compile(r"([A-Za-z\s&,]+(?:electric|gas|water|sewer|telecom|cable|fiber)[A-Za-z\s&,]*)\s*[:\-]", re.IGNORECASE),
]

# Cancellation signal patterns
_CANCEL_PATTERNS = [
    re.compile(r"\b(?:cancelled|canceled|void(?:ed)?)\b", re.IGNORECASE),
]


def extract_fields(text: str, county_hint: str = "") -> ExtractedFields:
    """
    Extract structured fields from raw PDF text.

    Uses label-based regex extraction (Strategy A). Falls back to
    positional extraction (Strategy B) if few fields are found.

    Args:
        text:        Full raw PDF text.
        county_hint: County name from context (used if not found in text).

    Returns:
        ExtractedFields with all matched values.
    """
    result = _extract_labeled(text)

    # If we got very few fields, try positional extraction as supplement
    found_count = sum(
        1 for f in [
            result.ticket_number, result.county, result.call_date,
            result.expiration_date, result.excavator_name,
        ]
        if f is not None
    )

    if found_count < 2:
        logger.debug("Labeled extraction found few fields; trying positional")
        positional = _extract_positional(text)
        # Merge: prefer labeled results, fill gaps with positional
        if not result.ticket_number and positional.ticket_number:
            result.ticket_number = positional.ticket_number
        if not result.county and positional.county:
            result.county = positional.county
        if not result.expiration_date and positional.expiration_date:
            result.expiration_date = positional.expiration_date
        result.warnings.append("Positional extraction used as supplement")

    # Apply county hint if still missing
    if not result.county and county_hint:
        result.county = county_hint

    # Extract utilities
    result.utility_references = _extract_utilities(text)

    # Check cancellation
    result.is_cancelled = any(p.search(text) for p in _CANCEL_PATTERNS)

    return result


def _extract_labeled(text: str) -> ExtractedFields:
    """Extract fields using label:value regex patterns."""
    result = ExtractedFields(extraction_method="labeled_regex")
    text_normalized = text.replace("\r\n", "\n").replace("\r", "\n")

    for field_name, patterns in _LABEL_PATTERNS:
        for pat_str in patterns:
            m = re.search(pat_str, text_normalized, re.IGNORECASE | re.MULTILINE)
            if not m:
                continue
            raw_value = m.group(1).strip()
            if not raw_value:
                continue

            # Date fields
            if field_name in ("call_date", "legal_start_date", "expiration_date"):
                parsed = parse_date(raw_value)
                if parsed:
                    setattr(result, field_name, parsed)
                break

            # String fields — truncate long multi-line matches
            value = " ".join(raw_value.split())[:500]
            setattr(result, field_name, value)
            break

    return result


def _extract_positional(text: str) -> ExtractedFields:
    """
    Positional extraction: look for TN ticket number pattern and dates
    in fixed positions within the text.

    This is a coarser fallback for PDFs that don't use labeled fields.
    """
    result = ExtractedFields(extraction_method="positional")

    # Ticket number: TN followed by digits and dashes
    tn_match = re.search(r"\bTN[\d\-]{8,}\b", text, re.IGNORECASE)
    if tn_match:
        result.ticket_number = tn_match.group().upper()

    # Dates: collect all date-like strings in order
    all_dates: list[date] = []
    for m in re.finditer(r"\d{1,2}/\d{1,2}/\d{4}", text):
        d = parse_date(m.group())
        if d:
            all_dates.append(d)

    # Heuristic: first date = call date, last date = expiration
    if len(all_dates) >= 1:
        result.call_date = all_dates[0]
    if len(all_dates) >= 2:
        result.expiration_date = all_dates[-1]

    return result


def _extract_utilities(text: str) -> list[str]:
    """Extract utility company/reference names from text."""
    utilities: list[str] = []
    seen: set[str] = set()

    for pattern in _UTILITY_PATTERNS:
        for m in pattern.finditer(text):
            util = m.group(1).strip()[:100]
            normalized = util.lower()
            if normalized not in seen and len(util) > 2:
                utilities.append(util)
                seen.add(normalized)

    return utilities[:20]  # cap at 20
