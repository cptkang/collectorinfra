"""잡 단위 도구 결과 캐시 (plans/187 §5.0.1 3번 · G-26 · D-341 ⑲).

같은 조사 잡 안에서 같은 도구·같은 인자는 한 번만 실제로 호출한다. 플레이북 엔진과 HolmesGPT가
한 잡 안에서 동시에 돌 때(§5.0.1 `parallel`) 제니퍼·폴스타 호출이 두 배가 되는 것을 막는다.

- 키 = 도구명 + 정규화 인자(값이 None인 키 제거 · 키 정렬 JSON). 인자 순서가 달라도 같은 키다.
- 진행 중인 같은 키 요청은 첫 호출의 결과를 기다려 받는다(스레드 안전).
- 오류 응답(`{"error": …}`)도 그 잡 안에서는 그대로 공유한다 — 같은 잡에서 재시도하지 않는다.
- 잡이 끝나면 `close()`(또는 `with` 블록 종료)로 버린다. 닫힌 캐시는 호출을 거부한다
  (잡 간 누수 0 — 다음 잡은 새 캐시를 만든다).

배치 콜러블(`[(도구, 인자)] → [JSON 문자열]`)을 감싸는 `CachedBatchCaller`를 돌려주며, 단일 호출
인터페이스 `call(tool, args) -> str`도 함께 노출한다(W4가 HolmesGPT 도구 경로에도 씌울 수 있게).
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from typing import Any

logger = logging.getLogger(__name__)

BatchCaller = Callable[[Sequence[tuple[str, dict]]], list[str]]

#: 진행 중 호출을 기다리는 기본 상한(초) — dispatcher 전체 예산(300s)과 같다.
DEFAULT_WAIT_TIMEOUT_S = 300.0


def _strip_none(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _strip_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple)):
        return [_strip_none(v) for v in value]
    return value


def cache_key(tool: str, args: Mapping[str, Any] | None) -> str:
    """도구명 + 정규화 인자(None 제거 · 정렬 JSON) 키."""
    return json.dumps(
        {"tool": tool, "args": _strip_none(dict(args or {}))},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )


def _err(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


class CacheClosedError(RuntimeError):
    """닫힌(잡이 끝난) 캐시로 호출했다."""


class _Entry:
    __slots__ = ("done", "value")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.value: str | None = None

    def set(self, value: str) -> None:
        self.value = value
        self.done.set()


class ToolResultCache:
    """잡 1건 범위의 도구 결과 캐시."""

    def __init__(self, *, wait_timeout_s: float = DEFAULT_WAIT_TIMEOUT_S) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, _Entry] = {}
        self._closed = False
        self._wait_timeout_s = float(wait_timeout_s)
        self.real_calls = 0  # 실제로 하위 콜러블에 넘긴 호출 수
        self.hits = 0  # 캐시(완료·진행 중)에서 받은 호출 수

    # ── 수명 ──
    def close(self) -> None:
        """캐시를 버린다. 이후 호출은 `CacheClosedError`."""
        with self._lock:
            self._closed = True
            self._entries.clear()

    @property
    def closed(self) -> bool:
        return self._closed

    def __enter__(self) -> ToolResultCache:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def contains(self, tool: str, args: Mapping[str, Any] | None) -> bool:
        """같은 키가 이미 있는지(완료·진행 중) — 실행 기록의 캐시 적중 표시용(plans/187 W13)."""
        key = cache_key(tool, args)
        with self._lock:
            return key in self._entries

    # ── 호출 ──
    def wrap(self, batch: BatchCaller) -> CachedBatchCaller:
        """배치 콜러블을 이 캐시로 감싼다."""
        return CachedBatchCaller(self, batch)

    def fetch(self, batch: BatchCaller, calls: Sequence[tuple[str, dict]]) -> list[str]:
        """calls 중 캐시에 없는 것만 `batch`로 호출하고, 전부의 결과를 입력 순서대로 돌려준다."""
        keys = [cache_key(tool, args) for tool, args in calls]
        owned: list[int] = []
        entries: list[_Entry] = []
        with self._lock:
            if self._closed:
                raise CacheClosedError("도구 결과 캐시가 닫혔다(잡 종료)")
            for i, key in enumerate(keys):
                entry = self._entries.get(key)
                if entry is None:
                    entry = _Entry()
                    self._entries[key] = entry
                    owned.append(i)
                else:
                    self.hits += 1
                entries.append(entry)
            self.real_calls += len(owned)
        if owned:
            sub = [calls[i] for i in owned]
            raws: list[str] = []
            try:
                raws = list(batch(sub))
            except Exception as e:  # noqa: BLE001 — 하위 콜러블 실패도 공유 결과(오류)로 남긴다
                logger.warning("도구 배치 호출 실패(캐시 경유): %s", e)
                raws = [_err(f"도구 배치 호출 실패: {e}")] * len(sub)
            finally:
                if len(raws) < len(sub):
                    raws = raws + [_err("응답 누락")] * (len(sub) - len(raws))
                for i, raw in zip(owned, raws):
                    entries[i].set(raw if isinstance(raw, str) else _err("응답 형식 오류"))
        out: list[str] = []
        for entry in entries:
            if not entry.done.wait(self._wait_timeout_s):
                out.append(_err(f"캐시 대기 시간 초과({self._wait_timeout_s:.0f}s)"))
                continue
            out.append(entry.value if entry.value is not None else _err("응답 누락"))
        return out


class CachedBatchCaller:
    """캐시를 거치는 배치 콜러블. `__call__(calls)`(배치)과 `call(tool, args)`(단일)를 낸다."""

    def __init__(self, cache: ToolResultCache, batch: BatchCaller) -> None:
        self._cache = cache
        self._batch = batch

    def __call__(self, calls: Sequence[tuple[str, dict]]) -> list[str]:
        return self._cache.fetch(self._batch, calls)

    def call(self, tool: str, args: dict) -> str:
        """단일 호출 — HolmesGPT 도구 경로처럼 한 건씩 부르는 쪽용."""
        return self._cache.fetch(self._batch, [(tool, args)])[0]

    def cached(self, tool: str, args: dict) -> bool:
        """이 호출이 캐시에서 나올지(호출 전 판정 — 실행 기록 `status=cached` · plans/187 W13)."""
        return self._cache.contains(tool, args)


__all__ = [
    "BatchCaller",
    "CacheClosedError",
    "CachedBatchCaller",
    "DEFAULT_WAIT_TIMEOUT_S",
    "ToolResultCache",
    "cache_key",
]
