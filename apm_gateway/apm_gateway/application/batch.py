"""다건 대상 배치 — 한 호출의 `targets`(대상 항목 목록) = 작업 1개 (plans/134 W3 · 계약 A-2 ·
D-296 ④ · D-299 ②④ · G-13).

`hostname`을 받는 데이터 도구는 `targets`를 받는다. 항목마다 그 도구 코어를 그 항목의 대상 인자로
1회 부르고(하위 봉투 = 단건 호출과 같은 의미·같은 칸 — 집계·판정은 대상별 그대로), 행에
`target_index`를 달아 합친다. 하위 호출은 차례로 돈다 — 작업 하나가 전 대상을 덮으므로 예상·진행·
부분 실패가 작업 기록으로 나간다. 개수 상한은 없다.

- **형식**(HTTP 0회): 항목은 객체이고 키는 `hostname`·`instance_name`·`source_id`(+ 그 도구가
  받으면 `instance_id`)뿐이다. 항목마다 `hostname` 또는 `instance_name` 하나 이상(둘 다 = AND).
  모르는 키·형식 밖 값·빈 목록·최상위 `hostname`·`instance_name`·`instance_id`와 함께 줌 =
  `invalid_argument`.
  전송 계층은 모르는 최상위 인자를 버리지만 중첩 객체 키는 그대로 넘기므로 여기서 본다. 최상위
  `source_ids`는 `source_id` 없는 항목에 쓴다.
- **예상·진행**: 첫 하위 호출이 신고한 호출 계획 × 항목 수로 시작하고, 항목이 돌며 그 항목의 실제
  신고로 바꾼다(바깥 작업에는 추정치의 차이만 더한다 — 이중 계산 없음). 단위는 종전 「API 호출」.
- **하위 봉투**: 호출 맥락 메모(`[한계]`)·가린 칸·미해결 조건은 작업 마감과 같은 규칙
  (`settle_notices`)으로 그 하위 봉투에 싣는다. 배치 봉투의 고지는 그 합집합이다.
- 일부 항목 실패 = `partial` + `[한계] 대상 k/N 조회 실패: …` · 전부 실패 = 오류 봉투(코드가 모두
  같으면 그 코드, 다르면 `api_error`) + `batch`. 최상위 `limits`는 실패 줄 + 하위 `[한계]`
  합집합이다(항목 순 · 중복 제거 — 최상위만 읽는 소비자용). 큰 결과는 종전 스풀(작업 관리자가
  맡는다).
- 항목 `hostname`은 `instance_name`과 같은 길이 상한(`QUERY_MAX`)을 둔다(원문을 담는 칸).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from apm_gateway.adapters.jennifer.allowlist import NotAllowedError
from apm_gateway.application.jobs import (
    FILE_ONLY_KEY,
    MASKED_KEY,
    TEXT_PARTS_KEY,
    UNRESOLVED_KEY,
    settle_notices,
)
from apm_gateway.application.tools import QUERY_MAX, ApmTools, _given, search_query
from apm_gateway.domain.call_context import CallScope, current_scope, use_scope
from apm_gateway.domain.errors import API_ERROR, CONTRACT_VIOLATION, INVALID_ARGUMENT, ApmError

logger = logging.getLogger(__name__)

# 항목 키 — 기본 · `instance_id`를 받는 도구 · `instance_name`을 받지 않는 도구
TARGET_KEYS = frozenset({"hostname", "instance_name", "source_id"})
TARGET_KEYS_WITH_ID = TARGET_KEYS | {"instance_id"}
HOSTNAME_KEYS = frozenset({"hostname", "source_id"})
# 하위 봉투에서 배치 항목으로 옮기지 않는 칸(행·봉투 공통 머리)
_ENVELOPE_HEAD = ("rows", "row_count", "queried_at", "source", "source_kind", "tool")
_FAILED_SHOWN = 20  # 실패 대상 고지에 싣는 수(나머지는 「외 n건」 — 고지 문구다)
_REASONS_SHOWN = 3  # 전부 실패 사유에 싣는 앞 건수

ToolResult = Awaitable[dict[str, Any]]


@dataclass(frozen=True)
class Target:
    """대상 1개 — 단건 호출(최상위 인자 그대로)이거나 `targets` 항목 하나."""

    hostname: str | None = None
    instance_name: str | None = None
    instance_id: int | None = None
    source_id: str | None = None

    def sources(self, default: list[str] | None) -> list[str] | None:
        """소스 목록 인자 — 항목 `source_id`가 있으면 그 소스만, 없으면 최상위 `source_ids`."""
        return [self.source_id] if self.source_id is not None else default

    def source(self, default: str | None) -> str | None:
        """소스 1개 인자(앞 결과 참조 도구) — 항목 `source_id`가 있으면 그것, 없으면 최상위 값."""
        return self.source_id if self.source_id is not None else default

    def label(self) -> str:
        names = [str(v).strip() for v in (self.hostname, self.instance_name) if _given(v)]
        text = "/".join(names) or "?"
        if self.instance_id is not None:
            text += f"#{self.instance_id}"
        return f"{self.source_id}:{text}" if self.source_id else text


class _Plan:
    """배치 전체의 호출 계획 — 바깥 맥락(작업)에는 추정치의 차이만 신고한다."""

    def __init__(self, outer: CallScope, size: int) -> None:
        self.outer = outer
        self.size = size
        self.done: list[int] = []  # 끝난 항목이 신고한 호출 수
        self.current = 0  # 도는 항목이 지금까지 신고한 호출 수
        self.running = False
        self.posted = 0

    def _unit(self) -> int:
        """항목 1개 추정치 = 처음으로 호출 계획을 신고한 하위 호출의 계획."""
        for count in self.done:
            if count:
                return count
        return self.current if self.running else 0

    def _sync(self) -> None:
        unit = self._unit()
        started = len(self.done) + (1 if self.running else 0)
        total = sum(self.done) + unit * (self.size - started)
        if self.running:
            total += max(self.current, unit)
        if total != self.posted:
            self.outer.expect_calls(total - self.posted)
            self.posted = total

    def begin(self) -> None:
        self.current, self.running = 0, True
        self._sync()

    def report(self, count: int) -> None:
        self.current += int(count)
        self._sync()

    def end(self) -> None:
        self.done.append(self.current)
        self.current, self.running = 0, False
        self._sync()


class _ItemScope(CallScope):
    """하위 호출 1회의 맥락 — 호출 훅(슬롯 대기·호출 수·우선순위)은 바깥 작업 그대로, 호출 계획은
    배치 계획으로, 메모·가린 칸은 그 하위 봉투 몫으로 모은다."""

    def __init__(self, plan: _Plan) -> None:  # 우선순위는 바깥 맥락 것을 읽는다(속성 대신)
        self._plan = plan
        self._outer = plan.outer
        self.notes: list[str] = []
        self.masked_fields: set[str] = set()

    @property
    def priority(self) -> str:
        return self._outer.priority

    @priority.setter
    def priority(self, value: str) -> None:
        self._outer.priority = value

    async def before_call(self, source_id: str = "") -> None:
        await self._outer.before_call(source_id)

    def count_call(self, source_id: str = "", *, shared: bool = False) -> None:
        self._outer.count_call(source_id, shared=shared)

    def expect_calls(self, count: int) -> None:
        self._plan.report(count)

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)

    def masked(self, fields: Iterable[str]) -> None:
        self.masked_fields.update(fields)


def _bad(text: str) -> ApmError:
    return ApmError(INVALID_ARGUMENT, text)


def parse_targets(
    core: ApmTools,
    targets: Any,
    *,
    keys: frozenset[str],
    hostname: str | None = None,
    instance_name: str | None = None,
    instance_id: int | None = None,
    source_ids: list[str] | None = None,
) -> list[tuple[dict[str, Any], Target]]:
    """`targets` 형식 검사(HTTP 전) → [(항목 그대로, 대상)]. 어긋나면 `invalid_argument`."""
    if _given(hostname) or _given(instance_name) or instance_id is not None:
        raise _bad("targets와 최상위 hostname·instance_name·instance_id는 함께 줄 수 없다")
    if not isinstance(targets, list) or not targets:
        raise _bad("targets는 대상 항목 1개 이상의 목록이어야 한다")
    core.sources.require_configured()
    if source_ids:
        core.sources.select(source_ids)  # 모르는 id는 여기서 invalid_argument
    configured = set(core.sources.ids)
    out: list[tuple[dict[str, Any], Target]] = []
    for i, item in enumerate(targets):
        at = f"targets[{i}]"
        if not isinstance(item, dict):
            raise _bad(f"{at}는 객체여야 한다")
        unknown = sorted(str(k) for k in item if k not in keys)
        if unknown:
            raise _bad(f"{at}: 이 도구가 받지 않는 키 {unknown} — 받는 키: {sorted(keys)}")
        host = item.get("hostname")
        if host is not None and not (isinstance(host, str) and host.strip()):
            raise _bad(f"{at}: hostname은 비지 않은 문자열이어야 한다")
        if host is not None and len(host.strip()) > QUERY_MAX:  # 원문을 담는 칸 — 이름과 같은 상한
            raise _bad(f"{at}: hostname은 {QUERY_MAX}자 이하여야 한다({len(host.strip())}자)")
        name = item.get("instance_name")
        if name is not None:
            search_query(name, "instance_name")  # 문자열 · 1~QUERY_MAX자(단건과 같은 검사)
        if host is None and name is None:
            raise _bad(f"{at}: hostname 또는 instance_name이 필요하다")
        iid = item.get("instance_id")
        if iid is not None and (isinstance(iid, bool) or not isinstance(iid, int) or iid < 0):
            raise _bad(f"{at}: instance_id는 0 이상의 정수여야 한다: {iid!r}")
        sid = item.get("source_id")
        if sid is not None:
            if not isinstance(sid, str) or sid.strip() not in configured:
                raise _bad(f"{at}: 모르는 source_id {sid!r} — 설정된 소스: {core.sources.ids}")
            sid = sid.strip()
        out.append((dict(item), Target(host, name, iid, sid)))
    return out


async def _guarded(
    core: ApmTools, tool: str, call: Callable[[], ToolResult]
) -> dict[str, Any]:
    """하위 호출 1회 — 예외를 계약 오류 봉투로 바꾼다(단건 도구 실행과 같은 규칙 · 한 항목 실패가
    배치를 끊지 않는다)."""
    try:
        return await call()
    except ApmError as e:
        return core.err(tool, e.code, e.reason)
    except NotAllowedError as e:  # 도구 코드가 허용목록 밖 요청을 만들었다 — 게이트웨이 버그
        logger.warning("허용목록 거부(게이트웨이 버그): tool=%s %s", tool, e)
        return core.err(tool, CONTRACT_VIOLATION, f"허용목록 거부: {e}")
    except (TypeError, ValueError) as e:
        return core.err(tool, INVALID_ARGUMENT, str(e))
    except Exception as e:
        logger.exception("도구 실행 실패(다건 대상 항목): %s", tool)
        return core.err(tool, API_ERROR, f"내부 오류: {type(e).__name__}")


async def run_batch(
    core: ApmTools,
    tool: str,
    targets: Any,
    call: Callable[[Target], ToolResult],
    *,
    keys: frozenset[str],
    hostname: str | None = None,
    instance_name: str | None = None,
    instance_id: int | None = None,
    source_ids: list[str] | None = None,
) -> dict[str, Any]:
    """`targets` 배치 1회 — 형식 검사 → 항목마다 하위 호출(차례로) → 배치 봉투."""
    items = parse_targets(
        core,
        targets,
        keys=keys,
        hostname=hostname,
        instance_name=instance_name,
        instance_id=instance_id,
        source_ids=source_ids,
    )
    plan = _Plan(current_scope(), len(items))
    outcomes: list[tuple[dict[str, Any], Target, dict[str, Any], _ItemScope]] = []
    for raw, target in items:
        scope = _ItemScope(plan)
        plan.begin()
        with use_scope(scope):  # 하위 호출이 이 자리에서 바로 돈다(늦은 바인딩 없음)
            sub = await _guarded(core, tool, lambda: call(target))
        plan.end()
        outcomes.append((raw, target, sub, scope))
    return _combine(core, tool, outcomes)


def _combine(
    core: ApmTools,
    tool: str,
    outcomes: list[tuple[dict[str, Any], Target, dict[str, Any], _ItemScope]],
) -> dict[str, Any]:
    rows: list[Any] = []
    batch: list[dict[str, Any]] = []
    failed: list[tuple[Target, str, str]] = []
    unresolved: list[str] = []
    sub_limits: list[str] = []
    masked: set[str] = set()
    file_only: list[str] = []
    text_parts: dict[str, str] = {}
    partial = False
    for index, (raw, target, sub, scope) in enumerate(outcomes):
        entry: dict[str, Any] = {"index": index, "target": raw}
        if "error" in sub:
            code, reason = str(sub["error"]), str(sub.get("reason") or "")
            entry.update(status="error", error=code, reason=reason, row_count=0)
            failed.append((target, code, reason))
            batch.append(entry)
            continue
        unresolved += list(sub.get(UNRESOLVED_KEY) or ())
        masked |= set(sub.get(MASKED_KEY) or ()) | scope.masked_fields
        settle_notices(sub, scope.notes, scope.masked_fields)  # 단건 작업 마감과 같은 규칙
        sub_limits += list(sub.get("limits") or [])
        file_only += list(sub.pop(FILE_ONLY_KEY, None) or ())
        parts = sub.pop(TEXT_PARTS_KEY, None) or {}
        if parts:  # 결과 파일 텍스트 부분 이름은 항목마다 달라야 한다
            renamed = {f"{name}_t{index}": text for name, text in parts.items()}
            text_parts.update(renamed)
            entry["text_parts"] = list(renamed)
        sub_rows = list(sub.get("rows") or [])
        rows += [{**r, "target_index": index} if isinstance(r, dict) else r for r in sub_rows]
        partial = partial or bool(sub.get("partial"))
        entry.update(status="ok", row_count=len(sub_rows))
        entry.update({k: v for k, v in sub.items() if k not in _ENVELOPE_HEAD})
        batch.append(entry)
    total = len(outcomes)
    if failed and len(failed) == total:
        codes = {code for _, code, _ in failed}
        shown = "; ".join(
            f"{t.label()}({code}): {reason}" for t, code, reason in failed[:_REASONS_SHOWN]
        )
        more = (
            f" (앞 {_REASONS_SHOWN}건 · 외 {total - _REASONS_SHOWN}건)"
            if total > _REASONS_SHOWN
            else ""
        )
        out = core.err(
            tool,
            codes.pop() if len(codes) == 1 else API_ERROR,
            f"대상 {total}개 모두 실패 — {shown}{more}",
        )
        out["batch"] = batch
        return out
    limits: list[str] = []
    if failed:
        labels = [f"{t.label()}({code})" for t, code, _ in failed]
        more = f" 외 {len(labels) - _FAILED_SHOWN}건" if len(labels) > _FAILED_SHOWN else ""
        limits.append(
            f"[한계] 대상 {len(failed)}/{total} 조회 실패: "
            + ", ".join(labels[:_FAILED_SHOWN])
            + more
        )
    # 최상위 [한계] = 실패 줄 + 하위 [한계] 합집합(항목 순 · 중복 제거) — 최상위만 읽는 소비자용
    limits = list(dict.fromkeys([*limits, *sub_limits]))
    payload = core.ok(
        tool,
        rows,
        limits=limits,
        partial=partial or bool(failed),
        file_only=tuple(dict.fromkeys(file_only)),
        text_parts=text_parts or None,
        batch=batch,
    )
    if unresolved:
        payload[UNRESOLVED_KEY] = list(dict.fromkeys(unresolved))
    settle_notices(payload, (), masked)  # 배치 고지 = 하위 고지 합집합
    return payload


def targeted(
    core: ApmTools,
    tool: str,
    targets: Any,
    call: Callable[[Target], ToolResult],
    *,
    label: str,
    keys: frozenset[str] = TARGET_KEYS,
    hostname: str | None = None,
    instance_name: str | None = None,
    instance_id: int | None = None,
    source_ids: list[str] | None = None,
    label_prefix: str = "",
    reject: str | None = None,
) -> tuple[str, Callable[[], ToolResult]]:
    """MCP 도구 1회의 (감사 대상 문구, 코어 호출). `targets`가 없으면 최상위 인자 그대로 단건
    (종전 호출과 같다), 있으면 배치(감사 대상 = `targets(N)` 요약 — 대상 원문을 싣지 않는다).
    `reject`(사유)를 주면 `targets`가 있을 때 HTTP 없이 `invalid_argument`다(그 인자 조합에서는
    대상이 뜻이 없다)."""
    if targets is None:
        single = Target(hostname, instance_name, instance_id)
        return label, lambda: call(single)
    count = len(targets) if isinstance(targets, list) else "?"
    if reject is not None:
        reason = reject

        async def rejected() -> dict[str, Any]:
            raise _bad(reason)

        return f"{label_prefix}targets({count})", rejected
    return f"{label_prefix}targets({count})", lambda: run_batch(
        core,
        tool,
        targets,
        call,
        keys=keys,
        hostname=hostname,
        instance_name=instance_name,
        instance_id=instance_id,
        source_ids=source_ids,
    )
