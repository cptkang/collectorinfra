"""plans/184 J5 — `apm_fleet` 기간 순위: 인스턴스 id가 없는 인스턴스는 묻지 않고 고지한다.

- 인스턴스 목록에서 `instanceId`가 빠진 인스턴스 → 시 단위 통계(`/api/status/application`)를 부르지
  않는다(종전: `instance_id=None`으로 호출 → 게이트웨이 400 → 인스턴스 실패·잠정 순위).
- 응답에는 `[한계]`로 알리고, 통계 행 없는 인스턴스와 같은 규약으로 순위 밖에 센다
  (`instances_total`·`instances_unranked` 포함 · 실패·잠정 순위 아님 · 호출 계획에서도 뺀다).

합성 Open API(`fleet_fake` + W6 `PeriodFake`) — 외부 네트워크 0 · 실 제니퍼 0.
"""

from __future__ import annotations

import httpx
import pytest
from apm_gateway.application import fleet_tools as fleet_module
from test_plan134_w6_period_ranking import PeriodFake, _ready

NO_ID = ("apm.test", 1, 2)  # (호스트, 도메인, k) — 인스턴스 목록에서 instanceId를 뺀다


class NoIdFake(PeriodFake):
    """`PeriodFake` + 인스턴스 목록의 한 인스턴스에서 `instanceId` 칸을 뺀다."""

    async def handler(self, request: httpx.Request) -> httpx.Response:
        resp = await super().handler(request)
        host, d = request.url.host, int(dict(request.url.params).get("domain_id", "0"))
        if request.url.path != "/api/instance" or (host, d) != NO_ID[:2]:
            return resp
        body = resp.json()
        for raw in body["result"]:
            if raw["instanceId"] == d * 100 + NO_ID[2]:
                del raw["instanceId"]
        return httpx.Response(200, json=body)


@pytest.mark.asyncio
async def test_instance_without_id_is_skipped_and_noticed(tmp_path, monkeypatch):
    fake = NoIdFake(1, hosts=("apm.test",), per_domain=3)
    fake.app_rows = {
        ("apm.test", 100): [{"name": "/a", "calls": 10, "failures": 0, "responseTime": 5.0,
                             "totalResponseTime": 50, "maxResponseTime": 9}],
        ("apm.test", 101): [{"name": "/b", "calls": 4, "failures": 0, "responseTime": 2.0,
                             "totalResponseTime": 8, "maxResponseTime": 3}],
    }
    planned: list[int] = []
    monkeypatch.setattr(fleet_module, "expect_calls", planned.append)
    fleet = await _ready(fake, tmp_path)

    out = await fleet.ranking(metric="calls", lookback_minutes=60)

    # id 없는 인스턴스는 묻지 않는다 — 통계 호출은 id 있는 두 인스턴스뿐(호출 계획도 같다)
    assert sorted(q["instance_id"] for _h, q in fake.status_calls()) == ["100", "101"]
    assert planned == [2]
    assert [(r["instance_id"], r["value"]) for r in out["rows"]] == [(100, 10), (101, 4)]
    # 고지 — 실패가 아니라 순위 밖(통계 행 없음과 같은 규약)
    assert (
        "[한계] 인스턴스 id가 없는 인스턴스 1개는 통계를 조회하지 않아 순위 밖이다"
        "(도메인 1 인스턴스 apm-i1-2)"
    ) in out["limits"]
    s = out["summary"]
    assert (s["instances_total"], s["instances_ranked"], s["instances_unranked"]) == (3, 2, 1)
    assert s["instances_failed"] == 0
    assert out["provisional"] is False
    assert not any("잠정 순위" in x for x in out["limits"])
