"""진단용 서버 기동 — reload 없음 + 주기적 전(全) 스레드 스택 덤프.

무한대기(SSE 응답 정지) 원인 분류용 상시 계측이다. 운영 코드는 건드리지 않는다.

  - uvicorn을 reload=False로 기동한다(main.py의 reload=True 하드코딩 우회 —
    리로더 부모/워커 자식 2프로세스 구조를 없애 관측 대상을 단일화).
  - 별도 스레드가 30초마다 전 스레드 스택을 logs/stack_dump.log에 기록한다.
    py-spy가 없는 폐쇄망에서 표준 라이브러리만으로 같은 판정을 대신한다:
      * 행이 걸린 구간의 연속 덤프에서 MainThread 스택이 같은 함수에 고정
        → 이벤트 루프 동기 블록(그 함수가 범인)
      * MainThread가 selector/select 대기(유휴 스택)
        → 루프는 살아 있음 — 코루틴 await 대기 또는 이벤트 지속 유입 케이스

사용 (저장소 루트에서):
    python scripts/diag_server.py

주의: 덤프는 30초마다 무조건 쌓인다(일 ~30MB). 진단 종료 후 로그를 지울 것.
"""

from __future__ import annotations

import asyncio
import faulthandler
import sys
import threading
import time
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DUMP_PATH = REPO_ROOT / "logs" / "stack_dump.log"
TASK_DUMP_PATH = REPO_ROOT / "logs" / "task_dump.log"
DUMP_INTERVAL_SEC = 30


def _stack_dumper() -> None:
    """전 스레드 스택을 주기 기록한다. 이벤트 루프가 블록돼도 이 스레드는 돈다."""
    while True:
        time.sleep(DUMP_INTERVAL_SEC)
        try:
            frames = sys._current_frames()
            names = {t.ident: t.name for t in threading.enumerate()}
            with open(DUMP_PATH, "a", encoding="utf-8") as f:
                f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
                for tid, frame in frames.items():
                    f.write(f"--- thread {names.get(tid, '?')} (id={tid}) ---\n")
                    f.write("".join(traceback.format_stack(frame)))
        except Exception:  # 진단 장치가 서버를 죽이면 안 된다
            pass


def _awaitable_frames(obj, limit: int = 40) -> list:
    """코루틴/제너레이터의 await 사슬을 직접 걸어 프레임을 모은다(바깥→안쪽).

    Task.get_stack()은 중단된 코루틴에서 최상위 프레임 1개만 반환한다
    (f_back 워크 — 2026-09-04 실측 한계). cr_await/ag_await/gi_yieldfrom을
    따라가면 실제로 멈춰 있는 가장 안쪽 await까지 내려간다. async generator의
    asend 래퍼 등 사슬이 끊기는 지점에서는 거기까지만 나온다.
    """
    frames = []
    seen: set[int] = set()
    while obj is not None and id(obj) not in seen and len(frames) < limit:
        seen.add(id(obj))
        fr = (
            getattr(obj, "cr_frame", None)
            or getattr(obj, "ag_frame", None)
            or getattr(obj, "gi_frame", None)
        )
        if fr is not None:
            frames.append(fr)
        obj = (
            getattr(obj, "cr_await", None)
            or getattr(obj, "ag_await", None)
            or getattr(obj, "gi_yieldfrom", None)
        )
    return frames


def _fmt_frames(frames) -> list[str]:
    return [
        f'  File "{fr.f_code.co_filename}", line {fr.f_lineno}, in {fr.f_code.co_name}'
        for fr in frames
    ]


async def _task_dumper() -> None:
    """이벤트 루프 위에서 전 asyncio 태스크의 중단 지점을 주기 기록한다.

    스레드 스택 덤프는 await로 중단된 코루틴을 못 본다(2026-09-04 실측: 무한대기
    구간에 전 스레드 유휴). 태스크 사슬에 더해, asyncio 태스크로 잡히지 않는
    **중단된 async generator**(SSE event_generator 등)도 gc 스캔으로 찾아 기록한다
    — "yield에 정지(미들웨어 송신 막힘)인지 wait_for 안(가드 활성)인지"가
    무한대기 근본 원인 판정의 결정 증거다.
    """
    import gc
    import inspect

    me = asyncio.current_task()
    while True:
        await asyncio.sleep(DUMP_INTERVAL_SEC)
        try:
            lines = [f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} ====="]
            for task in asyncio.all_tasks():
                if task is me:
                    continue
                coro = task.get_coro()
                name = getattr(coro, "__qualname__", None) or repr(coro)[:80]
                lines.append(f"--- task {task.get_name()} [{name}] ---")
                lines.extend(_fmt_frames(_awaitable_frames(coro)))
            # 중단된 async generator (src/ 소속만) — 태스크 목록에 안 잡히는 사각지대
            for obj in gc.get_objects():
                try:
                    if not inspect.isasyncgen(obj):
                        continue
                    fr = getattr(obj, "ag_frame", None)
                    if fr is None:
                        continue
                    fname = fr.f_code.co_filename
                    if "src" not in fname and "noise_gate" not in fname:
                        continue
                    lines.append(f"--- task agen:{fr.f_code.co_name} ---")
                    lines.extend(_fmt_frames(_awaitable_frames(obj)))
                except Exception:
                    continue
            with open(TASK_DUMP_PATH, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception:  # 진단 장치가 서버를 죽이면 안 된다
            pass


class _TaskDumperASGIWrapper:
    """첫 ASGI 호출(기동 직후의 lifespan scope) 시점에 태스크 덤퍼를 띄운다.

    add_event_handler("startup", ...)는 앱이 FastAPI(lifespan=...) 방식이면
    무시된다(2026-09-04 실측 — 덤프 파일 미생성 원인). ASGI 호출은 반드시
    이벤트 루프 안에서 일어나므로 이 래퍼는 앱 내부 구조와 무관하게 동작한다.
    """

    def __init__(self, app) -> None:
        self._app = app
        self._started = False

    async def __call__(self, scope, receive, send) -> None:
        if not self._started:
            self._started = True
            asyncio.create_task(_task_dumper(), name="diag-task-dumper")
            print(f"[diag] 태스크 덤퍼 기동 → {TASK_DUMP_PATH}")
        await self._app(scope, receive, send)


def main() -> None:
    DUMP_PATH.parent.mkdir(parents=True, exist_ok=True)
    # 크래시(세그폴트 등) 시 스택 확보 — 부수 안전망
    faulthandler.enable()
    threading.Thread(target=_stack_dumper, daemon=True, name="diag-stack-dumper").start()

    from src.api.server import app
    from src.config import load_config

    config = load_config()
    print(
        f"[diag] reload=False 기동 · {DUMP_INTERVAL_SEC}s 주기 덤프 → "
        f"스레드 {DUMP_PATH.name} / 태스크 {TASK_DUMP_PATH.name}"
    )

    import uvicorn

    # reload=False이므로 임포트 문자열 대신 app 객체를 직접 넘겨도 된다.
    uvicorn.run(
        _TaskDumperASGIWrapper(app),
        host=config.server.host,
        port=config.server.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
