"""엔진 간 일치 표시 — 플레이북 지배 계층 ↔ HolmesGPT 근거 계층 (plans/187 §5.0.1 6번 · D-341 ⑮).

두 엔진의 결론을 **서술이 아니라 근거**로 대조한다. 결과는 「같은 계층」·「다른 계층」·「비교 불가」
셋이며, 계층 어휘가 거친 「대략적 대조」라는 점을 결과에 함께 싣는다. 판정·가설 순위·통보는 이
결과로 바뀌지 않는다(표시 전용 — D-035).

- 플레이북 쪽: 판독 enum(R-C `dominant` → R-D `db_split` → R-F `heap_shape` → R-E `cpu_owner`
  → R-G `host_saturation` 순)의 첫 계층이 지배 계층이다.
- HolmesGPT 쪽: 심층 도구 원시 출력에서 매칭된 시그니처(OS 시그니처 이름 · 게이트웨이
  `was_signals` kind)의 계층 + 서술이 인용한 도구의 계층. 시그니처가 있으면 그것만 쓰고,
  없을 때만 인용 도구를 쓴다.

계층 어휘: db · external · cpu · gc · host_resource (그 밖 = unknown — 비교에 쓰지 않는다).

순수 도메인 — 표준 라이브러리만 쓴다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

LAYER_DB = "db"
LAYER_EXTERNAL = "external"
LAYER_CPU = "cpu"
LAYER_GC = "gc"
LAYER_HOST = "host_resource"
LAYER_UNKNOWN = "unknown"
LAYERS: tuple[str, ...] = (LAYER_DB, LAYER_EXTERNAL, LAYER_CPU, LAYER_GC, LAYER_HOST)

SAME_LAYER = "same_layer"
DIFFERENT_LAYER = "different_layer"
NOT_COMPARABLE = "not_comparable"
RESULT_LABELS: dict[str, str] = {
    SAME_LAYER: "같은 계층",
    DIFFERENT_LAYER: "다른 계층",
    NOT_COMPARABLE: "비교 불가",
}
#: 결론이 다를 때 붙는 배지(§5.0 충돌 처리).
DISAGREEMENT_BADGE = "엔진 간 불일치"
APPROXIMATE_NOTE = (
    "대략적 대조 — 서술이 아니라 근거(플레이북 판독 enum ↔ HolmesGPT 매칭 시그니처·인용 도구)의 "
    "계층만 비교한다"
)

#: 플레이북 판독 → 계층(판독 이름 순서 = 지배 계층 우선순위). 표에 없는 값은 계층을 내지 않는다.
READING_LAYERS: tuple[tuple[str, Mapping[str, str]], ...] = (
    (
        "dominant",
        {"sql": LAYER_DB, "fetch": LAYER_DB, "external": LAYER_EXTERNAL, "cpu": LAYER_CPU},
    ),
    ("db_split", {"acquire": LAYER_DB, "execute": LAYER_DB}),
    (
        "heap_shape",
        {"periodic": LAYER_GC, "leak_suspect": LAYER_GC, "load_proportional": LAYER_GC},
    ),
    (
        "cpu_owner",
        {"was_logic": LAYER_CPU, "was_load": LAYER_CPU, "other_process": LAYER_HOST},
    ),
    ("host_saturation", {"cpu": LAYER_HOST, "memory": LAYER_HOST, "fs": LAYER_HOST}),
)

#: 시그니처 이름(OS `SIGNATURES` · 게이트웨이 `was_signals` kind) → 계층.
SIGNAL_LAYERS: dict[str, str] = {
    "was_db_pool_exhaustion": LAYER_DB,
    "was_slow_sql": LAYER_DB,
    "was_external_call_delay": LAYER_EXTERNAL,
    "was_gc_stall": LAYER_GC,
    "was_heap_pressure": LAYER_GC,
    "oom_kill": LAYER_HOST,
    "oom_kill_metric": LAYER_HOST,
    "fs_readonly": LAYER_HOST,
    "fs_readonly_metric": LAYER_HOST,
    "inode_or_disk_full": LAYER_HOST,
    "fd_exhaustion": LAYER_HOST,
    "conntrack_full": LAYER_HOST,
    "soft_lockup": LAYER_HOST,
    "hung_task": LAYER_HOST,
}

#: 인용 도구 → 계층(시그니처가 없을 때만 쓴다). 증상 도구(골든 시그널·이벤트·트랜잭션 목록)는 계층을
#: 내지 않는다 — 어느 계층을 봤는지가 도구 이름에 드러나는 것만 둔다.
TOOL_LAYERS: dict[str, str] = {
    "apm_resource_pool": LAYER_DB,
    "apm_runtime_health": LAYER_GC,
    "polestar_metric_trend": LAYER_HOST,
    "polestar_process_snapshot": LAYER_HOST,
}


def playbook_layer(readings: Mapping[str, Any] | None) -> tuple[str, list[str]]:
    """플레이북 지배 계층과 근거(`판독=값`). 근거가 없으면 (`unknown`, [])."""
    values = readings or {}
    for name, table in READING_LAYERS:
        value = values.get(name)
        if isinstance(value, str) and value in table:
            return table[value], [f"{name}={value}"]
    return LAYER_UNKNOWN, []


def holmes_layers(
    signals: Sequence[str], cited_tools: Sequence[str]
) -> tuple[list[str], list[str]]:
    """HolmesGPT 근거 계층(중복 제거 · 등장 순)과 근거 목록."""
    layers: list[str] = []
    basis: list[str] = []
    for name in signals:
        layer = SIGNAL_LAYERS.get(name)
        if layer is not None:
            basis.append(f"signal:{name}")
            if layer not in layers:
                layers.append(layer)
    if layers:
        return layers, basis
    for tool in cited_tools:
        layer = TOOL_LAYERS.get(tool)
        if layer is not None:
            basis.append(f"tool:{tool}")
            if layer not in layers:
                layers.append(layer)
    return layers, basis


def compare_engines(
    readings: Mapping[str, Any] | None,
    signals: Sequence[str] = (),
    cited_tools: Sequence[str] = (),
    *,
    unavailable_reason: str | None = None,
) -> dict[str, Any]:
    """두 엔진의 계층을 대조한다.

    `unavailable_reason`이 있으면(심층 조사 결과 없음 — 진행 중·생략·실패) 바로 「비교 불가」다.
    반환: `{result, label, approximate, note, badge, reason, playbook{layer, basis},
    holmesgpt{layers, basis}}`.
    """
    pb_layer, pb_basis = playbook_layer(readings)
    h_layers, h_basis = holmes_layers(signals, cited_tools)
    if unavailable_reason:
        result, reason = NOT_COMPARABLE, unavailable_reason
    elif pb_layer == LAYER_UNKNOWN:
        result, reason = NOT_COMPARABLE, "플레이북 지배 계층 미확정(판독 근거 없음)"
    elif not h_layers:
        result = NOT_COMPARABLE
        reason = "HolmesGPT 근거 계층 없음(매칭 시그니처·계층 도구 인용 없음)"
    elif pb_layer in h_layers:
        result, reason = SAME_LAYER, f"두 엔진 모두 {pb_layer} 계층을 가리킴"
    else:
        result = DIFFERENT_LAYER
        reason = f"플레이북 {pb_layer} 계층 · HolmesGPT {'/'.join(h_layers)} 계층"
    return {
        "result": result,
        "label": RESULT_LABELS[result],
        "approximate": True,
        "note": APPROXIMATE_NOTE,
        "badge": DISAGREEMENT_BADGE if result == DIFFERENT_LAYER else None,
        "reason": reason,
        "playbook": {"layer": pb_layer, "basis": pb_basis},
        "holmesgpt": {"layers": h_layers, "basis": h_basis},
    }


__all__ = [
    "APPROXIMATE_NOTE",
    "DIFFERENT_LAYER",
    "DISAGREEMENT_BADGE",
    "LAYERS",
    "LAYER_CPU",
    "LAYER_DB",
    "LAYER_EXTERNAL",
    "LAYER_GC",
    "LAYER_HOST",
    "LAYER_UNKNOWN",
    "NOT_COMPARABLE",
    "READING_LAYERS",
    "RESULT_LABELS",
    "SAME_LAYER",
    "SIGNAL_LAYERS",
    "TOOL_LAYERS",
    "compare_engines",
    "holmes_layers",
    "playbook_layer",
]
