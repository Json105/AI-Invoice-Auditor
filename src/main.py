"""CLI Entrypoint for AI-Invoice-Auditor.

Process documents in batch, execute compliance audits, and export to ERP Excel & JSON.
"""

import os
import sys
import argparse
from pathlib import Path
from typing import List

from .services.env_loader import load_env_file

# Load local .env before importing extractors so model/key defaults apply in CLI mode
load_env_file()

from .models import AuditReport, AuditStatus
from .auditor import InvoiceAuditor
from .exporter import ExpenseExporter
from .template_exporter import TemplateExpenseExporter
from .extractors.mock_extractor import MockExtractor
from .extractors.gemini_extractor import DEFAULT_GEMINI_MODEL, GeminiExtractor
from .extractors.ollama_extractor import OllamaExtractor
from .extractors.openrouter_extractor import DEFAULT_OPENROUTER_MODEL, OpenRouterExtractor


def get_extractor(engine: str, api_key: str = None, model: str = DEFAULT_GEMINI_MODEL):
    """Factory to instantiate the selected extractor."""
    engine_lower = engine.lower()
    if engine_lower == "gemini":
        return GeminiExtractor(api_key=api_key, model=model)
    elif engine_lower == "openrouter":
        resolved_model = model if (model and model != DEFAULT_GEMINI_MODEL) else DEFAULT_OPENROUTER_MODEL
        return OpenRouterExtractor(api_key=api_key, model=resolved_model)
    elif engine_lower == "ollama":
        return OllamaExtractor(model="llama3.2-vision:latest")
    elif engine_lower == "mock":
        return MockExtractor()
    else:
        raise ValueError(f"Unknown extractor engine: '{engine}'. Choices: [gemini, openrouter, ollama, mock]")


def run_pipeline(
    input_path: Path,
    engine: str = "mock",
    api_key: str = None,
    model: str = DEFAULT_GEMINI_MODEL,
    output_dir: Path = Path("output"),
    template_path: Path = None,
    db_path: Path = Path("data/invoices.db"),
    save_to_db: bool = True
) -> List[AuditReport]:
    """Execute end-to-end pipeline."""
    extractor = get_extractor(engine, api_key=api_key, model=model)
    auditor = InvoiceAuditor(db_path=db_path)
    reports: List[AuditReport] = []

    # Collect files
    if input_path.is_file():
        target_files = [input_path]
    elif input_path.is_dir():
        supported_exts = {".json", ".jpg", ".jpeg", ".png", ".pdf", ".webp"}
        target_files = sorted([
            p for p in input_path.iterdir()
            if p.suffix.lower() in supported_exts and not p.name.startswith(".")
        ])
    else:
        print(f"❌ Error: Path not found: {input_path}")
        return []

    if not target_files:
        print(f"⚠️ Warning: No supported document files found in {input_path}")
        return []

    print(f"\n========================================================")
    print(f"🚀 AI-Invoice-Auditor | 企業智慧核銷與稽核引擎")
    print(f"   AI Engine : {engine.upper()} ({model if engine == 'gemini' else 'local'})")
    print(f"   Input Dir : {input_path}")
    print(f"   Files Qty : {len(target_files)} 份單據待處理")
    if template_path:
        print(f"   Template  : {template_path} (將自動套用企業報銷表)")
    if db_path:
        print(f"   Database  : {db_path} (跨月防重複歷史資料庫)")
    print(f"========================================================\n")

    for idx, file_path in enumerate(target_files, 1):
        print(f"[{idx}/{len(target_files)}] 正在解析: {file_path.name} ... ", end="", flush=True)
        try:
            invoice_data = extractor.extract(file_path)
            report = auditor.audit(invoice_data, file_path=str(file_path))
            reports.append(report)

            if report.status == AuditStatus.APPROVED:
                status_icon = "✅ APPROVED (合規)"
            elif report.status == AuditStatus.WARNING:
                status_icon = "⚠️ WARNING (警示)"
            else:
                status_icon = "❌ REJECTED (違規)"

            print(status_icon)
            for issue in report.issues:
                print(f"      ↳ [{issue.level}] {issue.message}")

        except Exception as e:
            print(f"💥 失敗 ({str(e)})")

    # Exporting
    output_dir.mkdir(parents=True, exist_ok=True)
    exporter = ExpenseExporter(reports)

    excel_path = output_dir / "expense_claim_report.xlsx"
    json_path = output_dir / "audit_trail.json"

    exporter.to_excel(excel_path)
    exporter.to_json(json_path)

    # Optional Corporate Template Export
    template_filled_path = None
    if template_path and Path(template_path).exists():
        template_filled_path = output_dir / "Travel_Expenses_Report_Filled.xlsx"
        t_exporter = TemplateExpenseExporter(reports, template_path=template_path)
        t_exporter.export(template_filled_path)

    # Database persistence
    if save_to_db and reports:
        from .database import save_audit_reports
        save_audit_reports(reports, db_path=db_path)

    # Summary
    approved = sum(1 for r in reports if r.status == AuditStatus.APPROVED)
    warning = sum(1 for r in reports if r.status == AuditStatus.WARNING)
    rejected = sum(1 for r in reports if r.status == AuditStatus.REJECTED)

    print(f"\n---------------- 執行統計總結 ----------------")
    print(f"總計處理 : {len(reports)} 份單據")
    print(f"合規通過 : {approved} 份")
    print(f"警示複核 : {warning} 份")
    print(f"違規阻擋 : {rejected} 份")
    print(f"----------------------------------------------")
    print(f"📊 ERP Excel 報表已產出 : {excel_path}")
    print(f"📝 JSON 審計追蹤已產出  : {json_path}")
    if template_filled_path:
        print(f"📑 企業報銷單已填妥產出 : {template_filled_path}")
    print()

    return reports


def main():
    parser = argparse.ArgumentParser(
        description="AI-Invoice-Auditor: Enterprise IDP for Invoices, THSR, and Taxi Receipts"
    )
    parser.add_argument(
        "-i", "--input",
        default="sample_data",
        help="Input document file or directory path (default: sample_data/)"
    )
    parser.add_argument(
        "-e", "--engine",
        choices=["gemini", "openrouter", "ollama", "mock"],
        default="mock",
        help="Extraction engine (default: mock for offline demo)"
    )
    parser.add_argument(
        "-m", "--model",
        default=DEFAULT_GEMINI_MODEL,
        help="Model name override (Gemini model or OpenRouter model ID)"
    )
    parser.add_argument(
        "-k", "--api-key",
        default=None,
        help="API Key (defaults to GEMINI_API_KEY or OPENROUTER_API_KEY)"
    )
    parser.add_argument(
        "-o", "--output",
        default="output",
        help="Output directory (default: output/)"
    )
    parser.add_argument(
        "-t", "--template",
        default=None,
        help="Path to corporate expense Excel template (e.g. Template.xlsx)"
    )

    args = parser.parse_args()
    input_path = Path(args.input)
    output_dir = Path(args.output)
    template_path = Path(args.template) if args.template else None
    resolved_api_key = args.api_key or (
        os.getenv("OPENROUTER_API_KEY") if args.engine == "openrouter" else os.getenv("GEMINI_API_KEY")
    )

    run_pipeline(
        input_path=input_path,
        engine=args.engine,
        api_key=resolved_api_key,
        model=args.model,
        output_dir=output_dir,
        template_path=template_path
    )


if __name__ == "__main__":
    main()

