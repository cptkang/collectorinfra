"""조사 대상 해소 공통 모듈 (Plan 78 W1 / Plan 80 WU-11 · SPEC-composite-orchestration M1).

수용 기준 정본은 `plans/78` W1. 여기서는 **결정적 로직만** 검증한다 —
라우팅 결과·relevance_score·의도 분류는 **단언하지 않는다**(3-A 조건 · WU-04 단서 승계).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.utils.prior_targets import (
    REASON_DEMONSTRATIVE,
    REASON_HALLUCINATED_COLUMN,
    REASON_NO_COLUMN,
    REASON_PROCESS_ROWS,
    SOURCE_FILTER,
    SOURCE_PREVIOUS,
    SOURCE_PRIOR,
    TargetRef,
    TargetResolution,
    build_prior_targets,
    resolve_targets,
)


class _CountingPicker:
    """3단 LLM 컬럼 지목 스텁 — **호출 횟수를 센다**(1·2단 확정 시 0회여야 한다)."""

    def __init__(self, answer: str | None):
        self.answer = answer
        self.calls = 0
        self.seen_columns: list[list[str]] = []

    def __call__(self, columns):
        self.calls += 1
        self.seen_columns.append(list(columns))
        return self.answer


# ──────────────────────────────────────────────
# 타입 계약 (78 W1-6)
# ──────────────────────────────────────────────

def test_target_ref_requires_at_least_one_identifier():
    """식별자가 하나도 없으면 `ValidationError` — 런타임까지 전파되지 않는다."""
    with pytest.raises(ValidationError):
        TargetRef(db_id="polestar_gimpo")


def test_target_ref_rejects_wrong_type():
    """타입 불일치가 `ValidationError`로 잡힌다 — dict 키 오타가 사는 구조를 막는다."""
    with pytest.raises(ValidationError):
        TargetRef(hostname=12345)


def test_target_ref_rejects_unknown_field():
    """오타 필드(`hostnmae`)가 조용히 무시되지 않는다(`extra="forbid"`)."""
    with pytest.raises(ValidationError):
        TargetRef(hostnmae="svweb001")


def test_target_ref_keeps_server_name_and_hostname_separate():
    """server_name ≠ hostname (D-046) — 한 필드로 뭉개지 않는다(SPEC C-2).

    `polestar_metric_trend`/`polestar_resource_status`는 server_name을,
    `polestar_os_config`/`polestar_process_snapshot`은 hostname을 받는다.
    """
    ref = TargetRef(server_name="웹서버01", hostname="svweb001", db_id="polestar_gimpo")
    assert ref.server_name == "웹서버01"
    assert ref.hostname == "svweb001"


def test_state_value_is_plain_dict():
    """상태에 실리는 값은 **dict** — LangGraph 체크포인터 직렬화 회귀 방지(78 W1-6)."""
    res = TargetResolution(targets=[TargetRef(hostname="svweb001")])
    value = res.as_state_value()
    assert isinstance(value, list) and all(isinstance(v, dict) for v in value)
    assert value[0]["hostname"] == "svweb001"


# ──────────────────────────────────────────────
# 해석 3단 (78 W1-3-1)
# ──────────────────────────────────────────────

def test_deterministic_match_does_not_call_llm():
    """1단에서 확정되면 **LLM 호출 0회** — 비용·지연·비결정성 최소화."""
    picker = _CountingPicker("hostname")
    res = build_prior_targets(
        [{"hostname": "svweb001"}, {"hostname": "svweb002"}], llm_pick_column=picker
    )
    assert picker.calls == 0
    assert res.llm_calls == 0
    assert [t.hostname for t in res.targets] == ["svweb001", "svweb002"]


def test_synonym_stage_does_not_call_llm():
    """2단(유사어)에서 확정되면 **LLM 호출 0회**."""
    picker = _CountingPicker("장비식별자")
    res = build_prior_targets(
        [{"장비식별자": "svweb001"}], synonym_lookup=lambda cols: "장비식별자",
        llm_pick_column=picker,
    )
    assert picker.calls == 0
    assert res.column == "장비식별자"
    assert [t.server_name for t in res.targets] == ["svweb001"]


def test_llm_stage_resolves_unknown_surface_form():
    """`HOST_IDENTIFIER_FIELDS`에 **없는 새 표면형**이 3단으로 해소된다.

    D-047의 커버리지 추격(새 별칭이 나올 때마다 상수를 늘리는 일)이 재발하지 않음을 고정한다.
    """
    picker = _CountingPicker("전산기기명")
    res = build_prior_targets([{"전산기기명": "svweb001"}], llm_pick_column=picker)
    assert picker.calls == 1
    assert res.llm_calls == 1
    assert [t.server_name for t in res.targets] == ["svweb001"]
    # 3단은 **컬럼명만** 본다 — 값을 넘기지 않는다(TDG 고정).
    assert picker.seen_columns == [["전산기기명"]]


def test_no_llm_injected_means_no_third_stage():
    """3단 콜러블 미주입(플래그 off)이면 3단을 쓰지 않고 사유를 남긴다."""
    res = build_prior_targets([{"전산기기명": "svweb001"}])
    assert not res.resolved
    assert res.llm_calls == 0
    assert res.dropped[0]["reason"] == REASON_NO_COLUMN


# ──────────────────────────────────────────────
# 결정적 확정 (78 W1-3-2)
# ──────────────────────────────────────────────

def test_hallucinated_column_produces_no_targets():
    """LLM이 **결과 행에 없는 컬럼명**을 반환하면 대상이 생성되지 않고 사유가 남는다."""
    picker = _CountingPicker("없는컬럼")
    res = build_prior_targets([{"전산기기명": "svweb001"}], llm_pick_column=picker)
    assert not res.resolved
    assert res.dropped[0]["reason"] == REASON_HALLUCINATED_COLUMN


def test_validation_failure_does_not_retry_third_stage():
    """검증 탈락이 3단 재호출 루프를 만들지 않는다 — 호출은 **1회로 끝난다**."""
    picker = _CountingPicker("없는컬럼")
    build_prior_targets([{"전산기기명": "svweb001"}], llm_pick_column=picker)
    assert picker.calls == 1


def test_demonstrative_value_is_not_an_identifier():
    """"해당 서버"는 식별자가 아니다 — 대상에서 빠지고 사유가 남는다."""
    res = build_prior_targets([{"hostname": "해당 서버"}, {"hostname": "svweb002"}])
    assert [t.hostname for t in res.targets] == ["svweb002"]
    assert any(d["reason"] == REASON_DEMONSTRATIVE for d in res.dropped)


def test_process_rows_are_excluded():
    """프로세스 결과 행(`pid` 보유)에서 서버 대상을 만들지 않는다(context_resolver:196 함정)."""
    rows = [{"name": "java", "pid": 1234, "cpu_pct": 91.2}]
    res = build_prior_targets(rows)
    assert not res.resolved
    assert res.dropped[0]["reason"] == REASON_PROCESS_ROWS


def test_truncation_is_reported():
    """상한 초과 시 절단하고 **절단 사실을 결과에 실는다**(부분 결과 오인 방지)."""
    rows = [{"hostname": f"svweb{i:03d}"} for i in range(15)]
    res = build_prior_targets(rows, max_targets=10)
    assert len(res.targets) == 10
    assert res.truncated is True
    assert res.truncated_count == 5


def test_duplicates_are_deduplicated():
    """같은 호스트가 여러 행에 나와도 대상은 1건이다(중복 조사 방지의 1차 방어)."""
    rows = [{"hostname": "svweb001"}, {"hostname": "svweb001"}, {"hostname": "svweb002"}]
    assert len(build_prior_targets(rows).targets) == 2


# ──────────────────────────────────────────────
# 우선순위 (78 W1-4)
# ──────────────────────────────────────────────

def test_this_turn_filter_beats_prior_targets():
    """①이 ②를 이긴다 — 사용자가 이번 턴에 명시 지목했으면 그것이 우선."""
    res = resolve_targets(
        filter_conditions=[{"field": "hostname", "value": "svdb001"}],
        prior_targets=[{"hostname": "svweb001"}, {"hostname": "svweb002"}],
    )
    assert res.source == SOURCE_FILTER
    assert [t.hostname for t in res.targets] == ["svdb001"]


def test_prior_targets_beat_previous_entities():
    """②가 ③을 이긴다 — 이번 복합 질의의 선행 결과가 직전 턴 승계보다 우선."""
    res = resolve_targets(
        prior_targets=[{"hostname": "svweb001"}],
        previous_entities=[{"field": "hostname", "value": "svold999"}],
    )
    assert res.source == SOURCE_PRIOR
    assert [t.hostname for t in res.targets] == ["svweb001"]


def test_previous_entities_used_when_nothing_else():
    """③ 폴백 — "해당 서버" 해소 경로가 살아 있다(M3 회귀 방지)."""
    res = resolve_targets(previous_entities=[{"field": "hostname", "value": "svold999"}])
    assert res.source == SOURCE_PREVIOUS
    assert [t.hostname for t in res.targets] == ["svold999"]


def test_alarm_payload_is_last_resort():
    """④ 이벤트 경로 폴백."""
    res = resolve_targets(alarm_payload={"hostname": "svalarm001", "db_id": "polestar_gimpo"})
    assert res.targets[0].hostname == "svalarm001"
    assert res.targets[0].db_id == "polestar_gimpo"


def test_all_conditions_survive_not_just_the_first():
    """조건이 N개면 N개가 남는다 — G3의 원인이던 **첫 건 조기 return**이 없다."""
    res = resolve_targets(
        filter_conditions=[
            {"field": "hostname", "value": "svweb001"},
            {"field": "hostname", "value": "svweb002"},
            {"field": "hostname", "value": "svbatch009"},
        ]
    )
    assert {t.hostname for t in res.targets} == {"svweb001", "svweb002", "svbatch009"}


def test_invalid_prior_target_is_isolated_not_fatal():
    """계약 위반 항목 하나가 전체를 무너뜨리지 않는다 — **항목 단위 격리**(E-2 전례)."""
    res = resolve_targets(prior_targets=[{"db_id": "x"}, {"hostname": "svweb001"}])
    assert [t.hostname for t in res.targets] == ["svweb001"]
    assert any(d["reason"] == "invalid_target_ref" for d in res.dropped)


def test_nothing_resolved_reports_no_source():
    """대상 미확정은 조용히 끝나지 않는다 — `source`가 비고 `resolved`가 False다."""
    res = resolve_targets()
    assert not res.resolved
    assert res.source == ""


# ──────────────────────────────────────────────
# 종류별 병합 계약 (회귀 방지 — 2026-08-27 실측)
# ──────────────────────────────────────────────

def test_different_identifier_kinds_merge_into_one_target():
    """★ `hostname`과 `server_name`은 **같은 서버의 두 표기**다 — 하나로 합친다.

    쪼개면 `sre_diagnose(server_name?, hostname?)` 계약이 깨진다. 종전
    `fault_diagnosis._extract_targets`가 두 종류를 누적 병합하던 동작이며,
    W1 착수 중 이것을 쪼갰다가 `test_from_filter_conditions`가 잡아냈다.
    """
    res = resolve_targets(
        filter_conditions=[
            {"field": "hostname", "value": "h9"},
            {"field": "server_name", "value": "srv-9"},
        ]
    )
    assert len(res.targets) == 1
    assert res.targets[0].hostname == "h9"
    assert res.targets[0].server_name == "srv-9"


def test_same_identifier_kind_stays_separate():
    """반대로 **같은 종류가 여럿**이면 서로 다른 서버다 — 합치면 G3가 그대로 남는다."""
    res = resolve_targets(
        filter_conditions=[
            {"field": "hostname", "value": "svweb001"},
            {"field": "hostname", "value": "svweb002"},
        ]
    )
    assert len(res.targets) == 2


def test_mixed_kinds_pair_positionally():
    """혼재 시 위치로 짝짓는다 — "서버 A(호스트 a), 서버 B(호스트 b)"의 자연스러운 해석."""
    res = resolve_targets(
        filter_conditions=[
            {"field": "hostname", "value": "a"},
            {"field": "server_name", "value": "A"},
            {"field": "hostname", "value": "b"},
            {"field": "server_name", "value": "B"},
        ]
    )
    assert [(t.hostname, t.server_name) for t in res.targets] == [("a", "A"), ("b", "B")]


def test_first_matched_field_kind_is_recorded():
    """단일 반환 래퍼(`_resolve_hostname`)가 종전 순서를 유지하도록 첫 종류를 남긴다."""
    res = resolve_targets(
        filter_conditions=[
            {"field": "server_name", "value": "srv-9"},
            {"field": "hostname", "value": "h9"},
        ]
    )
    assert res.column == "server_name"
