"""큰 응답 파싱·자격증명 검사 프로세스 풀 (plans/205 W1 · D-356 ② — 기본 on).

응답 파싱과 자격증명 검사(`scrub_detail`)는 순수 파이썬 객체 순회라 GIL을 쥔다 — 스레드로 넘겨도
코어는 1개다. 임계 이상 본문은 이 풀의 워커 프로세스가 **파싱과 검사를 함께** 하고 가린 결과만
돌려준다(D-296 ③ — 원본 객체를 부모로 보내지 않는다). 파싱 오류도 원문을 싣지 않은 예외로 바꿔
돌려준다(`JSONDecodeError`는 원문 전체를 pickle에 싣는다).

- 풀은 첫 사용 때 만든다(지연 생성). 시작 방식은 `spawn` — 실행 중 이벤트 루프·스레드가 있는
  프로세스의 fork를 피하고 맥·리눅스가 같게 동작한다.
- 워커가 import하는 모듈은 이 파일 · `adapters.json_stream` · `domain.credentials`(표준 라이브러리
  전용)뿐이다 — 서버·설정 모듈을 끌어오지 않는다.
- 풀이 깨지면(`BrokenProcessPool` — 워커 비정상 종료) WARNING을 남기고 풀을 버린 뒤(다음 호출이
  새로 만든다) 그 호출만 스레드에서 처리한다(침묵 폴백 금지).
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
from typing import Any, TypeVar

from apm_gateway.adapters.json_stream import load_json_file
from apm_gateway.domain.credentials import scrub_detail, scrub_text

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


def parse_scrub_json(
    text: str | None, path: str | None, encoding: str
) -> tuple[Any, list[str], set[str]]:
    """본문을 파싱해 자격증명을 가린 결과(`scrub_detail` 반환형)를 돌려준다(워커 진입점).

    `path`면 임시 파일을 점진 디코드하고, 아니면 `json.loads`한다. 오류는 원문 없는
    `RecursionError`(중첩 과다)·`ValueError`(형식 오류)로 바꿔 올린다.
    """
    try:
        if path is not None:
            parsed = load_json_file(Path(path), encoding)
        else:
            parsed = json.loads(text) if text else None
    except RecursionError:
        raise RecursionError("JSON 중첩 과다") from None
    except ValueError as e:
        raise ValueError(f"JSON 형식 오류({type(e).__name__})") from None
    return scrub_detail(parsed)


def scrub_text_worker(text: str) -> str:
    """텍스트 본문의 자격증명을 가린다(워커 진입점)."""
    return scrub_text(text)


class ParsePool:
    """프로세스당 1개 — 소스별 클라이언트가 공유한다. `min_bytes` 이상 본문만 이 풀로 보낸다."""

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
        # 동시에 깨짐을 본 호출이 이미 새로 만든 풀을 버리지 않게 같은 풀일 때만 버린다
        if self._executor is executor:
            self._executor = None
        executor.shutdown(wait=False, cancel_futures=True)

    async def _run(self, fn: Callable[..., _T], *args: Any) -> _T:
        executor = self._pool()
        try:
            return await asyncio.get_running_loop().run_in_executor(executor, fn, *args)
        except BrokenProcessPool as e:
            logger.warning(
                "apm 파싱 프로세스 풀 장애 — 풀을 다시 만들고 이 호출은 스레드로 처리한다: "
                "reason=%s fn=%s workers=%d",
                type(e).__name__,
                fn.__name__,
                self.workers,
            )
            self._discard(executor)
            return await asyncio.to_thread(fn, *args)

    async def json(
        self, text: str | None, path: Path | None, encoding: str
    ) -> tuple[Any, list[str], set[str]]:
        """메모리 본문(`text`) 또는 임시 파일(`path`)을 워커에서 파싱·검사한다(파일은 읽기만)."""
        return await self._run(
            parse_scrub_json, text, None if path is None else str(path), encoding
        )

    async def text(self, text: str) -> str:
        """텍스트 본문의 자격증명 검사를 워커에서 한다."""
        return await self._run(scrub_text_worker, text)

    def shutdown(self) -> None:
        """풀을 닫는다(대기 중 작업 취소 · 종료 지점에서 1회)."""
        executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)


def build_parse_pool(workers: int, min_bytes: int) -> ParsePool | None:
    """설정 값으로 풀을 만든다 — 워커 0이면 None(현행 스레드 경로와 비트 동일)."""
    return ParsePool(workers, min_bytes) if workers > 0 else None
