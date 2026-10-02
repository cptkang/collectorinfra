"""Polestar 프로필 드리프트 가드 (D-067, B).

gp/yd/b0는 동일 Polestar 스키마를 기술하는 near-duplicate 프로필이라, 한 프로필만
고치고 다른 프로필을 빠뜨리는 **전파 누락**이 반복됐다(D-061 서버명→gp/yd 누락,
D-066 예시 등). 이 테스트는 과거 버그를 **불변식**으로 박아 재발(전파 누락)을 CI에서
즉시 실패시키는 **순수 가드레일**이다(프로덕션 코드 미변경).

- B-1(TestPolestarProfileInvariants): 3개 프로필 각각이 만족해야 할 공통 correctness 불변식.
- B-2(test_gp_yd_only_allowlisted_diffs): gp/yd는 주석 제외 실질 차이가 allowlist뿐이어야 함.

의도적으로 공통 속성을 바꾸거나 gp/yd를 다르게 할 때만 여기 불변식/allowlist를 갱신한다.
"""

import difflib
from pathlib import Path

import pytest
import yaml

_PROFILE_DIR = Path("config/db_profiles")
POLESTAR_PROFILES = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]

# 서버 등록명(cmm_resource.name)에 매핑돼야 하는 용어. EAV Hostname synonyms에 들어가면
# "서버명"이 hostname으로 오매핑된다(D-061).
_IDENTITY_TERMS = {"서버명", "서버 이름", "서버이름", "장비명", "장비 이름", "장비이름"}


def _load(db_id: str) -> dict:
    with open(_PROFILE_DIR / f"{db_id}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _eav_attr(profile: dict, attr_name: str):
    """patterns.known_attributes에서 특정 name의 EAV 속성 dict를 찾는다(없으면 None)."""
    for pattern in profile.get("patterns", []) or []:
        for attr in pattern.get("known_attributes", []) or []:
            if isinstance(attr, dict) and attr.get("name") == attr_name:
                return attr
    return None


@pytest.mark.parametrize("db_id", POLESTAR_PROFILES)
class TestPolestarProfileInvariants:
    """B-1: 3개 Polestar 프로필 각각이 만족해야 할 공통 불변식(과거 버그 = 규칙)."""

    def test_source_manual(self, db_id):
        assert _load(db_id).get("source") == "manual"

    def test_eav_hostname_excludes_identity_terms(self, db_id):
        """D-061 재발 차단: EAV Hostname synonyms에 '서버명' 등 식별어가 있으면 hostname 오매핑."""
        host = _eav_attr(_load(db_id), "Hostname")
        assert host is not None, f"{db_id}: EAV Hostname 속성이 없음"
        leaked = _IDENTITY_TERMS & set(host.get("synonyms", []) or [])
        assert not leaked, (
            f"{db_id}: EAV Hostname synonyms에 식별어 누출 {leaked} — "
            f"'서버명'은 cmm_resource.name(등록명)으로 가야 함(D-061)"
        )

    def test_column_synonyms_name_hostname_split(self, db_id):
        """D-061: 서버명→cmm_resource.name, 호스트명→cmm_resource.hostname 구분 존재."""
        cs = _load(db_id).get("column_synonyms", {}) or {}
        name_words = set((cs.get("cmm_resource.name") or {}).get("words", []) or [])
        host_words = set((cs.get("cmm_resource.hostname") or {}).get("words", []) or [])
        assert "서버명" in name_words, f"{db_id}: 서버명→cmm_resource.name 매핑 누락"
        assert "호스트명" in host_words, f"{db_id}: 호스트명→cmm_resource.hostname 매핑 누락"

    def test_has_monthly_metric_example(self, db_id):
        """D-066: 단일/멀티 DB SQL 생성이 참조할 월별 사용률 피벗 예시(cmm_metric_stat_m) 존재."""
        examples = _load(db_id).get("query_examples", []) or []
        assert examples, f"{db_id}: query_examples 없음(폼필 metric 조인 환각 위험)"
        sql_all = " ".join(e.get("sql", "") for e in examples)
        assert "cmm_metric_stat_m" in sql_all, (
            f"{db_id}: 월별 metric 피벗 예시(cmm_metric_stat_m) 없음"
        )

    def test_examples_have_no_hallucinated_metric(self, db_id):
        """RC2/RC3: 예시 SQL 자체가 환각 패턴을 쓰면 안 됨(예시가 오염되면 전파됨)."""
        sql_all = " ".join(
            e.get("sql", "") for e in _load(db_id).get("query_examples", []) or []
        )
        # 실제 지표 구분은 definition_name='Utilization' + resource_type. 아래는 환각.
        assert "definition_name = 'CPU'" not in sql_all, f"{db_id}: 환각 definition_name='CPU'"
        assert "'CPUUtilization'" not in sql_all, f"{db_id}: 환각 'CPUUtilization'"
        assert "cpu_avg_val" not in sql_all, f"{db_id}: field_mapper가 지어내던 가짜 컬럼 cpu_avg_val"


# ── B-2: gp/yd near-duplicate 등가성 ──────────────────────────────────────────

def _content_lines(db_id: str) -> list[str]:
    """주석/빈 줄을 제거한 의미 있는 라인만 반환(주석 변경 노이즈 제거)."""
    result: list[str] = []
    with open(_PROFILE_DIR / f"{db_id}.yaml", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if not line.strip() or line.strip().startswith("#"):
                continue
            result.append(line)
    return result


def _is_allowlisted_diff(diff_line: str) -> bool:
    """gp/yd 간 정당한(알려진) 차이인지 판정.

    현재 허용: OSParameter EAV synonyms (yd에 'OS설정' 한 항목 추가). 그 외 실질 차이는 불허.
    """
    body = diff_line[1:]  # 선행 +/- 제거
    return "OS파라미터" in body


def test_gp_yd_only_allowlisted_diffs():
    """B-2: gp/yd는 사실상 쌍둥이(567줄 중 실질 1줄 차이) — allowlist 외 divergence 금지.

    gp만 고치고 yd를 빠뜨리면(또는 반대) 즉시 실패한다(RC2b 같은 한쪽-수정 재발 차단).
    의도적으로 둘을 다르게 만들 때만 _is_allowlisted_diff에 규칙을 추가한다.
    """
    gp = _content_lines("polestar_cm_gp")
    yd = _content_lines("polestar_cm_yd")
    diff = [
        line
        for line in difflib.unified_diff(gp, yd, lineterm="")
        if line[:1] in "+-" and line[:3] not in ("+++", "---")
    ]
    unexpected = [line for line in diff if not _is_allowlisted_diff(line)]
    assert not unexpected, (
        "gp/yd 프로필에 예상치 못한 divergence(한쪽만 수정?):\n" + "\n".join(unexpected)
    )
