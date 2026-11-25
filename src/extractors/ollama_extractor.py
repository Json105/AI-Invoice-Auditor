"""Ollama on-premise privacy extractor for enterprise air-gapped environments."""

import json
import base64
from pathlib import Path
from typing import List, Optional, Union
import requests

from .base import BaseInvoiceExtractor, clean_json_text, parse_invoices_payload, prepare_document_payload
from .gemini_extractor import EXTRACTION_SYSTEM_PROMPT, MULTI_RECEIPT_INSTRUCTION
from ..models import InvoiceData


class OllamaExtractor(BaseInvoiceExtractor):
    """Local offline vision extractor using Ollama (e.g. llama3.2-vision, qwen2.5-vl).

    Designed for enterprise intranet environments with strict zero-cloud data policies.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3.2-vision:latest"
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def _call_ollama(self, file_path: Union[str, Path], prompt_text: str) -> str:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        raw_bytes, _ = prepare_document_payload(path)
        encoded_image = base64.b64encode(raw_bytes).decode("utf-8")

        url = f"{self.base_url}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt_text,
            "images": [encoded_image],
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0.1
            }
        }

        try:
            response = requests.post(url, json=payload, timeout=120)
        except requests.exceptions.ConnectionError as e:
            raise ConnectionError(
                f"Cannot connect to Ollama at {self.base_url}. "
                "Ensure Ollama is running (`ollama serve`) and model is pulled."
            ) from e

        if response.status_code != 200:
            raise RuntimeError(f"Ollama error ({response.status_code}): {response.text}")

        res_json = response.json()
        return clean_json_text(res_json.get("response", ""))

    def extract(self, file_path: Union[str, Path]) -> InvoiceData:
        """Extract structured invoice data using local Ollama model."""
        prompt = (
            f"{EXTRACTION_SYSTEM_PROMPT}\n\n"
            "Analyze this document image and return ONLY the required JSON object."
        )
        raw_output = self._call_ollama(file_path, prompt)
        return InvoiceData.model_validate(json.loads(raw_output))

    def extract_many(self, file_path: Union[str, Path], multi_receipt: bool = False) -> List[InvoiceData]:
        """Extract one or multiple receipts arranged on one image via Ollama."""
        if not multi_receipt:
            return [self.extract(file_path)]
        prompt = f"{EXTRACTION_SYSTEM_PROMPT}\n\n{MULTI_RECEIPT_INSTRUCTION}"
        raw_output = self._call_ollama(file_path, prompt)
        return parse_invoices_payload(raw_output)
