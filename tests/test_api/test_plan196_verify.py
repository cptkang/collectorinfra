"""plans/196 독립 검증 — 계약 키 정합 · 플래그 off 비트 동일 · Q5 자동 결합 규칙 · D6 누수.

`xfail(strict=True)`는 검증에서 찾은 결함의 재현이다 — 결함이 고쳐지면 XPASS로 실패하니 그때
마커를 지운다. DB·Redis·조사 서비스·LLM은 쓰지 않는다.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noise_gate.application.nodes.investigation_trigger import _attach_polestar_link
from noise_gate.domain.host_link import (
    BASIS_NODE_ADDRESS,
    apm_source_id,
    polestar_link_hint,
    user_selected_hint,
)
from noise_gate.infrastructure.host_link_store import HostLinkStore
from noise_gate.infrastructure.investigation_store import playbook_user_view
from tests.mocks.async_redis import FakeAsyncRedis
from tests.test_api.test_plan187_w11_manual import (
    _FakeClient,
    _reset_poll_state,  # noqa: F401 — autouse
)
from tests.test_api.test_plan196_host_link import (
    ADMIN,
    GONGJON_USER,
    LINK,
    POD,
    _app,
    _host_config,
    _inject,
    _seed,
    audits,  # noqa: F401 — 픽스처 재사용
    probes,  # noqa: F401 — 픽스처 재사용
)

_ROOT = Path(__file__).resolve().parents[2]
_RUNNER = _ROOT / "sre_agent" / "sre_agent" / "application" / "playbook_runner.py"
_HL_JS = _ROOT / "src" / "static" / "js" / "admin-host-links.js"
_DV_JS = _ROOT / "src" / "static" / "js" / "diagnosis-view.js"


# ─── 1. 플래그 off · 비APM — 페이로드 비트 동일 ───────────────────────────────


def _cfg(enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(noise_gate=SimpleNamespace(host_link_enabled=enabled))


def _event(db_id: str = "jennifer_cm_prd", hostname: str = POD) -> SimpleNamespace:
    return SimpleNamespace(db_id=db_id, hostname=hostname, alarm_id="a1")


def _payload(solution: str | None = "apm") -> dict[str, Any]:
    hints = {"solution": solution, "source_id": "cm_prd"} if solution else None
    meta: dict[str, Any] = {"contract_version": "1"}
    if hints is not None:
        meta["hints"] = hints
    return {"meta": meta, "decision": {"fingerprint": "f"}}


def _run(coro):  # noqa: ANN001, ANN202
    return asyncio.run(coro)


def test_flag_off_payload_untouched_even_with_explicit_link_and_rerun() -> None:
    """off면 호출자가 link·rerun을 넘겨도 페이로드는 바이트 동일(수동·자동 공용 함수)."""
    for payload in (_payload(), _payload(None)):
        before = json.dumps(payload, sort_keys=True)
        _run(_attach_polestar_link(
            _cfg(False), _event(), payload, store=HostLinkStore(FakeAsyncRedis()),
            link={"status": "linked", "db_id": "x", "server_name": "y"},
            rerun={"parent_investigation_id": "p", "axes": ["polestar"], "prior_sections": []},
        ))
        assert json.dumps(payload, sort_keys=True) == before


def test_flag_on_non_apm_payload_untouched() -> None:
    payload = _payload("polestar")
    before = copy.deepcopy(payload)
    _run(_attach_polestar_link(_cfg(True), _event("polestar_cm_gp"), payload,
                               redis=FakeAsyncRedis()))
    assert payload == before


# ─── 2. 자동 결합은 확정만(Q5) · 저장소 실패는 unresolved ───────────────────────


def _store_with(**save: Any) -> HostLinkStore:
    store = HostLinkStore(FakeAsyncRedis())
    _run(store.save("cm_prd", POD, by="u", by_role="user", **save))
    return store


@pytest.mark.parametrize(("save", "status"), [
    ({"kind": "link", "confirmed": False, "db_id": "polestar_cm_gp", "server_name": "S1"},
     "unresolved"),
    ({"kind": "absent", "confirmed": False}, "unresolved"),
    ({"kind": "link", "confirmed": True, "db_id": "polestar_cm_gp", "server_name": "S1"},
     "linked"),
    ({"kind": "absent", "confirmed": True}, "absent"),
])
def test_auto_path_uses_confirmed_only(save: dict, status: str) -> None:
    payload = _payload()
    _run(_attach_polestar_link(_cfg(True), _event(), payload, store=_store_with(**save)))
    link = payload["meta"]["hints"]["polestar_link"]
    assert link["status"] == status
    assert "rerun" not in payload["meta"]["hints"]
    if status == "linked":
        assert link["basis"] == "previous_confirmed" and link["server_name"] == "S1"


def test_auto_path_hostname_is_normalized_for_lookup() -> None:
    """저장 키는 정규화 hostname — 대소문자·FQDN이 달라도 같은 확정 연결을 쓴다."""
    store = _store_with(kind="link", confirmed=True, db_id="polestar_cm_gp", server_name="S1")
    payload = _payload()
    _run(_attach_polestar_link(_cfg(True), _event(hostname=POD.upper() + ".kb.local"), payload,
                               store=store))
    assert payload["meta"]["hints"]["polestar_link"]["status"] == "linked"


def test_auto_path_redis_failure_is_unresolved_not_raised() -> None:
    class _Broken:
        async def get(self, *_a: Any, **_k: Any) -> Any:
            raise ConnectionError("down")

    payload = _payload()
    _run(_attach_polestar_link(_cfg(True), _event(), payload, redis=_Broken()))
    assert payload["meta"]["hints"]["polestar_link"] == {"status": "unresolved"}


def test_link_key_symmetric_between_auto_and_manual_paths() -> None:
    """자동(원문 hints.source_id)과 수동(판정 기록 dbId만) 경로가 같은 키를 쓴다."""
    assert apm_source_id("jennifer_cm_prd", "cm_prd") == apm_source_id("jennifer_cm_prd")


# ─── 3. 계약 키 정합 — noise_gate 힌트 ↔ sre_agent 판독 ↔ 프론트 정규식 ─────────


def _runner_src() -> str:
    return _RUNNER.read_text(encoding="utf-8")


def test_polestar_link_basis_values_match_sre_agent_templates() -> None:
    """noise_gate가 싣는 `basis` 두 값이 sre_agent `POLESTAR_LINK_BASIS` 키와 같다."""
    src = _runner_src()
    block = re.search(r"POLESTAR_LINK_BASIS: dict\[str, str\] = \{\n(.*?)\n\}", src, re.S)
    assert block is not None
    sre_keys = set(re.findall(r'^\s+"([a-z_]+)":', block.group(1), re.M))
    now = __import__("datetime").datetime(2026, 10, 27)
    prev = polestar_link_hint({"state": "confirmed", "kind": "link", "db_id": "d",
                               "server_name": "s", "by": "b", "at": "t"}, now)
    user = user_selected_hint(db_id="d", server_name="s", polestar_hostname="h", by="b", at="t")
    # plans/199 · D-352 ② — 노드 주소 자동 연결 근거도 같은 키 집합이다
    assert {prev["basis"], user["basis"], BASIS_NODE_ADDRESS} == sre_keys
    # sre_agent `polestar_link()`이 linked로 받는 필수 키(db_id·server_name)와 상태 어휘
    statuses = re.search(r"_LINK_STATUSES = frozenset\(\{(.*?)\}\)", src, re.S)
    assert statuses is not None
    assert set(re.findall(r'"(\w+)"', statuses.group(1))) == {"linked", "absent", "unresolved"}
    assert prev["db_id"] and prev["server_name"] and user["db_id"] and user["server_name"]


def test_frontend_regexes_match_sre_agent_sentences() -> None:
    """화면 정규식(HOST_WAIT_RE·HOST_ABSENT_RE·HOST_LINKED_RE) ↔ sre_agent 사유·근거 문장."""
    src = _runner_src()

    def const(name: str) -> str:
        m = re.search(rf'^{name} = "(.*?)"', src, re.M)
        assert m is not None, name
        return m.group(1)

    js = _DV_JS.read_text(encoding="utf-8")

    def js_re(name: str) -> re.Pattern[str]:
        m = re.search(rf"var {name} = /(.*?)/;", js)
        assert m is not None, name
        return re.compile(m.group(1))

    assert js_re("HOST_WAIT_RE").search(const("POLESTAR_LINK_UNRESOLVED"))
    assert js_re("HOST_ABSENT_RE").search(const("POLESTAR_LINK_ABSENT"))
    linked = js_re("HOST_LINKED_RE")
    block = re.search(r"POLESTAR_LINK_BASIS: dict\[str, str\] = \{\n(.*?)\n\}", src, re.S)
    assert block is not None
    for sentence in re.findall(r':\s*"(.*?)"', block.group(1)):
        assert linked.search(sentence), sentence
    # (plans/199 · D-352 ④) 파드 미확정 문장은 선택 UI를 열지 않는 HOST_POD_RE에 걸린다
    pod = re.search(r'^POLESTAR_POD_UNRESOLVED = f"(.*?)"', src, re.M)
    assert pod is not None
    assert js_re("HOST_POD_RE").search(pod.group(1))


# ─── 4. 결함 재현 — 직접 입력 서버명(후보 0건) 경로 ────────────────────────────


def test_direct_input_body_sent_by_frontend_is_accepted(  # noqa: ANN201
    tmp_path, monkeypatch, probes, audits,  # noqa: ANN001, F811
):
    _seed(tmp_path)
    _inject(monkeypatch, _FakeClient())
    # 공동존 사용자 — 인가 소스(cm_gp·cm_yd) 중 cm_gp 한 곳에만 있다(cm_yd는 조회 실패).
    # 전 존(ADMIN)이면 픽스처상 b0·cm_gp 두 곳이 맞아 409 server_ambiguous다(본체 테스트).
    client = TestClient(_app(_host_config(tmp_path), GONGJON_USER))
    # diagnosis-view.js submitHost — 후보 0건 + 입력값이면 이 본문(test_ui_plan196 고정 값)
    res = client.post(LINK, json={"action": "select", "server_name": "kbl-kbland-admin-price"})
    assert res.status_code == 200, res.text


def test_frontend_direct_input_body_has_no_db_id() -> None:
    """재현 근거 — 화면 직접 입력 본문에는 db_id가 없다(코드 고정)."""
    js = _DV_JS.read_text(encoding="utf-8")
    m = re.search(r"else if \(!cands\.length && typed\) \{(.*?)\}", js, re.S)
    assert m is not None
    assert "server_name = typed" in m.group(1) and "db_id" not in m.group(1)


# ─── 5. 결함 재현 — 관리자 목록 응답 키(links ↔ items) ──────────────────────────


_HL_HARNESS = r"""
var store = {};
function El(tag) { this.tagName = tag; this.children = []; this.textContent = ""; this.style = {};
  this.className = ""; this.listeners = {}; }
El.prototype.appendChild = function (c) { this.children.push(c); return c; };
El.prototype.addEventListener = function (k, f) { this.listeners[k] = f; };
Object.defineProperty(El.prototype, "textContent", {
  get: function () { return this._t || ""; },
  set: function (v) { this._t = String(v); if (v === "") this.children = []; } });
globalThis.Node = El;
var ids = { hostLinksBody: new El("tbody"), hostLinksPatternBody: new El("tbody"),
  hostLinksNotice: new El("div"), refreshHostLinksBtn: new El("button") };
var ready = [];
globalThis.document = {
  getElementById: function (id) { return ids[id] || null; },
  createElement: function (t) { return new El(t); },
  addEventListener: function (k, f) { if (k === "DOMContentLoaded") ready.push(f); },
  querySelectorAll: function () { return []; } };
globalThis.localStorage = { getItem: function () { return "tok"; } };
globalThis.window = { confirm: function () { return true; } };
var LIST = __LIST__;
globalThis.fetch = function () {
  return Promise.resolve({ ok: true, status: 200,
    json: function () { return Promise.resolve(LIST); } });
};
"""


def test_admin_js_renders_links_from_real_response_shape() -> None:
    if shutil.which("node") is None:
        pytest.skip("node 미설치")
    # 실제 응답 모양 — admin_host_links.list_host_links
    listed = {"links": [{"state": "pending", "kind": "link", "source_id": "cm_prd",
                         "hostname": POD, "db_id": "polestar_cm_gp", "server_name": "S1",
                         "by": "u1", "at": "2026-10-27T10:00:00+00:00"}],
              "patterns": []}
    script = "\n".join([
        _HL_HARNESS.replace("__LIST__", json.dumps(listed)),
        f"require({json.dumps(str(_HL_JS))});",
        "ready.forEach(function (f) { f(); });",
        "ids.refreshHostLinksBtn.listeners.click();",
        "setTimeout(function () {",
        "  var rows = ids.hostLinksBody.children;",
        "  var empty = rows.length === 1 && rows[0].children.length === 1;",
        "  process.stdout.write(JSON.stringify({ rows: rows.length, empty: empty }));",
        "}, 50);",
    ])
    done = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30,
                          check=False)
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout)
    assert out == {"rows": 1, "empty": False}


def test_admin_api_returns_links_key(tmp_path, monkeypatch, probes, audits):  # noqa: ANN001, F811
    """재현 근거 — API 응답 키는 `links`다(`items` 아님)."""
    _seed(tmp_path)
    app = _app(_host_config(tmp_path), ADMIN, admin_router=True)
    body = TestClient(app).get("/api/v1/admin/host-links").json()
    assert set(body) == {"links", "patterns"}


# ─── 6. 결함 재현 — D6 개발자 사유가 사용자 뷰 scope로 샌다 ─────────────────────


def test_user_view_drops_scope_reason_detail() -> None:
    detail = ("APM 알람 페이로드에 폴스타 소스(meta.episode.infra_db_ids)가 없고 설정된 폴스타 "
              "소스도 없다 — APM 호스트 → 폴스타 서버 브리지 미구현(plans/165 F3)")
    briefing = {"playbook": {
        "scope": {"polestar_source": None, "polestar_reason_detail": detail},
        "sections": [{"step": "S4", "status": "missing", "reason": "r", "reason_detail": detail}],
    }}
    view = playbook_user_view(briefing, lambda s: s)
    assert view is not None
    assert "reason_detail" not in view["playbook"]["sections"][0]
    assert detail not in json.dumps(view, ensure_ascii=False)
