"""plans/193 독립 검증(verifier) — 구현 테스트가 덮지 않은 경로.

- 포털 탭 첫 mount가 예외로 끝난 뒤 재시도: `noise.js`의 실물 `mountOnce`(한 페이지 한
  인스턴스)와 `app.js`의 실물 `loadNoiseConsole`을 함께 돌린다. 두 번째 mount는 첫 결과
  (null)를 그대로 돌려주므로 빈 화면이 안내 없이 남으면 안 된다
  (지적 m-1 — 교정 뒤 「새로고침」 안내를 남기고 멈춤).
- 답변 스트리밍 중 관제 탭 서랍 진입(진단 꺼짐): 실물 `openInvestigationEntry` →
  `openInvestigationConversation`이 거부한다 — 사유가 채팅 오류 칸(`showError`)뿐 아니라 관제 탭
  안에도 보여야 한다(지적 M-1 — 교정 뒤 관제 뷰 일시 안내).
  실브라우저 재현은 scratchpad `verify_193.py` V1.
- 회귀 고정(통과): 독립 두 페이지 마크업에 `.noise-view` 조상이 없어 포털 층 규칙이 새지 않는다 ·
  포털 탭 mount 인자에 운영자 경로·토큰이 없다(K-2 A).

하네스는 plans/193 W2 테스트(`test_ui_plan193_w2_portal_tab`)의 가짜 DOM을 그대로 쓴다.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.test_api.test_ui_plan192_w1_entry import body_of
from tests.test_api.test_ui_plan193_w2_portal_tab import _caps, _run

_ROOT = Path(__file__).resolve().parents[2]
_STATIC = _ROOT / "src" / "static"
_NOISE_JS = _STATIC / "js" / "noise.js"


def _real_mount_once() -> str:
    """noise.js 실물 `mountOnce` + 그 상태 변수(started·instance) 선언."""
    src = _NOISE_JS.read_text(encoding="utf-8")
    assert re.search(r"^var started = false;$", src, re.M)
    assert re.search(r"^var instance = null;$", src, re.M)
    return "var started = false;\nvar instance = null;\n" + body_of(src, "mountOnce", "noise.js")


def test_retry_after_mount_exception_recovers_or_keeps_notice() -> None:
    out = _run(
        _caps(True)
        + r"""
applyNoiseTabCapability();
setActiveView("noise");
await settle();
var first = { notice: !!noiseView.querySelector(".noise-view-notice"), handle: noiseHandle };
setActiveView("chat");
setActiveView("noise");          // 안내대로 「탭을 다시 연다」
await settle();
var second = { notice: !!noiseView.querySelector(".noise-view-notice"), handle: noiseHandle,
               mountCalls: mountCalls, loadingStuck: noiseLoading !== null };
setActiveView("chat");
setActiveView("noise");          // 한 번 더
await settle();
out = { first: first, second: second,
        fetches: requests.filter(function (u) { return u === "/static/noise.html"; }).length };
""",
        extra=_real_mount_once()
        + r"""
var mountCalls = 0;
function mount(root, opts) {
    mountCalls += 1;
    if (mountCalls === 1) throw new Error("첫 mount 실패(가정)");
    return { pause: function () {}, resume: function () {}, unmount: function () {} };
}
FAKE_NC = { mount: mountOnce };
""",
    )
    # 첫 실패는 안내가 뜬다(구현대로)
    assert out["first"] == {"notice": True, "handle": None}
    # 기대: 다시 열면 붙거나(핸들) 적어도 실패 안내가 남는다 — 침묵 빈 화면 금지
    second = out["second"]
    assert second["handle"] is not None or second["notice"] is True, out


def test_rejected_entry_while_streaming_is_visible_on_noise_tab() -> None:
    out = _run(
        _caps(True)
        + r"""
applyNoiseTabCapability();
setActiveView("noise");
await settle();
isProcessing = true;               // 답변 스트리밍 중
mounts[0].opts.onOpenInvestigation("inv-7");
out = { view: activeView, errors: errors,
        notice: noiseView.querySelector(".noise-view-notice")
            ? noiseView.querySelector(".noise-view-notice").textContent : null };
""",
        drop=("openInvestigationEntry",),
        extra=r"""
var isProcessing = false;
var errors = [];
function showError(msg) { errors.push({ msg: msg, view: activeView }); }
window.DiagnosisView = { openIfEnabled: function () { return false; } };   // 장애 진단 꺼짐
""",
    )
    # 실물은 관제 탭에 머물고(view noise) 사유는 채팅 오류 칸으로만 간다
    assert out["view"] == "noise"
    assert out["errors"] and "답변이 끝난 뒤" in out["errors"][0]["msg"]
    # 기대: 관제 탭 안에서 사유가 보인다
    assert out["notice"] and "답변이 끝난 뒤" in out["notice"], out


def test_standalone_pages_have_no_portal_layer_ancestor() -> None:
    """`.noise-view` 범위 z-index 규칙이 독립 두 페이지에 새지 않는다 — 조상 클래스가 없다."""
    css = (_STATIC / "css" / "noise-console.css").read_text(encoding="utf-8")
    scoped = re.findall(r"^\.noise-view [^{]+\{[^}]*z-index", css, re.M)
    assert len(scoped) == 5
    for page in ("noise.html", "admin/noise.html"):
        html = (_STATIC / page).read_text(encoding="utf-8")
        assert not re.search(r'class="[^"]*\bnoise-view\b', html), page
    # noise.js도 그 클래스를 만들지 않는다(포털 컨테이너만 가진다)
    assert "noise-view" not in _NOISE_JS.read_text(encoding="utf-8")


def test_portal_mount_uses_user_mode_and_user_token_only() -> None:
    """K-2 A — 포털 탭 mount는 사용자 모드 · `user_token`만 · 운영자 토큰·경로 0."""
    out = _run(
        _caps(True)
        + r"""
store.admin_token = "a-1";         // break-glass 토큰이 같이 있어도
applyNoiseTabCapability();
setActiveView("noise");
await settle();
var o = mounts[0].opts;
out = { mode: o.mode, token: o.token() };   // 게터(교정 m-4) — 부를 때의 user_token
"""
    )
    assert out == {"mode": "user", "token": "u-1"}
    app = (_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    body = body_of(app, "loadNoiseConsole", "app.js")
    assert "admin_token" not in body and "/admin/" not in body


def test_noise_page_has_nothing_the_portal_loader_would_drop() -> None:
    """포털 조각 로더(`loadNoiseConsole`)는 머리 `<link rel=stylesheet>`·본문 `<script src>`만 옮겨
    싣는다 — 인라인 스크립트·`<style>`·`on*=` 속성·머리의 다른 스크립트가 생기면 포털 탭에서만
    조용히 빠지거나(스크립트·스타일) 그대로 살아난다(속성). 지금은 0건이어야 한다."""
    html = (_STATIC / "noise.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)
    assert "<style" not in html
    assert not re.search(r"\son[a-z]+\s*=", html)
    head = html[: html.index("<body")]
    # 머리의 스크립트는 테마 부트스트랩 하나 — 포털이 이미 싣는다
    assert re.findall(r'<script src="([^"?]+)', head) == ["/static/js/theme.js"]
    assert "/static/js/theme.js" in (_STATIC / "index.html").read_text(encoding="utf-8")
