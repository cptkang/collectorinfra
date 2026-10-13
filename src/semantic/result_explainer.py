"""조회 결과 용어 설명·값 해석 서비스(plans/160 §2.3 B-2 · §2.4 C-2 · D-327).

라우트(`/glossary`·`/interpret`)가 소유권·실존 검증과 감사를 마친 뒤 호출하는 async 함수 2개다.

- `explain_term` — 사전(`result_glossary.lookup_term`) 적중이면 LLM 호출 없이 반환한다. 미적중이면
  로그 `glossary_miss` 1줄을 남기고 LLM으로 뜻·단위를 만든다(「사전 미등재 · LLM 생성」 표시용
  `source="llm_generated"`). 생성분은 이 모듈의 메모리 캐시(결과 소유자 단위)에만 둔다 — 사전
  파일·DB에 쓰지 않는다.
- `interpret_value` — 위치 사실(`value_context`)을 코드가 계산하고 LLM은 2~4문장 문장화만 한다.
  출력에 판정·원인 어휘가 있으면 문장을 버리고(로그 1줄) 계산값만 돌려준다.

입력 행은 **호출자가 마스킹한 뒤** 넘긴다 — 이 모듈은 받은 행만 프롬프트에 싣는다. 중첩 시계열은
`chart_rows`로 편 행 기준이다(멱등이라 이미 편 행을 넘겨도 같다). LLM은 팩토리로 받아 필요할
때만 만든다(사전 적중·캐시 적중이면 만들지 않는다). LLM 동시 호출은 프로세스 전체 `LLM_CONCURRENCY`
개로 묶는다. 실패·타임아웃은 예외 대신 사유 문자열로 돌린다.

계층: application.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Callable, Hashable, Sequence
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from src.domain.result_chart import chart_rows, is_blank
from src.domain.result_value_context import value_context
from src.prompts.result_explain import (
    INTERPRET_SYSTEM,
    TERM_SYSTEM,
    find_forbidden_terms,
    render_interpret_user,
    render_term_user,
)
from src.semantic.result_glossary import DescriptionProvider, ResultSource, lookup_term
from src.utils.json_extract import coerce_content_text

logger = logging.getLogger(__name__)

#: LLM 응답 대기 상한(초) — 동시 호출 슬롯 대기 시간을 포함한다.
LLM_TIMEOUT_S = 20.0
#: 프로세스 전체 동시 LLM 호출 상한(사용자 클릭이 워커 평면을 몰아붙이지 않게).
LLM_CONCURRENCY = 4
#: 캐시 상한(항목 수).
CACHE_MAX = 500
#: 용어 생성에 싣는 표본 값 수 · 이웃 칼럼 수 상한.
TERM_SAMPLE_COUNT = 3
TERM_NEIGHBOR_MAX = 15

#: 인자 없이 `ainvoke`가 있는 LLM을 만든다.
#: 운영에서는 `lambda: create_llm(config, purpose="answer")`.
LlmFactory = Callable[[], Any]


class _LRU:
    """상한이 있는 인메모리 LRU."""

    def __init__(self, maxsize: int) -> None:
        self._data: OrderedDict[Hashable, dict[str, Any]] = OrderedDict()
        self._maxsize = maxsize

    def get(self, key: Hashable) -> dict[str, Any] | None:
        value = self._data.get(key)
        if value is not None:
            self._data.move_to_end(key)
        return value

    def put(self, key: Hashable, value: dict[str, Any]) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)

    def clear(self) -> None:
        self._data.clear()


_TERM_CACHE = _LRU(CACHE_MAX)
_INTERPRET_CACHE = _LRU(CACHE_MAX)


def clear_caches() -> None:
    """두 캐시를 비운다(테스트·운영 재적재용)."""
    _TERM_CACHE.clear()
    _INTERPRET_CACHE.clear()


_semaphore_state: tuple[asyncio.AbstractEventLoop, asyncio.Semaphore] | None = None


def _llm_semaphore() -> asyncio.Semaphore:
    """실행 중인 이벤트 루프의 동시 호출 세마포어(루프가 바뀌면 새로 만든다 — 테스트 루프 대비)."""
    global _semaphore_state
    loop = asyncio.get_running_loop()
    if _semaphore_state is None or _semaphore_state[0] is not loop:
        _semaphore_state = (loop, asyncio.Semaphore(LLM_CONCURRENCY))
    return _semaphore_state[1]


async def _ask(
    llm_factory: LlmFactory, system: str, user: str, timeout_s: float
) -> tuple[str | None, str | None]:
    """LLM 한 번 호출 — (문장, None) 또는 (None, 실패 사유).

    동시 호출 슬롯(`LLM_CONCURRENCY`) 대기 시간도 `timeout_s` 안에 든다.
    """

    async def call() -> Any:
        async with _llm_semaphore():
            llm = llm_factory()
            return await llm.ainvoke(
                [SystemMessage(content=system), HumanMessage(content=user)]
            )

    try:
        response = await asyncio.wait_for(call(), timeout=timeout_s)
    except TimeoutError:
        return None, f"LLM 응답 시간 초과({timeout_s:g}초)"
    except Exception as e:  # noqa: BLE001 — 실패는 사유로 돌려 계산값·사전만이라도 보인다
        logger.warning("결과 해설 LLM 호출 실패: %s: %s", type(e).__name__, e)
        return None, f"LLM 호출 실패({type(e).__name__})"
    text = coerce_content_text(getattr(response, "content", response)).strip()
    if not text:
        return None, "LLM 응답이 비었습니다"
    return text, None


def _columns(rows: Sequence[dict[str, Any]]) -> list[str]:
    seen: dict[str, None] = {}
    for row in rows:
        for key in row:
            seen.setdefault(str(key), None)
    return list(seen)


def _samples(rows: Sequence[dict[str, Any]], column: str) -> list[Any]:
    """그 칼럼의 서로 다른 비어 있지 않은 값 앞쪽 N개."""
    out: list[Any] = []
    seen: set[str] = set()
    for row in rows:
        value = row.get(column)
        if is_blank(value) or str(value) in seen:
            continue
        seen.add(str(value))
        out.append(value)
        if len(out) >= TERM_SAMPLE_COUNT:
            break
    return out


async def explain_term(
    column: str,
    *,
    rows: Sequence[dict[str, Any]],
    user_query: str,
    source: ResultSource,
    llm_factory: LlmFactory,
    owner: str | None = None,
    description_provider: DescriptionProvider | None = None,
    timeout_s: float = LLM_TIMEOUT_S,
) -> dict[str, Any]:
    """칼럼(머리글) 설명 — 사전 → 캐시 → LLM 생성 순.

    LLM 생성분은 원 질문·표본 값을 보고 만든 문장이라 결과 소유자(`owner` — 저장 결과의
    `owner_sub` · 인증 off면 None) 단위로 캐시한다. 사전 적중분은 정본이라 사용자와 무관하다.

    Returns:
        사전 적중 `{"source": "glossary", "text", "unit", "origin"}` ·
        LLM 생성 `{"source": "llm_generated", "text"}` ·
        실패 `{"source": "unavailable", "reason"}`

    Raises:
        ValueError: column이 결과 행 키에 없을 때
    """
    flat = chart_rows(list(rows))
    columns = _columns(flat)
    if column not in columns:
        raise ValueError(f"결과에 없는 칼럼: {column}")
    hit = await lookup_term(column, source, description_provider=description_provider)
    if hit is not None:
        return dict(hit)
    cache_key = (source.key, owner, column)
    cached = _TERM_CACHE.get(cache_key)
    if cached is not None:
        return dict(cached)
    logger.info("glossary_miss column=%r source=%r", column, source.key)
    user = render_term_user(
        column=column,
        user_query=user_query,
        source_label=source.label,
        samples=_samples(flat, column),
        neighbors=[c for c in columns if c != column][:TERM_NEIGHBOR_MAX],
    )
    text, reason = await _ask(llm_factory, TERM_SYSTEM, user, timeout_s)
    if text is None:
        return {"source": "unavailable", "reason": reason}
    result = {"source": "llm_generated", "text": text}
    _TERM_CACHE.put(cache_key, result)
    return dict(result)


async def interpret_value(
    column: str,
    row: int,
    *,
    rows: Sequence[dict[str, Any]],
    user_query: str,
    llm_factory: LlmFactory,
    cache_key: tuple[str, str],
    source: ResultSource | None = None,
    description_provider: DescriptionProvider | None = None,
    timeout_s: float = LLM_TIMEOUT_S,
) -> dict[str, Any]:
    """셀(column, row) 값 해석 — 계산 사실 + LLM 문장(가드 통과 시).

    `cache_key`는 `(query_id, table)`. `source`를 주면 사전 설명(사전 적중분만 — LLM 생성 용어는
    쓰지 않는다)을 프롬프트에 함께 싣는다.

    Returns:
        `{"column", "row", "value", "facts", "text", "source", "reason"}` — `source`는
        `"llm_interpretation"`(text 있음 · reason None) 또는 `"facts_only"`(text None · reason 있음)

    Raises:
        ValueError: column이 결과 행 키에 없거나 row가 범위 밖일 때(`value_context` 계약)
    """
    key = (*cache_key, column, row)
    cached = _INTERPRET_CACHE.get(key)
    if cached is not None:
        return dict(cached)
    ctx = value_context(chart_rows(list(rows)), column, row)
    term = None
    if source is not None:
        term = await lookup_term(column, source, description_provider=description_provider)
    user = render_interpret_user(
        column=column,
        value=ctx["value"],
        fact_labels=[f["label"] for f in ctx["facts"]],
        user_query=user_query,
        term_text=term["text"] if term else None,
    )
    text, reason = await _ask(llm_factory, INTERPRET_SYSTEM, user, timeout_s)
    if text is not None and (hits := find_forbidden_terms(text)):
        logger.warning(
            "result_interpret_guard_drop column=%r row=%d terms=%s", column, row, hits
        )
        text, reason = None, "LLM 문장에 판정·원인 어휘가 있어 표시하지 않습니다"
    result: dict[str, Any] = {
        "column": column,
        "row": row,
        "value": ctx["value"],
        "facts": ctx["facts"],
        "text": text,
        "source": "llm_interpretation" if text is not None else "facts_only",
        "reason": reason,
    }
    if text is not None:
        _INTERPRET_CACHE.put(key, result)
    return dict(result)
