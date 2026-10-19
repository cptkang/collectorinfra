"""plans/187 W1 — 플레이북 스키마 · 로더 · 선택기 · `when` 평가기 (D-341)."""

from __future__ import annotations

import ast
import copy
import inspect
import logging
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError
from sre_agent.domain import playbook as playbook_mod
from sre_agent.domain import playbook_expr as expr_mod
from sre_agent.domain.playbook import (
    FALLBACK_ID,
    STEP_ACTIONS,
    AlarmFacts,
    Playbook,
    parse_playbook,
    select_playbook,
)
from sre_agent.domain.playbook_expr import (
    ExprSyntaxError,
    Tri,
    compile_expr,
    compile_list_condition,
    eval_all,
    eval_any,
)
from sre_agent.infrastructure import playbook_loader as loader_mod
from sre_agent.infrastructure.playbook_loader import DEFAULT_PLAYBOOK_DIR, load_playbooks

T, F, U = Tri.TRUE, Tri.FALSE, Tri.UNKNOWN

_RUNBOOK = {
    "meaning": "의미",
    "impact": "영향",
    "owner": "운영팀",
    "last_reviewed": "2026-10-17",
}


def _pb(pid: str, match: dict | None = None, steps: list | None = None, **extra) -> dict:
    data: dict = {
        "id": pid,
        "version": 1,
        "steps": steps or [{"id": "S9", "action": "synthesize"}],
        "runbook": dict(_RUNBOOK),
    }
    if match is not None:
        data["match"] = match
    data.update(extra)
    return data


# ---------------------------------------------------------------------------
# §4.1 유형 13종 — 대표 알람 사실
# ---------------------------------------------------------------------------

_APM = "apm.Instance"

#: 유형 ID → 대표 알람 사실(호출자가 `classify_alarm_kind`·was_signals에서 결정적으로 만든 값).
TYPE_FACTS: dict[str, AlarmFacts] = {
    "was.latency": AlarmFacts(_APM, "apm", ("response_time",)),
    "was.error": AlarmFacts(_APM, "apm", ("error",)),
    "was.active": AlarmFacts(_APM, "apm", ("active",)),
    "was.heap": AlarmFacts(_APM, "apm", ("heap",)),
    "was.pool": AlarmFacts(_APM, "apm", ("pool",)),
    "host.cpu": AlarmFacts("host", "cpu", ()),
    "host.memory": AlarmFacts("host", "memory", ()),
    "host.fs": AlarmFacts("host", "disk", ("filesystem",)),
    "host.disk_io": AlarmFacts("host", "disk", ("disk_io",)),
    "host.process": AlarmFacts("host", "process", ()),
    "host.network": AlarmFacts("host", "network", ()),
    "db.session": AlarmFacts("dpm.Instance", None, ("lock",)),
    "fallback": AlarmFacts("host", "log", ()),
}

#: 유형 13종의 match 스펙(§4.1 매칭 열) — 테스트 안에서만 만든 플레이북 세트.
TYPE_MATCHES: dict[str, dict | None] = {
    "was.latency": {
        "resource_type": _APM,
        "any_kind": ["latency", "response_time"],
        "priority": 10,
    },
    "was.error": {"resource_type": _APM, "any_kind": ["error", "exception"], "priority": 10},
    "was.active": {"resource_type": _APM, "any_kind": ["active", "thread_stuck"], "priority": 10},
    "was.heap": {"resource_type": _APM, "any_kind": ["heap", "gc", "memory"], "priority": 10},
    "was.pool": {"resource_type": _APM, "any_kind": ["pool", "connection"], "priority": 10},
    "host.cpu": {"alarm_kind": "cpu"},
    "host.memory": {"alarm_kind": "memory"},
    "host.fs": {"alarm_kind": "disk", "any_kind": ["filesystem", "inode"]},
    "host.disk_io": {"alarm_kind": "disk", "any_kind": ["disk_io"]},
    "host.process": {"alarm_kind": "process"},
    "host.network": {"alarm_kind": "network"},
    "db.session": {"resource_type": "dpm.Instance", "any_kind": ["lock", "session", "wait"]},
    "fallback": None,
}


#: 저장소 YAML(W6 — db.*는 DPM 연동 뒤라 없다).
SHIPPED_IDS = sorted(t for t in TYPE_FACTS if t != "db.session")


@pytest.fixture(scope="module")
def shipped() -> tuple[Playbook, ...]:
    result = load_playbooks(DEFAULT_PLAYBOOK_DIR)
    assert result.error is None
    assert result.rejected == ()
    return result.playbooks


def test_shipped_yaml_two(shipped: tuple[Playbook, ...]) -> None:
    assert sorted(pb.id for pb in shipped) == SHIPPED_IDS
    latency = next(pb for pb in shipped if pb.id == "was.latency")
    assert [s.id for s in latency.steps] == [f"S{i}" for i in range(10)]
    assert latency.window.before_min == 30 and latency.window.after_min == 10
    assert latency.window.baseline == ["prev_day", "prev_week"]
    assert latency.deep_dive.focus
    for pb in shipped:
        assert pb.runbook.meaning and pb.runbook.impact and pb.runbook.owner
        assert pb.steps[-1].action == "synthesize"


@pytest.mark.parametrize("type_id", list(TYPE_FACTS))
def test_select_with_shipped_yaml(type_id: str, shipped: tuple[Playbook, ...]) -> None:
    pb, matched_by = select_playbook(TYPE_FACTS[type_id], shipped)
    assert pb is not None
    if type_id in ("db.session", FALLBACK_ID):
        assert (pb.id, matched_by) == (FALLBACK_ID, "fallback")
    else:
        assert pb.id == type_id
        assert matched_by == "match:" + "+".join(
            k for k in ("resource_type", "alarm_kind", "any_kind") if TYPE_MATCHES[type_id].get(k)
        )


@pytest.fixture(scope="module")
def full_set() -> list[Playbook]:
    return [parse_playbook(_pb(tid, m)) for tid, m in TYPE_MATCHES.items()]


@pytest.mark.parametrize("type_id", list(TYPE_FACTS))
def test_select_with_full_type_set(type_id: str, full_set: list[Playbook]) -> None:
    pb, matched_by = select_playbook(TYPE_FACTS[type_id], full_set)
    assert pb is not None and pb.id == type_id
    assert (matched_by == "fallback") == (type_id == FALLBACK_ID)


def test_select_order_independent_of_list_order(full_set: list[Playbook]) -> None:
    for facts in TYPE_FACTS.values():
        assert (
            select_playbook(facts, full_set)[0]
            == select_playbook(facts, list(reversed(full_set)))[0]
        )


def test_select_priority_then_list_order() -> None:
    a = parse_playbook(_pb("a.first", {"alarm_kind": "cpu"}))
    b = parse_playbook(_pb("b.second", {"alarm_kind": "cpu", "priority": 1}))
    c = parse_playbook(_pb("c.third", {"alarm_kind": "cpu"}))
    facts = AlarmFacts("host", "cpu", ())
    assert select_playbook(facts, [a, b, c])[0].id == "b.second"
    assert select_playbook(facts, [c, a])[0].id == "c.third"


def test_select_case_insensitive_resource_type(shipped: tuple[Playbook, ...]) -> None:
    pb, _ = select_playbook(AlarmFacts("APM.INSTANCE", "apm", ("Latency",)), shipped)
    assert pb.id == "was.latency"


def test_select_without_fallback_returns_none() -> None:
    only = [parse_playbook(_pb("host.cpu", {"alarm_kind": "cpu"}))]
    assert select_playbook(AlarmFacts("host", "memory", ()), only) == (None, "none")


def test_select_deterministic(full_set: list[Playbook]) -> None:
    for facts in TYPE_FACTS.values():
        results = {select_playbook(facts, full_set)[0].id for _ in range(5)}
        assert len(results) == 1


# ---------------------------------------------------------------------------
# 스키마 거부
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["id", "version", "steps", "runbook"])
def test_reject_missing_required_key(key: str) -> None:
    data = _pb("host.cpu", {"alarm_kind": "cpu"})
    del data[key]
    with pytest.raises(ValidationError):
        parse_playbook(data)


@pytest.mark.parametrize("key", ["meaning", "impact", "owner", "last_reviewed"])
def test_reject_runbook_missing_required(key: str) -> None:
    data = _pb("host.cpu", {"alarm_kind": "cpu"})
    del data["runbook"][key]
    with pytest.raises(ValidationError):
        parse_playbook(data)


def test_reject_runbook_blank_text() -> None:
    data = _pb("host.cpu", {"alarm_kind": "cpu"})
    data["runbook"]["meaning"] = "   "
    with pytest.raises(ValidationError):
        parse_playbook(data)


@pytest.mark.parametrize(
    "steps",
    [
        [{"id": "S1", "action": "apm.unknown_action"}],
        [{"id": "S1", "action": "apm_app_health"}],  # 도구명은 액션 ID가 아니다
        [{"id": "S10", "action": "synthesize"}],
        [{"id": "S0′", "action": "scope.host_to_was"}],
        [{"id": "S1", "action": "synthesize"}, {"id": "S1", "action": "synthesize"}],
        [{"id": "S1", "action": "synthesize", "narrate": {"focus": "x", "max_sentences": 5}}],
        [{"id": "S1", "action": "synthesize", "for_each": "hosts"}],
        [{"id": "S1", "action": "synthesize", "extra_key": 1}],
    ],
)
def test_reject_bad_steps(steps: list) -> None:
    with pytest.raises(ValidationError):
        parse_playbook(_pb("host.cpu", {"alarm_kind": "cpu"}, steps=steps))


@pytest.mark.parametrize(
    "when",
    [
        "S1.verdict != normal",  # 맨 이름 — 따옴표 필요
        "S1.verdict ==",
        "S1.verdict = 'x'",
        "(S1.verdict == 'x'",
        "S1.verdict == 'x' and",
        "S1.verdict in ['a',",
        "__import__('os')",
        "S1.verdict == 'x'; 1",
        "",
    ],
)
def test_reject_when_syntax(when: str) -> None:
    steps = [
        {"id": "S1", "action": "apm.golden_signals"},
        {"id": "S3", "action": "apm.slow_tx_breakdown", "when": when},
    ]
    with pytest.raises(ValidationError):
        parse_playbook(_pb("host.cpu", {"alarm_kind": "cpu"}, steps=steps))


def test_reject_when_forward_step_reference() -> None:
    steps = [
        {"id": "S1", "action": "apm.golden_signals", "when": "S3.dominant == 'sql'"},
        {"id": "S3", "action": "apm.slow_tx_breakdown"},
    ]
    with pytest.raises(ValidationError):
        parse_playbook(_pb("host.cpu", {"alarm_kind": "cpu"}, steps=steps))


def test_reject_mitigation_when_syntax() -> None:
    data = _pb("host.cpu", {"alarm_kind": "cpu"})
    data["runbook"]["mitigation"] = [
        {
            "when": "db_split == acquire",
            "action": "a",
            "risk": "low",
            "verification": "v",
            "rollback": "r",
        }
    ]
    with pytest.raises(ValidationError):
        parse_playbook(data)


def test_reject_match_rules() -> None:
    with pytest.raises(ValidationError):
        parse_playbook(_pb("host.cpu", None))  # 일반 플레이북에 빈 match
    with pytest.raises(ValidationError):
        parse_playbook(_pb("fallback", {"alarm_kind": "cpu"}))  # fallback에 match
    with pytest.raises(ValidationError):
        parse_playbook(_pb("host.cpu", {"alarm_kind": "cpu", "unknown": 1}))


def test_deep_dive_mode() -> None:
    pb = parse_playbook(_pb("host.cpu", {"alarm_kind": "cpu"}, deep_dive={"mode": False}))
    assert pb.deep_dive.mode == "off"  # YAML의 따옴표 없는 off
    assert (
        parse_playbook(
            _pb("host.cpu", {"alarm_kind": "cpu"}, deep_dive={"mode": "parallel"})
        ).deep_dive.mode
        == "parallel"
    )
    with pytest.raises(ValidationError):
        parse_playbook(_pb("host.cpu", {"alarm_kind": "cpu"}, deep_dive={"mode": "always"}))


def test_runbook_is_stale() -> None:
    data = _pb("host.cpu", {"alarm_kind": "cpu"})
    data["runbook"]["review_every_days"] = 10
    rb = parse_playbook(data).runbook
    assert rb.is_stale(date(2026, 10, 27)) is False
    assert rb.is_stale(date(2026, 10, 28)) is True


def test_step_actions_have_no_tool_names() -> None:
    assert all("_" not in a.split(".")[0] and "." in a or a == "synthesize" for a in STEP_ACTIONS)
    for tool_prefix in ("apm_", "polestar_"):
        assert not any(a.startswith(tool_prefix) for a in STEP_ACTIONS)


# ---------------------------------------------------------------------------
# 로더
# ---------------------------------------------------------------------------


def _write(dirpath: Path, name: str, text: str) -> None:
    (dirpath / name).write_text(text, encoding="utf-8")


def test_loader_disables_bad_file_only(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    for src in DEFAULT_PLAYBOOK_DIR.glob("*.yaml"):
        _write(tmp_path, src.name, src.read_text(encoding="utf-8"))
    _write(
        tmp_path,
        "bad_action.yaml",
        "id: x.bad\nversion: 1\nmatch: {alarm_kind: cpu}\nsteps: [{id: S1, action: nope}]\n"
        "runbook: {meaning: m, impact: i, owner: o, last_reviewed: 2026-10-17}\n",
    )
    _write(tmp_path, "broken.yaml", "id: [unterminated\n")
    _write(tmp_path, "list.yaml", "- a\n- b\n")
    with caplog.at_level(logging.WARNING, logger=loader_mod.__name__):
        result = load_playbooks(tmp_path)
    assert result.error is None
    assert sorted(pb.id for pb in result.playbooks) == SHIPPED_IDS
    assert sorted(name for name, _ in result.rejected) == [
        "bad_action.yaml",
        "broken.yaml",
        "list.yaml",
    ]
    assert "bad_action.yaml" in caplog.text


def test_loader_duplicate_id(tmp_path: Path) -> None:
    text = (DEFAULT_PLAYBOOK_DIR / "fallback.yaml").read_text(encoding="utf-8")
    _write(tmp_path, "fallback.yaml", text)
    _write(tmp_path, "zz_copy.yaml", text)
    result = load_playbooks(tmp_path)
    assert [pb.id for pb in result.playbooks] == ["fallback"]
    assert result.rejected[0][0] == "zz_copy.yaml"


def test_loader_fallback_missing_or_invalid(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "was.latency.yaml",
        (DEFAULT_PLAYBOOK_DIR / "was.latency.yaml").read_text(encoding="utf-8"),
    )
    assert load_playbooks(tmp_path).error == "fallback 플레이북 없음"
    _write(
        tmp_path,
        "fallback.yaml",
        "id: fallback\nversion: 1\nsteps: [{id: S9, action: synthesize}]\n",
    )  # runbook 없음
    result = load_playbooks(tmp_path)
    assert result.error == "fallback 플레이북 무효"
    assert result.fallback is None


def test_loader_missing_dir(tmp_path: Path) -> None:
    result = load_playbooks(tmp_path / "nope")
    assert result.playbooks == () and result.error


def test_loader_deterministic() -> None:
    a, b = load_playbooks(), load_playbooks()
    assert [pb.model_dump() for pb in a.playbooks] == [pb.model_dump() for pb in b.playbooks]


# ---------------------------------------------------------------------------
# `when` 평가기
# ---------------------------------------------------------------------------

CTX: dict = {
    "S1": {"verdict": "warning", "tps": 120, "ok": True},
    "S2": {"pool": {"verdict": "exhausted", "active": 50}},
    "S3": {"top1": {"acquire_ms": 15600, "sql_ms": 7.5}, "dominant": "sql"},
    "S4": {"cpu": None},
    "db_split": "acquire",
    "dominant": "sql",
}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("S1.verdict != 'normal'", T),
        ('S1.verdict == "warning"', T),
        ("S1.verdict == 'normal'", F),
        ("S2.pool.verdict == 'exhausted'", T),
        ("db_split == 'acquire'", T),
        ("S3.top1.acquire_ms > S3.top1.sql_ms", T),
        ("S3.top1.acquire_ms < S3.top1.sql_ms", F),
        ("S3.top1.sql_ms <= 7.5", T),
        ("S3.top1.sql_ms >= 7.6", F),
        ("S1.tps == 120.0", T),
        ("S1.tps > -1", T),
        ("S1.tps != 120", F),
        ("dominant in ['sql', 'fetch']", T),
        ("dominant in ['external', 1]", F),
        ("S1.ok", T),
        ("not S1.ok", F),
        ("not (S1.verdict == 'normal')", T),
        ("db_split == 'acquire' and S1.tps > 100", T),
        ("db_split == 'acquire' and S1.tps > 200", F),
        ("db_split == 'execute' or S1.tps > 100", T),
        ("db_split == 'execute' or S1.tps > 200", F),
        ("(dominant == 'sql' or dominant == 'fetch') and not S1.verdict == 'normal'", T),
        # 결손 → UNKNOWN
        ("S2.pool.wait == 0", U),
        ("S9.anything != 'x'", U),
        ("S4.cpu > 80", U),  # None 값
        ("S2.pool > 1", U),  # 스칼라가 아닌 값
        ("scope == 'instance'", U),  # 문맥에 없는 판독 이름
        ("scope in ['instance']", U),
        ("not S2.pool.wait == 0", U),
        ("S1.verdict > 1", U),  # 수치가 아닌 대소 비교
        # Kleene
        ("S2.pool.wait == 0 and S1.tps > 200", F),
        ("S2.pool.wait == 0 and S1.tps > 100", U),
        ("S2.pool.wait == 0 or S1.tps > 100", T),
        ("S2.pool.wait == 0 or S1.tps > 200", U),
        # 형 불일치 동등 비교는 결정적 False
        ("S1.tps == '120'", F),
        ("S1.ok == 1", F),
    ],
)
def test_when_eval_table(source: str, expected: Tri) -> None:
    assert compile_expr(source).evaluate(CTX) is expected


def test_when_empty_context_is_unknown() -> None:
    assert compile_expr("S1.verdict != 'normal'").evaluate({}) is U


def test_when_step_refs() -> None:
    assert compile_expr("S3.top1.acquire_ms > S0r.x and db_split == 'acquire'").step_refs() == {
        "S3",
        "S0r",
    }


def test_when_limits() -> None:
    with pytest.raises(ExprSyntaxError):
        compile_expr("S1.a == 1 and " * 60 + "S1.a == 1")
    with pytest.raises(ExprSyntaxError):
        compile_expr("(" * 20 + "S1.ok" + ")" * 20)


def test_list_condition() -> None:
    match = compile_list_condition(
        {"all": ["S2.pool.verdict == 'exhausted'", "db_split == 'acquire'"]}
    )
    assert match.evaluate(CTX) is T
    assert (
        compile_list_condition(
            {"all": ["S3.top1.acquire_ms > S3.top1.sql_ms", "S2.pool.wait > 0"]}
        ).evaluate(CTX)
        is U
    )
    assert (
        compile_list_condition({"any": ["S2.pool.wait > 0", "dominant == 'sql'"]}).evaluate(CTX)
        is T
    )
    assert (
        compile_list_condition({"any": ["dominant == 'cpu'", "S2.pool.wait > 0"]}).evaluate(CTX)
        is U
    )
    both = compile_list_condition({"all": ["dominant == 'sql'"], "any": ["S1.tps > 999"]})
    assert both.evaluate(CTX) is F
    assert eval_all([], CTX) is T and eval_any([], CTX) is F
    for bad in (
        {},
        {"none": ["S1.ok"]},
        {"all": []},
        {"all": "S1.ok"},
        {"all": ["S1.verdict == x"]},
    ):
        with pytest.raises(ExprSyntaxError):
            compile_list_condition(bad)


def test_when_deterministic() -> None:
    expr = compile_expr("(dominant == 'cpu' or S2.pool.wait > 0) and S1.tps > 100")
    assert {expr.evaluate(copy.deepcopy(CTX)) for _ in range(5)} == {U}


def test_shipped_when_expressions_evaluate(shipped: tuple[Playbook, ...]) -> None:
    latency = next(pb for pb in shipped if pb.id == "was.latency")
    s3 = next(s for s in latency.steps if s.id == "S3")
    assert s3.when_expr is not None and s3.when_expr.evaluate(CTX) is T
    mitigation = latency.runbook.mitigation[0]
    assert mitigation.when_expr is not None and mitigation.when_expr.evaluate(CTX) is T


# ---------------------------------------------------------------------------
# eval/exec 미사용 · 순수 도메인
# ---------------------------------------------------------------------------

_FORBIDDEN_NAMES = {"eval", "exec", "compile", "__import__"}  # 내장 호출
_FORBIDDEN_ATTRS = {"eval", "exec", "literal_eval"}  # ast.literal_eval 등 (re.compile은 허용)


@pytest.mark.parametrize("module", [expr_mod, playbook_mod, loader_mod])
def test_no_eval_exec(module) -> None:
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                assert fn.id not in _FORBIDDEN_NAMES, f"{module.__name__}: {fn.id}"
            elif isinstance(fn, ast.Attribute):
                assert fn.attr not in _FORBIDDEN_ATTRS, f"{module.__name__}: {fn.attr}"
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
            assert not any(n == "ast" or n.startswith("ast.") for n in names), module.__name__


@pytest.mark.parametrize("module", [expr_mod, playbook_mod])
def test_domain_modules_do_no_io(module) -> None:
    tree = ast.parse(inspect.getsource(module))
    imported = {
        (a.name if isinstance(n, ast.Import) else n.module or "").split(".")[0]
        for n in ast.walk(tree)
        if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in n.names
    }
    assert not imported & {"os", "pathlib", "io", "yaml", "open", "logging"}
    assert "open(" not in inspect.getsource(module)
