"""Gemini 3.7 Flash multimodal extractor for invoices and receipts."""

import os
import json
import base64
from pathlib import Path
from typing import List, Optional, Union
import requests

from .base import BaseInvoiceExtractor, clean_json_text, parse_invoices_payload, prepare_document_payload
from ..models import InvoiceData, DocumentType

EXTRACTION_SYSTEM_PROMPT = """You are an enterprise accounting AI expert specialized in extracting structured data from global financial documents including:
1. Taiwan Uniform Invoices (台灣統一發票: 二聯式, 三聯式, 電子發票證明聯) & THSR Tickets / Taxi Receipts
2. China VAT Invoices (中國增值稅普通發票 / 專用發票), Didi Travel Receipts (滴滴出行行程單), China Railway tickets
3. Southeast Asia Travel Receipts (Thailand Grab, Taxi receipts, hotel invoices, food & beverage receipts in THB, SGD, VND, etc.)
4. European & Global Commercial Invoices & Receipts (EUR, USD, HKD, etc.)

Extract the following JSON structure matching this exact schema:
{
  "document_type": "tw_invoice" | "thsr_ticket" | "taxi_receipt" | "commercial_invoice" | "general_receipt",
  "invoice_number": "string (invoice number / ticket number / receipt number)",
  "invoice_date": "YYYY-MM-DD",
  "buyer_tax_id": "string (8 digits if Taiwan Tax ID, else null)",
  "buyer_name": "string or null",
  "seller_tax_id": "string (tax ID / unified code if available, else null)",
  "seller_name": "string (company, merchant, hotel, or taxi fleet name)",
  "currency": "NT$" | "RMB" | "THB" | "EUR" | "USD" | "HKD" | "SGD" | "TWD",
  "subtotal": float or null (amount before tax),
  "tax_amount": float or null (VAT / sales tax),
  "tax_rate": float or null (e.g. 0.05 for Taiwan 5%, 0.06 or 0.13 for China, 0.07 for Thailand),
  "total_amount": float (final total payment amount in original currency),
  "expense_category": "F" | "I" | "T" | "H" | "S" | "E",
  "purpose": "string (brief summary in Traditional Chinese or English, e.g. 高鐵商務差旅, 曼谷拜訪客戶計程車, 上海出差住宿, 客戶交流餐費)",
  "payment_method": "Credit Card" | "Cash",
  "items": [
    {
      "name": "string (item name / description)",
      "quantity": float,
      "unit_price": float or null,
      "amount": float
    }
  ],
  "travel_details": {
    "departure": "string or null",
    "destination": "string or null",
    "train_or_flight_no": "string or null",
    "seat_info": "string or null",
    "vehicle_number": "string or null",
    "driver_name": "string or null",
    "travel_date": "YYYY-MM-DD or null",
    "start_time": "HH:MM or null",
    "end_time": "HH:MM or null",
    "mileage_km": float or null
  },
  "confidence_score": float (between 0.0 and 1.0)
}

Important Rules:
- Return ONLY valid JSON, with NO surrounding markdown or backticks (no ```json ... ```).
- Currency Normalization:
  * For Taiwan documents, use "NT$".
  * For China documents (RMB/CNY/元), use "RMB".
  * For Thailand documents (THB/Baht/฿), use "THB".
  * For Euro receipts (€), use "EUR".
  * For US Dollar ($), use "USD".
  * For Hong Kong ($HK), use "HKD".
- Expense Category Codes (F/I/T/H/S/E):
  * "F": Food & Dining / 餐飲膳食
  * "I": Insurance & Visa / 旅遊保險與簽證費
  * "T": Transportation / 交通 (機票、高鐵、台鐵、計程車、Grab、滴滴出行、地鐵)
  * "H": Hotel & Lodging / 住宿飯店
  * "S": Social gift & Entertainment / 社交伴手禮與業務招待
  * "E": Extra category / 行政雜支 (行李超重費、國際SIM卡/WIFI、電話通訊費)
- For Taiwan Uniform Invoices, look for 2-letter prefix + 8-digit number (e.g., AB-12345678).
- For THSR tickets, capture departure/destination stations, ticket number, train number, and date.
- For Taxi/Grab receipts, capture vehicle number if available, total fare, and travel date/time.
"""

MULTI_RECEIPT_INSTRUCTION = """
MULTI-RECEIPT MODE:
This image or page may contain ONE OR MULTIPLE distinct invoices/tickets/receipts placed side-by-side or arranged together.
Inspect the entire frame carefully and extract EVERY distinct receipt separately into a JSON wrapper object matching:
{
  "invoices": [
    { ...same invoice schema as above... }
  ]
}
Return ONLY valid JSON with key "invoices" containing an array of all detected receipts (even if there is only 1 receipt).
"""


class GeminiExtractor(BaseInvoiceExtractor):
    """Multimodal document extractor powered by Gemini 3.7 Flash."""

    def __init__(self, api_key: Optional[str] = None, model: str = "gemini-3.7-flash"):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model = model

    def _call_gemini(self, file_path: Union[str, Path], prompt_text: str) -> str:
        """Send encoded document payload and prompt to Gemini REST API and return cleaned JSON."""
        if not self.api_key:
            raise ValueError(
                "Gemini API Key is missing! Set GEMINI_API_KEY environment variable "
                "or pass api_key to GeminiExtractor."
            )

        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        raw_bytes, mime_type = prepare_document_payload(path)
        encoded_data = base64.b64encode(raw_bytes).decode("utf-8")

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt_text},
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": encoded_data
                            }
                        }
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "response_mime_type": "application/json"
            }
        }

        response = requests.post(url, headers=headers, json=payload, timeout=60)
        if response.status_code != 200:
            raise RuntimeError(f"Gemini API request failed ({response.status_code}): {response.text}")

        res_json = response.json()
        try:
            raw_text = res_json["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as e:
            raise RuntimeError(f"Unexpected response structure from Gemini API: {res_json}") from e

        return clean_json_text(raw_text)

    def extract(self, file_path: Union[str, Path]) -> InvoiceData:
        """Extract structured invoice data using Gemini Vision."""
        cleaned = self._call_gemini(file_path, EXTRACTION_SYSTEM_PROMPT)
        return InvoiceData.model_validate(json.loads(cleaned))

    def extract_many(self, file_path: Union[str, Path], multi_receipt: bool = False) -> List[InvoiceData]:
        """Extract one or multiple receipts from one photo/page using Gemini Vision."""
        if not multi_receipt:
            return [self.extract(file_path)]
        prompt = f"{EXTRACTION_SYSTEM_PROMPT}\n\n{MULTI_RECEIPT_INSTRUCTION}"
        cleaned = self._call_gemini(file_path, prompt)
        return parse_invoices_payload(cleaned)
