"""plans/187 W8 — KB 읽기 측: 알려진 오류(KEDB) 스키마·로더·결정적 일치 · 가설 1순위 고정 ·
런북 블록 · 브리핑 키 · 심층 생략 · 런북 커버리지 스크립트 · 런타임 파일 쓰기 0.

W0 골든 픽스처(case1 JDBC 획득 지연)를 W2 `FakeCaller`로 재생한다. 실 LLM·MCP·DB 호출 0.
"""

from __future__ import annotations

import ast
import copy
import json
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import runbook_coverage
import yaml
from pydantic import ValidationError
from sre_agent.application.investigation_dispatcher import (
    DEEP_SKIP_KNOWN_ERROR,
    PlaybookWiring,
)
from sre_agent.application.playbook_narrator import PlaybookNarrator
from sre_agent.application.playbook_runner import finalize_playbook_run
from sre_agent.domain import known_error as ke_mod
from sre_agent.domain.known_error import (
    NOT_VALIDATED_BADGE,
    RESULT_CANDIDATE,
    RESULT_INCONCLUSIVE,
    RESULT_MATCH,
    RESULT_NO_MATCH,
    evaluate_known_error,
    evaluate_known_errors,
    is_pinned,
    parse_known_error,
    runbook_briefing,
)
from sre_agent.domain.playbook import parse_playbook
from sre_agent.domain.remediation import (
    _CANDIDATES_BY_SIGNATURE,
    COMMAND_LITERAL_PATTERNS,
    find_command_literal,
)
from sre_agent.infrastructure import known_error_loader as loader_mod
from sre_agent.infrastructure.known_error_loader import (
    DEFAULT_KNOWN_ERROR_DIR,
    load_known_errors,
)
from sre_agent.infrastructure.playbook_loader import DEFAULT_PLAYBOOK_DIR, load_playbooks
from sre_agent.interface import mcp_service
from test_plan187_w2_runner import FakeCaller, _load, _playbook_for, _run
from test_plan187_w4_dispatch import (
    FakeDeep,
    _comparable,
    alarm_of,
    build,
    make_settings,
    run_job,
)

TODAY = date(2026, 10, 19)
CONFIG_DIR = DEFAULT_KNOWN_ERROR_DIR.parent
#: 형식 예시 레코드는 런타임 경로가 아니라 테스트 픽스처에 둔다(검증 지적 — 런타임 기본 적재 0건).
EXAMPLE_DIR = Path(__file__).resolve().parent / "fixtures" / "plan187" / "known_errors"
EXAMPLE = load_known_errors(EXAMPLE_DIR).known_errors

BASE_KE: dict[str, Any] = {
    "id": "KE-T-001",
    "title": "테스트 레코드",
    "state": "validated",
    "issue": "응답 지연",
    "environment": {"playbook": "was.latency"},
    "match": {"all": ["S2.pool == 'exhausted'", "db_split == 'acquire'"]},
    "cause": "풀 점유",
    "cause_test": {"all": ["S3.top1.acquire_ms > S3.top1.sql_ms"]},
    "workaround": [
        {"action": "담당 업무팀에 동시 실행 축소 요청", "risk": "low",
         "verification": "풀 대기 해소", "rollback": "해당 없음"},
    ],
    "owner": "WAS운영팀",
    "last_reviewed": "2026-10-01",
}

#: 판정 4종을 내는 평가 문맥(스텝 ID·판독 이름 → 값).
CTX: dict[str, dict[str, Any]] = {
    RESULT_MATCH: {"S2": {"pool": "exhausted"}, "db_split": "acquire",
                   "S3": {"top1": {"acquire_ms": 15612, "sql_ms": 7}}},
    RESULT_CANDIDATE: {"S2": {"pool": "exhausted"}, "db_split": "acquire",
                       "S3": {"top1": {"acquire_ms": 3, "sql_ms": 7}}},
    RESULT_INCONCLUSIVE: {"S2": {"pool": "exhausted"}, "db_split": None},
    RESULT_NO_MATCH: {"S2": {"pool": "exhausted"}, "db_split": "execute",
                      "S3": {"top1": {"acquire_ms": 3, "sql_ms": 7}}},
}
SCOPE = {"instance": "A1", "solution": "apm"}
ALARM_TIME = "2026-10-19T10:00:00"  # 월요일


def ke_of(**over: Any) -> Any:
    data = copy.deepcopy(BASE_KE)
    data.update(over)
    return parse_known_error(data)


def evaluate(ke: Any, ctx: dict, *, scope: dict | None = None, alarm_time: str | None = ALARM_TIME):
    return evaluate_known_error(
        ke, playbook_id="was.latency", scope=SCOPE if scope is None else scope, ctx=ctx,
        alarm_time=alarm_time, reading_steps={"db_split": ["S3"]},
    )


def case1_run(*, profile: dict | None = None, known_errors: Any = EXAMPLE,
              narrator: Any = None, today: date = TODAY):
    """case1 픽스처 실행 → (구조화 필드 주입) → 실행 후 처리.

    현 게이트웨이·S3 스텝은 커넥션 획득·SQL 실행 구조화 필드를 싣지 않는다(W0 실측). 「주입」 변형은
    그 필드가 생겼을 때를 흉내 내려고 S3 스텝 결과의 `profile`에 직접 넣는다.
    """
    fx = _load("case1_jdbc_acquire")
    run = _run(fx, FakeCaller(fx))
    if profile is not None:
        s3 = run.section("S3")
        assert s3 is not None and s3.status == "ok"
        s3.data["profile"] = {**(s3.data.get("profile") or {}), **profile}
    pb, _ = _playbook_for(fx)
    finalize_playbook_run(
        run, pb, known_errors=known_errors, payload=fx["alarm"], today=today, narrator=narrator
    )
    return run


def hyp_ids(run: Any) -> list[tuple[int, str, str]]:
    return [(h["rank"], h["rule_id"], h["status"]) for h in run.synthesis["hypotheses"]]


# ── 예시 레코드 · 스키마 ────────────────────────────────────────────────────


def test_runtime_dir_loads_no_records_by_default() -> None:
    """런타임 기본 경로는 형식 안내 README만 둔다 — 적재 0건 · 거부 0건(README는 무시)."""
    assert (DEFAULT_KNOWN_ERROR_DIR / "README.md").is_file()
    assert load_known_errors() == loader_mod.KnownErrorLoadResult()


def test_example_record_loads_as_validated() -> None:
    result = load_known_errors(EXAMPLE_DIR)
    assert result.rejected == ()
    assert [k.id for k in result.known_errors] == ["KE-EXAMPLE-001"]
    ke = result.known_errors[0]
    assert ke.state == "validated" and ke.owner and ke.last_reviewed
    assert ke.environment.playbook == "was.latency"


@pytest.mark.parametrize("key", ["owner", "last_reviewed"])
def test_validated_requires_owner_and_last_reviewed(key: str) -> None:
    data = copy.deepcopy(BASE_KE)
    del data[key]
    with pytest.raises(ValidationError, match="쓰기 지점 차단"):
        parse_known_error(data)
    data["state"] = "not_validated"
    assert parse_known_error(data).state == "not_validated"  # 다른 상태는 허용


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(extra="x"),                                    # extra forbid
        lambda d: d.update(state="draft"),                                # 상태 4종 밖
        lambda d: d.pop("cause_test"),                                    # cause_test 필수
        lambda d: d.update(match={"all": ["S2.pool == "]}),               # 구문 오류
        lambda d: d.update(match={"none": ["db_split == 'acquire'"]}),    # all·any 밖
        lambda d: d.update(id="ke-1"),                                    # ID 형식
        lambda d: d["environment"].update(time_window={"weekday": ["5-1"]}),
        lambda d: d["environment"].update(time_window={"hours": ["9-18"]}),
        lambda d: d["workaround"][0].pop("rollback"),                     # 롤백 필수
        lambda d: d["workaround"][0].update(risk="critical"),             # 위험도 3종
    ],
)
def test_schema_rejects_invalid(mutate: Any) -> None:
    data = copy.deepcopy(BASE_KE)
    mutate(data)
    with pytest.raises(ValidationError):
        parse_known_error(data)


# ── 상태 4종 × 판정 4종 표 ──────────────────────────────────────────────────


@pytest.mark.parametrize("expected", [RESULT_MATCH, RESULT_CANDIDATE, RESULT_INCONCLUSIVE,
                                      RESULT_NO_MATCH])
def test_evaluation_results(expected: str) -> None:
    assert evaluate(ke_of(), CTX[expected]).result == expected


@pytest.mark.parametrize("state", ["wip", "not_validated", "validated", "archived"])
@pytest.mark.parametrize("expected", [RESULT_MATCH, RESULT_CANDIDATE, RESULT_INCONCLUSIVE,
                                      RESULT_NO_MATCH])
def test_state_by_result_display_table(state: str, expected: str) -> None:
    ke = ke_of(state=state)
    shown = evaluate_known_errors(
        [ke], playbook_id="was.latency", scope=SCOPE, ctx=CTX[expected], alarm_time=ALARM_TIME
    )
    if state in ("wip", "archived") or expected == RESULT_NO_MATCH:
        assert shown == []  # wip·archived는 평가조차 하지 않는다 · 불일치는 싣지 않는다
        return
    assert [ev.result for _, ev in shown] == [expected]
    _, ev = shown[0]
    assert is_pinned(ke, ev) is (state == "validated" and expected == RESULT_MATCH)
    entry = ke_mod.known_error_briefing(ke, ev, TODAY)
    assert entry["badge"] == (NOT_VALIDATED_BADGE if state == "not_validated" else None)


def test_cause_test_missing_is_inconclusive_with_missing_steps() -> None:
    ctx = {"S2": {"pool": "exhausted"}, "db_split": "acquire"}  # S3.top1 결손
    ev = evaluate(ke_of(), ctx)
    assert ev.result == RESULT_INCONCLUSIVE
    assert ev.unknown == ("S3.top1.acquire_ms > S3.top1.sql_ms",)
    assert ev.missing_steps == ("S3",)


def test_match_unknown_reports_reading_step() -> None:
    ev = evaluate(ke_of(), CTX[RESULT_INCONCLUSIVE])
    assert "db_split == 'acquire'" in ev.unknown and "S3" in ev.missing_steps


def test_other_playbook_is_no_match_without_evaluation() -> None:
    ev = evaluate_known_error(ke_of(), playbook_id="was.heap", scope=SCOPE,
                              ctx=CTX[RESULT_MATCH], alarm_time=ALARM_TIME)
    assert ev.result == RESULT_NO_MATCH and ev.satisfied == ()


# ── environment (대상 · 시간대) ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("targets", "scope", "expected"),
    [
        ({"instance_glob": ["A*"]}, {"instance": "A1"}, RESULT_MATCH),
        ({"instance_glob": ["A*"]}, {"instance": "B1"}, RESULT_NO_MATCH),
        ({"instance_glob": ["A*"]}, {"instance": "a1"}, RESULT_NO_MATCH),  # 대소문자 구분
        ({"instance_glob": ["A*"]}, {}, RESULT_INCONCLUSIVE),              # 대상 결손
        ({"solution": ["APM"]}, {"solution": "apm"}, RESULT_MATCH),        # 대소문자 무시
        ({"solution": ["polestar"]}, {"solution": "apm"}, RESULT_NO_MATCH),
        ({"solution": ["apm"]}, {}, RESULT_INCONCLUSIVE),
    ],
)
def test_environment_targets(targets: dict, scope: dict, expected: str) -> None:
    ke = ke_of(environment={"playbook": "was.latency", "targets": targets})
    assert evaluate(ke, CTX[RESULT_MATCH], scope=scope).result == expected


@pytest.mark.parametrize(
    ("window", "alarm_time", "expected"),
    [
        ({"weekday": ["1-5"], "hours": ["09:00-18:00"]}, "2026-10-19T10:00:00", RESULT_MATCH),
        ({"weekday": ["1-5"]}, "2026-10-18T10:00:00", RESULT_NO_MATCH),        # 일요일
        ({"weekday": [7]}, "2026-10-18T10:00:00", RESULT_MATCH),
        ({"hours": ["02:00-04:00"]}, "2026-10-19T10:00:00", RESULT_NO_MATCH),
        ({"hours": ["09:00-10:00"]}, "2026-10-19T10:00:00", RESULT_NO_MATCH),  # 끝 제외
        ({"hours": ["22:00-02:00"]}, "2026-10-19T01:30:00", RESULT_MATCH),     # 자정 넘김
        ({"hours": ["22:00-02:00"]}, "2026-10-19T03:00:00", RESULT_NO_MATCH),
        ({"hours": ["09:00-18:00"]}, None, RESULT_INCONCLUSIVE),               # 시각 결손
    ],
)
def test_environment_time_window(window: dict, alarm_time: str | None, expected: str) -> None:
    ke = ke_of(environment={"playbook": "was.latency", "time_window": window})
    assert evaluate(ke, CTX[RESULT_MATCH], alarm_time=alarm_time).result == expected


def test_yaml_weekday_range_literal_parses_as_string() -> None:
    doc = yaml.safe_load("weekday: [1-5]")
    assert doc == {"weekday": ["1-5"]}


# ── 수용 기준 9 — case1 JDBC 획득 지연 ─────────────────────────────────────


def test_ac9_case1_original_inconclusive_gateway_lacks_structured_fields() -> None:
    """원본 case1 — 게이트웨이에 커넥션 획득·SQL 실행 구조화 필드가 없다(현 한계) → 판단 보류."""
    run = case1_run()
    assert [(k["id"], k["result"]) for k in run.known_errors] == [
        ("KE-EXAMPLE-001", RESULT_INCONCLUSIVE)
    ]
    entry = run.known_errors[0]
    assert entry["missing_steps"] == ["S3"] and entry["pinned"] is False
    assert "S3.top1.acquire_ms > S3.top1.sql_ms" in entry["unknown"]
    assert hyp_ids(run) == hyp_ids(case1_run(known_errors=()))  # 순위 영향 0


def test_ac9_case1_injected_structured_acquire_gt_sql_matches() -> None:
    """구조화 필드 주입(acquire 15612ms > sql 7ms · 현 게이트웨이에는 없음) → validated 「일치」."""
    run = case1_run(profile={"acquire_ms": 15612, "execute_ms": 7})
    assert run.readings.values["db_split"] == "acquire"
    entry = run.known_errors[0]
    assert entry["result"] == RESULT_MATCH and entry["pinned"] is True
    assert entry["result_label"] == "알려진 문제 일치"
    assert "S3.top1.acquire_ms > S3.top1.sql_ms" in entry["satisfied"]
    top = run.synthesis["hypotheses"][0]
    assert (top["rank"], top["rule_id"], top["status"]) == (1, "KE:KE-EXAMPLE-001", "supported")
    assert "KE-EXAMPLE-001" in top["title"]
    assert set(top["evidence_steps"]) == {"S2", "S3"}
    assert run.synthesis["top_inconclusive"] is False
    first = run.synthesis["actions"][0]
    assert first["source"] == "known_error" and first["known_error"] == "KE-EXAMPLE-001"
    assert first["confidence"] == "high" and first["review_only"] is False


def test_ac9_case1_injected_execute_dominant_is_no_match() -> None:
    """같은 증상이지만 db_split=execute(acquire 3ms < sql 7ms) 구조화 필드 주입 → 「불일치」."""
    run = case1_run(profile={"acquire_ms": 3, "execute_ms": 15600})
    assert run.readings.values["db_split"] == "execute"
    assert run.known_errors == []  # 불일치는 브리핑에 싣지 않는다
    assert all(not h["rule_id"].startswith("KE:") for h in run.synthesis["hypotheses"])
    ke = EXAMPLE[0]
    ev = evaluate_known_error(
        ke, playbook_id="was.latency", scope=SCOPE,
        ctx={"S2": {"pool": "exhausted"}, "db_split": "execute",
             "S3": {"top1": {"acquire_ms": 3, "sql_ms": 7}}},
        alarm_time=ALARM_TIME,
    )
    assert ev.result == RESULT_NO_MATCH and "db_split == 'acquire'" in ev.unsatisfied


@pytest.mark.parametrize("state", ["not_validated", "wip", "archived"])
def test_non_validated_match_has_zero_rank_effect(state: str) -> None:
    profile = {"acquire_ms": 15612, "execute_ms": 7}
    baseline = case1_run(profile=profile, known_errors=())
    record = EXAMPLE[0].model_copy(update={"state": state})
    run = case1_run(profile=profile, known_errors=[record])
    assert run.synthesis["hypotheses"] == baseline.synthesis["hypotheses"]
    assert run.synthesis["actions"] == baseline.synthesis["actions"]
    if state == "not_validated":
        assert [(k["result"], k["badge"]) for k in run.known_errors] == [
            (RESULT_MATCH, NOT_VALIDATED_BADGE)
        ]
    else:
        assert run.known_errors == []


def test_synthesis_input_carries_only_deterministic_ke_fact() -> None:
    seen: list[str] = []

    def llm(messages: list[dict[str, str]]) -> str:
        seen.append(messages[-1]["content"])
        return "수집된 표의 값을 확인했다."

    case1_run(profile={"acquire_ms": 15612, "execute_ms": 7}, narrator=PlaybookNarrator(llm))
    synth = seen[-1]
    assert "알려진 오류 KE-EXAMPLE-001 일치" in synth
    assert EXAMPLE[0].cause not in synth  # 사람 서술(원인 문장)은 종합 입력이 아니다
    seen.clear()
    case1_run(narrator=PlaybookNarrator(llm))  # 판단 보류 → 종합 입력에 KE 없음
    assert "KE-EXAMPLE-001" not in seen[-1]


# ── 우회 항목 — remediation 경계 규칙 확장 ─────────────────────────────────


@pytest.mark.parametrize(
    "literal", ["systemctl restart app", "kill -9 1234", "rm -rf /data/tmp", "dmesg -C"]
)
@pytest.mark.parametrize("field", ["action", "verification", "rollback"])
def test_workaround_command_literal_rejected(literal: str, field: str) -> None:
    data = copy.deepcopy(BASE_KE)
    data["workaround"][0][field] = f"조치: {literal}"
    with pytest.raises(ValidationError, match="변경 명령 형태 금지"):
        parse_known_error(data)


def test_command_literal_patterns_shared_with_remediation_catalog() -> None:
    assert len(COMMAND_LITERAL_PATTERNS) == 4
    for candidates in _CANDIDATES_BY_SIGNATURE.values():
        for _action, _risk, *extra in candidates:
            for text in (_action, *extra):
                assert find_command_literal(text) is None, text
    for ke in EXAMPLE:
        for w in ke.workaround:
            assert all(find_command_literal(t) is None
                       for t in (w.action, w.verification, w.rollback))


def test_high_risk_workaround_review_only_unless_validated_match() -> None:
    high = [{"action": "인스턴스 재기동 검토", "risk": "high",
             "verification": "p95 복귀", "rollback": "되돌릴 수 없음"}]
    ke = ke_of(workaround=high)
    cand = evaluate(ke, CTX[RESULT_CANDIDATE])
    item = ke_mod.workaround_items(ke, cand)[0]
    assert item["review_only"] is True and item["line"].startswith("[검토 필요] ")
    match = evaluate(ke, CTX[RESULT_MATCH])
    item = ke_mod.workaround_items(ke, match)[0]
    assert item["review_only"] is False and item["confidence"] == "high"
    assert "검증: p95 복귀" in item["line"] and "롤백: 되돌릴 수 없음" in item["line"]


def test_known_error_module_has_no_execution_means() -> None:
    import inspect

    src = inspect.getsource(ke_mod).lower()
    for banned in ("subprocess", "os.system", "popen", "paramiko", "asyncssh"):
        assert banned not in src


# ── 로더 · 쓰기 0 ──────────────────────────────────────────────────────────


def test_loader_disables_bad_files_and_duplicates(tmp_path: Path,
                                                  caplog: pytest.LogCaptureFixture) -> None:
    (tmp_path / "a.yaml").write_text(yaml.safe_dump(BASE_KE, allow_unicode=True), "utf-8")
    (tmp_path / "b.yaml").write_text(yaml.safe_dump(BASE_KE, allow_unicode=True), "utf-8")
    bad = copy.deepcopy(BASE_KE)
    bad["id"] = "KE-T-002"
    del bad["owner"]
    (tmp_path / "c.yaml").write_text(yaml.safe_dump(bad, allow_unicode=True), "utf-8")
    (tmp_path / "d.yaml").write_text("id: [broken\n", "utf-8")
    with caplog.at_level(logging.WARNING):
        result = load_known_errors(tmp_path)
    assert [k.id for k in result.known_errors] == ["KE-T-001"]
    assert [name for name, _ in result.rejected] == ["b.yaml", "c.yaml", "d.yaml"]
    assert "알려진 오류 비활성" in caplog.text


def test_loader_missing_dir_is_empty(tmp_path: Path) -> None:
    assert load_known_errors(tmp_path / "none").known_errors == ()


_WRITE_NAMES = {"write_text", "write_bytes", "write", "writelines", "mkdir", "unlink", "rename",
                "replace", "touch", "rmdir", "safe_dump", "dump", "remove", "makedirs"}


@pytest.mark.parametrize("module", [loader_mod, ke_mod])
def test_known_error_modules_have_no_write_api(module: Any) -> None:
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in _WRITE_NAMES, node.attr
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "open", "open() 호출 금지(읽기는 Path.read_text)"
    public = {n for n in dir(module) if not n.startswith("_")}
    assert not {n for n in public if n.startswith(("save", "write", "store", "dump"))}


def _tree_state(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(p.relative_to(root)): (p.stat().st_mtime_ns, p.stat().st_size)
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def test_runtime_writes_nothing_to_config_tree() -> None:
    """조사 종단(기동 적재 + 플레이북 잡 + KE 판정) 전후 `config/` 트리 mtime·크기 불변."""
    before = _tree_state(CONFIG_DIR)
    wiring = mcp_service._default_playbook_wiring(make_settings(), lambda: None)
    assert wiring is not None and wiring.known_errors == ()
    settings = make_settings(sre_deep_dive_mode="conditional")
    caller = FakeCaller(_load("case1_jdbc_acquire"))
    d = build(settings, caller, deep=FakeDeep(settings))
    d._playbook = PlaybookWiring(playbooks=wiring.playbooks, polestar=caller, apm=caller,
                                 known_errors=EXAMPLE)
    job = run_job(d, alarm_of("case1_jdbc_acquire"))
    assert job.status == "done" and job.briefing["playbook"]["known_errors"]
    assert _tree_state(CONFIG_DIR) == before


# ── dispatcher 통합 · 브리핑 계약 · 심층 생략 ──────────────────────────────

#: case1에서 현 게이트웨이로도 완전 일치하는 validated 레코드(dispatcher 종단용).
DISPATCH_KE = parse_known_error({
    **BASE_KE,
    "id": "KE-T-DISPATCH",
    "environment": {"playbook": "was.latency", "targets": {"instance_glob": ["A*"]}},
    "match": {"all": ["S2.pool == 'exhausted'"]},
    "cause_test": {"all": ["S1.verdict == 'degraded'"]},
})


def _dispatch(known_errors: tuple, mode: str = "conditional"):
    settings = make_settings(sre_deep_dive_mode=mode)
    deep = FakeDeep(settings)
    caller = FakeCaller(_load("case1_jdbc_acquire"))
    d = build(settings, caller, deep=deep)
    d._playbook = PlaybookWiring(playbooks=d._playbook.playbooks, polestar=caller, apm=caller,
                                 known_errors=known_errors)
    return run_job(d, alarm_of("case1_jdbc_acquire")), deep


def test_conditional_deep_dive_skipped_on_validated_match_with_reason() -> None:
    baseline, deep0 = _dispatch(())
    assert baseline.briefing["deep_dive"]["status"] == "done" and deep0.jobs  # 판단 보류 → 심층
    job, deep = _dispatch((DISPATCH_KE,))
    assert deep.jobs == []
    dd = job.briefing["deep_dive"]
    assert dd["status"] == "skipped"
    assert dd["reason"] == DEEP_SKIP_KNOWN_ERROR.format(ids="KE-T-DISPATCH")
    assert any("KE-T-DISPATCH" in line for line in job.briefing["limitations"])


def test_validated_match_not_pinned_when_not_validated_deep_dive_runs() -> None:
    record = DISPATCH_KE.model_copy(update={"state": "not_validated"})
    job, deep = _dispatch((record,))
    assert deep.jobs and job.briefing["deep_dive"]["status"] == "done"
    kes = job.briefing["playbook"]["known_errors"]
    assert [(k["result"], k["badge"], k["pinned"]) for k in kes] == [
        (RESULT_MATCH, NOT_VALIDATED_BADGE, False)
    ]


def test_briefing_contract_runbook_and_known_errors() -> None:
    job, _ = _dispatch((DISPATCH_KE,))
    b = job.briefing
    pb = b["playbook"]
    rb = pb["runbook"]
    assert set(rb) == {"meaning", "impact", "mitigation", "escalation", "references", "owner",
                       "last_reviewed", "review_every_days", "stale"}
    assert rb["stale"] is False and rb["owner"]
    entry = pb["known_errors"][0]
    # 본체 W9가 읽는 키(recurrence는 본체가 덧붙인다 — sre_agent는 만들지 않는다 · D-118)
    assert {"id", "state", "result", "satisfied", "missing_steps", "workaround", "last_reviewed",
            "review_every_days", "stale"} <= set(entry)
    assert "recurrence" not in entry
    assert entry["result"] == RESULT_MATCH and entry["pinned"] is True
    # 표시 순서 = KE 우회 > 런북 완화 > 일반 조치 후보 — 권고 첫 줄이 KE 우회(remediation 형식)
    first = b["recommendation"]["items"][0]
    assert first.startswith("담당 업무팀에 동시 실행 축소 요청 (위험도 low·신뢰도 high)")
    assert "알려진 오류 KE-T-DISPATCH" in first
    assert b["playbook"]["synthesis"]["hypotheses"][0]["rule_id"] == "KE:KE-T-DISPATCH"
    json.dumps(b, ensure_ascii=False)


def test_flag_off_bit_identical_with_known_errors_wired() -> None:
    settings = make_settings(sre_playbook_enabled=False)
    caller = FakeCaller(_load("case1_jdbc_acquire"))
    base = build(settings, caller, wiring=False)
    off = build(settings, caller)
    off._playbook = PlaybookWiring(playbooks=off._playbook.playbooks, polestar=caller,
                                   apm=caller, known_errors=EXAMPLE)
    j1 = run_job(base, alarm_of("case1_jdbc_acquire"))
    j2 = run_job(off, alarm_of("case1_jdbc_acquire"))
    assert _comparable(j1) == _comparable(j2)
    assert "playbook" not in (j2.briefing or {}) and caller.calls == []


def test_mcp_service_off_does_not_load_known_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("off 경로가 알려진 오류를 적재했다")

    monkeypatch.setattr(mcp_service, "load_known_errors", boom)
    assert mcp_service._default_playbook_wiring(
        make_settings(sre_playbook_enabled=False), lambda: None
    ) is None


# ── 런북 블록 · 커버리지 ────────────────────────────────────────────────────


def test_runbook_stale_badge_keeps_content() -> None:
    pb = load_playbooks().playbooks[0]
    rb = pb.runbook
    fresh = runbook_briefing(rb, rb.last_reviewed)
    late = runbook_briefing(rb, rb.last_reviewed + timedelta(days=rb.review_every_days + 1))
    assert fresh["stale"] is False and late["stale"] is True
    assert {k: v for k, v in late.items() if k != "stale"} == {
        k: v for k, v in fresh.items() if k != "stale"
    }


def test_known_error_stale_flag() -> None:
    ke = ke_of(last_reviewed="2026-01-01", review_every_days=90)
    ev = evaluate(ke, CTX[RESULT_MATCH])
    assert ke_mod.known_error_briefing(ke, ev, TODAY)["stale"] is True
    assert ke_mod.known_error_briefing(ke, ev, date(2026, 3, 1))["stale"] is False


def test_playbook_without_runbook_fails_to_load(tmp_path: Path) -> None:
    src = yaml.safe_load((DEFAULT_PLAYBOOK_DIR / "fallback.yaml").read_text("utf-8"))
    parse_playbook(src)  # 원본은 유효
    del src["runbook"]
    (tmp_path / "fallback.yaml").write_text(yaml.safe_dump(src, allow_unicode=True), "utf-8")
    result = load_playbooks(tmp_path)
    assert result.playbooks == () and result.rejected[0][0] == "fallback.yaml"
    assert "runbook" in result.rejected[0][1]


def test_runbook_coverage_ci(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert runbook_coverage.main(["--ci"]) == 0
    assert "런북 커버리지: 100.0%" in capsys.readouterr().out
    for name in ("fallback.yaml", "was.latency.yaml"):
        (tmp_path / name).write_text((DEFAULT_PLAYBOOK_DIR / name).read_text("utf-8"), "utf-8")
    broken = yaml.safe_load((DEFAULT_PLAYBOOK_DIR / "was.heap.yaml").read_text("utf-8"))
    del broken["runbook"]["owner"]
    (tmp_path / "was.heap.yaml").write_text(yaml.safe_dump(broken, allow_unicode=True), "utf-8")
    before = _tree_state(tmp_path)
    assert runbook_coverage.main(["--ci", "--playbook-dir", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "66.7%" in out and "[런북 없음/무효] was.heap.yaml" in out
    assert runbook_coverage.main(["--playbook-dir", str(tmp_path)]) == 0  # --ci 아니면 0
    report = runbook_coverage.coverage_report(tmp_path, date(2099, 1, 1))
    assert {s["id"] for s in report["stale"]} == {"fallback", "was.latency"}
    assert _tree_state(tmp_path) == before  # 읽기 전용


def test_runbook_coverage_fallback_distribution(tmp_path: Path) -> None:
    now = datetime(2026, 10, 19, tzinfo=UTC)
    recent = (now - timedelta(days=1)).isoformat()
    old = (now - timedelta(days=40)).isoformat()

    def rec(iid: str, pid: str | None, *, ts: str = recent, status: str = "done",
            kind: str = "alarm", alarm: str | None = None) -> str:
        r: dict[str, Any] = {"investigation_id": iid, "kind": kind, "status": status, "ts": ts}
        if pid is not None:
            r["briefing"] = {"playbook": {"id": pid, "scope": {"alarm_name": alarm}}}
        else:
            r["briefing"] = {"summary": "x"}
        return json.dumps(r, ensure_ascii=False)

    lines = [
        rec("i1", None, status="running"),
        rec("i1", "was.latency"),                       # 최신 행이 이긴다
        rec("i2", "fallback", alarm="DISK_SMART"),
        rec("i3", "fallback", alarm="DISK_SMART"),
        rec("i4", "fallback"),                          # 알람 이름 없음
        rec("i5", "fallback", ts=old, alarm="OLD"),     # 창 밖
        rec("i6", "fallback", kind="manual", alarm="M"),  # 사용자 요청 제외
        rec("i7", None),                                # 플레이북 미적용(off)
        "{broken",
    ]
    path = tmp_path / "investigations.jsonl"
    path.write_text("\n".join(lines) + "\n", "utf-8")
    dist = runbook_coverage.fallback_distribution(path, 30, now=now)
    assert dist["total"] == 5 and dist["no_playbook"] == 1 and dist["bad_lines"] == 1
    assert dist["by_playbook"] == {"fallback": 3, "was.latency": 1}
    assert dist["fallback_by_alarm"] == {"DISK_SMART": 2, runbook_coverage.NO_ALARM_NAME: 1}
    assert dist["fallback_total"] == 3
