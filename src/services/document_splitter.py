"""Document splitting utilities for multi-page scanned PDFs and batch ingestion."""

import mimetypes
import re
import shutil
import subprocess
import logging
from pathlib import Path
from typing import List, Tuple, Union

logger = logging.getLogger(__name__)

_MIME_MAP = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".bmp": "image/bmp",
    ".json": "application/json",
}


def detect_document_mime(file_path: Union[str, Path]) -> str:
    """Resolve canonical MIME type from file suffix."""
    path = Path(file_path)
    mime = _MIME_MAP.get(path.suffix.lower())
    if not mime:
        mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


def _page_sort_key(path: Path) -> Tuple[int, str]:
    """Sort pdftoppm output files naturally by numeric page suffix."""
    match = re.search(r"-(\d+)\.png$", path.name)
    if match:
        return (int(match.group(1)), path.name)
    return (0, path.name)


def split_pdf_to_page_images(
    pdf_path: Union[str, Path],
    output_dir: Union[str, Path],
    dpi: int = 150,
    max_pages: int = 100,
) -> List[Path]:
    """Render each page of a PDF into an ordered PNG image using pdftoppm.

    Returns an empty list cleanly if pdftoppm is unavailable or conversion fails
    so callers can fall back to passing the original PDF directly.
    """
    source = Path(pdf_path)
    pdftoppm_bin = shutil.which("pdftoppm")
    if not pdftoppm_bin or not source.exists() or not source.is_file():
        return []

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_prefix = out_dir / "page"

    try:
        proc = subprocess.run(
            [
                pdftoppm_bin,
                "-png",
                "-r",
                str(dpi),
                "-l",
                str(max_pages),
                str(source),
                str(out_prefix),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as exc:
        logger.warning("Failed to invoke pdftoppm for %s: %s", source, exc)
        return []

    if proc.returncode != 0:
        logger.warning("pdftoppm exited %s for %s: %s", proc.returncode, source, proc.stderr.strip())
        return []

    pages = sorted(out_dir.glob("page-*.png"), key=_page_sort_key)
    return pages


def expand_uploaded_document(
    file_path: Union[str, Path],
    original_name: str,
    split_multipage_pdf: bool = True,
    max_pdf_pages: int = 100,
) -> List[Tuple[Path, str, str]]:
    """Expand a multi-page PDF into page images; return single item on fallback or images."""
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix == ".pdf" and split_multipage_pdf:
        pages_dir = path.parent / f"{path.stem}_pages"
        page_images = split_pdf_to_page_images(
            pdf_path=path,
            output_dir=pages_dir,
            dpi=150,
            max_pages=max_pdf_pages,
        )
        if page_images:
            base_stem = Path(original_name).stem or path.stem
            return [
                (img_path, f"{base_stem}_p{idx}.png", "image/png")
                for idx, img_path in enumerate(page_images, start=1)
            ]

    return [(path, original_name, detect_document_mime(path))]
