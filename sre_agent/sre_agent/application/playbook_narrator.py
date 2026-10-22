"""플레이북 단계 소견기 · 종합기 + 결정적 가드 (plans/187 §5.4 · §4.0 · D-341 ③④).

LLM은 주입 콜러블(`LlmCall`)이다 — OpenAI 호환 채팅 메시지 목록을 받아 응답 텍스트를 돌려준다.
sre_agent의 기존 LLM(holmes `DefaultLLM.completion(messages, …)` → litellm `ModelResponse`)은
`lambda m: llm.completion(m, temperature=0).choices[0].message.content or ""`로 꽂는다(배선은 W4).

- 단계 소견: `narrate` 지시가 있고 상태가 ok인 스텝만 · 단계 간 병렬
  · 호출 상한 `max_step_calls`(기본 7) · 초과분은 결정적 우선순위(반박 아닌 가설의 근거
  스텝·degraded 판정 먼저 → YAML 순서)로 잘라 사유.
- 종합: LLM 1회. 가설 순위·상태·대응 순서는 코드값으로 고정되고 LLM 문장은 `narrative` 칸에만
  들어간다.
- 결정적 가드(후처리) — 순서대로:
  ① 극성: 판정 normal인데 이상 어휘(부정 표현 없이) · degraded인데 정상 어휘만 → 소견 전체 버림
     (「판정과 불일치 — 소견 생략」). 종합은 1순위 가설이 지지(supported)일 때 degraded와 같은
     검사를 건다(「모든 지표 정상」 종합 → 「1순위 가설(지지)과 불일치 — 종합 소견 생략」).
  ② 환각 수치: 문장의 수치가 사용자 메시지(단계 표·판정·판독 근거)의 어떤 값과도 맞지 않으면
     그 문장 제거.
     허용: 같은 값을 그 수치의 소수 자릿수로 반올림한 값 · 천 단위 쉼표 · 부호 무시 ·
     「건·개·곳·대·위·순위·번째」가 붙은 10 이하 정수(개수·순위) · 식별자 일부(`p95`·`S3`)는
     수치로 보지 않는다. 단위 환산은 근거가 있는 칸에만 — `*_ms`(÷1000 ms→초) · `*_s`(×1000) ·
     `*ratio*`·`share`(×100 비율→%) · `*_pct`(÷100). 칸 이름은 표의 열·같은 행의 문자열 칸
     (지표 이름)·JSON 키 경로·`이름 값` 앞 이름으로 정한다. 날짜·시각은 각 자리 숫자가 아니라
     문자열 전체(`2026-10-19`·`07:20` — 문장 쪽 `07시 20분` 포함)로만 허용한다.
  ③ 순위 서술: 순위·확정 어휘(`RANK_CLAIM_TERMS` — 「1순위·가장 유력·가장 가능성이 높은·주원인·
     주된 원인·근본 원인은」 등) 문장은 종합에서는 코드 1순위 가설 제목을 담아야 하고,
     단계 소견에서는 쓸 수 없다(원인 순위는 종합 담당) — 어기면 그 문장 제거.
  ④ 문장 수 상한 → ⑤ 길이 상한(뒤 문장부터 버림 · 첫 문장이 넘으면 자름).
  ⑥ (단계만) 실시간 단면 배지가 있는데 「현재값」이 없으면 앞에 「(현재값 기준)」을 붙인다.
- LLM 예외·타임아웃은 그 단계 소견만 비우고 사유를 남긴다.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as wait_futures
from dataclasses import dataclass, field
from typing import Any

from sre_agent.application.playbook_prompts import (
    render_step_messages,
    render_synthesis_messages,
)
from sre_agent.domain.activity_log import ActivityLog, safe_append

logger = logging.getLogger(__name__)

#: LLM 콜러블 계약 — 채팅 메시지 목록(`[{"role": "system"|"user", "content": str}]`) → 응답 텍스트.
LlmCall = Callable[[list[dict[str, str]]], str]

NOT_CONFIGURED = "소견기 미구성"
POLARITY_DROPPED = "판정과 불일치 — 소견 생략"
SYNTH_POLARITY_DROPPED = "1순위 가설(지지)과 불일치 — 종합 소견 생략"
REALTIME_MARK = "현재값"

#: 이상 어휘(판정 normal인데 부정 없이 쓰면 극성 반대).
ANOMALY_TERMS = (
    "이상",
    "급증",
    "급등",
    "급감",
    "폭증",
    "지연",
    "악화",
    "포화",
    "고갈",
    "장애",
    "병목",
    "초과",
)
#: 정상 어휘(판정 degraded인데 이상 어휘 없이 이것만 쓰면 극성 반대).
NORMAL_TERMS = (
    "정상",
    "문제없",
    "문제 없",
    "이상 없",
    "이상없",
    "양호",
    "특이사항 없",
    "특이 사항 없",
)
#: 이상 어휘 뒤 이 범위 안의 부정 표현은 「이상 아님」으로 본다.
_NEGATIONS = ("없", "않", "아니", "아닌", "못")
_NEGATION_WINDOW = 14
#: 「N배 이상」「80% 이상」처럼 비교 뜻의 「이상」 앞 글자.
_COMPARATIVE_PREFIX = re.compile(r"[\d%배건개분초]\s*$")

#: 허용 집합 쪽 수치 — 넓게 잡는다(`T07:20`의 07 등).
_NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
#: 소견 문장 쪽 수치 토큰 — 식별자 일부(`p95`·`S3`)는 수치가 아니다.
_CLAIM_NUMBER = re.compile(r"(?<![A-Za-z_\d.])\d+(?:,\d{3})*(?:\.\d+)?")
_COUNT_SUFFIX = re.compile(r"\s*(건|개|곳|대|위|순위|번째)")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。])\s+|\n+")
_BULLET = re.compile(r"^\s*(?:[-*•·]|\d+[.)])\s+")
#: 순위·확정 진술 어휘(결정적 목록) — 종합은 코드 1순위 제목을 함께 담아야 하고 단계 소견은 금지.
RANK_CLAIM_TERMS = (
    "1순위",
    "가장 유력",
    "가장 가능성이 높",
    "가장 가능성 높",
    "가장 가능성이 큰",
    "가장 가능성 큰",
    "가장 큰 원인",
    "주원인",
    "주 원인",
    "주된 원인",
    "근본 원인은",
    "근본원인은",
    "원인으로 확정",
    "확실한 원인",
)
#: 날짜·시각 문자열(허용 집합 쪽 — 각 자리 숫자로 분해하지 않는다).
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TIME = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?::(\d{2}))?(?![\d:])")
#: 소견 문장 쪽 시각(`07:20` · `07시 20분`).
_CLAIM_TIME = re.compile(r"(?<![\d:])(\d{1,2})(?::|시\s*)(\d{2})(?::\d{2}|분)?(?![\d:])")
#: `이름 값`·`"이름":값` 꼴의 앞 이름(단위 환산 근거).
_LABELED_NUMBER = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)\"?\s*[:=]?\s*(-?\d+(?:,\d{3})*(?:\.\d+)?)(?![\d.])"
)


@dataclass
class Opinion:
    """소견 1건 — `text`(가드 통과분) · `dropped_reason`(전부 버렸을 때) · `guard`(가드 메모)."""

    text: str = ""
    dropped_reason: str | None = None
    guard: list[str] = field(default_factory=list)
    called: bool = False


@dataclass(frozen=True)
class NarratorLimits:
    """소견기 상한 — [제안] 값(§4.0 단계당 ≤ 4문장 · 약 140단어 · §5.4 호출 ≤ 7 + 1)."""

    max_step_calls: int = 7
    max_workers: int = 4
    step_timeout_s: float = 20.0
    synthesis_timeout_s: float = 30.0
    max_input_chars: int = 6000
    max_chars: int = 420
    synthesis_max_sentences: int = 6
    synthesis_max_chars: int = 900
    synthesis_max_input_chars: int = 8000


@dataclass(frozen=True)
class StepNarration:
    """단계 소견 요청 1건."""

    step: str
    section: Mapping[str, Any]
    focus: str
    max_sentences: int
    readings: Mapping[str, Mapping[str, Any]]
    realtime_badges: tuple[str, ...] = ()


# ── 결정적 가드 ─────────────────────────────────────────────────────────────


def split_sentences(text: str) -> list[str]:
    """문장 분리 — 마침표·물음표·느낌표 뒤 공백 또는 줄바꿈. 목록 머리표는 지운다."""
    parts = [_BULLET.sub("", p).strip() for p in _SENTENCE_SPLIT.split(text or "")]
    return [p for p in parts if p]


def _has_anomaly(text: str) -> bool:
    """부정·비교 뜻이 아닌 이상 어휘가 있으면 참."""
    for term in ANOMALY_TERMS:
        for m in re.finditer(re.escape(term), text):
            tail = text[m.end() : m.end() + _NEGATION_WINDOW]
            if any(n in tail for n in _NEGATIONS):
                continue
            if term == "이상" and _COMPARATIVE_PREFIX.search(text[: m.start()]):
                continue
            return True
    return False


def _has_normal(text: str) -> bool:
    return any(t in text for t in NORMAL_TERMS)


def polarity_conflict(text: str, verdict: str | None) -> bool:
    """판정과 반대 극성인가 — normal인데 이상 어휘 · degraded인데 정상 어휘만(이상 어휘 없음)."""
    if verdict == "normal":
        return _has_anomaly(text)
    if verdict == "degraded":
        return _has_normal(text) and not _has_anomaly(text)
    return False


@dataclass(frozen=True)
class SourceNumber:
    """허용 수치 1개 — 단위 환산 근거가 있는 칸이면 `factors`(값에 곱해 허용할 배수)."""

    value: float
    factors: tuple[float, ...] = ()


#: 허용 집합 원소 — 수(정확 일치만) · `SourceNumber`(근거 있는 환산 포함) · 날짜·시각 문자열.
AllowedItem = float | SourceNumber | str


def _unit_factors(labels: Sequence[str]) -> tuple[float, ...]:
    """칸 이름들 → 허용 환산 배수(ms↔s · 비율↔%). 근거 없으면 빈 튜플."""
    out: set[float] = set()
    for raw in labels:
        label = raw.lower()
        if label == "ms" or label.endswith("_ms"):
            out.add(1 / 1000)
        if label.endswith(("_s", "_sec", "_seconds")):
            out.add(1000.0)
        if "ratio" in label or label == "share" or label.endswith("_share"):
            out.add(100.0)
        if label == "pct" or label.endswith("_pct") or "percent" in label:
            out.add(1 / 100)
    return tuple(sorted(out))


def _walk_json(value: Any, path: tuple[str, ...], out: list[AllowedItem]) -> None:
    """JSON 값의 수에 키 경로(표는 열 이름 + 같은 행의 문자열 칸)로 환산 근거를 붙인다."""
    if isinstance(value, Mapping):
        cols, rows = value.get("columns"), value.get("rows")
        if isinstance(cols, list) and isinstance(rows, list):
            for row in rows:
                if not isinstance(row, list):
                    continue
                names = [c for c in row if isinstance(c, str)]
                for col, cell in zip(cols, row):
                    _walk_json(cell, (*path, str(col), *names), out)
        for k, v in value.items():
            if k not in ("columns", "rows"):
                _walk_json(v, (*path, str(k)), out)
    elif isinstance(value, list):
        for v in value:
            _walk_json(v, path, out)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        factors = _unit_factors(path)
        if factors:
            out.append(SourceNumber(float(value), factors))


def _norm_time(h: str, m: str) -> str:
    return f"{int(h):02d}:{int(m):02d}"


def source_numbers(text: str) -> list[AllowedItem]:
    """허용 수치 집합 — 입력 텍스트의 모든 수(쉼표 제거) + 근거 있는 칸의 환산 + 날짜·시각 문자열.

    날짜·시각은 문자열 전체로만 싣고 그 자리 숫자는 수로 넣지 않는다(검증 지적 7).
    """
    raw = text or ""
    out: list[AllowedItem] = []
    blanked = raw
    for m in _DATE.finditer(raw):
        out.append(m.group())
        blanked = blanked[: m.start()] + " " * len(m.group()) + blanked[m.end() :]
    for m in _TIME.finditer(blanked):
        out.append(_norm_time(m.group(1), m.group(2)))
        blanked = blanked[: m.start()] + " " * len(m.group()) + blanked[m.end() :]
    out.extend(float(m.group().replace(",", "")) for m in _NUMBER.finditer(blanked))
    for m in _LABELED_NUMBER.finditer(blanked):
        factors = _unit_factors([m.group(1)])
        if factors:
            out.append(SourceNumber(float(m.group(2).replace(",", "")), factors))
    decoder = json.JSONDecoder()
    pos = raw.find("{")
    while pos != -1:
        try:
            obj, end = decoder.raw_decode(raw, pos)
        except ValueError:
            pos = raw.find("{", pos + 1)
            continue
        _walk_json(obj, (), out)
        pos = raw.find("{", end)
    return out


def _decimals(token: str) -> int:
    return len(token.split(".")[1]) if "." in token else 0


def number_allowed(token: str, allowed: Sequence[AllowedItem]) -> bool:
    """수치 토큰 1개가 허용 집합의 값(반올림 · 근거 있는 칸의 단위 환산 포함)과 맞는가."""
    x = float(token.replace(",", ""))
    d = _decimals(token)
    tol = 0.5 * 10 ** (-d) + 1e-9
    for item in allowed:
        if isinstance(item, str):
            continue
        if isinstance(item, SourceNumber):
            cands: tuple[float, ...] = (item.value, *(item.value * f for f in item.factors))
        else:
            cands = (float(item),)
        if any(abs(abs(c) - x) <= tol for c in cands):
            return True
    return False


def _mask_known_times(sentence: str, allowed: Sequence[AllowedItem]) -> str:
    """허용 집합에 문자열 전체로 있는 날짜·시각은 수치 검사에서 뺀다(공백으로 가림)."""
    texts = {a for a in allowed if isinstance(a, str)}
    if not texts:
        return sentence
    out = sentence
    for m in _DATE.finditer(sentence):
        if m.group() in texts:
            out = out[: m.start()] + " " * len(m.group()) + out[m.end() :]
    for m in _CLAIM_TIME.finditer(out):
        if _norm_time(m.group(1), m.group(2)) in texts:
            out = out[: m.start()] + " " * len(m.group()) + out[m.end() :]
    return out


def unknown_numbers(sentence: str, allowed: Sequence[AllowedItem]) -> list[str]:
    """문장에서 허용 집합에 없는 수치 토큰(개수·순위 접미의 10 이하 정수는 제외)."""
    bad: list[str] = []
    sentence = _mask_known_times(sentence, allowed)
    for m in _CLAIM_NUMBER.finditer(sentence):
        token = m.group()
        if (
            "." not in token
            and float(token.replace(",", "")) <= 10
            and _COUNT_SUFFIX.match(sentence, m.end())
        ):
            continue
        if not number_allowed(token, allowed):
            bad.append(token)
    return bad


def _cap(sentences: list[str], max_sentences: int, max_chars: int, notes: list[str]) -> list[str]:
    if len(sentences) > max_sentences:
        notes.append(
            f"문장 수 상한 {max_sentences} 초과 — {len(sentences) - max_sentences}문장 잘림"
        )
        sentences = sentences[:max_sentences]
    total = 0
    kept: list[str] = []
    for s in sentences:
        add = len(s) + (1 if kept else 0)
        if total + add > max_chars:
            if not kept:
                kept.append(s[: max(1, max_chars - 1)] + "…")
                notes.append(f"길이 상한 {max_chars}자 초과 — 첫 문장 절단")
            else:
                notes.append(
                    f"길이 상한 {max_chars}자 초과 — {len(sentences) - len(kept)}문장 잘림"
                )
            break
        kept.append(s)
        total += add
    return kept


def guard_text(
    text: str,
    *,
    verdict: str | None,
    allowed: Sequence[AllowedItem],
    max_sentences: int,
    max_chars: int,
    top_title: str | None = None,
    rank_guard: bool = False,
    top_status: str | None = None,
) -> Opinion:
    """LLM 응답에 결정적 가드를 건다(모듈 독스트링 ①~⑤).

    top_status: (종합) 코드 1순위 가설 상태 — `supported`면 판정 degraded와 같은 극성 검사.
    """
    raw = (text or "").strip()
    if not raw:
        return Opinion(dropped_reason="빈 응답", called=True)
    if polarity_conflict(raw, verdict):
        return Opinion(dropped_reason=POLARITY_DROPPED, called=True, guard=[POLARITY_DROPPED])
    if verdict is None and top_status == "supported" and polarity_conflict(raw, "degraded"):
        return Opinion(
            dropped_reason=SYNTH_POLARITY_DROPPED, called=True, guard=[SYNTH_POLARITY_DROPPED]
        )
    notes: list[str] = []
    kept: list[str] = []
    for s in split_sentences(raw):
        bad = unknown_numbers(s, allowed)
        if bad:
            notes.append(f"환각 수치 문장 제거({', '.join(bad)})")
            continue
        if (
            rank_guard
            and any(k in s for k in RANK_CLAIM_TERMS)
            and (top_title is None or top_title not in s)
        ):
            notes.append("코드 1순위와 다른 순위 서술 문장 제거")
            continue
        kept.append(s)
    kept = _cap(kept, max_sentences, max_chars, notes)
    if not kept:
        return Opinion(
            dropped_reason=" · ".join(notes) or "가드 후 남은 문장 없음", guard=notes, called=True
        )
    return Opinion(text=" ".join(kept), guard=notes, called=True)


# ── 소견기 ──────────────────────────────────────────────────────────────────


class PlaybookNarrator:
    """단계 소견기 · 종합기. `calls`는 실제 LLM 호출 수(단계 ≤ max_step_calls + 종합 1).

    `activity`(plans/187 W13)가 있으면 호출마다 `llm_start`·`llm_end`를, 단계 소견은 가드 전 초안을
    `ai_note`(AI 메모 — 검증 전)로 남긴다. 없으면 기록 0(종전 동작).
    """

    def __init__(self, llm: LlmCall, limits: NarratorLimits | None = None,
                 activity: ActivityLog | None = None) -> None:
        self.llm = llm
        self.limits = limits or NarratorLimits()
        self.activity = activity
        self.calls = 0
        self._lock = threading.Lock()

    def _call(self, messages: list[dict[str, str]]) -> str:
        with self._lock:
            self.calls += 1
        out = self.llm(messages)
        return out if isinstance(out, str) else str(out or "")

    def _logged_call(self, messages: list[dict[str, str]], *, step: str | None, label: str,
                     note_label: str | None = None) -> str:
        """`_call` + 실행 기록(시작·끝·소견 초안). 기록이 없으면 `_call`과 같다."""
        log = self.activity
        if log is None:
            return self._call(messages)
        safe_append(log, "llm_start", engine="playbook", step=step, label=label)
        t0 = time.monotonic()
        try:
            text = self._call(messages)
        except Exception as e:
            safe_append(log, "llm_end", engine="playbook", step=step, label=label, status="error",
                        elapsed_ms=int((time.monotonic() - t0) * 1000),
                        excerpt=f"{type(e).__name__}: {e}")
            raise
        safe_append(log, "llm_end", engine="playbook", step=step, label=label, status="success",
                    elapsed_ms=int((time.monotonic() - t0) * 1000))
        if note_label is not None and text.strip():
            safe_append(log, "ai_note", engine="playbook", step=step, label=note_label, note=text)
        return text

    def narrate_steps(self, requests: Sequence[StepNarration]) -> dict[str, Opinion]:
        """단계 소견을 병렬로 만든다(요청 수는 호출자가 `max_step_calls` 안으로 자른다)."""
        if not requests:
            return {}
        rendered = {
            r.step: render_step_messages(
                section=r.section,
                focus=r.focus,
                max_sentences=r.max_sentences,
                readings=r.readings,
                realtime_badges=r.realtime_badges,
                max_input_chars=self.limits.max_input_chars,
            )
            for r in requests
        }
        out: dict[str, Opinion] = {}
        pool = ThreadPoolExecutor(
            max_workers=max(1, min(self.limits.max_workers, len(requests))),
            thread_name_prefix="playbook-narrate",
        )
        try:
            futures = {
                r.step: pool.submit(
                    self._logged_call, rendered[r.step], step=r.step,
                    label=f"{r.step} 소견 작성", note_label=f"{r.step} 소견 초안",
                )
                for r in requests
            }
            wait_futures(list(futures.values()), timeout=self.limits.step_timeout_s)
            for r in requests:
                fut = futures[r.step]
                if not fut.done():
                    fut.cancel()
                    out[r.step] = Opinion(
                        dropped_reason=f"소견기 타임아웃 {self.limits.step_timeout_s:g}s",
                        called=True,
                    )
                    continue
                try:
                    text = fut.result()
                except Exception as e:  # noqa: BLE001 — 단계 단위 격리(사유 노출)
                    logger.warning("단계 소견 실패 %s: %s", r.step, e)
                    out[r.step] = Opinion(
                        dropped_reason=f"소견기 실패 — {type(e).__name__}: {e}", called=True
                    )
                    continue
                op = guard_text(
                    text,
                    verdict=r.section.get("verdict"),
                    allowed=source_numbers(rendered[r.step][-1]["content"]),
                    max_sentences=r.max_sentences,
                    max_chars=self.limits.max_chars,
                    rank_guard=True,
                )
                # 실시간 단면 스텝은 「현재값」을 결정적으로 보장한다(§4.0 — LLM이 빠뜨려도).
                if r.realtime_badges and op.text and REALTIME_MARK not in op.text:
                    op.text = f"({REALTIME_MARK} 기준) {op.text}"
                    op.guard.append("실시간 단면 — 「현재값」 표기 보강")
                out[r.step] = op
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        return out

    def synthesize(self, *, top_title: str | None, **render_kwargs: Any) -> Opinion:
        """종합 1회. `render_kwargs`는 `render_synthesis_messages` 인자(max_sentences 제외)."""
        messages = render_synthesis_messages(
            max_sentences=self.limits.synthesis_max_sentences,
            max_input_chars=self.limits.synthesis_max_input_chars,
            **render_kwargs,
        )
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="playbook-synth")
        try:
            fut = pool.submit(self._logged_call, messages, step=None, label="종합 소견 작성")
            wait_futures([fut], timeout=self.limits.synthesis_timeout_s)
            if not fut.done():
                fut.cancel()
                return Opinion(
                    dropped_reason=f"종합기 타임아웃 {self.limits.synthesis_timeout_s:g}s",
                    called=True,
                )
            try:
                text = fut.result()
            except Exception as e:  # noqa: BLE001 — 종합 실패는 서술만 비운다(가설·대응은 코드값)
                logger.warning("종합 소견 실패: %s", e)
                return Opinion(dropped_reason=f"종합기 실패 — {type(e).__name__}: {e}", called=True)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        return guard_text(
            text,
            verdict=None,
            allowed=source_numbers(messages[-1]["content"]),
            max_sentences=self.limits.synthesis_max_sentences,
            max_chars=self.limits.synthesis_max_chars,
            top_title=top_title,
            rank_guard=True,
            top_status=_top_status(render_kwargs.get("hypotheses")),
        )


def _top_status(hypotheses: Any) -> str | None:
    """코드 1순위 가설(순위 그대로 실린 첫 항목)의 상태."""
    if isinstance(hypotheses, Sequence) and hypotheses and isinstance(hypotheses[0], Mapping):
        status = hypotheses[0].get("status")
        return str(status) if status is not None else None
    return None


__all__ = [
    "ANOMALY_TERMS",
    "LlmCall",
    "NORMAL_TERMS",
    "NOT_CONFIGURED",
    "NarratorLimits",
    "Opinion",
    "AllowedItem",
    "POLARITY_DROPPED",
    "PlaybookNarrator",
    "RANK_CLAIM_TERMS",
    "REALTIME_MARK",
    "SYNTH_POLARITY_DROPPED",
    "SourceNumber",
    "StepNarration",
    "guard_text",
    "number_allowed",
    "polarity_conflict",
    "source_numbers",
    "split_sentences",
    "unknown_numbers",
]
