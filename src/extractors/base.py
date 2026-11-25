"""Abstract base class and shared payload helpers for document extractors."""

import io
import json
import mimetypes
from abc import ABC, abstractmethod
from typing import Any, List, Tuple, Union
from pathlib import Path
from PIL import Image
from ..models import InvoiceData


def clean_json_text(raw_text: str) -> str:
    """Strip surrounding markdown code fences from model JSON responses."""
    cleaned = raw_text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


def parse_invoices_payload(raw_text: str) -> List[InvoiceData]:
    """Parse single-object or multi-receipt JSON payloads into validated InvoiceData items."""
    parsed: Any = json.loads(clean_json_text(raw_text))
    if isinstance(parsed, dict) and isinstance(parsed.get("invoices"), list):
        items = parsed["invoices"]
    elif isinstance(parsed, list):
        items = parsed
    else:
        items = [parsed]

    results = [InvoiceData.model_validate(item) for item in items if isinstance(item, dict)]
    if not results:
        raise ValueError("AI 回傳結果未包含有效發票結構")
    return results


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
        """Extract structured InvoiceData from a given file path (PDF or Image)."""
        pass

    def extract_many(self, file_path: Union[str, Path], multi_receipt: bool = False) -> List[InvoiceData]:
        """Extract one or multiple receipts from a single image/page."""
        return [self.extract(file_path)]
