"""plans/195 독립 검증(verifier) — 질의 생성·권한 판정·시간 되읽기·「조회한 것」 줄 배치.

- 운영 기본 마스킹 패턴(`***MASKED***`)에서 부분 가림(`mask_secrets` → `***`) 대상이 생략되는가
  (W2 테스트 config는 `mask_pattern="***"`라 두 표지가 같아 결함이 가려진다)
- `_attach_requery` 권한 판정: 관리자·None·[] 의미
  (폴스타 `allowed_db_ids` · 제니퍼 `allowed_sources`)
- 실제 산출 문장 전체가 질의응답 시간 해석기(`resolve_query_time`)에서
  사건창·변경 창 그대로 풀리는가
- 상세형 고정 카드에 런북이 있을 때 「조회한 것」 줄이 제 단계 본문에 붙는가
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import src.domain.investigation_requery as requery_mod
from src.api.routes.alarm import _attach_requery
from src.domain.investigation_requery import build_requery
from src.domain.query_time import resolve_query_time

_ROOT = Path(__file__).resolve().parents[1]
_BV_JS = _ROOT / "src" / "static" / "js" / "briefing-view.js"
PROD_MASK = "***MASKED***"  # src/config/security.py 기본값
ACTIVE = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]


def _cfg(mask: str = PROD_MASK) -> SimpleNamespace:
    return SimpleNamespace(
        noise_gate=SimpleNamespace(investigation_requery_enabled=True),
        security=SimpleNamespace(mask_pattern=mask),
        multi_db=SimpleNamespace(get_active_db_ids=lambda: list(ACTIVE)),
    )


def _rec(scope_over: dict[str, Any] | None = None, action: str = "apm.golden_signals") -> dict:
    scope = {
        "host": "kbl-host-01",
        "targets": [{"instance": "was01"}],
        "incident_window": {"start": "2026-10-26T19:10:00", "end": "2026-10-26T19:40:00"},
        "polestar_server": "KBL-HOST-01",
        "polestar_source": "polestar_cm_gp",
        "polestar_link": "linked",
    }
    scope.update(scope_over or {})
    return {
        "investigation_id": "inv-v",
        "briefing": {"playbook": {"scope": scope, "sections": [
            {"step": "S2", "title": "골든 시그널", "status": "ok", "action": action},
        ]}},
    }


@pytest.fixture
def polestar_catalog(monkeypatch):  # noqa: ANN001, ANN201
    tpl = requery_mod.RequeryTemplate(
        "apm.golden_signals", "polestar", "server", "incident", "text2sql", "", "TEST-PS-1",
        False, "{target} 서버 {window} CPU 사용률 보여줘",
    )
    monkeypatch.setattr(requery_mod, "REQUERY_CATALOG", (*requery_mod.REQUERY_CATALOG, tpl))


# ── 마스킹 표지 불일치 ────────────────────────────────────────────────────


def test_partially_masked_instance_dropped_with_production_mask_pattern() -> None:
    rec = _rec({"targets": [{"instance": "was01"},
                            {"instance": "was02 Bearer abcdefghijklmnopqrstuvwxyz123456"}]})
    view: dict[str, Any] = {}
    _attach_requery(view, rec, _cfg(PROD_MASK), {"sub": "u", "role": "user"})
    prompts = [i["prompt"] for i in view["requery"]]
    assert prompts, "깨끗한 인스턴스 질의는 남아야 한다"
    assert all("***" not in p for p in prompts), prompts


# ── 권한 판정(관리자 · None · []) ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("user", "ps_denied", "apm_denied"),
    [
        ({"sub": "a", "role": "admin", "allowed_db_ids": [], "allowed_sources": []}, False, False),
        (
            {"sub": "u", "role": "user", "allowed_db_ids": None, "allowed_sources": None},
            False, False,
        ),
        ({"sub": "u", "role": "user"}, False, False),  # 키 없음 = None = 전체
        ({"sub": "u", "role": "user", "allowed_db_ids": [], "allowed_sources": []}, True, True),
        ({"sub": "u", "role": "user", "allowed_db_ids": ["polestar_b0"],
          "allowed_sources": ["polestar"]}, True, True),
        ({"sub": "u", "role": "user", "allowed_db_ids": ["polestar_cm_gp"],
          "allowed_sources": ["apm"]}, False, False),
    ],
)
def test_authz_semantics(polestar_catalog, user, ps_denied, apm_denied) -> None:  # noqa: ANN001
    view: dict[str, Any] = {}
    _attach_requery(view, _rec(), _cfg(), user)
    items = view["requery"]
    ps = [i for i in items if i["axis"] == "polestar"]
    apm = [i for i in items if i["axis"] == "apm"]
    assert ps and apm
    assert all(("disabled_reason" in i) is ps_denied for i in ps)
    assert all(("disabled_reason" in i) is apm_denied for i in apm)


def test_flag_off_leaves_view_untouched() -> None:
    cfg = _cfg()
    cfg.noise_gate.investigation_requery_enabled = False
    view: dict[str, Any] = {"status": "done"}
    _attach_requery(view, _rec(), cfg, {"sub": "u", "role": "user"})
    assert view == {"status": "done"}


def test_generation_failure_is_empty_list_not_raise() -> None:
    cfg = _cfg()
    cfg.multi_db = None  # 폴스타 항목이 없으면 닿지 않는다 — 생성 단계 자체를 깨뜨린다
    cfg.security = None
    view: dict[str, Any] = {}
    _attach_requery(view, _rec(), cfg, {"sub": "u", "role": "user"})
    assert view["requery"] == []


# ── 실제 산출 문장 → 질의응답 시간 해석기 ─────────────────────────────────

_NOW = datetime(2026, 10, 28, 9, 0)
_TARGETS = ["was01", "svr-was-01", "WAS_8080", "kp1-web-10", "gp-ap02:8080", "B1"]
_WINDOWS = [
    (datetime(2026, 10, 26, 19, 10), datetime(2026, 10, 26, 19, 40)),
    (datetime(2026, 10, 26, 11, 50), datetime(2026, 10, 26, 12, 20)),  # 정오 걸침
    (datetime(2026, 10, 26, 23, 45), datetime(2026, 10, 27, 0, 15)),  # 자정 걸침
    (datetime(2026, 10, 27, 0, 0), datetime(2026, 10, 27, 0, 30)),   # 0시 정각
]


@pytest.mark.parametrize("target", _TARGETS)
@pytest.mark.parametrize(("start", "end"), _WINDOWS)
def test_generated_prompts_resolve_to_incident_and_change_windows(
    target: str, start: datetime, end: datetime
) -> None:
    scope = {
        "host": target, "targets": [{"instance": target}],
        "incident_window": {"start": start.isoformat(), "end": end.isoformat()},
    }
    actions = sorted({t.action for t in requery_mod.REQUERY_CATALOG})
    pb = {"scope": scope, "sections": [
        {"step": f"S{i}", "title": a, "status": "ok", "action": a} for i, a in enumerate(actions)
    ]}
    items = build_requery(pb, mask_marker=PROD_MASK)
    by_ref = {t.source_ref: t for t in requery_mod.REQUERY_CATALOG}
    seen = set()
    for item in items:
        tpl = by_ref[item["source_ref"]]
        if tpl.window == "none":
            continue
        seen.add(tpl.window)
        qt = resolve_query_time(item["prompt"], _NOW)
        assert qt.clarify is None, item["prompt"]
        want = (start, end) if tpl.window == "incident" else (end - timedelta(hours=24), end)
        # 제니퍼 창은 사건 주체(`event`)를 쓴다
        # (src/orchestration/apm/window._resolved_bounds) — 분 정밀.
        ev = qt.event
        got = (ev.start.replace(tzinfo=None), ev.end.replace(tzinfo=None))
        assert got == want, (item["prompt"], got)
        # 지표 주체는 시 단위로 넓혀 덮는다(설계) — 창을 잘라 먹지 않는지만 본다
        mt = qt.metric
        assert mt.start.replace(tzinfo=None) <= want[0] and mt.end.replace(tzinfo=None) >= want[1]
    assert seen == {"incident", "change"}


# ── 「조회한 것」 줄 배치(런북 있는 상세형) ───────────────────────────────


def _node(script: str) -> Any:
    if shutil.which("node") is None:
        pytest.skip("node 미설치")
    done = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                          timeout=30, check=False)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_queried_line_index_matches_step_when_runbook_present() -> None:
    view = {
        "status": "done", "investigation_id": "inv-r",
        "briefing": {"playbook": {
            "id": "host.cpu",
            "runbook": {"meaning": "CPU 포화", "mitigation": "프로세스 확인"},
            "sections": [
                {"step": "S1", "action": "apm.golden_signals", "title": "골든", "status": "ok",
                 "queries": [{"tool": "apm_app_health", "phrase": "S1 조회"}]},
                {"step": "S2", "action": "apm.events_errors", "title": "오류", "status": "ok",
                 "queries": [{"tool": "apm_events", "phrase": "S2 조회"}]},
            ],
        }},
    }
    script = f"""
var BV = require({json.dumps(str(_BV_JS))});
var html = BV.renderBody({json.dumps(view, ensure_ascii=False)}, {{ mode: "detail" }});
var re = /<details class="([^"]*)"([^>]*)>/g, m, order = [];
while ((m = re.exec(html))) {{
    if (m[1].split(" ").indexOf("bv-step") < 0) continue;
    var s = /data-bv-step="([^"]*)"/.exec(m[2]);
    order.push(s ? s[1] : "(" + m[1] + ")");
}}
process.stdout.write(JSON.stringify(order));
"""
    order = _node(script)
    # 런북(`bv-step bv-runbook`)은 단계 섹션 앞에 그려지고 `data-bv-step`이 없다 — 순번 매칭이면
    # 한 칸 밀린다(교정 전 결함). decorate()는 `data-bv-step` 값으로 매칭해야 한다.
    assert [o for o in order if not o.startswith("(")] == ["S1", "S2"], order
    src = _BV_JS.read_text(encoding="utf-8")
    deco = src[src.index("function decorate"):]
    deco = deco[: deco.index("\n    function ", 1)] if "\n    function " in deco[1:] else deco
    assert 'getAttribute("data-bv-step")' in deco
