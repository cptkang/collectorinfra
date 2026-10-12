"""plans/150 W3 — ITAM 지식 자산 교정(F3 · G-3 (a)) 고정.

- K1 g05: 「서비스 조건이 없으면 `tcdmsif80` 하나 · `tcdmsif72` 조인 금지」 문장이
  「서비스 조건이 함께 필요하면 …」 문장 **앞**에 있고, 행 불어남은 (관측) 사실 문장이다.
- K4: 마트 단독 규칙 s11(펜스 없음) · s10 일반어 목록이 본체 `GENERIC_NAME_WORDS`와 같다.
- K2: 변형 2건은 `tcdmsif80` 한 테이블 · 서버별 한 행(`서버호스트명` 묶음)이고, 질문이 기존
  closed 시나리오·6회차 세트와 겹치지 않는다(겹침 가드와 같은 정규화·임계).
- K3: `tcdmsif72.담당부점코드` — 비어 있음(관측) · 부서 후보 `tcdmsif80` `담당부점명`(추정).
- 빌드 산출(`config/`)이 원천과 같은 글을 싣는다.

LLM·DB 0.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from src.nodes.name_match_retry import GENERIC_NAME_WORDS
from tests.test_scripts import test_itam_bench_plan150_r6_overlap as ov

ROOT = Path(__file__).resolve().parents[2]
KNOW = ROOT / "testdata/itam_bench/closed/knowledge"
PROFILE = ROOT / "config/db_profiles/itam.yaml"
TEMPLATE = ROOT / "config/knowledge/itam/prompt_template.yaml"
DESCRIPTIONS = ROOT / "config/knowledge/itam/column_descriptions.yaml"
RUN5 = "20261008-163344"
NEW_K2 = ("k2-maint-end-window-by-server", "k2-acquired-years-by-server")


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _norm(text: str) -> str:
    return " ".join(str(text).split())


def _items(name: str) -> dict[str, dict]:
    items = _load(KNOW / f"{name}.yaml")["items"]
    return {i["id"]: i for i in items if i.get("status") == "active"}


def test_k1_g05_no_join_rule_precedes_service_join() -> None:
    g05 = _norm(_items("guide")["g05-asset-maintenance"]["text"])
    no_join = g05.index(
        "서비스 조건이 없는 서버 단위 취득·계약·경과 연수 질문은 `tcdmsif80` 하나로 답하고"
    )
    service = g05.index("서비스 조건이 함께 필요하면")
    assert no_join < service
    assert "`tcdmsif72`를 조인하지 않는다" in g05[no_join:service]
    assert "호스트당 행이 불어난다(관측)" in g05[no_join:service]
    assert _items("guide")["g05-asset-maintenance"]["evidence"] == RUN5
    assert _items("guide")["g03-server"]["evidence"] == RUN5


def test_k4_mart_rule_and_generic_words_aligned() -> None:
    rules = _items("prompt_section")
    s11 = rules["s11-server-mart"]["text"]
    assert "`tcdmsif80` 하나로 답하고" in s11 and "`tcdmsif72`를 조인하지 않는다" in s11
    assert "```" not in s11
    words = set(re.findall(r"「([^」]+)」", rules["s10-name-match"]["text"]))
    assert words == set(GENERIC_NAME_WORDS)


@pytest.mark.parametrize("item_id", NEW_K2)
def test_k2_variant_is_mart_only_one_row_per_server(item_id: str) -> None:
    item = _items("examples")[item_id]
    assert item["tables"] == ["tcdmsif80"] and item["evidence"] == RUN5
    sql = _norm(item["sql"])
    assert "tcdmsif72" not in sql and " JOIN " not in sql.upper()
    assert re.search(r"GROUP BY \w+\.`서버호스트명`", sql)


@pytest.mark.parametrize("item_id", NEW_K2)
def test_k2_variant_question_not_copied(item_id: str) -> None:
    """새 K2 질문이 기존 closed 시나리오·6회차 세트와 같거나 임계 이상으로 닮지 않았다."""
    question = _items("examples")[item_id]["question"]
    refs = ov._scenario_prompts(ov.CLOSED_PATH) + ov._scenario_prompts(ov.R6_PATH)
    hits = [
        (label, round(ov._jaccard(question, ref), 3))
        for label, ref in refs
        if ov._normalize(question) == ov._normalize(ref)
        or ov._jaccard(question, ref) >= ov.JACCARD_LIMIT
    ]
    assert not hits, hits
    # 기간·연수도 기존·6회차 세트에 쓰인 값과 다르다
    numbers = set(re.findall(r"\d+", question))
    used = {n for _, ref in refs for n in re.findall(r"\d+", ref)}
    assert numbers and not numbers & used


def test_k3_dept_code_candidate() -> None:
    # plans/151 W12(G-11 (a)) — 확인 run에서 값 0이 확정돼 br01 풀이를 빼고
    # 80 `담당부점명`으로 고정했다(g07과 같음).
    item = _items("descriptions")["tcdmsif72.담당부점코드"]
    text = item["text"]
    assert "값이 비어 있다(관측" in text and "tcdmsif80의 담당부점명" in text
    assert "tcdmsbr01" not in text
    assert len(text) <= 200 and item["evidence"] == "20261012-141107"


def test_build_carries_sources() -> None:
    guide = _norm(_load(PROFILE)["query_guide"])
    for item_id in ("g03-server", "g05-asset-maintenance"):
        assert _norm(_items("guide")[item_id]["text"]) in guide
    section = _norm(_load(TEMPLATE)["section"])
    for item_id in ("s10-name-match", "s11-server-mart"):
        assert _norm(_items("prompt_section")[item_id]["text"]) in section
    built_ids = {e["id"] for e in _load(PROFILE)["query_examples"]}
    assert set(NEW_K2) <= built_ids
    built = _load(DESCRIPTIONS)["descriptions"]["tcdmsif72.담당부점코드"]
    assert _norm(built) == _norm(_items("descriptions")["tcdmsif72.담당부점코드"]["text"])
