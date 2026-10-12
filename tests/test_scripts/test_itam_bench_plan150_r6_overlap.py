"""ITAM 6회차 신규 프롬프트 세트 겹침 가드 (plans/150 W9 4).

6회차 세트(`scenarios.closed.r6.yaml`)는 「처음 보는 사용자 프롬프트」로
일반화를 재려고 만든다. 그래서 r6의 모든 턴 프롬프트가 기존 closed 시나리오
(`scenarios.closed.yaml` · 회귀 세트로 보존)와 K2 질문
(`closed/knowledge/examples.yaml`)에 대해 정규화 문자열이 같거나
낱말 Jaccard가 임계 이상이면 실패한다. r6 안의 서로 간 겹침은 보지 않는다
(219·229 · 211·222처럼 주제를 잇는 것은 의도한 대조). K2는 뒤 Wave(W3)가
예시를 더하므로 실행 시점에 파일에서 읽는다 — 새 K2 질문이 r6 문구와
겹쳐도 여기서 걸린다.

임계 근거(2026-10-08 실측 · 정규화 = NFKC·casefold·문장부호 제거 ·
낱말 = 공백 분리):
- 기존 closed 시나리오끼리(19턴) 최대 0.714 — ITAM-104 t1 ↔ ITAM-112 t1
  (「자산관리 시스템에서 ○○별 서버 수 알려줘」 틀에서 집계 축 한 낱말만
  다름). 다음이 0.625(112↔116 · 104↔116 — 같은 틀).
- K2 질문을 넣어도 최대는 같다(K2 ↔ closed 최대 0.333 —
  k2-server-count-by-group-path ↔ ITAM-116 t1).
- 기존 세트의 0.625~0.714는 축 한 낱말만 바꾼 틀 복사다. 6회차가 피해야 할
  바로 그 겹침이라 임계는 그보다 낮아야 한다 → 계획 기본 0.6을 그대로 둔다
  (`test_threshold_catches_existing_template_copy`가 그 근거를 고정).
- 이때 r6 최대: 기존 closed 대비 0.5(ITAM-214 t1 ↔ ITAM-106 t1) · K2 대비
  0.429(ITAM-215 t1 ↔ k2-server-count-by-os). 정규화 완전 일치 0.

서비스 이름 자리표(2026-10-12 · 검증 Medium 6 권고): 낱말 Jaccard는 서비스 이름만
바꾼 꼴에 둔감하다(옛 221 「무인공과금 업무 스토리지를 쓰는 서버들 …」 ↔ 118
「… 통합인증 업무 스토리지를 쓰는 서버들의 …」 0.45). 그래서 비교 전에 시나리오에
쓰인 서비스 이름 5개를 한 자리표로 바꾼다 — 옛 221은 0.6으로 걸린다
(`test_service_name_swap_is_caught`).
"""

from __future__ import annotations

import itertools
import re
import unicodedata
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
BENCH = REPO / "testdata" / "itam_bench"
R6_PATH = BENCH / "scenarios.closed.r6.yaml"
CLOSED_PATH = BENCH / "scenarios.closed.yaml"
K2_PATH = BENCH / "closed" / "knowledge" / "examples.yaml"

#: 낱말 Jaccard 임계(이상이면 겹침) — 근거는 모듈 독스트링
JACCARD_LIMIT = 0.6

#: 기존 세트(통합인증·인터넷뱅킹)와 r6(G-5 답 3개)의 서비스 이름 — 비교 전 한 자리표로 바꾼다
SERVICE_NAMES = ("무인공과금", "법원공탁금", "그린뱅크", "통합인증", "인터넷뱅킹")


def _normalize(text: str) -> str:
    """NFKC·casefold 뒤 문장부호를 공백으로 바꾸고 공백을 하나로 접는다."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    for name in SERVICE_NAMES:
        folded = folded.replace(name, " 서비스명 ")
    return " ".join(re.sub(r"[^\w\s]", " ", folded).split())


def _words(text: str) -> set[str]:
    return set(_normalize(text).split())


def _jaccard(a: str, b: str) -> float:
    left, right = _words(a), _words(b)
    return len(left & right) / len(left | right) if left | right else 0.0


def _scenario_prompts(path: Path) -> list[tuple[str, str]]:
    """시나리오 파일의 모든 턴 프롬프트 → [(「ID 턴N」, 문구)]."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [
        (f"{s['id']} t{i}", str(turn["send"]["query"]))
        for s in data.get("scenarios") or []
        for i, turn in enumerate(s.get("turns") or [], start=1)
        if (turn.get("send") or {}).get("query")
    ]


def _k2_questions() -> list[tuple[str, str]]:
    """K2 예시 질문(상태 무관 전부) — 실행 시점에 읽는다."""
    data = yaml.safe_load(K2_PATH.read_text(encoding="utf-8")) or {}
    return [
        (str(item.get("id")), str(item["question"]))
        for item in data.get("items") or []
        if item.get("question")
    ]


def _references() -> list[tuple[str, str]]:
    return _scenario_prompts(CLOSED_PATH) + _k2_questions()


def test_inputs_present() -> None:
    """r6 32건 35턴(다중 턴 3건) · 기존 closed 19턴 · K2 질문 1개 이상.

    빈 입력으로 통과하지 않게 한다.
    """
    r6 = _scenario_prompts(R6_PATH)
    assert len(r6) == 35
    assert len({label.split()[0] for label, _ in r6}) == 32
    assert len(_scenario_prompts(CLOSED_PATH)) == 19
    assert _k2_questions()


_R6 = _scenario_prompts(R6_PATH)


@pytest.mark.parametrize("label,prompt", _R6, ids=[label for label, _ in _R6])
def test_r6_prompt_not_copied(label: str, prompt: str) -> None:
    """r6 턴 프롬프트가 기존 closed 시나리오·K2 질문과 같거나 임계 이상으로 닮지 않았다."""
    hits = [
        (ref_label, round(_jaccard(prompt, ref), 3))
        for ref_label, ref in _references()
        if _normalize(prompt) == _normalize(ref) or _jaccard(prompt, ref) >= JACCARD_LIMIT
    ]
    assert not hits, (label, prompt, hits)


def test_service_name_swap_is_caught() -> None:
    """서비스 이름만 바꾼 옛 221 문구는 118에 걸린다(자리표 — 검증 Medium 6)."""
    closed = dict(_scenario_prompts(CLOSED_PATH))
    old_221 = "무인공과금 업무 스토리지를 쓰는 서버들 운영체제 버전 알려줘"
    assert _jaccard(old_221, closed["ITAM-118 t1"]) >= JACCARD_LIMIT


def test_threshold_catches_existing_template_copy() -> None:
    """임계 근거 고정 — 기존 세트의 틀 복사(104 ↔ 112: 집계 축 한 낱말만 다름)는 임계에 걸린다.

    기존 closed 세트 안 최대 유사도가 임계 이상이어야 「기존 세트끼리도 이
    정도면 복사」라는 근거가 선다.
    """
    closed = dict(_scenario_prompts(CLOSED_PATH))
    assert _jaccard(closed["ITAM-104 t1"], closed["ITAM-112 t1"]) >= JACCARD_LIMIT
    intra = max(
        _jaccard(a, b) for (_, a), (_, b) in itertools.combinations(_references(), 2)
    )
    assert intra >= JACCARD_LIMIT
