"""SEC filing parser.

Pipeline: HTML in -> ParsedFiling out.

1. BeautifulSoup parses the HTML.
2. <table> elements are extracted into a separate list and removed from the tree.
3. The remaining tree is converted to plain text; whitespace is normalised.
4. A regex finds every "Item N[A-Z]?." header in the text.
5. For each Item number we keep the candidate section with the most content
   (this filters out table-of-contents entries, which only have a few
   characters between consecutive headers).
6. A SHA-256 hash of the cleaned text gives a stable idempotency key.
"""

import hashlib
import re
import warnings
from typing import Final

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from pydantic import BaseModel, Field

# SEC filings are iXBRL (XML with embedded HTML). The HTML parser still handles
# them, so silence the warning rather than switch parsers.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


_ITEM_TITLES: Final[dict[str, str]] = {
    "1": "Business",
    "1A": "Risk Factors",
    "1B": "Unresolved Staff Comments",
    "1C": "Cybersecurity",
    "2": "Properties",
    "3": "Legal Proceedings",
    "4": "Mine Safety Disclosures",
    "5": "Market for Registrant's Common Equity",
    "6": "Selected Financial Data",
    "7": "Management's Discussion and Analysis",
    "7A": "Quantitative and Qualitative Disclosures About Market Risk",
    "8": "Financial Statements and Supplementary Data",
    "9": "Changes in and Disagreements with Accountants",
    "9A": "Controls and Procedures",
    "9B": "Other Information",
    "10": "Directors, Executive Officers and Corporate Governance",
    "11": "Executive Compensation",
    "12": "Security Ownership",
    "13": "Certain Relationships and Related Transactions",
    "14": "Principal Accountant Fees and Services",
    "15": "Exhibits and Financial Statement Schedules",
}

# Matches headers like "Item 1.", "Item 1A.", "ITEM  7A:", "item 10."
_ITEM_RE: Final[re.Pattern[str]] = re.compile(
    r"\bitem\s+(\d{1,2}[a-z]?)\s*[.:]",
    re.IGNORECASE,
)

# Minimum char count for a candidate to count as a real section vs a TOC entry.
_MIN_SECTION_LENGTH: Final[int] = 500


class ParsedTable(BaseModel):
    """A table extracted from a filing's HTML."""

    rows: list[list[str]] = Field(default_factory=list, description="Cells, row by row.")
    text: str = Field(..., description="Plain-text rendering for fallback retrieval.")


class ParsedSection(BaseModel):
    """A single Item section from a filing."""

    item_number: str = Field(..., description='Item identifier, e.g. "1", "1A", "7A".')
    title: str = Field(..., description="Human-readable section title.")
    text: str = Field(..., description="Plain-text content of the section.")


class ParsedFiling(BaseModel):
    """A filing after parsing — sections + tables + content hash."""

    accession_number: str
    form: str
    content_hash: str = Field(..., description="SHA-256 of cleaned text. Idempotency key.")
    sections: list[ParsedSection] = Field(default_factory=list)
    tables: list[ParsedTable] = Field(default_factory=list)


def _normalise_whitespace(text: str) -> str:
    """Replace non-breaking spaces and collapse runs of whitespace."""
    text = text.replace(" ", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_tables(soup: BeautifulSoup) -> list[ParsedTable]:
    """Pop every <table> out of the soup, returning them as ParsedTable objects."""
    tables: list[ParsedTable] = []
    for table_tag in soup.find_all("table"):
        rows: list[list[str]] = []
        for tr in table_tag.find_all("tr"):
            cells = [_normalise_whitespace(td.get_text(" ")) for td in tr.find_all(["td", "th"])]
            if any(cells):
                rows.append(cells)
        if rows:
            text = "\n".join(" | ".join(cells) for cells in rows)
            tables.append(ParsedTable(rows=rows, text=text))
        table_tag.decompose()
    return tables


def _html_to_text(html: str) -> tuple[str, list[ParsedTable]]:
    """Convert HTML to plain text and pull tables aside. Returns (text, tables)."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    tables = _extract_tables(soup)
    text = soup.get_text(" ")
    return _normalise_whitespace(text), tables


def _item_sort_key(item_number: str) -> tuple[int, str]:
    """Natural sort: '2' before '10', '1A' after '1'."""
    match = re.match(r"(\d+)([a-z]*)", item_number, re.IGNORECASE)
    if not match:
        return (999, item_number)
    return (int(match.group(1)), match.group(2).upper())


def _find_item_sections(text: str) -> list[ParsedSection]:
    """Find Item N sections; keep the largest candidate per item number."""
    matches = list(_ITEM_RE.finditer(text))
    if not matches:
        return []

    best_for_item: dict[str, tuple[int, int]] = {}
    for i, match in enumerate(matches):
        item_number = match.group(1).upper()
        section_start = match.end()
        section_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        length = section_end - section_start
        if length < _MIN_SECTION_LENGTH:
            continue
        prev = best_for_item.get(item_number)
        if prev is None or (prev[1] - prev[0]) < length:
            best_for_item[item_number] = (section_start, section_end)

    sections: list[ParsedSection] = []
    for item_number in sorted(best_for_item, key=_item_sort_key):
        start, end = best_for_item[item_number]
        section_text = text[start:end].strip()
        title = _ITEM_TITLES.get(item_number, f"Item {item_number}")
        sections.append(ParsedSection(item_number=item_number, title=title, text=section_text))

    return sections


def parse_filing(html: str, *, accession_number: str, form: str) -> ParsedFiling:
    """Parse a filing's HTML into structured sections + tables + a content hash."""
    text, tables = _html_to_text(html)
    sections = _find_item_sections(text)
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return ParsedFiling(
        accession_number=accession_number,
        form=form,
        content_hash=content_hash,
        sections=sections,
        tables=tables,
    )
