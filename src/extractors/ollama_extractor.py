"""Ollama on-premise privacy extractor for enterprise air-gapped environments."""

import json
import base64
from pathlib import Path
from typing import Union, Optional
import requests

from .base import BaseInvoiceExtractor, prepare_document_payload
from .gemini_extractor import EXTRACTION_SYSTEM_PROMPT
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

    def extract(self, file_path: Union[str, Path]) -> InvoiceData:
        """Extract structured invoice data using local Ollama model."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        raw_bytes, _ = prepare_document_payload(path)
        encoded_image = base64.b64encode(raw_bytes).decode("utf-8")

        prompt = (
            f"{EXTRACTION_SYSTEM_PROMPT}\n\n"
            "Analyze this document image and return ONLY the required JSON object."
        )

        url = f"{self.base_url}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
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
        raw_output = res_json.get("response", "").strip()

        # Clean JSON markdown if model wrapped it
        if raw_output.startswith("```json"):
            raw_output = raw_output[7:]
        if raw_output.endswith("```"):
            raw_output = raw_output[:-3]
        raw_output = raw_output.strip()

        data_dict = json.loads(raw_output)
        return InvoiceData.model_validate(data_dict)
