import sys
import io
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from openpyxl import load_workbook

from backend.app.excel_io import export_workbook, normalize_request_business_fields, parse_weekly_excel


SAMPLE = Path("/Users/smk/Downloads/20260626~20260707请款明细.xlsx")


def test_weekly_excel_imports_known_workbook():
    if not SAMPLE.exists():
        return
    rows, meta = parse_weekly_excel(SAMPLE)
    assert len(rows) == 161
    sheet_counts = {item["sheet"]: item.get("imported", 0) for item in meta["sheets"]}
    assert sheet_counts["汇总7.7可支付"] == 86
    assert sheet_counts["赣瑞模具（7月前）"] == 7
    assert sheet_counts["赣瑞模具（7月后）"] == 1
    assert sheet_counts["志威模具（7月前）"] == 10
    assert sheet_counts["志威模具（7月后）"] == 1
    assert sheet_counts["YUEWEI自研产品+OEM项目款"] == 43
    assert sheet_counts["物流"] == 5
    assert sheet_counts["员工报销"] == 1
    assert sheet_counts["员工成长金+待定付款"] == 7
    first = rows[0]
    assert first["dingding_id"]
    assert first["amount"] is not None
    assert first["content_hash"]
    assert {row.get("finance_review") for row in rows} <= {"未付款", "部分付款", "已付款"}
    assert {row.get("payment_status") for row in rows} <= {None}
    assert {row.get("general_manager_approval") for row in rows if row.get("general_manager_approval")} <= {"同意付款", "延缓批付", "存在争议"}
    paid_remark_row = next(row for row in rows if row["source_sheet"] == "YUEWEI自研产品+OEM项目款" and row["source_row"] == 24)
    assert paid_remark_row["finance_review"] == "未付款"
    assert "已支付10万" in paid_remark_row["remark"]
    assert "Tiffany垫付" in paid_remark_row["remark"]
    assert paid_remark_row["actual_payment_date"] is None
    finance_paid_row = next(row for row in rows if row["source_sheet"] == "志威模具（7月后）" and row["source_row"] == 3)
    assert finance_paid_row["finance_review"] == "已付款"
    assert "已经支付14500元" in finance_paid_row["remark"]
    payment_status_note_row = next(row for row in rows if row["source_sheet"] == "物流" and row["source_row"] == 2)
    assert payment_status_note_row["finance_review"] == "未付款"
    assert "原付款情况：同意支付" in payment_status_note_row["remark"]
    manager_note_row = next(row for row in rows if row["source_sheet"] == "员工报销" and row["source_row"] == 2)
    assert manager_note_row["general_manager_approval"] is None
    assert "我要审核一下" in manager_note_row["general_manager_opinion"]
    assert manager_note_row["remark"] is None
    manager_plan_row = next(row for row in rows if row["source_sheet"] == "志威模具（7月前）" and row["source_row"] == 5)
    assert manager_plan_row["general_manager_approval"] is None
    assert manager_plan_row["general_manager_opinion"] == "对完账已确认付款计划"
    assert "对完账已确认付款计划" not in manager_plan_row["remark"]
    dispute_row = next(row for row in rows if row["source_sheet"] == "员工成长金+待定付款" and row["source_row"] == 9)
    assert dispute_row["general_manager_approval"] == "存在争议"
    assert "CMA船司" in dispute_row["general_manager_opinion"]
    assert "CMA船司" not in dispute_row["remark"]
    advance_note_row = next(row for row in rows if row["source_sheet"] == "员工成长金+待定付款" and row["source_row"] == 8)
    assert advance_note_row["finance_review"] == "未付款"
    assert advance_note_row["general_manager_approval"] is None
    assert "Tiffany垫付" in advance_note_row["general_manager_opinion"]
    assert "Tiffany垫付" not in advance_note_row["remark"]
    assert not any(row.get("finance_review") == "2026/5/8付" for row in rows)


def test_partial_finance_review_is_not_normalized_as_paid():
    row = {
        "finance_review": "部分付款",
        "actual_payment_date": "2026-07-10",
        "payment_status": "",
        "remark": "",
    }
    normalize_request_business_fields(row)
    assert row["finance_review"] == "部分付款"
    assert row["payment_status"] is None

    status_row = {
        "finance_review": "",
        "actual_payment_date": "",
        "payment_status": "部分付款",
        "remark": "",
    }
    normalize_request_business_fields(status_row)
    assert status_row["finance_review"] == "部分付款"
    assert status_row["payment_status"] is None


def test_weekly_excel_extracts_embedded_images():
    if not SAMPLE.exists():
        return
    rows, meta = parse_weekly_excel(SAMPLE)
    embedded_images = [image for row in rows for image in row.get("_embedded_images", [])]
    assert meta["images"] == {"found": 9, "attached": 9, "skipped": 0}
    assert len(embedded_images) == 9
    assert all(image["data"] and image["label"].startswith("Excel图片 ") for image in embedded_images)
    image_rows = {(row["source_sheet"], row["source_row"]) for row in rows if row.get("_embedded_images")}
    assert ("汇总7.7可支付", 3) in image_rows
    assert ("志威模具（7月前）", 7) in image_rows
    assert ("员工成长金+待定付款", 10) in image_rows


def test_export_workbook_roundtrip_bytes():
    rows, _ = parse_weekly_excel(SAMPLE) if SAMPLE.exists() else ([], {})
    content = export_workbook(
        {"name": "测试批次", "end_date": "2026-07-07"},
        [{"id": idx + 1, **row} for idx, row in enumerate(rows[:5])],
        {},
    )
    assert content.startswith(b"PK")
    assert len(content) > 1000
    workbook = load_workbook(io.BytesIO(content))
    headers = [cell.value for worksheet in workbook.worksheets for row in worksheet.iter_rows(max_row=2) for cell in row]
    assert "付款情况" not in headers
