"""큰 응답 파싱·자격증명 검사 프로세스 풀 (plans/205 W1 · D-356 ②).

응답 파싱과 `scrub_detail`은 GIL을 쥐는 순수 파이썬이라 스레드로 넘겨도 코어 1개다. 임계 이상
본문은 이 풀의 워커 프로세스가 파싱과 자격증명 검사를 **함께** 하고 가린 결과만 돌려준다 — 원본
객체는 부모로 오지 않는다(D-296 ③ 경계 불변).

- 풀은 프로세스당 1개(소스별 클라이언트가 공유)이고 첫 사용 때 만든다(큰 응답이 없으면 0개).
- 시작 방식은 spawn — 실행 중 이벤트 루프·스레드가 있는 프로세스의 fork를 피하고 맥·리눅스 동작을
  같게 한다. 워커 import 표면은 `domain.credentials`·`adapters.json_stream`(표준 라이브러리
  전용)뿐이다 — 설정·클라이언트·서버 모듈을 끌어오지 않는다.
- 풀 장애(워커 비정상 종료 · 제출 실패)는 WARNING(예외 유형·바이트 수 — 본문 없음) 뒤 풀을 버리고
  그 호출만 같은 함수를 스레드에서 돌린다(침묵 폴백 금지). 다음 호출은 새 풀을 만든다. 버린 풀에
  줄 서 있던 다른 호출은 취소하지 않는다(그 풀에서 끝나거나 깨짐을 받아 각자 폴백).
- 임시 파일 삭제는 호출자(부모)가 한다 — 워커는 읽기만 한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import multiprocessing
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Any

from apm_gateway.adapters.json_stream import load_json_file
from apm_gateway.domain.credentials import scrub_detail, scrub_text

logger = logging.getLogger(__name__)


def parse_scrub_json(
    text: str | None, path: str | None, encoding: str
) -> tuple[Any, list[str], set[str]]:
    """본문(메모리 `text` 또는 임시 파일 `path`)을 파싱해 자격증명을 가린 (값, 메모, 가린 칸)을
    돌려준다. 형식 오류는 `ValueError` · 중첩 과다는 `RecursionError`(호출자가 `ApmError`로 바꾼다).
    JSON 디코드 오류는 원문(`doc`)을 싣지 않는 `ValueError`로 바꿔 보낸다 — 원문이 부모로 오지
    않게."""
    try:
        if path is not None:
            parsed = load_json_file(Path(path), encoding)
        else:
            parsed = json.loads(text) if text else None
    except json.JSONDecodeError as e:
        raise ValueError(str(e)) from None
    return scrub_detail(parsed)


def scrub_text_worker(text: str) -> str:
    """텍스트 본문의 자격증명을 가린다(워커 프로세스용 최상위 함수)."""
    return scrub_text(text)


class ParsePool:
    """spawn 프로세스 풀 — 첫 사용 때 만든다. `min_bytes` 이상 본문만 여기로 보낸다(판정은
    호출자)."""

    def __init__(self, workers: int, min_bytes: int) -> None:
        self.workers = workers
        self.min_bytes = min_bytes
        self._executor: ProcessPoolExecutor | None = None

    def _pool(self) -> ProcessPoolExecutor:
        if self._executor is None:
            self._executor = ProcessPoolExecutor(
                max_workers=self.workers, mp_context=multiprocessing.get_context("spawn")
            )
        return self._executor

    def _discard(self, executor: ProcessPoolExecutor) -> None:
        """깨진 풀을 버린다 — 동시 호출이 이미 새로 만든 풀은 건드리지 않는다. 대기 작업은 취소하지
        않는다 — 그 풀에서 끝나거나, 풀이 깨졌으면 `BrokenProcessPool`로 와서 각자 폴백을 탄다
        (취소하면 그 호출이 폴백 없이 `CancelledError`로 끝난다)."""
        if self._executor is executor:
            self._executor = None
        executor.shutdown(wait=False, cancel_futures=False)

    async def _run(self, func: Callable[..., Any], *args: Any, size: int) -> Any:
        loop = asyncio.get_running_loop()
        executor = self._pool()
        try:
            future = loop.run_in_executor(executor, func, *args)
        except (BrokenProcessPool, RuntimeError, OSError) as e:  # 제출 실패(깨짐·종료·기동 실패)
            return await self._fallback(executor, e, func, args, size)
        try:
            return await future
        except BrokenProcessPool as e:  # 워커 비정상 종료 — 워커 안 예외(ValueError 등)는 그대로
            return await self._fallback(executor, e, func, args, size)

    async def _fallback(
        self,
        executor: ProcessPoolExecutor,
        error: BaseException,
        func: Callable[..., Any],
        args: tuple[Any, ...],
        size: int,
    ) -> Any:
        logger.warning(
            "apm 파싱 프로세스 풀 장애 — 이 호출은 스레드로 처리하고 풀을 다시 만든다:"
            " error=%s bytes=%d workers=%d",
            type(error).__name__,
            size,
            self.workers,
        )
        self._discard(executor)
        return await asyncio.to_thread(func, *args)

    async def json(
        self, text: str | None, path: str | None, encoding: str, size: int
    ) -> tuple[Any, list[str], set[str]]:
        """`parse_scrub_json`을 워커에서 돌린다. `size`는 장애 로그용 본문 바이트 수다."""
        result: tuple[Any, list[str], set[str]] = await self._run(
            parse_scrub_json, text, path, encoding, size=size
        )
        return result

    async def text(self, text: str, size: int) -> str:
        """`scrub_text_worker`를 워커에서 돌린다."""
        cleaned: str = await self._run(scrub_text_worker, text, size=size)
        return cleaned

    def shutdown(self) -> None:
        """풀을 닫는다(대기 작업 취소 · 진행 중 작업은 끝날 때까지 기다린다). 다시 쓰면 새로
        만든다."""
        executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
