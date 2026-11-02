"""plans/205 W1 — 큰 응답 파싱·자격증명 검사 프로세스 풀 (D-356 ② · D-296 ③).

① 풀 경로와 스레드(인라인) 경로의 결과(cleaned·notes·masked) 동일 — 메모리 본문 · 스풀 파일 본문
② 임계 미만이면 풀을 쓰지 않는다(풀 미생성) ③ 파싱 오류는 종전과 같은 `ApmError(API_ERROR)` 문구
④ 워커를 강제 종료해 풀이 깨지면 WARNING + 결과 정상 + 다음 호출은 새 풀 ⑤ 워커 0 = 풀 미생성
⑥ 이벤트 루프 지연: 큰 응답 4건 처리 동안 10ms 틱의 최대 지연이 풀 경로에서 유의하게 작다.

실 spawn 프로세스를 쓰는 시험은 `asyncio.wait_for`로 10초 안에 끝나게 묶는다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable
from typing import Any, TypeVar

import httpx
import pytest
from apm_gateway.adapters.jennifer.client import JenniferClient
from apm_gateway.adapters.parse_pool import ParsePool, build_parse_pool, parse_scrub_json
from apm_gateway.application.sources import build_source_set
from apm_gateway.domain.credentials import ROOT_FIELD
from apm_gateway.domain.errors import API_ERROR, ApmError

from apm_gateway.config import JenniferApiConfig, default_parse_workers, load_config

_T = TypeVar("_T")
LIMIT_S = 10.0
SECRET = "S3cr3t-Pw-9a7f"


async def _within(coro: Awaitable[_T]) -> _T:
    return await asyncio.wait_for(coro, LIMIT_S)


def _deep(depth: int) -> Any:
    node: Any = {"leaf": f"jdbc:mysql://app:{SECRET}@db/x"}
    for _ in range(depth):
        node = {"n": node}
    return node


def _rows(n: int) -> list[dict[str, Any]]:
    return [
        {
            "instanceId": i,
            "name": f"인스턴스-{i:06d}",
            "password": f"{SECRET}{i}",
            "props": [{"key": "DB_PASSWORD", "value": f"v{i}"}],
            "cmd": f"java -Ddb.password={SECRET} -jar app{i}.jar",
            "ratio": i / 7,
        }
        for i in range(n)
    ]


def _payload(n: int) -> bytes:
    # 자격증명(칸 이름·명령행·이름/값 묶음) + 깊이 32 넘는 중첩([한계] 메모) 포함 표본
    return json.dumps({"result": _rows(n), "meta": _deep(40)}, ensure_ascii=False).encode()


def _client(raw: bytes | str, spool, *, pool: ParsePool | None, cap: int = 4 * 1024 * 1024):
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(raw, str):
            return httpx.Response(200, text=raw, headers={"content-type": "text/plain"})
        return httpx.Response(200, content=raw, headers={"content-type": "application/json"})

    client = JenniferClient(
        JenniferApiConfig(
            url="http://apm.test", token="t", rate_limit_per_sec=0, max_response_bytes=cap
        ),
        transport=httpx.MockTransport(handler),
        spool_dir=spool,
        parse_pool=pool,
    )
    seen: list[tuple[list[str], set[str]]] = []
    client._absorb = lambda notes, masked: seen.append((list(notes), set(masked)))  # type: ignore[method-assign]
    return client, seen


async def _get_json(raw: bytes, spool, pool: ParsePool | None, cap: int = 4 * 1024 * 1024):
    client, seen = _client(raw, spool, pool=pool, cap=cap)
    try:
        body = await _within(client.get_json("/api/instance", {"domain_id": 1000}))
    finally:
        await client.aclose()
    return body, seen


@pytest.fixture(scope="module")
def pool():
    p = ParsePool(2, 1024)
    yield p
    p.shutdown()


# ── ① 결과 동일 ─────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("spooled", [False, True])
async def test_pool_and_inline_results_are_identical(tmp_path, pool, spooled):
    raw = _payload(3000)
    assert len(raw) > 256 * 1024
    cap = 64 * 1024 if spooled else 4 * 1024 * 1024  # 스풀: 메모리 임계를 넘겨 임시 파일로 받는다
    spool = tmp_path / "tmp"
    inline, inline_seen = await _get_json(raw, spool, None, cap)
    pooled, pooled_seen = await _get_json(raw, spool, pool, cap)
    assert pool._executor is not None  # 실제로 풀을 탔다
    assert pooled == inline
    assert pooled_seen == inline_seen
    notes, masked = pooled_seen[0]
    assert masked >= {"password", "value", "cmd"} and any("[한계]" in n for n in notes)
    assert SECRET not in json.dumps(pooled, ensure_ascii=False)
    assert not spool.exists() or list(spool.iterdir()) == []  # 임시 파일은 부모가 지운다


@pytest.mark.asyncio
async def test_pool_and_inline_text_results_are_identical(tmp_path, pool):
    text = "\n".join(f"line {i} --password hunter{i}" for i in range(20_000))
    outs = []
    for p in (None, pool):
        client, seen = _client(text, tmp_path / "tmp", pool=p)
        try:
            outs.append(
                (
                    await _within(
                        client.get_text(
                            "/api/transaction/profile.txt", {"domain_id": 1, "txid": 2, "time": 3}
                        )
                    ),
                    seen,
                )
            )
        finally:
            await client.aclose()
    assert outs[0] == outs[1]
    assert "hunter" not in outs[1][0] and outs[1][1] == [([], {ROOT_FIELD})]


# ── ② 임계 미만 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_below_threshold_does_not_use_pool(tmp_path):
    big_threshold = ParsePool(1, 10**9)
    raw = _payload(200)
    inline, inline_seen = await _get_json(raw, tmp_path / "tmp", None)
    out, seen = await _get_json(raw, tmp_path / "tmp", big_threshold)
    client, _ = _client("plain --password x", tmp_path / "tmp", pool=big_threshold)
    try:
        await _within(
            client.get_text("/api/transaction/profile.txt", {"domain_id": 1, "txid": 2, "time": 3})
        )
    finally:
        await client.aclose()
    assert (out, seen) == (inline, inline_seen)
    assert big_threshold._executor is None  # 풀은 한 번도 만들어지지 않았다


# ── ③ 파싱 오류 문구 ─────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw", "cap"),
    [
        (b'{"result": [1, 2' + b" " * 300_000, 4 * 1024 * 1024),  # 형식 오류(메모리)
        (b'{"result": [1 2]}' + b" " * 300_000, 64 * 1024),  # 형식 오류(스풀 파일)
        (b"[" * 300_000, 4 * 1024 * 1024),  # 중첩 과다
    ],
    ids=["malformed-memory", "malformed-spooled", "too-deep"],
)
async def test_parse_error_reason_is_unchanged(tmp_path, pool, raw, cap):
    reasons = []
    for p in (None, pool):
        with pytest.raises(ApmError) as exc:
            await _get_json(raw, tmp_path / "tmp", p, cap)
        assert exc.value.code == API_ERROR
        reasons.append(exc.value.reason)
    assert reasons[0] == reasons[1]
    assert reasons[0].startswith("JSON 파싱 실패")


def test_worker_error_does_not_carry_raw_body():
    """`JSONDecodeError`는 원문 전체를 pickle에 싣는다 — 워커는 원문 없는 예외로 바꿔 돌려준다."""
    with pytest.raises(ValueError) as exc:
        parse_scrub_json('{"password": "' + SECRET, None, "utf-8")
    assert SECRET not in repr(exc.value.__reduce__())
    assert exc.value.__suppress_context__


# ── ④ 깨진 풀 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_broken_pool_warns_and_falls_back_to_thread(tmp_path, caplog):
    broken = ParsePool(1, 1024)
    try:
        await _within(broken.text("warm-up"))  # 워커 1개를 띄운다
        executor = broken._executor
        assert executor is not None
        for proc in list(executor._processes.values()):  # 워커 강제 종료
            proc.kill()
            proc.join(timeout=LIMIT_S)
        raw = _payload(1500)
        inline, inline_seen = await _get_json(raw, tmp_path / "tmp", None)
        with caplog.at_level(logging.WARNING, logger="apm_gateway.adapters.parse_pool"):
            out, seen = await _get_json(raw, tmp_path / "tmp", broken)
        assert (out, seen) == (inline, inline_seen)
        warnings = [r for r in caplog.records if "apm 파싱 프로세스 풀 장애" in r.getMessage()]
        assert warnings and "BrokenProcessPool" in warnings[0].getMessage()
        assert broken._executor is not executor  # 깨진 풀은 버렸다
        # 다음 호출은 새 풀에서 정상
        again, _ = await _get_json(raw, tmp_path / "tmp", broken)
        assert again == inline and broken._executor is not None
        assert broken._executor is not executor
    finally:
        broken.shutdown()


# ── ⑤ 워커 0 = 끔 ─────────────────────────────────────────


def test_zero_workers_builds_no_pool():
    cfg = load_config(
        {"JENNIFER_API_URL": "http://apm.test", "APM_PARSE_WORKERS": "0"}, dotenv=None
    )
    assert cfg.perf.parse_workers == 0
    pool = build_parse_pool(cfg.perf.parse_workers, cfg.perf.parse_process_min_bytes)
    assert pool is None
    sources = build_source_set(cfg, parse_pool=pool)
    assert all(s.api.client._parse_pool is None for s in sources)


def test_parse_pool_config_defaults_and_validation():
    cfg = load_config({}, dotenv=None)
    assert cfg.perf.parse_workers == default_parse_workers() >= 1
    assert default_parse_workers() <= 4
    assert cfg.perf.parse_process_min_bytes == 262144
    built = build_parse_pool(cfg.perf.parse_workers, cfg.perf.parse_process_min_bytes)
    assert built is not None and built._executor is None  # 지연 생성 — 첫 큰 응답 전엔 프로세스 0
    assert load_config({"APM_PARSE_WORKERS": "3"}, dotenv=None).perf.parse_workers == 3
    for bad in ("-1", "1.5", "two"):
        with pytest.raises(ValueError, match="APM_PARSE_WORKERS"):
            load_config({"APM_PARSE_WORKERS": bad}, dotenv=None)
    for bad in ("0", "-5", "x"):
        with pytest.raises(ValueError, match="APM_PARSE_PROCESS_MIN_BYTES"):
            load_config({"APM_PARSE_PROCESS_MIN_BYTES": bad}, dotenv=None)


def test_sources_share_one_pool():
    cfg = load_config(
        {
            "JENNIFER_SOURCES": '["a", "b"]',
            "JENNIFER_A_API_URL": "http://a.test",
            "JENNIFER_A_API_TOKEN": "ta",
            "JENNIFER_B_API_URL": "http://b.test",
            "JENNIFER_B_API_TOKEN": "tb",
        },
        dotenv=None,
    )
    shared = ParsePool(1, 1024)
    sources = build_source_set(cfg, parse_pool=shared)
    assert [s.api.client._parse_pool for s in sources] == [shared, shared]


# ── ⑥ 이벤트 루프 지연 ─────────────────────────────────────


async def _max_lag(client: JenniferClient, calls: int) -> tuple[float, float]:
    lag = 0.0
    stop = False

    async def tick() -> None:
        nonlocal lag
        while not stop:
            started = time.perf_counter()
            await asyncio.sleep(0.01)
            lag = max(lag, time.perf_counter() - started - 0.01)

    ticker = asyncio.create_task(tick())
    await asyncio.sleep(0.05)
    started = time.perf_counter()
    try:
        await _within(
            asyncio.gather(
                *(client.get_json("/api/instance", {"domain_id": 1000}) for _ in range(calls))
            )
        )
    finally:
        stop = True
        await ticker
    return lag, time.perf_counter() - started


@pytest.mark.asyncio
async def test_pool_keeps_event_loop_responsive(tmp_path):
    raw = json.dumps({"result": _rows(24_000)}).encode()
    assert len(raw) > 4 * 1024 * 1024  # 메모리 임계(4MiB)를 넘어 스풀 파일 경로
    spool = tmp_path / "tmp"
    inline_client, _ = _client(raw, spool, pool=None)
    pool = ParsePool(4, 256 * 1024)
    pool_client, _ = _client(raw, spool, pool=pool)
    try:
        lag_inline = await _max_lag(inline_client, 4)
        # 워커를 미리 띄운다(spawn 기동 비용은 비교에서 뺀다)
        await _within(asyncio.gather(*(pool.text("warm-up") for _ in range(4))))
        lag_pool = await _max_lag(pool_client, 4)
    finally:
        await inline_client.aclose()
        await pool_client.aclose()
        pool.shutdown()
    print(
        f"loop lag inline={lag_inline[0] * 1000:.0f}ms ({lag_inline[1]:.2f}s) · "
        f"pool={lag_pool[0] * 1000:.0f}ms ({lag_pool[1]:.2f}s)"
    )
    # 느슨한 단언 — 맥북 실측 inline 200~240ms · pool 27~35ms
    assert lag_pool[0] < lag_inline[0] / 2
