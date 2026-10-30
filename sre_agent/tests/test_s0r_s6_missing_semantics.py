"""S0r 「WAS 아님」 판정과 S6 0건 결손 — 「없음」과 「조회 실패」를 가른다.

S0r: 게이트웨이 `apm_instance_map(hostname=…)`은 일치가 없으면 빈 목록 대신 `instance_unresolved`
오류를 낸다. 전 소스·도메인을 대조한 결과면 「WAS 아님」(no_was)이고, 대조가 불완전하거나 다른
오류면 「WAS 여부 미확인」 결손이다.
S6: 프로세스 API는 폴스타마다 따로라 조사 대상 소스를 `source`로 넘기고, 0건은 결손이다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from sre_agent.application.playbook_steps import (
    APM_INSTANCE_UNRESOLVED,
    APM_PARTIAL_MARKERS,
    NO_WAS_VERDICT,
    POLESTAR_PROCESS,
    RunScope,
    RunWindows,
    StepContext,
    host_to_was,
    process_top,
)

GATEWAY = Path(__file__).resolve().parents[2] / "apm_gateway" / "apm_gateway"


class Fake:
    def __init__(self, rule) -> None:
        self.rule = rule
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, calls):
        out = []
        for tool, args in calls:
            self.calls.append((tool, dict(args)))
            out.append(json.dumps(self.rule(tool, dict(args)), ensure_ascii=False))
        return out


def _ctx(*, apm=None, polestar=None) -> StepContext:
    w = RunWindows("2026-10-28T10:00:00", "2026-10-28T10:10:00", 40,
                   "2026-10-28T09:30:00", "2026-10-28T10:10:00")
    scope = RunScope(host="srv01", polestar_server="srv01", polestar_source="db_gp")
    return StepContext(payload={}, windows=w, scope=scope, args={}, targets=[],
                       polestar=polestar, apm=apm, now=datetime.now, prior={})


def _unresolved(suffix: str = "") -> dict:
    return {"error": APM_INSTANCE_UNRESOLVED,
            "reason": f"hostname 'srv01'에 대응하는 APM 인스턴스 없음(인스턴스 4713건 대조{suffix})"
                      " — 정합 파일 overrides로 매핑할 수 있다"}


# ── S0r ──────────────────────────────────────────────────────────────────────


def test_s0r_complete_no_match_is_no_was_not_missing():
    res = host_to_was(_ctx(apm=Fake(lambda t, a: _unresolved())))
    assert res.status == "ok"
    assert res.verdict == NO_WAS_VERDICT
    assert "4713건 대조" in res.reason and "'srv01'" in res.reason
    assert res.missing == []


@pytest.mark.parametrize("marker", APM_PARTIAL_MARKERS)
def test_s0r_partial_compare_stays_undetermined(marker):
    res = host_to_was(_ctx(apm=Fake(lambda t, a: _unresolved(f" · {marker}"))))
    assert res.status == "missing"
    assert res.verdict is None
    assert res.reason.startswith("WAS 여부 미확인")


def test_s0r_gateway_failure_is_undetermined_with_reason():
    apm = Fake(lambda t, a: {"error": "source_unavailable", "reason": "인벤토리 적재 중"})
    res = host_to_was(_ctx(apm=apm))
    assert res.status == "missing"
    assert res.reason == "WAS 여부 미확인 — apm_instance_map: source_unavailable: 인벤토리 적재 중"


@pytest.mark.skipif(not GATEWAY.exists(), reason="apm_gateway 소스 없음(배포 사본)")
def test_gateway_wording_copy_matches():
    """판정이 게이트웨이 문구 사본에 기대므로 원문이 바뀌면 여기서 깨진다."""
    errors = (GATEWAY / "domain" / "errors.py").read_text(encoding="utf-8")
    sources = (GATEWAY / "application" / "sources.py").read_text(encoding="utf-8")
    assert f'INSTANCE_UNRESOLVED = "{APM_INSTANCE_UNRESOLVED}"' in errors
    for marker in APM_PARTIAL_MARKERS:
        assert marker in sources
    assert "건 대조" in sources


# ── S6 ───────────────────────────────────────────────────────────────────────


def _proc(rows):
    return {"rows": rows, "row_count": len(rows), "total_processes": len(rows),
            "captured_at": "2026-10-28 10:05:00"}


def test_s6_passes_polestar_source():
    pol = Fake(lambda t, a: _proc([{"name": "java", "pid": 1, "pcpu": 80.0}]))
    res = process_top(_ctx(polestar=pol))
    assert res.status == "ok"
    assert pol.calls == [(POLESTAR_PROCESS,
                          {"hostname": "srv01", "top_n": 10, "sort": "cpu", "source": "db_gp"})]


def test_s6_zero_rows_is_missing_with_reason():
    res = process_top(_ctx(polestar=Fake(lambda t, a: _proc([]))))
    assert res.status == "missing"
    assert res.missing == [POLESTAR_PROCESS]
    assert "0건" in res.reason and "db_gp" in res.reason
    assert res.data["rows"] == [] and res.badges


def test_s6_tool_error_unchanged():
    res = process_top(_ctx(polestar=Fake(lambda t, a: {"error": "소스 db_gp의 프로세스 API 미설정"})))
    assert res.status == "missing"
    assert res.reason == f"{POLESTAR_PROCESS}: 소스 db_gp의 프로세스 API 미설정"


def test_s6_zero_rows_reason_shows_resolved_hostname():
    """MCP가 등록명을 OS 호스트명으로 바꿔 조회했으면 사유에 실제 조회 값과 해소 노트를 보인다."""
    data = {"rows": [], "total_processes": 0, "captured_at": "2026-10-28 10:05:00",
            "hostname": "yd-web01", "hostname_note": "폴스타 등록명 'srv01' → 호스트명 'yd-web01'로 조회"}
    res = process_top(_ctx(polestar=Fake(lambda t, a: data)))
    assert res.status == "missing"
    assert "호스트 'yd-web01'" in res.reason and "→ 호스트명 'yd-web01'" in res.reason
