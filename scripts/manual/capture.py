"""매뉴얼 캡처 (plans/116 §4.5) — captures.yaml 의 장면을 재생 서버 위에서 찍는다.

실행(루트 venv 에 playwright 가 없다 — 앱 코드를 import 하지 않는 독립 스크립트다):

    uv run --no-project --with playwright==1.63.0 --with pyyaml \\
        python scripts/manual/capture.py [--only ID ...] [--base http://127.0.0.1:18981]

전제: ``python -m scripts.manual.run_capture`` 가 캡처 서버(재생 그래프 · LLM 호출 0)를 띄우고
시드를 넣은 상태. 브라우저는 playwright 캐시의 Chrome for Testing 을 쓴다(다운로드하지 않는다).

결정성: 뷰포트 1440×900 · 배율 1 · 애니메이션 끔 · 캐럿 숨김 · 응답 내용은 녹화 재생이라 같다.
시각 표시(메시지 시각·경과 시간·알람 시각)는 캡처 시점을 따른다 — 결정 기록을 현재로 옮기므로
브라우저 시계만 고정하면 상대 시각이 어긋난다.
콜아웃 셀렉터가 없으면 **실패로 끝낸다** — 조용히 건너뛰면 매뉴얼 번호와 그림이 어긋난다.

데이터 위생(§4.7): 찍기 직전 화면 텍스트(DOM)를 검사한다.
저장소 루트 ``.env``(운영 설정)의 호스트·IP·시크릿 값이 보이거나,
설정 화면(``settings-*``·``dbconfig``)에 루프백이 아닌 IPv4 가 보이면 그 장면을 실패로 끝낸다.
이미지 OCR 이 아니라 DOM 텍스트라 결정적이다. 값 자체는 출력하지 않는다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import ipaddress
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from playwright.sync_api import Page, sync_playwright

REPO = Path(__file__).resolve().parents[2]
HERE = REPO / "scripts" / "manual"
OUT = REPO / "src" / "static" / "manual" / "img"
FORMS = HERE / "fixtures" / "forms"
ACCOUNTS = {
    "user": ("demo_user", "Manual-User-2026"),
    "admin": ("admin", "Manual-Admin-2026"),
}

_FREEZE_CSS = """
*, *::before, *::after { animation: none !important; transition: none !important; caret-color: transparent !important; }
"""

# 번호 콜아웃: 대상 요소 둘레에 점선 테두리 + 좌상단 번호 배지. 문서 좌표(absolute)로 붙인다.
_CALLOUT_JS = """
([n, sel]) => {
  const el = document.querySelector(sel);
  if (!el) return false;
  // 보이는 부분만 — 스크롤 컨테이너(대화 영역·패널)가 자른 바깥까지 테두리를 그리지 않는다
  let r = el.getBoundingClientRect();
  let L = r.left, T = r.top, R = r.right, B = r.bottom;
  for (let p = el.parentElement; p; p = p.parentElement) {
    const ov = getComputedStyle(p).overflowY + getComputedStyle(p).overflowX;
    if (/auto|scroll|hidden/.test(ov)) {
      const q = p.getBoundingClientRect();
      L = Math.max(L, q.left); T = Math.max(T, q.top); R = Math.min(R, q.right); B = Math.min(B, q.bottom);
    }
  }
  L = Math.max(L, 0); T = Math.max(T, 0); R = Math.min(R, innerWidth); B = Math.min(B, innerHeight);
  if (R - L < 4 || B - T < 4) return false;
  r = {left: L, top: T, width: R - L, height: B - T};
  const sx = window.scrollX, sy = window.scrollY;
  const box = document.createElement('div');
  box.className = '__manual-callout';
  Object.assign(box.style, {position:'absolute', left:(r.left+sx-3)+'px', top:(r.top+sy-3)+'px',
    width:(r.width+6)+'px', height:(r.height+6)+'px', border:'2px dashed #ea580c', borderRadius:'8px',
    zIndex:2147483646, pointerEvents:'none', boxSizing:'border-box'});
  const tag = document.createElement('div');
  tag.className = '__manual-callout';
  tag.textContent = String(n);
  const tx = Math.max(2, r.left+sx-13), ty = Math.max(2, r.top+sy-13);
  Object.assign(tag.style, {position:'absolute', left:tx+'px', top:ty+'px', width:'24px', height:'24px',
    borderRadius:'50%', background:'#ea580c', color:'#fff', font:'700 13px/24px Pretendard, sans-serif',
    textAlign:'center', boxShadow:'0 0 0 2px #fff, 0 2px 6px rgba(0,0,0,.35)', zIndex:2147483647,
    pointerEvents:'none'});
  document.body.appendChild(box); document.body.appendChild(tag);
  return true;
}
"""


# ── 데이터 위생(§4.7) ─────────────────────────────────────────────────────

_SECRET_KEY = re.compile(r"KEY|SECRET|PASSWORD|PASSWD|TOKEN|CREDENTIAL", re.I)
_HOST_KEY = re.compile(r"HOST|URL|DSN|ENDPOINT|ADDR|URI", re.I)
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_SETTINGS_SCENES = re.compile(r"^(settings-|dbconfig)")


def _is_local(host: str) -> bool:
    host = host.strip("[]").lower()
    if host in ("localhost", ""):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_unspecified


def _denylist() -> list[str]:
    """루트 ``.env``(캡처 스냅샷이 아닌 운영 설정)에서 화면에 나오면 안 되는 값을 모은다."""
    path = REPO / ".env"
    if not path.exists():
        return []
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, val = line.split("=", 1)
        val = val.strip().strip("'\"")
        if not val:
            continue
        if _SECRET_KEY.search(key) and len(val) >= 8:
            out.add(val)
        if _HOST_KEY.search(key):
            for part in re.split(r"[,\s\[\]\"]+", val):
                if "://" in part:
                    host = urlsplit(part).hostname
                elif re.search(r"HOST|ADDR", key, re.I) or _IPV4.fullmatch(part.split(":")[0]):
                    host = part.split(":")[0]
                else:  # 스킴 없는 URL 계열 값은 파일 경로(예: 체크포인트 sqlite 파일)다
                    host = None
                if host and ("." in host) and not _is_local(host):
                    out.add(host)
    return sorted(out)


def _hygiene(page: Page, cap_id: str, deny: list[str]) -> None:
    text = page.evaluate("document.body ? document.body.innerText : ''")
    text += " " + " ".join(
        page.evaluate("Array.from(document.querySelectorAll('input,textarea')).map(e => e.value)")
    )
    hits = sum(1 for v in deny if v in text)
    if hits:
        raise RuntimeError(
            f"{cap_id}: 화면에 운영 설정(루트 .env) 값 {hits}종이 보인다 — 값은 출력하지 않는다"
        )
    if _SETTINGS_SCENES.match(cap_id):
        bad = sorted({ip for ip in _IPV4.findall(text) if not _is_local(ip)})
        if bad:
            raise RuntimeError(
                f"{cap_id}: 설정 화면에 루프백이 아닌 IP {len(bad)}건 — 캡처 스냅샷 .env 확인"
            )


def _assert_step(page: Page, kind: str, sel: str) -> None:
    """화면 상태 단언(캡처 이미지는 바꾸지 않는다).

    역할별 링크 노출 같은 수용 기준을 캡처 중에 확인한다.

    판정은 요소 **자신의** ``display`` 다 — 닫힌 메뉴(<details>) 안의 링크도
    메뉴를 열지 않고 판정한다."""
    display = page.evaluate(
        "s => { const e = document.querySelector(s);"
        " return e ? getComputedStyle(e).display : null; }",
        sel,
    )
    if display is None:
        raise AssertionError(f"{kind} 실패 — 요소 없음 {sel}")
    shown = display != "none"
    if (kind == "assert_visible") != shown:
        raise AssertionError(f"{kind} 실패 — {sel} display={display}")


def _chrome() -> str:
    hits = glob.glob(
        str(
            Path.home()
            / "Library/Caches/ms-playwright/chromium-*/chrome-mac*/*.app/Contents/MacOS/*"
        )
    )
    hits += glob.glob(str(Path.home() / ".cache/ms-playwright/chromium-*/chrome-linux*/chrome"))
    if not hits:
        sys.exit(
            "playwright 캐시에 Chrome 이 없다 — 다운로드하지 않는다. `playwright install chromium` 은 사람이 판단할 일이다"
        )
    return sorted(hits)[-1]


def _login(page: Page, base: str, who: str) -> None:
    user, pw = ACCOUNTS[who]
    page.goto(f"{base}/login")
    page.fill("#userId", user)
    page.fill("#password", pw)
    page.click("#loginBtn")
    page.wait_for_url(f"{base}/", timeout=15000)
    page.wait_for_load_state("networkidle")


def _wait_answer(page: Page, timeout: int = 60000) -> None:
    """응답이 끝날 때까지 — 전송 버튼이 정지(■) 상태에서 돌아오면 끝이다."""
    try:
        page.wait_for_selector("#sendBtn.input-btn--stop", timeout=3000)
    except Exception:
        pass
    page.wait_for_selector("#sendBtn:not(.input-btn--stop)", timeout=timeout)
    page.wait_for_timeout(400)


def _alarm_replay(opt: dict) -> None:
    """scripts/manual/alarms.py 의 replay 와 같은 일 — 앱 코드를 import 하지 않으려고 여기 둔다."""
    import redis

    env = dict(
        ln.split("=", 1)
        for ln in (REPO / "build/manual_capture/app-capture/.env")
        .read_text(encoding="utf-8")
        .splitlines()
        if "=" in ln
    )
    r = redis.Redis(host=env["REDIS_HOST"], port=int(env["REDIS_PORT"]), db=int(env["REDIS_DB"]))
    src = HERE / "fixtures" / "alarms"
    for name, ch in (
        ("incident.jsonl", env["NOISE_INCIDENT_EVENT_CHANNEL"]),
        ("sse.jsonl", env["NOISE_SSE_BRIDGE_CHANNEL"]),
    ):
        if opt.get("only") and name.split(".")[0] != opt["only"]:
            continue
        path = src / name
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r.publish(ch, line)
                    time.sleep(0.3)


def _step(page: Page, base: str, step: dict) -> None:
    ((kind, arg),) = step.items()
    if kind == "goto":
        page.goto(base + arg)
        page.wait_for_load_state("networkidle")
    elif kind == "click":
        page.click(arg)
    elif kind == "dblclick":
        page.dblclick(arg)
    elif kind == "hover":
        page.hover(arg)
    elif kind == "fill":
        page.fill(arg[0], arg[1])
    elif kind == "press":
        page.keyboard.press(arg)
    elif kind == "check":
        page.check(arg)
    elif kind == "select":
        page.select_option(arg[0], arg[1])
    elif kind == "upload":
        page.set_input_files(arg[0], str(FORMS / arg[1]))
    elif kind == "send":  # 질문을 보내고 응답이 끝날 때까지 기다린다
        page.fill("#prompt", arg)
        page.click("#sendBtn")
        _wait_answer(page)
    elif kind == "send_nowait":  # 스트리밍 중간 장면용
        page.fill("#prompt", arg)
        page.click("#sendBtn")
    elif kind == "wait_answer":
        _wait_answer(page)
    elif kind == "wait":
        if isinstance(arg, int):
            page.wait_for_timeout(arg)
        else:
            page.wait_for_selector(arg, timeout=20000)
    elif kind == "scroll":
        page.locator(arg).first.scroll_into_view_if_needed()
    elif kind == "scroll_top":
        page.evaluate(f"document.querySelector({arg!r}).scrollTop = 0")
    elif kind == "scroll_bottom":
        page.evaluate(
            f"(() => {{ const e = document.querySelector({arg!r}); e.scrollTop = e.scrollHeight; }})()"
        )
    elif kind == "eval":
        page.evaluate(arg)
    elif kind in ("assert_visible", "assert_hidden"):
        _assert_step(page, kind, arg)
    elif kind == "alarm_replay":  # 녹화한 알람 이벤트를 캡처 서버가 구독하는 채널로 다시 발행
        _alarm_replay(arg or {})
    else:
        raise ValueError(f"알 수 없는 단계: {kind}")


def shoot(browser, base: str, cap: dict, deny: list[str]) -> Path:
    ctx = browser.new_context(
        viewport={"width": cap.get("width", 1440), "height": cap.get("height", 900)},
        device_scale_factor=1,
        locale="ko-KR",
        timezone_id="Asia/Seoul",
        color_scheme="light",
    )
    page = ctx.new_page()
    try:
        if cap.get("theme") == "dark":
            page.add_init_script("try{localStorage.setItem('ui.theme.personal','dark')}catch(e){}")
        else:
            page.add_init_script("try{localStorage.setItem('ui.theme.personal','light')}catch(e){}")
        if cap.get("login"):
            _login(page, base, cap["login"])
        for n, step in enumerate(cap.get("steps") or [], 1):
            try:
                _step(page, base, step)
            except Exception as e:
                dbg = REPO / "build" / "manual_capture" / "tmp" / f"{cap['id']}-fail.png"
                dbg.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(dbg))
                raise RuntimeError(
                    f"{n}번째 단계 {step} 실패({type(e).__name__}) — 화면 {dbg.name}"
                ) from e
        _hygiene(page, cap["id"], deny)
        page.add_style_tag(content=_FREEZE_CSS)
        page.wait_for_timeout(300)
        for n, sel in cap.get("callouts") or []:
            if not page.evaluate(_CALLOUT_JS, [n, sel]):
                raise RuntimeError(f"{cap['id']}: 콜아웃 {n} 대상이 없거나 화면 밖 — {sel}")
        OUT.mkdir(parents=True, exist_ok=True)
        path = OUT / f"{cap['id']}.png"
        clip = cap.get("clip")
        if clip:
            loc = page.locator(clip).first
            box = loc.bounding_box()
            if not box:
                raise RuntimeError(f"{cap['id']}: 잘라낼 영역 없음 — {clip}")
            pad = cap.get("pad", 16)
            vw = page.viewport_size
            x, y = max(0, box["x"] - pad), max(0, box["y"] - pad)
            w = min(vw["width"] - x, box["width"] + 2 * pad)
            h = min(vw["height"] - y, box["height"] + 2 * pad)
            page.screenshot(path=str(path), clip={"x": x, "y": y, "width": w, "height": h})
        else:
            page.screenshot(path=str(path), full_page=False)
        return path
    finally:
        ctx.close()


def _stamp() -> None:
    """마지막 캡처 일자·기준 커밋 — build.py 가 판 정보(§4.2)에 싣는다."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:
        commit = "unknown"
    (OUT / "captured.json").write_text(
        json.dumps({"date": dt.date.today().isoformat(), "commit": commit}) + "\n", encoding="utf-8"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--base", default="http://127.0.0.1:18981")
    ap.add_argument(
        "--server",
        default="capture",
        help="이 서버 프로필에 속한 장면만 찍는다(captures.yaml server)",
    )
    a = ap.parse_args()
    caps = yaml.safe_load((HERE / "captures.yaml").read_text(encoding="utf-8"))["captures"]
    failed, shot = [], 0
    deny = _denylist()
    print(f"위생 검사: 루트 .env 금지값 {len(deny)}종")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=_chrome())
        for cap in caps:
            if a.only and cap["id"] not in a.only:
                continue
            if cap.get("server", "capture") != a.server:
                continue
            try:
                path = shoot(browser, a.base, cap, deny)
                shot += 1
                print(f"OK   {cap['id']:24s} {path.stat().st_size // 1024} KB")
            except Exception as e:  # 한 장 실패가 나머지를 막지 않게 — 끝에 실패로 종료한다
                failed.append(cap["id"])
                print(f"FAIL {cap['id']:24s} {type(e).__name__}: {str(e).splitlines()[0][:200]}")
        browser.close()
    if shot:
        _stamp()
    if failed:
        sys.exit(f"실패 {len(failed)}건: {failed}")


if __name__ == "__main__":
    main()
