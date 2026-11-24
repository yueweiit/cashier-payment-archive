import json

import pytest

from backend.app import external_expenses as expenses


PAYEE = "陈示例"
ACCOUNT = "6222 1234 5678 9012"
BANK = "测试银行深圳示例支行"


def operation_row(form_values=(), **overrides):
    return {
        "source_type": "operation",
        "source_id": "payment-fields-1",
        "effective_date": "2026-11-24",
        "approval_no": "PAYMENT-FIELDS-1",
        "approval_status": "RUNNING",
        "approval_result": "agree",
        "execution_region": "中国 China",
        "beneficiary": PAYEE,
        "base_currency_amount": 100,
        "raw_data": {"formComponentValues": list(form_values)},
        **overrides,
    }


def assert_payment_fields(mapped, *, name, account, bank=None, invoice=None):
    expected = {
        "payee_name": name,
        "payee_account": account,
        "bank_name": bank,
        "invoice_status": invoice,
    }
    request_data = mapped["request_data"]
    external_source = request_data["raw_extra"]["external_source"]
    metadata = expenses._external_expense_metadata(mapped)
    for field, value in expected.items():
        assert request_data.get(field) == value, field
        assert external_source.get(field) == value, field
        assert metadata.get(field) == value, field


def test_operation_maps_real_bilingual_payment_components():
    mapped = expenses.map_external_expense(operation_row([
        {"name": "收款人beneficiario", "value": PAYEE},
        {"name": "收款账户Cuenta de cobro", "value": ACCOUNT},
        {"name": "收款银行（分行）Banco receptor (sucursal)", "value": BANK},
        {"name": "是否有发票¿Existe factura?", "value": "否"},
    ]))

    assert mapped["request_data"]["payee_account"] == ACCOUNT
    assert_payment_fields(mapped, name=PAYEE, account=ACCOUNT, bank=BANK, invoice="否")
    assert mapped["payment_account"] == "私户"
    assert mapped["request_data"]["payment_account"] == "私户"


@pytest.mark.parametrize(("forms", "beneficiary", "account_source"), [
    ([{"name": "收款账户Cuenta de cobro", "value": ACCOUNT}], PAYEE, "form"),
    ([], "历史姓名 银行 旧账号", "beneficiary_fallback"),
    ([{"name": "收款账户信息", "value": "组合资料"}], PAYEE, "beneficiary_fallback"),
    ([], None, None),
], ids=["explicit-account", "legacy-beneficiary", "combined-component", "no-account"])
def test_payee_account_source_distinguishes_explicit_account_from_fallback(forms, beneficiary, account_source):
    mapped = expenses.map_external_expense(operation_row(forms, beneficiary=beneficiary))
    source = mapped["request_data"]["raw_extra"]["external_source"]

    assert "payee_account_source" in source
    assert source["payee_account_source"] == account_source
    assert expenses._external_expense_metadata(mapped)["payee_account_source"] == account_source


def test_explicit_payment_components_take_precedence_over_legacy_beneficiary():
    mapped = expenses.map_external_expense(operation_row([
        {"name": "收款人beneficiario", "value": PAYEE},
        {"name": "收款账户信息", "value": "组合收款资料，不是独立账号"},
        {"name": "收款账户Cuenta de cobro", "value": ACCOUNT},
        {"name": "收款银行（分行）Banco receptor (sucursal)", "value": BANK},
    ], beneficiary="历史姓名 银行 旧账号"))

    assert_payment_fields(mapped, name=PAYEE, account=ACCOUNT, bank=BANK)
    assert mapped["beneficiary"] == PAYEE


def test_spanish_label_only_components_preserve_display_values():
    mapped = expenses.map_external_expense(operation_row([
        {"label": "Beneficiario", "value": PAYEE},
        {"label": "Cuenta de cobro", "value": ACCOUNT},
        {"label": "Banco receptor (sucursal)", "value": BANK},
        {"label": "¿Existe factura?", "value": "Sí"},
    ], beneficiary=None))

    assert_payment_fields(mapped, name=PAYEE, account=ACCOUNT, bank=BANK, invoice="Sí")
    assert mapped["payment_account"] == "公户"


@pytest.mark.parametrize("encode", [lambda value: value, json.dumps], ids=["objects", "json"])
def test_payment_components_decode_json_display_values(encode):
    mapped = expenses.map_external_expense(operation_row([
        {"name": "收款人beneficiario", "value": encode({"name": PAYEE})},
        {"name": "收款账户Cuenta de cobro", "value": encode([{"value": ACCOUNT}])},
        {"name": "收款银行（分行）Banco receptor (sucursal)", "value": encode({"label": BANK})},
        {"name": "是否有发票¿Existe factura?", "value": encode({"text": "待定"})},
    ], beneficiary=None))

    assert_payment_fields(mapped, name=PAYEE, account=ACCOUNT, bank=BANK, invoice="待定")
    assert mapped["payment_account"] is None


@pytest.mark.parametrize("beneficiary", ["历史收款人 银行 旧账号", None])
def test_missing_components_retain_legacy_beneficiary_fallback(beneficiary):
    mapped = expenses.map_external_expense(operation_row(beneficiary=beneficiary))

    assert_payment_fields(mapped, name=beneficiary, account=beneficiary)
    assert mapped["payment_account"] is None
    assert ("缺少收款信息" in mapped["warnings"]) == (beneficiary is None)


def test_combined_account_information_is_not_an_explicit_account():
    legacy = "历史收款人 旧账号"
    mapped = expenses.map_external_expense(operation_row([
        {"name": "收款人beneficiario", "value": PAYEE},
        {"name": "收款账户信息", "value": "组合资料不能冒充独立账号"},
    ], beneficiary=legacy))

    assert_payment_fields(mapped, name=PAYEE, account=legacy)


@pytest.mark.parametrize("source_type", ["monthly", "logistics"])
@pytest.mark.parametrize("account_label", ["收款账户信息", "收款账户Cuenta beneficiaria"])
def test_process_payments_retain_combined_beneficiary_fallback(source_type, account_label):
    combined = "示例公司 测试银行 6222 9999"
    mapped = expenses.map_process_expense({
        "source_id": "combined-payment-1",
        "process_instance_id": "combined-payment-instance",
        "create_time": "2026-11-24T08:00:00+00:00",
        "status": "RUNNING",
        "result": "agree",
        "raw_payload": {
            "businessId": "COMBINED-PAYMENT-1",
            "formComponentValues": [
                {"name": "执行地区", "value": "中国 China"},
                {"name": "币种Moneda", "value": "CNY"},
                {"name": account_label, "value": combined},
                {"name": "合计总额" if source_type == "monthly" else "本次申请付款总额", "value": "100"},
                {"name": "申请付款明细" if source_type == "monthly" else "物流费用明细", "value": [
                    {"rowValue": [{"label": "本次申请付款金额", "value": "100"}]},
                ]},
            ],
        },
    }, source_type=source_type)

    assert_payment_fields(mapped, name=combined, account=combined)
    assert mapped["request_data"]["raw_extra"]["external_source"].get("payee_account_source") == "beneficiary_fallback"
    assert not mapped["errors"]


@pytest.mark.parametrize("account_count", [1, 2])
def test_purchase_multiple_payees_do_not_receive_first_payees_account(account_count):
    payees = ["示例供应商甲", "示例供应商乙"]
    forms = [{"name": "收款人beneficiario", "value": name} for name in payees]
    forms.extend({"name": "收款账户Cuenta de cobro", "value": f"6222 {index}"} for index in range(account_count))
    forms.append({"name": "收款银行（分行）Banco receptor (sucursal)", "value": BANK})
    mapped = expenses.map_external_expense(operation_row(forms, source_type="purchase", beneficiary=None))
    combined = " / ".join(payees)

    assert_payment_fields(mapped, name=combined, account=combined)
    assert mapped["request_data"]["raw_extra"]["external_source"].get("payee_account_source") == "beneficiary_fallback"
    assert mapped["beneficiary"] == combined
    assert "存在多个收款人，请确认" in mapped["warnings"]


def test_purchase_single_payee_uses_explicit_payment_components():
    mapped = expenses.map_external_expense(operation_row([
        {"label": "Beneficiario", "value": json.dumps({"name": PAYEE})},
        {"label": "Cuenta de cobro", "value": json.dumps({"value": ACCOUNT})},
        {"label": "Banco receptor (sucursal)", "value": json.dumps({"label": BANK})},
    ], source_type="purchase", beneficiary=None))

    assert_payment_fields(mapped, name=PAYEE, account=ACCOUNT, bank=BANK)
    assert "存在多个收款人，请确认" not in mapped["warnings"]
