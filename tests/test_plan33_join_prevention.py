"""resource_conf_id 조인 규칙의 **현행 정본** 고정.

파일명은 Plan 33(2026-04 `resource_conf_id` JOIN 금지) 시절의 것이지만, 그 결정은
**D-022 재검토(2026-07-30)로 뒤집혔다** — 지금 정본은 `cmm_resource.resource_conf_id =
core_config_prop.configuration_id` 조인을 **필수**로 지시한다(docs/02_decision.md D-022 부기,
`config/db_profiles/*.yaml` query_guide "반드시 resource_conf_id를 사용하세요").

종전 이 파일은 삭제된 `config/db_profiles/polestar_pg.yaml`(2026-07-01 ac2cdc8에서 제거)을
픽스처로 읽어 5건 전부 수집 오류였고, 단언 내용도 뒤집힌 규칙 쪽이었다(2026-09-21 정리).
`build_excluded_join_map()`·스키마 프롬프트 주석·구조 가이드 렌더는 합성 프로필로
`tests/test_utils/test_schema_utils.py`·`tests/test_nodes/test_query_generator_excluded_join.py`가
이미 덮으므로, 여기서는 **뒤집힌 결정이 되돌아오지 않는지만** 실 프로필로 지킨다.
"""

from pathlib import Path

import pytest
import yaml

_PROFILE_DIR = Path(__file__).resolve().parent.parent / "config" / "db_profiles"
_POLESTAR_PROFILES = ("polestar", "polestar_b0", "polestar_cm_gp", "polestar_cm_yd")


def _load(db_id: str) -> dict:
    with open(_PROFILE_DIR / f"{db_id}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.mark.parametrize("db_id", _POLESTAR_PROFILES)
def test_resource_conf_id_is_not_an_excluded_join_column(db_id):
    """resource_conf_id를 조인 금지 목록에 되돌리면 EAV 피벗이 전부 막힌다(D-022 재검토)."""
    for pattern in _load(db_id).get("patterns") or []:
        excluded = {
            (e.get("table"), e.get("column"))
            for e in pattern.get("excluded_join_columns") or []
        }
        assert ("cmm_resource", "resource_conf_id") not in excluded, (
            f"{db_id}: resource_conf_id가 조인 금지로 되돌아왔다 — D-022 재검토(2026-07-30) 위배"
        )


@pytest.mark.parametrize("db_id", _POLESTAR_PROFILES)
def test_query_guide_directs_resource_conf_id_join(db_id):
    """query_guide가 resource_conf_id 조인을 지시한다 — 이게 현행 정본 조인 키다."""
    guide = _load(db_id).get("query_guide") or ""
    assert "resource_conf_id = core_config_prop.configuration_id" in guide, (
        f"{db_id}: query_guide에 정본 조인 키 지시가 없다"
    )
