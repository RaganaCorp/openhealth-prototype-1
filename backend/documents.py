"""
Text extraction, chunking, patient.md I/O, document type/date detection.

Scan rules:
  - Top-level of uploads/ only (no subdirectories).
  - Supported extensions: .pdf, .tif, .tiff, .txt, .html, .json, .png, .jpg, .jpeg, .webp
  - Exclude *.extracted and *.facts.json files (cached extraction sidecars).

Image pages (standalone image files and image-only PDF/TIFF pages) are routed
through a multimodal model via ``vision_fn`` when one is supplied, falling back
to tesseract OCR otherwise.
"""

import asyncio
import html as html_module
import io
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import fitz  # PyMuPDF
import pytesseract
from PIL import Image, ImageSequence

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
SUPPORTED_EXTENSIONS = {".pdf", ".tif", ".tiff", ".txt", ".html", ".json"} | IMAGE_EXTENSIONS

# Callable that turns an image (PNG/JPEG bytes) into transcribed text.
VisionFn = Callable[[bytes], str]


# ---------------------------------------------------------------------------
# File scanning
# ---------------------------------------------------------------------------

def _is_sidecar(name: str) -> bool:
    # Cached extraction artifacts live alongside the source files in uploads/.
    # `.facts.json` ends in a supported extension (.json), so it must be excluded
    # explicitly or it would be re-ingested as its own document.
    return name.endswith(".extracted") or name.endswith(".facts.json")


def scan_uploads(uploads_dir: Path) -> list[Path]:
    """Return top-level supported files, excluding cached extraction sidecars."""
    if not uploads_dir.exists():
        return []
    return [
        f
        for f in uploads_dir.iterdir()
        if f.is_file()
        and f.suffix.lower() in SUPPORTED_EXTENSIONS
        and not _is_sidecar(f.name)
    ]


# ---------------------------------------------------------------------------
# Text extraction (sync — call via asyncio.to_thread from async context)
# ---------------------------------------------------------------------------

def extract_text_sync(
    file_path: Path,
    extracted_path: Path,
    llm_json_fn: Optional[Callable[[str], str]] = None,
    vision_fn: Optional[VisionFn] = None,
) -> str:
    """
    Return extracted text for a file.
    Reads from .extracted sidecar cache if present.
    Writes sidecar after extraction (overwrites if file changed).
    """
    if extracted_path.exists():
        return extracted_path.read_text(encoding="utf-8")

    text = _extract_raw(file_path, llm_json_fn, vision_fn)
    extracted_path.write_text(text, encoding="utf-8")
    return text


def overwrite_extracted_sidecar(
    file_path: Path,
    extracted_path: Path,
    llm_json_fn: Optional[Callable[[str], str]] = None,
    vision_fn: Optional[VisionFn] = None,
) -> str:
    """Re-extract and overwrite the sidecar (used when file has changed)."""
    text = _extract_raw(file_path, llm_json_fn, vision_fn)
    extracted_path.write_text(text, encoding="utf-8")
    return text


def _extract_raw(
    file_path: Path,
    llm_json_fn: Optional[Callable[[str], str]] = None,
    vision_fn: Optional[VisionFn] = None,
) -> str:
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(file_path, vision_fn)
    if suffix in (".tif", ".tiff"):
        return _extract_tiff(file_path, vision_fn)
    if suffix in IMAGE_EXTENSIONS:
        return _extract_image(file_path, vision_fn)
    if suffix == ".txt":
        return file_path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".html":
        return _extract_html(file_path)
    if suffix == ".json":
        return _extract_json(file_path, llm_json_fn)
    return ""


# Cap the longest edge of an image before sending it to the vision model. Scanned
# pages are often far larger than the model's internal input resolution, so the
# extra pixels only slow inference (and risk timeouts) without improving the
# transcription.
_VISION_MAX_EDGE = 2048

# High-bit-depth modes whose values span beyond 0-255. PIL's direct convert to
# "L"/"RGB" clips instead of rescaling, which washes a 16-bit scanned document out
# to near-white and makes it unreadable (to OCR and to the vision model alike).
_HIGH_BIT_MODES = {"I", "I;16", "I;16B", "I;16L", "I;16N", "F"}


def _to_8bit(img: Image.Image) -> Image.Image:
    """Normalize a high-bit-depth image to 8-bit grayscale by linearly stretching
    its actual value range to 0-255. 8-bit images pass through unchanged."""
    if img.mode not in _HIGH_BIT_MODES:
        return img
    src = img.convert("I")
    lo, hi = src.getextrema()
    if hi > lo:
        # point() on mode "I" only accepts a linear scale+offset expression, so
        # express the stretch as (v - lo) * scale without any wrapping calls.
        scale = 255.0 / (hi - lo)
        src = src.point(lambda v: (v - lo) * scale)
    return src.convert("L")


def _encode_for_vision(img: Image.Image) -> bytes:
    rgb = _to_8bit(img).convert("RGB")
    longest = max(rgb.size)
    if longest > _VISION_MAX_EDGE:
        scale = _VISION_MAX_EDGE / longest
        rgb = rgb.resize((max(1, round(rgb.width * scale)), max(1, round(rgb.height * scale))))
    buf = io.BytesIO()
    rgb.save(buf, format="PNG")
    return buf.getvalue()


def _collapse_repeats(text: str, max_period: int = 16, min_repeats: int = 4) -> str:
    """Collapse runaway repetition to a single copy: a block of 1..max_period
    consecutive lines repeated >= min_repeats times is reduced to one occurrence.
    Vision transcription of small models can degenerate into a loop — sometimes a
    single line, sometimes a multi-line section block (the observed case was an
    8-line FINDINGS/COMPARISON/TECHNIQUE/IMPRESSION cycle) — so the period window
    must be comfortably larger than a section block to catch it."""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        collapsed = False
        for period in range(1, max_period + 1):
            block = lines[i:i + period]
            if len(block) < period:
                break
            reps = 1
            j = i + period
            while j + period <= n and lines[j:j + period] == block:
                reps += 1
                j += period
            if reps >= min_repeats:
                out.extend(block)
                i = j
                collapsed = True
                break
        if not collapsed:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def _transcribe(vision_fn: VisionFn, image_bytes: bytes) -> str:
    """Run vision transcription and strip any runaway repetition from the result."""
    return _collapse_repeats(vision_fn(image_bytes))


def _extract_pdf(file_path: Path, vision_fn: Optional[VisionFn] = None) -> str:
    doc = fitz.open(str(file_path))
    pages: list[str] = []
    for page in doc:
        # Page triage: pages with a real text layer are read directly (no model);
        # only image-only pages go to vision/OCR.
        text = page.get_text().strip()
        if text:
            pages.append(text)
        elif vision_fn is not None:
            pix = page.get_pixmap(dpi=200)
            pages.append(_transcribe(vision_fn, pix.tobytes("png")))
        else:
            tp = page.get_textpage_ocr()
            pages.append(tp.extractText())
    doc.close()
    return "\n\n".join(pages)


def _extract_tiff(file_path: Path, vision_fn: Optional[VisionFn] = None) -> str:
    img = Image.open(str(file_path))
    pages: list[str] = []
    for frame in ImageSequence.Iterator(img):
        if vision_fn is not None:
            pages.append(_transcribe(vision_fn, _encode_for_vision(frame)))
        else:
            pages.append(pytesseract.image_to_string(_to_8bit(frame)))
    return "\n\n".join(pages)


def _extract_image(file_path: Path, vision_fn: Optional[VisionFn] = None) -> str:
    img = Image.open(file_path)
    if vision_fn is not None:
        return _transcribe(vision_fn, _encode_for_vision(img))
    return pytesseract.image_to_string(_to_8bit(img))


# Block-level tags whose boundaries become line breaks, so the document's
# structure (sections, table rows, list items) survives tag stripping and gives
# the chunker something meaningful to split on.
_HTML_BLOCK_BOUNDARY_RE = re.compile(
    r"(?i)<\s*(?:br|/p|/div|/li|/tr|/h[1-6]|/table|/ul|/ol|/section)\b[^>]*>"
)


def _extract_html(file_path: Path) -> str:
    raw = file_path.read_text(encoding="utf-8", errors="replace")
    # Remove non-clinical payloads that frequently dominate CCDA/portal exports.
    raw = re.sub(r"<style\b[^>]*>[\s\S]*?</style>", " ", raw, flags=re.IGNORECASE)
    raw = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", " ", raw, flags=re.IGNORECASE)
    raw = re.sub(r"<!--([\s\S]*?)-->", " ", raw)
    # Turn block-element boundaries into newlines before stripping the rest.
    raw = _HTML_BLOCK_BOUNDARY_RE.sub("\n", raw)
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html_module.unescape(text)
    # Collapse runs of spaces/tabs within each line, but keep newlines; drop blanks.
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def _extract_json(
    file_path: Path,
    llm_json_fn: Optional[Callable[[str], str]] = None,
) -> str:
    raw = file_path.read_text(encoding="utf-8", errors="replace")
    try:
        parsed = json.loads(raw)
        serialized = json.dumps(parsed, indent=2)
    except json.JSONDecodeError:
        serialized = raw

    if llm_json_fn:
        return llm_json_fn(serialized)
    return serialized


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def _char_split(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Overlapping fixed-size character windows — the fallback for a single unit
    too large to keep whole."""
    step = max(chunk_size - chunk_overlap, 1)
    return [text[i : i + chunk_size] for i in range(0, len(text), step)]


def chunk_text(text: str, chunk_size: int = 900, chunk_overlap: int = 100) -> list[str]:
    """Structure-aware chunking: pack whole lines/paragraphs into chunks up to
    chunk_size rather than cutting at arbitrary character offsets, so a lab line,
    medication, or section heading stays intact. A single unit larger than
    chunk_size falls back to character windows; chunk_overlap carries a tail of the
    previous chunk into the next so context spanning a boundary isn't lost."""
    if not text.strip():
        return []

    units = [u for u in (line.strip() for line in text.split("\n")) if u]
    chunks: list[str] = []
    current = ""

    for unit in units:
        if len(unit) > chunk_size:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_char_split(unit, chunk_size, chunk_overlap))
            continue

        candidate = f"{current}\n{unit}" if current else unit
        if len(candidate) > chunk_size and current:
            chunks.append(current)
            tail = current[-chunk_overlap:] if chunk_overlap > 0 else ""
            current = f"{tail}\n{unit}" if tail else unit
        else:
            current = candidate

    if current.strip():
        chunks.append(current)
    return chunks


# ---------------------------------------------------------------------------
# Document metadata detection
# ---------------------------------------------------------------------------

def detect_document_type(filename: str, text: str) -> str:
    combined = (filename + " " + text[:500]).lower()
    if any(w in combined for w in ["lab", "result", "panel", "blood", "urine", "culture", "pathology"]):
        return "lab result"
    if any(w in combined for w in ["discharge", "hospital", "admission"]):
        return "discharge summary"
    if any(w in combined for w in ["imaging", "radiology", "mri", "ct scan", "x-ray", "ultrasound", "xray"]):
        return "imaging"
    if any(w in combined for w in ["prescription", "rx ", "pharmacy", "dispense"]):
        return "prescription"
    if any(w in combined for w in ["clinical note", "progress note", "soap", "assessment", "encounter"]):
        return "clinical note"
    if any(w in combined for w in ["medication", "drug", "dosage"]):
        return "prescription"
    return "unknown"


_DATE_PATTERNS = [
    r"\b(\d{4}-\d{2}-\d{2})\b",
    r"\b(\d{1,2}/\d{1,2}/\d{4})\b",
    r"\b((?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2},?\s+\d{4})\b",
]


def _normalize_date(raw: str) -> str:
    """Convert a detected date string to ISO (YYYY-MM-DD) so chronological
    sorting works across mixed source formats. Falls back to the raw string."""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%B %d %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def date_sort_key(date: Optional[str]) -> str:
    """Chronological sort key for a possibly mixed-format or unknown date.

    Returns an ISO 'YYYY-MM-DD' string when the date parses, else '' so that
    unknown/unparseable dates sort *oldest* (before any real date). Comparing
    detected dates as raw strings is unsafe: stored values mix 'MM/DD/YYYY' and
    ISO, so '04/20/2021' > '2026-03-19' lexically, and the literal 'Unknown'
    (capital U) outranks every digit — which made unknown-dated docs look newest.
    Normalize through this key wherever dates are compared or sorted."""
    iso = _normalize_date((date or "").strip())
    return iso if _ISO_DATE_RE.fullmatch(iso) else ""


def detect_date(text: str, patient_dob: Optional[str] = None) -> Optional[str]:
    """Return the first recognisable date in the first 2000 chars, normalised to
    ISO. Skips any date matching the patient's DOB — reports routinely print the
    DOB above the service date, so the naive "first date" would misattribute it."""
    dob = _normalize_date(patient_dob) if patient_dob else None
    window = text[:2000]
    for pattern in _DATE_PATTERNS:
        for m in re.finditer(pattern, window, re.IGNORECASE):
            normalized = _normalize_date(m.group(0))
            if dob and normalized == dob:
                continue
            return normalized
    return None


# ---------------------------------------------------------------------------
# patient.md writer
# ---------------------------------------------------------------------------

def _atomic_write_text(path: Path, text: str) -> None:
    """Write text via a unique temp file + atomic rename so a concurrent reader
    (e.g. the chat pipeline reading patient.md) never sees a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def write_patient_md(patient_folder: Path, docs: list[dict]) -> None:
    """
    Write patient.md from a list of document dicts:
      { filename, document_type, date_detected, text }
    Documents are assumed to be pre-sorted by date_detected.
    """
    sep = "=" * 80
    half_sep = "=" * 16
    lines: list[str] = []
    for doc in docs:
        lines.append(sep)
        lines.append(f"DOCUMENT: {doc['filename']}")
        lines.append(f"TYPE: {doc['document_type']}")
        lines.append(f"DATE DETECTED: {doc.get('date_detected') or 'unknown'}")
        lines.append(sep)
        lines.append("")
        lines.append(doc["text"])
        lines.append("")
        lines.append(f"{half_sep} END: {doc['filename']} {half_sep}")
        lines.append("")
        lines.append("")
    _atomic_write_text(patient_folder / "patient.md", "\n".join(lines))


def read_patient_md(patient_folder: Path) -> str:
    f = patient_folder / "patient.md"
    if f.exists():
        return f.read_text(encoding="utf-8")
    return ""
