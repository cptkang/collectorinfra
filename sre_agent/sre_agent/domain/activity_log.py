"""조사 잡 실행 기록(activity) 링 버퍼 (plans/187 W13 · §5.6.3 ⓑ · D-341 부기 4 ㉘).

잡마다 하나 두는 메모리 버퍼다. 플레이북 스텝·도구 호출·소견 LLM·HolmesGPT 스트림이 진행하면서
이벤트를 덧붙이고, `sre_get_investigation(activity_since=n)`이 `seq > n`인 것만 돌려준다.

- **단조 seq**: 1부터 1씩 늘린다. 버린 이벤트의 seq는 다시 쓰지 않는다
  (커서가 겹치거나 빠지지 않는다).
- **상한**: 이벤트 수 · 발췌(`excerpt`) 1건 · AI 메모(`note`) 1건 · 한 줄(`label`) 1건 ·
  잡당 합계 바이트.
  넘으면 오래된 이벤트부터 버리고 `truncated=True`를 단다(발췌·메모·한 줄은 잘라 넣는다).
- **가림**: `label`·`excerpt`·`note`는 버퍼에 넣기 **전에** 주입한 `masker`를 거친다(G-36). domain은
  가림 규칙을 모른다 — 만드는 쪽(application)이 `infrastructure.activity_masking`을 꽂는다.
- **스레드 안전**: append·조회가 Lock 하나를 쓴다
  (플레이북 스텝 스레드·소견 스레드·HolmesGPT 스레드).
- **원시 출력 전체는 넣지 않는다**(D-324 ⑦과 같은 원칙) — 발췌만.

표준 라이브러리만 쓴다(domain 순수).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: 엔진 칩 어휘.
ENGINES: tuple[str, ...] = ("playbook", "holmes", "system")
#: 이벤트 종류 어휘(§5.6.3 ⓑ 표).
KINDS: tuple[str, ...] = (
    "queued", "started", "step_start", "step_end", "tool_start", "tool_end",
    "llm_start", "llm_end", "ai_note", "deep_start", "deep_end", "finalize",
)  # fmt: skip
#: 진행 단계 어휘(D-123 상태 어휘는 그대로 두고 `running` 안을 이것으로 나눈다).
PHASES: tuple[str, ...] = ("queued", "collecting", "narrating", "deep_dive", "finalizing")

#: 잘라 넣은 텍스트의 꼬리 표지.
CLIP_MARK = "…"
#: 가림 전 입력 상한 배수 — 가림은 입력 길이에 선형이지만 수 MB 원시 출력을 통째로 돌리지 않는다.
#: 상한의 4배(문자)까지만 가리고 다시 바이트 상한으로 자르므로, 앞자르기 경계(가림 규칙이 꼬리를
#: 못 볼 수 있는 자리)는 결과에 남지 않는다(가림으로 3배 넘게 줄어드는 입력만 예외 — 실측 32KB 가림
#: 약 10ms라 8KB로 줄였다).
_PRE_CLIP_FACTOR = 4


@dataclass(frozen=True)
class ActivityLimits:
    """실행 기록 상한 — [제안] 값(§5.6.3 ⓑ)."""

    max_events: int = 300
    max_excerpt_bytes: int = 2048
    max_note_bytes: int = 2048
    max_label_bytes: int = 512
    max_total_bytes: int = 256 * 1024


def clip_bytes(text: str, limit: int) -> str:
    """UTF-8 바이트 상한으로 자른다(글자 경계 보존 · 잘렸으면 꼬리 표지)."""
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    mark = CLIP_MARK.encode("utf-8")
    return raw[: max(0, limit - len(mark))].decode("utf-8", "ignore") + CLIP_MARK


def _event_size(event: dict) -> int:
    return len(json.dumps(event, ensure_ascii=False).encode("utf-8"))


class ActivityLog:
    """잡 1건의 실행 기록 링 버퍼 + 진행 단계(`phase`)·대기 순번(`queue_position`)."""

    def __init__(
        self,
        *,
        masker: Callable[[str], str],
        limits: ActivityLimits | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._mask = masker
        self.limits = limits or ActivityLimits()
        self._clock = clock
        self._lock = threading.Lock()
        self._events: deque[tuple[dict, int]] = deque()
        self._bytes = 0
        self._seq = 0
        self._truncated = False
        self._phase: str | None = None
        self._queue_position: int | None = None

    # ── 진행 단계 ──

    def set_phase(self, phase: str, queue_position: int | None = None) -> None:
        """진행 단계를 바꾼다. 대기 순번은 `queued`일 때만 남는다."""
        with self._lock:
            self._phase = phase
            self._queue_position = queue_position if phase == "queued" else None

    @property
    def phase(self) -> str | None:
        with self._lock:
            return self._phase

    @property
    def queue_position(self) -> int | None:
        with self._lock:
            return self._queue_position

    # ── 기록 ──

    def _text(self, value: object, limit: int) -> str:
        text = str(value)
        if len(text) > limit * _PRE_CLIP_FACTOR:
            text = text[: limit * _PRE_CLIP_FACTOR]
        return clip_bytes(self._mask(text), limit)

    def append(
        self,
        kind: str,
        *,
        engine: str,
        label: str,
        step: str | None = None,
        tool: str | None = None,
        status: str | None = None,
        elapsed_ms: int | None = None,
        rows: int | None = None,
        excerpt: str | None = None,
        note: str | None = None,
        purpose: str | None = None,
    ) -> int:
        """이벤트 1건을 덧붙이고 seq를 돌려준다. 값이 None인 선택 키는 싣지 않는다.

        `purpose`(plans/187 W17)는 「무엇 때문에 무엇을 보는지」 한 줄 — 플레이북 선택 근거 · 스텝
        초점(`narrate.focus`) · HolmesGPT 조사 이유. `label`과 같은 상한·가림을 거친다.
        """
        lim = self.limits
        event: dict = {"seq": 0, "at": 0.0, "engine": engine, "kind": kind}
        if step is not None:
            event["step"] = step
        if tool is not None:
            event["tool"] = self._text(tool, lim.max_label_bytes)
        event["label"] = self._text(label, lim.max_label_bytes)
        if status is not None:
            event["status"] = status
        if elapsed_ms is not None:
            event["elapsed_ms"] = int(elapsed_ms)
        if rows is not None:
            event["rows"] = int(rows)
        if excerpt is not None:
            event["excerpt"] = self._text(excerpt, lim.max_excerpt_bytes)
        if note is not None:
            event["note"] = self._text(note, lim.max_note_bytes)
        if purpose is not None:
            event["purpose"] = self._text(purpose, lim.max_label_bytes)
        with self._lock:
            self._seq += 1
            event["seq"] = self._seq
            event["at"] = round(self._clock(), 3)
            size = _event_size(event)
            self._events.append((event, size))
            self._bytes += size
            while self._events and (
                len(self._events) > lim.max_events or self._bytes > lim.max_total_bytes
            ):
                _old, old_size = self._events.popleft()
                self._bytes -= old_size
                self._truncated = True
            return event["seq"]

    # ── 조회 ──

    def since(self, cursor: int) -> dict:
        """`seq > cursor`인 이벤트 — `{next_seq, truncated, events}`.

        `next_seq`는 지금까지의 마지막 seq다 — 다음 조회에 `activity_since`로 그대로 넘긴다.
        """
        with self._lock:
            events = [dict(e) for e, _ in self._events if e["seq"] > cursor]
            return {"next_seq": self._seq, "truncated": self._truncated, "events": events}

    def stats(self) -> dict:
        """보관 중 이벤트 수·바이트(실측·테스트용)."""
        with self._lock:
            return {"events": len(self._events), "bytes": self._bytes, "next_seq": self._seq}


def safe_append(log: ActivityLog | None, kind: str, **fields: object) -> int | None:
    """기록 1건을 best-effort로 덧붙인다 — 기록 예외는 경고만 남기고 조사 본체로 번지지 않는다.

    기록(관측)의 실패가 HolmesGPT 스트림·플레이북 스텝·소견 호출을 실패시키지 않게 한다
    (plans/187 v2 검증 Medium 1). `log`가 None이면 무동작이다.
    """
    if log is None:
        return None
    try:
        return log.append(kind, **fields)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 — 기록은 best-effort
        logger.warning("실행 기록 실패(무시) kind=%s: %s: %s", kind, type(exc).__name__, exc)
        return None


__all__ = [
    "ENGINES",
    "KINDS",
    "PHASES",
    "CLIP_MARK",
    "ActivityLimits",
    "ActivityLog",
    "clip_bytes",
    "safe_append",
]
