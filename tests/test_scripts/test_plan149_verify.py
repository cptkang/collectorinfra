"""plans/149 W1·W2·W4·W5 독립 검증(verifier) — 기능 정확성.

- W1·W2: 원천(`testdata/itam_bench/closed/{knowledge,table_definitions}`)과 빌드 산출
  (`config/db_profiles/itam.yaml` · `config/knowledge/itam/*`)이 같은 글을 싣는다 · 서비스 연결
  1·2순위 문장 · `용도내용` 범위 · 「사용률」 규칙(`utilization_issues`) 위반 0 ·
  `tcdmsif80` manages에 EOS 없음(의도).
- W4 (3): 한글·백틱 식별자 인식이 영문(ASCII) SQL에서는 예전 토큰화와 같은 결과(회귀 없음).
- W5: closed 정책 77칸 · 등급 분포(identifier 12) · 사본 실존.

값 반출·LLM·DB는 쓰지 않는다.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pytest
import yaml

import scripts.itam_bench.catalog as cat
import scripts.itam_bench.judge as jd
from src.domain.knowledge_assets import utilization_issues

ROOT = Path(__file__).resolve().parents[2]
KNOW = ROOT / "testdata/itam_bench/closed/knowledge"
DEFS = ROOT / "testdata/itam_bench/closed/table_definitions.yaml"
PROFILE = ROOT / "config/db_profiles/itam.yaml"
TEMPLATE = ROOT / "config/knowledge/itam/prompt_template.yaml"
DESCRIPTIONS = ROOT / "config/knowledge/itam/column_descriptions.yaml"
CLOSED_POLICY = ROOT / "testdata/itam_bench/column_policy.closed.yaml"
CLOSED_SCHEMA = ROOT / "testdata/itam_bench/closed/itam_schema.json"
ORACLES = ROOT / "testdata/scenarios/oracles"


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _norm(text: str) -> str:
    return " ".join(str(text).split())


def _items(name: str) -> dict[str, dict]:
    items = _load(KNOW / f"{name}.yaml")["items"]
    return {i["id"]: i for i in items if i.get("status") == "active"}


# ──────────────────────────────────────────────
# W1·W2 — 원천 ↔ 빌드 산출
# ──────────────────────────────────────────────

def test_k3_descriptions_build_matches_source():
    built = _load(DESCRIPTIONS)["descriptions"]
    src = _items("descriptions")
    assert len(built) == len(src)
    for item in src.values():
        assert _norm(built[f"{item['table']}.{item['column']}"]) == _norm(item["text"]), item["id"]


@pytest.mark.parametrize("item_id", ["g03-server", "g05-asset-maintenance", "g06-service"])
def test_k1_changed_items_in_profile_guide(item_id):
    guide = _norm(_load(PROFILE)["query_guide"])
    assert _norm(_items("guide")[item_id]["text"]) in guide


def test_k4_s07_in_prompt_template():
    section = _norm(_load(TEMPLATE)["section"])
    assert _norm(_items("prompt_section")["s07-service-filter"]["text"]) in section


@pytest.mark.parametrize("table", ["tcdmsgt82", "tcdmsif80"])
def test_w2_manages_build_matches_source(table):
    src = _load(DEFS)["tables"][table]["manages"]
    built = _load(PROFILE)["table_definitions"][table]["manages"]
    assert _norm(built) == _norm(src)


def test_service_order_first_direct_then_gt82():
    """1순위 = 서버 원장 두 칸 OR LIKE 직접 · 2순위 = 1순위 0행일 때만 `tcdmsgt82`."""
    for text in (_items("guide")["g06-service"]["text"],
                 _items("prompt_section")["s07-service-filter"]["text"]):
        t = _norm(text)
        first, second = t.index("1순위"), t.index("2순위")
        assert first < second
        assert "그룹경로내용" in t[first:second] and "구성항목설명내용" in t[first:second]
        assert "OR" in t[first:second] and "LIKE" in t[first:second]
        assert "1순위가 0행일 때만" in t[second:] and "tcdmsgt82" in t[second:]


def test_usage_column_scoped_to_usage_questions():
    for text in (_items("guide")["g06-service"]["text"],
                 _items("prompt_section")["s07-service-filter"]["text"],
                 _items("descriptions")["tcdmsif72.용도내용"]["text"]):
        t = _norm(text)
        assert "용도내용" in t or "용도 자유 기재" in t
        assert "용도를 지목할 때만" in t
        assert "서비스" in t and ("쓰지 않는다" in t)


def test_no_utilization_rule_violation_in_knowledge():
    hits = {
        f"{name}:{iid}": utilization_issues(item["text"])
        for name in ("guide", "prompt_section", "descriptions")
        for iid, item in _items(name).items()
    }
    assert {k: v for k, v in hits.items() if v} == {}


@pytest.mark.parametrize("table,head", [
    ("tcdmsgt82", "현업 주무 부점/담당자."),
    ("tcdmsif80", "한 행에 모음."),
])
def test_w2_added_sentence_has_no_utilization(table, head):
    manages = _norm(_load(DEFS)["tables"][table]["manages"])
    added = manages.split(head, 1)[1]
    assert added.strip() and utilization_issues(added) == []


def test_tcdmsif80_manages_has_no_eos():
    manages = _load(DEFS)["tables"]["tcdmsif80"]["manages"]
    assert "EOS" not in manages and "지원 종료" not in manages and "지원종료" not in manages


# ──────────────────────────────────────────────
# W4 (3) — 영문(ASCII) SQL 토큰화 회귀 없음
# ──────────────────────────────────────────────

def _old_columns(sql: str, cols) -> list[str]:
    body = jd._DOUBLE_LITERAL.sub(" ", jd.strip_literals(sql))
    present = {t.casefold() for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", body)}
    return sorted({c for c in cols if c.casefold() in present})


def _old_joined(sql: str) -> set[str]:
    body = jd.strip_literals(sql)
    return {m.group(2).casefold()
            for m in re.finditer(r"\b\w+\.(\w+)\s*=\s*\w+\.(\w+)\b", body)
            if m.group(1).casefold() == m.group(2).casefold()}


def _new_joined(sql: str) -> set[str]:
    body = jd.strip_literals(sql)
    return {jd._unquote(m.group(2)).casefold() for m in jd._JOIN_EQUAL.finditer(body)
            if jd._unquote(m.group(1)).casefold() == jd._unquote(m.group(2)).casefold()}


def _ascii_oracles() -> list[Path]:
    return [p for p in sorted(ORACLES.glob("*.sql"))
            if jd.strip_comments(p.read_text(encoding="utf-8")).isascii()]


def test_ascii_corpus_nonempty():
    assert len(_ascii_oracles()) >= 20


@pytest.mark.parametrize("path", _ascii_oracles(), ids=lambda p: p.name)
def test_ascii_sql_same_tokens_as_before(path):
    sql = path.read_text(encoding="utf-8")
    cols = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", sql))
    assert jd.sql_columns(sql, cols) == _old_columns(sql, cols)
    assert _new_joined(sql) == _old_joined(sql)


def test_korean_prefix_ascii_not_split():
    """`IP주소내용`에서 `IP`를 따로 떼지 않는다(예전 토큰화의 오탐 제거)."""
    sql = "SELECT s.`IP주소내용` FROM `tcdmsif72` s WHERE s.`CPU개수` > 4"
    assert jd.sql_columns(sql, {"IP", "IP주소내용", "CPU개수"}) == ["CPU개수", "IP주소내용"]


# ──────────────────────────────────────────────
# W5 — closed 정책 재키잉
# ──────────────────────────────────────────────

def test_closed_policy_grades_and_existence():
    policy = cat.load_policy(CLOSED_POLICY)
    grades = Counter(g for cols in policy.tables.values() for g in cols.values())
    assert sum(grades.values()) == 77
    assert grades["identifier"] == 12
    schema = json.loads(CLOSED_SCHEMA.read_text(encoding="utf-8"))
    tables = schema["schema"]["tables"]
    for table, cols in policy.tables.items():
        real = {c["name"] for c in tables[table]["columns"]}
        assert set(cols) <= real, (table, sorted(set(cols) - real))
    # 영문 벤더 변수명(1회차 정책 이름)이 남지 않았다
    assert not any(re.fullmatch(r"[a-z][A-Za-z0-9]+", c)
                   for cols in policy.tables.values() for c in cols)


def test_closed_identifier_set():
    policy = cat.load_policy(CLOSED_POLICY)
    ids = {(t, c) for t, cols in policy.tables.items()
           for c, g in cols.items() if g == "identifier"}
    assert ("tcdmsif80", "서버호스트명") in ids and ("tcdmsif79", "서버호스트명") in ids
    # IP는 network(식별자 등급 아님) · 그룹회사코드는 코드라 general
    assert policy.tables["tcdmsif80"]["IP주소내용"] == "network"
    assert policy.tables["tcdmsif80"]["그룹회사코드"] == "general"
    assert "identifier" in cat.KEY_GRADES and "pii" not in cat.KEY_GRADES
