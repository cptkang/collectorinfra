"""결정적 채점기 (plans/154 §4.4·§4.5 — 이 모듈의 정본 · D-324 ③).

조사 실행 1건(`raw.jsonl` 1행)을 세계 YAML 정답과 세계 서버 호출 로그로 채점해 `scores.jsonl` 1행을
만든다. LLM 심판은 쓰지 않는다 — 같은 입력이면 같은 점수다(D-035).

- 원인 적중(RC@1·RC@3): 가설 순위(`root_cause_hypotheses` — 없으면 `cause`가 1위)에서 정답 `kind`
  표면형 **그리고** 정답 호스트가 함께 맞아야 적중이다. 구성요소는 있으면 부분 점수만 기록한다.
  가설 문장이 세계 호스트를 하나도 말하지 않으면 알람 호스트를 가리킨 것으로 본다.
- 증거 폐포(ECR): `key_evidence` 중 `cite` 토큰이 브리핑 서술에 전부 있고 **그 도구가 그 매처
  응답을 실제로 내준**(호출 기록에 같은 도구 · `match` 부분집합 일치 · 인자 오류 없음 · 오류 응답
  아님) 비율.
- 환각(HALLU): 브리핑 서술의 숫자+단위 주장 중 그 실행에서 세계 서버가 내준 응답·알람 이벤트 값
  어디에도 없는 것의 비율. 반올림·같은 계열 단위 환산을 허용하고 시각·날짜·호스트명·버전 속 숫자는
  주장으로 보지 않는다(`extract_claims`).
- 유인(DIST): 1위 원인이 `distractors` 표면형과 맞고 정답 표면형과는 맞지 않을 때.
- 기권(ABST): `expect.abstain`과 (`evidence_insufficient` 또는 최고 confidence ≤ low)의 일치.
- 실패 유형: `classify` 독스트링의 순서에서 가장 앞선 하나.

조인 규칙: 실행의 호출 목록은 raw 행의 `tool_trace`가 1차다. `world_calls.jsonl`은 `reply_hash`로
응답 본문을 찾는 데만 쓴다(같은 세계·반복을 실험군이 나눠 쓰므로 world_calls만으로는 실행을
못 가른다).

경계(D-139 · §9.5): `sre_agent` 패키지를 import하지 않는다 — 브리핑 키 이름은 문자열로만 안다.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from investigation_bench.worlds import World, load_worlds

# 채점 규칙이 바뀌면 올린다 — `--rejudge`가 이전 판을 `scores.<판>.jsonl`로 보존한다.
JUDGE_VERSION = "j2"  # j2: HALLU 환산 비교를 주장 단위로 · 응답 시각 숫자 제외 · 정체 노트 단일화

FAILURE_CODES: tuple[str, ...] = (
    "F-INFRA", "F-TMO", "F-INC", "F-ARG", "F-LOOP", "F-MISS",
    "F-DIST", "F-READ", "F-HALLU", "F-CITE", "F-ABST", "PASS",
)

# 무효표 판정: 이 상태만 유효다(그 밖 — `rejected`·`stub`·`failed`·폴링 초과 등 — 은 F-INFRA).
VALID_STATUSES = frozenset({"done", "timeout"})
TIMEOUT_STATUS = "timeout"

# 기권 임계: 최고 confidence가 이 순위 이하면 「단정하지 않음」이다. 근거 — 브리핑 조립기가
# 인용 없는 LLM 원인을 `low`로 강등하고(`_llm_hypothesis`), severity 시그니처 무매칭이 `none`이다.
# `medium` 이상은 인용이 붙은 단정이다.
CONFIDENCE_RANK: dict[str, int] = {"none": 0, "low": 1, "medium": 2, "high": 3}
ABSTAIN_MAX_CONFIDENCE_RANK = 1
# 정체 판정: 같은 도구·같은 인자가 이 횟수 이상이면 F-LOOP 후보다 — sre_agent 정체 가드
# (`STALL_REPEAT_THRESHOLD = 3` · domain/investigation_limits.py)와 같은 값.
LOOP_REPEAT_THRESHOLD = 3
# 인용 부족: ECR이 이 값 미만이면 F-CITE, 이상이어야 PASS(§4.5).
ECR_PASS_MIN = 0.5
RC_TOP_K = 3

# 브리핑 서술 — HALLU 추출·인용 탐색 대상. `bottleneck`(시그니처 라벨)·`limitations`·
# `recommendation`·`severity`는 조립기 고정 문구라 제외한다.
NARRATIVE_KEYS: tuple[str, ...] = ("summary", "cause", "timeline", "hypotheses")
HYPOTHESIS_TEXT_KEYS: tuple[str, ...] = ("cause", "evidence", "reasoning")


# ─────────────────────────────── 정규화 ───────────────────────────────

_SEP_RE = re.compile(r"[\s_\-·]+")


def norm(text: Any) -> str:
    """비교용 정규화 — NFKC · 대소문자 무시 · 공백·`_`·`-`·`·` 제거(한영 혼용 표면형 대조)."""
    return _SEP_RE.sub("", unicodedata.normalize("NFKC", str(text)).casefold())


def _strings(node: Any, out: list[str]) -> None:
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, Mapping):
        for v in node.values():
            _strings(v, out)
    elif isinstance(node, (list, tuple)):
        for v in node:
            _strings(v, out)


def narrative_texts(briefing: Mapping[str, Any] | None) -> list[str]:
    """브리핑 서술 문자열(요약·원인·타임라인·미인용 단정·가설의 원인/근거/추론)."""
    out: list[str] = []
    if not isinstance(briefing, Mapping):
        return out
    for key in NARRATIVE_KEYS:
        _strings(briefing.get(key), out)
    for h in briefing.get("root_cause_hypotheses") or []:
        if isinstance(h, Mapping):
            for key in HYPOTHESIS_TEXT_KEYS:
                _strings(h.get(key), out)
    return out


def _forms(primary: Any, extra: Any) -> list[str]:
    """표면형 목록 — 기본값 + 별칭(목록 또는 `{kind: [...]}`)의 정규형(빈 값 제외 · 순서 유지)."""
    raw: list[Any] = [primary]
    if isinstance(extra, Mapping):
        extra = extra.get("kind")
    if isinstance(extra, (list, tuple)):
        raw.extend(extra)
    elif extra:
        raw.append(extra)
    return list(dict.fromkeys(n for n in (norm(x) for x in raw if x not in (None, "")) if n))


def kind_forms(label: Mapping[str, Any]) -> list[str]:
    """정답 kind 표면형 — 라벨 kind 자체 + `aliases.kind`."""
    return _forms(label.get("kind"), label.get("aliases"))


# ─────────────────────────────── 가설 ───────────────────────────────


def ranked_hypotheses(briefing: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """가설을 순위대로. 가설 목록이 없으면 `cause`를 1위로 둔다(confidence는 조립기 규칙대로)."""
    if not isinstance(briefing, Mapping):
        return []
    hyps = [dict(h) for h in briefing.get("root_cause_hypotheses") or [] if isinstance(h, Mapping)]
    if hyps:
        def _key(item: tuple[int, dict[str, Any]]) -> tuple[float, int]:
            rank = item[1].get("rank")
            return (float(rank) if isinstance(rank, (int, float)) else float("inf"), item[0])
        return [h for _, h in sorted(enumerate(hyps), key=_key)]
    cause = briefing.get("cause")
    if not cause:
        return []
    conf = "medium" if briefing.get("citations_verified") else "low"
    return [{"rank": 1, "cause": str(cause), "confidence": conf}]


def _hyp_text(h: Mapping[str, Any]) -> str:
    out: list[str] = []
    for key in HYPOTHESIS_TEXT_KEYS:
        _strings(h.get(key), out)
    return norm(" ".join(out))


def world_hosts(world: World) -> list[str]:
    """세계 호스트 전부(`hosts` + 알람 호스트 + 라벨 호스트 · 원형 · 순서 유지)."""
    ev = world.event
    raw = list(world.data.get("hosts") or []) + [
        ev.get("hostname"), ev.get("serverName"),
        (world.label.get("root_entity") or {}).get("host"),
    ]
    return list(dict.fromkeys(str(h) for h in raw if h))


@dataclass(frozen=True)
class HypMatch:
    """가설 1건의 정답 대조."""

    kind_hit: bool
    host_hit: bool
    component_hit: bool | None
    dist_kind: str | None
    host: str | None


def match_hypothesis(h: Mapping[str, Any], world: World) -> HypMatch:
    """가설 1건을 정답 라벨·유인과 대조한다.

    kind·유인은 가설의 `cause` 문장만 본다(근거 줄에 유인 수치가 「정상」으로 적혀도 유인으로 세지
    않도록). 호스트·구성요소는 `cause`·`evidence`·`reasoning`을 함께 본다. 호스트를 하나도 말하지
    않으면 알람 호스트를 가리킨 것으로 본다.
    """
    label = world.label
    entity = label.get("root_entity") or {}
    cause = norm(h.get("cause") or "")
    text = _hyp_text(h)
    kind_hit = any(f in cause for f in kind_forms(label))
    dist_kind: str | None = None
    if not kind_hit:
        for d in world.distractors:
            if any(f in cause for f in _forms(d.get("kind"), d.get("aliases"))):
                dist_kind = str(d.get("kind"))
                break
    hosts = world_hosts(world)
    mentioned = [x for x in hosts if norm(x) in text]
    ev = world.event
    alarm_host = str(ev.get("hostname") or ev.get("serverName") or "")
    label_host = entity.get("host")
    if label_host:
        if mentioned:
            host_hit = norm(label_host) in {norm(x) for x in mentioned}
        else:
            host_hit = norm(label_host) == norm(alarm_host)
    else:
        host_hit = True
    host: str | None
    if host_hit and label_host:
        host = str(label_host)
    else:
        host = mentioned[0] if mentioned else (alarm_host or None)
    component = entity.get("component")
    component_hit = (norm(component) in text) if component else None
    return HypMatch(kind_hit, bool(host_hit), component_hit, dist_kind, host)


# ─────────────────────────────── 호출 기록 ───────────────────────────────


def _clean(args: Any) -> dict[str, Any]:
    if not isinstance(args, Mapping):
        return {}
    return {k: v for k, v in args.items() if v is not None}


def _is_error_reply(reply: Any) -> bool:
    if isinstance(reply, str):
        return reply.startswith("Error executing tool")
    return isinstance(reply, Mapping) and bool(reply.get("error"))


def reply_index(world_calls: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """`reply_hash` → 응답 본문(처음 본 것)."""
    out: dict[str, Any] = {}
    for row in world_calls:
        h = row.get("reply_hash")
        if h and h not in out:
            out[str(h)] = row.get("reply")
    return out


def _trace(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(t) for t in raw.get("tool_trace") or [] if isinstance(t, Mapping)]


def delivered(ke: Mapping[str, Any], trace: Sequence[Mapping[str, Any]],
              replies: Mapping[str, Any]) -> bool:
    """세계 서버가 이 핵심 증거의 응답을 실제로 내줬는가."""
    match = ke.get("match") or {}
    for t in trace:
        if t.get("tool") != ke.get("tool") or t.get("arg_error"):
            continue
        args = _clean(t.get("args"))
        if not all(k in args and args[k] == v for k, v in match.items()):
            continue
        if _is_error_reply(replies.get(str(t.get("reply_hash")))):
            continue
        return True
    return False


def _repeats(trace: Sequence[Mapping[str, Any]]) -> list[str]:
    keys = Counter(
        (str(t.get("tool")), json.dumps(_clean(t.get("args")), sort_keys=True, ensure_ascii=False,
                                        default=str))
        for t in trace
    )
    # 판정 단위는 (도구, 인자) 쌍이고, 표기는 도구별로 모은다
    # — 최대 반복 수 + 인자가 여럿이면 종류 수.
    by_tool: dict[str, list[int]] = {}
    for (name, _a), n in keys.items():
        if n >= LOOP_REPEAT_THRESHOLD:
            by_tool.setdefault(name, []).append(n)
    return [f"{name} x{max(ns)}" + (f" (인자 {len(ns)}종)" if len(ns) > 1 else "")
            for name, ns in by_tool.items()]


def _unresolved(raw: Mapping[str, Any]) -> list[str]:
    """정체 가드 `unresolved`(raw 최상위 또는 `done` 감사 이벤트)."""
    values = [raw.get("unresolved")] + [
        ev.get("unresolved") for ev in raw.get("audit") or []
        if isinstance(ev, Mapping) and ev.get("event") == "done"
    ]
    found: list[str] = []
    for u in values:
        if isinstance(u, list):
            found.extend(str(x) for x in u)
        elif u:
            found.append(str(u))
    return list(dict.fromkeys(found))


# ─────────────────────────────── 수치 주장 (HALLU) ───────────────────────────────

# 단위 → (계열, 지수). 바이트 계열은 1024·1000 두 기수를 모두 허용한다(MB↔GB 환산).
_UNITS: dict[str, tuple[str, int]] = {
    "%": ("pct", 0), "퍼센트": ("pct", 0),
    "b": ("byte", 0), "byte": ("byte", 0), "bytes": ("byte", 0), "바이트": ("byte", 0),
    "kb": ("byte", 1), "kib": ("byte", 1), "mb": ("byte", 2), "mib": ("byte", 2),
    "gb": ("byte", 3), "gib": ("byte", 3), "tb": ("byte", 4), "tib": ("byte", 4),
    "ms": ("time", -3), "밀리초": ("time", -3),
    "s": ("time", 0), "sec": ("time", 0), "초": ("time", 0),
    "건": ("count", 0), "회": ("count", 0),
}
_FAMILY_BASES: dict[str, tuple[int, ...]] = {"byte": (1024, 1000), "time": (10,), "pct": (1,),
                                             "count": (1,)}
_UNIT_ALT = "|".join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True))
_NUM = r"\d+(?:,\d{3})*(?:\.\d+)?"
# 숫자 앞이 영숫자·`_`·`.`·`-`·`/`·`:`면 식별자·경로·시각의 일부라 주장이 아니다
# (예: `app01`·`T-15m`).
_CLAIM_RE = re.compile(rf"(?<![A-Za-z0-9_.\-/:])({_NUM})\s?({_UNIT_ALT})(?![A-Za-z0-9])", re.I)
# 응답 문자열 속 수치 — 식별자(`app01`·`bw-d07`)·소수 뒷자리에 붙은 숫자는 뺀다.
_REPLY_NUM_RE = re.compile(rf"(?<![A-Za-z0-9_.\-]){_NUM}")
# 주장에서 빼는 구간(시각·날짜·버전·타임스탬프) — 순서대로 지운다(긴 형태 먼저).
_MASKS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?:[T ]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?"),
    re.compile(r"\d{1,2}:\d{2}(?::\d{2})?"),
    re.compile(r"\d{1,2}\s*시\s*\d{1,2}\s*분(?:\s*\d{1,2}\s*초)?"),
    re.compile(r"\d{4}\s*년|\d{1,2}\s*월|\d{1,2}\s*일|\d{1,2}\s*시(?!간)"),
    re.compile(r"\b\d{8,14}\b"),
    re.compile(r"[vV]\d+(?:\.\d+)*|\d+(?:\.\d+){2,}"),
)


@dataclass(frozen=True)
class Num:
    """수치 1개와 소수 자릿수(반올림 허용 폭 계산용)."""

    value: Decimal
    decimals: int


@dataclass(frozen=True)
class Claim:
    """브리핑의 숫자+단위 주장 1건."""

    text: str
    num: Num
    unit: str


def _num(text: str) -> Num | None:
    s = text.replace(",", "")
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return Num(d, len(s.split(".", 1)[1]) if "." in s else 0)


def extract_claims(texts: Iterable[str], hosts: Iterable[str] = ()) -> list[Claim]:
    """서술에서 숫자+단위 주장을 뽑는다(같은 표기는 한 번 · 처음 본 순서).

    먼저 호스트명(대소문자 무시)과 시각·날짜·버전·타임스탬프 구간을 지운 뒤 `_CLAIM_RE`로 찾는다.
    단위 표 `_UNITS`에 없는 단위(분·시간·일 같은 기간 · `m`)는 주장이 아니다 — 조사 서술의
    분 단위 수는 대부분 조회 구간·상대 시각이다.
    """
    ordered = sorted({h for h in hosts if h}, key=len, reverse=True)
    host_res = [re.compile(re.escape(h), re.I) for h in ordered]
    seen: dict[str, Claim] = {}
    for text in texts:
        s = unicodedata.normalize("NFKC", text)
        for pat in (*host_res, *_MASKS):
            s = pat.sub(" ", s)
        for m in _CLAIM_RE.finditer(s):
            num = _num(m.group(1))
            unit = m.group(2).casefold()
            if num is None or unit not in _UNITS:
                continue
            key = f"{m.group(1).replace(',', '')}{unit}"
            seen.setdefault(key, Claim(f"{m.group(1)}{m.group(2)}", num, unit))
    return list(seen.values())


def collect_numbers(node: Any, out: list[Num]) -> None:
    """응답·이벤트의 수치 전부(숫자 값 + 문자열 속 숫자 — 시각·날짜·버전·식별자 속 숫자 제외)."""
    if isinstance(node, bool) or node is None:
        return
    if isinstance(node, (int, float)):
        n = _num(repr(node) if isinstance(node, float) else str(node))
        if n is not None:
            out.append(n)
    elif isinstance(node, str):
        s = unicodedata.normalize("NFKC", node)
        for pat in _MASKS:  # 주장 쪽과 같은 시각·날짜·버전 마스크(응답의 시각 숫자는 근거가 아니다)
            s = pat.sub(" ", s)
        for m in _REPLY_NUM_RE.finditer(s):
            n = _num(m.group(0))
            if n is not None:
                out.append(n)
    elif isinstance(node, Mapping):
        for v in node.values():
            collect_numbers(v, out)
    elif isinstance(node, (list, tuple)):
        for v in node:
            collect_numbers(v, out)


def _half_ulp(decimals: int) -> Decimal:
    return Decimal(5) * Decimal(10) ** (-(decimals + 1))


def claim_supported(claim: Claim, numbers: Sequence[Num]) -> bool:
    """주장이 수치 목록 어딘가와 맞는가 — 반올림 · 같은 계열 단위 환산 허용.

    응답 쪽 단위는 모르므로 응답 수치를 같은 계열의 각 단위로 읽어 **주장 단위로 바꿔** 비교한다.
    - 같은 단위: 허용 폭 = max(주장 자릿수의 반 단위, 응답 자릿수의 반 단위) — 98 ↔ 98.4 ·
      98.4 ↔ 98(폭 0.5) · 98.5 ↔ 98.4 불일치(폭 0.05).
    - 환산: 허용 폭 = 주장 자릿수의 반 단위만 — 2GB ↔ 2048(MB) · 1.5GB ↔ 1500(MB · 1000 기수) ·
      50ms ↔ 0.05(s). 응답 정수의 반 단위를 환산 뒤에 쓰면 0·1 같은 흔한 값이 812ms·700MB를
      뒷받침하므로 쓰지 않는다(j2).
    """
    family, exp = _UNITS[claim.unit]
    exps = sorted({e for f, e in _UNITS.values() if f == family})
    eps = Decimal("1e-9")
    claim_tol = _half_ulp(claim.num.decimals)
    for n in numbers:
        if abs(claim.num.value - n.value) <= max(claim_tol, _half_ulp(n.decimals)) + eps:
            return True
        for base in _FAMILY_BASES[family]:
            for other in exps:
                if other == exp:
                    continue
                converted = n.value * Decimal(base) ** (other - exp)  # 응답(단위 other) → 주장 단위
                if abs(claim.num.value - converted) <= claim_tol + eps:
                    return True
    return False


# ─────────────────────────────── 채점 ───────────────────────────────


def _valid(raw: Mapping[str, Any]) -> tuple[bool, str | None]:
    status = str(raw.get("status") or "")
    if raw.get("infra_error"):
        return False, f"infra_error: {raw.get('infra_error')}"
    if status not in VALID_STATUSES:
        return False, f"status={status or '없음'}"
    briefing = raw.get("briefing")
    if status == "done" and (not isinstance(briefing, Mapping) or briefing.get("stub")):
        return False, "done인데 브리핑 없음·stub"
    return True, None


def _evidence_insufficient(briefing: Mapping[str, Any]) -> bool:
    sev = briefing.get("severity")
    return bool(briefing.get("evidence_insufficient")) or (
        isinstance(sev, Mapping) and bool(sev.get("evidence_insufficient")))


def abstained(briefing: Mapping[str, Any], hyps: Sequence[Mapping[str, Any]]) -> bool:
    """단정하지 않았는가 — 증거 불충분 표기 또는 최고 confidence ≤ `low`(가설 없음 포함)."""
    if _evidence_insufficient(briefing):
        return True
    ranks = [CONFIDENCE_RANK.get(str(h.get("confidence") or "").strip().casefold(), 2)
             for h in hyps]
    return not ranks or max(ranks) <= ABSTAIN_MAX_CONFIDENCE_RANK


def _label_summary(world: World) -> dict[str, Any]:
    entity = world.label.get("root_entity") or {}
    return {"host": entity.get("host"), "component": entity.get("component"),
            "kind": world.label.get("kind"), "abstain": bool(world.expect.get("abstain"))}


def _invalid_row(base: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        **base, "valid": False, "rc1": None, "rc3": None, "entity_hit": None, "kind_hit": None,
        "component_hit": None, "ecr": None, "hallu": None, "dist": None, "abst_ok": None,
        "abstained": None, "complete": False, "failure_code": "F-INFRA", "top1": None,
        "judge_version": JUDGE_VERSION, "notes": [reason],
    }


def score_one(raw: Mapping[str, Any], world: World, replies: Mapping[str, Any]) -> dict[str, Any]:
    """조사 실행 1건 → scores 1행(§4.9)."""
    trace = _trace(raw)
    base: dict[str, Any] = {
        "arm": raw.get("arm"), "world_id": raw.get("world_id"),
        "split": raw.get("split") or world.split, "trial": raw.get("trial"),
        "investigation_id": raw.get("investigation_id"),
        "label": _label_summary(world),
        "cost": {"elapsed_s": raw.get("elapsed_s"), "steps": raw.get("steps"),
                 "tokens": raw.get("tokens"), "tool_calls": len(trace)},
    }
    ok, reason = _valid(raw)
    if not ok:
        return _invalid_row(base, reason or "무효")

    notes: list[str] = []
    status = str(raw.get("status"))
    got_briefing = raw.get("briefing")
    briefing: Mapping[str, Any] = got_briefing if isinstance(got_briefing, Mapping) else {}
    if briefing.get("stub"):
        briefing = {}
    hyps = ranked_hypotheses(briefing)
    expect_abstain = bool(world.expect.get("abstain"))

    # RC
    has_label = bool(world.label.get("kind"))
    matches = [match_hypothesis(h, world) for h in hyps[:RC_TOP_K]]
    top = matches[0] if matches else None
    rc1 = rc3 = None
    if has_label:
        rc1 = bool(top and top.kind_hit and top.host_hit)
        rc3 = any(m.kind_hit and m.host_hit for m in matches)
    dist = bool(top and top.dist_kind)
    top1 = None
    if hyps and top is not None:
        kind_key = (str(world.label.get("kind")) if top.kind_hit else
                    top.dist_kind if top.dist_kind else "?" + norm(hyps[0].get("cause") or ""))
        top1 = {"cause": str(hyps[0].get("cause") or ""), "host": top.host, "kind": kind_key,
                "confidence": hyps[0].get("confidence")}

    # ECR
    narrative = narrative_texts(briefing)
    narr_norm = norm(" ".join(narrative))
    kes = world.key_evidence
    got = [delivered(ke, trace, replies) for ke in kes]
    cited = [g and all(norm(c) in narr_norm for c in ke.get("cite") or [])
             for ke, g in zip(kes, got, strict=True)]
    ecr = None
    if kes:
        ecr = {"num": sum(cited), "den": len(kes), "value": round(sum(cited) / len(kes), 4)}

    # HALLU
    numbers: list[Num] = []
    for t in trace:
        collect_numbers(replies.get(str(t.get("reply_hash"))), numbers)
    collect_numbers(world.event, numbers)
    claims = extract_claims(narrative, world_hosts(world))
    bad = [c.text for c in claims if not claim_supported(c, numbers)]
    hallu = {"num": len(bad), "den": len(claims), "claims": bad}

    # ABST
    did_abstain = abstained(briefing, hyps)
    abst_ok = did_abstain == expect_abstain

    incomplete = bool(raw.get("incomplete"))
    complete = status == "done" and not incomplete

    # 실패 유형 근거
    ke_tools = {str(ke.get("tool")) for ke in kes}
    ke_calls = [t for t in trace if str(t.get("tool")) in ke_tools] if kes else list(trace)
    arg_fail = bool(ke_calls) and all(t.get("arg_error") for t in ke_calls)
    # 정체 가드 기록이 있으면 그것만 쓴다(같은 도구를 두 번 적지 않게)
    loop = _unresolved(raw) or _repeats(trace)
    miss = bool(kes) and not any(got)
    if arg_fail:
        notes.append("핵심 증거 도구 호출이 모두 인자 오류: "
                     + ", ".join(sorted({str(t.get("arg_error")) for t in ke_calls})))
    if loop:
        notes.append("반복·정체: " + ", ".join(loop))
    if miss:
        notes.append("핵심 증거를 받은 적 없음: " + ", ".join(sorted(ke_tools)))
    if top and top.dist_kind:
        notes.append(f"1위 = 유인 {top.dist_kind}")
    if top and has_label and not rc1:
        notes.append(f"1위 대조 — kind {'적중' if top.kind_hit else '불일치'} · "
                     f"호스트 {'적중' if top.host_hit else '불일치'}")
    if bad:
        notes.append("근거 없는 수치: " + ", ".join(bad))
    if ecr is not None and ecr["value"] < ECR_PASS_MIN:
        notes.append(f"ECR {ecr['num']}/{ecr['den']}")
    if not abst_ok:
        notes.append("기권 오류 — " + ("무장애·근거 결손 세계에 단정" if expect_abstain
                                     else "근거 충분 세계에서 불충분·저신뢰"))

    code = classify(
        status=status, incomplete=incomplete, expect_abstain=expect_abstain, rc1=bool(rc1),
        arg_fail=arg_fail, loop=bool(loop), miss=miss, dist=dist, hallu_n=len(bad),
        ecr=None if ecr is None else float(ecr["value"]), abst_ok=abst_ok,
    )
    return {
        **base, "valid": True, "rc1": rc1, "rc3": rc3,
        "entity_hit": None if top is None else top.host_hit,
        "kind_hit": None if top is None else top.kind_hit,
        "component_hit": None if top is None else top.component_hit,
        "ecr": ecr, "hallu": hallu, "dist": dist, "abst_ok": abst_ok, "abstained": did_abstain,
        "complete": complete, "failure_code": code, "top1": top1,
        "judge_version": JUDGE_VERSION, "notes": notes,
    }


def classify(*, status: str, incomplete: bool, expect_abstain: bool, rc1: bool, arg_fail: bool,
             loop: bool, miss: bool, dist: bool, hallu_n: int, ecr: float | None,
             abst_ok: bool) -> str:
    """유효 실행의 실패 유형 — 가장 앞선 하나(F-INFRA는 `_valid`가 먼저 가른다).

    순서: F-TMO > F-INC > [RC@1 미적중] F-ARG > F-LOOP > F-MISS > F-DIST > F-READ >
    [RC@1 적중] F-HALLU > F-CITE > F-ABST > PASS.

    - §4.5 표는 F-READ가 F-DIST 앞이지만 그러면 핵심 증거를 받은 실행에서 F-DIST가 나올 수 없다 —
      「1위가 유인이면 F-DIST, 아니면 F-READ」로 둔다(팀 리드 확정 2026-10-13).
    - 기권 기대 세계(`expect.abstain`)는 RC 대신 기권 여부로 가른다: 단정했으면 1위가 유인일 때
      F-DIST, 아니면 F-ABST. 기권했으면 F-HALLU > F-CITE > PASS.
    """
    if status == TIMEOUT_STATUS:
        return "F-TMO"
    if incomplete:
        return "F-INC"
    cite_low = ecr is not None and ecr < ECR_PASS_MIN
    if expect_abstain:
        if not abst_ok:
            return "F-DIST" if dist else "F-ABST"
        if hallu_n:
            return "F-HALLU"
        return "F-CITE" if cite_low else "PASS"
    if not rc1:
        if arg_fail:
            return "F-ARG"
        if loop:
            return "F-LOOP"
        if miss:
            return "F-MISS"
        return "F-DIST" if dist else "F-READ"
    if hallu_n:
        return "F-HALLU"
    if cite_low:
        return "F-CITE"
    if not abst_ok:
        return "F-ABST"
    return "PASS"


def score_run(raw_rows: list[dict[str, Any]], world_calls: list[dict[str, Any]],
              worlds: Mapping[str, World]) -> list[dict[str, Any]]:
    """실행 전체 채점 — raw 순서를 유지한다. raw의 세계가 `worlds`에 없으면 KeyError."""
    replies = reply_index(world_calls)
    out: list[dict[str, Any]] = []
    for raw in raw_rows:
        wid = str(raw.get("world_id"))
        if wid not in worlds:
            raise KeyError(f"세계 YAML 없음: {wid}")
        out.append(score_one(raw, worlds[wid], replies))
    return out


# ─────────────────────────────── 파일 입출력 ───────────────────────────────


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """JSONL 읽기(없으면 빈 목록 · 빈 줄 무시)."""
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def dumps_jsonl(rows: Iterable[Mapping[str, Any]]) -> str:
    """결정적 JSONL 직렬화(키 순서 = 삽입 순서 · 한글 그대로)."""
    return "".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rows)


def write_scores(path: Path, rows: Iterable[Mapping[str, Any]]) -> Path:
    """`scores.jsonl` 쓰기 — 러너와 `rejudge`가 같은 직렬화를 쓴다."""
    p = Path(path)
    p.write_text(dumps_jsonl(rows), encoding="utf-8")
    return p


def _preserve_previous(scores: Path) -> Path | None:
    """기존 `scores.jsonl`을 `scores.<판>.jsonl`로 옮긴다(같은 이름에 다른 내용이 있으면 번호)."""
    if not scores.exists():
        return None
    prev = read_jsonl(scores)
    version = str(prev[0].get("judge_version") or "unknown") if prev else "unknown"
    content = scores.read_bytes()
    n = 1
    while True:
        name = f"scores.{version}.jsonl" if n == 1 else f"scores.{version}.{n}.jsonl"
        target = scores.with_name(name)
        if not target.exists() or target.read_bytes() == content:
            scores.replace(target)
            return target
        n += 1


def rejudge(run_dir: Path, worlds_root: Path) -> Path:
    """run(또는 `.bundle`) 디렉터리를 현재 채점기·세계 YAML로 다시 채점한다(§4.9 `--rejudge`).

    `raw.jsonl`·`world_calls.jsonl`만 입력이다 — 같은 입력이면 같은 `scores.jsonl` 바이트다.
    이전 판은 `scores.<judge_version>.jsonl`로 보존하고, 끝에 `report.md`를 다시 쓴다.
    반환 = scores 경로.
    """
    from investigation_bench import report  # report가 이 모듈을 쓰지 않도록 늦게 import한다

    run_dir = Path(run_dir)
    raw_rows = read_jsonl(run_dir / "raw.jsonl")
    calls = read_jsonl(run_dir / "world_calls.jsonl")
    worlds = load_worlds([Path(worlds_root)])
    rows = score_run(raw_rows, calls, worlds)
    scores = run_dir / "scores.jsonl"
    _preserve_previous(scores)
    write_scores(scores, rows)
    report.write_report(run_dir)
    return scores
