"""폼필 역질문 후보 필터 (P-14 · CU-10 ① · 2026-09-16).

체크포인트 실측: 미매핑 열 하나에 후보가 **86개** 실렸고 원시 스키마 순서 그대로라
선두 10개가 `dtype` · `id` · `acl_id` · `acl_manager_group_id` · `acl_manager_id` ·
`haschildren` · `inheritstatus` · `*pollinginterval` 이었다. 후보는 **사람이 고르는
목록**이므로 조립 가능성만으로는 부족하다.
"""

from __future__ import annotations

from src.db_adapters.polestar.assembler import build_form_fill_candidates


def _schema(columns: list[str]) -> dict:
    return {"tables": {"polestar.cmm_resource": {"columns": [{"name": c} for c in columns]}}}


def _values(candidates: list[dict]) -> list[str]:
    return [c["value"] for c in candidates]


def test_내부_감사_칼럼은_후보에서_빠진다() -> None:
    schema = _schema([
        "dtype", "id", "acl_id", "acl_manager_group_id", "haschildren", "inheritstatus",
        "inventorypollinginterval", "name", "hostname", "ipaddress",
    ])

    values = _values(build_form_fill_candidates(schema, {"entity_table": "cmm_resource"}))

    assert values == ["column:name", "column:hostname", "column:ipaddress"]


def test_전부_노이즈면_거르지_않는다() -> None:
    """빈 드롭다운은 사람이 아무것도 못 고르게 만든다 — 시끄러운 쪽이 낫다."""
    schema = _schema(["id", "acl_id", "dtype"])

    values = _values(build_form_fill_candidates(schema, {"entity_table": "cmm_resource"}))

    assert values == ["column:id", "column:acl_id", "column:dtype"]


def test_EAV_후보는_그대로_유지된다() -> None:
    schema = _schema(["name", "acl_id"])
    schema["_structure_meta"] = {
        "patterns": [{
            "type": "eav",
            "known_attributes_detail": [
                {"name": "Vendor", "description": "제조사"},
                {"name": "OSType", "description": "OS 종류"},
            ],
        }]
    }

    values = _values(build_form_fill_candidates(schema, {"entity_table": "cmm_resource"}))

    assert "eav:Vendor" in values and "eav:OSType" in values
    assert "column:acl_id" not in values
