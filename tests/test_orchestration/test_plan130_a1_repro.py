"""plans/130 W0 · A-1 재현 — 말한 대상을 해석하지 못하면 첫 홉이 임의 인스턴스를 조회한다.

「이미지업무 WAS 응답시간」처럼 서버명 칸으로 온 대상이 폴스타 등록명(간선 E2)에서 미연결이면
`hostnames`가 비어 첫 홉(`apm.instances` 앞 N개)이 끼어들고, **말한 대상과 무관한** 인스턴스의
응답시간을 조회한다(plans/123 침묵 오답 유형). W4(대상 텍스트 해석 · M-4 첫 홉 규칙)부터
보기 도구 호출 0 · 「찾지 못함」 고지다(D-290 ⑥). W0 결함 재현을 옮긴 것이다 — 설정만 보강했다
(업무명 간선 E6을 모의로 바꿨다 — 실 DB 0).
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest

from src.config import DBHubConfig
from src.orchestration import apm_query as aq
from src.orchestration.entity_link import UNLINKED, LinkEntry

NOW = datetime(2026, 10, 6, 10, 0, 0)


def _cfg() -> SimpleNamespace:
    dbhub = DBHubConfig(_env_file=None, source_endpoints={"apm": "http://127.0.0.1:9096/sse"},
                        source_tokens='{"apm": "gw-token"}', source_call_timeout=10.0)
    return SimpleNamespace(dbhub=dbhub,
                           composite=SimpleNamespace(max_targets=10, fanout_concurrency=2,
                                                     audit_enabled=False),
                           multi_db=SimpleNamespace(get_active_db_ids=lambda: ["polestar_cm_gp"]))


def _env(tool: str, rows: list[dict]) -> dict:
    return {"rows": rows, "row_count": len(rows), "queried_at": "2026-10-06T10:00:00+09:00",
            "source_kind": "apm_api", "source": "apm", "tool": tool, "limits": []}


class _Gateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def factory(self):
        @asynccontextmanager
        async def open_(url, headers):
            yield self

        return open_

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, dict(arguments)))
        if name == "apm_instance_map":
            if any(arguments.get(k) for k in ("query", "business", "hostname")):
                reply = _env(name, [])  # 검색·업무·정합 — 해당 없음
            else:  # 대상 없는 목록(첫 홉)
                reply = _env(name, [{"instance_id": i, "instance_name": f"was{i:02d}_a",
                                     "hostname": f"was{i:02d}", "match_confidence": "high"}
                                    for i in range(1, 4)])
        else:
            reply = _env(name, [{"tps": 1.0}])
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(reply))], isError=False)


@pytest.mark.asyncio
async def test_named_but_unresolved_target_does_not_query_arbitrary_instances(monkeypatch):
    gw = _Gateway()
    monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())

    async def unlinked(refs, *, consumer, app_config):
        names = [str(r.server_name) for r in refs if r.server_name]
        return [], [LinkEntry(n, "server_name", "E2", UNLINKED, grade="none",
                              reason="등록 서버에서 찾지 못했다") for n in names], []

    monkeypatch.setattr(aq, "link_hostnames", unlinked)

    async def no_business(terms, *, app_config, authorized_db_ids):
        return {t: [] for t in terms}, [], []

    monkeypatch.setattr(aq, "link_business_names", no_business)
    isolated = {"parsed_requirements": {
        "filter_conditions": [{"field": "server_name", "op": "=", "value": "이미지업무"}],
        "time_range": None}, "conversation_context": {}, "thread_id": "th-1"}
    res = await aq.run_apm_query({"task_id": "t1", "agent": "apm_query",
                                  "views": ["apm.app_health"]},
                                 isolated, llm=None, app_config=_cfg(), now=NOW)
    view_calls = [name for name, _ in gw.calls if name != "apm_instance_map"]
    assert view_calls == [], f"말한 대상과 무관한 인스턴스를 조회했다: {gw.calls}"
    listing = [a for name, a in gw.calls if name == "apm_instance_map"
               and not any(a.get(k) for k in ("query", "business", "hostname"))]
    assert listing == [], "대상 텍스트가 있으면 첫 홉(목록 앞 N개 선정)으로 가지 않는다"
    text = json.dumps(res, ensure_ascii=False, default=str)
    assert "찾지 못" in text, "「찾지 못함」을 답한다"
