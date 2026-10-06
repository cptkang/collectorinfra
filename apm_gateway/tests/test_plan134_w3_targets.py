"""plans/134 W3 — 다건 대상 `targets`(계약 A-2 · D-296 ④ · D-299 ②④).

- 하위 봉투 = 단건 호출 봉투(오라클 — 같은 서버에서 단건으로 부른 결과와 칸·값·바이트 대조).
- 2·12개(종전 대상 상한 10 초과) · hostname/instance_name 혼합 · 소스 2개 · 일부 실패(partial ·
  대상별 사유) · 전부 실패(오류 봉투 + batch) · 형식 오류는 HTTP 0회 · 작업 승격 시 예상이 배치
  전체 · 이중 계산 없음 · 큰 결과 스풀(`total_row_count`·청크) · 감사 1줄(`targets(N)`).

외부 네트워크 0 — `httpx.MockTransport`만 쓴다. 결과 파일은 테스트 임시 디렉터리에만.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest
from apm_gateway.application.jobs import JobManager
from apm_gateway.application.sources import build_source_set
from apm_gateway.application.spool import Spool
from apm_gateway.application.tools import ApmTools
from apm_gateway.domain.errors import API_ERROR, INSTANCE_UNRESOLVED, INVALID_ARGUMENT
from apm_gateway.interface.server import audit_job_finished, create_server
from conftest import DOMAIN, NOW_MS, NOW_S, TOKEN, synthetic_fixtures, synthetic_handler
from test_plan134_w7_tools import w7_handler

from apm_gateway.config import JobConfig, load_config

# 단건 봉투에서 배치 항목으로 옮기지 않는 칸(계약 A-2) + 작업 마감이 단건에만 붙이는 칸
_HEAD = {"rows", "row_count", "queried_at", "source", "source_kind", "tool", "total_row_count"}
_ITEM_HEAD = {"index", "target", "status", "row_count"}


def _json(result) -> dict:
    blocks = result[0] if isinstance(result, tuple) else result
    return json.loads(blocks[0].text)


def _counting(handler, seen: list[str]):
    async def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.url.host}{request.url.path}")
        return await handler(request)

    return wrapped


def _env(tmp_path, **extra: str) -> dict[str, str]:
    return {
        "JENNIFER_API_URL": "http://apm.test",
        "JENNIFER_API_TOKEN": TOKEN,
        "JENNIFER_RATE_LIMIT_PER_SEC": "0",
        "APM_SPOOL_DIR": str(tmp_path / "spool"),
        **extra,
    }


def _server(tmp_path, handler, env=None, *, rate: float = 0.0, **job_cfg):
    cfg = load_config(env or _env(tmp_path))
    tools = ApmTools(
        build_source_set(cfg, transport=httpx.MockTransport(handler)), cfg, clock=lambda: NOW_S
    )
    jcfg = JobConfig(spool_dir=tmp_path / "spool", **job_cfg)
    jobs = JobManager(
        Spool(jcfg.spool_dir),
        jcfg,
        envelope=tools.ok,
        error_envelope=tools.err,
        rate_per_sec=rate,
        on_finish=audit_job_finished,
    )
    return create_server(tools, jobs=jobs), tools, jobs


def _host_fixtures(prefix: str, count: int, *, response_base: float = 100.0) -> list[dict]:
    """호스트 `count`대(`{prefix}00`… · 인스턴스 1개씩 · id 2000+i)의 합성 응답 — 나머지 경로는
    표준 합성 픽스처 그대로."""
    out = []
    for fx in synthetic_fixtures():
        fx = json.loads(json.dumps(fx))
        tpl = fx["request"]["template"]
        body = fx["response"].get("body_json")
        if tpl == "/api/instance":
            body["result"] = [
                {
                    "instanceId": 2000 + i,
                    "name": f"{prefix}{i:02d}_was",
                    "hostName": f"{prefix}{i:02d}",
                    "ipAddress": f"10.1.0.{i}",
                    "platform": "JAVA",
                    "status": "RUNNING",
                    "version": "5.6.5",
                }
                for i in range(count)
            ]
        elif tpl == "/api/realtime/instance":
            body["result"] = [
                {
                    "domainId": DOMAIN,
                    "instanceId": 2000 + i,
                    "instanceName": f"{prefix}{i:02d}_was",
                    "responseTime": response_base + i,
                    "tps": 10.0 + i,
                    "activeService": i,
                    "activeDBConnection": 1.0,
                    "averageDbPoolConfiguredCount": 10,
                }
                for i in range(count)
            ]
        elif tpl == "/api/transaction/time":
            for k, tx in enumerate(body["result"]):
                tx["instanceId"] = 2000 + k % count
        out.append(fx)
    return out


# ── 1. 오라클 — 하위 봉투 = 단건 호출 봉투 ───────────────────────

ORACLE_CASES = [
    ("apm_app_health", {"lookback_minutes": 15}),
    ("apm_runtime_health", {"metrics": ["heap_used_mb"]}),
    ("apm_resource_pool", {}),
    ("apm_slow_transactions", {"lookback_minutes": 3}),
    ("apm_active_services", {}),
    ("apm_events", {"lookback_minutes": 30}),
    ("apm_status_stats", {"kind": "sql"}),
    ("apm_metrics", {"mode": "series", "metrics": ["heap_used_mb"]}),
    ("apm_source_changes", {}),
    ("apm_change_impact", {"width_minutes": 5}),
    ("apm_period_compare", {
        "current_start": "2026-09-29T08:00:00",
        "current_end": "2026-09-29T10:00:00",
        "baseline_start": "2026-09-22T08:00:00",
        "baseline_end": "2026-09-22T10:00:00",
    }),
    ("apm_environment", {}),
    ("apm_config", {"kind": "event_rules"}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "args"), ORACLE_CASES)
async def test_sub_envelopes_equal_single_call_envelopes(tmp_path, tool, args):
    mcp, _, jobs = _server(tmp_path, w7_handler())
    targets = [{"hostname": "was-host01"}, {"instance_name": "api02_main"}]
    try:
        singles = [_json(await mcp.call_tool(tool, {**t, **args})) for t in targets]
        batch = _json(await mcp.call_tool(tool, {"targets": targets, **args}))
    finally:
        await jobs.aclose()
    assert all("error" not in s for s in singles), singles
    assert "error" not in batch and [b["status"] for b in batch["batch"]] == ["ok", "ok"]
    for i, single in enumerate(singles):
        item = batch["batch"][i]
        assert item["index"] == i and item["target"] == targets[i]
        view = {k: v for k, v in item.items() if k not in _ITEM_HEAD}
        expected = {k: v for k, v in single.items() if k not in _HEAD}
        assert json.dumps(view, ensure_ascii=False) == json.dumps(expected, ensure_ascii=False)
        rows = [
            {k: v for k, v in r.items() if k != "target_index"}
            for r in batch["rows"]
            if r["target_index"] == i
        ]
        assert json.dumps(rows, ensure_ascii=False) == json.dumps(
            single["rows"], ensure_ascii=False
        )
        assert item["row_count"] == single["row_count"] == len(rows)
    assert batch["row_count"] == sum(s["row_count"] for s in singles)
    assert batch["total_row_count"] == batch["row_count"]
    # 배치 고지 = 하위 고지 합집합(종류 기준 · 가린 칸 합치기는 아래 시험)
    kinds = {d["kind"] for s in singles for d in s.get("disclosures") or []}
    assert kinds == {d["kind"] for d in batch.get("disclosures") or []}


@pytest.mark.asyncio
async def test_failed_sub_calls_carry_the_single_call_error(tmp_path):
    """하위 호출 실패 = 단건 호출과 같은 오류 코드·사유(이 핸들러에는 GUID 조회 응답이 없다)."""
    mcp, _, jobs = _server(tmp_path, w7_handler())
    targets = [{"hostname": "was-host01"}, {"instance_name": "api02_main"}]
    args = {"guid": "guid-0001"}
    try:
        singles = [
            _json(await mcp.call_tool("apm_transaction_trace", {**t, **args})) for t in targets
        ]
        batch = _json(await mcp.call_tool("apm_transaction_trace", {"targets": targets, **args}))
    finally:
        await jobs.aclose()
    assert all(s["error"] == API_ERROR for s in singles)
    assert batch["error"] == API_ERROR and "rows" not in batch
    for single, item in zip(singles, batch["batch"], strict=True):
        assert (item["status"], item["error"], item["reason"], item["row_count"]) == (
            "error",
            single["error"],
            single["reason"],
            0,
        )


@pytest.mark.asyncio
async def test_masked_fields_union_is_one_batch_disclosure(tmp_path):
    """가린 칸 고지: 하위 봉투마다 자기 칸, 배치 봉투는 합집합 한 줄."""
    mcp, _, jobs = _server(tmp_path, synthetic_handler())
    try:
        out = _json(
            await mcp.call_tool(
                "apm_slow_transactions",
                {
                    "targets": [{"hostname": "was-host01"}, {"instance_name": "was01_a"}],
                    "lookback_minutes": 3,
                },
            )
        )
    finally:
        await jobs.aclose()
    masked = [d for d in out["disclosures"] if d["kind"] == "apm_masked_fields"]
    assert len(masked) == 1 and "client_id" in masked[0]["text"] and "user_id" in masked[0]["text"]
    for item in out["batch"]:
        assert [d["kind"] for d in item["disclosures"]] == ["apm_masked_fields"]


# ── 2. 2·12개 · 혼합 · 소스 2개 ─────────────────────────────────


def _two_source_handler(seen: list[str] | None = None):
    bank = synthetic_handler(_host_fixtures("bank", 6, response_base=100.0))
    common = synthetic_handler(_host_fixtures("comm", 6, response_base=500.0))

    async def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(f"{request.url.host}{request.url.path}")
        return await (bank if request.url.host == "bank.test" else common)(request)

    return handler


def _two_env(tmp_path) -> dict[str, str]:
    return {
        "JENNIFER_SOURCES": '["bank", "common"]',
        "JENNIFER_BANK_API_URL": "http://bank.test",
        "JENNIFER_BANK_API_TOKEN": TOKEN,
        "JENNIFER_COMMON_API_URL": "http://common.test",
        "JENNIFER_COMMON_API_TOKEN": "tok-OTHER-77c1",
        "JENNIFER_RATE_LIMIT_PER_SEC": "0",
        "APM_SPOOL_DIR": str(tmp_path / "spool"),
    }


@pytest.mark.asyncio
async def test_twelve_targets_two_sources_mixed_one_job_one_audit(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="apm_gateway.audit")
    seen: list[str] = []
    mcp, _, jobs = _server(tmp_path, _two_source_handler(seen), _two_env(tmp_path))
    targets = [{"hostname": f"bank{i:02d}"} for i in range(6)] + [
        {"instance_name": f"comm{i:02d}_was", "source_id": "common"} for i in range(5)
    ]
    targets.append({"hostname": "comm05", "instance_name": "comm05_was"})  # AND
    try:
        out = _json(await mcp.call_tool("apm_app_health", {"targets": targets}))
    finally:
        await jobs.aclose()
    assert len(targets) == 12 and "error" not in out and "partial" not in out
    assert [b["status"] for b in out["batch"]] == ["ok"] * 12
    by_target = {}
    for row in out["rows"]:
        by_target.setdefault(row["target_index"], []).append(row)
    assert sorted(by_target) == list(range(12))  # 상한 10을 넘는 대상이 전부 조회됐다
    for i in range(6):
        (row,) = by_target[i]
        assert (row["source_id"], row["instance_id"]) == ("bank", 2000 + i)
        assert row["response_time_avg_ms"] == 100.0 + i
    for i in range(6, 12):
        (row,) = by_target[i]
        assert (row["source_id"], row["instance_id"]) == ("common", 2000 + i - 6)
        assert row["response_time_avg_ms"] == 500.0 + i - 6
    for i in range(6, 11):  # 이름으로 찾은 행은 역정합 hostname(hostname AND 이름은 종전 모양)
        assert by_target[i][0]["hostname"] == f"comm{i - 6:02d}"
    # 항목 source_id가 있으면 그 소스만 묻는다 — 이름 대상 5개는 bank 실시간을 부르지 않는다
    realtime = [s for s in seen if s.endswith("/api/realtime/instance")]
    assert realtime.count("bank.test/api/realtime/instance") == 6
    assert realtime.count("common.test/api/realtime/instance") == 6
    lines = [r.getMessage() for r in caplog.records if r.name == "apm_gateway.audit"]
    assert len(lines) == 1 and "target=targets(12)" in lines[0]
    assert "bank" not in lines[0].split("target=")[1].split(" ")[0]  # 대상 원문을 싣지 않는다
    assert "sources=bank:" in lines[0] and ",common:" in lines[0]


@pytest.mark.asyncio
async def test_single_item_targets_and_top_level_source_ids_apply_to_items(tmp_path):
    seen: list[str] = []
    mcp, _, jobs = _server(tmp_path, _two_source_handler(seen), _two_env(tmp_path))
    try:
        out = _json(
            await mcp.call_tool(
                "apm_resource_pool",
                {"targets": [{"hostname": "bank01"}], "source_ids": ["bank"]},
            )
        )
    finally:
        await jobs.aclose()
    assert [b["status"] for b in out["batch"]] == ["ok"] and out["row_count"] == 1
    assert not any(s.startswith("common.test") for s in seen)  # 최상위 source_ids가 항목에 적용


# ── 3. 일부 실패 · 전부 실패 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_partial_failure_marks_batch_partial_with_per_target_reasons(tmp_path):
    mcp, _, jobs = _server(tmp_path, synthetic_handler())
    targets = [
        {"hostname": "was-host01"},
        {"hostname": "nohost-a"},
        {"instance_name": "api02_main"},
        {"instance_name": "no_such_was"},
    ]
    try:
        out = _json(await mcp.call_tool("apm_resource_pool", {"targets": targets}))
    finally:
        await jobs.aclose()
    assert "error" not in out and out["partial"] is True
    assert [b["status"] for b in out["batch"]] == ["ok", "error", "ok", "error"]
    assert out["batch"][1]["error"] == INSTANCE_UNRESOLVED
    assert "nohost-a" in out["batch"][1]["reason"]
    assert out["batch"][3]["row_count"] == 0 and "no_such_was" in out["batch"][3]["reason"]
    assert (
        "[한계] 대상 2/4 조회 실패: nohost-a(instance_unresolved), no_such_was(instance_unresolved)"
        in out["limits"]
    )
    assert {r["target_index"] for r in out["rows"]} == {0, 2}


@pytest.mark.asyncio
async def test_all_failed_is_error_envelope_with_batch(tmp_path):
    mcp, _, jobs = _server(tmp_path, synthetic_handler())
    try:
        same = _json(
            await mcp.call_tool(
                "apm_app_health",
                {"targets": [{"hostname": f"nohost-{i}"} for i in range(5)]},
            )
        )
        mixed = _json(
            await mcp.call_tool(
                "apm_app_health",
                {
                    "targets": [{"hostname": "nohost-x"}, {"hostname": "was-host01"}],
                    "reference_time": "not-a-time",
                },
            )
        )
    finally:
        await jobs.aclose()
    assert same["error"] == INSTANCE_UNRESOLVED and same["tool"] == "apm_app_health"
    assert same["reason"].startswith("대상 5개 모두 실패 — nohost-0(instance_unresolved)")
    assert "(앞 3건 · 외 2건)" in same["reason"] and "rows" not in same
    assert [b["status"] for b in same["batch"]] == ["error"] * 5
    # 원인 코드가 섞이면 api_error(첫 항목은 정합 실패, 나머지는 인자 오류)
    assert {b["error"] for b in mixed["batch"]} == {INSTANCE_UNRESOLVED, INVALID_ARGUMENT}
    assert mixed["error"] == API_ERROR and mixed["reason"].startswith("대상 2개 모두 실패")


# ── 4. 형식 오류 = invalid_argument · HTTP 0회 ─────────────────────

BAD = [
    ("apm_app_health", {"targets": []}),
    ("apm_app_health", {"targets": [{"hostname": "was-host01", "bogus": 1}]}),
    ("apm_app_health", {"targets": [{"source_id": "default"}]}),
    ("apm_app_health", {"targets": [{"hostname": 3}]}),
    ("apm_app_health", {"targets": [{"hostname": "  "}]}),
    ("apm_app_health", {"targets": [{"instance_name": ""}]}),
    ("apm_app_health", {"targets": [{"instance_name": "x" * 201}]}),
    ("apm_app_health", {"targets": [{"hostname": "was-host01", "instance_id": -1}]}),
    ("apm_app_health", {"targets": [{"hostname": "was-host01", "instance_id": True}]}),
    ("apm_app_health", {"targets": [{"hostname": "was-host01", "source_id": "nope"}]}),
    ("apm_app_health", {"targets": [{"hostname": "was-host01"}], "hostname": "was-host01"}),
    ("apm_app_health", {"targets": [{"hostname": "was-host01"}], "instance_name": "was01_a"}),
    ("apm_app_health", {"targets": [{"hostname": "was-host01"}], "instance_id": 1001}),
    ("apm_app_health", {"targets": [{"hostname": "was-host01"}], "source_ids": ["nope"]}),
    # 그 도구가 받지 않는 instance_id · instance_name을 받지 않는 도구
    ("apm_events", {"targets": [{"hostname": "was-host01", "instance_id": 1001}]}),
    ("apm_instance_map", {"targets": [{"instance_name": "was01_a"}]}),
    ("apm_environment", {"targets": [{"hostname": "was-host01", "instance_id": 1001}]}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "args"), BAD)
async def test_malformed_targets_are_invalid_argument_without_http(tmp_path, tool, args):
    seen: list[str] = []
    mcp, _, jobs = _server(tmp_path, _counting(synthetic_handler(), seen))
    try:
        out = _json(await mcp.call_tool(tool, args))
    finally:
        await jobs.aclose()
    assert out["error"] == INVALID_ARGUMENT, out
    assert "batch" not in out and seen == []


@pytest.mark.asyncio
async def test_core_rejects_non_list_targets_without_http(tmp_path):
    from apm_gateway.application.batch import TARGET_KEYS, run_batch

    seen: list[str] = []
    _, tools, jobs = _server(tmp_path, _counting(synthetic_handler(), seen))
    await jobs.aclose()
    with pytest.raises(Exception) as exc:
        await run_batch(
            tools,
            "apm_app_health",
            {"hostname": "was-host01"},
            lambda t: tools.apm_app_health(t.hostname),
            keys=TARGET_KEYS,
        )
    assert getattr(exc.value, "code", None) == INVALID_ARGUMENT and seen == []


# ── 5. 작업 승격 — 예상·진행이 배치 전체 · 이중 계산 없음 ─────────────


@pytest.mark.asyncio
async def test_promoted_batch_estimate_covers_all_targets(tmp_path):
    gate = asyncio.Event()
    handler = synthetic_handler(
        _host_fixtures("host", 12), gate=gate, gate_paths=("/api/transaction/time",)
    )
    mcp, tools, jobs = _server(tmp_path, handler, rate=5.0)
    for src in tools.sources:  # 명단 선적재(공유 적재 호출은 호출 계획 밖)
        await src.resolver.inventory()
    targets = [{"hostname": f"host{i:02d}"} for i in range(12)]
    try:
        handle = _json(
            await mcp.call_tool(
                "apm_slow_transactions",
                {"targets": targets, "lookback_minutes": 3, "wait_seconds": 0.05, "owner": "o"},
            )
        )
        job = handle["job"]
        # 첫 하위 호출 계획(1분 조각 3개) × 대상 12 = 36 — 단위는 API 호출 · 속도 5회/초
        assert job["estimate"] == {"api_calls": 36, "seconds": 7.2}
        assert job["progress"]["total"] == 36 and job["progress"]["unit"] == "api_calls"
        gate.set()
        for _ in range(500):
            status = _json(
                await mcp.call_tool("apm_job_status", {"job_id": job["job_id"], "owner": "o"})
            )
            if status["job"]["state"] not in ("queued", "running"):
                break
            await asyncio.sleep(0.01)
    finally:
        gate.set()
        await jobs.aclose()
    assert status["job"]["state"] == "completed"
    # 끝난 뒤 예상 = 실제(이중 계산 없음)
    assert status["job"]["progress"] == {
        "done": 36,
        "total": 36,
        "unit": "api_calls",
        "label": "API 호출",
    }
    assert status["job"]["estimate"]["api_calls"] == 36
    assert len(status["result_meta"]["batch"]) == 12


@pytest.mark.asyncio
async def test_plan_corrects_estimate_as_items_report():
    """첫 항목 계획 × N으로 시작해 실제 신고로 바꾼다 — 바깥 맥락에는 차이만 신고한다."""
    from apm_gateway.application.batch import _ItemScope, _Plan
    from apm_gateway.domain.call_context import CallScope

    class Outer(CallScope):
        def __init__(self) -> None:
            super().__init__()
            self.expected = 0
            self.reports: list[int] = []

        def expect_calls(self, count: int) -> None:
            self.expected += count
            self.reports.append(count)

    outer = Outer()
    plan = _Plan(outer, 4)
    scope = _ItemScope(plan)
    plan.begin()
    scope.expect_calls(3)
    assert outer.expected == 12  # 3 × 4
    scope.expect_calls(1)
    assert outer.expected == 16  # 첫 항목 계획이 4로 늘었다 → 4 × 4
    plan.end()
    for count in (2, 0):  # 둘째 2(추정 4보다 적다) · 셋째 0(정합 실패 등)
        plan.begin()
        if count:
            _ItemScope(plan).expect_calls(count)
        plan.end()
    assert outer.expected == 4 + 2 + 0 + 4
    plan.begin()
    _ItemScope(plan).expect_calls(5)
    plan.end()
    assert outer.expected == 4 + 2 + 0 + 5  # 끝 = 신고 합계
    assert sum(outer.reports) == outer.expected


@pytest.mark.asyncio
async def test_item_scope_keeps_job_priority_and_counts_calls(tmp_path):
    """하위 호출 맥락은 작업의 우선순위(승격 뒤 background)를 그대로 읽고 호출 수를 작업에 센다."""
    from apm_gateway.application.batch import _ItemScope, _Plan
    from apm_gateway.domain.call_context import PRIORITY_BACKGROUND, CallScope

    outer = CallScope()
    scope = _ItemScope(_Plan(outer, 1))
    assert scope.priority == outer.priority
    outer.priority = PRIORITY_BACKGROUND
    assert scope.priority == PRIORITY_BACKGROUND
    scope.note("[한계] x")
    scope.note("[한계] x")
    scope.masked(["user_id"])
    assert scope.notes == ["[한계] x"] and scope.masked_fields == {"user_id"}


# ── 6. 큰 결과 스풀 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_large_batch_result_spools_with_total_row_count(tmp_path):
    mcp, _, jobs = _server(tmp_path, synthetic_handler(), inline_rows=4, chunk_rows=5)
    try:
        out = _json(
            await mcp.call_tool(
                "apm_slow_transactions",
                {
                    "targets": [{"hostname": "was-host01"}, {"instance_name": "was01_a"}],
                    "lookback_minutes": 3,
                    "full": True,
                    "owner": "o",
                },
            )
        )
        assert out["total_row_count"] == 12 and out["row_count"] == 4
        assert [b["row_count"] for b in out["batch"]] == [6, 6]  # 하위 row_count는 실제 건수
        manifest = out["artifact"]
        assert [c["rows"] for c in manifest["chunks"]] == [5, 5, 2]
        read = []
        for index in range(len(manifest["chunks"])):
            part = _json(
                await mcp.call_tool(
                    "apm_job_read", {"job_id": out["job"]["job_id"], "owner": "o", "chunk": index}
                )
            )
            read += part["rows"]
    finally:
        await jobs.aclose()
    assert [r["target_index"] for r in read] == [0] * 6 + [1] * 6
    assert all("instance_oid" in r for r in read)  # 결과 파일에는 전체 파일 전용 칸이 남는다
    assert all("instance_oid" not in r for r in out["rows"])
    assert (tmp_path / "spool").is_dir()


@pytest.mark.asyncio
async def test_profile_text_parts_are_renamed_per_target(tmp_path):
    """결과 파일 텍스트 부분(프로파일 전문)은 항목마다 이름이 다르다."""
    long_text = "\n".join(f"STEP {i} select * from t where id = {i}" for i in range(80))
    fixtures = synthetic_fixtures()
    for fx in fixtures:
        if fx["request"]["template"] == "/api/transaction/profile.txt":
            fx["response"]["body_text"] = long_text
    mcp, _, jobs = _server(tmp_path, synthetic_handler(fixtures))
    ref = {"domain_id": DOMAIN, "txid": "9000", "time_ms": NOW_MS - 30_000, "owner": "o"}
    try:
        out = _json(
            await mcp.call_tool(
                "apm_transaction_profile",
                {"targets": [{"hostname": "was-host01"}, {"instance_name": "was01_a"}], **ref},
            )
        )
        names = [p["name"] for p in out["artifact"]["text_parts"]]
        text = _json(
            await mcp.call_tool(
                "apm_job_read", {"job_id": out["job"]["job_id"], "owner": "o", "part": names[1]}
            )
        )
    finally:
        await jobs.aclose()
    assert names == ["profile_t0", "profile_t1"]
    assert [b["text_parts"] for b in out["batch"]] == [["profile_t0"], ["profile_t1"]]
    assert text["text"].count("\n") == 79 and "id = 79" not in text["text"]  # 마스킹한 전문


@pytest.mark.asyncio
async def test_targets_argument_on_every_hostname_tool(tmp_path):
    mcp, _, jobs = _server(tmp_path, synthetic_handler())
    await jobs.aclose()
    schemas = {t.name: t.inputSchema for t in await mcp.list_tools()}
    with_host = sorted(n for n, s in schemas.items() if "hostname" in s["properties"])
    with_targets = sorted(n for n, s in schemas.items() if "targets" in s["properties"])
    assert with_host == with_targets and len(with_host) == 17
    for name in with_targets:
        prop = schemas[name]["properties"]["targets"]
        assert [p.get("type") for p in prop["anyOf"]] == ["array", "null"], name
        assert "targets" not in schemas[name].get("required", [])
