"""시나리오 로더 · 자리표 · 린트 · 레지스트리 정합 · 분할 · 커버리지 (plans/152 §3.2 · W1).

시나리오는 **사용자 프롬프트**다. 로더가 막는 것:
  - 도구명(`apm_`)·보기명(`apm.`)·API 경로(`/api`)·영문 인자 키·IP 리터럴(R2 — 사용자는 그런 말을
    치지 않는다 · 넣으면 「계획 LLM 이 사용자 말을 알아듣는가」를 재지 못한다)
  - 허용 밖 자리표(운영 이름은 저장소에 넣지 않는다 — 내부망 키트가 로컬 전용 파일로 채운다)

기대값 정합은 레지스트리가 정본이다(D-053 — 사본 금지). 기대 보기가 `solutions[apm].views`에 있는지,
기대 인자가 그 턴 기대 보기들 중 하나의 `args`에 있는지 본체 로더(`load_registry`)로 읽어 검사한다.

커버리지 원천 표는 `spec/CAPABILITY-MAP-134.md`의 markdown 표 행을 정규식으로 결정적으로 읽는다.
분할(dev/holdout)은 파일에 적힌 값이 정본이다 — 로더는 재배정하지 않고 검사만 한다.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import CAPABILITY_MAP_PATH, SCENARIOS_PATH, SOLUTION

SPLITS: tuple[str, ...] = ("dev", "holdout")
CLARIFY_KINDS: frozenset[str] = frozenset({"source", "required_arg"})
REFUSAL_KINDS: frozenset[str] = frozenset({"unsupported", "write_control"})
#: holdout 비율 목표·허용 폭(시나리오 기준 30%±3%p — plans/152 §3.2).
HOLDOUT_TARGET = 0.30
HOLDOUT_TOLERANCE = 0.03
#: 무거운 등급 상한(D-323 ③).
HEAVY_MAX = 5
ANY_VALUE = "*"

SCENARIO_KEYS: frozenset[str] = frozenset(
    {"id", "covers", "split", "heavy", "was_touch", "e2_skip", "sensitive", "turns"}
)
TURN_KEYS: frozenset[str] = frozenset({"prompt", "expect", "note", "reply_to"})
EXPECT_KEYS: frozenset[str] = frozenset(
    {"agents", "views", "target", "window", "args", "apm_sources", "clarify", "refusal"}
)

#: 자리표 — `{{inst_N}}`·`{{host_N}}`·`{{biz_N}}`·`{{svc_N}}` · 존·WAS 접촉 인스턴스 ·
#: 단건 식별자(`url`·`pid`·`guid`·`user`·`domain_id`). 시나리오 파일 머리 주석이 정본이다.
PLACEHOLDER_RE = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")
ALLOWED_PLACEHOLDER_RE = re.compile(
    r"^(?:(?:inst|host|biz|svc)_[1-9][0-9]*|inst_[cbw]|(?:url|pid|guid|user|domain_id)_1)$"
)
COVER_RE = re.compile(r"^(COV-[A-Z0-9]+(?:-[A-Z0-9]+)*)(?:\.([A-Za-z][A-Za-z0-9_]*))?$")
TARGET_RE = re.compile(
    r"^(?:none|untargeted|ref|inst_w|inst:(?:[1-9][0-9]*|c|b)|(?:host|biz|svc):[1-9][0-9]*)$"
)
WINDOW_RE = re.compile(
    r"^(?:default|none|current|compare|range:[1-9][0-9]*|hourly:(?:today|yesterday|[1-9][0-9]*))$"
)
IP_RE = re.compile(r"(?<![0-9.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9.])")
#: 자리표 밖 운영 이름처럼 보이는 영문 호스트(경고만 — 예 `kbwas01`·`web-12`).
HOSTLIKE_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]{2,}[A-Za-z0-9]*[-_]?[0-9]{2,}[A-Za-z0-9-]*")
#: 린트 금지 문구(규칙, 정규식, 설명).
BANNED_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("R2-tool-name", re.compile(r"apm_", re.IGNORECASE), "도구명(apm_)"),
    ("R2-view-name", re.compile(r"apm\.", re.IGNORECASE), "보기명(apm.)"),
    ("R2-api-path", re.compile(r"/api", re.IGNORECASE), "API 경로(/api)"),
)


class ScenarioError(ValueError):
    """시나리오 파일 구조 오류 — `issues`에 (id, 턴, 규칙, 문구) 전부를 싣는다."""

    def __init__(self, issues: Sequence[Issue]) -> None:
        self.issues = list(issues)
        super().__init__(f"시나리오 구조 위반 {len(self.issues)}건")


class PlaceholderError(ValueError):
    """채우지 못한 자리표가 남았다."""

    def __init__(self, names: Sequence[str]) -> None:
        self.names = list(names)
        super().__init__(f"채우지 않은 자리표: {', '.join(self.names)}")


@dataclass(frozen=True)
class Issue:
    """검사 결과 1건.

    Attributes:
        scenario_id: 시나리오 id(전역 검사는 `""` · 경로 단위는 COV id)
        turn: 턴 번호(0부터 · 시나리오·전역 단위는 None)
        rule: 규칙 코드(`R2-*` 린트 · `S-*` 구조 · `G-*` 레지스트리 정합 · `P-*` 분할)
        message: 사람이 읽는 문구
        level: `error`(위반 — exit 1) · `warning`(경고 — 표시만)
    """

    scenario_id: str
    turn: int | None
    rule: str
    message: str
    level: str = "error"

    def line(self) -> str:
        """한 줄 표기."""
        where = self.scenario_id or "(전역)"
        if self.turn is not None:
            where += f"#{self.turn}"
        return f"[{self.rule}] {where}: {self.message}"


@dataclass(frozen=True)
class Expect:
    """턴 하나의 기대값(시나리오 파일 머리 주석 「expect 칸」).

    Attributes:
        agents: 담당 처리기 집합(거절 턴은 빈 튜플)
        views: 기대 보기 집합(정확 일치 판정 · 거절 턴은 빈 튜플)
        target: 대상 표기(`none`·`untargeted`·`host:N`·`inst:N`·`inst:c`·`inst:b`·`biz:N`·`svc:N`·
            `inst_w`·`ref`)
        window: 창 표기(`default`·`none`·`current`·`compare`·`range:<분>`·
            `hourly:<today|yesterday|분>`)
        args: 요구한 보기 인자만(적지 않은 인자가 붙으면 환각) · 값 `"*"` = 존재만 판정
        apm_sources: 소스 지목 반영(레지스트리 apm 소스 id)
        clarify: 되묻기가 정답인 턴(`source`·`required_arg`)
        refusal: 거절이 정답인 턴(`unsupported`·`write_control`)
    """

    agents: tuple[str, ...]
    views: tuple[str, ...]
    target: str
    window: str
    args: dict[str, Any] = field(default_factory=dict, hash=False)
    apm_sources: tuple[str, ...] = ()
    clarify: str | None = None
    refusal: str | None = None


@dataclass(frozen=True)
class Turn:
    """턴 1건.

    Attributes:
        prompt: 사용자 프롬프트(자리표 포함 원문)
        expect: 기대값
        note: 생성 메모(판정에 쓰지 않음)
        reply_to: 앞 턴 되묻기에 대한 답 턴이면 그 되묻기 종류(`source` 등)
    """

    prompt: str
    expect: Expect
    note: str = ""
    reply_to: str | None = None


@dataclass(frozen=True)
class Scenario:
    """시나리오 1건.

    Attributes:
        id: 시나리오 id(파일 안 유일)
        covers: 덮는 COV 행(`COV-XXX`)·인자(`COV-XXX.<arg>`)
        split: `dev` · `holdout`(파일 값이 정본)
        turns: 턴 목록(멀티턴은 같은 스레드)
        heavy: 무거운 등급(최대 5 · 전부 dev · 맨 마지막 실행)
        was_touch: WAS 에이전트 접촉 가능 API — 대상은 `{{inst_w}}`만
        e2_skip: 내부망에서 건너뛰는 사유(없으면 None)
        sensitive: 응답 모양만 기록
    """

    id: str
    covers: tuple[str, ...]
    split: str
    turns: tuple[Turn, ...]
    heavy: bool = False
    was_touch: bool = False
    e2_skip: str | None = None
    sensitive: bool = False

    @property
    def cov_paths(self) -> frozenset[str]:
        """덮는 표 A 경로 id(인자 표기의 경로 부분 포함)."""
        return frozenset(c.split(".", 1)[0] for c in self.covers)

    @property
    def cov_args(self) -> frozenset[str]:
        """덮는 표 B 인자 키(`COV-XXX.<arg>`)."""
        return frozenset(c for c in self.covers if "." in c)


# --- 자리표 -----------------------------------------------------------------------------


def placeholders_of(text: str) -> list[str]:
    """텍스트의 자리표 이름(출현 순 · 중복 포함)."""
    return PLACEHOLDER_RE.findall(text)


def is_allowed_placeholder(name: str) -> bool:
    """허용 자리표 이름인가."""
    return bool(ALLOWED_PLACEHOLDER_RE.match(name))


def fill_placeholders(text: str, mapping: Mapping[str, str]) -> str:
    """`{{name}}`을 mapping 값으로 채운다.

    내부망 키트가 로컬 전용 파일에서 mapping을 준다. 채운 뒤 `{{`·`}}`가 남으면(미등록 이름 ·
    깨진 자리표) `PlaceholderError`를 낸다 — 자리표가 그대로 질의로 나가면 조회 대상이 틀린다.

    Args:
        text: 자리표가 든 원문
        mapping: 자리표 이름 → 운영 값

    Returns:
        채운 텍스트

    Raises:
        PlaceholderError: 채우지 못한 자리표가 남았을 때
    """
    missing: list[str] = []

    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in mapping:
            missing.append(name)
            return match.group(0)
        return str(mapping[name])

    filled = PLACEHOLDER_RE.sub(repl, text)
    if missing:
        raise PlaceholderError(list(dict.fromkeys(missing)))
    if "{{" in filled or "}}" in filled:
        raise PlaceholderError(["(깨진 자리표)"])
    return filled


# --- 로더 -------------------------------------------------------------------------------


def _str_tuple(value: Any) -> tuple[str, ...] | None:
    if value is None:
        return ()
    if isinstance(value, list) and all(isinstance(v, str) and v for v in value):
        return tuple(value)
    return None


def _parse_expect(raw: Any, sid: str, idx: int, issues: list[Issue]) -> Expect | None:
    if not isinstance(raw, dict):
        issues.append(Issue(sid, idx, "S-expect", "expect 가 매핑이 아니다"))
        return None
    unknown = sorted(set(raw) - EXPECT_KEYS)
    if unknown:
        issues.append(Issue(sid, idx, "S-key", f"expect 모르는 칸 {unknown}"))
    agents = _str_tuple(raw.get("agents"))
    views = _str_tuple(raw.get("views"))
    sources = _str_tuple(raw.get("apm_sources"))
    ok = True
    if "agents" not in raw or agents is None:
        issues.append(Issue(sid, idx, "S-agents", "agents 는 문자열 목록이어야 한다"))
        ok = False
    if views is None:
        issues.append(Issue(sid, idx, "S-views", "views 는 문자열 목록이어야 한다"))
        ok = False
    if sources is None:
        issues.append(Issue(sid, idx, "S-sources", "apm_sources 는 문자열 목록이어야 한다"))
        ok = False
    target, window = raw.get("target"), raw.get("window")
    if not isinstance(target, str) or not TARGET_RE.match(target):
        issues.append(Issue(sid, idx, "S-target", f"target 형식 밖: {target!r}"))
        ok = False
    if not isinstance(window, str) or not WINDOW_RE.match(window):
        issues.append(Issue(sid, idx, "S-window", f"window 형식 밖: {window!r}"))
        ok = False
    args = raw.get("args") or {}
    if not isinstance(args, dict) or not all(isinstance(k, str) for k in args):
        issues.append(Issue(sid, idx, "S-args", "args 는 문자열 키 매핑이어야 한다"))
        ok = False
    clarify, refusal = raw.get("clarify"), raw.get("refusal")
    if clarify is not None and clarify not in CLARIFY_KINDS:
        issues.append(Issue(sid, idx, "S-clarify", f"clarify 값 밖: {clarify!r}"))
        ok = False
    if refusal is not None and refusal not in REFUSAL_KINDS:
        issues.append(Issue(sid, idx, "S-refusal", f"refusal 값 밖: {refusal!r}"))
        ok = False
    if not ok:
        return None
    assert agents is not None and views is not None and sources is not None
    return Expect(
        agents=agents,
        views=views,
        target=target,
        window=window,
        args=dict(args),
        apm_sources=sources,
        clarify=clarify,
        refusal=refusal,
    )


def _parse_turn(raw: Any, sid: str, idx: int, issues: list[Issue]) -> Turn | None:
    if not isinstance(raw, dict):
        issues.append(Issue(sid, idx, "S-turn", "턴이 매핑이 아니다"))
        return None
    unknown = sorted(set(raw) - TURN_KEYS)
    if unknown:
        issues.append(Issue(sid, idx, "S-key", f"턴 모르는 칸 {unknown}"))
    prompt = raw.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        issues.append(Issue(sid, idx, "S-prompt", "prompt 가 비었다"))
        return None
    expect = _parse_expect(raw.get("expect"), sid, idx, issues)
    if expect is None:
        return None
    reply_to = raw.get("reply_to")
    if reply_to is not None and reply_to not in CLARIFY_KINDS:
        issues.append(Issue(sid, idx, "S-reply-to", f"reply_to 값 밖: {reply_to!r}"))
        return None
    note = raw.get("note") or ""
    return Turn(prompt=prompt, expect=expect, note=str(note), reply_to=reply_to)


def _flag(raw: dict[str, Any], key: str, sid: str, issues: list[Issue]) -> bool:
    value = raw.get(key, False)
    if value is None:
        return False
    if not isinstance(value, bool):
        issues.append(Issue(sid, None, "S-flag", f"{key} 는 true/false 여야 한다: {value!r}"))
        return False
    return value


def _parse_scenario(raw: Any, pos: int, issues: list[Issue]) -> Scenario | None:
    if not isinstance(raw, dict):
        issues.append(Issue(f"(#{pos})", None, "S-scenario", "시나리오가 매핑이 아니다"))
        return None
    sid = raw.get("id")
    if not isinstance(sid, str) or not sid.strip():
        issues.append(Issue(f"(#{pos})", None, "S-id", "id 가 비었다"))
        return None
    before = len(issues)
    unknown = sorted(set(raw) - SCENARIO_KEYS)
    if unknown:
        issues.append(Issue(sid, None, "S-key", f"시나리오 모르는 칸 {unknown}"))
    split = raw.get("split")
    if split not in SPLITS:
        issues.append(Issue(sid, None, "S-split", f"split 은 dev|holdout: {split!r}"))
    covers = _str_tuple(raw.get("covers"))
    if covers is None:
        issues.append(Issue(sid, None, "S-covers", "covers 가 문자열 목록이 아니다"))
        covers = ()
    for cover in covers:
        if not COVER_RE.match(cover):
            issues.append(Issue(sid, None, "S-covers", f"covers 형식 밖: {cover!r}"))
    e2_skip = raw.get("e2_skip")
    if e2_skip is not None and (not isinstance(e2_skip, str) or not e2_skip.strip()):
        issues.append(Issue(sid, None, "S-flag", f"e2_skip 은 사유 문자열: {e2_skip!r}"))
        e2_skip = None
    heavy = _flag(raw, "heavy", sid, issues)
    was_touch = _flag(raw, "was_touch", sid, issues)
    sensitive = _flag(raw, "sensitive", sid, issues)
    raw_turns = raw.get("turns")
    if not isinstance(raw_turns, list) or not raw_turns:
        issues.append(Issue(sid, None, "S-turns", "turns 가 비었다"))
        return None
    turns = [_parse_turn(t, sid, i, issues) for i, t in enumerate(raw_turns)]
    if len(issues) > before or any(t is None for t in turns):
        return None
    # 범위 밖 요청(거절이 정답)만 COV 행이 없다
    if not covers and not all(t is not None and t.expect.refusal for t in turns):
        issues.append(
            Issue(sid, None, "S-covers", "covers 가 비었다(거절 시나리오만 비울 수 있다)")
        )
        return None
    return Scenario(
        id=sid,
        covers=covers,
        split=str(split),
        turns=tuple(t for t in turns if t is not None),
        heavy=heavy,
        was_touch=was_touch,
        e2_skip=e2_skip,
        sensitive=sensitive,
    )


def check_structure(scenarios: Sequence[Scenario]) -> list[Issue]:
    """파싱된 시나리오의 턴 간 구조 규칙.

    - id 유일
    - `target: ref`·`reply_to`는 첫 턴에 올 수 없다 · `reply_to`는 바로 앞 턴의 `clarify`와
      같아야 한다
    - 거절 턴: agents·views 없음 · 되묻기와 함께 쓸 수 없다
    - 되묻기도 거절도 아닌 턴: views 1개 이상 · agents 에 `apm_query`
    - 대상이 자리표 대상(`host:N`·`inst:N`·`inst_w` 등)이면 답 턴이 아닌 한 프롬프트에 그
      자리표가 있다
    """
    issues: list[Issue] = []
    for sid, n in Counter(s.id for s in scenarios).items():
        if n > 1:
            issues.append(Issue(sid, None, "S-id-dup", f"id 중복 {n}회"))
    for s in scenarios:
        for i, t in enumerate(s.turns):
            e = t.expect
            if e.target == "ref" and i == 0:
                issues.append(Issue(s.id, i, "S-ref-first", "target: ref 는 첫 턴에 올 수 없다"))
            if t.reply_to is not None:
                prev = s.turns[i - 1].expect.clarify if i > 0 else None
                if prev != t.reply_to:
                    issues.append(
                        Issue(s.id, i, "S-reply-to", f"앞 턴 clarify({prev!r})에 대한 답이 아니다")
                    )
            if e.refusal is not None:
                if e.views or e.agents:
                    issues.append(Issue(s.id, i, "S-refusal", "거절 턴에 agents·views 가 있다"))
                if e.clarify is not None:
                    issues.append(Issue(s.id, i, "S-refusal", "거절 턴에 clarify 를 함께 적었다"))
            elif e.clarify is None:
                if not e.views:
                    issues.append(Issue(s.id, i, "S-views", "기대 보기가 없다"))
                if "apm_query" not in e.agents:
                    issues.append(Issue(s.id, i, "S-agents", "agents 에 apm_query 가 없다"))
            needed = _target_placeholder(e.target)
            if needed and t.reply_to is None and needed not in placeholders_of(t.prompt):
                issues.append(
                    Issue(
                        s.id, i, "S-target-prompt", f"대상 {e.target} 인데 {{{{{needed}}}}} 가 없다"
                    )
                )
    return issues


def _target_placeholder(target: str) -> str | None:
    if target == "inst_w":
        return "inst_w"
    if ":" in target:
        kind, ref = target.split(":", 1)
        return f"{kind}_{ref}"
    return None


def parse_scenarios(data: Any) -> tuple[list[Scenario], list[Issue]]:
    """YAML 원자료 → (시나리오, 구조 위반). 위반 시나리오는 결과에서 빠진다."""
    if not isinstance(data, list):
        return [], [Issue("", None, "S-file", "시나리오 파일 최상위가 목록이 아니다")]
    issues: list[Issue] = []
    parsed = [_parse_scenario(raw, pos, issues) for pos, raw in enumerate(data)]
    scenarios = [s for s in parsed if s is not None]
    issues.extend(check_structure(scenarios))
    return scenarios, issues


def load_scenarios(path: Path = SCENARIOS_PATH) -> list[Scenario]:
    """시나리오 파일을 읽어 dataclass 목록으로 돌려준다(파일 순서 보존).

    Raises:
        ScenarioError: 구조 위반이 하나라도 있을 때(전부 `issues`에)
    """
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    scenarios, issues = parse_scenarios(data)
    if issues:
        raise ScenarioError(issues)
    return scenarios


# --- 레지스트리 (D-053 정본) -------------------------------------------------------------


@dataclass(frozen=True)
class RegistryInfo:
    """레지스트리 apm 솔루션의 보기·소스(본체 로더 해석 그대로).

    Attributes:
        views: 보기 id → `ViewSpec`(선언 순서)
        source_ids: 소스 id 집합
        source_labels: 소스 표시 이름(레지스트리 `label` — 저장소 공개 값 · 반출 위생의 소스
            라벨 닫힌 어휘 · plans/164 W1)
    """

    views: dict[str, Any]
    source_ids: frozenset[str]
    source_labels: frozenset[str] = frozenset()

    @property
    def arg_keys(self) -> frozenset[str]:
        """보기 인자 이름·도구 인자 이름 전수(영문 인자 키 린트 대상)."""
        keys: set[str] = set()
        for view in self.views.values():
            for arg in view.args:
                keys.add(arg.name)
                if arg.tool_arg:
                    keys.add(arg.tool_arg)
        return frozenset(keys)


def load_registry_info(path: Path | None = None) -> RegistryInfo:
    """본체 레지스트리 로더로 apm 보기·소스를 읽는다(런타임과 같은 해석)."""
    from src.routing.registry import load_registry

    registry = load_registry(path)
    views = {view.id: view for view in registry.views_of(SOLUTION)}
    specs = registry.sources_of(SOLUTION)
    return RegistryInfo(views=views, source_ids=frozenset(source.id for source in specs),
                        source_labels=frozenset(s.label for s in specs if s.label))


def _value_issue(arg: Any, value: Any) -> str | None:
    if isinstance(value, str) and (value == ANY_VALUE or placeholders_of(value)):
        return None
    if arg.type == "enum":
        if value not in arg.choices:
            return f"선택지 {list(arg.choices)} 밖"
    elif arg.type == "bool":
        if not isinstance(value, bool):
            return "bool 이 아니다"
    elif arg.type == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            return "정수가 아니다"
        if arg.min is not None and value < arg.min:
            return f"하한 {arg.min} 미만"
    return None


def check_registry(scenarios: Sequence[Scenario], registry: RegistryInfo) -> list[Issue]:
    """기대 보기·인자·소스가 레지스트리와 정합하는가(D-053).

    - 기대 보기가 apm 보기 id 에 있다
    - 기대 인자 키가 그 턴 기대 보기들 중 하나의 `args`에 있다 · 값이 그 선언(선택지·형·하한)에 맞다
      (`"*"`·자리표 값은 존재만)
    - 기대 보기의 필수 인자가 빠지면 그 턴은 `clarify: required_arg`여야 하고, 그 역도 같다
    - `apm_sources`가 레지스트리 apm 소스 id 에 있다
    """
    issues: list[Issue] = []
    for s in scenarios:
        for i, t in enumerate(s.turns):
            e = t.expect
            specs = []
            for view_id in e.views:
                spec = registry.views.get(view_id)
                if spec is None:
                    issues.append(Issue(s.id, i, "G-view", f"레지스트리에 없는 보기 {view_id}"))
                else:
                    specs.append(spec)
            for key, value in e.args.items():
                decls = [a for spec in specs for a in spec.args if a.name == key]
                if not decls:
                    issues.append(
                        Issue(s.id, i, "G-arg", f"인자 {key} 가 기대 보기 {list(e.views)} 에 없다")
                    )
                    continue
                problems = [p for p in (_value_issue(a, value) for a in decls) if p]
                if len(problems) == len(decls):
                    issues.append(Issue(s.id, i, "G-arg-value", f"{key}={value!r}: {problems[0]}"))
            missing = [
                f"{spec.id}.{a.name}"
                for spec in specs
                for a in spec.args
                if a.required and a.name not in e.args
            ]
            if missing and e.clarify != "required_arg":
                issues.append(Issue(s.id, i, "G-required-arg", f"필수 인자 없음 {missing}"))
            if e.clarify == "required_arg" and not missing:
                issues.append(
                    Issue(
                        s.id,
                        i,
                        "G-required-arg",
                        "clarify: required_arg 인데 빠진 필수 인자가 없다",
                    )
                )
            for source in e.apm_sources:
                if source not in registry.source_ids:
                    issues.append(Issue(s.id, i, "G-source", f"레지스트리에 없는 소스 {source}"))
    return issues


# --- 린트 (R2) --------------------------------------------------------------------------


def lint_text(text: str, arg_keys: Iterable[str]) -> list[tuple[str, str, str]]:
    """프롬프트 1건 린트 → (규칙, 문구, 수준) 목록.

    자리표는 먼저 지운 뒤 금지 문구를 찾는다(자리표 이름이 영문 인자 키와 겹쳐도 위반이 아니다).
    영문 인자 키는 대소문자를 구분해 단어 경계로만 찾는다 — 「GUID」·클래스명 「Order」 같은 사용자
    말은 위반이 아니다.
    """
    found: list[tuple[str, str, str]] = []
    for name in placeholders_of(text):
        if not is_allowed_placeholder(name):
            found.append(("R2-placeholder", f"허용 밖 자리표 {{{{{name}}}}}", "error"))
    bare = PLACEHOLDER_RE.sub(" ", text)
    if "{{" in bare or "}}" in bare:
        found.append(("R2-placeholder", "깨진 자리표(중괄호 짝)", "error"))
    for rule, pattern, label in BANNED_PATTERNS:
        match = pattern.search(bare)
        if match:
            found.append((rule, f"{label} 「{match.group(0)}」", "error"))
    for key in sorted(arg_keys):
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])", bare):
            found.append(("R2-arg-key", f"영문 인자 키 「{key}」", "error"))
    ip = IP_RE.search(bare)
    if ip:
        found.append(("R2-ip", f"IP 리터럴 「{ip.group(0)}」", "error"))
    for host in HOSTLIKE_RE.findall(bare):
        found.append(
            ("R2-hostlike", f"운영 이름처럼 보이는 영문 「{host}」 — 자리표 검토", "warning")
        )
    return found


def lint_scenarios(scenarios: Sequence[Scenario], arg_keys: Iterable[str]) -> list[Issue]:
    """전 턴 프롬프트 린트 + 기대 인자 값의 자리표 허용 검사."""
    keys = frozenset(arg_keys)
    issues: list[Issue] = []
    for s in scenarios:
        for i, t in enumerate(s.turns):
            for rule, message, level in lint_text(t.prompt, keys):
                issues.append(Issue(s.id, i, rule, message, level))
            for value in t.expect.args.values():
                for name in placeholders_of(str(value)):
                    if not is_allowed_placeholder(name):
                        issues.append(
                            Issue(s.id, i, "R2-placeholder", f"기대 인자 값 허용 밖 자리표 {name}")
                        )
    return issues


# --- 대응표 (spec/CAPABILITY-MAP-134.md 표 A·B) -------------------------------------------


_CELL_SPLIT = re.compile(r"(?<!\\)\|")
_TICKED = re.compile(r"`([^`]+)`")


@dataclass(frozen=True)
class CovPath:
    """표 A 행 — 허용 GET 경로 1건."""

    cov_id: str
    path: str


@dataclass(frozen=True)
class CovArg:
    """표 B 행 — (경로, 인자) 1건.

    Attributes:
        cov_id: 표 A COV id(전 경로 공통 행은 표의 첫 칸 그대로)
        arg: 인자 이름(백틱 안 첫 토큰)
        required: 「필수」 칸 원문
        disposition: 「처분」 칸 원문
        mapping: 「게이트웨이 인자 매핑」 칸 원문
    """

    cov_id: str
    arg: str
    required: str
    disposition: str
    mapping: str

    @property
    def key(self) -> str:
        """시나리오 `covers` 표기(`COV-XXX.<arg>`)."""
        return f"{self.cov_id}.{self.arg}"

    @property
    def selected(self) -> bool:
        """커버리지 대상 — 필수가 아니고(「선택」·「미기재」) 처분이 「전달」인 인자."""
        return not self.required.startswith("필수") and self.disposition.startswith("전달")

    @property
    def exclusion(self) -> str:
        """대상 밖이면 제외 사유(대상이면 빈 문자열)."""
        if self.selected:
            return ""
        if not self.disposition.startswith("전달"):
            return f"처분 「{_short(self.disposition)}」"
        if "코드가 채움" in self.mapping:
            return "필수 · 코드가 채움(LLM 미노출)"
        return "필수 인자"


@dataclass(frozen=True)
class CapabilityMap:
    """표 A 경로 · 표 B 인자."""

    paths: tuple[CovPath, ...]
    args: tuple[CovArg, ...]

    @property
    def path_ids(self) -> tuple[str, ...]:
        return tuple(p.cov_id for p in self.paths)

    @property
    def selected_args(self) -> tuple[CovArg, ...]:
        return tuple(a for a in self.args if a.selected)


def _short(text: str, limit: int = 60) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _cells(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [cell.strip() for cell in _CELL_SPLIT.split(body)]


def _section(lines: list[str], heading_prefix: str) -> list[str]:
    """`heading_prefix`로 시작하는 제목 다음부터 다음 제목(`#`) 전까지의 줄."""
    out: list[str] = []
    inside = False
    for line in lines:
        if line.startswith("#"):
            if inside:
                break
            inside = line.startswith(heading_prefix)
            continue
        if inside:
            out.append(line)
    return out


def _table_rows(lines: list[str], first_header: str) -> tuple[list[str], list[list[str]]]:
    """절 안에서 첫 칸이 `first_header`인 표의 머리·데이터 행(구분 행 제외)."""
    header: list[str] = []
    rows: list[list[str]] = []
    for line in lines:
        if not line.startswith("|"):
            if header:
                break
            continue
        cells = _cells(line)
        if not header:
            if cells and cells[0] == first_header:
                header = cells
            continue
        if all(re.fullmatch(r":?-+:?", c) for c in cells if c):
            continue
        rows.append(cells)
    return header, rows


def parse_capability_map(path: Path = CAPABILITY_MAP_PATH) -> CapabilityMap:
    """`spec/CAPABILITY-MAP-134.md`의 표 A(§2)·표 B(§3)를 결정적으로 읽는다.

    Raises:
        ValueError: 표·칸을 찾지 못했을 때(대응표 서식이 바뀌면 조용히 0건이 되지 않게)
    """
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    header_a, rows_a = _table_rows(_section(lines, "## 2. 표 A"), "capability_id")
    header_b, rows_b = _table_rows(_section(lines, "## 3. 표 B"), "capability_id")
    if not rows_a or not rows_b:
        raise ValueError(f"대응표에서 표 A·B 를 찾지 못했다: {path}")
    try:
        col_path = header_a.index("GET 경로")
        col_arg = header_b.index("인자")
        col_req = header_b.index("필수")
        col_disp = header_b.index("처분")
        col_map = header_b.index("게이트웨이 인자 매핑")
    except ValueError as exc:
        raise ValueError(f"대응표 칸 이름이 바뀌었다: {exc}") from exc
    paths: list[CovPath] = []
    for cells in rows_a:
        cov_id = cells[0].strip("*").strip()
        if cov_id.startswith("COV-") and len(cells) > col_path:
            paths.append(CovPath(cov_id=cov_id, path=cells[col_path].strip("`")))
    args: list[CovArg] = []
    for cells in rows_b:
        if len(cells) <= max(col_arg, col_req, col_disp, col_map):
            continue
        ticked = _TICKED.search(cells[col_arg])
        if not ticked:
            continue
        args.append(
            CovArg(
                cov_id=cells[0].strip("*").strip(),
                arg=ticked.group(1).strip(),
                required=cells[col_req],
                disposition=cells[col_disp],
                mapping=cells[col_map],
            )
        )
    return CapabilityMap(paths=tuple(paths), args=tuple(args))


def check_covers(scenarios: Sequence[Scenario], cap: CapabilityMap) -> list[Issue]:
    """`covers`가 표 A 경로 · 표 B 대상 인자에 있는가."""
    path_ids = set(cap.path_ids)
    arg_rows = {a.key: a for a in cap.args}
    issues: list[Issue] = []
    for s in scenarios:
        for cover in s.covers:
            base = cover.split(".", 1)[0]
            if base not in path_ids:
                issues.append(Issue(s.id, None, "S-covers", f"표 A 에 없는 경로 {base}"))
            elif "." in cover:
                row = arg_rows.get(cover)
                if row is None:
                    issues.append(Issue(s.id, None, "S-covers", f"표 B 에 없는 인자 {cover}"))
                elif not row.selected:
                    issues.append(
                        Issue(s.id, None, "S-covers", f"표 B 대상 밖 인자 {cover}({row.exclusion})")
                    )
    return issues


# --- 분할 -------------------------------------------------------------------------------


@dataclass(frozen=True)
class SplitSummary:
    """분할 검사 결과.

    Attributes:
        total: 시나리오 수
        holdout: holdout 시나리오 수
        ratio: holdout 비율
        per_path: COV id → (dev 시나리오 id, holdout 시나리오 id)
        issues: 위반·경고
    """

    total: int
    holdout: int
    ratio: float
    per_path: dict[str, tuple[tuple[str, ...], tuple[str, ...]]]
    issues: list[Issue]


def check_split(scenarios: Sequence[Scenario], path_ids: Sequence[str]) -> SplitSummary:
    """분할·무거운 등급·WAS 접촉 규칙(파일 값 검사 — 재배정 없음).

    - holdout 비율 30%±3%p
    - 표 A 경로마다 dev·holdout 양쪽 최소 1 · 한쪽이 전부 `e2_skip`이면 「외부망 전용」 경고
    - heavy ≤ 5 · heavy 는 전부 dev
    - `was_touch`면 첫 턴 대상이 `inst_w`이고 앞 턴 지목(`ref`) 밖 대상도 모두 `inst_w`
    """
    issues: list[Issue] = []
    total = len(scenarios)
    holdout = sum(1 for s in scenarios if s.split == "holdout")
    ratio = holdout / total if total else 0.0
    if abs(ratio - HOLDOUT_TARGET) > HOLDOUT_TOLERANCE + 1e-9:
        issues.append(
            Issue(
                "",
                None,
                "P-ratio",
                f"holdout {holdout}/{total} = {ratio:.1%} — 목표 "
                f"{HOLDOUT_TARGET:.0%}±{HOLDOUT_TOLERANCE * 100:.0f}%p 밖",
            )
        )
    per_path: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
    for cov_id in path_ids:
        sides = {
            split: [s for s in scenarios if s.split == split and cov_id in s.cov_paths]
            for split in SPLITS
        }
        per_path[cov_id] = (
            tuple(s.id for s in sides["dev"]),
            tuple(s.id for s in sides["holdout"]),
        )
        for split, members in sides.items():
            if not members:
                issues.append(Issue(cov_id, None, "P-path-sides", f"{split} 시나리오가 없다"))
            elif all(s.e2_skip for s in members):
                reasons = sorted({str(s.e2_skip) for s in members})
                issues.append(
                    Issue(
                        cov_id,
                        None,
                        "P-e2-only",
                        f"{split} 가 전부 내부망 건너뜀({', '.join(reasons)}) — 외부망 전용",
                        "warning",
                    )
                )
    heavy = [s for s in scenarios if s.heavy]
    if len(heavy) > HEAVY_MAX:
        issues.append(Issue("", None, "P-heavy-count", f"heavy {len(heavy)}개 > 상한 {HEAVY_MAX}"))
    for s in heavy:
        if s.split != "dev":
            issues.append(Issue(s.id, None, "P-heavy-split", "heavy 는 dev 여야 한다"))
    for s in scenarios:
        if not s.was_touch:
            continue
        targets = [t.expect.target for t in s.turns]
        if targets[0] != "inst_w" or any(x not in ("inst_w", "ref") for x in targets):
            issues.append(
                Issue(
                    s.id, None, "P-was-touch", f"was_touch 인데 대상이 {{{{inst_w}}}} 밖: {targets}"
                )
            )
    return SplitSummary(total, holdout, ratio, per_path, issues)


# --- 커버리지 -----------------------------------------------------------------------------


@dataclass(frozen=True)
class CoverageItem:
    """커버리지 항목 1건 — 덮은 시나리오가 없으면 미커버."""

    key: str
    label: str
    covered_by: tuple[str, ...]

    @property
    def covered(self) -> bool:
        return bool(self.covered_by)


@dataclass(frozen=True)
class CoverageReport:
    """표 A 경로 · 표 B 선택 인자 · 레지스트리 보기 · 보기 인자 커버리지 + 분할 요약.

    Attributes:
        paths: 표 A 경로
        args: 표 B 대상 인자(필수 아님 · 처분 「전달」)
        excluded_args: 표 B 제외 인자 (키, 사유)
        views: 레지스트리 apm 보기
        view_args: 레지스트리 (보기, 인자)
        split: 분할 검사 결과
    """

    paths: tuple[CoverageItem, ...]
    args: tuple[CoverageItem, ...]
    excluded_args: tuple[tuple[str, str], ...]
    views: tuple[CoverageItem, ...]
    view_args: tuple[CoverageItem, ...]
    split: SplitSummary

    def groups(self) -> tuple[tuple[str, tuple[CoverageItem, ...]], ...]:
        """(이름, 항목) 4묶음."""
        return (
            ("표 A 경로", self.paths),
            ("표 B 선택 인자", self.args),
            ("레지스트리 보기", self.views),
            ("레지스트리 보기 인자", self.view_args),
        )

    @property
    def uncovered(self) -> int:
        """미커버 합계."""
        return sum(1 for _name, items in self.groups() for item in items if not item.covered)


def build_coverage(
    scenarios: Sequence[Scenario], cap: CapabilityMap, registry: RegistryInfo
) -> CoverageReport:
    """시나리오가 덮는 표 A·B 행과 레지스트리 보기·보기 인자를 센다.

    표 A·B 는 `covers`로, 보기는 `expect.views`로, 보기 인자는 그 보기를 기대한 턴의 `expect.args`
    키로 센다(인자 이름이 그 보기 선언에 있을 때만).
    """
    paths = tuple(
        CoverageItem(p.cov_id, p.path, tuple(s.id for s in scenarios if p.cov_id in s.cov_paths))
        for p in cap.paths
    )
    args = tuple(
        CoverageItem(
            a.key, _short(a.mapping, 50), tuple(s.id for s in scenarios if a.key in s.cov_args)
        )
        for a in cap.selected_args
    )
    excluded = tuple((a.key, a.exclusion) for a in cap.args if not a.selected)
    view_hits: dict[str, list[str]] = {view_id: [] for view_id in registry.views}
    arg_hits: dict[tuple[str, str], list[str]] = {
        (view.id, arg.name): [] for view in registry.views.values() for arg in view.args
    }
    for s in scenarios:
        for t in s.turns:
            for view_id in t.expect.views:
                if view_id in view_hits and s.id not in view_hits[view_id]:
                    view_hits[view_id].append(s.id)
                for key in t.expect.args:
                    hits = arg_hits.get((view_id, key))
                    if hits is not None and s.id not in hits:
                        hits.append(s.id)
    views = tuple(
        CoverageItem(view_id, registry.views[view_id].tool, tuple(ids))
        for view_id, ids in view_hits.items()
    )
    arg_types = {(v.id, a.name): a.type for v in registry.views.values() for a in v.args}
    view_args = tuple(
        CoverageItem(f"{view_id}.{name}", arg_types[(view_id, name)], tuple(ids))
        for (view_id, name), ids in arg_hits.items()
    )
    split = check_split(scenarios, cap.path_ids)
    return CoverageReport(paths, args, excluded, views, view_args, split)


# --- coverage.md 섹션 (W3가 「예문 커버리지 × 실패율」 절을 덧붙인다) -------------------------


Section = Callable[[CoverageReport], str]


def _items_table(items: Sequence[CoverageItem], label_head: str) -> list[str]:
    out = [f"| 항목 | {label_head} | 시나리오 수 | 시나리오(앞 3) |", "|---|---|---:|---|"]
    for item in items:
        mark = "" if item.covered else " **미커버**"
        head = ", ".join(item.covered_by[:3])
        out.append(f"| `{item.key}`{mark} | {item.label} | {len(item.covered_by)} | {head} |")
    return out


def _group_section(title: str, items: Sequence[CoverageItem], label_head: str) -> str:
    missing = [item.key for item in items if not item.covered]
    lines = [
        f"## {title}",
        "",
        f"커버 {len(items) - len(missing)}/{len(items)} · 미커버 {len(missing)}"
        + (f": {', '.join(missing)}" if missing else ""),
        "",
        *_items_table(items, label_head),
    ]
    return "\n".join(lines)


def section_summary(report: CoverageReport) -> str:
    """요약 — 묶음별 커버 수 · 분할."""
    lines = ["## 요약", "", "| 묶음 | 대상 | 커버 | 미커버 |", "|---|---:|---:|---:|"]
    for name, items in report.groups():
        covered = sum(1 for item in items if item.covered)
        lines.append(f"| {name} | {len(items)} | {covered} | {len(items) - covered} |")
    split = report.split
    lines += [
        "",
        f"- 미커버 합계: {report.uncovered}",
        f"- 분할: 시나리오 {split.total} · dev {split.total - split.holdout} · holdout "
        f"{split.holdout} ({split.ratio:.1%})",
    ]
    return "\n".join(lines)


def section_paths(report: CoverageReport) -> str:
    """표 A 경로 커버리지."""
    return _group_section("표 A — 허용 GET 경로", report.paths, "GET 경로")


def section_args(report: CoverageReport) -> str:
    """표 B 선택 인자 커버리지 + 제외 인자와 사유."""
    body = _group_section("표 B — 선택 인자(실제 전달)", report.args, "게이트웨이 인자 매핑")
    lines = [
        body,
        "",
        f"### 표 B 제외 {len(report.excluded_args)}행",
        "",
        "| 인자 | 제외 사유 |",
        "|---|---|",
    ]
    lines += [f"| `{key}` | {reason} |" for key, reason in report.excluded_args]
    return "\n".join(lines)


def section_views(report: CoverageReport) -> str:
    """레지스트리 보기 커버리지."""
    return _group_section("레지스트리 보기", report.views, "도구")


def section_view_args(report: CoverageReport) -> str:
    """레지스트리 보기 인자 커버리지."""
    return _group_section("레지스트리 보기 인자", report.view_args, "형식")


def section_split(report: CoverageReport) -> str:
    """경로별 dev·holdout 분할과 분할 위반·경고."""
    split = report.split
    lines = ["## 분할 — 경로별 dev·holdout", "", "| 경로 | dev | holdout |", "|---|---:|---:|"]
    for cov_id, (dev, hold) in split.per_path.items():
        lines.append(f"| `{cov_id}` | {len(dev)} | {len(hold)} |")
    lines += ["", "### 분할 위반·경고", ""]
    lines += [f"- {issue.level}: {issue.line()}" for issue in split.issues] or ["- 없음"]
    return "\n".join(lines)


COVERAGE_SECTIONS: tuple[Section, ...] = (
    section_summary,
    section_paths,
    section_args,
    section_views,
    section_view_args,
    section_split,
)


def render_coverage_md(
    report: CoverageReport,
    *,
    extra_sections: Sequence[Section] = (),
    title: str = "APM 벤치 커버리지",
) -> str:
    """coverage.md 본문 — 기본 섹션 뒤에 `extra_sections`를 차례로 붙인다."""
    parts = [f"# {title}"]
    parts += [section(report) for section in (*COVERAGE_SECTIONS, *extra_sections)]
    return "\n\n".join(parts).rstrip() + "\n"
