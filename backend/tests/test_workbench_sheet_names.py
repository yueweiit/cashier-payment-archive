import json
import sqlite3
from copy import deepcopy

import pytest

from backend.app import sheet_names


def metadata(subject=None, **source_fields):
    return {"external_source": {"service_subject": subject, **source_fields}}


@pytest.mark.parametrize(("raw_name", "expected"), [
    ("悦为智能 YW Tech_Ai", "悦为智能"),
    ("凌翔", "广州凌翔"),
    ("凌翔产品&开发", "广州凌翔"),
    ("凌翔供应链及采购执行单元", "广州凌翔"),
    ("凌翔产品&开发及供应链", "广州凌翔"),
    ("Guangzhou Lingxiang广州凌翔", "广州凌翔"),
    ("星铭HR人力资源中心", "星铭 HR"),
    ("星铭FC财务中心", "星铭 财务"),
    ("YW MOLDES MX模具", "YW MODELS"),
    ("YW MOLDES MX/模具", "YW MODELS"),
    ("YW MOLDES MX", "YW MODELS"),
    ("yw moldes", "YW MODELS"),
    ("YW MODELS模具", "YW MODELS"),
    ("YW MODELS MX模具", "YW MODELS"),
    ("星铭HR", "星铭 HR"),
    ("星铭FC", "星铭 财务"),
    ("星铭财务", "星铭 财务"),
    ("YUEWEI MX核心制造", "YUEWEI MX"),
    ("LEMOS MX供应链开发及管理", "LEMOS MX"),
    ("LEMOS MX 销售", "LEMOS MX"),
    ("拉丁购", "拉丁购"),
    ("LATINGO", "LATINGO"),
    ("柠檬树", "柠檬树"),
])
def test_confirmed_organization_aliases_have_display_groups(raw_name, expected):
    assert sheet_names.workbench_sheet_name(raw_name) == expected
    assert sheet_names.canonical_sheet_name(raw_name) == raw_name


@pytest.mark.parametrize("name", [
    "广州凌翔", "星铭 HR", "星铭 财务", "YW MODELS", "YUEWEI MX",
    "LEMOS MX", "拉丁购", "LATINGO", "柠檬树",
])
def test_explicit_display_group_is_not_changed_by_another_service_subject(name):
    raw_extra = metadata("LATINGO" if name != "LATINGO" else "拉丁购", applicant_department=name)

    assert sheet_names.workbench_sheet_name(name, raw_extra) == name


@pytest.mark.parametrize("mixed", ["LatínGo拉丁购", "拉丁购LATINGO", "LatínGo 拉丁购"])
@pytest.mark.parametrize("subject", ["拉丁购", "LATINGO"])
@pytest.mark.parametrize("encode", [lambda value: value, json.dumps], ids=["dict", "saved-json"])
def test_mixed_latin_labels_require_explicit_service_subject(mixed, subject, encode):
    assert sheet_names.workbench_sheet_name(mixed, encode(metadata(subject))) == subject


@pytest.mark.parametrize("raw_extra", [
    None, {}, "invalid json", "[]", "null", metadata("未知公司"),
    metadata("拉丁购 / LATINGO"), metadata(["拉丁购", "LATINGO"]),
    metadata(["拉丁购", "未知公司"]),
    {"employee_department_mapping": {"third_level_department": "拉丁购"}},
])
def test_mixed_latin_labels_without_single_explicit_subject_are_pending(raw_extra):
    assert sheet_names.workbench_sheet_name("LatínGo拉丁购", raw_extra) == "待归组"


@pytest.mark.parametrize(("subject", "expected"), [
    ("广州凌翔", "广州凌翔"),
    ("YW MOLDES", "YW MODELS"),
    ("星铭HR人力资源中心", "星铭 HR"),
    ("星铭FC财务中心", "星铭 财务"),
])
def test_mixed_supply_chain_center_uses_service_subject_before_employee_department(subject, expected):
    raw_extra = metadata(subject)
    raw_extra["employee_department_mapping"] = {"third_level_department": "凌翔产品&开发"}

    assert sheet_names.workbench_sheet_name("凌翔/星铭供应链及职能中心", raw_extra) == expected


@pytest.mark.parametrize(("third_level", "expected"), [
    ("凌翔产品&开发", "广州凌翔"),
    ("星铭HR人力资源中心", "星铭 HR"),
    ("星铭FC财务中心", "星铭 财务"),
])
@pytest.mark.parametrize("location", ["employee_mapping", "external_source"])
def test_mixed_supply_chain_center_can_use_known_third_level_department(third_level, expected, location):
    raw_extra = (
        {"employee_department_mapping": {"third_level_department": third_level}}
        if location == "employee_mapping"
        else metadata(applicant_department_level3=third_level)
    )

    assert sheet_names.workbench_sheet_name("凌翔/星铭供应链及职能中心", raw_extra) == expected


@pytest.mark.parametrize("raw_extra", [None, metadata("未知公司"), metadata("星铭")])
def test_unresolved_mixed_supply_chain_center_is_pending(raw_extra):
    assert sheet_names.workbench_sheet_name("凌翔/星铭供应链及职能中心", raw_extra) == "待归组"


@pytest.mark.parametrize("name", ["FC 财务中心Centro Financiero (FC)", "FC 财务中心 Centro Financiero (FC)"])
@pytest.mark.parametrize("subject", ["星铭", "星铭FC财务中心"])
def test_generic_finance_center_needs_explicit_xingming_subject(name, subject):
    assert sheet_names.workbench_sheet_name(name, metadata(subject)) == "星铭 财务"


@pytest.mark.parametrize("name", ["FC 财务中心Centro Financiero (FC)", "HR", "FC", "HR人力资源中心"])
@pytest.mark.parametrize("raw_extra", [None, metadata("未知公司"), metadata("广州凌翔")])
def test_generic_departments_without_automatic_origin_keep_their_names(name, raw_extra):
    assert sheet_names.workbench_sheet_name(name, raw_extra) == name


@pytest.mark.parametrize(("subject", "expected"), [
    ("广州凌翔", "广州凌翔"),
    ("YW MOLDES", "YW MODELS"),
    ("星铭HR人力资源中心", "星铭 HR"),
    ("星铭FC财务中心", "星铭 财务"),
    ("拉丁购", "拉丁购"),
    ("LATINGO", "LATINGO"),
    ("柠檬树", "柠檬树"),
    ("LEMOS MX", "LEMOS MX"),
    ("YUEWEI MX", "YUEWEI MX"),
])
def test_automatic_original_department_can_follow_explicit_service_subject(subject, expected):
    name = "原申请部门"

    assert sheet_names.workbench_sheet_name(name, metadata(subject, applicant_department=name)) == expected


@pytest.mark.parametrize("raw_extra", [
    metadata("广州凌翔"),
    metadata("广州凌翔", applicant_department="其他申请部门"),
    metadata("广州凌翔", applicant_department="原申请部门 "),
    metadata("未知主体", applicant_department="原申请部门"),
    {**metadata("广州凌翔", applicant_department="原申请部门"),
     "employee_department_mapping": {"assigned_department": "原申请部门"}},
    metadata("广州凌翔", applicant_department="原申请部门", sheet_assignment_source="employee_info.level2_department"),
])
def test_manual_or_already_assigned_departments_are_not_regrouped_by_service_subject(raw_extra):
    assert sheet_names.workbench_sheet_name("原申请部门", raw_extra) == "原申请部门"


@pytest.mark.parametrize("subject", ['["拉丁购"]', {"label": "拉丁购"}, [{"name": "拉丁购"}], '"拉丁购"'])
def test_service_subject_json_display_values_are_supported(subject):
    assert sheet_names.workbench_sheet_name("LatínGo拉丁购", metadata(subject)) == "拉丁购"


@pytest.mark.parametrize(("subject", "expected"), [
    ("拉丁购/拉丁购", "拉丁购"),
    ("凌翔/产品&开发", "广州凌翔"),
    ("Guangzhou Lingxiang广州凌翔/产品&开发Departamento de Producto y Desarrollo", "广州凌翔"),
    ("YW MOLDES MX模具/PC生产计划Control de Producción", "YW MODELS"),
    ("Dongguan Xingming东莞星铭/FC 财务中心Centro Financiero (FC)", "星铭 财务"),
    ("东莞星铭/HR人力资源中心", "星铭 HR"),
])
@pytest.mark.parametrize("name", ["凌翔/星铭供应链及职能中心", "原申请部门"])
def test_explicit_service_subject_paths_resolve_company_and_known_department(subject, expected, name):
    raw_extra = metadata(subject, applicant_department=name)

    assert sheet_names.workbench_sheet_name(name, json.dumps(raw_extra)) == expected


@pytest.mark.parametrize("subject", [
    "拉丁购/LATINGO", "LATINGO/拉丁购", "凌翔/星铭供应链及职能中心",
    "凌翔/星铭HR人力资源中心", "YW MOLDES/广州凌翔", "LEMOS MX/柠檬树",
    "广州凌翔拓展公司/产品&开发", "东莞星铭/HR/FC",
])
def test_service_subject_paths_with_conflicting_or_unknown_roots_are_not_guessed(subject):
    assert sheet_names.workbench_sheet_name("凌翔/星铭供应链及职能中心", metadata(subject)) == "待归组"


def test_mixed_latin_label_accepts_explicit_repeated_ladingou_service_path():
    assert sheet_names.workbench_sheet_name("LatínGo拉丁购", metadata("拉丁购/拉丁购")) == "拉丁购"


@pytest.mark.parametrize("name", ["东莞星铭", "Dongguan Xingming东莞星铭"])
def test_service_only_root_aliases_do_not_change_direct_sheet_identity(name):
    assert sheet_names.workbench_sheet_name(name) == name
    assert sheet_names.canonical_sheet_name(name) == name


def test_service_only_company_root_can_resolve_mixed_center():
    assert sheet_names.workbench_sheet_name("凌翔/星铭供应链及职能中心", metadata("凌翔")) == "广州凌翔"
    assert sheet_names.workbench_sheet_name("FC 财务中心Centro Financiero (FC)", metadata("东莞星铭")) == "星铭 财务"


@pytest.mark.parametrize(("raw_extra", "expected"), [
    (None, "待归组"),
    (metadata("星铭"), "待归组"),
    (metadata("广州凌翔"), "待归组"),
    (metadata("星铭HR"), "星铭 HR"),
    (metadata("星铭财务"), "星铭 财务"),
    ({"employee_department_mapping": {"third_level_department": "星铭FC财务中心"}}, "星铭 财务"),
])
def test_bare_xingming_never_creates_a_third_xingming_group(raw_extra, expected):
    assert sheet_names.workbench_sheet_name("星铭", raw_extra) == expected


@pytest.mark.parametrize("assignment_source", [[], {}, 123])
def test_unexpected_saved_metadata_types_do_not_break_sql_projection(assignment_source):
    raw_extra = metadata("广州凌翔", applicant_department="原申请部门", sheet_assignment_source=assignment_source)

    assert sheet_names.workbench_sheet_name("原申请部门", json.dumps(raw_extra)) == "广州凌翔"


def test_saved_metadata_is_safe_for_sql_function_and_input_is_not_mutated():
    raw_extra = metadata("YW MOLDES")
    before = deepcopy(raw_extra)
    assert sheet_names.workbench_sheet_name("凌翔/星铭供应链及职能中心", raw_extra) == "YW MODELS"
    assert raw_extra == before
    with sqlite3.connect(":memory:") as conn:
        conn.create_function("workbench_sheet_name", 2, sheet_names.workbench_sheet_name)
        assert conn.execute("SELECT workbench_sheet_name(?, ?)", ("拉丁购LATINGO", json.dumps(metadata("拉丁购")))).fetchone()[0] == "拉丁购"
        assert conn.execute("SELECT workbench_sheet_name(?, ?)", ("拉丁购LATINGO", "invalid-json")).fetchone()[0] == "待归组"


@pytest.mark.parametrize("value", [None, "", "  "])
def test_empty_sheet_names_keep_existing_fallback(value):
    assert sheet_names.workbench_sheet_name(value) == "未分 Sheet"


def test_legacy_mould_canonicalization_is_preserved_without_new_persistent_aliases():
    assert sheet_names.workbench_sheet_name("赣瑞模具（7月前）") == "赣瑞模具"
    assert sheet_names.canonical_sheet_name("YW MOLDES MX模具") == "YW MOLDES MX模具"
    assert sheet_names.canonical_sheet_name("凌翔产品&开发") == "凌翔产品&开发"


def test_dingtalk_workbench_sheet_choices_are_ordered_fixed_groups():
    assert sheet_names.DINGTALK_WORKBENCH_SHEETS == (
        "悦为智能", "广州凌翔", "星铭 HR", "星铭 财务", "YW MODELS", "YUEWEI MX",
        "LEMOS MX", "拉丁购", "LATINGO", "柠檬树",
    )


@pytest.mark.parametrize(("name", "subject", "expected"), [
    ("悦为智能 YW Tech_Ai", None, "悦为智能"),
    ("原财务部门", "悦为智能 YW Tech_Ai/行政管理", "悦为智能"),
    ("原财务部门", "YW Tech_Ai", "悦为智能"),
    ("原财务部门", "Yuewei Intelligent", "悦为智能"),
    ("原财务部门", "悦为智能技术（东莞）有限公司", "悦为智能"),
    ("YUEWEI", None, None),
    ("原财务部门", "YUEWEI", None),
    ("原财务部门", "YUEWEI/行政管理", None),
    ("原财务部门", "YUEWEI MX", "YUEWEI MX"),
    ("原财务部门", "广州凌翔", "广州凌翔"),
    ("原财务部门", "未知主体", None),
    ("LatínGo拉丁购", "拉丁购/拉丁购", "拉丁购"),
    ("LatínGo拉丁购", "LATINGO", "LATINGO"),
    ("LatínGo拉丁购", "悦为智能", None),
    ("拉丁购LATINGO", "拉丁购/LATINGO", None),
    ("凌翔/星铭供应链及职能中心", "凌翔/星铭供应链及职能中心", None),
    ("凌翔/星铭供应链及职能中心", "星铭", None),
    ("凌翔/星铭供应链及职能中心", "东莞星铭/FC", "星铭 财务"),
    ("YUEWEI MX核心制造", "悦为智能", "YUEWEI MX"),
    ("拉丁购", "LATINGO", "拉丁购"),
    ("星铭", "广州凌翔", None),
])
def test_new_dingtalk_group_resolution_requires_a_fixed_unambiguous_company(name, subject, expected):
    raw_extra = metadata(subject)
    before = deepcopy(raw_extra)

    assert sheet_names.resolve_dingtalk_workbench_sheet(name, raw_extra) == expected
    assert raw_extra == before


def assignment(source_sheet="凌翔产品&开发", sheet="YW MODELS", mode="manual"):
    return {"dingtalk_workbench_assignment": {"source_sheet": source_sheet, "sheet": sheet, "mode": mode}}


@pytest.mark.parametrize("mode", ["auto", "manual"])
@pytest.mark.parametrize("encode", [lambda value: value, json.dumps], ids=["dict", "saved-json"])
def test_valid_pin_controls_display_and_registry_without_changing_raw_source(mode, encode):
    raw_name = "凌翔产品&开发"
    raw_extra = assignment(raw_name, mode=mode)
    before = deepcopy(raw_extra)

    assert sheet_names.pinned_dingtalk_workbench_sheet(raw_name, encode(raw_extra)) == "YW MODELS"
    assert sheet_names.workbench_sheet_name(raw_name, encode(raw_extra)) == "YW MODELS"
    assert sheet_names.resolve_dingtalk_workbench_sheet(raw_name, encode(raw_extra)) == "YW MODELS"
    assert sheet_names.workbench_registered_sheet(raw_name, encode(raw_extra)) == "YW MODELS"
    assert sheet_names.canonical_sheet_name(raw_name) == raw_name
    assert raw_extra == before


@pytest.mark.parametrize("raw_extra", [
    None, "invalid-json", {"dingtalk_workbench_assignment": []},
    assignment(source_sheet="凌翔供应链及采购执行单元"),
    assignment(sheet="未指定公司"), assignment(sheet="凌翔产品&开发"), assignment(sheet=" YW MODELS"),
    assignment(mode="MANUAL"), assignment(mode=""), assignment(mode=None),
    {"dingtalk_workbench_assignment": {"sheet": "YW MODELS", "mode": "manual"}},
    {"dingtalk_workbench_assignment": {"source_sheet": "凌翔产品&开发", "sheet": ["YW MODELS"], "mode": "manual"}},
    {"external_source": assignment()["dingtalk_workbench_assignment"]},
])
def test_invalid_or_differently_bound_pins_do_not_change_projection(raw_extra):
    raw_name = "凌翔产品&开发"

    assert sheet_names.pinned_dingtalk_workbench_sheet(raw_name, raw_extra) is None
    assert sheet_names.workbench_sheet_name(raw_name, raw_extra) == "广州凌翔"
    assert sheet_names.workbench_registered_sheet(raw_name, raw_extra) == raw_name


def test_pin_binding_uses_raw_canonical_identity_instead_of_display_aliases():
    raw_extra = assignment(source_sheet="yw moldes mx模具", sheet="悦为智能")
    assert sheet_names.pinned_dingtalk_workbench_sheet("YW MOLDES MX模具", raw_extra) is None
    assert sheet_names.workbench_sheet_name("YW MOLDES MX模具", raw_extra) == "YW MODELS"
    assert sheet_names.pinned_dingtalk_workbench_sheet("赣瑞模具（7月前）", assignment("赣瑞模具（7月后）")) == "YW MODELS"


@pytest.mark.parametrize("raw_name", ["手工自定义组", "凌翔产品&开发", "悦为智能 YW Tech_Ai", "赣瑞模具（7月前）"])
def test_unpinned_registry_preserves_canonical_raw_source(raw_name):
    assert sheet_names.workbench_registered_sheet(raw_name, metadata("悦为智能")) == sheet_names.canonical_sheet_name(raw_name)
