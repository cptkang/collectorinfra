"""U층 드라이버 — 시나리오를 2단 본체에 사용자 프롬프트로 보내고 턴 관측을 모은다.

plans/152 §3.1·§3.3 · W2 · D-323.

한 run 의 수명:
  1. 세션 임시 디렉터리(0700 · 끝나면 지운다 — 캡처·감사 로그에 운영 이름이 들어갈 수 있다)
  2. E1: 목 Open API(스레드) · 게이트웨이(서브프로세스 · 목에 연결) · 가짜 LLM(스레드)
     E2: 게이트웨이(서브프로세스 · 자기 `.env`의 제니퍼) — 가짜 LLM 없이 본체 LLM 설정 그대로
  3. 본체(`scripts.apm_bench._serve` — 관측 수신기 설치) · 2단(`tier2_intent`) 고정 · 기동 검증
  4. 시나리오 일정(`guard.schedule`)대로 턴 송신 — 첫 턴 본문은 `query`뿐(R2) · 멀티턴은
     같은 thread ·
     소스 되묻기가 정답이 아닌 턴에서 오면 대상 자리표의 소스로 자동 답(기록만)
  5. 턴마다 캡처(관측 지점 ①②③) · 게이트웨이 감사 줄(thread_id) · SSE done 을 묶어 `TurnRecord`
  6. 가드(예산·429·타임아웃·지연) — 턴 도중엔 감시 스레드가 감사 줄을 읽어 넘으면
     게이트웨이를 멈춘다
  7. 인벤토리 이름(관문 원이름 대조 집합) — 턴 송신 전에 게이트웨이 목록 도구 2회(`INVENTORY_CALLS`)
  8. P층 훅(`probe_fn`) — 시나리오가 끝난 뒤 게이트웨이를 내리기 전에 같은 예산으로 부른다

판정·위생·산출물 쓰기는 하지 않는다(W3·W4) — `judge_fn`·`probe_fn` 훅만 둔다. 디스크 쓰기는 E1
`out_dir`(디버그 원본)뿐이다.

LLM(E1): `fake`(기본 — 가짜 LLM 이 정답 계획·파싱을 낸다) · `mlx`(127.0.0.1 MLX 서버 — 두 평면
mlx · 루프백 · `mlx_run_blockers` 통과일 때만 · D-240). E2 는 본체 LLM 설정 그대로다.
"""

from __future__ import annotations

import dataclasses
import functools
import json
import os
import re
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from . import gateway as gw
from . import guard as gd
from .record import (
    ERROR_CODES,
    RECORD_VERSION,
    Call,
    Observed,
    Reselect,
    SanitizeChange,
    TurnRecord,
)
from .scenarios import (
    PlaceholderError,
    RegistryInfo,
    Scenario,
    Turn,
    fill_placeholders,
    load_registry_info,
    placeholders_of,
)

KST = ZoneInfo("Asia/Seoul")
PROFILE = "tier2_intent"
EXPECTED_TIER = "intent_orchestration"
ENTRY_MODULE = "scripts.apm_bench._serve"
APM_AGENT = "apm_query"
APM_SYSTEM = "apm"
#: 감시 스레드가 감사 줄을 읽는 간격(초).
MONITOR_INTERVAL_SEC = 0.5
#: 턴 응답 대기 상한(초 — 스트림 청크 간격 상한).
TURN_TIMEOUT_SEC = 300.0
#: 같은 턴에서 소스 자동 답을 보내는 최대 횟수.
AUTO_ANSWER_MAX = 1
#: SSE done 에서 보관하는 키(나머지는 버린다).
DONE_KEYS = ("thread_id", "response", "processing_time_ms", "time_resolution",
             "apm_source_clarification", "apm_source_scope", "disclosures", "plan_summary",
             "clarification")
#: 필수 조건 되묻기 고지(처리기 `_required_text`)의 표지.
REQUIRED_ASK_MARK = "필요한 조건("
#: 라우트 사전 되묻기(done `clarification.kind`) → `clarify_kind`(그 밖 kind 는 원값).
PRE_GATE_KINDS = {"zone_select": "zone"}

#: 인벤토리 수집 도구(BENCH 주체 · 가장 가벼운 목록 도구) — (도구, 인자, 이름 칸).
INVENTORY_CALLS: tuple[tuple[str, dict[str, Any], tuple[str, ...]], ...] = (
    ("apm_instance_map", {}, ("instance_name", "domain_name", "host_name")),
    ("apm_business", {"mode": "list"}, ("business_name", "domain_name")),
)
#: 인벤토리 도구 1회 상한(초) — 도메인마다 1호출 · 벤치 게이트웨이 초당 2회라 운영 규모(수백 도메인)는
#: 수 분 걸린다. 도구 기본 60초면 끊겨 원이름 대조 집합이 빈다(W8 1회차 실측).
INVENTORY_TIMEOUT_SEC = 900.0
LLM_CHOICES = ("fake", "mlx")
#: `--llm mlx` 기본 주소·모델(`scripts/mlx_server.sh` 기본 · 모델 별칭은 서버 `--model`로 매핑).
MLX_DEFAULT_URL = "http://127.0.0.1:8080/v1"
MLX_DEFAULT_MODEL = "default_model"

JudgeFn = Callable[[TurnRecord], Any]
PlanHook = Callable[[Scenario, int, dict[str, Any]], "dict[str, Any] | None"]
CallTool = Callable[[str, dict[str, Any]], dict[str, Any]]
#: P층 훅 — (게이트웨이 도구 호출, 예산 남음?, 턴 레코드) → 무시(호출 측이 결과를 모은다).
ProbeFn = Callable[[CallTool, Callable[[], bool], list[TurnRecord]], Any]


class LLMBlockedError(RuntimeError):
    """LLM 사전 점검 실패 — `--llm mlx` 점검 실패 또는 E2 본체 평면이 과금 평면(D-222). 실 LLM 을
    부르지 않고 아무것도 띄우지 않는다(호출 측 exit 2)."""


#: 드라이버 중단 고정 문구(`RunAbortError.phrase`) — 실행 로그에는 이 문구와 오류 코드만 찍는다
#: (plans/164 W2a — 예외 원문에 인벤토리 오류 사유·로그인 오류 본문·기동 검증 사유가 섞인다).
ABORT_INVENTORY_EMPTY = "인벤토리 이름 0개 — 누출 관문 원이름 대조 집합이 비어 실행하지 않는다"
ABORT_VERIFY_FAILED = "기동 검증 실패(사다리·설정 에코 — 원문은 벤치 세션의 서버 로그)"
ABORT_LOGIN_FAILED = "로그인 실패(--user 계정·비밀번호 확인)"
ABORT_NO_PORT = "본체 포트를 고르지 못했다"
#: 무거운 등급 실행 조건 불충족 건너뜀 사유(`verdict.skipped` — 안전 요약 고정 어휘).
HEAVY_GATE_REASON = "heavy_gate:앞 단계 비정상(타임아웃·429)"
#: 가드 중단 사유(`guard.Guard.abort` 값 — `budget:<소스>`는 따로 본다).
GUARD_ABORTS: frozenset[str] = frozenset({"quota_429", "timeout_ratio", "latency_3x"})
#: 예외 클래스 이름 모양(인벤토리 수집 실패 사유 `<도구>:<예외 이름>` 판별용).
_EXC_NAME = re.compile(r"[A-Z][A-Za-z0-9]*(?:Error|Exception|Timeout|Exit|Group)")


class RunAbortError(RuntimeError):
    """드라이버 중단 — 고정 문구(`ABORT_*`)와 오류 코드(`record.ERROR_CODES` 교집합)만 싣는다.

    호출 측은 `str(exc)` 대신 `phrase`·`codes`로 한 줄을 만든다(plans/164 W2a). 메시지에도 원문을
    넣지 않는다 — 누가 `str(exc)`를 찍어도 운영 이름이 새지 않게.

    Attributes:
        phrase: 고정 문구
        codes: 오류 코드(정렬 · 중복 없음)
    """

    def __init__(self, phrase: str, codes: Sequence[str] = ()) -> None:
        self.phrase = phrase
        self.codes = tuple(sorted({c for c in codes if c in ERROR_CODES}))
        super().__init__(phrase + (f" ({', '.join(self.codes)})" if self.codes else ""))


def guarded_names(names: Iterable[str], sources: Iterable[str],
                  registry_sources: Iterable[str]) -> list[str]:
    """관문 원이름 대조 집합 — 인벤토리 이름 + 레지스트리 밖 소스 id(E2 게이트웨이 `.env`
    `JENNIFER_SOURCES` · plans/164 W1 N-1 — 원이름처럼 치환·대조한다)."""
    known = set(registry_sources)
    return [*names, *(s for s in sources if s not in known)]


def safe_abort_reason(reason: str | None, sources: Iterable[str]) -> str | None:
    """가드 중단 사유 → 고정 어휘(`GUARD_ABORTS` · `budget:<레지스트리 소스 id>` · 그 밖은
    `budget:other`·`other`). None 이면 None — 실행 로그·안전 요약용(plans/164 W2a)."""
    if reason is None:
        return None
    if reason in GUARD_ABORTS:
        return reason
    if reason.startswith("budget:"):
        return reason if reason.removeprefix("budget:") in set(sources) else "budget:other"
    return "other"


def inventory_error_summary(errors: Iterable[str]) -> dict[str, Any]:
    """인벤토리 수집 실패 사유(`<도구>:<사유>`) → 어휘만 남긴 요약.

    Returns:
        `{"codes": {"<도구>:<오류 코드>": 건수}, "exceptions": {"<도구>": 건수}, "other": 건수}` —
        도구는 `INVENTORY_CALLS` · 코드는 `record.ERROR_CODES` 교집합 · 예외 이름은 건수만(이름은
        버린다) · 그 밖(자유 문장 사유)은 건수만
    """
    tools = {tool for tool, _, _ in INVENTORY_CALLS}
    codes: dict[str, int] = {}
    exceptions: dict[str, int] = {}
    other = 0
    for item in errors:
        tool, _, why = str(item).partition(":")
        code = why.split(":", 1)[0].strip()
        if tool in tools and code in ERROR_CODES:
            codes[f"{tool}:{code}"] = codes.get(f"{tool}:{code}", 0) + 1
        elif tool in tools and _EXC_NAME.fullmatch(why.strip()):
            exceptions[tool] = exceptions.get(tool, 0) + 1
        else:
            other += 1
    return {"codes": dict(sorted(codes.items())), "exceptions": dict(sorted(exceptions.items())),
            "other": other}


def _inventory_error_text(errors: Sequence[str]) -> str:
    """진행 줄용 인벤토리 실패 요약(어휘만 — `inventory_error_summary`)."""
    summary = inventory_error_summary(errors)
    parts = [f"{k} {n}" for k, n in summary["codes"].items()]
    parts += [f"{k}:예외 {n}" for k, n in summary["exceptions"].items()]
    if summary["other"]:
        parts.append(f"그 밖 {summary['other']}")
    return ", ".join(parts)


def now_kst() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


# --- 자리표 파일 ------------------------------------------------------------------------


@dataclass(frozen=True)
class Placeholders:
    """자리표 파일(`testdata/apm_bench/placeholders.e1.yaml` 형식).

    Attributes:
        values: 자리표 이름 → 값
        any_values: `"*"` 인자 이름 → 가짜 LLM 값(E1 정답 계획 전용)
        source_default: 소스 자동 답의 기본 소스 id
        source_by_placeholder: 대상 자리표 → 소스 id
        answers: 소스 id → 자동 답 문구
        canaries: 누출 관문 탐지용 canary 값(목 Open API 가 심은 것 — 산출물에 나오면 실패)
    """

    values: dict[str, str]
    any_values: dict[str, Any] = field(default_factory=dict)
    source_default: str | None = None
    source_by_placeholder: dict[str, str] = field(default_factory=dict)
    answers: dict[str, str] = field(default_factory=dict)
    canaries: tuple[str, ...] = ()

    def source_for(self, placeholder: str | None) -> str | None:
        if placeholder and placeholder in self.source_by_placeholder:
            return self.source_by_placeholder[placeholder]
        return self.source_default


def load_placeholders(path: Path) -> Placeholders:
    """자리표 파일을 읽는다(값은 모두 문자열로)."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    sources = data.get("sources") or {}
    return Placeholders(
        values={str(k): str(v) for k, v in (data.get("values") or {}).items()},
        any_values=dict(data.get("any_values") or {}),
        source_default=sources.get("default"),
        source_by_placeholder={str(k): str(v)
                               for k, v in (sources.get("by_placeholder") or {}).items()},
        answers={str(k): str(v) for k, v in (data.get("answers") or {}).items()},
        canaries=tuple(str(v) for v in (data.get("canaries") or ())),
    )


def target_placeholder(target: str) -> str | None:
    """기대 대상 표기 → 자리표 이름(`inst:c` → `inst_c` · `inst_w` 그대로 · 그 밖 None)."""
    if target == "inst_w":
        return target
    kind, _, ref = target.partition(":")
    return f"{kind}_{ref}" if ref else None


def inherited_target(scenario: Scenario, idx: int) -> str | None:
    """대상 승계 턴(`target: ref`)이 잇는 앞 턴 대상 자리표.

    가장 가까운 앞 턴 것이다(판정 `judge_target`과 같은 규칙).
    """
    if scenario.turns[idx].expect.target != "ref":
        return None
    for prev in range(idx - 1, -1, -1):
        name = target_placeholder(scenario.turns[prev].expect.target)
        if name is not None:
            return name
    return None


def turn_placeholders(turn: Turn, values: Mapping[str, str], *,
                      inherited: str | None = None) -> dict[str, str]:
    """턴이 쓰는 자리표 → 값(프롬프트 · 기대 인자 · 대상 · 승계 대상)."""
    names = placeholders_of(turn.prompt)
    for value in turn.expect.args.values():
        if isinstance(value, str):
            names += placeholders_of(value)
    target = target_placeholder(turn.expect.target)
    if target:
        names.append(target)
    if inherited:
        names.append(inherited)
    return {n: values[n] for n in dict.fromkeys(names) if n in values}


def parse_for_turn(scenario: Scenario, idx: int, values: Mapping[str, str]) -> dict[str, Any]:
    """턴의 가짜 입력 파싱 답 — 소스 되묻기 답 턴이면 대기 중인 원 질문 기준(plans/157 G-5).

    답 턴(`reply_to == "source"`)이면 라우트가 답 문장을 원 질문으로 바꿔 다시 파싱한다(`query.py`
    `_apm_source_text_answer` → `turn_query = pending["query"]`). 그래서 기간 표현(`time_slot`)·
    호스트 자리표 필터는 **원 질문 템플릿·문장**에서 뽑고, 기대 창은 **이번(답) 턴의
    `expect.window`**를 쓴다. 답 턴이 연쇄되면 `reply_to` 사슬을 거슬러 최초 질문을 쓴다(대기
    질문은 최초 원 질문이다). 되묻기 뒤라도 답 턴이 아닌 턴은 종전대로 자기 문장 기준이다.

    Raises:
        PlaceholderError: 기준 문장의 자리표가 `values`에 없을 때
    """
    from .fake_llm import parse_from_turn

    turn = scenario.turns[idx]
    first = idx
    while first > 0 and scenario.turns[first].reply_to == "source":
        first -= 1
    asked = scenario.turns[first]
    return parse_from_turn(turn.expect, asked.prompt, fill_placeholders(asked.prompt, values),
                           mapping=values)


# --- SSE done 보관 --------------------------------------------------------------------


class _DoneTee:
    """`response.iter_lines`를 흘려보내며 done 페이로드(필요한 키만)를 잡는다."""

    def __init__(self, response: Any, sink: list[dict[str, Any]]) -> None:
        self._response = response
        self._sink = sink

    def iter_lines(self) -> Iterator[str]:
        for line in self._response.iter_lines():
            if line.startswith("data: ") and '"done"' in line:
                try:
                    payload = json.loads(line[6:])
                except json.JSONDecodeError:
                    payload = None
                if isinstance(payload, dict) and payload.get("type") == "done":
                    self._sink.append({k: payload[k] for k in DONE_KEYS if k in payload})
            yield line


def _bench_client_class() -> type:
    """`ScenarioClient` 하위 클래스 — done 페이로드를 따로 보관한다(`scripts/scenario` 무수정)."""
    from scripts.scenario.client import ScenarioClient

    class BenchClient(ScenarioClient):  # type: ignore[misc, valid-type]
        def __init__(self, config: Any) -> None:
            super().__init__(config)
            self.done: list[dict[str, Any]] = []

        def _consume_sse(self, response: Any, obs: Any, started: float) -> None:
            super()._consume_sse(_DoneTee(response, self.done), obs, started)

    return BenchClient


def _admin_token(port: int) -> tuple[str | None, str | None]:
    """설정 에코용 운영자 토큰 — 본체 설정의 `ADMIN_USERNAME`/`ADMIN_PASSWORD` 로 받는다.

    `/admin/settings/schema` 는 `require_admin_user` 라 인증이 켜진 본체(E2)에서는 토큰 없이
    401 이다(ITAM 벤치 `__main__.py` 와 같은 방식). 인증이 꺼진 본체(E1)는 토큰 없이 열린다.
    """
    from scripts.scenario.client import ClientConfig, ScenarioClient
    from scripts.scenario.runner import resolve_admin_credentials

    user, password = resolve_admin_credentials(None, None)
    if not (user and password):
        return None, "운영자 계정 없음(ADMIN_USERNAME·ADMIN_PASSWORD)"
    probe = ScenarioClient(ClientConfig(port=port))
    try:
        return probe.admin_login(user, password)
    finally:
        probe.close()


# --- 가드 감시-------------------------------------------------------------------------


class GuardFeed:
    """감사 로그를 자기 오프셋으로 끝까지 읽어 가드에 넣는다(본 스레드·감시 스레드 공용 · 잠금)."""

    def __init__(self, tail: gw.AuditTail, guard: gd.Guard,
                 on_abort: Callable[[str], None]) -> None:
        self._tail = tail
        self._guard = guard
        self._on_abort = on_abort
        self._offset = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.entries: list[dict[str, Any]] = []

    def pump(self) -> str | None:
        with self._lock:
            already = self._guard.aborted
            entries, self._offset = self._tail.read(self._offset)
            self.entries += entries
            reason = self._guard.observe(entries)
        if reason and not already:
            self._on_abort(reason)
        return reason

    def observe_latency(self, elapsed_ms: float) -> str | None:
        with self._lock:
            already = self._guard.aborted
            reason = self._guard.observe_latency(elapsed_ms)
        if reason and not already:
            self._on_abort(reason)
        return reason

    def start(self) -> None:
        def loop() -> None:
            while not self._stop.wait(MONITOR_INTERVAL_SEC):
                self.pump()

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(5)


# --- 관측 조립 -------------------------------------------------------------------------


def _apm_tasks(tasks: Any) -> list[dict[str, Any]]:
    return [t for t in tasks or [] if isinstance(t, dict) and t.get("agent") == APM_AGENT]


def sanitize_changes(raw: Sequence[Mapping[str, Any]],
                     final: Sequence[Mapping[str, Any]]) -> list[SanitizeChange]:
    """정제 전(②) APM task 와 디스패치(①) task 의 차이 — 처리기 정제 함수로 다시 계산해 같으면
    `sanitize`, 다르면 `dispatch`(분해 뒤 다른 단계가 바꿈)."""
    from src.orchestration import apm_query as aq

    finals = {t.get("task_id"): t for t in final}
    out: list[SanitizeChange] = []
    for task in raw:
        tid = task.get("task_id")
        after_task = finals.get(tid)
        if after_task is None:
            out.append({"task_id": str(tid), "field": "agent", "before": APM_AGENT, "after": None,
                        "reason": "dispatch"})
            continue
        views = aq.sanitize_views(task.get("views"))
        expected = {
            "views": views,
            "view_args": aq.sanitize_view_args(task.get("view_args"), views),
            "targets": aq.sanitize_targets(task.get("targets")),
            "sources": aq.sanitize_sources(task.get("sources")),
        }
        for name, recomputed in expected.items():
            before, after = task.get(name), after_task.get(name)
            if before == after or (not before and not after):
                continue
            out.append({"task_id": str(tid), "field": name, "before": before, "after": after,
                        "reason": "sanitize" if after == recomputed else "dispatch"})
    return out


def _calls(audit: Sequence[Mapping[str, Any]],
           provenance: Sequence[Mapping[str, Any]]) -> list[Call]:
    """감사 줄(게이트웨이가 실제로 받은 호출) + provenance(처리기 인자·창)를 도구 순서로 맞춘다."""
    unused = list(provenance)
    calls: list[Call] = []
    for entry in audit:
        match = next((p for p in unused if p.get("tool") == entry.get("tool")), None)
        if match is not None:
            unused.remove(match)
        calls.append({
            "tool": str(entry.get("tool")),
            "view": (match or {}).get("view"),
            "hostname": (match or {}).get("hostname"),
            "reference": (match or {}).get("reference"),
            "args": dict((match or {}).get("args") or {}),
            "window": (match or {}).get("window"),
            "rows": entry.get("rows"),
            "error": entry.get("error"),
            "elapsed_ms": int(entry.get("elapsed_ms") or 0),
            "api_calls": entry.get("api_calls"),
            "sources": sorted((entry.get("sources") or {}).keys()),
        })
    for prov in unused:
        calls.append({"tool": str(prov.get("tool")), "view": prov.get("view"),
                      "hostname": prov.get("hostname"), "reference": prov.get("reference"),
                      "args": dict(prov.get("args") or {}),
                      "window": prov.get("window"), "rows": prov.get("rows"), "error": None,
                      "elapsed_ms": None, "api_calls": None, "sources": []})
    return calls


def _clarify(clar: Any, done: Mapping[str, Any], disclosures: Sequence[Mapping[str, Any]],
             response: str) -> tuple[str | None, str | None]:
    """되묻기 종류·문구 — 우선순위: 소스 되묻기(D-322) → 시간 되묻기(input_parser 가 기간을 해석하지
    못해 턴을 끝냄 — done `time_resolution.clarify` + 응답이 그 사유의 되묻기 문구) → 라우트 사전
    되묻기(done `clarification` — 존 역질문 등) → 필수 조건 되묻기(처리기 고지)."""
    from src.domain.query_time import clarify_message

    if isinstance(clar, dict) or clar:
        text = (str(clar.get("question") or clar.get("message") or "")
                if isinstance(clar, dict) else str(clar))
        return "source", text
    tr = done.get("time_resolution")
    code = tr.get("clarify") if isinstance(tr, dict) else None
    if isinstance(code, str) and code and response.strip() == clarify_message(code).strip():
        return "time", response
    pre = done.get("clarification")
    if isinstance(pre, dict) and pre:
        kind = str(pre.get("kind") or "pre_gate")
        return PRE_GATE_KINDS.get(kind, kind), str(pre.get("question") or response)
    required = next((d for d in disclosures if REQUIRED_ASK_MARK in str(d.get("text") or "")), None)
    if required is not None:
        return "required_arg", str(required["text"])
    return None, None


def build_observed(
    *,
    obs: Any,
    done: Mapping[str, Any],
    captures: Sequence[Mapping[str, Any]],
    audit: Sequence[Mapping[str, Any]],
) -> Observed:
    """한 요청의 관측(본체 응답 · 캡처 · 감사 줄) → `Observed`."""
    plan_raw_records = [c for c in captures if c.get("kind") == "apm_plan_raw"]
    results = [c for c in captures if c.get("kind") == "investigation_result"]
    reselects = [c for c in captures if c.get("kind") == "apm_view_reselect"]
    plan_raw = _apm_tasks(plan_raw_records[0]["tasks"]) if plan_raw_records else []
    plan_final = [r["task"] for r in results if r.get("task")]
    metas = [r.get("meta") or {} for r in results]
    selection = next((m.get("selection") for m in metas if m.get("selection")), {}) or {}
    reselect: Reselect | None = None
    if selection or reselects:
        first = reselects[0] if reselects else {}
        reselect = {
            "happened": bool(selection.get("retried")) or bool(reselects),
            "candidates": list(selection.get("candidates") or []),
            "parsed_views": list(first.get("parsed_views") or []),
            "parsed_view_args": dict(first.get("parsed_view_args") or {}),
            "result_views": [v for m in metas for v in m.get("views") or []],
            "reason": selection.get("error") or selection.get("result"),
        }
    selected = [str(i.get("apm_instance_name")) for m in metas
                for i in (m.get("targets") or {}).get("instances") or []]
    # searched·failures 는 건수만 — 원문은 서버·검색어 이름이 섞인 자유 문장이다(plans/164 §4.3-3)
    targets = [
        {"text": t.get("text"), "kind": t.get("kind"), "resolved": bool(t.get("instances")),
         "candidates": list(t.get("suggestions") or []), "selected": selected,
         "searched": len(t.get("searched") or []), "failures": len(t.get("failures") or [])}
        for m in metas for t in (m.get("targets") or {}).get("texts") or []
    ]
    disclosures = [d for d in done.get("disclosures") or [] if isinstance(d, dict)]
    summary = done.get("plan_summary") if isinstance(done.get("plan_summary"), dict) else {}
    agents = [str(t.get("agent")) for t in summary.get("tasks") or [] if isinstance(t, dict)]
    response = str(done.get("response") or getattr(obs, "response", "") or "")
    apm_called = bool(audit)
    clar = done.get("apm_source_clarification")
    clarify_kind, clarify_text = _clarify(clar, done, disclosures, response)
    observed: Observed = {
        "agents": agents,
        "plan_raw": plan_raw,
        "plan_final": plan_final,
        "sanitize_changes": sanitize_changes(plan_raw, plan_final) if plan_raw else [],
        "reselect": reselect,
        "targets": targets,  # type: ignore[typeddict-item]
        "untargeted": any(s.get("mode") for m in metas for s in m.get("inserted_steps") or []),
        "time_resolution": done.get("time_resolution"),
        "calls": _calls(audit, [p for m in metas for p in m.get("provenance") or []]),
        "apm_source_scope": done.get("apm_source_scope"),
        "apm_source_clarification": clar,
        "answer_lines": [line for r in results for line in r.get("answer_lines") or []],
        "disclosures": disclosures,
        "clarify_kind": clarify_kind,
        "clarify_text": clarify_text,
        # 거절 휴리스틱 — APM 담당이 계획에 없고 호출도 없다 · 되묻기(소스·필수 인자·시간·사전
        # 게이트)는 거절이 아니다
        "refused": not apm_called and APM_AGENT not in agents and not clarify_kind,
        "response": response,
        "response_len": len(response),
        "degraded_reason": next((r.get("degraded_reason") for r in results
                                 if r.get("degraded_reason")), None),
        "node_elapsed_ms": dict(getattr(obs, "node_elapsed_ms", {}) or {}),
        "elapsed_ms": int(getattr(obs, "wall_ms", 0) or 0),
        "apm_called": apm_called,
        "error": getattr(obs, "error", None),
    }
    if results:
        # 처리기 보기 호출 실패(보기 · 오류 코드) — 판정이 오류 감사 줄을 보기·해석 단계로 가른다
        # (`judge.error_stages` · plans/164 §4.3-3). 처리기 결과 캡처가 없으면 칸을 두지 않는다
        # (판별 포기 = 현행 `gw_error`)
        observed["view_failures"] = [
            {"view": f.get("view"), "code": _error_code(f.get("reason"))}
            for m in metas for f in m.get("failures") or [] if isinstance(f, dict)
        ]
    return observed


def _error_code(reason: Any) -> str | None:
    """처리기 실패 사유(`"<코드>: <사유>"`) → 오류 코드.

    알려진 코드(`record.ERROR_CODES` — 게이트웨이 오류 어휘 사본 + 처리기 코드)가 아니면 None —
    자유 문장(서버·대상 이름이 섞인다)이나 코드 모양의 이름 조각(소문자 호스트명)은 보관하지 않는다.
    """
    code = str(reason or "").split(":", 1)[0].strip()
    return code if code in ERROR_CODES else None


# --- LLM 평면 · 인벤토리 ------------------------------------------------------------------


def _loopback(url: str) -> bool:
    import ipaddress
    from urllib.parse import urlparse

    host = urlparse(url).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def mlx_blockers(base_url: str, model: str, *,
                 run_blockers: Callable[[Any], list[Any]] | None = None) -> list[str]:
    """`--llm mlx` 사전 점검 — 막는 사유(빈 목록 = 진행).

    본체에 주입할 두 평면(워커·오케스트레이터 = mlx · 같은 주소·모델)이 비과금
    (`external_planes`)이고 루프백이며 MLX 서버 점검(`mlx_run_blockers` — 도달·1토큰 생성)을
    통과해야 한다(D-240).

    Args:
        base_url: MLX 서버 `/v1` 주소
        model: 요청 모델 ID(별칭 `default_model` = 서버 `--model`)
        run_blockers: 점검 함수 주입(테스트) · None 이면 `preflight.mlx_run_blockers`
    """
    from types import SimpleNamespace

    from scripts.scenario.preflight import external_planes, mlx_run_blockers

    out = [f"과금 평면: {p}" for p in external_planes("mlx", "mlx")]
    if not _loopback(base_url):
        out.append(f"MLX 주소가 루프백이 아니다: {base_url}")
        return out
    cfg = SimpleNamespace(
        llm=SimpleNamespace(provider="mlx", mlx_base_url=base_url, mlx_model=model),
        orchestrator=SimpleNamespace(provider="mlx", base_url=base_url, model=model),
    )
    checks = (run_blockers or mlx_run_blockers)(cfg)
    out += [f"MLX 점검 중단: {getattr(c, 'key', c)}={getattr(c, 'observed', '')}" for c in checks]
    return out


def _leaf_error(exc: BaseException) -> str:
    """예외 이름 — MCP 클라이언트가 감싼 `ExceptionGroup`은 첫 잎 예외까지 푼다(원인이 보이게)."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return type(exc).__name__


def collect_inventory(
    call_tool: CallTool, *, hosts: dict[str, list[str]] | None = None,
    sources: dict[str, set[str]] | None = None,
) -> tuple[list[str], int, list[str]]:
    """게이트웨이 목록 도구로 인스턴스·호스트·업무·도메인 이름을 모은다(관문 원이름 대조 집합).

    Args:
        call_tool: 게이트웨이 도구 호출
        hosts: 주면 인스턴스 이름(casefold) → 호스트 이름 목록을 채운다(대상 승계 판정 보조 —
            「그 서버」가 앞 턴 인스턴스의 호스트로 이어졌는지 본다)
        sources: 주면 이름(casefold) → 그 이름이 나온 행의 소스 id 를 채운다(행에 소스 표지
            `source_id`가 있을 때만 · 키트 검사 소스 대응 범위 — plans/164 §4.2 (d)-2)

    Returns:
        (이름 목록 · 정렬 · 중복 없음, 부른 도구 수, 실패 사유) — 도구 하나가 실패해도 나머지는
        모은다(독립 수집 · 실패는 사유로 남긴다)
    """
    names: set[str] = set()
    errors: list[str] = []
    calls = 0
    for tool, args, keys in INVENTORY_CALLS:
        calls += 1
        try:
            envelope = call_tool(tool, dict(args))
        except Exception as exc:  # noqa: BLE001 — 수집 하나의 실패가 run 을 막지 않는다
            errors.append(f"{tool}:{_leaf_error(exc)}")
            continue
        if envelope.get("error"):
            errors.append(f"{tool}:{envelope.get('error')}")
        for row in envelope.get("rows") or []:
            if isinstance(row, dict):
                got = [str(row[k]).strip() for k in keys
                       if isinstance(row.get(k), str) and row[k].strip()]
                names.update(got)
                sid = row.get("source_id")
                if sources is not None and isinstance(sid, str) and sid.strip():
                    for name in got:
                        sources.setdefault(name.casefold(), set()).add(sid.strip())
                inst, host = row.get("instance_name"), row.get("host_name")
                if hosts is not None and isinstance(inst, str) and isinstance(host, str) \
                        and inst.strip() and host.strip():
                    slot = hosts.setdefault(inst.strip().casefold(), [])
                    if host.strip() not in slot:
                        slot.append(host.strip())
    return sorted(names), calls, errors


# --- run --------------------------------------------------------------------------------


@dataclass
class _Ctx:
    """run 1회의 살아 있는 자원."""

    env: str
    session: Path
    registry: RegistryInfo
    placeholders: Placeholders
    client: Any
    capture: Any
    audit: gw.AuditTail
    feed: GuardFeed
    guard: gd.Guard
    fake: Any | None
    gateway: gw.GatewayProc
    plan_hook: PlanHook | None
    judge_fn: JudgeFn | None
    say: Callable[[str], None]
    instance_hosts: dict[str, list[str]] = field(default_factory=dict)


def _send(ctx: _Ctx, query: str, thread_id: str | None) -> tuple[Any, dict[str, Any],
                                                                  list[dict[str, Any]],
                                                                  list[dict[str, Any]], list[str]]:
    """요청 1회 → (Observation, done, 캡처, 감사 줄(그 thread · chat 주체), 가짜 LLM 요청 종류)."""
    cap_mark, audit_mark = ctx.capture.mark(), ctx.audit.mark()
    llm_mark = len(ctx.fake.requests) if ctx.fake is not None else 0
    payload: dict[str, Any] = {"query": query}
    if thread_id:
        payload["thread_id"] = thread_id
    ctx.client.done.clear()
    obs = ctx.client.send("stream", payload)
    done = ctx.client.done[-1] if ctx.client.done else {}
    tid = done.get("thread_id") or thread_id
    captures = [c for c in ctx.capture.collect(cap_mark)
                if c.get("thread_id") in (None, tid)]
    audit = [e for e in ctx.audit.collect(audit_mark, tid)
             if e.get("principal") == gw.CHAT_PRINCIPAL] if tid else []
    kinds = ctx.fake.kinds_since(llm_mark) if ctx.fake is not None else []
    ctx.feed.pump()
    return obs, done, captures, audit, kinds


def _skipped(scenario: Scenario, reason: str, values: Mapping[str, str]) -> list[TurnRecord]:
    return [_base_record(scenario, idx, turn, values, prompt=None)
            | {"verdict": {"skipped": reason}} for idx, turn in enumerate(scenario.turns)]


def _base_record(scenario: Scenario, idx: int, turn: Turn, values: Mapping[str, str],
                 prompt: str | None) -> TurnRecord:
    return {
        "v": RECORD_VERSION,
        "scenario_id": scenario.id,
        "turn": idx,
        "split": scenario.split,
        "covers": list(scenario.covers),
        "heavy": scenario.heavy,
        "was_touch": scenario.was_touch,
        "sensitive": scenario.sensitive,
        "prompt_template": turn.prompt,
        "prompt": prompt or "",
        "placeholders": turn_placeholders(turn, values,
                                          inherited=inherited_target(scenario, idx)),
        "expect": dataclasses.asdict(turn.expect),
        "thread_id": "",
        "sent_at": "",
        "observed": {},
        "verdict": {},
        "probe": [],
        "evidence": {},
    }


def _run_scenario(ctx: _Ctx, scenario: Scenario) -> list[TurnRecord]:
    from .fake_llm import plan_from_expect

    values = ctx.placeholders.values
    records: list[TurnRecord] = []
    thread_id: str | None = None
    for idx, turn in enumerate(scenario.turns):
        if ctx.guard.aborted:
            records += [_base_record(scenario, j, t, values, None)
                        | {"verdict": {"skipped": f"aborted:{ctx.guard.aborted}"}}
                        for j, t in enumerate(scenario.turns) if j >= idx]
            break
        try:
            prompt = fill_placeholders(turn.prompt, values)
            parse = parse_for_turn(scenario, idx, values) if ctx.fake is not None else None
        except PlaceholderError as exc:
            record = _base_record(scenario, idx, turn, values, None)
            record["observed"] = {"error": f"자리표 미등록: {exc}"}
            records.append(record)
            continue
        record = _base_record(scenario, idx, turn, values, prompt)
        extra: dict[str, Any] = {}
        inherited = inherited_target(scenario, idx)
        if inherited and inherited.startswith("inst") and values.get(inherited):
            # 대상 승계 판정 보조 — 앞 턴 인스턴스의 호스트(인벤토리 · 「그 서버」 승계 확인용)
            extra["inherited_hosts"] = list(
                ctx.instance_hosts.get(values[inherited].casefold(), []))
        if ctx.fake is not None:
            plan = plan_from_expect(turn.expect, prompt, mapping=values,
                                    views=ctx.registry.views,
                                    any_values=ctx.placeholders.any_values)
            if ctx.plan_hook is not None:
                plan = ctx.plan_hook(scenario, idx, plan) or plan
            ctx.fake.set_plan(plan)
            ctx.fake.set_parse(parse)
        record["sent_at"] = now_kst()
        obs, done, captures, audit, kinds = _send(ctx, prompt, thread_id)
        thread_id = done.get("thread_id") or thread_id
        extra["llm_kinds"] = kinds
        answers = 0
        while (done.get("apm_source_clarification") and turn.expect.clarify != "source"
               and answers < AUTO_ANSWER_MAX and not ctx.guard.aborted):
            source = ctx.placeholders.source_for(target_placeholder(turn.expect.target))
            answer = ctx.placeholders.answers.get(source or "")
            if not answer:
                extra["source_auto_answer"] = {"source": source, "sent": False,
                                               "why": "답 문구 없음(자리표 파일 answers)"}
                break
            extra["source_auto_answer"] = {
                "source": source, "sent": True,
                "first": {"apm_called": bool(audit), "llm_kinds": kinds,
                          "clarification": done.get("apm_source_clarification")},
            }
            answers += 1
            # 글 답이면 라우트가 질의를 되묻기 원 질문으로 바꿔 다시 파싱한다(`query.py`
            # `_apm_source_text_answer` → `turn_query = pending["query"]`) — 파싱 답은 그대로 둔다
            obs, done, captures, audit, kinds = _send(ctx, answer, thread_id)
            extra["llm_kinds_answer"] = kinds
        record["thread_id"] = thread_id or ""
        record["observed"] = build_observed(obs=obs, done=done, captures=captures, audit=audit)
        extra["plan_summary"] = done.get("plan_summary")
        extra["capture_kinds"] = [c.get("kind") for c in captures]
        record["extra"] = extra  # type: ignore[typeddict-unknown-key]
        if ctx.judge_fn is not None:
            record["verdict"] = ctx.judge_fn(record)
        records.append(record)
    return records


def _measure_baseline(gateway_proc: gw.GatewayProc, audit: gw.AuditTail, feed: GuardFeed,
                      say: Callable[[str], None]) -> float | None:
    """지연 기준선 1회 — 가장 가벼운 도구(`gateway_health` = 소스마다 `/api/domain` 1회).

    게이트웨이 캐시(30초)에 걸리면 호출 0이라 측정에서 뺀다(None). 첫 측정은 본체 기동 전에 한다 —
    본체가 기동 때 같은 도구를 불러 캐시를 채운다(2026-10-12 실측).
    """
    mark = audit.mark()
    try:
        gateway_proc.call_tool("gateway_health", {})
    except Exception as exc:  # noqa: BLE001 — 기준선 실패는 run 메타로만 남긴다
        say(f"      [경고] 지연 기준선 측정 실패: {type(exc).__name__}")
        return None
    entries = [e for e in audit.collect(mark)
               if e.get("tool") == "gateway_health" and e.get("principal") == gw.BENCH_PRINCIPAL]
    feed.pump()
    if not entries or not entries[-1].get("api_calls"):
        return None
    elapsed = float(entries[-1]["elapsed_ms"])
    feed.observe_latency(elapsed)
    return elapsed


#: 벤치 본체 인증 끔 — 벤치 본체는 벤치 전용 인스턴스라 127.0.0.1 에만 연다. 재는 것은 보기·대상·
#: 구간·인자 선택 정확도이고, 익명 사용자는 소스 제한 없음(`allowed_sources=None`)이라 전체 권한
#: 사용자와 같은 경로를 탄다. 운영 계정·비밀번호가 필요 없다(`--user`를 주면 `.env` 인증 그대로).
#: 벤치 본체는 `--user`(인증 유지)여도 루프백에만 연다.
LOOPBACK_ENV = {"API_HOST": "127.0.0.1"}
NO_AUTH_ENV = {"AUTH_ENABLED": "false", **LOOPBACK_ENV}
#: E1 활성 DB — 폐쇄망형 구성(폴스타 존 DB + 제니퍼 · 활성 시스템 2개)을 닮게 한다
#: (plans/157 G-1 (a)). 활성 DB가 0건이면 시스템이 제니퍼 하나라 라우트 존 게이트가 task
#: 게이트로 위임되지 않아, 「…서버별…」 같은 APM 질문이 그래프 전에 존 되묻기로 끝난다(측정
#: 환경 부정합 — 제품 결함 아님). `.env` 와 무관하게 명시 주입해 밀봉하고 설정 에코로 대조한다.
#: E1 은 DB를 조회하지 않는다 — id는 레지스트리 정본의 공동존 두 DB(시나리오 위치어 「김포」·
#: 「여의도」가 가리키는 DB와 같다). E2 는 본체 `.env` 가 정본이라 넣지 않는다.
E1_ENV = {"ACTIVE_DB_IDS": "polestar_cm_gp,polestar_cm_yd"}


def _server_overrides(env: str, session: Path, gateway_proc: gw.GatewayProc,
                      llm_url: str | None, llm_model: str | None = None,
                      keep_auth: bool = False,
                      ) -> tuple[dict[str, str], dict[str, str]]:
    """(검증할 프로파일 주입값, 전체 주입값)."""
    from scripts.scenario.catalog import load_profiles
    from scripts.scenario.runner import ISOLATION_ENV

    from ._serve import CAPTURE_ENV

    expected = {**ISOLATION_ENV, **load_profiles()[PROFILE],
                **(LOOPBACK_ENV if keep_auth else NO_AUTH_ENV),
                **(E1_ENV if env == "e1" else {})}
    overrides = {
        **expected,
        "CHECKPOINT_DB_URL": str(session / "checkpoints.db"),
        CAPTURE_ENV: str(session / "capture.jsonl"),
        # dict 필드는 `.env` 값과 키 단위로 병합된다 — 이 키만 명시한다
        "MCP_SOURCE_ENDPOINTS": json.dumps({APM_SYSTEM: gateway_proc.sse_url}),
        "MCP_SOURCE_TOKENS": json.dumps({APM_SYSTEM: gateway_proc.tokens[gw.CHAT_PRINCIPAL]}),
    }
    if llm_url is not None:
        from .fake_llm import MODEL_ID

        model = llm_model or MODEL_ID
        overrides.update({
            "LLM_PROVIDER": "mlx",
            "LLM_MLX_BASE_URL": llm_url,
            "LLM_MLX_MODEL": model,
            "ORCHESTRATOR_PROVIDER": "mlx",
            "ORCHESTRATOR_BASE_URL": llm_url,
            "ORCHESTRATOR_MODEL": model,
        })
    return expected, overrides


def _body_plane(plane: str) -> str:
    """본체 설정의 LLM provider 이름(E2 — 주입하지 않은 평면) · 못 읽으면 `unknown`."""
    try:
        from src.config import load_config

        cfg = load_config()
        return str(getattr(getattr(cfg, plane), "provider", "") or "unknown").strip().lower()
    except Exception:  # noqa: BLE001 — 기록용이다(판정은 `loop.directional_reasons`가 닫힌 쪽)
        return "unknown"


def _pick_port() -> int:
    from scripts.scenario.server import pick_port

    for _ in range(20):
        port = pick_port()
        if port not in gw.FORBIDDEN_PORTS:
            return port
    raise RunAbortError(ABORT_NO_PORT)


def run(
    scenarios: Sequence[Scenario],
    *,
    env: str,
    placeholders: Placeholders,
    out_dir: Path | None = None,
    view_server_version: str | None = None,
    judge_fn: JudgeFn | None = None,
    plan_hook: PlanHook | None = None,
    probe_fn: ProbeFn | None = None,
    llm: str = "fake",
    mlx_url: str | None = None,
    mlx_model: str | None = None,
    mlx_check: Callable[[str, str], list[str]] | None = None,
    say: Callable[[str], None] = print,
    user_id: str | None = None,
    password: str | None = None,
    inventory_check: Callable[[list[str]], None] | None = None,
    kit_check: Callable[[list[str], dict[str, set[str]], list[str]], None] | None = None,
) -> tuple[list[TurnRecord], dict[str, Any]]:
    """시나리오를 2단 본체로 돌려 턴 레코드와 run 메타를 돌려준다.

    Args:
        scenarios: 돌릴 시나리오(입력 순서 — 무거운 등급은 맨 뒤로 옮긴다)
        env: `e1`(목 · 가짜 LLM) · `e2`(내부망 게이트웨이 · 본체 LLM 설정)
        placeholders: 자리표 파일
        out_dir: E1 디버그 원본 디렉터리(서버·게이트웨이 로그 · 캡처) — E2 는 허용하지 않는다
        view_server_version: 뷰 서버 버전(E2 필수 · `guard.check_view_server_version`)
        judge_fn: 턴 레코드 → verdict(W3 · 없으면 빈 verdict)
        plan_hook: (시나리오, 턴, 정답 계획) → 덮어쓸 계획(E1 오답 주입 · None = 그대로)
        probe_fn: P층 훅 — 시나리오 뒤 게이트웨이를 내리기 전에 (도구 호출, 가드 정상?, 레코드)로
            부른다(BENCH 주체 · 같은 예산 · 정상 = 중단·타임아웃·429 없음)
        llm: E1 LLM — `fake`(가짜 LLM) · `mlx`(127.0.0.1 MLX — `mlx_blockers` 통과일 때만)
        mlx_url · mlx_model: `llm="mlx"` 주소·모델(기본 `MLX_DEFAULT_URL`·`MLX_DEFAULT_MODEL`)
        mlx_check: (주소, 모델) → 막는 사유(테스트 주입 · None = `mlx_blockers`)
        say: 진행 출력
        user_id · password: 인증이 켜진 본체의 질의 로그인 계정(None = 내장 테스트 계정 —
            운영 인증 DB 에는 없을 수 있다)
        inventory_check: 인벤토리 이름 → None(멈출 때는 예외) — 수집 직후 · 턴 송신 전에 부른다
            (plans/164 W1 사용자 정보 충돌 검사 · 예외는 정리 뒤 그대로 올린다)
        kit_check: (인벤토리 이름, 이름 → 소스 id, 수집 실패 사유) → None — `inventory_check` 뒤
            · 턴 송신 전에 부른다(plans/164 §4.2 (d) 실행 전 키트 검사 · 출력만 하고 멈추지 않는다)

    Raises:
        ValueError: 뷰 서버 버전 미달·미입력(E2) · E2 에 `out_dir` · E2 에 `llm="mlx"`
        LLMBlockedError: `llm="mlx"` 사전 점검 실패 · E2 본체 평면 중 하나라도 과금 평면
            (`preflight.external_planes` — 워커 `fabrix·ollama·mlx` · 오케스트레이터 `vllm·mlx`
            밖 · 못 읽음 포함 · D-222). 아무것도 띄우지 않는다. 우회 옵션은 두지 않는다
        RunAbortError: 기동 검증·로그인 실패 · 포트 없음 · E2 인벤토리 이름 0 — 고정 문구와 오류
            코드만 싣는다(plans/164 W2a · 실행 로그에 원문이 남지 않게)
    """
    from scripts.scenario.client import ClientConfig
    from scripts.scenario.runner import (
        DEFAULT_USER_ID,
        DEFAULT_USER_PASSWORD,
        TokenSource,
        jwt_lifetime_sec,
        user_relogin,
    )
    from scripts.scenario.server import ServerHandle, verify_profile

    from ._serve import CaptureTail
    from .fake_llm import FakeLLM

    if env not in ("e1", "e2"):
        raise ValueError(f"env 는 e1·e2 — {env!r}")
    if env == "e2" and out_dir is not None:
        raise ValueError("E2 는 원본을 디스크에 쓰지 않는다(--debug-raw 는 E1 전용)")
    ok, version_label = gd.check_view_server_version(env, view_server_version)
    if not ok:
        raise ValueError(version_label)
    if llm not in LLM_CHOICES:
        raise ValueError(f"llm 은 {'·'.join(LLM_CHOICES)} — {llm!r}")
    if env == "e2" and llm != "fake":
        raise ValueError("--llm mlx 는 E1 전용이다(E2 는 본체 LLM 설정 그대로)")
    mlx_url = mlx_url or MLX_DEFAULT_URL
    mlx_model = mlx_model or MLX_DEFAULT_MODEL
    if llm == "mlx":
        blocked = (mlx_check or mlx_blockers)(mlx_url, mlx_model)
        if blocked:
            raise LLMBlockedError("; ".join(blocked))
    if env == "e2":
        from scripts.scenario.preflight import external_planes

        billed = external_planes(_body_plane("llm"), _body_plane("orchestrator"))
        if billed:
            raise LLMBlockedError(f"E2 본체 과금 평면 {billed} — 실행하지 않는다(D-222 · 비과금 "
                                  "워커 fabrix·ollama·mlx · 오케스트레이터 vllm·mlx)")

    started = time.monotonic()
    meta: dict[str, Any] = {"env": env, "started_at": now_kst(), "view_server_version":
                            version_label, "profile": PROFILE,
                            "llm": llm if env == "e1" else "body",
                            "gateway_settings": {"rate_per_sec": gw.BENCH_RATE_PER_SEC,
                                                 "api_timeout_sec": gw.BENCH_API_TIMEOUT_SEC}}
    registry = load_registry_info()
    plan = gd.schedule(scenarios, env=env)
    meta["schedule"] = {"light": len(plan.light), "heavy": len(plan.heavy),
                        "skipped": dict(plan.skipped)}
    session = Path(tempfile.mkdtemp(prefix="apm_bench_"))
    os.chmod(session, 0o700)
    mock: gw.MockServer | None = None
    gateway_proc: gw.GatewayProc | None = None
    fake: FakeLLM | None = None
    handle: Any = None
    client: Any = None
    feed: GuardFeed | None = None
    records: list[TurnRecord] = []
    guard = gd.Guard(timeout_sec=gw.BENCH_API_TIMEOUT_SEC, http_lines=True,
                     latency_floor_ms=gd.LATENCY_FLOOR_MS if env == "e1" else 0.0)
    try:
        sources = sorted(registry.source_ids)
        if env == "e1":
            import secrets

            mock = gw.start_mock(gw.bench_fixtures(), "bench-mock-" + secrets.token_hex(8),
                                 session / "fixtures")
        else:
            sources = sorted({*sources, *gw.dotenv_sources()})
        known = [s for s in sources if s in registry.source_ids]  # 그 밖(.env 소스)은 수만
        say(f"[1/4] 게이트웨이 기동 — {env} · 소스 {', '.join(known)}"
            + (f" 외 {len(sources) - len(known)}개" if len(sources) > len(known) else "")
            + f" · 초당 {gw.BENCH_RATE_PER_SEC:g}회")
        gateway_proc = gw.start_gateway(session, sources=sources, mock=mock)
        audit = gw.AuditTail(gateway_proc.log_path)

        def on_abort(reason: str) -> None:
            say(f"      [중단] 가드 — {safe_abort_reason(reason, registry.source_ids)} · "
                "게이트웨이를 멈춘다")
            if gateway_proc is not None:
                gateway_proc.stop()

        feed = GuardFeed(audit, guard, on_abort)
        _measure_baseline(gateway_proc, audit, feed, say)
        instance_hosts: dict[str, list[str]] = {}
        name_sources: dict[str, set[str]] = {}
        names, inv_calls, inv_errors = collect_inventory(
            functools.partial(gateway_proc.call_tool, timeout_sec=INVENTORY_TIMEOUT_SEC),
            hosts=instance_hosts, sources=name_sources)
        feed.pump()
        if env == "e2" and not names:
            # 원이름 대조 집합이 비면 누출 관문이 자리표 값만 본다 — 반출본 안전을 보장 못 한다
            raise RunAbortError(ABORT_INVENTORY_EMPTY,
                           [c.split(":", 1)[1] for c in
                            inventory_error_summary(inv_errors)["codes"]])
        guarded = guarded_names(names, sources, registry.source_ids)
        if inventory_check is not None:
            inventory_check(guarded)
        meta["inventory_names"] = guarded  # 관문 원이름 대조 집합 — 호출 측이 run.json 에서 뺀다
        meta["inventory"] = {"calls": inv_calls, "names": len(names), "errors": inv_errors}
        say(f"      인벤토리 이름 {len(names)}개(도구 {inv_calls}회"
            + (f" · 실패 {_inventory_error_text(inv_errors)}" if inv_errors else "") + ")")
        if kit_check is not None:
            kit_check(list(names), name_sources, list(inv_errors))
        llm_url: str | None = None
        llm_model: str | None = None
        if env == "e1" and llm == "fake":
            fake = FakeLLM()
            llm_url = fake.start()
        elif env == "e1":
            llm_url, llm_model = mlx_url, mlx_model
        expected, overrides = _server_overrides(env, session, gateway_proc, llm_url, llm_model,
                                                keep_auth=bool(user_id))
        meta["auth"] = "user_login" if user_id else "off_local_bench"
        # 평면 = 실제로 답한 LLM. 가짜 LLM 은 본체에 mlx 프로토콜로 붙지만 MLX 모델이 아니다 —
        # `fake`로 적어 run 대조(`loop.directional_reasons`)가 MLX 결과로 읽지 않게 한다
        meta["planes"] = ({"worker": "fake", "orchestrator": "fake"} if fake is not None else
                          {"worker": overrides.get("LLM_PROVIDER") or _body_plane("llm"),
                           "orchestrator": overrides.get("ORCHESTRATOR_PROVIDER")
                           or _body_plane("orchestrator")})
        port = _pick_port()
        handle = ServerHandle(profile=PROFILE, env_overrides=overrides, port=port,
                              log_path=session / "server.log", entry_module=ENTRY_MODULE)
        say(f"[2/4] 본체 기동 — 프로파일 {PROFILE} · 포트 {port}")
        handle.start()
        healthy, _ = handle.wait_healthy()  # 운영자 로그인은 본체가 뜬 뒤에만
        admin_token, admin_error = _admin_token(port) if healthy else (None, None)
        status = verify_profile(handle, expected, admin_token, expected_tier=EXPECTED_TIER)
        if admin_error and not status.valid:
            status.reasons.append(admin_error)
        meta["tier"], meta["degraded_reason"] = status.tier, status.degraded_reason
        if not status.valid:
            raise RunAbortError(ABORT_VERIFY_FAILED)
        say(f"      사다리 확정: {status.tier} (사유 {status.degraded_reason})")
        token: str | None = None
        login_id = user_id or DEFAULT_USER_ID
        login_password = (password or "") if user_id else DEFAULT_USER_PASSWORD
        if status.auth_enabled:
            login_client = _bench_client_class()(ClientConfig(port=port))
            try:
                token, error = login_client.login(login_id, login_password)
            finally:
                login_client.close()
            if not token:
                raise RunAbortError(ABORT_LOGIN_FAILED)
        source = TokenSource(
            token=token,
            relogin=(user_relogin(port, login_id, login_password)
                     if status.auth_enabled else None),
            lifetime_sec=jwt_lifetime_sec(), issued_at=time.monotonic())
        client = _bench_client_class()(ClientConfig(
            port=port, token_source=source, server_timeouts=status.server_timeouts,
            timeout_sec=TURN_TIMEOUT_SEC))
        say(f"[3/4] 로그인 {'완료' if status.auth_enabled else '생략(인증 꺼짐)'}")
        ctx = _Ctx(env=env, session=session, registry=registry, placeholders=placeholders,
                   client=client, capture=CaptureTail(session / "capture.jsonl"), audit=audit,
                   feed=feed, guard=guard, fake=fake, gateway=gateway_proc,
                   plan_hook=plan_hook, judge_fn=judge_fn, say=say,
                   instance_hosts=instance_hosts)
        feed.start()
        say(f"[4/4] 시나리오 {len(plan.light)} + 무거운 등급 {len(plan.heavy)}")
        for order, scenario in enumerate(plan.light, 1):
            if guard.aborted:
                records += _skipped(scenario, f"aborted:{guard.aborted}", placeholders.values)
                continue
            if order > 1 and (order - 1) % gd.BASELINE_EVERY == 0:
                _measure_baseline(gateway_proc, audit, feed, say)
            records += _run_scenario(ctx, scenario)
            say(f"      {order:>3}/{len(plan.light)} {scenario.id}")
        for scenario in plan.heavy:
            if not guard.healthy:
                reason = f"aborted:{guard.aborted}" if guard.aborted else HEAVY_GATE_REASON
                records += _skipped(scenario, reason, placeholders.values)
                continue
            records += _run_scenario(ctx, scenario)
            say(f"      무거운 등급 {scenario.id}")
        for sid, reason in plan.skipped.items():
            scenario = next(s for s in scenarios if s.id == sid)
            records += _skipped(scenario, reason, placeholders.values)
        feed.pump()
        if probe_fn is not None:
            mark = audit.mark()
            say("[P층] 빈 결과 판별 · 허용값 탐침")
            probe_fn(gateway_proc.call_tool, lambda: guard.healthy, records)
            feed.pump()
            meta["probe_calls"] = len([e for e in audit.collect(mark)
                                       if e.get("principal") == gw.BENCH_PRINCIPAL])
    finally:
        if feed is not None:
            feed.stop()
        if client is not None:
            client.close()
        if handle is not None:
            handle.stop()
        if fake is not None:
            meta["fake_llm_requests"] = len(fake.requests)
            fake.stop()
        if gateway_proc is not None:
            meta["gateway_pid"] = gateway_proc.pid
            gateway_proc.stop()
        if mock is not None:
            mock.stop()
        if out_dir is not None:
            out_dir.mkdir(parents=True, exist_ok=True)
            for name in ("server.log", "gateway.log", "capture.jsonl"):
                if (session / name).exists():
                    shutil.copy2(session / name, out_dir / name)
        shutil.rmtree(session, ignore_errors=True)
    meta.update({
        "finished_at": now_kst(),
        "elapsed_sec": round(time.monotonic() - started, 1),
        "guard": guard.meta(),
        "aborted": guard.aborted,
        "turns": sum(1 for r in records if not r["verdict"].get("skipped")),
        "apm_observed_turns": sum(1 for r in records if r["observed"].get("apm_called")),
        # 하위 호환 — run 끝 시점 존재 여부뿐(이번 run 의 생성·변경은 `__main__`의 전후 비교 ·
        # `gateway_var`)
        "gateway_var_absent": gw.gateway_var_absent(),
    })
    return records, meta


__all__ = [
    "PROFILE",
    "EXPECTED_TIER",
    "ENTRY_MODULE",
    "INVENTORY_CALLS",
    "LLM_CHOICES",
    "MLX_DEFAULT_URL",
    "MLX_DEFAULT_MODEL",
    "LLMBlockedError",
    "RunAbortError",
    "HEAVY_GATE_REASON",
    "inventory_error_summary",
    "guarded_names",
    "safe_abort_reason",
    "mlx_blockers",
    "collect_inventory",
    "Placeholders",
    "load_placeholders",
    "target_placeholder",
    "inherited_target",
    "turn_placeholders",
    "sanitize_changes",
    "build_observed",
    "GuardFeed",
    "run",
]
