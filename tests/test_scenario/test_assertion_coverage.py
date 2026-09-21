"""단언 이관 결과 — 벤치마크가 실제로 판정할 수 있는가 (2026-09-14).

실 스위프가 아무것도 판정하지 못한 이유는 인증만이 아니었다. 정상군 107건 중
**기계 단언이 있는 시나리오가 1건뿐**이라 정확도 쌍이 언제나 0쌍이었다. 단언을 옮긴
뒤 다시 비어 가는 것을 막는다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts.scenario import runner as runner_mod
from scripts.scenario.catalog import ENDPOINTS, load_catalog

REPO_ROOT = Path(__file__).resolve().parents[2]

#: `_schema.yaml` 이 약속하고 평가기가 실제로 읽는 키.
KNOWN_KEYS = {
    "status", "http_status", "intent", "db_ids", "row_count", "has_file",
    "response_must_contain", "response_must_not_contain", "clarification", "file",
    "sql_must_match", "sql_must_not_match", "column_must_not_map",
    "node_path", "sse_events", "llm_calls", "retries", "gold_sql", "manual_review",
    # 판정 계약 교정 (Y-4·Y-5 · D-218). `row_count` 는 단일 DB 턴 전용이 됐다.
    "row_count_per_db", "row_count_total", "period_covers",
    # 재작성 감사 단언 (plans/94 §19.2 Y-11·Y-12 · plans/107). 하위 키 gate·slots_preserved.
    "rewrite",
}


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


def _normal_closed(catalog):
    # 러너 동작 시나리오(부하 묶음·시드 재적재·선행 상태, D-217)는 자기 턴을 판정하지 않는다 -
    # 묶음은 참조 시나리오의 단언으로, 러너 동작은 러너가 판정한다. 질의 시나리오만 센다.
    return [s for s in catalog.scenarios
            if s.kind == "normal" and s.env in ("closed", "both")
            and not (s.is_bundle or s.action)]


def test_정상군_대부분이_기계_판정_대상이다(catalog) -> None:
    """판정 가능 비율이 떨어지면 스위프는 완주율만 재게 된다."""
    # 러너가 건너뛰는 것(프롬프트 미작성·teardown 미지원)은 실행 대상이 아니다.
    runnable = [s for s in _normal_closed(catalog)
                if s.prompt_authored and not runner_mod._teardown(s)]
    judged = [s for s in runnable
              if any(set(t.expect) - {"manual_review"} for t in s.turns)]

    assert len(runnable) >= 95, "실행 가능 시나리오가 줄었다"
    ratio = len(judged) / len(runnable)
    assert ratio >= 0.85, (
        f"기계 판정 가능 {len(judged)}/{len(runnable)}({ratio:.0%}) — "
        "단언 없는 시나리오가 늘면 정확도 비교가 다시 0쌍이 된다"
    )


def test_단언_키는_평가기가_읽는_것뿐이다(catalog) -> None:
    """오타 키는 조용히 무시된다 - 단언을 쓴 줄 알았는데 아무것도 안 하는 상태가 된다."""
    unknown = {
        (s.id, key)
        for s in catalog.scenarios
        for t in s.turns
        for key in t.expect
        if key not in KNOWN_KEYS
    }
    assert not unknown, f"평가기가 읽지 않는 키: {sorted(unknown)}"


def test_정규식_단언은_전부_컴파일된다(catalog) -> None:
    for scenario in catalog.scenarios:
        for index, turn in enumerate(scenario.turns, start=1):
            for key in ("sql_must_match", "sql_must_not_match"):
                for pattern in turn.expect.get(key) or []:
                    re.compile(pattern)   # 실패하면 그 자리에서 터진다


def test_업로드_파일은_전부_실재한다(catalog) -> None:
    """H군 17건이 전부 같은 샘플을 올리고 있었다 - 양식 해석을 시험하지 못했다."""
    missing = [
        (s.id, s.upload) for s in catalog.scenarios
        if s.upload and not (REPO_ROOT / s.upload).exists()
    ]
    assert not missing, f"없는 업로드 파일: {missing}"


def test_폼필_양식이_시나리오마다_구별된다(catalog) -> None:
    forms = {s.id: s.upload for s in catalog.scenarios
             if s.group == "H" and s.upload}
    assert len(set(forms.values())) >= 5, (
        f"H군이 {len(set(forms.values()))}종 양식만 쓴다 — "
        "같은 파일을 올리면 양식별 해석을 구별하지 못한다"
    )


def test_턴_엔드포인트는_정의된_값뿐이다(catalog) -> None:
    for scenario in catalog.scenarios:
        for turn in scenario.turns:
            assert turn.endpoint is None or turn.endpoint in ENDPOINTS


def test_폼필_답변턴은_JSON_경로로_간다(catalog) -> None:
    """`/query/file` 은 form_fill_answers 를 Form 파라미터로 받지 않는다."""
    offenders = [
        (s.id, i) for s in catalog.scenarios
        for i, t in enumerate(s.turns, start=1)
        if "form_fill_answers" in t.send
        and (t.endpoint or s.endpoint) not in ("stream", "plain")
    ]
    assert not offenders, f"파일 엔드포인트로 보내는 답변 턴: {offenders}"


def test_미작성_시나리오는_사유를_남긴다() -> None:
    """'원문이 산문이다'로는 다음 사람이 무엇을 해야 할지 알 수 없다.

    K군 7건은 2026-09-15 러너 동작(replay·concurrent·setup)으로 실행 가능해져 미작성이 0건이다(D-217).
    다시 미작성이 생기면 건마다 구체 사유(`# 실행 불가:`)가 붙어야 한다.
    """
    text = (REPO_ROOT / "testdata" / "scenarios" / "k_load.yaml").read_text(encoding="utf-8")
    generic = text.count("원문이 프롬프트가 아니라 산문")
    specific = text.count("# 실행 불가:")
    unauthored = [s for s in load_catalog().scenarios if s.group == "K" and not s.prompt_authored]
    assert generic == 0 and specific >= len(unauthored), (
        f"구체 사유 {specific}건 · 일반 문구 {generic}건 · 미작성 {len(unauthored)}건"
    )
