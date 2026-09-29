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
from scripts.scenario.catalog import ENDPOINTS, Catalog, Scenario, load_catalog

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
    # 선택지형 응답 단언 (plans/120 U-4 · H-06).
    "response_must_contain_any",
    # 계획 구조 단언 (plans/121 TP-0.3). 하위 키는 catalog.PLAN_KEYS 가 로드 시점에 검사한다.
    "plan",
    # 관측값 단언 (plans/122 H-5) · 결과 행 단언 (H-1). 하위 키(`stream`·`result`·`file`·
    # `period_covers`)는 catalog 의 *_KEYS 가 로드 시점에 검사한다.
    "sql_executed", "node_path_must_not", "status_any", "form_memory_panel", "stream",
    "dependency_notes_contains", "result",
    # 실 DB 오라클 단언 (plans/122 O-2) — 평가기 `_check_oracle` 과 같은 변경으로 넣는다
    # (D-275 주의 ③ · §9.2 「조용한 통과 방지」). 하위 키·정본 파일·엔진 커버리지는 로더가
    # `oracle.validate_oracle_spec` 로 검사한다.
    "oracle",
    # 응답 고지 kind 단언 (plans/123 V-1 · W-8) — 어휘는 catalog.DISCLOSURE_KIND_GRADES 가 로드
    # 시점에 검사한다.
    "disclosures_contains",
}


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


def _normal_closed(catalog: Catalog) -> list[Scenario]:
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


#: 선언하면 관측 수단이 없어 **늘 보류**되는 키(assertions.evaluate_turn) — `manual_review`
#: 처럼 합격을 막는다. `intent` 는 스트림 done 에 없고, `llm_calls` 는 호출 수가 실리지 않고,
#: `gold_sql` 은 러너가 실행하지 않는다(eval_text2sql 별도 판정).
STRUCTURALLY_HELD_KEYS = frozenset({"intent", "llm_calls", "gold_sql"})

#: 「합격 가능」 비율 하한(plans/122 J-2) — 현 실측 42/104(40.4%)을 소수 둘째 자리에서 내림해
#: 고정했다
#: (최종 하한 0.90 으로 먼저 돌려 실패를 확인함 · Prove-It 2026-09-29).
#: 단계 하한은 이관 커밋과 함께 올린다(122 §6: 0.55 → 0.70 → 0.90).
#: 이력(현 작업 트리 카탈로그 실측 · 소수 둘째 자리 내림):
#:   - 단계 0(2026-09-29) 42/104 = 40.4% → 0.40
#:   - 카탈로그 v2 이관 CatA(A~D군)·CatB(F~L군) 뒤 74/103 = 71.8%(하한은 올리지 않았다)
#:   - 하네스 보완(refine · 2026-09-29 — H-4 다단 머리글 · value_range 별칭 · K-3 H-18 미작성 전환)
#:     뒤 78/102 = 76.5% → **0.76**. 이관 +4(B-12 · H-06 · H-15 · H-16) · 모집단 −1(H-18 미작성).
#:     단계 3 하한 0.70 은 넘었고 단계 5 하한 0.90 은 아직이다.
PASSABLE_FLOOR = 0.76


def _runnable(catalog: Catalog) -> list[Scenario]:
    """러너가 실제로 도는 정상군 — 프롬프트 작성됨 · teardown 지원.

    `test_정상군_대부분이_기계_판정_대상이다` 와 같은 모집단이다.
    """
    return [s for s in _normal_closed(catalog)
            if s.prompt_authored and not runner_mod._teardown(s)]


def test_passable_ratio_floor(catalog: Catalog) -> None:
    """plans/122 J-2 — 「판정 가능」(단언 1개 이상)이 아니라 「합격 가능」을 센다.

    어느 턴에든 `manual_review` 가 있으면 기계 단언이 전부 통과해도 판정은 `manual` 이 상한이다
    (`assertions.evaluate_turn` 판정 순서 `error → forbidden → fail → manual → pass`).
    구조적 보류 키(`STRUCTURALLY_HELD_KEYS`)도 같은 이유로 합격을 막으므로 합격 가능에서
    뺀다(엄격 정의).

    실측(2026-09-29 · 현 작업 트리 카탈로그): 모집단 104건 · `manual_review` 없는 시나리오 42건
    (40.4% — 122 §4 「현 작업 트리」 42/104와 같다) · 구조적 보류 키를 가진 시나리오 0건
    (카탈로그 전체 229건에서도 0건). 「판정 가능」 테스트는 같은 모집단을 93/104(89%)로 센다.
    이 단락은 단계 0 실측이다 — 이후 값은 `PASSABLE_FLOOR` 주석 이력에 적는다.
    """
    runnable = _runnable(catalog)
    passable = [
        s for s in runnable
        if not any(t.expect.get("manual_review") or set(t.expect) & STRUCTURALLY_HELD_KEYS
                   for t in s.turns)
    ]
    ratio = len(passable) / len(runnable)
    assert ratio >= PASSABLE_FLOOR, (
        f"합격 가능 {len(passable)}/{len(runnable)}({ratio:.1%}) — 하한 {PASSABLE_FLOOR:.2f}. "
        "`manual_review` 를 새로 두면 그 시나리오는 기계 단언이 다 통과해도 합격이 될 수 없다"
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
