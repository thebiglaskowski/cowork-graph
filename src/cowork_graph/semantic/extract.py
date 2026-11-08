"""Text extraction per file kind (plan.md Phase 8, decision 4).

Each extractor returns Sections — text with an optional heading breadcrumb
and page number — which chunking.py splits into embedding-sized chunks.
Failures raise; sync.py records them on the file row instead of silently
indexing half a document (fail loud).
"""

from __future__ import annotations

import csv
import io
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import frontmatter

# Tesseract on a page is only worth it when the PDF text layer is this thin
# (ported from cowork-rag-chat: under 30 characters means a scan).
OCR_MIN_TEXT = 30
OCR_DPI = 200
OCR_TIMEOUT = 180
# OCR on photos mostly yields noise. Keep image text only when it looks like
# writing: enough real words, and mostly letters.
_WORD = re.compile(r"[A-Za-z]{3,}")


class ExtractionError(RuntimeError):
    pass


@dataclass
class Section:
    text: str
    heading: str | None = None
    page: int | None = None


@dataclass
class Extracted:
    sections: list[Section]
    ocr_pages: int = 0

    @property
    def text(self) -> str:
        parts = []
        for s in self.sections:
            prefix = f"[page {s.page}] " if s.page else ""
            head = f"{s.heading}\n" if s.heading else ""
            parts.append(f"{prefix}{head}{s.text}".strip())
        return "\n\n".join(p for p in parts if p)


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def _tesseract(image: bytes | Path) -> str:
    if not tesseract_available():
        raise ExtractionError("tesseract is not installed (pacman -S tesseract tesseract-data-eng)")
    src = str(image) if isinstance(image, Path) else "stdin"
    try:
        proc = subprocess.run(  # fixed argv, no shell
            ["tesseract", src, "stdout", "-l", "eng", "--psm", "3"],
            input=None if isinstance(image, Path) else image,
            capture_output=True,
            timeout=OCR_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ExtractionError(f"tesseract timed out after {OCR_TIMEOUT}s") from exc
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ExtractionError(f"tesseract failed: {err[-1] if err else proc.returncode}")
    return proc.stdout.decode("utf-8", "replace")


def looks_like_text(text: str) -> bool:
    words = _WORD.findall(text)
    if len(words) < 5:
        return False
    letters = sum(c.isalpha() for c in text)
    visible = sum(not c.isspace() for c in text) or 1
    return letters / visible >= 0.6


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def extract_markdown(raw: str) -> Extracted:
    """Split on headings, carrying an 'H1 > H2 > H3' breadcrumb per section."""
    try:
        body = frontmatter.loads(raw).content
    except Exception:  # noqa: BLE001 — malformed frontmatter: index the raw text
        body = raw
    stack: list[tuple[int, str]] = []
    sections: list[Section] = []
    buf: list[str] = []
    in_fence = False

    def flush() -> None:
        text = "\n".join(buf).strip()
        if text:
            crumb = " > ".join(h for _, h in stack) or None
            sections.append(Section(text=text, heading=crumb))
        buf.clear()

    for line in body.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        m = None if in_fence else _HEADING.match(line)
        if m:
            flush()
            level = len(m.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, m.group(2).strip()))
        else:
            buf.append(line)
    flush()
    if not sections and stack:
        # Heading-only doc: still searchable by its titles.
        sections.append(Section(text=" > ".join(h for _, h in stack)))
    return Extracted(sections)


# ---------------------------------------------------------------------------
# PDF, images, office, plain text
# ---------------------------------------------------------------------------


def extract_pdf(path: Path, *, ocr: bool) -> Extracted:
    import pymupdf

    sections: list[Section] = []
    ocr_pages = 0
    try:
        doc = pymupdf.open(path)
    except Exception as exc:
        raise ExtractionError(f"cannot open PDF: {exc}") from exc
    with doc:
        if doc.needs_pass:
            raise ExtractionError("PDF is password-protected")
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text") or ""
            if len(text.strip()) < OCR_MIN_TEXT and ocr:
                png = page.get_pixmap(dpi=OCR_DPI).tobytes("png")
                ocr_text = _tesseract(png)
                ocr_pages += 1
                if len(ocr_text.strip()) > len(text.strip()):
                    text = ocr_text
            if text.strip():
                sections.append(Section(text=text.strip(), page=i))
    return Extracted(sections, ocr_pages=ocr_pages)


def extract_image(path: Path, *, ocr: bool) -> Extracted:
    if not ocr:
        return Extracted([])
    text = _tesseract(path)
    if not looks_like_text(text):
        return Extracted([], ocr_pages=1)
    return Extracted([Section(text=text.strip())], ocr_pages=1)


def extract_docx(path: Path) -> Extracted:
    import docx

    d = docx.Document(str(path))
    sections: list[Section] = []
    heading: str | None = None
    buf: list[str] = []

    def flush() -> None:
        if buf:
            sections.append(Section(text="\n".join(buf), heading=heading))
            buf.clear()

    for para in d.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        if (para.style is not None and para.style.name or "").lower().startswith("heading"):
            flush()
            heading = text
        else:
            buf.append(text)
    flush()
    for t, table in enumerate(d.tables, start=1):
        rows = [" | ".join(c.text.strip() for c in row.cells) for row in table.rows]
        rows = [r for r in rows if r.strip(" |")]
        if rows:
            sections.append(Section(text="\n".join(rows), heading=f"Table {t}"))
    return Extracted(sections)


def extract_xlsx(path: Path) -> Extracted:
    import openpyxl

    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    sections: list[Section] = []
    try:
        for ws in wb.worksheets:
            rows = []
            for row in ws.iter_rows(values_only=True):
                cells = ["" if v is None else str(v) for v in row]
                if any(c.strip() for c in cells):
                    rows.append(" | ".join(cells).rstrip(" |"))
            if rows:
                sections.append(Section(text="\n".join(rows), heading=ws.title))
    finally:
        wb.close()
    return Extracted(sections)


def extract_csv(raw: str) -> Extracted:
    rows = [" | ".join(r) for r in csv.reader(io.StringIO(raw)) if any(c.strip() for c in r)]
    return Extracted([Section(text="\n".join(rows))] if rows else [])


def extract(path: Path, kind: str, *, ocr: bool) -> Extracted:
    suffix = path.suffix.lower()
    if kind == "md":
        return extract_markdown(path.read_text("utf-8", errors="replace"))
    if kind == "pdf":
        return extract_pdf(path, ocr=ocr)
    if kind == "image":
        return extract_image(path, ocr=ocr)
    if suffix == ".docx":
        return extract_docx(path)
    if suffix == ".xlsx":
        return extract_xlsx(path)
    raw = path.read_text("utf-8", errors="replace")
    if suffix == ".csv":
        return extract_csv(raw)
    return Extracted([Section(text=raw.strip())] if raw.strip() else [])
