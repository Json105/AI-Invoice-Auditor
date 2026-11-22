"""Abstract base class and shared payload helpers for document extractors."""

import io
import mimetypes
from abc import ABC, abstractmethod
from typing import Tuple, Union
from pathlib import Path
from PIL import Image
from ..models import InvoiceData


def prepare_document_payload(file_path: Union[str, Path]) -> Tuple[bytes, str]:
    """Read document bytes and normalize scanned image formats (TIFF/BMP) to PNG."""
    path = Path(file_path)
    suffix = path.suffix.lower()
    raw_bytes = path.read_bytes()

    if suffix in (".tiff", ".tif", ".bmp"):
        with Image.open(io.BytesIO(raw_bytes)) as img:
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue(), "image/png"

    mime_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".pdf": "application/pdf",
    }
    mime_type = mime_map.get(suffix)
    if not mime_type:
        mime_type, _ = mimetypes.guess_type(str(path))
    return raw_bytes, (mime_type or "image/jpeg")


class BaseInvoiceExtractor(ABC):
    """Abstract interface for invoice and receipt multimodal extractors."""

    @abstractmethod
    def extract(self, file_path: Union[str, Path]) -> InvoiceData:
        """Extract structured InvoiceData from a given file path (PDF or Image).

        Args:
            file_path: Path to the image or PDF file.

        Returns:
            InvoiceData: Fully structured and validated Pydantic model.
        """
        pass
