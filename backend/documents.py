"""
Text extraction, chunking, patient.md I/O, document type/date detection.

Scan rules:
  - Top-level of uploads/ only (no subdirectories).
  - Supported extensions: .pdf, .tif, .tiff, .txt, .html, .json
  - Exclude *.extracted files (cached extraction sidecars).
"""

import asyncio
import html as html_module
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import fitz  # PyMuPDF
import pytesseract
from PIL import Image

SUPPORTED_EXTENSIONS = {".pdf", ".tif", ".tiff", ".txt", ".html", ".json"}


# ---------------------------------------------------------------------------
# File scanning
# ---------------------------------------------------------------------------

def scan_uploads(uploads_dir: Path) -> list[Path]:
    """Return top-level supported files, excluding .extracted sidecars."""
    if not uploads_dir.exists():
        return []
    return [
        f
        for f in uploads_dir.iterdir()
        if f.is_file()
        and f.suffix.lower() in SUPPORTED_EXTENSIONS
        and not f.name.endswith(".extracted")
    ]


# ---------------------------------------------------------------------------
# Text extraction (sync — call via asyncio.to_thread from async context)
# ---------------------------------------------------------------------------

def extract_text_sync(
    file_path: Path,
    extracted_path: Path,
    llm_json_fn: Optional[Callable[[str], str]] = None,
) -> str:
    """
    Return extracted text for a file.
    Reads from .extracted sidecar cache if present.
    Writes sidecar after extraction (overwrites if file changed).
    """
    if extracted_path.exists():
        return extracted_path.read_text(encoding="utf-8")

    text = _extract_raw(file_path, llm_json_fn)
    extracted_path.write_text(text, encoding="utf-8")
    return text


def overwrite_extracted_sidecar(
    file_path: Path,
    extracted_path: Path,
    llm_json_fn: Optional[Callable[[str], str]] = None,
) -> str:
    """Re-extract and overwrite the sidecar (used when file has changed)."""
    text = _extract_raw(file_path, llm_json_fn)
    extracted_path.write_text(text, encoding="utf-8")
    return text


def _extract_raw(
    file_path: Path,
    llm_json_fn: Optional[Callable[[str], str]] = None,
) -> str:
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(file_path)
    if suffix in (".tif", ".tiff"):
        return _extract_tiff(file_path)
    if suffix == ".txt":
        return file_path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".html":
        return _extract_html(file_path)
    if suffix == ".json":
        return _extract_json(file_path, llm_json_fn)
    return ""


def _extract_pdf(file_path: Path) -> str:
    doc = fitz.open(str(file_path))
    pages: list[str] = []
    for page in doc:
        text = page.get_text().strip()
        if text:
            pages.append(text)
        else:
            # OCR fallback for scanned/image pages.
            tp = page.get_textpage_ocr()
            pages.append(tp.extractText())
    doc.close()
    return "\n\n".join(pages)


def _extract_tiff(file_path: Path) -> str:
    img = Image.open(str(file_path))
    return pytesseract.image_to_string(img)


def _extract_html(file_path: Path) -> str:
    raw = file_path.read_text(encoding="utf-8", errors="replace")
    # Remove non-clinical payloads that frequently dominate CCDA/portal exports.
    raw = re.sub(r"<style\b[^>]*>[\s\S]*?</style>", " ", raw, flags=re.IGNORECASE)
    raw = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", " ", raw, flags=re.IGNORECASE)
    raw = re.sub(r"<!--([\s\S]*?)-->", " ", raw)
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html_module.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


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

def chunk_text(text: str, chunk_size: int = 900, chunk_overlap: int = 100) -> list[str]:
    """Split text into overlapping character-count chunks."""
    if not text.strip():
        return []
    chunks: list[str] = []
    start = 0
    step = max(chunk_size - chunk_overlap, 1)
    while start < len(text):
        chunks.append(text[start : start + chunk_size])
        start += step
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


def detect_date(text: str) -> Optional[str]:
    """Return the first recognisable date in the first 2000 chars, normalised to ISO."""
    for pattern in _DATE_PATTERNS:
        m = re.search(pattern, text[:2000], re.IGNORECASE)
        if m:
            return _normalize_date(m.group(0))
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
