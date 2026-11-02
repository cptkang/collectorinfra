"""plans/205 W1 — 큰 응답 파싱·자격증명 검사 프로세스 풀 (D-356 ② · D-296 ③).

① 풀 경로 = 인라인 경로(값·메모·가린 칸 — 메모리 본문 · 스풀 파일 · 텍스트) ② 임계 미만은 풀 미사용
③ 파싱 오류 → 기존과 같은 `ApmError` 문구 ④ 깨진 풀 → WARNING + 결과 정상 + 다음 호출은 새 풀
⑤ `APM_PARSE_WORKERS=0` → 풀 미생성 ⑥ 큰 응답 4건 처리 동안 이벤트 루프 지연이 풀 경로에서 작다.
설정 3키 로딩·거부. 모든 풀은 테스트 안에서 닫는다.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

import httpx
import pytest
from apm_gateway.adapters import parse_pool as parse_pool_mod
from apm_gateway.adapters.jennifer.client import JenniferClient
from apm_gateway.adapters.parse_pool import ParsePool, parse_scrub_json, scrub_text_worker
from apm_gateway.application.sources import build_source_set
from apm_gateway.domain.credentials import ROOT_FIELD
from apm_gateway.domain.errors import ApmError

from apm_gateway.config import GatewayConfig, JenniferApiConfig, PerfConfig, load_config

C = "Canary#Secret-205"
KIB = 1024
MIB = 1024 * KIB


def _rows(n: int) -> list[dict[str, Any]]:
    """자격증명 칸(키째 제거 · 값 가림)과 일반 칸이 섞인 행."""
    return [
        {
            "instanceId": i,
            "name": f"인스턴스-{i:06d}",
            "hostName": f"was-{i:06d}.example.local",
            "description": "x" * 40,
            "ratio": i / 7,
            "user": {"id": f"u{i}", "password": C},
            "SYSTEM": {"PATH": "/bin", "DB_PASSWORD": C},
            "cmd": f"/x.sh --password {C} --verbose",
            "flags": [True, None, {"k": i}],
        }
        for i in range(n)
    ]


def _deep_row() -> dict[str, Any]:
    """검사 메모(`[한계] 자격증명 검사: 예상 밖 응답 모양`)가 나는 깊은 행."""
    deep: dict[str, Any] = {}
    cur = deep
    for _ in range(40):
        cur["n"] = {}
        cur = cur["n"]
    cur.update({"secret_token": C, "plain": "ok"})
    return deep


def _raw(n: int) -> bytes:
    return json.dumps({"result": [*_rows(n), _deep_row()]}, ensure_ascii=False).encode()


def _client(
    raw: bytes, spool, pool: ParsePool | None, *, cap: int = 4 * MIB, text: bool = False
) -> JenniferClient:
    def handler(request: httpx.Request) -> httpx.Response:
        kind = "text/plain" if text else "application/json"
        return httpx.Response(200, content=raw, headers={"content-type": kind})

    return JenniferClient(
        JenniferApiConfig(
            url="http://apm.test", token="t", rate_limit_per_sec=0, max_response_bytes=cap
        ),
        transport=httpx.MockTransport(handler),
        spool_dir=spool,
        parse_pool=pool,
    )


@pytest.fixture
def absorbed(monkeypatch) -> list[tuple[list[str], set[str]]]:
    """클라이언트가 호출 맥락에 넘긴 (메모, 가린 칸)을 모은다."""
    seen: list[tuple[list[str], set[str]]] = []
    monkeypatch.setattr(
        JenniferClient,
        "_absorb",
        staticmethod(lambda notes, masked: seen.append((list(notes), set(masked)))),
    )
    return seen


@pytest.fixture
def pool() -> Iterator[ParsePool]:
    made = ParsePool(2, 1024)
    yield made
    made.shutdown()


def _spy_pool(pool: ParsePool, monkeypatch) -> list[str]:
    """풀이 실제로 받은 호출 종류를 센다."""
    used: list[str] = []
    real_json, real_text = pool.json, pool.text

    async def json_spy(*args: Any) -> Any:
        used.append("json")
        return await real_json(*args)

    async def text_spy(*args: Any) -> Any:
        used.append("text")
        return await real_text(*args)

    monkeypatch.setattr(pool, "json", json_spy)
    monkeypatch.setattr(pool, "text", text_spy)
    return used


# ── ① 풀 경로 = 인라인 경로 ─────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("spooled", [False, True])
async def test_pool_json_equals_inline(tmp_path, pool, absorbed, monkeypatch, spooled):
    raw = _raw(3000)
    cap = 64 * KIB if spooled else 4 * MIB  # 스풀 표본은 메모리 임계를 낮춰 임시 파일로 받게
    used = _spy_pool(pool, monkeypatch)
    spool = tmp_path / "tmp"
    inline = _client(raw, spool, None, cap=cap)
    pooled = _client(raw, spool, pool, cap=cap)
    want = await inline.get_json("/api/instance", {"domain_id": 1000})
    got = await pooled.get_json("/api/instance", {"domain_id": 1000})
    await inline.aclose()
    await pooled.aclose()
    assert used == ["json"]
    assert got == want
    assert C not in json.dumps(got, ensure_ascii=False)
    assert absorbed[0] == absorbed[1]
    notes, masked = absorbed[1]
    assert len(notes) == 1 and notes[0].startswith("[한계] 자격증명 검사")
    assert {"password", "DB_PASSWORD"} <= masked
    assert not spool.exists() or list(spool.iterdir()) == []  # 임시 파일은 부모가 지운다


@pytest.mark.asyncio
@pytest.mark.parametrize("spooled", [False, True])
async def test_pool_text_equals_inline(tmp_path, pool, absorbed, monkeypatch, spooled):
    raw = "\n".join(f"line {i} --password {C}{i}" for i in range(20_000)).encode()
    cap = 64 * KIB if spooled else 4 * MIB
    used = _spy_pool(pool, monkeypatch)
    spool = tmp_path / "tmp"
    path = "/api/transaction/profile.txt"
    params = {"domain_id": 1, "txid": 2, "time": 3}
    inline = _client(raw, spool, None, cap=cap, text=True)
    pooled = _client(raw, spool, pool, cap=cap, text=True)
    want = await inline.get_text(path, params)
    got = await pooled.get_text(path, params)
    await inline.aclose()
    await pooled.aclose()
    assert used == ["text"]
    assert got == want and C not in got
    assert absorbed == [([], {ROOT_FIELD}), ([], {ROOT_FIELD})]
    assert not spool.exists() or list(spool.iterdir()) == []


def test_worker_functions_match_inline_functions(tmp_path):
    raw = _raw(50).decode()
    file = tmp_path / "body.json"
    file.write_text(raw, encoding="utf-8")
    from_text = parse_scrub_json(raw, None, "utf-8")
    from_file = parse_scrub_json(None, str(file), "utf-8")
    assert from_text == from_file
    assert parse_scrub_json("", None, "utf-8") == (None, [], set())  # 빈 본문 = None(현행 동일)
    assert C not in scrub_text_worker(f"exec --password {C}")


# ── ② 임계 미만은 풀 미사용 ─────────────────────────────────


@pytest.mark.asyncio
async def test_below_threshold_does_not_use_pool(tmp_path, absorbed, monkeypatch):
    big = ParsePool(2, 10 * MIB)
    used = _spy_pool(big, monkeypatch)
    raw = _raw(200)
    client = _client(raw, tmp_path / "tmp", big)
    body = await client.get_json("/api/instance", {"domain_id": 1000})
    text_client = _client(b"plain text", tmp_path / "tmp", big, text=True)
    text = await text_client.get_text(
        "/api/transaction/profile.txt", {"domain_id": 1, "txid": 2, "time": 3}
    )
    await client.aclose()
    await text_client.aclose()
    assert used == [] and big._executor is None  # 프로세스를 띄우지 않았다
    assert C not in json.dumps(body, ensure_ascii=False) and text == "plain text"
    big.shutdown()


# ── ③ 파싱 오류 → 기존과 같은 ApmError 문구 ─────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (b'{"result": [1, 2,' + b" " * 4096, "JSON 파싱 실패: /api/instance"),
        (b"[" * 200_000 + b"]" * 200_000, "JSON 파싱 실패(중첩 과다): /api/instance"),
    ],
)
@pytest.mark.parametrize("use_pool", [False, True])
async def test_parse_errors_keep_existing_reason(tmp_path, pool, raw, reason, use_pool):
    spool = tmp_path / "tmp"
    client = _client(raw, spool, pool if use_pool else None)
    with pytest.raises(ApmError) as exc:
        await client.get_json("/api/instance", {"domain_id": 1000})
    await client.aclose()
    assert exc.value.code == "apm_api_error" and exc.value.reason == reason
    if use_pool:
        assert pool._executor is not None  # 풀에서 났다(스레드 폴백이 아니다)


@pytest.mark.asyncio
async def test_worker_decode_error_does_not_carry_document(pool):
    """워커의 JSON 디코드 오류는 원문(`doc`)을 싣지 않은 ValueError로 온다(원문이 부모로 오지
    않게)."""
    bad = '{"password": "' + C + '", oops'
    with pytest.raises(ValueError) as exc:
        await pool.json(bad, None, "utf-8", len(bad))
    assert C not in str(exc.value) and not hasattr(exc.value, "doc")


# ── ④ 깨진 풀 → WARNING + 결과 정상 + 다음 호출은 새 풀 ──────


@pytest.mark.asyncio
async def test_broken_pool_warns_falls_back_and_recreates(tmp_path, pool, absorbed, caplog):
    raw = _raw(3000)
    inline = _client(raw, tmp_path / "tmp", None)
    want = await inline.get_json("/api/instance", {"domain_id": 1})
    await inline.aclose()
    client = _client(raw, tmp_path / "tmp", pool)
    assert await client.get_json("/api/instance", {"domain_id": 1}) == want  # 풀을 띄운다
    old = pool._executor
    assert old is not None
    for proc in list(old._processes.values()):  # 워커 강제 종료
        proc.kill()
        proc.join(5)
    caplog.set_level("WARNING", logger=parse_pool_mod.__name__)
    assert await client.get_json("/api/instance", {"domain_id": 1}) == want
    warned = [r for r in caplog.records if r.name == parse_pool_mod.__name__]
    assert len(warned) == 1 and warned[0].levelname == "WARNING"
    message = warned[0].getMessage()
    assert "BrokenProcessPool" in message and f"bytes={len(raw)}" in message
    assert C not in message
    assert pool._executor is None  # 깨진 풀은 버렸다
    caplog.clear()
    assert await client.get_json("/api/instance", {"domain_id": 1}) == want
    assert pool._executor is not None and pool._executor is not old  # 새 풀
    assert not [r for r in caplog.records if r.name == parse_pool_mod.__name__]
    await client.aclose()
    assert absorbed[-1] == absorbed[0]


@pytest.mark.asyncio
async def test_submit_failure_also_falls_back(pool, caplog):
    """제출 실패(종료된 풀)도 같은 폴백이다 — 침묵하지 않는다."""
    executor = pool._pool()
    executor.shutdown(wait=True)
    caplog.set_level("WARNING", logger=parse_pool_mod.__name__)
    out = await pool.json('{"a": 1}', None, "utf-8", 8)
    assert out == ({"a": 1}, [], set())
    assert "RuntimeError" in caplog.text and pool._executor is None


# ── ⑤ 설정 · 풀 생성 ────────────────────────────────────────


def _env(**extra: str) -> dict[str, str]:
    return {"JENNIFER_API_URL": "http://apm.test", "JENNIFER_API_TOKEN": "t", **extra}


def test_parse_workers_zero_builds_no_pool():
    cfg = load_config(_env(APM_PARSE_WORKERS="0"), dotenv=None)
    sources = build_source_set(cfg)
    assert cfg.perf.parse_workers == 0 and sources.parse_pool is None
    assert all(src.api.client._parse_pool is None for src in sources)


def test_one_pool_shared_by_all_source_clients_and_lazy():
    cfg = load_config(
        _env(
            JENNIFER_API_URL="",
            JENNIFER_API_TOKEN="",
            JENNIFER_SOURCES='["bank","common"]',
            JENNIFER_BANK_API_URL="http://bank.test",
            JENNIFER_BANK_API_TOKEN="b",
            JENNIFER_COMMON_API_URL="http://common.test",
            JENNIFER_COMMON_API_TOKEN="c",
            APM_PARSE_WORKERS="3",
            APM_PARSE_PROCESS_MIN_BYTES="1000",
        ),
        dotenv=None,
    )
    sources = build_source_set(cfg)
    shared = sources.parse_pool
    assert shared is not None and (shared.workers, shared.min_bytes) == (3, 1000)
    assert [src.api.client._parse_pool for src in sources] == [shared, shared]
    assert shared._executor is None  # 큰 응답 전에는 프로세스 0개
    shared.shutdown()


def test_perf_defaults(monkeypatch):
    monkeypatch.setattr("os.cpu_count", lambda: 10)
    cfg = load_config(_env(), dotenv=None)
    assert (cfg.perf.parse_workers, cfg.perf.parse_process_min_bytes) == (4, 262144)
    assert cfg.perf.fanout_concurrency == 2  # W3b 다중 사용자 측정으로 4→2(D-356 부기)
    monkeypatch.setattr("os.cpu_count", lambda: 2)
    assert load_config(_env(), dotenv=None).perf.parse_workers == 1
    monkeypatch.setattr("os.cpu_count", lambda: None)
    assert load_config(_env(), dotenv=None).perf.parse_workers == 1
    assert PerfConfig().parse_process_min_bytes == 262144
    assert GatewayConfig().perf.fanout_concurrency == 2


def test_perf_keys_load():
    cfg = load_config(
        _env(
            APM_PARSE_WORKERS=" 2 ",
            APM_PARSE_PROCESS_MIN_BYTES="1048576",
            APM_FANOUT_CONCURRENCY="1",
        ),
        dotenv=None,
    )
    assert (cfg.perf.parse_workers, cfg.perf.parse_process_min_bytes) == (2, 1048576)
    assert cfg.perf.fanout_concurrency == 1


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("APM_PARSE_WORKERS", "-1", "APM_PARSE_WORKERS는 0 이상이어야 한다"),
        ("APM_PARSE_WORKERS", "1.5", "APM_PARSE_WORKERS는 정수여야 한다"),
        ("APM_PARSE_WORKERS", "auto", "APM_PARSE_WORKERS는 정수여야 한다"),
        ("APM_PARSE_PROCESS_MIN_BYTES", "0", "APM_PARSE_PROCESS_MIN_BYTES는 1 이상이어야 한다"),
        ("APM_PARSE_PROCESS_MIN_BYTES", "x", "APM_PARSE_PROCESS_MIN_BYTES는 정수여야 한다"),
        ("APM_FANOUT_CONCURRENCY", "0", "APM_FANOUT_CONCURRENCY는 1 이상이어야 한다"),
        ("APM_FANOUT_CONCURRENCY", "-3", "APM_FANOUT_CONCURRENCY는 1 이상이어야 한다"),
    ],
)
def test_perf_keys_reject_bad_values(key, value, message):
    with pytest.raises(ValueError, match=message):
        load_config(_env(**{key: value}), dotenv=None)


# ── ⑥ 이벤트 루프 지연 ──────────────────────────────────────


async def _max_tick_lag(work: Callable[[], Awaitable[Any]]) -> float:
    """`work` 동안 10ms 틱 코루틴이 늦은 최대 시간(초)."""
    lags: list[float] = []
    done = asyncio.Event()

    async def ticker() -> None:
        while not done.is_set():
            started = time.perf_counter()
            await asyncio.sleep(0.01)
            lags.append(time.perf_counter() - started - 0.01)

    task = asyncio.create_task(ticker())
    await asyncio.sleep(0.05)
    await work()
    done.set()
    await task
    return max(lags)


@pytest.mark.asyncio
async def test_event_loop_lag_is_smaller_with_pool(tmp_path):
    raw = json.dumps({"result": _rows(20_000)}, ensure_ascii=False).encode()
    assert 5 * MIB < len(raw) < 8 * MIB  # 6MiB급 — 메모리 임계(4MiB)를 넘어 스풀 경로
    pool = ParsePool(4, 256 * KIB)
    try:
        # 워커 4개를 미리 띄운다(첫 사용 기동 지연은 측정 밖)
        await asyncio.gather(*(pool.json('{"a": 1}', None, "utf-8", 8) for _ in range(8)))

        async def run(parse_pool: ParsePool | None) -> list[Any]:
            client = _client(raw, tmp_path / "tmp", parse_pool)
            try:
                return await asyncio.gather(
                    *(client.get_json("/api/instance", {"domain_id": d}) for d in range(4))
                )
            finally:
                await client.aclose()

        threaded = await _max_tick_lag(lambda: run(None))
        pooled = await _max_tick_lag(lambda: run(pool))
    finally:
        pool.shutdown()
    print(f"max tick lag: thread {threaded * 1000:.1f}ms · pool {pooled * 1000:.1f}ms")
    assert pooled < threaded * 0.6
