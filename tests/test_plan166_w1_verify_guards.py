"""plans/166 W1 독립 검증(verifier) — 직접 접근 치환 뒤 남긴 None 보존 분기 · 인가 술어 배선 ·
SSE 직렬화 바이트 · 침묵 폴백 로그의 반환값 불변을 고정한다.

동작 불변 리팩터(D-337)라 기대값은 「치환 전 사본의 식」이다. 설정은 SimpleNamespace로
검증 대상 필드만 명시한다(.env 누수 차단).
"""

from __future__ import annotations

import json
import logging
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.api.sse_events import SSE_DONE, SSE_ERROR, SSE_HEARTBEAT

# ── X1 None 보존 분기 (multi_db_executor · query_validator) ─────────────────


def _old_zone_group_exclusive(app_config: Any) -> bool:
    multi = getattr(app_config, "multi_db", None)
    value = getattr(multi, "zone_group_exclusive", True)
    return value if isinstance(value, bool) else True


@pytest.mark.parametrize(
    "cfg",
    [
        None,
        MagicMock(),
        SimpleNamespace(multi_db=SimpleNamespace(zone_group_exclusive=False)),
        SimpleNamespace(multi_db=SimpleNamespace(zone_group_exclusive=True)),
        SimpleNamespace(multi_db=SimpleNamespace(zone_group_exclusive="false")),
    ],
)
def test_zone_group_exclusive_matches_old_copy(cfg: Any) -> None:
    from src.nodes import multi_db_executor as mdb

    assert mdb._zone_group_exclusive(cfg) is _old_zone_group_exclusive(cfg)


def test_group_preview_none_config_carries_no_rows() -> None:
    from src.nodes import multi_db_executor as mdb

    assert mdb._group_preview([{"hostname": "h1"}], None) is None


def test_gate_schema_tables_none_config_returns_input() -> None:
    from src.nodes import multi_db_executor as mdb

    schema = {"tables": {"t1": {}}}
    out = mdb._gate_schema_tables(schema, None, None, {}, "", db_id="x", app_config=None)
    assert out is schema


@pytest.mark.parametrize(
    ("cfg", "expected"),
    [
        (None, 0.0),
        (MagicMock(), 0.0),
        (SimpleNamespace(server=SimpleNamespace(answer_reserve_sec=7)), 7.0),
        (SimpleNamespace(server=SimpleNamespace(answer_reserve_sec=-3)), 0.0),
        (SimpleNamespace(server=SimpleNamespace(answer_reserve_sec=True)), 0.0),
    ],
)
def test_retrieval_reserve_sec_matches_old_copy(cfg: Any, expected: float) -> None:
    from src.nodes.query_validator import retrieval_reserve_sec

    assert retrieval_reserve_sec(cfg) == expected


# ── A5 인가 술어 배선 (alarm 운영자 뷰) ─────────────────────────────────────


@pytest.mark.parametrize("auth_on", [True, False])
@pytest.mark.parametrize(
    "user",
    [{}, {"role": "user"}, {"role": "admin"}, {"role": "ADMIN"}, {"sub": "x"}],
)
def test_alarm_operator_matches_old_expression(auth_on: bool, user: dict) -> None:
    from src.api.routes.alarm import _is_alarm_operator

    config = SimpleNamespace(auth=SimpleNamespace(enabled=auth_on))
    old = bool(not config.auth.enabled) or (bool(user) and user.get("role") == "admin")
    assert _is_alarm_operator(config, user) is old


# ── A6 SSE 직렬화 바이트 ────────────────────────────────────────────────────


def test_sse_event_bytes_unchanged_for_constant_types() -> None:
    from src.api.routes.query import _sse_event

    for const, literal in ((SSE_DONE, "done"), (SSE_ERROR, "error")):
        payload = {"type": const, "response": "응답", "n": 1}
        assert _sse_event(payload) == (
            "data: " + json.dumps({"type": literal, "response": "응답", "n": 1},
                                  ensure_ascii=False) + "\n\n"
        )


def test_heartbeat_payload_type_literal() -> None:
    from src.api.routes.query import _heartbeat_sse_payload

    payload = _heartbeat_sse_payload(time.time(), {"idle_ms": 0.0})
    assert payload["type"] == "heartbeat" == SSE_HEARTBEAT
    assert list(payload)[0] == "type"  # 키 순서(직렬화 바이트) — type이 첫 키


# ── X5 침묵 폴백 로그 — 반환값 불변 ──────────────────────────────────────────


def test_governance_lookup_failure_still_off(monkeypatch, caplog) -> None:
    import src.config as config_module
    from src.schema_cache.redis_cache import RedisSchemaCache

    def _boom() -> Any:
        raise RuntimeError("cfg broken")

    monkeypatch.setattr(config_module, "load_config", _boom)
    cache = RedisSchemaCache(MagicMock())
    with caplog.at_level(logging.WARNING, logger="src.schema_cache.redis_cache"):
        assert cache._governance_enabled() is False
    warned = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warned and not any(r.exc_info for r in warned)
    assert all("cfg broken" not in r.getMessage() for r in warned)


async def test_column_description_failure_returns_none(caplog) -> None:
    from unittest.mock import AsyncMock

    from src.schema_cache.redis_cache import RedisSchemaCache

    cache = RedisSchemaCache(MagicMock())
    cache._connected = True
    cache._redis = MagicMock()
    cache._redis.hget = AsyncMock(side_effect=ConnectionError("down"))
    cache._redis.aclose = AsyncMock(side_effect=ConnectionError("down"))
    with caplog.at_level(logging.WARNING, logger="src.schema_cache.redis_cache"):
        assert await cache.get_description("db1", "t.c") is None
        await cache.disconnect()
    # 종료 실패여도 상태는 끊김으로 정리된다(종전 흐름)
    assert cache._redis is None and cache._connected is False
    assert sum(1 for r in caplog.records if r.levelno == logging.WARNING) == 2
