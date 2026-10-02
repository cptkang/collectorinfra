"""시스템 소개 페이지 대체 영상 캡처 (plans/124 · D-277 ⑤ 개정).

WebGL 을 못 쓰는 PC(GPU 없는 VDI 등)는 실시간 3D 대신 장면별 반복 영상을 본다. 이 스크립트가 그 영상을 만든다.
개발 맥에서만 돌린다 — 헤드리스 크롬에 가상 시계를 주입해 실제 3D 장면을 한 프레임씩 그려 받고,
ffmpeg 로 끝과 처음을 겹쳐 이음매 없는 VP9 webm 을 만든다. 요청은 127.0.0.1 로만 나간다.

    python scripts/intro_capture.py                       # 9개 장면 전부 → src/static/intro/video/
    python scripts/intro_capture.py --stills /tmp/intro   # 대표 프레임만 PNG 로(구도 확인)
    python scripts/intro_capture.py --scenes 3 --at 3=0.4 # 한 장면의 진행률을 바꿔 다시 뜬다

장면 구도(`SCENES`)나 3D 코드(`src/static/intro/*.js`)를 바꾸면 다시 돌려 영상을 갱신한다.
준비물: Google Chrome · ffmpeg(libvpx-vp9 — `brew install ffmpeg`).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import functools
import http.server
import json
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import websockets

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "src" / "static" / "intro" / "video"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# 장면 번호(data-scene) → 대표 진행률. 3D 피사체가 다 드러나고 글자 패널과 겹치지 않는 지점.
# (시작, 끝, 초)는 반복 한 바퀴 동안 진행률 구간을 훑는다 — 기능 링(5)은 글자 카드가 스크롤로 기능을 바꾸므로
# 한 기능에 멈춰 있으면 카드와 어긋난다.
SCENES: dict[int, float | tuple[float, float, float]] = {
    0: 0.86, 1: 0.5, 2: 0.88, 3: 0.52, 4: 0.44, 5: (0.0, 0.94, 14.0), 6: 0.5, 7: 0.74, 8: 1.0,
}
# 영상에서 흐리게 할 장면(가우스 sigma) — 정지 등급의 글자 카드와 겹치는 3D 카드 글자가 읽히지 않게.
# 재생 중 CSS filter 로 흐리면 GPU 없는 PC에서 CPU 를 먹으므로 인코딩할 때 굽는다.
BLUR = {5: 14}

# 페이지 스크립트보다 먼저 돈다 — 시간과 프레임을 이 스크립트가 쥔다.
CLOCK = r"""
(() => {
  let now = 0;
  const queue = [];
  Object.defineProperty(performance, 'now', { value: () => now, configurable: true });
  window.requestAnimationFrame = (cb) => { queue.push(cb); return queue.length; };
  window.__cap = {
    step(ms, n = 1) {
      for (let i = 0; i < n; i++) { now += ms; queue.splice(0).forEach((cb) => cb(now)); }
    },
    seek(i, p) {
      const el = document.querySelector(`[data-scene="${i}"]`);
      const vh = innerHeight;
      const top = el.getBoundingClientRect().top + scrollY;
      const span = el.offsetHeight - vh;
      const y = el.classList.contains('scene') && span > 1 ? top + p * span : top - vh * (1 - p);
      scrollTo({ top: Math.round(y), behavior: 'instant' });
    },
    frame(type, q) { return document.querySelector('#gl').toDataURL(type, q); },
  };
})();
"""


class Cdp:
    """크롬 DevTools 프로토콜 최소 클라이언트 — 요청 하나에 응답 하나를 기다린다."""

    def __init__(self, ws: websockets.ClientConnection) -> None:
        self.ws = ws
        self.n = 0

    async def send(self, method: str, **params: object) -> dict:
        self.n += 1
        mid = self.n
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def js(self, expr: str) -> object:
        r = await self.send("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"].get("exception", r["exceptionDetails"]))
        return r["result"].get("value")


def serve_src() -> http.server.ThreadingHTTPServer:
    """`src/` 를 루트로 서빙한다 — 페이지가 `/static/...` 절대 경로를 쓴다."""

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(REPO / "src")))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def launch_chrome(user_dir: str, w: int, h: int) -> tuple[subprocess.Popen, str]:
    proc = subprocess.Popen(
        [
            CHROME, "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={user_dir}",
            "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--hide-scrollbars", "--mute-audio",
            "--no-first-run", "--no-default-browser-check", f"--window-size={w},{h}", "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    port_file = Path(user_dir) / "DevToolsActivePort"
    for _ in range(100):
        if port_file.exists() and port_file.read_text().strip():
            break
        time.sleep(0.1)
    port = port_file.read_text().split()[0]
    targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list"))
    page = next(t for t in targets if t["type"] == "page")
    return proc, page["webSocketDebuggerUrl"]


def encode(frames: Path, n_loop: int, n_blend: int, fps: int, crf: int, blur: int, out: Path) -> None:
    """끝 n_blend 프레임을 처음 n_blend 프레임 위로 서서히 겹쳐 이음매 없는 반복 영상을 만든다."""
    d = n_blend / fps
    graph = (
        f"[0:v]split[a][b];[a]trim=start_frame={n_blend},setpts=PTS-STARTPTS[m];"
        f"[b]trim=end_frame={n_blend},setpts=PTS-STARTPTS[h];"
        f"[m][h]xfade=transition=fade:duration={d}:offset={(n_loop - n_blend) / fps}"
        f"{f',gblur=sigma={blur}' if blur else ''},format=yuv420p[v]"
    )
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", "-framerate", str(fps), "-i", str(frames / "f%04d.jpg"),
            "-filter_complex", graph, "-map", "[v]", "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", str(crf),
            "-row-mt", "1", "-deadline", "good", "-cpu-used", "2", "-g", str(n_loop), "-an", str(out),
        ],
        check=True,
    )


async def capture(args: argparse.Namespace, scenes: dict[int, float | tuple[float, float, float]]) -> None:
    w, h = args.size
    ms = 1000 / args.fps
    n_blend = round(args.blend * args.fps)
    srv = serve_src()
    user_dir = tempfile.mkdtemp(prefix="intro_cap_")
    proc, ws_url = launch_chrome(user_dir, w, h)
    try:
        async with websockets.connect(ws_url, max_size=None) as ws:
            cdp = Cdp(ws)
            await cdp.send("Page.enable")
            await cdp.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)
            await cdp.send("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "no-preference"}])
            await cdp.send("Page.addScriptToEvaluateOnNewDocument", source=CLOCK)
            await cdp.send("Page.navigate", url=f"http://127.0.0.1:{srv.server_port}/static/intro.html?quality=high")

            # 3D 모듈 import · 폰트 대기(실시간 최대 1.8초)가 끝나 첫 프레임을 그릴 때까지 한 칸씩 민다
            deadline = time.monotonic() + 60
            while not await cdp.js(f"!!window.__cap && (__cap.step({ms}), document.querySelector('#gl').classList.contains('on'))"):
                if time.monotonic() > deadline:
                    raise RuntimeError("3D 첫 프레임을 받지 못했다 — 콘솔 오류를 확인할 것")
                await asyncio.sleep(0.2)

            for i, spec in scenes.items():
                p, p_end, secs = spec if isinstance(spec, tuple) else (spec, spec, args.seconds)
                n_loop = round(secs * args.fps)
                await cdp.js(f"__cap.seek({i}, {p})")
                await cdp.js(f"__cap.step({ms}, {args.settle * args.fps})")  # 카메라가 자리 잡을 때까지
                if args.stills:
                    url = await cdp.js(f"(__cap.step({ms}), __cap.frame('image/png'))")
                    path = args.stills / f"scene-{i}.png"
                    path.write_bytes(base64.b64decode(str(url).split(",", 1)[1]))
                    print(f"scene {i} (p={p}) → {path}")
                    continue
                frames = Path(tempfile.mkdtemp(prefix=f"intro_f{i}_"))
                t0 = time.monotonic()
                for k in range(n_loop + n_blend):
                    if p_end != p:
                        await cdp.js(f"__cap.seek({i}, {p + (p_end - p) * min(k, n_loop) / n_loop})")
                    url = await cdp.js(f"(__cap.step({ms}), __cap.frame('image/jpeg', 0.95))")
                    (frames / f"f{k:04d}.jpg").write_bytes(base64.b64decode(str(url).split(",", 1)[1]))
                out = args.out / f"scene-{i}.webm"
                encode(frames, n_loop, n_blend, args.fps, args.crf, BLUR.get(i, 0), out)
                shutil.rmtree(frames)
                print(f"scene {i} (p={p}) → {out.relative_to(REPO)} {out.stat().st_size // 1024}KB · {time.monotonic() - t0:.0f}s")
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        shutil.rmtree(user_dir, ignore_errors=True)
        srv.shutdown()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenes", type=int, nargs="*", help="장면 번호(기본: 전부)")
    ap.add_argument("--at", nargs="*", default=[], metavar="I=P", help="장면 진행률 덮어쓰기(예: 3=0.4)")
    ap.add_argument("--stills", type=Path, help="영상 대신 대표 프레임 PNG 를 이 폴더에 쓴다")
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    ap.add_argument("--size", type=lambda s: tuple(map(int, s.split("x"))), default=(1280, 720))
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--seconds", type=float, default=6.0, help="반복 한 바퀴 길이")
    ap.add_argument("--blend", type=float, default=1.0, help="이음매를 겹치는 길이(초)")
    ap.add_argument("--settle", type=int, default=5, help="장면 이동 후 카메라 안정 대기(초)")
    ap.add_argument("--crf", type=int, default=38, help="VP9 품질(낮을수록 고화질·큰 파일)")
    args = ap.parse_args()

    scenes = {i: SCENES[i] for i in (args.scenes if args.scenes else SCENES)}
    for kv in args.at:
        i, p = kv.split("=")
        scenes[int(i)] = float(p)
    (args.stills or args.out).mkdir(parents=True, exist_ok=True)
    asyncio.run(capture(args, scenes))


if __name__ == "__main__":
    main()
