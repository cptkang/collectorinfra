"""호출 맥락 — 외부 API 호출 1회마다 어댑터가 읽는 우선순위·훅 (plans/134 W0-B N-14 ·
SPEC-apm-question-coverage §3.4).

- **우선순위**: `poller`(폴러) > `interactive`(승격 전 호출 · 기본) > `background`(승격된 작업).
  소스 클라이언트의 속도 제어가 대기열을 이 순서로 판다. 값은 호출 시점에 읽는다 — 작업이 승격되면
  이후 호출부터 `background`다(같은 객체를 바꾼다).
- **훅**: 어댑터가 HTTP를 보내기 직전 `before_call(source_id)`를 부른다. 작업은 이 자리에서 동시
  실행 슬롯을 기다리고(승격 뒤) 소스별 호출 수·임대 시각(`api_calls`·`updated_at`)을 올린다(감사의
  `api_calls`·`sources`가 작업별 값이 된다).
- **공유 적재**: 여러 요청이 함께 기다리는 적재(인스턴스 명단)는 `SharedLoadScope`로 돈다 —
  우선순위 `interactive` · 슬롯 대기 없음 · 호출 수와 메모는 적재를 시작한 요청에만 센다.
- **호출 계획 · 메모**: 도구가 아는 호출 수(`expect_calls`)는 진행·비용 예측에, 자격증명 검사의
  `[한계]` 메모(`note`)는 봉투 `limits`에, 자격증명 검사가 실제로 가린 칸 이름(`masked`)은 봉투
  고지 `disclosures`(`apm_masked_fields`)에 쓰인다.

맥락이 없으면(작업 밖 호출) 기본 맥락(`interactive` · 훅 없음 · 메모는 경고 로그)을 쓴다. 표준
라이브러리만 쓰는 순수 모듈이라 어댑터·애플리케이션 양쪽이 import한다.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterable, Iterator
from contextvars import ContextVar

logger = logging.getLogger(__name__)

PRIORITY_POLLER = "poller"
PRIORITY_INTERACTIVE = "interactive"
PRIORITY_BACKGROUND = "background"
# 작을수록 먼저 나간다(대기 시간이 길어지면 한 단계씩 올린다 — 에이징).
PRIORITY_RANK: dict[str, int] = {
    PRIORITY_POLLER: 0,
    PRIORITY_INTERACTIVE: 1,
    PRIORITY_BACKGROUND: 2,
}


class CallScope:
    """작업 밖 기본 맥락. 작업(`application/jobs.py`)이 이 클래스를 넓혀 훅을 채운다."""

    def __init__(self, priority: str = PRIORITY_INTERACTIVE) -> None:
        if priority not in PRIORITY_RANK:
            raise ValueError(f"미지 우선순위: {priority}")
        self.priority = priority

    async def before_call(self, source_id: str = "") -> None:
        """HTTP 1회 직전(허용목록 검사 뒤 · 속도 제어 앞)."""
        self.count_call(source_id)

    def count_call(self, source_id: str = "", *, shared: bool = False) -> None:
        """호출 1회를 센다(작업만 기록한다). `shared`는 이 요청이 시작한 공유 적재의 호출이다."""
        return None

    def expect_calls(self, count: int) -> None:
        """도구가 앞으로 부를 API 호출 수를 신고한다(누적)."""
        return None

    def note(self, text: str) -> None:
        """봉투 `limits`에 실을 메모. 작업 밖이면 경고 로그로만 남긴다(침묵 금지)."""
        logger.warning("apm 호출 메모(작업 밖): %s", text)

    def masked(self, fields: Iterable[str]) -> None:
        """자격증명 검사가 실제로 가린 칸 이름(값 없음) — 작업만 모아 봉투 고지로 싣는다."""
        return None


class SharedLoadScope(CallScope):
    """공유 적재의 맥락 — 시작한 요청의 우선순위·슬롯에 묶이지 않고 호출 수·메모만 그 요청에
    센다."""

    def __init__(self, origin: CallScope) -> None:
        super().__init__(PRIORITY_INTERACTIVE)
        self._origin = origin

    async def before_call(self, source_id: str = "") -> None:
        self._origin.count_call(source_id, shared=True)

    def note(self, text: str) -> None:
        self._origin.note(text)

    def masked(self, fields: Iterable[str]) -> None:
        self._origin.masked(fields)


_DEFAULT = CallScope()
_current: ContextVar[CallScope | None] = ContextVar("apm_call_scope", default=None)


def current_scope() -> CallScope:
    return _current.get() or _DEFAULT


def set_scope(scope: CallScope) -> None:
    """현재 태스크의 맥락을 바꾼다(태스크 맥락은 태스크마다 사본이라 다른 태스크에 새지 않는다)."""
    _current.set(scope)


@contextlib.contextmanager
def use_scope(scope: CallScope) -> Iterator[CallScope]:
    """블록 안에서만 맥락을 바꾼다(블록 안에서 만든 태스크는 이 맥락을 물려받는다)."""
    token = _current.set(scope)
    try:
        yield scope
    finally:
        _current.reset(token)


def expect_calls(count: int) -> None:
    """현재 맥락에 호출 계획을 더한다(작업 밖이면 아무것도 하지 않는다)."""
    if count > 0:
        current_scope().expect_calls(int(count))
