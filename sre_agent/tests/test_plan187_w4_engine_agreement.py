"""plans/187 W4 — 엔진 간 일치 표시(`domain/engine_agreement.py`) 경계 표."""

from __future__ import annotations

import pytest
from sre_agent.domain.engine_agreement import (
    APPROXIMATE_NOTE,
    DIFFERENT_LAYER,
    DISAGREEMENT_BADGE,
    NOT_COMPARABLE,
    RESULT_LABELS,
    SAME_LAYER,
    SIGNAL_LAYERS,
    compare_engines,
    holmes_layers,
    playbook_layer,
)
from sre_agent.domain.severity_signatures import SIGNATURES


@pytest.mark.parametrize(
    ("readings", "layer", "basis"),
    [
        ({"dominant": "sql"}, "db", ["dominant=sql"]),
        ({"dominant": "fetch"}, "db", ["dominant=fetch"]),
        ({"dominant": "external"}, "external", ["dominant=external"]),
        ({"dominant": "cpu"}, "cpu", ["dominant=cpu"]),
        # dominant이 계층을 내지 않으면 다음 판독으로 내려간다
        ({"dominant": "other", "db_split": "acquire"}, "db", ["db_split=acquire"]),
        ({"dominant": "network", "heap_shape": "leak_suspect"}, "gc", ["heap_shape=leak_suspect"]),
        ({"db_split": "n/a", "heap_shape": "periodic"}, "gc", ["heap_shape=periodic"]),
        ({"heap_shape": "normal", "cpu_owner": "was_logic"}, "cpu", ["cpu_owner=was_logic"]),
        ({"cpu_owner": "other_process"}, "host_resource", ["cpu_owner=other_process"]),
        ({"cpu_owner": "none", "host_saturation": "memory"}, "host_resource",
         ["host_saturation=memory"]),
        # 우선순위 — dominant가 heap_shape보다 앞선다
        ({"dominant": "sql", "heap_shape": "leak_suspect"}, "db", ["dominant=sql"]),
        ({"host_saturation": "none", "heap_shape": "normal", "db_split": "n/a"}, "unknown", []),
        ({}, "unknown", []),
        (None, "unknown", []),
        ({"dominant": None, "cpu_owner": 3}, "unknown", []),
    ],
)
def test_playbook_layer_boundaries(readings, layer, basis) -> None:
    assert playbook_layer(readings) == (layer, basis)


def test_holmes_layers_signatures_first_then_tools() -> None:
    assert holmes_layers(["was_slow_sql", "oom_kill", "was_slow_sql"], ["apm_runtime_health"]) == (
        ["db", "host_resource"],
        ["signal:was_slow_sql", "signal:oom_kill", "signal:was_slow_sql"],
    )
    # 시그니처가 계층을 못 내면 인용 도구를 쓴다(증상 도구는 계층 없음)
    assert holmes_layers(["was_error_burst"], ["apm_events", "apm_resource_pool"]) == (
        ["db"],
        ["tool:apm_resource_pool"],
    )
    assert holmes_layers([], ["apm_app_health"]) == ([], [])


@pytest.mark.parametrize(
    ("readings", "signals", "tools", "unavailable", "result"),
    [
        ({"dominant": "sql"}, ["was_db_pool_exhaustion"], [], None, SAME_LAYER),
        ({"dominant": "sql"}, [], ["apm_resource_pool"], None, SAME_LAYER),
        ({"dominant": "external"}, ["was_external_call_delay"], [], None, SAME_LAYER),
        ({"heap_shape": "leak_suspect"}, ["was_gc_stall"], [], None, SAME_LAYER),
        ({"cpu_owner": "other_process"}, ["oom_kill"], [], None, SAME_LAYER),
        # 여러 계층 중 하나라도 같으면 같은 계층
        ({"dominant": "sql"}, ["was_gc_stall", "was_slow_sql"], [], None, SAME_LAYER),
        ({"dominant": "sql"}, ["was_heap_pressure"], [], None, DIFFERENT_LAYER),
        ({"dominant": "cpu"}, [], ["apm_runtime_health"], None, DIFFERENT_LAYER),
        ({"dominant": "external"}, ["fs_readonly"], [], None, DIFFERENT_LAYER),
        ({"dominant": "other"}, ["was_slow_sql"], [], None, NOT_COMPARABLE),
        ({"dominant": "sql"}, [], ["apm_events"], None, NOT_COMPARABLE),
        ({"dominant": "sql"}, ["was_slow_sql"], [], "심층 조사 skipped", NOT_COMPARABLE),
    ],
)
def test_compare_engines_boundary_table(readings, signals, tools, unavailable, result) -> None:
    out = compare_engines(readings, signals, tools, unavailable_reason=unavailable)
    assert out["result"] == result and out["label"] == RESULT_LABELS[result]
    assert out["approximate"] is True and out["note"] == APPROXIMATE_NOTE
    assert out["badge"] == (DISAGREEMENT_BADGE if result == DIFFERENT_LAYER else None)
    assert out["reason"]
    if unavailable:
        assert out["reason"] == unavailable


def test_compare_engines_shape() -> None:
    out = compare_engines({"dominant": "sql"}, ["was_heap_pressure"], ["apm_runtime_health"])
    assert set(out) == {
        "result", "label", "approximate", "note", "badge", "reason", "playbook", "holmesgpt"
    }
    assert out["playbook"] == {"layer": "db", "basis": ["dominant=sql"]}
    assert out["holmesgpt"] == {"layers": ["gc"], "basis": ["signal:was_heap_pressure"]}


def test_signal_layer_names_exist_in_signature_vocabulary() -> None:
    """OS 시그니처 이름은 `severity_signatures.SIGNATURES`에 있어야 한다(오타 방지)."""
    os_names = {s.name for s in SIGNATURES}
    for name in SIGNAL_LAYERS:
        assert name.startswith("was_") or name in os_names, name
