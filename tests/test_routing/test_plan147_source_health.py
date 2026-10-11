"""plans/147 W4 — 제니퍼 소스 정합 판정 `reconcile`·점검 `check_sources`(D-322 ⑥).

  1. 일치 → 전 소스 사용 가능 · 불일치 3종(레지스트리만·게이트웨이만·도달 실패)
  2. 미연결(레지스트리만)만 사용 가능 집합에서 빠진다 — 도달 실패·degraded는 남는다
  3. 점검 실패(봉투 오류·계약 밖·연결 실패·시간 초과) → `available=None`(전 소스 사용 가능)
  4. 보고서·로그 줄에 URL·토큰 0
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import pytest

from src.clients.source_mcp_client import SourceMcpError
from src.routing import apm_source_health as ash
from src.routing.registry import SourceSpec

URL = "http://gw-secret-host.example:9096/sse"
TOKEN = "tok-very-secret-p147"
REG = (
    SourceSpec(id="bank", label="은행존 제니퍼", zone="bankjon", terms=("은행존", "은행")),
    SourceSpec(id="common", label="공동존 제니퍼", zone="gongjon",
               terms=("공동존", "김포", "여의도")),
    SourceSpec(id="legacy", label="레거시 제니퍼", zone="bankjon", terms=("레거시",)),
)


def _row(sid: str, *, reachable: bool = True, status: str = "ok", domains: int | None = 2,
         configured: bool = True) -> dict[str, Any]:
    return {"source_id": sid, "status": status, "jennifer_configured": configured,
            "jennifer_reachable": reachable, "domain_count": domains if reachable else None,
            "allowlist_size": 41, "api_calls_total": 1}


def _env(rows: list[dict[str, Any]], status: str = "ok") -> dict[str, Any]:
    return {"rows": rows, "row_count": len(rows), "queried_at": "2026-10-08T10:00:00+09:00",
            "source_kind": "apm_api", "source": "jennifer", "tool": "gateway_health",
            "limits": [], "status": status, "poller": {"enabled": False}}


def _by_id(report: ash.SourceHealthReport) -> dict[str, ash.SourceHealth]:
    return {s.id: s for s in report.sources}


# ─── 1·2. 판정 ────────────────────────────────────────────────────────────────


def test_all_consistent_every_source_available() -> None:
    report = ash.reconcile(REG, _env([_row("bank"), _row("common"), _row("legacy")]))
    assert report.check_ok and report.consistent
    assert report.available == frozenset({"bank", "common", "legacy"})
    assert (report.registry_only, report.gateway_only, report.unreachable) == ((), (), ())
    common = _by_id(report)["common"]
    assert common.to_dict() == {
        "id": "common", "label": "공동존 제니퍼", "zone": "gongjon", "term_count": 3,
        "in_registry": True, "gateway_configured": True, "reachable": True,
        "domain_count": 2, "status": "ok"}


def test_registry_only_source_is_dropped_from_available() -> None:
    """레지스트리에만 있는 소스(미연결)는 선택지에서 뺀다(R-7 · AC-5)."""
    report = ash.reconcile(REG, _env([_row("bank"), _row("common")]))
    assert report.registry_only == ("legacy",)
    assert report.available == frozenset({"bank", "common"})
    legacy = _by_id(report)["legacy"]
    assert (legacy.status, legacy.gateway_configured, legacy.in_registry) == \
        ("registry_only", False, True)
    assert not report.consistent


def test_gateway_only_source_is_listed_but_not_available() -> None:
    """게이트웨이에만 있는 소스는 존·단어 미상 — 선택 불가(표시만)."""
    report = ash.reconcile(REG, _env([_row("bank"), _row("common"), _row("legacy"),
                                      _row("drsite")]))
    assert report.gateway_only == ("drsite",)
    assert "drsite" not in (report.available or set())
    assert report.available == frozenset({"bank", "common", "legacy"})
    dr = _by_id(report)["drsite"]
    assert (dr.status, dr.in_registry, dr.label, dr.zone, dr.term_count) == \
        ("gateway_only", False, "", "", 0)
    assert [s.id for s in report.sources] == ["bank", "common", "legacy", "drsite"], \
        "레지스트리 선언 순서 → 게이트웨이에만 있는 소스"


def test_unreachable_source_stays_available() -> None:
    """도달 실패는 선택지에서 빼지 않는다 — 조회 시 종전 실패 고지(계획 §4.4)."""
    report = ash.reconcile(REG, _env([_row("bank"), _row("common", reachable=False,
                                                         status="degraded"),
                                      _row("legacy")], status="degraded"))
    assert report.unreachable == ("common",)
    assert report.available == frozenset({"bank", "common", "legacy"})
    common = _by_id(report)["common"]
    assert (common.status, common.reachable, common.domain_count) == ("unreachable", False, None)
    assert not report.consistent
    assert "도달 실패 ['common']" in report.log_line()


def test_reachable_but_degraded_is_degraded_not_mismatch() -> None:
    report = ash.reconcile(REG, _env([_row("bank", status="degraded", domains=0),
                                      _row("common"), _row("legacy")], status="degraded"))
    assert _by_id(report)["bank"].status == "degraded"
    assert report.consistent, "도메인 0건은 불일치가 아니다(카드에서 강조만)"
    assert report.available == frozenset({"bank", "common", "legacy"})


def test_unconfigured_gateway_row_counts_as_registry_only() -> None:
    report = ash.reconcile(REG, _env([_row("bank"), _row("common"),
                                      _row("legacy", configured=False, reachable=False)]))
    assert report.registry_only == ("legacy",)
    assert "legacy" not in (report.available or set())


def test_gateway_not_configured_envelope_means_nothing_available() -> None:
    """게이트웨이가 소스 0개(`not_configured`)면 점검은 성공이고 레지스트리 전 소스가 미연결이다."""
    report = ash.reconcile(REG, _env([], status="not_configured"))
    assert report.check_ok
    assert report.available == frozenset()
    assert report.registry_only == ("bank", "common", "legacy")


# ─── 3. 점검 실패 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("envelope", [
    None,
    {"error": "unauthorized", "reason": "bad token"},
    {"status": "ok"},                       # rows 없음
    {"rows": "nope"},
])
def test_unusable_envelope_is_check_failure(envelope) -> None:
    report = ash.reconcile(REG, envelope)
    assert not report.check_ok
    assert report.available is None, "점검 실패 = 레지스트리 전 소스 사용 가능(선택지 유지)"
    assert {s.status for s in report.sources} == {"unknown"}
    assert report.error
    assert "레지스트리 전 소스를 사용 가능" in report.log_line()


def _factory(*, envelope: dict | None = None, exc: Exception | None = None,
             delay: float = 0.0, calls: list | None = None):
    class _Session:
        async def call_tool(self, name: str, arguments: dict):
            if calls is not None:
                calls.append((name, dict(arguments)))
            if delay:
                await asyncio.sleep(delay)
            if exc is not None:
                raise exc
            return envelope

    @asynccontextmanager
    async def factory(url, headers):
        yield _Session()

    return factory


async def test_check_sources_success_uses_health_tool_once() -> None:
    calls: list = []
    report = await ash.check_sources(
        URL, TOKEN, REG,
        session_factory=_factory(envelope=_env([_row("bank"), _row("common")]), calls=calls))
    assert calls == [("gateway_health", {})]
    assert report.check_ok and report.available == frozenset({"bank", "common"})
    assert report.checked_at


async def test_check_sources_timeout_is_failure_not_raise() -> None:
    report = await ash.check_sources(
        URL, TOKEN, REG, timeout=0.05,
        session_factory=_factory(envelope=_env([_row("bank")]), delay=1.0))
    assert not report.check_ok and report.available is None
    assert "초과" in (report.error or "")


@pytest.mark.parametrize("exc", [SourceMcpError("APM 게이트웨이 연결·호출 실패: ConnectError"),
                                 RuntimeError(f"boom {URL} {TOKEN}")])
async def test_check_sources_errors_are_failure_without_secrets(exc) -> None:
    report = await ash.check_sources(URL, TOKEN, REG, session_factory=_factory(exc=exc))
    assert not report.check_ok and report.available is None
    blob = repr(report.to_dict()) + report.log_line()
    assert TOKEN not in blob and URL not in blob and "gw-secret-host" not in blob


# ─── 4. 비밀 값 0 ────────────────────────────────────────────────────────────


def test_report_and_log_line_carry_no_secrets() -> None:
    report = ash.reconcile(REG, _env([_row("bank"), _row("drsite")]))
    blob = repr(report.to_dict()) + report.log_line()
    for word in (URL, TOKEN, "http", "Bearer"):
        assert word not in blob
    body = report.to_dict()
    assert body["available"] == ["bank"], "사용 가능 목록은 레지스트리 선언 순서"
    assert set(body) == {"check_ok", "error", "checked_at", "consistent", "available",
                         "registry_only", "gateway_only", "unreachable", "sources"}
