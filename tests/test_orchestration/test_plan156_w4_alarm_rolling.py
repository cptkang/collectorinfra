"""plans/156 W4 — 사건 시 단위 롤링 끝(G-3 (a) · D-333)의 알람 이력 종단.

「최근 3시간 알람」·「3시간 전 알람」(G-6)을 기준 시각 2026-10-13(화) 10:37 KST에 물으면 알람 이력
SQL의 `ctime` 경계가 `[07:37, 10:37)`(기준 시각 분 경계까지 — 종전 정시 절단 `[07:00, 10:00)`)이고
시간 조건 검증기(`check_time_conditions`)를 통과한다(재생성 0). LLM SQL 경로와 결정적 조립 경로가
같은 해석 단일 출처(`QueryTime.event` · D-309 ③)를 쓴다.

실 LLM·DB·네트워크 0 — plans/122 트랙 T 검증 하네스(`build_env` · 실제 `build_graph()` · 2단)를
그대로 쓴다(plans/159 W3 종단과 같은 방식).
"""

from __future__ import annotations

import importlib
from datetime import datetime

import pytest

from src.db_adapters.polestar.validators import check_time_conditions
from src.domain.time_spec import KST
from tests.test_plan122_t_verify import (  # noqa: F401 — build_env는 픽스처
    _TS_RE,
    DB_SINGLE,
    _tr,
    build_env,
)

NOW = datetime(2026, 10, 13, 10, 37, tzinfo=KST)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["최근 3시간 알람", "3시간 전 알람"])
@pytest.mark.parametrize("alarm_det", [False, True], ids=["llm_sql", "deterministic"])
async def test_e2e_hour_rolling_alarm_uses_anchor_minute_ctime_bounds(
    build_env, monkeypatch, alarm_det, text,  # noqa: F811
):
    env, _ = build_env("t2", DB_SINGLE, alarm_det=alarm_det)
    monkeypatch.setattr(importlib.import_module("src.nodes.input_parser"), "_now", lambda: NOW)
    out = await env.run(text, f"p156-w4-{alarm_det}-{len(text)}")
    qt = _tr(out)
    assert qt.clarify is None and qt.event is not None
    assert len(env.llm.sql_humans()) == (0 if alarm_det else 1), "검증기 반려(재생성) 0"
    ((_db, sql),) = env.executed
    assert _TS_RE.search(sql).groups() == ("2026-10-13 07:37:00", "2026-10-13 10:37:00")  # type: ignore[union-attr]
    assert check_time_conditions(sql, qt) == []
    final = out.get("final_response") or ""
    assert "[조회 기간] 2026-10-13 07:37 ~ 2026-10-13 10:37" in final, final
