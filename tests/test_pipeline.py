"""Integration tests for the end-to-end pipeline and Excel exporter."""

import os
from pathlib import Path
import openpyxl
from src.extractors.mock_extractor import MockExtractor
from src.auditor import InvoiceAuditor
from src.exporter import ExpenseExporter
from src.main import run_pipeline


def test_pipeline_execution(tmp_path):
    """Test full pipeline with mock extractor producing Excel and JSON."""
    sample_dir = Path(__file__).parent.parent / "sample_data"
    output_dir = tmp_path / "test_output"

    reports = run_pipeline(
        input_path=sample_dir,
        engine="mock",
        output_dir=output_dir
    )

    assert len(reports) >= 3

    # Check output files exist
    excel_path = output_dir / "expense_claim_report.xlsx"
    json_path = output_dir / "audit_trail.json"

    assert excel_path.exists()
    assert json_path.exists()
    assert excel_path.stat().st_size > 3000  # Valid Excel binary size

    # Verify Excel contents with openpyxl
    wb = openpyxl.load_workbook(excel_path)
    assert "差旅與發票報銷清冊" in wb.sheetnames

    ws = wb["差旅與發票報銷清冊"]
    assert "企業智慧財務報銷與合規稽核清冊" in str(ws["A1"].value)
    assert ws.max_row >= 7  # Title + headers + data rows + summary
