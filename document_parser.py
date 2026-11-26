"""Deep extraction of delivery terms from HTML sections and PDF/DOCX attachments."""

import io
import logging
import re
from typing import Iterable

from docx import Document
from pypdf import PdfReader

logger = logging.getLogger(__name__)

FALLBACK_DELIVERY_TEXT = "იხილეთ სატენდერო დოკუმენტაცია"

MISSING_DELIVERY_MARKERS = (
    "სატენდერო დოკუმენტაცი",
    "იხ.",
    "იხილეთ",
    "დანართ",
    "თანდართულ",
    "დოკუმენტაცია",
    "ტექნიკურ დავალება",
)

PRIMARY_KEYWORDS = (
    "მიწოდების ვადა",
    "მოწოდების ვადა",
    "შესრულების ვადა",
    "გაწევის ვადა",
    "მიწოდება უნდა განხორციელდეს",
    "მიწოდება უნდა უზრუნველყოს",
    "მიწოდებულ უნდა იქნას",
    "მიწოდებულ იქნეს",
)

TIMEFRAME_RE = re.compile(
    r"(?:\d+|ერთი|ორი|სამი|ოთხი|ხუთი|ათი|ოცი|ოცდაათი|ორმოცი|სამოცი|ოთხმოცი|ასი)"
    r".{0,25}(?:კალენდარულ|სამუშაო|დღე|დღის|თვე|თვის|კვირ|წლის|დეკემბ|იანვ|თებერვ|მარტ|აპრილ|მაის|ივნის|ივლის|აგვისტ|სექტემბ|ოქტომბ|ნოემბ)"
    r"|არაუგვიანეს\s+\d{1,4}",
    re.IGNORECASE,
)


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def is_delivery_missing_or_redirect(value: str) -> bool:
    """Return True if the summary delivery field is empty or just points to documentation."""
    cleaned = normalize_whitespace(value).lower()
    if not cleaned or cleaned in {"-", "—", "n/a", "არ არის მითითებული"}:
        return True
    if any(marker in cleaned for marker in MISSING_DELIVERY_MARKERS):
        # Some summaries say "90 დღე, იხ. დოკუმენტაცია" — only treat as missing if no timeframe is present
        if not TIMEFRAME_RE.search(cleaned):
            return True
    return False


def _clean_snippet(snippet: str, max_len: int = 280) -> str:
    text = normalize_whitespace(snippet)
    text = re.sub(r"^[\d\.\)\-\–\:\s]+", "", text).strip()
    if len(text) <= max_len:
        return text
    cut = text[:max_len].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + "..."


def extract_delivery_from_text(text: str) -> str | None:
    """Search document or section text for explicit delivery deadline statements."""
    if not text:
        return None

    lines = [normalize_whitespace(line) for line in re.split(r"[\r\n]+", text) if normalize_whitespace(line)]
    joined = "\n".join(lines)

    # Pass 1: Look for lines containing primary delivery/execution deadline keywords
    for idx, line in enumerate(lines):
        low = line.lower()
        if any(kw in low for kw in PRIMARY_KEYWORDS):
            # Combine with next line if timeframe is on the following line
            candidate = line
            if not TIMEFRAME_RE.search(candidate) and idx + 1 < len(lines):
                candidate = f"{line} {lines[idx + 1]}"
            if is_delivery_missing_or_redirect(candidate):
                continue
            if TIMEFRAME_RE.search(candidate):
                return _clean_snippet(candidate)

    # Pass 2: Regex match around "ვადა" with timeframe context
    for m in re.finditer(r"([^.\n]{0,60}ვადა[^.\n]{0,180})", joined, re.IGNORECASE):
        snippet = normalize_whitespace(m.group(1))
        low = snippet.lower()
        # Exclude bid validity / warranty / payment terms
        if any(skip in low for skip in ("საგარანტიო", "გარანტიის", "ანგარიშსწორებ", "წინადადების", "მოქმედების ვადა")):
            continue
        if not is_delivery_missing_or_redirect(snippet) and TIMEFRAME_RE.search(snippet):
            return _clean_snippet(snippet)

    return None


def extract_text_from_pdf(content: bytes) -> str:
    """Extract text from PDF bytes using pypdf."""
    reader = PdfReader(io.BytesIO(content))
    chunks: list[str] = []
    for page in reader.pages[:25]:
        extracted = page.extract_text() or ""
        if extracted.strip():
            chunks.append(extracted)
    return "\n".join(chunks)


def extract_text_from_docx(content: bytes) -> str:
    """Extract text from DOCX paragraphs and tables using python-docx."""
    doc = Document(io.BytesIO(content))
    parts: list[str] = []
    for para in doc.paragraphs:
        if para.text.strip():
            parts.append(para.text)
    for table in doc.tables:
        for row in table.rows:
            row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
            if row_text:
                parts.append(row_text)
    return "\n".join(parts)


def _file_priority(filename: str) -> tuple[int, int]:
    low = filename.lower()
    ext_score = 0 if low.endswith(".pdf") else (1 if low.endswith(".docx") else 9)
    kw_score = 0 if any(k in low for k in ("ვადა", "ტექნიკ", "დავალებ", "განცხადებ", "პირობ", "ხელშეკრულ")) else 1
    return (ext_score, kw_score)


def extract_delivery_from_attachments(
    fetch_bytes_fn,
    attachments: Iterable[tuple[str, str]],
    max_files: int = 5,
) -> str | None:
    """Download and parse PDF/DOCX attachments to locate delivery deadlines."""
    parseable = [
        (name, url)
        for name, url in attachments
        if name.lower().endswith((".pdf", ".docx"))
    ]
    parseable.sort(key=lambda item: _file_priority(item[0]))

    for name, url in parseable[:max_files]:
        try:
            raw = fetch_bytes_fn(url)
            if not raw:
                continue
            low = name.lower()
            if low.endswith(".pdf"):
                text = extract_text_from_pdf(raw)
            elif low.endswith(".docx"):
                text = extract_text_from_docx(raw)
            else:
                continue
            found = extract_delivery_from_text(text)
            if found:
                logger.info("Extracted delivery terms from attachment '%s'", name)
                return found
        except Exception as exc:
            logger.warning("Failed to parse attachment '%s': %s", name, exc)
    return None
