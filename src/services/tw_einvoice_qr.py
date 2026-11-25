"""Cross-border-safe Taiwan e-Invoice QR Code pre-decoder.

Uses `zbarimg` locally to extract QR payloads from receipt photos or split PDF
pages. Strictly validates the Ministry of Finance (MOF) 77-char left-QR layout
plus 8-digit Taiwan Seller Tax ID checksum so Mainland China Fapiao, Southeast
Asia PromptPay/PayNow, and Europe/US QR receipts are NEVER misclassified and
cleanly route to the multimodal AI extractor.
"""

import logging
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple, Union

from ..auditor import validate_taiwan_tax_id
from ..models import DocumentType, InvoiceData, InvoiceItem

logger = logging.getLogger(__name__)

_INV_NUM_RE = re.compile(r"^[A-Z]{2}\d{8}$")
_ROC_DATE_RE = re.compile(r"^\d{7}$")
_RAND_RE = re.compile(r"^\d{4}$")
_HEX8_RE = re.compile(r"^[0-9A-Fa-f]{8}$")
_TAX8_RE = re.compile(r"^\d{8}$")
_AES_RE = re.compile(r"^[A-Za-z0-9+/=]{24}$")


def decode_qr_codes_from_image(image_path: Union[str, Path]) -> List[str]:
    """Decode all QR code strings present in an image via zbarimg."""
    zbar_bin = shutil.which("zbarimg")
    if not zbar_bin:
        return []

    path = Path(image_path)
    if not path.exists() or not path.is_file():
        return []

    try:
        proc = subprocess.run(
            [zbar_bin, "--quiet", "--raw", "-Sdisable", "-Sqr.enable", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception as exc:
        logger.warning("zbarimg execution failed for %s: %s", path.name, exc)
        return []

    # Exit code 4 means no barcode detected in image; exit code 0 means success.
    if proc.returncode not in (0, 4):
        return []

    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _parse_roc_date(roc_yyyymmdd: str) -> Optional[str]:
    """Convert 7-digit ROC date (YYYMMDD, e.g. 1151110) to YYYY-MM-DD."""
    if not _ROC_DATE_RE.match(roc_yyyymmdd):
        return None
    roc_year = int(roc_yyyymmdd[:3])
    month = int(roc_yyyymmdd[3:5])
    day = int(roc_yyyymmdd[5:7])
    if not (100 <= roc_year <= 199):
        return None
    try:
        dt = datetime(1911 + roc_year, month, day)
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return None


def _is_strict_tw_left_qr(raw_text: str) -> bool:
    """Strictly validate Taiwan MOF e-Invoice left QR Code 77-char fixed header."""
    if not raw_text or len(raw_text) < 77:
        return False

    inv_no = raw_text[0:10]
    roc_date = raw_text[10:17]
    rand_code = raw_text[17:21]
    sales_hex = raw_text[21:29]
    total_hex = raw_text[29:37]
    buyer_id = raw_text[37:45]
    seller_id = raw_text[45:53]
    aes_cipher = raw_text[53:77]

    if not _INV_NUM_RE.match(inv_no):
        return False
    if not _parse_roc_date(roc_date):
        return False
    if not _RAND_RE.match(rand_code):
        return False
    if not _HEX8_RE.match(sales_hex) or not _HEX8_RE.match(total_hex):
        return False

    sales_amt = int(sales_hex, 16)
    total_amt = int(total_hex, 16)
    if total_amt <= 0:
        return False
    if sales_amt > 0 and sales_amt > total_amt:
        return False

    if not _TAX8_RE.match(buyer_id) or not _TAX8_RE.match(seller_id):
        return False
    if not validate_taiwan_tax_id(seller_id):
        return False
    if buyer_id != "00000000" and not validate_taiwan_tax_id(buyer_id):
        return False
    if not _AES_RE.match(aes_cipher):
        return False

    return True


def classify_qr_payload(raw_text: str) -> str:
    """Classify QR payload to distinguish Taiwan e-Invoices from overseas QR formats."""
    if not raw_text:
        return "unknown"
    text = raw_text.strip()
    lower = text.lower()
    if lower.startswith("http://") or lower.startswith("https://"):
        return "url"
    if text.startswith("01,"):
        # Mainland China VAT / Digital Fapiao comma-delimited QR (e.g. 01,04,3100...,)
        return "cn_fapiao"
    if text.startswith("000201") or text.startswith("BCD\n") or text.startswith("BCD\r\n"):
        # SEA PromptPay/PayNow/QRIS EMVCo payment QR or EU SEPA EPC QR
        return "sea_or_global_payment"
    if _is_strict_tw_left_qr(text):
        return "tw_einvoice_left"
    if text.startswith("**"):
        return "tw_einvoice_right"
    return "unknown"


def _parse_qr_items(left_tail: str, right_texts: List[str]) -> Tuple[List[InvoiceItem], Optional[str]]:
    """Parse optional colon-delimited line items appended across left & right e-Invoice QRs."""
    combined_parts: List[str] = []
    if left_tail.startswith(":"):
        combined_parts.extend(left_tail.split(":")[1:])
    for r_text in right_texts:
        body = r_text[2:] if r_text.startswith("**") else r_text
        if body:
            combined_parts.extend(body.lstrip(":").split(":"))

    # Taiwan e-Invoice spec appends metadata slots (often item_count:item_count:encoding:item1:qty1:price1:...)
    # Look backwards/sequentially for numeric qty/unit_price triples where meaningful.
    items: List[InvoiceItem] = []
    idx = 0
    # Skip leading numeric encoding triplet when present (e.g. **********:1:1:1:Item:Qty:Price)
    while idx + 2 < len(combined_parts):
        a, b, c = combined_parts[idx], combined_parts[idx + 1], combined_parts[idx + 2]
        if a.isdigit() and b.isdigit() and c.isdigit() and idx == 0:
            idx += 3
            continue
        name = a.strip()
        try:
            qty = float(b)
            unit_p = float(c)
            if name and qty > 0 and unit_p >= 0:
                items.append(InvoiceItem(name=name, quantity=qty, unit_price=unit_p, amount=round(qty * unit_p, 2)))
                idx += 3
                continue
        except (ValueError, TypeError):
            pass
        idx += 1

    summary_purpose = items[0].name if items else None
    return items, summary_purpose


def parse_taiwan_einvoice_qr_texts(qr_texts: List[str]) -> Optional[InvoiceData]:
    """Build InvoiceData strictly from Taiwan e-Invoice QR strings, or return None."""
    left_qr: Optional[str] = None
    right_qrs: List[str] = []

    for raw in qr_texts:
        kind = classify_qr_payload(raw)
        if kind == "tw_einvoice_left" and left_qr is None:
            left_qr = raw.strip()
        elif kind == "tw_einvoice_right":
            right_qrs.append(raw.strip())

    if not left_qr:
        return None

    inv_track = left_qr[0:2]
    inv_digits = left_qr[2:10]
    invoice_number = f"{inv_track}-{inv_digits}"
    invoice_date = _parse_roc_date(left_qr[10:17])

    sales_amt = int(left_qr[21:29], 16)
    total_amt = int(left_qr[29:37], 16)
    buyer_tax_id = None if left_qr[37:45] == "00000000" else left_qr[37:45]
    seller_tax_id = left_qr[45:53]

    subtotal: Optional[float] = None
    tax_amount: Optional[float] = None
    if 0 < sales_amt <= total_amt:
        subtotal = float(sales_amt)
        tax_amount = float(total_amt - sales_amt)

    items, item_purpose = _parse_qr_items(left_qr[77:], right_qrs)

    return InvoiceData(
        document_type=DocumentType.TW_INVOICE,
        invoice_number=invoice_number,
        invoice_date=invoice_date,
        buyer_tax_id=buyer_tax_id,
        seller_tax_id=seller_tax_id,
        currency="NT$",
        subtotal=subtotal,
        tax_amount=tax_amount,
        total_amount=float(total_amt),
        purpose=item_purpose or "台灣電子發票消費",
        items=items,
        notes="台灣電子發票 QR Code 高速離線解碼",
        confidence_score=0.99,
    )


def try_decode_taiwan_einvoice_from_image(image_path: Union[str, Path]) -> Optional[InvoiceData]:
    """Attempt fast offline QR decoding; returns None for all non-TW or unreadable documents."""
    qr_texts = decode_qr_codes_from_image(image_path)
    if not qr_texts:
        return None
    return parse_taiwan_einvoice_qr_texts(qr_texts)
