"""plans/187 독립 검증 — 본체 쪽(W9·W10·W11·W12) 경계·상한·라우팅·쓰기 차단.

- 수용 기준 12: 라우팅 결정성 · LLM 0(모듈 import 표면) · 표면어 부분 일치 오탐(교정 확인)
- 수용 기준 10: KB 기록 경로가 sre_agent 아래(심볼릭 링크 경유 포함)면 거부
- 수용 기준 13: 사용자 요청 조사 3종(diagnosis · manual · chat)이 한 카운터를 공유
- 지적 교정 확인: 빈 sub는 접수 거부(fail-closed) · 사용자 뷰에 `owner_sub`가 실리지 않는다 ·
  같은 사용자의 동시 요청은 1건만 상한 검사를 통과한다
"""

from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]


# ── W12 라우터 ─────────────────────────────────────────────────────────────

A1 = {"server_name": "A1", "hostname": "A1", "db_id": "polestar"}


def test_ac12_latency_query_routes_to_playbook() -> None:
    from src.domain.symptom_router import ROUTE_PLAYBOOK, route_symptom

    r = route_symptom("A1 응답 지연 원인 알려줘", [A1])
    assert (r.route, r.playbook_hint) == (ROUTE_PLAYBOOK, "was.latency")


def test_ac12_deictic_query_routes_to_holmesgpt() -> None:
    from src.domain.symptom_router import REASON_TARGET_NONE, ROUTE_HOLMESGPT, route_symptom

    r = route_symptom("그 서버 이상해", [])
    assert (r.route, r.reason) == (ROUTE_HOLMESGPT, REASON_TARGET_NONE)


def test_ac12_router_module_has_no_llm_or_io_imports() -> None:
    tree = ast.parse((REPO / "src/domain/symptom_router.py").read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    assert mods <= {"__future__", "re", "collections", "dataclasses", "typing"}


@pytest.mark.parametrize(
    "query",
    ["A1 로그인 응답 지연 원인 알려줘", "A1 login 응답 지연 원인 알려줘"],
)
def test_finding_kind_keyword_substring_misroutes_login_latency(query: str) -> None:
    """[교정 확인] 「로그인」·「login」의 `로그`/`log`는 log kind로 잡히지 않는다.

    대상·증상이 확정된 지연 질의가 플레이북(was.latency)으로 간다."""
    from src.domain.symptom_router import ROUTE_PLAYBOOK, route_symptom, symptom_candidates

    assert "kind:log" not in symptom_candidates(query.replace("A1", ""))
    r = route_symptom(query, [A1])
    assert (r.route, r.playbook_hint) == (ROUTE_PLAYBOOK, "was.latency")


@pytest.mark.parametrize("query", ["A1 로그인 실패 로그 확인", "A1 login error log 확인"])
def test_log_keyword_still_matches_beside_excluded_word(query: str) -> None:
    """제외어를 지워도 따로 쓴 「로그」/`log`는 그대로 log kind다(제외 범위를 좁게)."""
    from src.domain.symptom_router import symptom_candidates

    assert "kind:log" in symptom_candidates(query.replace("A1", ""))


# ── W9 KB 기록 경로 차단(수용 기준 10) ──────────────────────────────────────


def test_kb_store_rejects_sre_agent_paths_including_symlink(tmp_path: Path) -> None:
    from noise_gate.infrastructure.incident_kb_store import (
        ForbiddenKbPathError,
        IncidentKbStore,
    )

    target = REPO / "sre_agent" / "config" / "known_errors"
    with pytest.raises(ForbiddenKbPathError):
        IncidentKbStore(str(target / "x.jsonl"))
    link = tmp_path / "kb_link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ForbiddenKbPathError):
        IncidentKbStore(str(link / "x.jsonl"))
    # 상대 경로 우회
    rel = Path("logs") / ".." / "sre_agent" / "config" / "playbooks" / "y.jsonl"
    with pytest.raises(ForbiddenKbPathError):
        IncidentKbStore(str(REPO / rel))


def test_kb_store_write_lands_only_in_given_file(tmp_path: Path) -> None:
    from noise_gate.infrastructure.incident_kb_store import IncidentKbStore

    before = {p: p.stat().st_mtime_ns for p in (REPO / "sre_agent" / "config").rglob("*")}
    store = IncidentKbStore(str(tmp_path / "kb.jsonl"), max_lines=2)
    for i in range(5):
        assert store.record_engine_choice(
            investigation_id=f"i{i}", choice="playbook", playbook_id="was.latency", chosen_by="u"
        )
    after = {p: p.stat().st_mtime_ns for p in (REPO / "sre_agent" / "config").rglob("*")}
    assert before == after
    assert sorted(p.name for p in tmp_path.iterdir()) == ["kb.jsonl", "kb.jsonl.1"]


# ── 사용자 요청 조사 상한(수용 기준 13 · W10/W11/W12 공유) ──────────────────


async def _no_refresh(store, rec, ng):  # noqa: ANN001, ANN202
    return {**rec, "status": "done"}, None


def _store(tmp_path: Path):  # noqa: ANN202
    from noise_gate.infrastructure.investigation_store import InvestigationStore

    return InvestigationStore(str(tmp_path / "inv.jsonl"))


def test_ac13_three_request_kinds_share_one_hourly_counter(tmp_path: Path) -> None:
    from src.infrastructure.investigation_request_limit import user_request_limit

    store = _store(tmp_path)
    now = datetime.now().astimezone()
    store.record(investigation_id="d1", alarm_ids=[], kind="diagnosis", status="done",
                 source="investigation_chat", owner_sub="u1", ts=now)
    store.record(investigation_id="m1", alarm_ids=["A-1"], kind="manual", status="done",
                 source="manual", owner_sub="u1", parent_alarm_id="A-1", origin="manual", ts=now)
    ng = SimpleNamespace(investigation_additional_user_hourly_limit=3)
    assert asyncio.run(user_request_limit(store, ng, "u1", refresh=_no_refresh)) is None
    store.record(investigation_id="c1", alarm_ids=[], kind="manual", status="done",
                 source="chat", owner_sub="u1", origin="chat", ts=now)
    limited = asyncio.run(user_request_limit(store, ng, "u1", refresh=_no_refresh))
    assert limited is not None and limited[0] == "hourly_limit"
    # 다른 사용자는 별도
    assert asyncio.run(user_request_limit(store, ng, "u2", refresh=_no_refresh)) is None


def test_ac13_old_requests_do_not_count(tmp_path: Path) -> None:
    from src.infrastructure.investigation_request_limit import user_request_limit

    store = _store(tmp_path)
    old = datetime.now().astimezone() - timedelta(hours=2)
    for i in range(5):
        store.record(investigation_id=f"d{i}", alarm_ids=[], kind="diagnosis", status="done",
                     source="x", owner_sub="u1", ts=old)
    ng = SimpleNamespace(investigation_additional_user_hourly_limit=3)
    assert asyncio.run(user_request_limit(store, ng, "u1", refresh=_no_refresh)) is None


def test_finding_empty_sub_bypasses_user_limit(tmp_path: Path) -> None:
    """[교정 확인] sub가 빈 요청은 상한을 셀 수 없으므로 거부한다(fail-closed · `owner_unknown`).
    종전에는 `list_requests(owner_sub="")`가 아무것도 세지 않아 상한이 걸리지 않았다."""
    from src.infrastructure.investigation_request_limit import (
        REASON_OWNER_UNKNOWN,
        user_request_limit,
    )

    store = _store(tmp_path)
    now = datetime.now().astimezone()
    for i in range(10):
        store.record(investigation_id=f"c{i}", alarm_ids=[], kind="manual", status="running",
                     source="chat", owner_sub="" or None, origin="chat", ts=now)
    ng = SimpleNamespace(investigation_additional_user_hourly_limit=3)
    limited = asyncio.run(user_request_limit(store, ng, "", refresh=_no_refresh))
    assert limited is not None and limited[0] == REASON_OWNER_UNKNOWN


def test_slot_serializes_concurrent_requests_of_same_user(tmp_path: Path) -> None:
    """[교정 M1] 같은 사용자의 동시 요청 N개 중 1개만 상한 검사를 통과한다(검사 → 기록 한 구간)."""
    from src.infrastructure.investigation_request_limit import user_request_slot

    store = _store(tmp_path)
    ng = SimpleNamespace(investigation_additional_user_hourly_limit=3)

    async def _still_running(store, rec, ng):  # noqa: ANN001, ANN202
        return rec, None

    async def _one(i: int) -> bool:
        async with user_request_slot(store, ng, "u1", refresh=_still_running) as limited:
            if limited is not None:
                return False
            await asyncio.sleep(0.01)  # 접수 호출(네트워크) 자리
            store.record(investigation_id=f"r{i}", alarm_ids=[], kind="manual",
                         status="submitted", source="chat", owner_sub="u1", origin="chat")
            return True

    async def _run() -> list[bool]:
        return await asyncio.gather(*(_one(i) for i in range(5)))

    results = asyncio.run(_run())
    assert results.count(True) == 1
    assert len(store.list_requests(owner_sub="u1")) == 1


def test_slot_empty_sub_rejected_without_lock(tmp_path: Path) -> None:
    from src.infrastructure import investigation_request_limit as lim

    async def _run():  # noqa: ANN202
        async with lim.user_request_slot(_store(tmp_path), SimpleNamespace(), "",
                                         refresh=_no_refresh) as limited:
            return limited

    assert asyncio.run(_run())[0] == lim.REASON_OWNER_UNKNOWN
    assert "" not in lim._USER_LOCKS


def test_user_lock_dict_sweeps_expired_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """데몬 dict 원칙 — 마지막 사용 뒤 TTL이 지난 잠기지 않은 키는 다음 호출에서 지운다."""
    from src.infrastructure import investigation_request_limit as lim

    lim._USER_LOCKS.clear()
    lim._user_lock("old")
    assert "old" in lim._USER_LOCKS
    lock, last = lim._USER_LOCKS["old"]
    lim._USER_LOCKS["old"] = (lock, last - lim.USER_LOCK_TTL_SECONDS - 1)
    lim._user_lock("new")
    assert "old" not in lim._USER_LOCKS and "new" in lim._USER_LOCKS
    lim._USER_LOCKS.clear()


# ── 사용자 뷰 · 조사 ref 헤더 ────────────────────────────────────────────────


def test_finding_user_view_exposes_other_users_owner_sub() -> None:
    """[교정 확인] 사용자 뷰에는 요청자 `owner_sub`를 싣지 않는다(운영자 뷰만) — 식별자가 아닌
    `origin`·`parent_alarm_id`는 그대로 싣는다."""
    from noise_gate.infrastructure.investigation_store import investigation_view

    rec = {
        "investigation_id": "m1", "alarm_ids": ["A-1"], "kind": "manual", "status": "done",
        "owner_sub": "alice@corp", "origin": "manual", "parent_alarm_id": "A-1",
        "ts": "2026-10-19T10:00:00+00:00", "source": "manual",
    }
    view = investigation_view(rec, mask_fn=lambda s: "***", operator_view=False)
    assert "owner_sub" not in view
    assert view["origin"] == "manual" and view["parent_alarm_id"] == "A-1"
    op = investigation_view(rec, operator_view=True)
    assert op["owner_sub"] == "alice@corp"


@pytest.mark.parametrize(
    "value,ok",
    [("inv-1", True), ("a" * 128, True), ("a" * 129, False), ("x y", False),
     ("<script>", False), ("", False), ("../etc", False)],
)
def test_investigation_ref_header_format(value: str, ok: bool) -> None:
    from src.api.thread_history import investigation_ref

    req = SimpleNamespace(headers={"X-Investigation-Ref": value})
    assert (investigation_ref(req) == value) is ok


def test_investigation_ref_not_duplicated_and_absent_keeps_value() -> None:
    from src.api.thread_history import _with_investigation_ref

    base = [{"kind": "apm_job", "ref": {"job_id": "j"}}]
    assert _with_investigation_ref(base, None) is base
    once = _with_investigation_ref(base, "inv-1")
    assert _with_investigation_ref(once, "inv-1") == once
    assert len(once) == 2
