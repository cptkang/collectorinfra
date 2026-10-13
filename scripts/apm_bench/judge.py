"""턴 판정 5축 · 분류 13종 · few-shot 예문 복사 지목 · 집계 · 예문 커버리지 절.

plans/152 §3.4·§3.5 · W3 · D-323.

순수 로직이다 — 네트워크·LLM·파일 쓰기 0. 입력은 드라이버가 만든 `TurnRecord`(`record.py` 계약)와
시나리오 기대값(`scenarios.Expect`)·레지스트리(`scenarios.RegistryInfo` — D-053 정본)다.

판정 축(§3.4):
  ① source  담당 처리기 집합 일치(교차 질의도 집합) + 소스 지목 반영(D-322 — 기대가 있을 때만)
  ② views   기대 보기 집합 vs **처리기가 실제로 조회한 보기 집합**(재선택 뒤 `result_views`,
            재선택이 없으면 디스패치된 `plan_final` 보기). 호출 도구는 비교 대상이 아니다 —
            도구 하나를 여러 보기가 나눠 쓰고(`apm_status_stats` 3보기 · `apm_config` 4보기 ·
            `apm_metrics` 3보기 · `apm_fleet` 2보기) 대상 미지정 첫 홉(인스턴스 목록·순위)이
            호출에 끼어들기 때문이다
  ③ target  자리표 대상 해석 성공·종류 · `untargeted` = 상위 N 경로 · `none` = 대상 없음 ·
            `ref` = 앞 턴
  ④ window  실제 조회 창(게이트웨이 봉투 `window`) 우선, 없으면 요청 시간 해석(`time_resolution`)
  ⑤ args    계획 LLM 이 단 보기 조건(`plan_final[].view_args` — 정제 뒤) 기준. 요구 인자 반영 ·
            요구하지 않은 인자 = 환각(코드·보기 기본값은 제외 — `excluded_arg_reason`)

되묻기가 정답인 턴(`clarify`)·거절이 정답인 턴(`refusal`)은 5축을 `na`로 두고 발생 여부만 판정해
따로 센다. `skipped`(드라이버가 `verdict.skipped`에 미리 적은 e2_skip·aborted)는 판정하지 않는다.

분해 전 되묻기(`PRE_GRAPH_CLARIFY` — 시간 해석 불가로 input_parser 가 턴을 끝냄 · 라우트 사전
게이트의 존 역질문)는 분해·조회가 없어 5축 대부분을 볼 수 없다. 끊긴 단계의 축 하나만 `fail`로
두고 그 단계 코드를 지목한다(few-shot 으로 못 고친다 — 분해 프롬프트에 닿기 전이다). 거절로 세지
않는다.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .record import TurnRecord, Verdict
from .scenarios import ANY_VALUE, PLACEHOLDER_RE, CoverageReport, RegistryInfo, Scenario

AXES: tuple[str, ...] = ("source", "views", "target", "window", "args")
PASS, FAIL, NA = "pass", "fail", "na"
APM_AGENT = "apm_query"
KST = timezone(timedelta(hours=9), "KST")

# --- 구간 판정 허용 오차(분 · 앵커 = 턴 송신 시각 KST) ------------------------------------------
#: 길이 오차 하한(분) — 1분 조각·반올림(`Window.minutes`)을 흡수한다.
WINDOW_LEN_TOL_MIN = 5
#: 길이 오차 비율 — 기대 길이의 10%.
WINDOW_LEN_TOL_RATIO = 0.10
#: 끝 시각이 송신 시각보다 앞설 수 있는 폭 — 롤링 해석은 직전 정시에서 끝난다(최대 59분).
WINDOW_END_TOL_MIN = 60
#: 끝 시각이 송신 시각보다 뒤일 수 있는 폭 — 조회는 송신 뒤(턴 처리 중)에 일어난다.
WINDOW_LATE_SLACK_MIN = 10
#: 시 단위(hourly) 보기 경계 오차 — 게이트웨이가 시 경계로 넓힌다(양끝 최대 1시간).
HOURLY_EDGE_TOL_MIN = 60

# --- 환각 제외(코드가 채우는 인자) ----------------------------------------------------------
#: 처리기가 대상·소스로 채우는 도구 인자(`apm_query` provenance `names`의 비조건 칸 · 3520행대) —
#: 계획 LLM 이 조건에 되풀이해도 환각으로 세지 않는다.
CODE_FILLED_ARGS: frozenset[str] = frozenset(
    {"hostname", "instance_name", "instance_id", "source_ids", "targets"}
)
#: 처리기가 다는 기간 인자(`plan_window` — `reference_time`·`lookback_minutes`·비교 4시각).
WINDOW_ARGS: frozenset[str] = frozenset(
    {"reference_time", "lookback_minutes", "current_start", "current_end", "baseline_start",
     "baseline_end"}
)
#: 비교 창 표지(`time_spec.NOTE_MULTIPLE_PERIODS`).
NOTE_MULTIPLE_PERIODS = "multiple_periods"


# --- 분류 13종(§3.5 표) -------------------------------------------------------------------


@dataclass(frozen=True)
class Category:
    """분류 1종 — §3.5 표 1행(쌍 행은 이름 둘이 같은 행을 나눈다).

    Attributes:
        name: 분류 이름
        row: §3.5 표 행 번호(1~13)
        fewshot_fixable: few-shot 수정 가능(True) · 불가(False) · 일부(None)
        locus: 기본 고칠 곳(턴별 지목이 앞에 붙는다)
    """

    name: str
    row: int
    fewshot_fixable: bool | None
    locus: tuple[str, ...]


#: 대표 분류 우선순위 순서(앞이 대표). 파이프라인 상류 → 하류이고, 상류 실패가 하류 증상을 만들기
#: 때문이다: 환경(게이트웨이 오류) → 담당 → 소스 지목 → 정제가 버림(분해는 맞음 — 보기·인자 증상보다
#: 앞) → 보기 → 인자(예문 복사 지목이 일반 환각보다 구체적) → 대상 → 구간 → 결과 0 → 응답 탈락 →
#: 재선택(정보성 — 결과가 맞아도 붙는다).
CATEGORIES: tuple[Category, ...] = (
    Category("gw_error", 12, False, ("gateway",)),
    Category("source_miss", 1, True, ("F2", "F3")),
    Category("source_select", 7, None, ("sources[].terms",)),
    Category("sanitize_drop", 8, False, ("code:sanitize_task_views",)),
    Category("view_wrong", 2, True, ("F2",)),
    Category("view_partial", 2, True, ("F2",)),
    Category("fewshot_copy", 6, True, ()),
    Category("arg_missing", 4, True, ()),
    Category("arg_hallucination", 5, True, ()),
    Category("target_unresolved", 9, False, ("code:resolve_apm_targets",)),
    Category("target_wrong", 9, False, ("code:resolve_apm_targets",)),
    Category("window_wrong", 10, False, ("code:input_parser", "code:plan_window")),
    Category("empty_result", 11, False, ("window", "target")),
    Category("answer_drop", 13, False, ("code:result_aggregator",)),
    Category("view_retry", 3, True, ("F1", "F4")),
)
CATEGORY_BY_NAME: dict[str, Category] = {c.name: c for c in CATEGORIES}
CATEGORY_ORDER: dict[str, int] = {c.name: i for i, c in enumerate(CATEGORIES)}


@dataclass(frozen=True)
class PreGraphClarify:
    """분해 전 되묻기 1종 — 끊긴 축 · 분류 · 지목 코드(턴별로 분류 기본값을 덮는다).

    Attributes:
        axis: `fail`로 두는 축(나머지는 `na`)
        category: 분류(§3.5 13종 중)
        locus: 지목 위치(분류 기본 위치보다 앞)
    """

    axis: str
    category: str
    locus: tuple[str, ...]


#: 관측 `clarify_kind` → 분해 전 되묻기. 그 밖 라우트 사전 게이트 kind(드라이버가 done
#: `clarification.kind` 원값을 싣는다)는 `PRE_GATE_DEFAULT`.
#: - time: 요청 시간 해석(`resolve_query_time`)이 기간을 못 잡아 `input_parser`가 되묻기로 턴을 끝냄
#:   (`route_after_input_parser`) — 축 ④ 구간 · `window_wrong`(§3.5 10행 「구간」 · 기본 위치
#:   `code:input_parser`)
#: - zone: 라우트 존 역질문 사전 게이트(`_zone_clarification_or_none`)가 분해 전에 폴스타 존을
#:   물음 — 담당 처리기가 정해지지 않음 · 축 ① · `source_miss`(§3.5 1행)이지만 프롬프트가
#:   아니라 라우트 코드가 고칠 곳이다
PRE_GRAPH_CLARIFY: dict[str, PreGraphClarify] = {
    "time": PreGraphClarify("window", "window_wrong", ("code:input_parser",)),
    "zone": PreGraphClarify("source", "source_miss", ("code:route_zone_gate",)),
}
PRE_GATE_DEFAULT = PreGraphClarify("source", "source_miss", ("code:route_pre_gate",))
#: 분해 뒤에 생기는 되묻기 — 분해 전 되묻기가 아니다.
POST_PLAN_CLARIFY: frozenset[str] = frozenset({"source", "required_arg"})


def pre_graph_clarify(kind: Any) -> PreGraphClarify | None:
    """관측 `clarify_kind` → 분해 전 되묻기(아니면 None)."""
    if not isinstance(kind, str) or not kind or kind in POST_PLAN_CLARIFY:
        return None
    return PRE_GRAPH_CLARIFY.get(kind, PRE_GATE_DEFAULT)


# --- F1 예문 파서 ---------------------------------------------------------------------------

_EXAMPLE_RE = re.compile(r"^\s*(?:\((?P<pre>[^)]*)\)\s*)?「(?P<q>[^」]+)」(?P<post>.*)$")
_KV_RE = re.compile(r"^(?P<k>[A-Za-z_][A-Za-z0-9_]*)=(?P<v>\S.*)$")
_TARGET_RE = re.compile(r"^targets에\s*「(?P<t>[^」]+)」$")
_NOTE_RE = re.compile(r"^\((?P<n>[^)]*)\)$")


class ExampleParseError(ValueError):
    """예문 문자열이 「…」 → k=v · k=v 형식이 아니다."""


@dataclass(frozen=True)
class ParsedExample:
    """F1 예문 1건(레지스트리 `views[].examples` 원소).

    Attributes:
        view: 보기 id
        index: 보기 안 예문 번호(**1부터** — locus `example#N`과 같다)
        question: 「…」 안 질문
        args: `→` 뒤 `k=v`(값은 `true`/`false` → bool · 정수 → int · 나머지 문자열)
        targets: `→ targets에 「X」`
        context: 앞 괄호(예 「느린 트랜잭션 목록에 이어」)
        note: 뒤 괄호(예 「이름 없음 = 전체 서비스」)
    """

    view: str
    index: int
    question: str
    args: dict[str, Any] = field(default_factory=dict, hash=False)
    targets: tuple[str, ...] = ()
    context: str = ""
    note: str = ""


def _typed(raw: str) -> Any:
    text = raw.strip()
    if text in ("true", "false"):
        return text == "true"
    if re.fullmatch(r"-?[0-9]+", text):
        return int(text)
    return text


def parse_example(text: str, view: str = "", index: int = 0) -> ParsedExample:
    """예문 문자열 → `ParsedExample`.

    형식: `[(앞 맥락)] 「질문」 [→ k=v · k=v | → targets에 「X」] [(메모)]`.
    화살표가 없으면 인자 없음.

    Raises:
        ExampleParseError: 형식 밖 조각이 남았을 때
    """
    m = _EXAMPLE_RE.match(text)
    if m is None:
        raise ExampleParseError(f"「…」 질문 없음: {text!r}")
    post = m.group("post").strip()
    args: dict[str, Any] = {}
    targets: list[str] = []
    note = ""
    if post.startswith("→"):
        for part in (p.strip() for p in post[1:].split("·")):
            kv = _KV_RE.match(part)
            tg = _TARGET_RE.match(part)
            if kv is not None:
                args[kv.group("k")] = _typed(kv.group("v"))
            elif tg is not None:
                targets.append(tg.group("t"))
            else:
                raise ExampleParseError(f"인자 조각 해석 불가 {part!r}: {text!r}")
    elif post:
        nm = _NOTE_RE.match(post)
        if nm is None:
            raise ExampleParseError(f"질문 뒤 조각 해석 불가 {post!r}: {text!r}")
        note = nm.group("n").strip()
    return ParsedExample(view, index, m.group("q").strip(), args, tuple(targets),
                         (m.group("pre") or "").strip(), note)


def example_table(registry: RegistryInfo) -> dict[str, tuple[ParsedExample, ...]]:
    """레지스트리 apm 보기 전부의 예문 파싱 결과(보기 선언 순 · 예문 번호 1부터)."""
    return {
        view_id: tuple(parse_example(text, view_id, i + 1)
                       for i, text in enumerate(view.examples))
        for view_id, view in registry.views.items()
    }


# --- 값 정규화 ------------------------------------------------------------------------------


def norm_value(value: Any) -> Any:
    """비교용 값 — 불린 `true`/`false` · 정수형 문자열/실수 → 정수 문자열 · 문자열 casefold ·
    목록은 정렬 튜플."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(int(value)) if float(value).is_integer() else str(value)
    if isinstance(value, (list, tuple)):
        return tuple(sorted(str(norm_value(v)) for v in value))
    if isinstance(value, str):
        text = value.strip().casefold()
        if text in ("true", "false"):
            return text
        if re.fullmatch(r"-?[0-9]+(?:\.0+)?", text):
            return str(int(float(text)))
        return text
    return value


def _fill(value: Any, placeholders: Mapping[str, str]) -> Any:
    """기대 값의 자리표를 채운다 — 값이 없는 자리표가 있으면 `"*"`(존재만)."""
    if not isinstance(value, str) or not PLACEHOLDER_RE.search(value):
        return value
    missing = [n for n in PLACEHOLDER_RE.findall(value) if n not in placeholders]
    if missing:
        return ANY_VALUE
    return PLACEHOLDER_RE.sub(lambda m: str(placeholders[m.group(1)]), value)


def grounded(value: Any, prompt: str) -> bool:
    """값이 질문에 근거가 있는가 — 값 토큰이 프롬프트에 있다.

    - 정수: 앞뒤가 숫자가 아닌 같은 숫자(「5개」·「5분」 모두 근거로 본다 — 보수적)
    - 문자열: casefold 부분 일치(「OutOfMemory」 = `OUTOFMEMORY`)
    - 불린: 표면 토큰이 없어 근거를 세지 않는다(False)
    - 목록: 원소 하나라도 근거가 있으면 True
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, (list, tuple)):
        return any(grounded(v, prompt) for v in value)
    text = prompt.casefold()
    if isinstance(value, (int, float)) or (isinstance(value, str)
                                           and re.fullmatch(r"-?[0-9]+", value.strip())):
        token = str(norm_value(value))
        return re.search(rf"(?<![0-9]){re.escape(token)}(?![0-9])", text) is not None
    if isinstance(value, str):
        token = value.strip().casefold()
        return bool(token) and token in text
    return False


# --- 관측 정리 ------------------------------------------------------------------------------


def _apm_tasks(tasks: Any) -> list[dict[str, Any]]:
    return [t for t in (tasks or []) if isinstance(t, dict) and t.get("agent") == APM_AGENT]


def _ordered(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(i for i in items if isinstance(i, str)))


def _view_args_of(task: Mapping[str, Any], known: Iterable[str]) -> dict[str, dict[str, Any]]:
    """task `view_args` → {보기: 조건}.

    평면 묶음(`"*"` 키 · 보기 id 없는 dict)은 task 보기 전부에 편다.
    """
    raw = task.get("view_args")
    if not isinstance(raw, dict):
        return {}
    known_ids = set(known)
    views = [v for v in task.get("views") or [] if isinstance(v, str)]
    nested = any(k in known_ids or k == ANY_VALUE for k in raw)
    flat = raw.get(ANY_VALUE) if nested else raw
    out: dict[str, dict[str, Any]] = {}
    for view in views:
        merged = dict(flat) if isinstance(flat, dict) else {}
        own = raw.get(view) if nested else None
        if isinstance(own, dict):
            merged.update(own)
        if merged:
            out[view] = merged
    if nested:
        for key, value in raw.items():
            if key not in out and key != ANY_VALUE and isinstance(value, dict):
                out[key] = dict(value)
    return out


def _plan_views_args(tasks: Any,
                     known: Iterable[str]) -> tuple[list[str], dict[str, dict[str, Any]]]:
    views: list[str] = []
    args: dict[str, dict[str, Any]] = {}
    for task in _apm_tasks(tasks):
        views += [v for v in task.get("views") or [] if isinstance(v, str)]
        for view, cond in _view_args_of(task, known).items():
            args.setdefault(view, {}).update(cond)
    return _ordered(views), args


def effective_views(observed: Mapping[str, Any], registry: RegistryInfo) -> dict[str, Any]:
    """처리기가 실제로 조회한 보기와 그 조건(판정 ②·⑤의 관측값).

    재선택(`_select_views`)이 일어났으면 그 결과 보기(`result_views`)가 조회 보기다. 조건은 처리기
    `_selected_args`와 같이 — 분해가 고른 보기는 분해 조건, 재선택이 더한 보기는 재선택 조건.
    """
    final_views, final_args = _plan_views_args(observed.get("plan_final"), registry.views)
    reselect = observed.get("reselect") or {}
    happened = bool(reselect.get("happened"))
    result_views = reselect.get("result_views")
    if happened and isinstance(result_views, list):
        views = _ordered(result_views)
        args = {v: dict(final_args[v]) for v in views if v in final_args}
        retry = reselect.get("parsed_view_args")
        if isinstance(retry, dict):
            retry_args = _view_args_of({"views": views, "view_args": retry}, registry.views)
            for view in views:
                if view not in final_views and view in retry_args:
                    args[view] = retry_args[view]
        basis = "reselect.result_views"
    else:
        views, args, basis = final_views, final_args, "plan_final"
    return {"views": views, "args": args, "basis": basis, "planned": final_views,
            "reselected": happened}


def _calls(observed: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [c for c in observed.get("calls") or [] if isinstance(c, dict)]


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _strings(v)]
    return []


def _target_strings(observed: Mapping[str, Any]) -> list[str]:
    """해석·호출에 실제로 쓰인 대상 문자열(해석된 대상 텍스트 · 고른 이름 · 호출 대상 인자).

    후보(`candidates`)는 제안일 뿐 고른 것이 아니라 넣지 않는다. 상위 N 경로(`untargeted`)면 호출
    인자를 넣지 않는다 — 그 호출의 대상은 사용자가 말한 대상이 아니라 순위 첫 홉이 고른
    인스턴스라서, 이름이 우연히 겹치면(자리표 값이 호스트 이름의 일부) 거짓 통과가 된다(통합 Wave
    실측 — 「지난 1시간 동안 제일 느렸던 트랜잭션」이 순위 경로로 갔는데 통과).
    """
    out: list[str] = []
    for t in observed.get("targets") or []:
        if not isinstance(t, dict):
            continue
        if t.get("resolved") or t.get("selected"):
            out += _strings(t.get("text")) + _strings(t.get("selected"))
    if observed.get("untargeted"):
        return out
    for call in _calls(observed):
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        for key in (*CODE_FILLED_ARGS, "service", "business"):
            out += _strings(args.get(key))
        out += _strings(call.get("hostname")) + _strings(call.get("instance"))
    return out


def _contains(haystack: Iterable[str], value: str) -> bool:
    """대상 값이 관측 문자열과 정확히 같거나(casefold) 토큰 경계로 들어 있는가.

    경계 = 앞뒤 문자가 영숫자·`_`·`-`·`/`가 아님 — `was01`이 `was01_b`·`xwas01`에 걸리지 않고,
    짧은 호스트가 FQDN(`was-host01.example.local`)·공백 낀 문장에는 걸린다.
    """
    needle = value.strip().casefold()
    if not needle:
        return False
    bounded = re.compile(rf"(?<![\w\-/]){re.escape(needle)}(?![\w\-/])")
    return any(s.casefold() == needle or bounded.search(s.casefold()) for s in haystack)


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt.replace(tzinfo=KST) if dt.tzinfo is None else dt


# --- 축 ① 소스 ------------------------------------------------------------------------------


def _observed_sources(observed: Mapping[str, Any], registry: RegistryInfo) -> list[str]:
    found: list[str] = []
    for call in _calls(observed):
        found += _strings(call.get("sources"))
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        found += _strings(args.get("source_ids"))
    if not found:
        scope = observed.get("apm_source_scope")
        if isinstance(scope, dict):
            found = _strings(scope.get("ids"))
    if not found:
        for task in _apm_tasks(observed.get("plan_final")):
            found += _strings(task.get("sources"))
    out: list[str] = []
    for sid in found:
        out += sorted(registry.source_ids) if sid == ANY_VALUE else [sid]
    return _ordered(out)


def judge_source(expect: Any, observed: Mapping[str, Any],
                 registry: RegistryInfo) -> tuple[str, dict[str, Any], list[str]]:
    """① 담당 처리기 집합 일치 + 소스 지목(D-322 — 기대 `apm_sources`가 있을 때만 집합 일치)."""
    exp_agents = sorted(set(expect.agents))
    obs_agents = sorted({a for a in observed.get("agents") or [] if isinstance(a, str)})
    detail: dict[str, Any] = {"expected_agents": exp_agents, "observed_agents": obs_agents}
    cats: list[str] = []
    if exp_agents != obs_agents:
        cats.append("source_miss")
    if expect.apm_sources:
        used = _observed_sources(observed, registry)
        detail.update(expected_sources=sorted(expect.apm_sources), observed_sources=sorted(used))
        if set(used) != set(expect.apm_sources):
            cats.append("source_select")
    return (FAIL if cats else PASS), detail, cats


# --- 축 ② 보기 ------------------------------------------------------------------------------


def judge_views(expect: Any, eff: Mapping[str, Any]) -> tuple[str, dict[str, Any], list[str]]:
    """② 집합 일치 · 부분(누락·초과 수) · 재선택 발생(분류 `view_retry` — 축 결과와 별개)."""
    expected = list(expect.views)
    observed = list(eff["views"])
    missing = [v for v in expected if v not in observed]
    extra = [v for v in observed if v not in expected]
    detail = {"expected": expected, "observed": observed, "missing": missing, "extra": extra,
              "basis": eff["basis"], "planned": eff["planned"], "reselected": eff["reselected"]}
    cats: list[str] = []
    if missing or extra:
        cats.append("view_partial" if set(expected) & set(observed) else "view_wrong")
    if eff["reselected"]:
        cats.append("view_retry")
    return (FAIL if (missing or extra) else PASS), detail, cats


# --- 축 ③ 대상 ------------------------------------------------------------------------------

#: 기대 대상 종류 → 받아들이는 관측 `kind`(비거나 `auto`면 종류를 따지지 않는다).
_TARGET_KINDS: dict[str, frozenset[str]] = {
    "host": frozenset({"host", "hostname", "instance"}),
    "inst": frozenset({"instance"}),
    "inst_w": frozenset({"instance"}),
    "biz": frozenset({"business"}),
    "svc": frozenset({"service"}),
}


def target_placeholder(target: str) -> str | None:
    """기대 대상 표기 → 자리표 이름(`inst:1` → `inst_1` · `inst_w` → `inst_w` · 그 밖 None)."""
    if target == "inst_w":
        return "inst_w"
    if ":" in target:
        kind, ref = target.split(":", 1)
        return f"{kind}_{ref}"
    return None


def _target_family(target: str) -> str:
    return "inst_w" if target == "inst_w" else target.split(":", 1)[0]


def _judge_named_target(value: str, family: str,
                        observed: Mapping[str, Any]) -> tuple[str, dict[str, Any], list[str]]:
    texts = [t for t in observed.get("targets") or [] if isinstance(t, dict)]
    hit = [t for t in texts if _contains(_strings(t.get("text")) + _strings(t.get("selected")),
                                         value)]
    detail: dict[str, Any] = {"texts": len(texts), "untargeted": bool(observed.get("untargeted"))}
    if _contains(_target_strings(observed), value):
        kinds = {str(t.get("kind") or "").lower() for t in hit} - {"", "auto"}
        allowed = _TARGET_KINDS.get(family, frozenset())
        if kinds and not kinds & allowed:
            detail["kind_mismatch"] = sorted(kinds)
            return FAIL, detail, ["target_wrong"]
        return PASS, detail, []
    if hit:  # 말한 대상 텍스트는 있었는데 해석 0건
        detail["unresolved"] = True
        return FAIL, detail, ["target_unresolved"]
    others = [t for t in texts if t.get("resolved") or t.get("selected")]
    if observed.get("untargeted") or others:
        detail["other_target"] = True
        return FAIL, detail, ["target_wrong"]
    detail["dropped"] = True
    return FAIL, detail, ["target_unresolved"]


def _is_fleet_view(view: Any) -> bool:
    return getattr(view, "target", "") == "none" and not getattr(view, "required_input", "")


def _host_key(name: str) -> str:
    return name.strip().casefold().split(".", 1)[0]


def judge_target(scenario: Scenario, turn_index: int, observed: Mapping[str, Any],
                 placeholders: Mapping[str, str], eff: Mapping[str, Any],
                 registry: RegistryInfo, *,
                 inherited_hosts: Sequence[str] = ()) -> tuple[str, dict[str, Any], list[str]]:
    """③ 대상.

    - 자리표 대상(`host:N`·`inst:N`·`inst:c|b`·`biz:N`·`svc:N`·`inst_w`): 자리표 값이 해석된 대상
      텍스트·고른 이름·호출 대상 인자에 있으면 통과. 관측 `kind`가 구체 종류인데 기대 종류와 다르면
      `target_wrong`. 값이 대상 텍스트에만 있고 해석 0건이면 `target_unresolved`. 값이 어디에도
      없는데 다른 대상·상위 N 경로로 갔으면 `target_wrong`, 대상 자체가 없으면 `target_unresolved`.
    - `untargeted`: 상위 N 경로 진입(`observed.untargeted`) 또는 대상 텍스트 없이 전 대상 보기
      (레지스트리 `target: none`이고 필수 대상이 없는 보기 — 순위·전 대상 이벤트)만 조회.
    - `none`: 대상 텍스트 없음 · 상위 N 경로 미진입.
    - `ref`: 앞 턴 행 지목. 기대 보기가 참조 보기(`target: reference`)면 그 보기 도구 호출이
      오류 없이 있어야 통과(어느 행인가는 ⑤ `ref` 인자가 본다). 아니면 대상 승계 — 같은
      시나리오의 가장 가까운 앞 턴 자리표 대상 값이 이 턴 해석·호출에 있어야 통과. 앞 턴 대상이
      인스턴스이고 이 턴에 대상 텍스트가 없으면, 그 인스턴스의 호스트(`inherited_hosts` —
      드라이버가 인벤토리에서 찾아 `extra`에 싣는다)로 부른 호출도 승계로 본다(「그 서버」는 앞
      인스턴스가 있는 서버를 가리킨다 · 호스트 비교는 첫 라벨 casefold).
    """
    target = scenario.turns[turn_index].expect.target
    detail: dict[str, Any] = {"expected": target}
    texts = [t for t in observed.get("targets") or [] if isinstance(t, dict)]
    if target == "none":
        ok = not texts and not observed.get("untargeted")
        detail.update(texts=len(texts), untargeted=bool(observed.get("untargeted")))
        return (PASS, detail, []) if ok else (FAIL, detail, ["target_wrong"])
    if target == "untargeted":
        fleet = bool(eff["views"]) and all(_is_fleet_view(registry.views.get(v))
                                           for v in eff["views"])
        ok = bool(observed.get("untargeted")) or (not texts and fleet)
        detail.update(untargeted=bool(observed.get("untargeted")), fleet_views=fleet)
        return (PASS, detail, []) if ok else (FAIL, detail, ["target_wrong"])
    if target == "ref":
        refs = [v for v in scenario.turns[turn_index].expect.views
                if getattr(registry.views.get(v), "target", "") == "reference"]
        if refs:
            tools = {registry.views[v].tool for v in refs}
            ok = any(c.get("tool") in tools and not c.get("error") for c in _calls(observed))
            detail.update(reference_views=refs, called=ok)
            return (PASS, detail, []) if ok else (FAIL, detail, ["target_unresolved"])
        for prev in range(turn_index - 1, -1, -1):
            name = target_placeholder(scenario.turns[prev].expect.target)
            if name is not None:
                break
        else:
            detail["reason"] = "앞 턴 자리표 대상 없음"
            return NA, detail, []
        value = placeholders.get(name)
        detail.update(inherited_from=prev, placeholder=name)
        if not value:
            detail["reason"] = f"자리표 값 없음({name})"
            return NA, detail, []
        axis, sub, cats = _judge_named_target(value, _target_family(
            scenario.turns[prev].expect.target), observed)
        detail.update(sub)
        wanted = {_host_key(h) for h in inherited_hosts if h}
        via = sorted({str(c.get("hostname")) for c in _calls(observed)
                      if c.get("hostname") and _host_key(str(c["hostname"])) in wanted})
        if axis == FAIL and detail.get("dropped") and via:
            detail.pop("dropped")
            detail["via_host"] = via
            return PASS, detail, []
        return axis, detail, cats
    name = target_placeholder(target)
    value = placeholders.get(name or "")
    detail["placeholder"] = name
    if not value:
        detail["reason"] = f"자리표 값 없음({name})"
        return NA, detail, []
    axis, sub, cats = _judge_named_target(value, _target_family(target), observed)
    detail.update(sub)
    return axis, detail, cats


# --- 축 ④ 구간 ------------------------------------------------------------------------------


def _len_tol(minutes: int) -> float:
    return max(WINDOW_LEN_TOL_MIN, WINDOW_LEN_TOL_RATIO * minutes)


def _interval_of(window: Any) -> tuple[datetime, datetime] | None:
    if not isinstance(window, dict):
        return None
    start, end = _parse_dt(window.get("start")), _parse_dt(window.get("end"))
    if start is None or end is None:
        return None
    return start, end


def _mins(delta: timedelta) -> float:
    return delta.total_seconds() / 60


def _match_interval(kind: str, arg: str, start: datetime, end: datetime,
                    anchor: datetime, *, clip: bool) -> tuple[bool, str]:
    """관측 구간 하나가 기대 창(`range:N`·`hourly:*`)에 맞는가 — (통과, 사유)."""
    if clip:  # 해석 구간은 처리기가 기준 시각에서 자른다(`plan_window` · `end = min(end, anchor)`)
        end = min(end, anchor)
    length = _mins(end - start)
    lag = _mins(anchor - end)  # 양수 = 송신 시각보다 앞에서 끝남
    if lag < -WINDOW_LATE_SLACK_MIN:
        return False, f"끝이 송신 시각보다 {-lag:.0f}분 뒤"
    midnight = anchor.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    if kind == "hourly" and arg in ("today", "yesterday"):
        want_start = midnight if arg == "today" else midnight - timedelta(days=1)
        if abs(_mins(start - want_start)) > HOURLY_EDGE_TOL_MIN:
            return False, f"시작 {start.isoformat()} ≠ {want_start.isoformat()}"
        if arg == "today":
            ok = lag <= HOURLY_EDGE_TOL_MIN
            return ok, "" if ok else f"끝이 송신 시각보다 {lag:.0f}분 앞"
        ok = abs(_mins(end - midnight)) <= HOURLY_EDGE_TOL_MIN
        return ok, "" if ok else f"끝 {end.isoformat()} ≠ {midnight.isoformat()}"
    want = int(arg)
    tol = _len_tol(want) + (2 * HOURLY_EDGE_TOL_MIN if kind == "hourly" else 0)
    if abs(length - want) > tol:
        return False, f"길이 {length:.0f}분 ≠ {want}분(허용 ±{tol:.0f})"
    if lag > max(WINDOW_END_TOL_MIN, want):
        return False, f"끝이 송신 시각보다 {lag:.0f}분 앞"
    return True, ""


def judge_window(expect_window: str, observed: Mapping[str, Any],
                 sent_at: Any) -> tuple[str, dict[str, Any], list[str]]:
    """④ 구간(앵커 = 턴 송신 시각 KST).

    관측 구간은 **실제 조회 창**(성공 호출 봉투 `window` = `{start, end, minutes}`)이 정본이고, 호출
    창이 없을 때만 요청 시간 해석(`time_resolution.metric` — 규칙·슬롯이 기간을 잡았을 때)을 쓴다.
    분 단위 상대 기간(「최근 30분」)은 요청 해석(통계)이 직전 정시까지 ⌈N/60⌉시간으로 넓혀지고
    (`minute_widened_to_hour` · D-328) 실제 호출 창은 분 그대로일 수 있어 해석 하나만으로는 판정할
    수 없다.

    - `none`·`default`·`current`: 명시 기간이 생기지 않았고(해석 출처 `default` 또는 해석 없음)
      모든 호출 창이 송신 시각 근처에서 끝난다(과거로 밀린 창 = 지어낸 기간). 참조 호출
      (`reference` — 앞 턴 행 지목)의 창은 그 행 시각 기준이라 지어낸 기간이 아니므로 보지 않는다
    - `range:N`: 길이 N±max(5분, 10%) · 끝이 송신 시각 앞 max(60분, N) 이내(롤링 정시 끝 · 「지난
      3일」 같은 완결 기간의 자정 끝) · 뒤로는 10분 이내
    - `hourly:N`: 위와 같되 길이 오차에 시 경계 확장 2시간을 더한다
    - `hourly:today|yesterday`: 시작이 그날 0시 ±60분 · 끝이 송신 시각(오늘)·오늘 0시(어제) ±60분
    - `compare`: 해석 표지 `multiple_periods` 또는 호출에 비교 두 구간(봉투 `current`·`baseline` /
      인자 `current_start`·`baseline_start`)이 있다
    """
    detail: dict[str, Any] = {"expected": expect_window}
    anchor = _parse_dt(sent_at)
    tr = observed.get("time_resolution") if isinstance(observed.get("time_resolution"),
                                                       dict) else None
    metric = (tr or {}).get("metric") if isinstance((tr or {}).get("metric"), dict) else None
    explicit = metric is not None and metric.get("source") != "default"
    calls = [c for c in _calls(observed) if not c.get("error")]
    windows = [w for w in (_interval_of(c.get("window")) for c in calls) if w is not None]
    detail.update(explicit_period=explicit, call_windows=len(windows),
                  clarify=(tr or {}).get("clarify"))
    if anchor is None:
        detail["reason"] = "송신 시각 없음"
        return NA, detail, []
    kind, _, arg = expect_window.partition(":")
    if kind in ("none", "default", "current"):
        own = [w for w in (_interval_of(c.get("window")) for c in calls if not c.get("reference"))
               if w is not None]
        late = [w for w in own if _mins(anchor - w[1]) > WINDOW_END_TOL_MIN]
        if explicit or late:
            detail["reason"] = ("명시 기간 생김" if explicit else
                                f"과거로 밀린 호출 창 {len(late)}개")
            return FAIL, detail, ["window_wrong"]
        return PASS, detail, []
    if kind == "compare":
        notes = _strings((metric or {}).get("notes"))
        in_call = any(
            (isinstance(c.get("window"), dict)
             and {"current", "baseline"} <= set(c["window"]))
            or (isinstance(c.get("args"), dict)
                and {"current_start", "baseline_start"} <= set(c["args"]))
            for c in calls)
        if NOTE_MULTIPLE_PERIODS in notes or in_call:
            return PASS, detail, []
        detail["reason"] = "비교 두 구간 관측 없음"
        return FAIL, detail, ["window_wrong"]
    observed_intervals: list[tuple[datetime, datetime, bool]] = [(s, e, False) for s, e in windows]
    if not observed_intervals and explicit and metric is not None:
        start, end = _parse_dt(metric.get("start")), _parse_dt(metric.get("end"))
        if start is not None and end is not None:
            observed_intervals.append((start, end, True))
            detail["basis"] = "time_resolution"
    if not observed_intervals:
        detail["reason"] = "관측 구간 없음"
        return FAIL, detail, ["window_wrong"]
    detail.setdefault("basis", "calls")
    reasons = []
    for start, end, clip in observed_intervals:
        ok, why = _match_interval(kind, arg, start, end, anchor, clip=clip)
        if not ok:
            reasons.append(why)
    if reasons:
        detail["reason"] = reasons[0]
        detail["mismatches"] = len(reasons)
        return FAIL, detail, ["window_wrong"]
    return PASS, detail, []


# --- 축 ⑤ 인자 ------------------------------------------------------------------------------


def excluded_arg_reason(view: Any, name: str, value: Any) -> str | None:
    """요구하지 않은 인자가 환각에서 빠지는 사유(없으면 None — 환각).

    근거: 레지스트리 `fixed_args`(도구 고정 인자) · `args[].default`(보기 기본값과 같은 값) ·
    `target_arg`(이름 보기의 대상 목록 — 처리기가 대상 텍스트로 채움) · `reference`(참조 칸 —
    처리기가 앞 결과 행에서 채움) · `CODE_FILLED_ARGS`·`WINDOW_ARGS`(처리기 provenance 의 대상·소스·
    기간 인자 — `apm_query` 3520행대 · `plan_window`).
    """
    if name in CODE_FILLED_ARGS or name in WINDOW_ARGS:
        return "code_filled"
    if view is None:
        return None
    if name in (getattr(view, "fixed_args", None) or {}):
        return "fixed_arg"
    if name and name in (getattr(view, "target_arg", ""), getattr(view, "reference", "")):
        return "code_filled"
    for arg in getattr(view, "args", ()):
        if arg.name == name and arg.default is not None and norm_value(value) == norm_value(
                arg.default):
            return "view_default"
    return None


def _arg_owner(name: str, views: Sequence[str], registry: RegistryInfo) -> str | None:
    for view_id in views:
        view = registry.views.get(view_id)
        if view is not None and any(a.name == name for a in view.args):
            return view_id
    return None


def _value_match(expected: Any, observed: Any) -> bool:
    return expected == ANY_VALUE or norm_value(expected) == norm_value(observed)


def _find_arg(args_by_view: Mapping[str, Mapping[str, Any]], name: str,
              expected: Any) -> tuple[bool, Any]:
    seen: Any = None
    for cond in args_by_view.values():
        if name in cond:
            if _value_match(expected, cond[name]):
                return True, cond[name]
            seen = cond[name]
    return False, seen


def find_fewshot_copy(hallucinated: Sequence[Mapping[str, Any]],
                      examples: Mapping[str, Sequence[ParsedExample]]) -> dict[str, Any] | None:
    """질문 근거 없는 부착 인자 조합이 특정 F1 예문의 인자와 같으면 그 예문을 지목한다.

    규칙: 보기별로 근거 없는(`grounded` False) 환각 쌍 H 를 모은다. 예문 인자 A 가 H 를 모두
    포함(같은 키·같은 정규화 값)하면 후보다. 같은 보기 예문 우선 → 겹치는 쌍 수 많은 순 →
    보기 선언 순 → 예문 번호 순으로 하나를 고른다. H 가 비면(전부 질문에 근거가 있으면) 복사가
    아니다.
    """
    by_view: dict[str, dict[str, Any]] = {}
    for h in hallucinated:
        if not h.get("grounded"):
            by_view.setdefault(str(h["view"]), {})[str(h["arg"])] = h["value"]
    best: tuple[tuple[int, int, int, int], dict[str, Any]] | None = None
    order = {v: i for i, v in enumerate(examples)}
    for view, pairs in by_view.items():
        for ex_view, exs in examples.items():
            for ex in exs:
                if not ex.args or not all(
                        k in ex.args and norm_value(ex.args[k]) == norm_value(v)
                        for k, v in pairs.items()):
                    continue
                key = (0 if ex_view == view else 1, -len(pairs), order[ex_view], ex.index)
                if best is None or key < best[0]:
                    best = (key, {"view": ex_view, "example_index": ex.index,
                                  "args": dict(ex.args), "attached_view": view,
                                  "attached": dict(pairs)})
    return best[1] if best else None


def judge_args(expect: Any, eff: Mapping[str, Any], raw_args: Mapping[str, Mapping[str, Any]],
               prompt: str, placeholders: Mapping[str, str], registry: RegistryInfo,
               examples: Mapping[str, Sequence[ParsedExample]],
               ) -> tuple[str, dict[str, Any], list[str], dict[str, Any] | None]:
    """⑤ 요구 인자 반영(`"*"` = 존재만 · 값은 정규화 비교) · 비요구 인자 부착 = 환각.

    관측값은 계획 LLM 출력(정제 뒤 `plan_final[].view_args` + 재선택이 더한 보기의 조건)이다 —
    코드가 채우는 도구 인자(창·대상·소스·고정·기본값)는 LLM 이 단 것이 아니므로 호출 인자로
    보지 않는다.
    분해 원출력(`plan_raw`)에는 있었는데 정제 뒤 사라진 요구 인자는 `sanitize_drop`으로 돌린다.
    """
    args_by_view: Mapping[str, Mapping[str, Any]] = eff["args"]
    expected = {k: _fill(v, placeholders) for k, v in expect.args.items()}
    views_for_owner = [*expect.views, *eff["views"]]
    missing: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for name, want in expected.items():
        ok, seen = _find_arg(args_by_view, name, want)
        if ok:
            continue
        entry = {"view": _arg_owner(name, views_for_owner, registry), "arg": name,
                 "expected": want, "observed": seen}
        if _find_arg(raw_args, name, want)[0]:
            dropped.append(entry)
        else:
            missing.append(entry)
    hallucinated: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for view_id, cond in args_by_view.items():
        view = registry.views.get(view_id)
        for name, value in cond.items():
            if name in expected or value is None:
                continue
            reason = excluded_arg_reason(view, name, value)
            item = {"view": view_id, "arg": name, "value": value}
            if reason is not None:
                excluded.append({**item, "reason": reason})
            else:
                hallucinated.append({**item, "grounded": grounded(value, prompt)})
    copy = find_fewshot_copy(hallucinated, examples) if hallucinated else None
    cats: list[str] = []
    if dropped:
        cats.append("sanitize_drop")
    if copy is not None:
        cats.append("fewshot_copy")
    if missing:
        cats.append("arg_missing")
    if hallucinated:
        cats.append("arg_hallucination")
    detail = {"expected": expected, "missing": missing, "sanitize_dropped": dropped,
              "hallucinated": hallucinated, "excluded": excluded}
    axis = FAIL if (missing or dropped or hallucinated) else PASS
    return axis, detail, cats, copy


# --- 부가: 결과·응답 -------------------------------------------------------------------------


def _probe_status(record: Mapping[str, Any], index: int) -> str | None:
    for row in record.get("probe") or []:
        if (isinstance(row, dict) and row.get("kind") == "empty_check"
                and row.get("call_index") == index):
            return str(row.get("status"))
    return None


def judge_results(record: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """게이트웨이 오류 · 행 0(P층 대조 반영) · 판정 줄 탈락.

    - `gw_error`: 호출 하나라도 `error`
    - `empty_result`: 오류 없는 호출이 있고 그 행 합이 0. 행 0 호출이 전부 P층 `no_data`(같은
      인자 직접 재호출도 0행)면 데이터 없음으로 보고 붙이지 않는다
    - `answer_drop`: 행 > 0 · 처리기 판정 줄(`answer_lines`)이 있는데 그중 하나라도 최종 응답에 없다
      (조합 탈락 — `result_aggregator`). 처리기가 판정 줄을 내지 않는 보기(목록 등)는 대상이 아니다
    """
    observed = record.get("observed") or {}
    calls = _calls(observed)
    errors = [str(c.get("error")) for c in calls if c.get("error")]
    ok_calls = [(i, c) for i, c in enumerate(calls) if not c.get("error")]
    counted = [(i, c) for i, c in ok_calls if isinstance(c.get("rows"), int)]
    rows = sum(int(c["rows"]) for _, c in counted)
    detail: dict[str, Any] = {"calls": len(calls), "errors": errors, "rows": rows}
    cats: list[str] = []
    if errors:
        cats.append("gw_error")
    zero = [i for i, c in counted if c["rows"] == 0]
    if counted and rows == 0:
        statuses = [_probe_status(record, i) for i in zero]
        detail["probe"] = statuses
        if not all(s == "no_data" for s in statuses):
            cats.append("empty_result")
    lines = [s for s in observed.get("answer_lines") or [] if isinstance(s, str) and s.strip()]
    if rows > 0 and lines:
        body = re.sub(r"\s+", " ", str(observed.get("response") or ""))
        lost = [s for s in lines if re.sub(r"\s+", " ", s.strip()) not in body]
        detail["answer_lines"] = len(lines)
        detail["answer_lost"] = len(lost)
        if lost:
            cats.append("answer_drop")
    return detail, cats


# --- 턴 판정 ---------------------------------------------------------------------------------


def _loci(cat: str, detail: Mapping[str, Any], copy: Mapping[str, Any] | None) -> list[str]:
    """분류별 지목 위치 — 턴 근거로 좁힌 것이 앞, 기본 위치가 뒤."""
    out: list[str] = []
    views = detail.get("views") or {}
    args = detail.get("args") or {}
    if cat in ("view_wrong", "view_partial"):
        out += [f"F1:{v}" for v in views.get("missing") or []]
    elif cat == "arg_missing":
        out += [f"F1:{m['view']}:args.{m['arg']}" for m in args.get("missing") or [] if m["view"]]
    elif cat == "arg_hallucination":
        out += [f"F1:{h['view']}:args.{h['arg']}" for h in args.get("hallucinated") or []]
    elif cat == "fewshot_copy" and copy is not None:
        out.append(f"F1:{copy['view']}:example#{copy['example_index']}")
    elif cat == "source_select":
        out += [f"sources:{s}" for s in (detail.get("source") or {}).get("expected_sources") or []]
    return list(dict.fromkeys([*out, *CATEGORY_BY_NAME[cat].locus]))


def _rank(cats: Iterable[str]) -> list[str]:
    return sorted(dict.fromkeys(cats), key=lambda c: CATEGORY_ORDER[c])


def _finish(axes: dict[str, str], detail: dict[str, Any], cats: list[str],
            copy: dict[str, Any] | None, *, turn_pass: bool,
            expected_clarify: bool = False, expected_refusal: bool = False,
            override: PreGraphClarify | None = None) -> Verdict:
    """분류 순위·지목 위치를 붙여 `Verdict`를 만든다.

    `override`(분해 전 되묻기)가 있으면 그 분류의 few-shot 수정 가능을 False 로 · 지목 위치를 그
    코드 앞에 둔다 — 턴별 값은 `axis_detail.categories[분류]`에 남고 집계(`summarize`)가 그것을
    본다.
    """
    ranked = _rank(cats)
    per: dict[str, dict[str, Any]] = {}
    for c in ranked:
        fixable, locus = CATEGORY_BY_NAME[c].fewshot_fixable, _loci(c, detail, copy)
        if override is not None and c == override.category:
            fixable, locus = False, list(dict.fromkeys([*override.locus, *locus]))
        per[c] = {"fewshot_fixable": fixable, "locus": locus}
    detail["categories"] = per
    locus_all = list(dict.fromkeys(loc for c in ranked for loc in per[c]["locus"]))
    return Verdict(
        axes=axes, axis_detail=detail, turn_pass=turn_pass,
        expected_clarify=expected_clarify, expected_refusal=expected_refusal,
        categories=ranked,
        fewshot_fixable=per[ranked[0]]["fewshot_fixable"] if ranked else None,
        locus=locus_all, fewshot_copy=copy if "fewshot_copy" in ranked else None, skipped=None,
        unjudged=detail.get("unjudged"),
    )


def _na_axes() -> dict[str, str]:
    return {a: NA for a in AXES}


def judge_turn(scenario: Scenario, turn_index: int, record: Mapping[str, Any],
               registry: RegistryInfo, *,
               examples: Mapping[str, Sequence[ParsedExample]] | None = None) -> Verdict:
    """턴 하나 → `Verdict`(5축 · 분류 · 지목 위치 · 예문 복사).

    - `record.verdict.skipped`가 있으면(드라이버 — e2_skip·aborted) 판정하지 않는다
    - 턴 자체 실패(`observed.error`) · APM 을 기대했는데 호출도 분해 관측도 0 → 판정 불가
      (`axis_detail.unjudged` · 5축 `na` · `turn_pass` False). 분해에 다른 담당만 있으면(APM 미배정)
      관측이 있는 것이라 판정한다(`source_miss`)
    - 되묻기·거절이 정답인 턴: 발생 여부만 `turn_pass`로 판정(5축 `na`). 실패하면 인자·담당 분류를
      참고로 붙인다(예: 필수 인자를 예문 값으로 채워 되묻지 않음 → `fewshot_copy`)
    - 정답이 아닌 되묻기·거절이 났으면 `axis_detail.unexpected`에 적고 턴은 실패다. 단, 소스
      되묻기(`source`)는 드라이버가 답하고 진행하므로 실패가 아니다(시나리오 머리 주석)
    - 분해 전 되묻기(`pre_graph_clarify` — 시간·존 등): 끊긴 단계 축 하나만 `fail` · 그 분류 하나 ·
      지목 코드 앞 · few-shot 수정 불가. 정답 되묻기와 종류가 같으면 통과다. 거절로 세지 않는다
    """
    preset = (record.get("verdict") or {}).get("skipped")
    if preset:
        return Verdict(axes=_na_axes(), axis_detail={}, turn_pass=False,
                       expected_clarify=False, expected_refusal=False, categories=[],
                       fewshot_fixable=None, locus=[], fewshot_copy=None, skipped=str(preset))
    expect = scenario.turns[turn_index].expect
    observed: Mapping[str, Any] = record.get("observed") or {}
    placeholders: Mapping[str, str] = record.get("placeholders") or {}
    prompt = str(record.get("prompt") or "")
    examples = examples if examples is not None else example_table(registry)
    apm_expected = APM_AGENT in expect.agents
    detail: dict[str, Any] = {}

    if observed.get("error"):
        detail["unjudged"] = f"turn_error:{observed.get('error')}"
        return _finish(_na_axes(), detail, [], None, turn_pass=False,
                       expected_clarify=bool(expect.clarify),
                       expected_refusal=bool(expect.refusal))
    if (apm_expected and not expect.clarify and not observed.get("apm_called")
            and not observed.get("agents") and not observed.get("plan_final")
            and not observed.get("clarify_kind") and not observed.get("refused")):
        detail["unjudged"] = "no_observation"
        return _finish(_na_axes(), detail, [], None, turn_pass=False)

    pre = pre_graph_clarify(observed.get("clarify_kind"))
    if pre is not None:
        kind = str(observed.get("clarify_kind"))
        got = expect.clarify == kind
        detail["pre_graph_clarify"] = {"kind": kind, "expected": expect.clarify, "pass": got}
        axes = _na_axes()
        if got:
            return _finish(axes, detail, [], None, turn_pass=True, expected_clarify=True)
        axes[pre.axis] = FAIL
        if not (expect.clarify or expect.refusal):
            detail["unexpected"] = f"clarify:{kind}"
        return _finish(axes, detail, [pre.category], None, turn_pass=False,
                       expected_clarify=bool(expect.clarify),
                       expected_refusal=bool(expect.refusal), override=pre)

    eff = effective_views(observed, registry)
    raw_views, raw_args = _plan_views_args(observed.get("plan_raw"), registry.views)
    src_axis, detail["source"], src_cats = judge_source(expect, observed, registry)
    view_axis, detail["views"], view_cats = judge_views(expect, eff)
    arg_axis, detail["args"], arg_cats, copy = judge_args(
        expect, eff, raw_args, prompt, placeholders, registry, examples)
    # 정제가 버림 — 분해 원출력에는 기대 보기·APM 담당이 있었는데 디스패치에서 사라졌다
    raw_agents = {t.get("agent") for t in observed.get("plan_raw") or [] if isinstance(t, dict)}
    dropped_views = [v for v in detail["views"]["missing"] if v in raw_views]
    dropped_agent = (apm_expected and APM_AGENT in raw_agents
                     and APM_AGENT not in (observed.get("agents") or []))
    if dropped_views or dropped_agent:
        detail["views"]["sanitize_dropped"] = dropped_views
        detail["source"]["sanitize_dropped_agent"] = dropped_agent
        view_cats = ["sanitize_drop", *view_cats]

    if expect.clarify or expect.refusal:
        if expect.clarify:
            got = observed.get("clarify_kind") == expect.clarify or (
                expect.clarify == "source" and bool(observed.get("apm_source_clarification")))
            detail["clarify"] = {"expected": expect.clarify,
                                 "observed": observed.get("clarify_kind"), "pass": got}
        else:
            got = bool(observed.get("refused")) and not observed.get("apm_called")
            detail["refusal"] = {"expected": expect.refusal, "refused": bool(
                observed.get("refused")), "apm_called": bool(observed.get("apm_called")),
                "pass": got}
        cats = [] if got else [c for c in [*src_cats, *arg_cats]
                               if c in ("source_miss", "fewshot_copy", "arg_hallucination")]
        return _finish(_na_axes(), detail, cats, copy, turn_pass=got,
                       expected_clarify=bool(expect.clarify),
                       expected_refusal=bool(expect.refusal))

    tgt_axis, detail["target"], tgt_cats = judge_target(
        scenario, turn_index, observed, placeholders, eff, registry,
        inherited_hosts=(record.get("extra") or {}).get("inherited_hosts") or ())
    # 자리표 값이 없어 대상 축을 못 매긴 턴은 통과가 아니라 판정 불가다(정확도 분모에서 빠진다)
    missing_value = tgt_axis == NA and str(detail["target"].get("reason") or "").startswith(
        "자리표 값 없음")
    if missing_value:
        detail["unjudged"] = f"placeholder_missing:{detail['target'].get('placeholder')}"
    win_axis, detail["window"], win_cats = judge_window(expect.window, observed,
                                                       record.get("sent_at"))
    detail["results"], res_cats = judge_results(record)
    axes = {"source": src_axis, "views": view_axis, "target": tgt_axis, "window": win_axis,
            "args": arg_axis}
    unexpected = None
    if observed.get("refused"):
        unexpected = "refusal"
    elif observed.get("clarify_kind") and observed.get("clarify_kind") != "source":
        unexpected = f"clarify:{observed.get('clarify_kind')}"
    if unexpected:
        detail["unexpected"] = unexpected
    judged = [v for v in axes.values() if v != NA]
    turn_pass = (bool(judged) and all(v == PASS for v in judged) and unexpected is None
                 and not missing_value)
    cats = [*src_cats, *view_cats, *arg_cats, *tgt_cats, *win_cats, *res_cats]
    return _finish(axes, detail, cats, copy, turn_pass=turn_pass)


# --- run 단위 -------------------------------------------------------------------------------


def _expect_of(record: Mapping[str, Any]) -> Mapping[str, Any]:
    exp = record.get("expect")
    return exp if isinstance(exp, Mapping) else {}


def _is_skipped(record: Mapping[str, Any]) -> bool:
    return bool((record.get("verdict") or {}).get("skipped"))


def _is_unjudged(record: Mapping[str, Any]) -> bool:
    return bool(((record.get("verdict") or {}).get("axis_detail") or {}).get("unjudged"))


def run_invalid(records: Sequence[Mapping[str, Any]]) -> bool:
    """run 무효(D-219 ③) — APM 호출을 기대한 턴(건너뜀·되묻기·거절 정답 제외)이 있는데 그중 APM
    도구 호출이 관측된 턴이 0이다. 참이면 호출 측이 exit 1 한다(측정 배선 고장 — 판정 무의미)."""
    eligible = [
        r for r in records
        if not _is_skipped(r) and APM_AGENT in (_expect_of(r).get("agents") or [])
        and not _expect_of(r).get("clarify") and not _expect_of(r).get("refusal")
    ]
    return bool(eligible) and not any((r.get("observed") or {}).get("apm_called")
                                      for r in eligible)


def _rate(passed: int, total: int) -> float | None:
    return round(passed / total, 4) if total else None


def _axis_table(verdicts: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for axis in AXES:
        c = Counter(str((v.get("axes") or {}).get(axis, NA)) for v in verdicts)
        out[axis] = {"pass": c[PASS], "fail": c[FAIL], "na": c[NA],
                     "accuracy": _rate(c[PASS], c[PASS] + c[FAIL])}
    return out


def _turn_table(verdicts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    passed = sum(1 for v in verdicts if v.get("turn_pass"))
    return {"pass": passed, "total": len(verdicts), "accuracy": _rate(passed, len(verdicts))}


def _ref(record: Mapping[str, Any]) -> str:
    return f"{record.get('scenario_id')}#{record.get('turn')}"


def summarize(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """판정이 붙은 레코드 → 집계(W4 `report.md` 원천).

    정확도 분모는 **일반 턴**(건너뜀·판정 불가·되묻기 정답·거절 정답 제외)이다. 되묻기·거절은 따로
    센다. 분류는 턴의 전 분류(`categories`)와 대표 분류(첫째)를 각각 센다.
    """
    skipped = [r for r in records if _is_skipped(r)]
    unjudged = [r for r in records if not _is_skipped(r) and _is_unjudged(r)]
    live = [r for r in records if not _is_skipped(r) and not _is_unjudged(r)]
    clarify = [r for r in live if (r.get("verdict") or {}).get("expected_clarify")]
    refusal = [r for r in live if (r.get("verdict") or {}).get("expected_refusal")]
    normal = [r for r in live if r not in clarify and r not in refusal]
    verdicts = [r.get("verdict") or {} for r in normal]

    by_split: dict[str, Any] = {}
    for split in sorted({str(r.get("split") or "") for r in normal}):
        vs = [r.get("verdict") or {} for r in normal if str(r.get("split") or "") == split]
        by_split[split] = {"turn": _turn_table(vs), "axes": _axis_table(vs)}
    by_cov: dict[str, Any] = {}
    for r in normal:
        for cov in dict.fromkeys(str(c).split(".", 1)[0] for c in r.get("covers") or []):
            slot = by_cov.setdefault(cov, {"pass": 0, "total": 0})
            slot["total"] += 1
            slot["pass"] += 1 if (r.get("verdict") or {}).get("turn_pass") else 0
    for slot in by_cov.values():
        slot["accuracy"] = _rate(slot["pass"], slot["total"])

    scored = [r for r in live if (r.get("verdict") or {}).get("categories")]
    all_cats = Counter(c for r in scored for c in r["verdict"]["categories"])
    primary = Counter(r["verdict"]["categories"][0] for r in scored)
    fewshot_locus: Counter[str] = Counter()
    code_fixes: list[dict[str, Any]] = []
    for r in scored:
        per = (r["verdict"].get("axis_detail") or {}).get("categories") or {}
        for cat in r["verdict"]["categories"]:
            info = per.get(cat) or {}
            if info.get("fewshot_fixable", CATEGORY_BY_NAME[cat].fewshot_fixable) is False:
                code_fixes.append({"turn": _ref(r), "category": cat,
                                   "locus": list(info.get("locus") or [])})
            else:
                fewshot_locus.update(info.get("locus") or [])
    copies = [{"turn": _ref(r), **r["verdict"]["fewshot_copy"]} for r in scored
              if r["verdict"].get("fewshot_copy")]

    def _side(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        passed = [r for r in rows if (r.get("verdict") or {}).get("turn_pass")]
        return {"pass": len(passed), "total": len(rows), "accuracy": _rate(len(passed), len(rows)),
                "failed": [_ref(r) for r in rows if r not in passed]}

    return {
        "counts": {"records": len(records), "skipped": len(skipped), "unjudged": len(unjudged),
                   "normal": len(normal), "clarify": len(clarify), "refusal": len(refusal)},
        "run_invalid": run_invalid(records),
        "turn": _turn_table(verdicts),
        "axes": _axis_table(verdicts),
        "by_split": by_split,
        "by_cov": dict(sorted(by_cov.items())),
        "categories": dict(all_cats.most_common()),
        "primary_categories": dict(primary.most_common()),
        "fewshot_locus": dict(fewshot_locus.most_common()),
        "code_fixes": code_fixes,
        "fewshot_copies": copies,
        "clarify": _side(clarify),
        "refusal": _side(refusal),
        "unexpected": [{"turn": _ref(r), "kind": r["verdict"]["axis_detail"]["unexpected"]}
                       for r in normal
                       if (r["verdict"].get("axis_detail") or {}).get("unexpected")],
        "unjudged": [{"turn": _ref(r), "reason": r["verdict"]["axis_detail"]["unjudged"]}
                     for r in unjudged],
        "skipped": [{"turn": _ref(r), "reason": r["verdict"]["skipped"]} for r in skipped],
    }


# --- coverage.md 덧붙임 절 — 예문 유무 × 실패율 ------------------------------------------------

Section = Callable[[CoverageReport], str]


def _prescription(has_example: bool, failures: int) -> str:
    if not failures:
        return ""
    return "예문 교정 또는 결정적 처리 후보" if has_example else "예문 추가"


def make_example_section(records: Sequence[Mapping[str, Any]], registry: RegistryInfo, *,
                         examples: Mapping[str, Sequence[ParsedExample]] | None = None,
                         ) -> Section:
    """판정 결과 → `scenarios.render_coverage_md(extra_sections=…)`에 넣는 절 하나.

    보기·보기 인자마다 F1 예문 수 · 기대 턴 수 · 실패 수(보기 = 누락·초과 선택 · 인자 = 누락·환각) ·
    처방(「예문 없음 + 실패」 = 예문 추가 · 「예문 있음 + 실패」 = 예문 교정 또는 결정적 처리
    후보)과 예문 복사 지목 표를 낸다. **dev 일반 턴만** 센다(건너뜀·판정 불가·되묻기·거절 정답
    제외) — holdout 실패를 예문 위치로 노출하지 않는다(권고 ③ · holdout 은 판정 턴 수만 적는다).
    """
    table = examples if examples is not None else example_table(registry)
    normal = [r for r in records if not _is_skipped(r) and not _is_unjudged(r)
              and not (r.get("verdict") or {}).get("expected_clarify")
              and not (r.get("verdict") or {}).get("expected_refusal")]
    live = [r for r in normal if str(r.get("split") or "") == "dev"]
    held = len(normal) - len(live)
    view_expected: Counter[str] = Counter()
    view_missing: Counter[str] = Counter()
    view_extra: Counter[str] = Counter()
    arg_expected: Counter[tuple[str, str]] = Counter()
    arg_missing: Counter[tuple[str, str]] = Counter()
    arg_halluc: Counter[tuple[str, str]] = Counter()
    copies: Counter[tuple[str, int]] = Counter()
    for r in live:
        verdict = r.get("verdict") or {}
        detail = verdict.get("axis_detail") or {}
        views = detail.get("views") or {}
        args = detail.get("args") or {}
        exp_views = list(views.get("expected") or [])
        view_expected.update(exp_views)
        view_missing.update(views.get("missing") or [])
        view_extra.update(views.get("extra") or [])
        for name in (args.get("expected") or {}):
            owner = _arg_owner(name, exp_views, registry)
            if owner:
                arg_expected[(owner, name)] += 1
        for m in [*(args.get("missing") or []), *(args.get("sanitize_dropped") or [])]:
            if m.get("view"):
                arg_missing[(m["view"], m["arg"])] += 1
        for h in args.get("hallucinated") or []:
            arg_halluc[(h["view"], h["arg"])] += 1
        copy = verdict.get("fewshot_copy")
        if copy:
            copies[(copy["view"], int(copy["example_index"]))] += 1

    def section(_report: CoverageReport) -> str:
        lines = ["## 예문 커버리지 × 실패율 (F1 few-shot)", "",
                 f"dev 판정 턴 {len(live)}개 기준(holdout {held}개는 수정 대상이 아니라 "
                 "세지 않는다 — 권고 ③). 처방: 「예문 없음 + 실패」 = 예문 추가 · "
                 "「예문 있음 + 실패」 = 예문 교정 또는 결정적 처리 후보.", "",
                 "### 보기", "",
                 "| 보기 | 예문 수 | 기대 턴 | 누락 | 초과 선택 | 실패율 | 처방 |",
                 "|---|---:|---:|---:|---:|---:|---|"]
        for view_id in registry.views:
            n_ex = len(table.get(view_id, ()))
            exp, miss, extra = view_expected[view_id], view_missing[view_id], view_extra[view_id]
            rate = f"{miss / exp:.0%}" if exp else "-"
            lines.append(f"| `{view_id}` | {n_ex} | {exp} | {miss} | {extra} | {rate} | "
                         f"{_prescription(n_ex > 0, miss + extra)} |")
        lines += ["", "### 보기 인자", "",
                  "| 보기.인자 | 예문 수 | 기대 턴 | 누락 | 환각 | 실패율 | 처방 |",
                  "|---|---:|---:|---:|---:|---:|---|"]
        for view_id, view in registry.views.items():
            for arg in view.args:
                key = (view_id, arg.name)
                n_ex = sum(1 for ex in table.get(view_id, ()) if arg.name in ex.args)
                exp, miss, hal = arg_expected[key], arg_missing[key], arg_halluc[key]
                rate = f"{miss / exp:.0%}" if exp else "-"
                lines.append(f"| `{view_id}.{arg.name}` | {n_ex} | {exp} | {miss} | {hal} | "
                             f"{rate} | {_prescription(n_ex > 0, miss + hal)} |")
        lines += ["", "### 예문 복사 지목", ""]
        if not copies:
            lines.append("- 없음")
        else:
            lines += ["| 보기 | 예문 | 지목 수 |", "|---|---|---:|"]
            for (view_id, idx), n in copies.most_common():
                exs = table.get(view_id, ())
                text = registry.views[view_id].examples[idx - 1] if 0 < idx <= len(exs) else ""
                lines.append(f"| `{view_id}` | #{idx} {text} | {n} |")
        return "\n".join(lines)

    return section


def judge_records(scenarios: Sequence[Scenario], records: Sequence[TurnRecord],
                  registry: RegistryInfo) -> list[TurnRecord]:
    """레코드 목록 전체에 판정을 붙여 돌려준다(원본 dict 를 고쳐 쓴다 · 시나리오 id 로 찾음).

    시나리오가 없는 레코드는 `verdict.skipped = "unknown_scenario"`로 둔다.
    """
    by_id = {s.id: s for s in scenarios}
    examples = example_table(registry)
    for record in records:
        scenario = by_id.get(str(record.get("scenario_id")))
        turn = record.get("turn")
        if scenario is None or not isinstance(turn, int) or not 0 <= turn < len(scenario.turns):
            record["verdict"] = Verdict(axes=_na_axes(), axis_detail={}, turn_pass=False,
                                        categories=[], locus=[], skipped="unknown_scenario")
            continue
        record["verdict"] = judge_turn(scenario, turn, record, registry, examples=examples)
    return list(records)


__all__ = [
    "AXES",
    "CATEGORIES",
    "CATEGORY_BY_NAME",
    "CODE_FILLED_ARGS",
    "HOURLY_EDGE_TOL_MIN",
    "PRE_GATE_DEFAULT",
    "PRE_GRAPH_CLARIFY",
    "PreGraphClarify",
    "WINDOW_ARGS",
    "WINDOW_END_TOL_MIN",
    "WINDOW_LATE_SLACK_MIN",
    "WINDOW_LEN_TOL_MIN",
    "WINDOW_LEN_TOL_RATIO",
    "Category",
    "ExampleParseError",
    "ParsedExample",
    "effective_views",
    "example_table",
    "excluded_arg_reason",
    "find_fewshot_copy",
    "grounded",
    "judge_args",
    "judge_records",
    "judge_results",
    "judge_source",
    "judge_target",
    "judge_turn",
    "judge_views",
    "judge_window",
    "make_example_section",
    "norm_value",
    "parse_example",
    "pre_graph_clarify",
    "run_invalid",
    "summarize",
    "target_placeholder",
]
