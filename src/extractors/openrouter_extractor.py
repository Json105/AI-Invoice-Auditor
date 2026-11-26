"""OpenRouter OpenAI-compatible multimodal extractor with free-vision fallback."""

import base64
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union
import requests

from .base import BaseInvoiceExtractor, clean_json_text, parse_invoices_payload, prepare_document_payload
from .gemini_extractor import EXTRACTION_SYSTEM_PROMPT, MULTI_RECEIPT_INSTRUCTION
from ..models import InvoiceData
from ..services.document_splitter import split_pdf_to_page_images

logger = logging.getLogger(__name__)

DEFAULT_OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "google/gemma-4-31b-it:free")

OPENROUTER_FREE_VISION_MODELS: List[Dict[str, str]] = [
    {
        "id": "google/gemma-4-31b-it:free",
        "label": "Google: Gemma 4 31B (free) — 首選多語文件",
    },
    {
        "id": "nvidia/nemotron-3.5-super-vl:free",
        "label": "NVIDIA: Nemotron 3.5 Super VL (free) — 複雜/多收據",
    },
    {
        "id": "google/gemma-4-26b-a4b-it:free",
        "label": "Google: Gemma 4 26B A4B (free) — 高速批次",
    },
    {
        "id": "inclusionai/ling-3.2-flash:free",
        "label": "inclusionAI: Ling 3.2 Flash (free) — 中文單據強化",
    },
    {
        "id": "openrouter/free",
        "label": "Free Models Router — 自動路由備援",
    },
]

_THINK_TAG_RE = re.compile(r"<think>.*?</think>", flags=re.DOTALL | re.IGNORECASE)


def _extract_json_block(raw_text: str) -> str:
    """Strip reasoning tags, code fences, and stray prose around JSON payload."""
    stripped = _THINK_TAG_RE.sub("", raw_text or "").strip()
    cleaned = clean_json_text(stripped)
    if (cleaned.startswith("{") and cleaned.endswith("}")) or (
        cleaned.startswith("[") and cleaned.endswith("]")
    ):
        return cleaned

    first_obj = cleaned.find("{")
    first_arr = cleaned.find("[")
    starts = [i for i in (first_obj, first_arr) if i != -1]
    if not starts:
        return cleaned
    start = min(starts)
    open_ch = cleaned[start]
    close_ch = "}" if open_ch == "{" else "]"
    end = cleaned.rfind(close_ch)
    if end > start:
        return cleaned[start : end + 1].strip()
    return cleaned


class OpenRouterExtractor(BaseInvoiceExtractor):
    """Multimodal document extractor using OpenRouter free or paid vision models."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        enable_fallback: bool = True,
    ):
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        self.model = (model or "").strip() or DEFAULT_OPENROUTER_MODEL
        self.last_used_model: str = self.model
        self.base_url = (base_url or os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")).rstrip("/")
        self.enable_fallback = enable_fallback

    def _build_candidate_models(self) -> List[str]:
        candidates = [self.model]
        if self.enable_fallback:
            for item in OPENROUTER_FREE_VISION_MODELS:
                mid = item["id"]
                if mid not in candidates:
                    candidates.append(mid)
        return candidates

    def _prepare_image_data_uri(self, file_path: Path) -> str:
        raw_bytes, mime_type = prepare_document_payload(file_path)
        if mime_type == "application/pdf":
            with tempfile.TemporaryDirectory() as tmp_dir:
                pages = split_pdf_to_page_images(file_path, Path(tmp_dir), dpi=150, max_pages=1)
                if not pages:
                    raise RuntimeError("無法將 PDF 頁面轉換為圖檔以供 OpenRouter 視覺模型辨識")
                raw_bytes = pages[0].read_bytes()
                mime_type = "image/png"
        b64 = base64.b64encode(raw_bytes).decode("utf-8")
        return f"data:{mime_type};base64,{b64}"

    @staticmethod
    def _extract_message_text(resp_json: Dict) -> str:
        choices = resp_json.get("choices") or []
        if not choices:
            raise ValueError(f"OpenRouter response missing choices: {resp_json}")
        message = choices[0].get("message") or {}
        content = message.get("content", "")
        if isinstance(content, list):
            parts = [
                str(p.get("text", ""))
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            content = "\n".join(parts)
        return _extract_json_block(str(content))

    def _call_openrouter(self, file_path: Union[str, Path], user_prompt: str) -> str:
        if not self.api_key:
            raise ValueError(
                "OpenRouter API Key is missing! Set OPENROUTER_API_KEY environment variable "
                "or enter your key in the settings panel."
            )

        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        data_uri = self._prepare_image_data_uri(path)
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "https://github.com/Json105/AI-Invoice-Auditor"),
            "X-Title": "AI-Invoice-Auditor",
        }

        candidates = self._build_candidate_models()
        last_error: Optional[Exception] = None

        for idx, candidate_model in enumerate(candidates):
            payload = {
                "model": candidate_model,
                "temperature": 0.1,
                "messages": [
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_prompt},
                            {"type": "image_url", "image_url": {"url": data_uri}},
                        ],
                    },
                ],
            }

            try:
                response = requests.post(url, headers=headers, json=payload, timeout=90)
            except requests.RequestException as exc:
                last_error = exc
                logger.warning("OpenRouter request error on %s (%s); trying next fallback", candidate_model, exc)
                continue

            if response.status_code in (401, 403):
                raise RuntimeError(f"OpenRouter authentication failed ({response.status_code}): {response.text}")

            if response.status_code == 429 or response.status_code == 404 or response.status_code >= 500:
                last_error = RuntimeError(f"OpenRouter HTTP {response.status_code} ({candidate_model}): {response.text}")
                if idx < len(candidates) - 1:
                    logger.warning(
                        "OpenRouter model %s returned HTTP %s; falling back to %s",
                        candidate_model,
                        response.status_code,
                        candidates[idx + 1],
                    )
                    continue
                raise last_error

            if response.status_code != 200:
                raise RuntimeError(f"OpenRouter API error ({response.status_code}): {response.text}")

            try:
                text_out = self._extract_message_text(response.json())
                json.loads(text_out)
                self.last_used_model = candidate_model
                return text_out
            except Exception as exc:
                last_error = exc
                if idx < len(candidates) - 1:
                    logger.warning(
                        "OpenRouter model %s returned invalid JSON (%s); trying %s",
                        candidate_model,
                        exc,
                        candidates[idx + 1],
                    )
                    continue
                raise RuntimeError(f"Failed to parse OpenRouter JSON output: {exc}") from exc

        raise RuntimeError(f"All OpenRouter candidate models failed: {last_error}")

    def extract(self, file_path: Union[str, Path]) -> InvoiceData:
        """Extract single receipt InvoiceData from image or single-page PDF."""
        raw_text = self._call_openrouter(
            file_path,
            "Analyze this invoice/receipt and return ONLY the required JSON object.",
        )
        return InvoiceData.model_validate(json.loads(raw_text))

    def extract_many(self, file_path: Union[str, Path], multi_receipt: bool = False) -> List[InvoiceData]:
        """Extract one or multiple receipts arranged on one image via OpenRouter."""
        if not multi_receipt:
            return [self.extract(file_path)]
        raw_text = self._call_openrouter(file_path, MULTI_RECEIPT_INSTRUCTION)
        return parse_invoices_payload(raw_text)
