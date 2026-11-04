"""알람 분석 워커 독립 프로세스 진입점 (plans/208 W5 · C-7 · D-359 부기 1 ②).

기동 방법:
    python -m noise_gate.worker   (레포 루트에서 — `.env`·`.encenv`는 CWD 기준으로 읽는다)

본체(`python -m src.main --server`)를 `ALARM_WORKER_IN_PROCESS=false`로 띄울 때 짝으로 쓴다.
설정은 본체와 같은 `AppConfig`다. 본체 기동부(`src/api/server.py` `_start_alarm_worker`)와 같은
`AlarmWorker(config).run()`을 이 프로세스의 이벤트 루프에서 돌려, 워커가 API와 다른 이벤트
루프·코어를 쓴다.

- SIGTERM·SIGINT를 받으면 워커 태스크를 취소한다 — 새로 읽지 않고 진행 중을 마무리한다(N≥2는
  W3 종료 드레인). `ALARM_WORKER_DRAIN_TIMEOUT_SECONDS` + `SHUTDOWN_GRACE_SECONDS` 안에 끝나지
  않으면 남은 처리를 다시 취소하고, 그 뒤에도 `SHUTDOWN_GRACE_SECONDS` 안에 끝나지 않으면 워커를
  두고 끝낸다(종료 코드 1).
- 종료 코드: 정상 0 · 워커 예외·정지 상한 초과 1 · 설정 로드 실패 2(ERROR 1줄) · 단일 인스턴스 잠금
  상실 3 · 잠금 획득 실패 4 — 잠금 때문에 멈춘 것을 정상 정지와 구분해 서비스 관리자의 실패 시
  재기동(`Restart=on-failure` 등)이 걸리게 한다.
- `ALARM_ENABLED=false`면 워커를 띄우지 않고 바로 끝낸다(종료 코드 0).
- 본체 기동부가 담당 강조(plans/201 W3) 기동에서 주입하는 프로세스 종류 판정 콜백은 주입하지
  않는다 — 판정 모듈이 pydantic·yaml을 써서 `noise_gate → src` 허용 범위(D-139 부기) 밖이다
  (분리 모드 제약).
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from typing import Any

from src.config import load_config
from src.utils.log_masking import install_log_record_factory

from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.infrastructure.worker_lock import OUTCOME_LOST, OUTCOME_NOT_ACQUIRED

logger = logging.getLogger("noise_gate.worker")

#: 종료 신호 뒤 드레인 시간에 더해 기다리는 여유(초) — 넘으면 남은 처리를 다시 취소하고, 재취소
#: 뒤에도 이 시간만 더 기다린다.
SHUTDOWN_GRACE_SECONDS = 5.0

#: `run_worker` 결과 — 재취소 뒤에도 워커가 끝나지 않아 두고 나왔다.
STOP_HUNG = "hung"

#: 종료 코드 — 정상 0 · 예외·정지 상한 초과 1 · 설정 로드 실패 2 · 잠금 상실 3 · 잠금 획득 실패 4.
EXIT_CODES: dict[str | None, int] = {
    None: 0,
    STOP_HUNG: 1,
    OUTCOME_LOST: 3,
    OUTCOME_NOT_ACQUIRED: 4,
}

_LOG_FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"


async def run_worker(config: Any, *, stop: asyncio.Event | None = None) -> str | None:
    """워커를 돌리다가 종료 신호(또는 `stop`)를 받으면 드레인 상한 안에서 멈춘다.

    Args:
        config: AppConfig 인스턴스.
        stop: 종료 요청 이벤트(테스트 seam). 없으면 새로 만들고 SIGTERM·SIGINT를 건다.

    Returns:
        멈춘 사유 — 잠금 상실·획득 실패면 워커의 `lock_outcome`, 정지 상한을 넘겨 워커를 두고
        나왔으면 `STOP_HUNG`, 그 밖에는 None. 워커 예외는 그대로 올린다.
    """
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
            installed.append(sig)
        except (NotImplementedError, RuntimeError, ValueError):  # Windows·메인 스레드 밖
            pass

    worker = AlarmWorker(config)
    task = asyncio.create_task(worker.run(), name="alarm-worker")
    stopper = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait({task, stopper}, return_when=asyncio.FIRST_COMPLETED)
        if not task.done():
            drain = float(getattr(config.alarm, "worker_drain_timeout_seconds", 30.0) or 0.0)
            limit = max(0.0, drain) + SHUTDOWN_GRACE_SECONDS
            logger.info("종료 신호 — 알람 워커를 멈춘다(새로 읽지 않음 · 최대 %.1f초)", limit)
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=limit)
            if not done:
                logger.warning("알람 워커가 %.1f초 안에 끝나지 않아 남은 처리를 취소한다", limit)
                task.cancel()
                done, _ = await asyncio.wait({task}, timeout=SHUTDOWN_GRACE_SECONDS)
                if not done:
                    logger.warning(
                        "알람 워커가 재취소 뒤에도 %.1f초 안에 끝나지 않아 기다리지 않고 끝낸다",
                        SHUTDOWN_GRACE_SECONDS,
                    )
                    return STOP_HUNG
    finally:
        stopper.cancel()
        for sig in installed:
            loop.remove_signal_handler(sig)
    if not task.cancelled():
        task.result()  # 워커 예외는 그대로 올린다
    return getattr(worker, "lock_outcome", None)


def _run_until_stopped(config: Any) -> str | None:
    """`asyncio.run(run_worker(config))`과 같되, 두고 나온 워커(`STOP_HUNG`)는 기다리지 않는다.

    `asyncio.run`은 끝날 때 남은 태스크를 취소하고 끝날 때까지 기다리므로, 취소를 받고도 끝나지
    않는 워커가 있으면 거기서 다시 멈춘다. 그 경우만 루프를 정리하지 않고 돌아온다(곧 프로세스가
    끝난다).
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    outcome: str | None = None
    try:
        outcome = loop.run_until_complete(run_worker(config))
        return outcome
    finally:
        asyncio.set_event_loop(None)
        if outcome != STOP_HUNG:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.run_until_complete(loop.shutdown_default_executor())
            finally:
                loop.close()


def main() -> int:
    """알람 워커 프로세스 메인 진입점 — 종료 코드를 돌려준다."""
    install_log_record_factory()  # 로그 비밀값 가림(plans/167)
    try:
        config = load_config()
    except Exception as exc:
        logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT)
        logger.error(
            "알람 워커 설정 로드 실패 — 레포 루트에서 실행했는지와 .env·.encenv 값을 확인하라: %s",
            exc,
        )
        return 2
    logging.basicConfig(level=config.log_level, format=_LOG_FORMAT)
    if not config.alarm.enabled:
        logger.info("ALARM_ENABLED=false — 알람 워커를 띄우지 않고 끝낸다")
        return 0
    if config.alarm.worker_in_process and not config.alarm.worker_singleton_guard:
        logger.warning(
            "ALARM_WORKER_IN_PROCESS=true — 본체도 같은 소비자 그룹으로 워커를 띄운다. "
            "본체를 ALARM_WORKER_IN_PROCESS=false로 띄우거나 "
            "ALARM_WORKER_SINGLETON_GUARD=true로 둘 중 하나만 소비하게 하라"
        )
    logger.info("알람 워커 프로세스 시작 (stream=%s)", config.alarm.redis_stream_key)
    try:
        outcome = _run_until_stopped(config)
    except Exception:
        logger.exception("알람 워커 비정상 종료")
        return 1
    code = EXIT_CODES.get(outcome, 1)
    if code:
        logger.error("알람 워커 프로세스 종료 (사유=%s · 종료 코드 %d)", outcome, code)
    else:
        logger.info("알람 워커 프로세스 종료")
    return code


if __name__ == "__main__":
    sys.exit(main())
