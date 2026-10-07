"""plans/143 · D-316 — K6 활성 여부 문장(2회차 개정) 독립 검증.

- 새 문장은 코드값 블록에 활성 값이 있을 때만 거르고, 없으면 칸을 보여 준다(옛 무조건 지시 부재).
- 문장에 값 리터럴이 없다(D-301 ② · D-316 값 0).
- 커밋된 `config/db_profiles/itam.yaml`의 K6 문장이 현재 코드 파생 결과와 같다(빌더 산출 낡음 방지).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from src.domain import knowledge_assets as ka

_ROOT = Path(__file__).resolve().parents[2]
_PROFILE = _ROOT / "config" / "db_profiles" / "itam.yaml"

_COLUMNS = {"zzab01": ["그룹코드", "활성화여부"], "zzab02": ["활성여부"]}
_DEFS = {"zzab01": {"kind": "현행"}, "zzab02": {"kind": "현행"}}


def _active_rule(defs: dict, columns: dict) -> str:
    rules = ka.derive_kind_rules(defs, columns, allowed=sorted(columns))
    assert rules, "활성 여부 규칙이 파생되지 않았다"
    return rules[0]


class TestActiveRuleWording:
    def test_conditional_on_guidance(self) -> None:
        rule = _active_rule(_DEFS, _COLUMNS)
        assert "`zzab01.활성화여부`" in rule and "`zzab02.활성여부`" in rule
        # 「있으면 거른다」가 「없으면 거르지 않는다」보다 먼저, 둘 다 있다
        has = rule.index("활성 값이 있으면")
        lacks = rule.index("활성 값이 없으면")
        assert has < lacks
        assert "짐작해 조건을 걸지 말고" in rule[lacks:]
        assert "거르지 않은 채" in rule[lacks:]
        # 옛 무조건 지시(「이 칸으로 활성 행만 고른다:」)가 돌아오지 않는다
        assert "이 칸으로 활성 행만 고른다" not in rule

    def test_no_value_literals(self) -> None:
        rule = _active_rule(_DEFS, _COLUMNS)
        assert not re.search(r"'[^']*'|\"[^\"]*\"", rule)
        prose = re.sub(r"`[^`]*`", "", rule)  # 칸 참조를 빼고 본문만
        assert not re.search(r"(?<![0-9A-Za-z])[0-9YN](?![0-9A-Za-z])", prose)
        assert ka.text_form_issues(rule) == []


class TestCommittedProfileInSync:
    def test_profile_active_rule_matches_code(self) -> None:
        profile = yaml.safe_load(_PROFILE.read_text(encoding="utf-8"))
        committed = [r for r in profile.get("query_rules") or [] if "활성 여부 칸" in r]
        assert len(committed) == 1, "itam.yaml에 K6 활성 여부 문장이 정확히 1건이어야 한다"
        refs = re.findall(r"`(tcdms[a-z]{2}\d+)\.(활성화여부|활성여부)`", committed[0])
        assert refs, "활성 여부 칸 참조가 없다"
        defs = profile.get("table_definitions") or {}
        for table, _ in refs:  # 참조 테이블은 정의상 `현행`이어야 한다
            assert (defs.get(table) or {}).get("kind") == "현행", table
        columns: dict[str, list[str]] = {}
        for table, column in refs:
            columns.setdefault(table, []).append(column)
        derived = _active_rule({t: {"kind": "현행"} for t in columns}, columns)
        assert committed[0] == derived
