"""plans/200 W1(I-1) — 실행 의존 표(`READING_INPUTS` · `PRIOR_INPUTS`)와 가드 · `deps_of`.

- 판독 가드: read_* 함수에 「값을 읽은 스텝의 액션」을 기록하는 프록시 결과를 넣어, 실제로 읽은
  액션 ⊆ `READING_INPUTS[판독]`을 단언한다(새 판독이 표 없이 추가되면 실패).
- prior 가드: 스텝 함수가 `ctx.prior`에서 값을 읽은 결과의 액션 ⊆ `PRIOR_INPUTS[액션]` — 녹화
  픽스처 종단 실행 + 합성 문맥 직접 실행(동적) · 소스 AST 대조(정적)를 병행한다.
- 플레이북별 `deps_of` 결과(계획서 §1.2 「실제 의존만 따르면」) · `plan_stages` 결과 불변.

실 LLM·실 MCP 호출 0.
"""

from __future__ import annotations

import ast
import inspect
import json
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from sre_agent.application import playbook_steps
from sre_agent.application.playbook_runner import _reading_refs, plan_stages, run_playbook
from sre_agent.application.playbook_schedule import ancestors, deps_of, reading_refs
from sre_agent.application.playbook_steps import (
    PRIOR_INPUTS,
    STEP_LIBRARY,
    BaselineWindow,
    RunScope,
    RunWindows,
    StepContext,
    StepFn,
    StepResult,
)
from sre_agent.domain.playbook import Playbook
from sre_agent.domain.playbook_expr import READING_NAMES
from sre_agent.domain.playbook_readings import (
    _READERS,
    DEFAULT_PARAMS,
    READING_ACTIONS,
    READING_INPUTS,
)
from sre_agent.infrastructure.playbook_loader import load_playbooks

FIXTURES = Path(__file__).parent / "fixtures" / "plan187"
PLAYBOOKS = load_playbooks().playbooks
NOW = datetime(2026, 10, 19, 10, 20, 0)


def _pb(pid: str) -> Playbook:
    return next(p for p in PLAYBOOKS if p.id == pid)


# ── 판독 가드 (READING_INPUTS) ──────────────────────────────────────────────


class _RecStep(Mapping[str, Any]):
    """스텝 결과 dict 프록시 — `action` 밖의 키를 읽으면 그 스텝의 액션을 기록한다."""

    def __init__(self, raw: Mapping[str, Any], log: set[str]) -> None:
        self._raw = raw
        self._log = log

    def __getitem__(self, key: str) -> Any:
        if key != "action":
            self._log.add(str(self._raw.get("action")))
        return self._raw[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._raw)

    def __len__(self) -> int:
        return len(self._raw)


def _iso(minute: int) -> str:
    return f"2026-10-19T09:{minute:02d}:00"


def _rich_steps(*, alarm_instance: str | None = "A1") -> dict[str, dict[str, Any]]:
    """판독마다 가장 깊은 분기(TPS 변화율까지)를 타게 만든 스텝 결과 전부(`to_dict` 모양)."""
    scope = {"alarm_instance": alarm_instance} if alarm_instance else {}
    heap = [{"time_ms": i * 60000, "value": 90.0} for i in range(8)]
    target_s1 = {
        "incident": {"tps": 100.0, "p95_ms": 900.0},
        "baselines": {"prev_day": {"tps": 50.0}},
        "baseline_p95_ms": 100.0,
        "period_compare": {"d": {"current": {"avg_response_ms": 200.0}}},
    }
    target_s2 = {
        "trend_summary": {"process_cpu_pct": {"max": 90.0}, "active_services": {"avg": 99.0}},
        "trend": {"heap_used_mb": heap, "heap_committed_mb": [{"value": 100.0}]},
        "pool": {"active": 1},
    }
    rows = [
        {"application": "svc", "response_time_ms": 1000.0, "sql_ms": 800.0, "fetch_ms": 10.0}
    ]
    data = {
        "scope.resolve_was_targets": {
            "resolution": "resolved",
            "instances": [
                {"instance": "A1", "host": "h1", "verdict": "degraded"},
                {"instance": "A2", "host": "h1", "verdict": "degraded"},
            ],
            "scope": scope,
        },
        "apm.golden_signals": {"targets": {"A1": target_s1}},
        "apm.runtime_and_pool": {"targets": {"A1": target_s2}},
        "apm.slow_tx_breakdown": {
            "rows": rows,
            "profile": {"acquire_ms": 5.0, "execute_ms": 1.0},
        },
        "host.usage_trend": {
            "findings": {
                "cpu": {"peak_value": 95.0, "is_anomalous": True},
                "memory": {"peak_value": 50.0},
                "filesystem": {"peak_value": 60.0},
            },
            "series": {"filesystem": {"points": [[_iso(0), 50.0], [_iso(30), 60.0]]}},
        },
        "host.process_top": {"rows": [{"name": "java", "pcpu": 90.0}]},
        "change.recent": {
            "candidates": [{"source": "x", "at": _iso(0), "minutes_before_alarm": 10}]
        },
        "xsource.related_alarms": {"rows": []},
    }
    return {
        f"S{i}": {"action": action, "status": "ok", "data": d, "missing": []}
        for i, (action, d) in enumerate(data.items())
    }


def _scenarios() -> list[dict[str, dict[str, Any]]]:
    rich = _rich_steps()
    no_alarm = _rich_steps(alarm_instance=None)
    failed = {sid: {**s, "status": "missing", "reason": "x"} for sid, s in rich.items()}
    return [rich, no_alarm, failed, {}]


def _accessed(name: str, steps: Mapping[str, Mapping[str, Any]]) -> set[str]:
    log: set[str] = set()
    proxied = {sid: _RecStep(s, log) for sid, s in steps.items()}
    _READERS[name](proxied, DEFAULT_PARAMS)
    return log


def test_reading_inputs_cover_every_reading() -> None:
    assert set(READING_INPUTS) == set(READING_NAMES) == set(_READERS) == set(READING_ACTIONS)
    for name, home in READING_ACTIONS.items():
        assert set(home) <= READING_INPUTS[name], name


@pytest.mark.parametrize("name", sorted(READING_NAMES))
def test_reading_reads_only_declared_inputs(name: str) -> None:
    seen: set[str] = set()
    for steps in _scenarios():
        got = _accessed(name, steps)
        assert got <= READING_INPUTS[name], (name, got - READING_INPUTS[name])
        seen |= got
    # 표가 과대하지 않다 — 깊은 분기 시나리오가 표의 입력을 전부 실제로 읽는다
    assert seen == READING_INPUTS[name]


def test_cpu_owner_hidden_inputs() -> None:
    """계획서 §1.3 숨은 의존 1 — R-E가 S1(TPS)·대상 확정(알람 인스턴스)을 읽는다."""
    got = _accessed("cpu_owner", _rich_steps())
    assert {"apm.golden_signals", "scope.resolve_was_targets"} <= got
    assert "apm.golden_signals" not in READING_ACTIONS["cpu_owner"]  # 담당 표는 그대로


# ── prior 가드 (PRIOR_INPUTS) ───────────────────────────────────────────────


class _RecResult:
    """`StepResult` 프록시 — `action`·`step` 밖의 속성을 읽으면 그 결과의 액션을 기록한다."""

    def __init__(self, inner: StepResult, log: set[str]) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_log", log)

    def __getattr__(self, name: str) -> Any:
        inner = object.__getattribute__(self, "_inner")
        if name not in ("action", "step"):
            object.__getattribute__(self, "_log").add(inner.action)
        return getattr(inner, name)


class _RecPrior(Mapping[str, Any]):
    def __init__(self, prior: Mapping[str, StepResult], log: set[str]) -> None:
        self._prior = prior
        self._log = log

    def __getitem__(self, key: str) -> Any:
        return _RecResult(self._prior[key], self._log)

    def __iter__(self) -> Iterator[str]:
        return iter(self._prior)

    def __len__(self) -> int:
        return len(self._prior)


def _recording_library(seen: dict[str, set[str]]) -> dict[str, StepFn]:
    def wrap(action: str, fn: StepFn) -> StepFn:
        def run(ctx: StepContext) -> StepResult:
            ctx.prior = _RecPrior(ctx.prior, seen.setdefault(action, set()))
            return fn(ctx)

        return run

    return {action: wrap(action, fn) for action, fn in STEP_LIBRARY.items()}


def _fixtures() -> list[dict]:
    return [
        json.loads(p.read_text(encoding="utf-8")) for p in sorted(FIXTURES.glob("*.json"))
    ]


class _Recorded:
    """녹화 조회 배치 콜러블(부분집합 일치 · 가장 구체적인 것) — 미녹화는 오류 응답."""

    def __init__(self, fixture: dict) -> None:
        self.responses = fixture.get("tool_responses") or {}

    def __call__(self, calls: list[tuple[str, dict]]) -> list[str]:
        out = []
        for tool, args in calls:
            best = None
            for entry in self.responses.get(tool, []):
                if all(args.get(k) == v for k, v in (entry.get("args") or {}).items()):
                    if best is None or len(entry["args"]) > len(best["args"]):
                        best = entry
            resp = best["response"] if best else {"error": "미녹화"}
            out.append(json.dumps(resp, ensure_ascii=False))
        return out


def _assert_prior_subset(seen: Mapping[str, set[str]]) -> None:
    for action, got in seen.items():
        allowed = PRIOR_INPUTS.get(action, frozenset())
        assert got <= allowed, (action, got - allowed)


@pytest.mark.parametrize("pid", [p.id for p in PLAYBOOKS])
def test_prior_reads_on_recorded_fixtures(pid: str) -> None:
    """녹화 픽스처 전부로 플레이북을 종단 실행 — 앞 결과에서 읽은 액션 ⊆ `PRIOR_INPUTS`."""
    seen: dict[str, set[str]] = {}
    for fx in _fixtures():
        caller = _Recorded(fx)
        run_playbook(
            fx["alarm"],
            _pb(pid),
            matched_by="test",
            polestar=caller,
            apm=caller,
            now=lambda: NOW,
            max_workers=1,
            for_each_max=1,
            polestar_source="polestar",
            steps=_recording_library(seen),
        )
    _assert_prior_subset(seen)


def _rich_prior() -> dict[str, StepResult]:
    """액션마다 성공 결과 1건 — 앞 결과 독자가 끝까지 읽도록 모양을 맞춘다."""
    data: dict[str, dict[str, Any]] = {
        "apm.runtime_and_pool": {
            "targets": {"A1": {"trend_summary": {"process_cpu_pct": {"max": 90.0}}}}
        },
        "apm.active_services": {
            "rows": [
                {
                    "instance_name": "A1",
                    "application": "svc",
                    "cpu_ms": 5,
                    "elapsed_ms": 9,
                    "active_ref": {"txid": "1", "instance_name": "A1"},
                }
            ]
        },
    }
    return {
        f"P{i}": StepResult(step=f"P{i}", action=action, status="ok", data=data.get(action, {}))
        for i, action in enumerate(STEP_LIBRARY)
    }


def _direct_ctx(args: Mapping[str, Any], prior: Mapping[str, Any]) -> StepContext:
    windows = RunWindows(
        alarm_time="2026-10-19T10:00:00",
        reference_time="2026-10-19T10:10:00",
        lookback_minutes=40,
        incident_start="2026-10-19T09:30:00",
        incident_end="2026-10-19T10:10:00",
        baselines=(
            BaselineWindow(
                "prev_day", "2026-10-18T09:30:00", "2026-10-18T10:10:00", "2026-10-18T10:10:00"
            ),
        ),
    )
    scope = RunScope(
        alarm_instance="A1",
        host="h1",
        polestar_source="polestar",
        polestar_server="h1",
        alarm_name="CPU",
        targets=[{"instance": "A1", "source_id": "s1"}],
        host_was=["A1"],
    )
    caller = _Recorded({"tool_responses": {}})
    return StepContext(
        payload={"event": {"alarmTime": "20261019100000", "serverName": "h1"}},
        windows=windows,
        scope=scope,
        args=args,
        targets=["A1"],
        target_sources=["s1"],
        polestar=caller,
        apm=caller,
        now=lambda: NOW,
        prior=prior,
    )


def _arg_variants() -> list[tuple[str, dict[str, Any]]]:
    out = [(action, {}) for action in STEP_LIBRARY]
    for pb in PLAYBOOKS:
        out += [(s.action, dict(s.args)) for s in pb.steps if s.action in STEP_LIBRARY]
    return out


def test_prior_reads_direct_every_action() -> None:
    """합성 문맥으로 스텝 함수 전부를 직접 실행(플레이북 YAML 인자 포함) — 표가 과소·과대 아님."""
    seen: dict[str, set[str]] = {}
    for action, args in _arg_variants():
        log = seen.setdefault(action, set())
        try:
            STEP_LIBRARY[action](_direct_ctx(args, _RecPrior(_rich_prior(), log)))
        except Exception:  # noqa: BLE001 — 가짜 응답으로 뒤에서 실패해도 앞 결과 접근은 기록됐다
            pass
    _assert_prior_subset(seen)
    assert {a: got for a, got in seen.items() if got} == dict(PRIOR_INPUTS)


def _prior_readers() -> dict[str, set[str]]:
    """정적 — `playbook_steps`에서 `.prior`를 읽는 함수 → 그 함수가 `.action`과 비교하는 리터럴."""
    tree = ast.parse(inspect.getsource(playbook_steps))
    out: dict[str, set[str]] = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef) or fn.name == "__init__":
            continue
        nodes = list(ast.walk(fn))
        if not any(
            isinstance(n, ast.Attribute) and n.attr == "prior" and isinstance(n.ctx, ast.Load)
            for n in nodes
        ):
            continue
        out[fn.name] = {
            c.value
            for n in nodes
            if isinstance(n, ast.Compare)
            and isinstance(n.left, ast.Attribute)
            and n.left.attr == "action"
            for c in n.comparators
            if isinstance(c, ast.Constant) and isinstance(c.value, str)
        }
    return out


def test_prior_readers_static() -> None:
    """정적 대조 — 새 앞 결과 독자 함수가 생기면 표와 이 목록을 함께 고치게 한다."""
    assert all(fn.__module__ == playbook_steps.__name__ for fn in STEP_LIBRARY.values())
    owner = {"_was_cpu_top": "apm.active_services", "active_sql": "apm.active_sql"}
    readers = _prior_readers()
    assert set(readers) == set(owner)
    for fn_name, read in readers.items():
        assert read and read <= PRIOR_INPUTS[owner[fn_name]], (fn_name, read)


# ── deps_of · ancestors ────────────────────────────────────────────────────

# 계획서 §1.2 「실제 의존만 따르면」 — YAML 근거(when · 판독 입력 · 앞 결과 · 대상 확정).
DEPS: dict[str, dict[str, set[str]]] = {
    "host.cpu": {
        "S4": set(),
        "S6": set(),
        "S0r": set(),
        "S1": {"S0r"},
        "S2": {"S0r"},
        # when S2.status · cpu_owner(S1·S2·S4·S6) · 앞 결과 S2 · 대상 확정 S0r
        "S3a": {"S0r", "S1", "S2", "S4", "S6"},
        "S3b": {"S0r", "S3a"},
        "S5": {"S0r"},
        "S7": {"S0r"},
    },
    "host.process": {
        "S4r": set(),
        "S4": set(),
        "S6": set(),
        "S5": set(),
        "S5a": set(),
        "S0r": set(),
        "S1": {"S0r"},
        "S2": {"S0r"},
        "S3a": {"S0r", "S1", "S2", "S4", "S6"},
        "S3b": {"S0r", "S3a"},
        "S7": {"S0r"},
    },
    "host.memory": {"S4": set(), "S6": set(), "S0r": set(), "S2": {"S0r"}, "S5": {"S0r"}},
    "was.latency": {
        "S0": set(),
        "S1": {"S0"},
        "S2": {"S0"},
        "S3": {"S0", "S1"},
        "S4": {"S0"},
        "S5": {"S0"},
        "S6": {"S0"},
        "S7": {"S0"},
        "S8": {"S0"},
    },
    "was.sql": {
        "S0": set(),
        "S3": {"S0"},
        "S3a": {"S0"},
        "S3b": {"S0", "S3a"},
        "S5": {"S0"},
        "S7": {"S0"},
        "S8": {"S0"},
    },
}


@pytest.mark.parametrize("pid", sorted(DEPS))
def test_deps_of_matches_actual_dependencies(pid: str) -> None:
    pb = _pb(pid)
    got = {s.id: set(deps_of(s, pb)) for s in pb.steps if s.action != "synthesize"}
    assert got == DEPS[pid]


@pytest.mark.parametrize("pb", PLAYBOOKS, ids=lambda p: p.id)
def test_deps_respect_yaml_order_and_current_stages(pb: Playbook) -> None:
    """의존은 YAML 앞 스텝뿐(위상 순서) · 현행 단계 분할은 모든 의존을 더 앞 단계에 둔다."""
    order = {s.id: i for i, s in enumerate(pb.steps)}
    stage_of = {s.id: i for i, st in enumerate(plan_stages(pb)) for s in st}
    for step in pb.steps:
        deps = deps_of(step, pb)
        if step.action == "synthesize":
            assert deps == frozenset()
            continue
        assert all(order[d] < order[step.id] for d in deps), step.id
        assert all(stage_of[d] < stage_of[step.id] for d in deps), step.id


def test_ancestors_transitive_closure() -> None:
    anc = ancestors(_pb("host.cpu"))
    assert "S9" not in anc
    assert anc["S3b"] == {"S0r", "S3a", "S1", "S2", "S4", "S6"}
    assert anc["S0r"] == frozenset() and anc["S5"] == {"S0r"}
    for pb in PLAYBOOKS:
        anc = ancestors(pb)
        for sid, deps in anc.items():
            for d in deps:
                assert anc[d] <= deps, (pb.id, sid, d)


def test_reading_refs_same_rule_as_runner() -> None:
    samples = [s.when for pb in PLAYBOOKS for s in pb.steps] + [
        "S2.scope == 'x'",
        "cpu_owner in ['was_load'] and 'scope' != load_mode",
        "not heap_shape",
        None,
    ]
    for when in samples:
        assert reading_refs(when) == _reading_refs(when), when


# ── plan_stages 불변 ────────────────────────────────────────────────────────

STAGES: dict[str, list[list[str]]] = {
    "fallback": [["S4", "S5", "S5a", "S7"]],
    "host.cpu": [["S4", "S6"], ["S0r"], ["S1", "S2"], ["S3a"], ["S3b", "S5", "S7"]],
    "host.disk_io": [["S4", "S6", "S5"]],
    "host.fs": [["S4", "S5", "S7"]],
    "host.memory": [["S4", "S6"], ["S0r"], ["S2", "S5"]],
    "host.network": [["S4r", "S4t", "S5"]],
    "host.process": [
        ["S4r", "S4", "S6", "S5", "S5a"], ["S0r"], ["S1", "S2"], ["S3a"], ["S3b", "S7"],
    ],
    "was.active": [["S0"], ["S1", "S1a", "S2", "S3", "S5"]],
    "was.error": [["S0"], ["S1", "S1e", "S3", "S7", "S5"]],
    "was.fallback": [["S0"], ["S1", "S1e", "S5", "S7"]],
    "was.heap": [["S0"], ["S2", "S1", "S7", "S4"]],
    "was.latency": [["S0"], ["S1", "S2"], ["S3", "S4", "S5", "S6", "S7", "S8"]],
    "was.pool": [["S0"], ["S2", "S3", "S8", "S1", "S5"]],
    "was.sql": [["S0"], ["S3", "S3a"], ["S3b", "S5", "S7", "S8"]],
}


def test_plan_stages_unchanged_all_playbooks() -> None:
    got = {pb.id: [[s.id for s in st] for st in plan_stages(pb)] for pb in PLAYBOOKS}
    assert got == STAGES
