"""폴스타 프로필 4종의 「장비명 → cmm_resource.name」 지시 대칭 회귀 방어 (plans/98 CU-1 · §6).

P-1(장비명이 `hostname`으로 새는 조용한 오답, 6/6 재현)의 원인은 LLM 흔들림이 아니라
**프로필 비대칭**이었다 — gp/yd/polestar에는 강제 지시·예시 SQL·피벗 HAVING 규칙이 있는데
`polestar_b0.yaml`에는 개념 설명만 있었고, B-04/K-01의 생성 SQL 6건이 전부 대문자
`POLESTAR.` 스키마(=b0)로 라우팅돼 `hostname` 필터로 떨어졌다.

`CLAUDE.md` 「단일/멀티 경로 대칭 — 한쪽만 고치는 비대칭이 반복 원인」의 교과서 사례이므로,
**네 프로필 중 하나라도 블록이 빠지면 깨지게** 고정한다. 문구 전체가 아니라 지시의 핵심
문장/구조만 단언해 정상적인 문구 다듬기까지 막지는 않는다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

PROFILE_DIR = Path(__file__).resolve().parents[1] / "config" / "db_profiles"

#: 장비명 지시가 반드시 함께 있어야 하는 폴스타 프로필 4종.
POLESTAR_PROFILES = ("polestar_b0", "polestar_cm_gp", "polestar_cm_yd", "polestar")

#: B-04의 `sql_must_match`와 같은 판정 — `hostname =`은 단어 경계 때문에 걸리지 않는다.
_NAME_EQUALS_RE = re.compile(r"\bname\s*=\s*'", re.IGNORECASE)


def _raw(db_id: str) -> str:
    return (PROFILE_DIR / f"{db_id}.yaml").read_text(encoding="utf-8")


def _profile(db_id: str) -> dict:
    return yaml.safe_load(_raw(db_id))


@pytest.mark.parametrize("db_id", POLESTAR_PROFILES)
class TestEquipmentNameDirectiveSymmetry:
    """네 프로필이 같은 지시 3종(재매핑 강제·호스트명 예외·피벗 HAVING)을 갖는다."""

    def test_filter_conditions_remap_block_present(self, db_id):
        """filter_conditions.field가 hostname이어도 name으로 재매핑하라는 강제 지시."""
        guide = _profile(db_id).get("query_guide") or ""
        assert "filter_conditions 필드명 재매핑" in guide, (
            f"{db_id}: 재매핑 강제 지시 블록이 없다 — P-1 재발 조건"
        )
        assert "예외 없이 적용" in guide, f"{db_id}: 재매핑이 강제(예외 없음)로 적혀 있지 않다"

    def test_output_column_does_not_relax_the_filter(self, db_id):
        """호스트명·IP를 출력으로 함께 요청받아도 **필터**는 name을 유지한다."""
        guide = _profile(db_id).get("query_guide") or ""
        assert "서버 식별 필터는 절대 hostname 으로 하지 말 것" in guide, db_id

    def test_explicit_hostname_request_is_the_only_exception(self, db_id):
        """"호스트명이 XXX인"이라고 **명시**한 경우에만 hostname 필터를 허용한다."""
        guide = _profile(db_id).get("query_guide") or ""
        assert "호스트명/호스트네임이 XXX인 서버" in guide, db_id

    def test_pivot_identity_uses_having_not_where(self, db_id):
        """단일 서버 피벗의 서버 식별은 WHERE가 아니라 HAVING이다(사유까지 함께)."""
        raw = _raw(db_id)
        assert "server.Cpus/server.Memory 행이 GROUP BY 전에" in raw, (
            f"{db_id}: WHERE 배치가 CPU·메모리를 NULL로 만든다는 사유가 없다"
        )
        assert "HAVING" in raw, f"{db_id}: 피벗 HAVING 규칙이 없다"

    def test_example_sql_filters_on_name(self, db_id):
        """장비명을 name으로 거르는 예시 SQL이 최소 1건 있다(few-shot이 지시와 같은 방향)."""
        examples = _profile(db_id).get("query_examples") or []
        matched = [e for e in examples if _NAME_EQUALS_RE.search(e.get("sql") or "")]
        assert matched, f"{db_id}: `name = '<값>'`로 서버를 거르는 예시 SQL이 없다"


class TestBankZoneComparisonShape:
    """b0(은행존)는 등호 단독이 아니라 OR 3분기다 — gp/yd 단순 복사 금지.

    b0의 `name`은 "<호스트명>(<설명>)" / "<호스트명> (<설명>)" 구조(프로필 자체 실측 기록)라
    gp/yd의 `WHERE r.name = '<값>'` 등호를 그대로 이식하면 **0건 반환**으로 바뀔 뿐이다.
    확정 형태: `(r.name = '<값>' OR r.name LIKE '<값> (%' OR r.name LIKE '<값>(%')`
      - 등호 결과의 초집합이라 현행보다 나빠지지 않는다
      - 여는 괄호 앞 경계를 못 박아 'sicwso01'이 'sicwso011'을 잡는 접두 충돌을 막는다
    """

    def test_guide_declares_the_three_branch_form(self):
        guide = _profile("polestar_b0").get("query_guide") or ""
        assert "WHERE (r.name = '<값>' OR r.name LIKE '<값> (%' OR r.name LIKE '<값>(%')" in guide

    def test_guide_forbids_boundaryless_prefix_like(self):
        """경계 없는 `LIKE '<값>%'`는 다른 서버를 섞어 잡으므로 금지가 명시돼야 한다."""
        guide = _profile("polestar_b0").get("query_guide") or ""
        assert "`LIKE '<값>%'`(경계 없는 접두 매칭)은 금지" in guide

    def test_having_rule_uses_the_same_three_branch_form(self):
        guide = _profile("polestar_b0").get("query_guide") or ""
        having = guide[guide.index("단일 서버 피벗"):]
        assert having.count("LIKE '<값>") == 2 and "= '<값>'" in having, having[:400]

    def test_example_sql_uses_the_three_branch_form(self):
        """예시가 지시와 경쟁하지 않는다 — 장비명 예시 SQL이 세 분기를 그대로 보인다."""
        examples = _profile("polestar_b0").get("query_examples") or []
        sqls = [e.get("sql") or "" for e in examples]
        three_branch = [
            s for s in sqls
            if "r.name = 'cob0-bnndbp01'" in s
            and "r.name LIKE 'cob0-bnndbp01 (%'" in s
            and "r.name LIKE 'cob0-bnndbp01(%'" in s
        ]
        assert three_branch, "b0에 OR 3분기 장비명 예시 SQL이 없다"
        # DB2 방언·스키마 한정(대문자 POLESTAR) 준수 — 은행존 예시의 기본 요건.
        sql = three_branch[0]
        assert "FETCH FIRST" in sql and "LIMIT" not in sql, sql
        assert "POLESTAR.cmm_resource" in sql, sql

    def test_no_example_filters_identity_by_hostname_literal(self):
        """b0 예시가 장비명 자리에 `hostname = '<장비명>'`을 쓰지 않는다(P-1 원형)."""
        examples = _profile("polestar_b0").get("query_examples") or []
        for example in examples:
            sql = example.get("sql") or ""
            assert "hostname = 'cob0-" not in sql and "hostname = '조회할_" not in sql, (
                f"장비명을 hostname으로 거르는 예시가 남아 있다: {example.get('question')}"
            )


class TestCU17NoHostnameFilterFewShot:
    """예시 SQL이 장비명을 `hostname` 으로 거르지 않는가 (plans/98 CU-17).

    CU-1이 강제 지시를 넣어도 **같은 프로필의 few-shot 이 반대를 보이면 그쪽이 이긴다** —
    `CLAUDE.md` 「프롬프트 강제가 프로필 few-shot 예시와 경쟁하면 반복 실패한다」.
    `"특정 서버의 CPU 리소스를 조회해줘"` 예시가 네 프로필 전부 `WHERE parent.hostname =
    '서버명'` 이었다(2026-09-16 실측) — 장비명을 hostname 으로 거르는 교본이었다.
    """

    #: 「특정 서버를 값으로 지목」하는 질문. 이 예시의 필터는 name 이어야 한다.
    _SERVER_SCOPED_QUESTION = "특정 서버의 CPU 리소스를 조회해줘"

    @pytest.mark.parametrize("db_id", POLESTAR_PROFILES)
    def test_no_hostname_filter_in_server_scoped_example(self, db_id):
        examples = _profile(db_id).get("query_examples") or []
        target = [e for e in examples if e.get("question") == self._SERVER_SCOPED_QUESTION]
        assert target, f"{db_id}: '{self._SERVER_SCOPED_QUESTION}' 예시가 사라졌다"
        for example in target:
            sql = example.get("sql") or ""
            # 주석(`-- ... hostname 이 아니다`)은 제외하고 실행문만 본다.
            statement = "\n".join(
                ln for ln in sql.splitlines() if not ln.strip().startswith("--")
            )
            assert not re.search(r"WHERE\s+\w+\.hostname\s*=", statement, re.IGNORECASE), (
                f"{db_id}: 장비명을 hostname 으로 거르는 few-shot 이 남아 있다 — P-1 재생산 경로"
            )
            assert _NAME_EQUALS_RE.search(statement), f"{db_id}: name 필터가 없다"

    @pytest.mark.parametrize("db_id", POLESTAR_PROFILES)
    def test_b0_uses_or_form_others_use_equality(self, db_id):
        """b0 등록명은 `"<호스트명>(<설명>)"` 구조라 등호 단독이면 0건이다(CU-1과 같은 형태)."""
        examples = _profile(db_id).get("query_examples") or []
        sql = "\n".join(
            e.get("sql") or "" for e in examples
            if e.get("question") == self._SERVER_SCOPED_QUESTION
        )
        if db_id == "polestar_b0":
            assert "LIKE '장비명 (%'" in sql and "LIKE '장비명(%'" in sql, sql
        else:
            assert "LIKE" not in sql.split("WHERE")[-1].split(";")[0], db_id
