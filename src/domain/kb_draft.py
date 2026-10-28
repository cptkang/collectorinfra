"""장애 대응 지식(KB) 쓰기·참조 측 도메인 (plans/187 W9 · §5.7.4~§5.7.5 · D-341 ⑩⑮⑳) — 순수 함수.

조사 브리핑(sre_agent가 낸 `briefing.playbook`)과 본체 기록(KE 피드백·엔진 선택)만 입력으로 받아
다음을 **결정적으로** 만든다. LLM을 부르지 않는다(G-14 — 이번 Wave는 제안 기능도 없다).

1. KB 초안 — 알려진 오류 레코드(§5.7.3 스키마) 조각. `state`는 항상 `wip`이고, `match` 후보는
   브리핑 `readings.values`에서 **실제로 성립한 판독 enum**만 표현식 문자열로 옮긴다.
   `cause_test`·`workaround`는 빈 칸(사람이 채운다). 원인 기본값은 운영자 엔진 선택을 따른다
   (§5.0.1 7번). 입력 브리핑에 `state`·`match` 같은 키가 섞여 있어도 읽지 않는다.
2. YAML 조각 직렬화 — 스키마 순서 · 「사람 검토 후 not_validated 이상으로 올림」 주석.
3. 포스트모템 — 작성 기준(SRE Book 15장 중 판정 가능한 것 · G-16) 평가와 PagerDuty 템플릿 절
   Markdown. 기준을 충족한 건만 내보낸다(호출부가 거부 사유를 노출한다).
4. 엔진 선택 통계·전환 권고(G-27) · KE 「아님」 연속 · KE 재발 신호·검토 대상 · 런북 커버리지.
5. 매뉴얼 RAG 참고 인용의 질의 조립·정렬(G-15 — 참고 목록만, 판정·종합 입력이 아니다).

표준 라이브러리만 의존한다. 마스킹 함수는 호출부가 주입한다(domain은 security 계층을 모른다).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

# ── 초안 ─────────────────────────────────────────────────────────────────────

#: 초안 상태(KCS 4상태 중 첫 단계) — 코드가 이 값 외를 쓰지 않는다(승격은 사람만 · §5.7.5 2).
DRAFT_STATE = "wip"
DRAFT_GOVERNANCE = "experience"
#: 알려진 오류 검토 주기 기본값(D-341 ⑩ — KE 90일 [제안 값]).
DRAFT_REVIEW_EVERY_DAYS = 90
#: 원인 문장 뒤 고정 표기 — 1순위 가설은 사람이 확인하기 전까지 원인이 아니다.
UNVERIFIED_MARK = "(미검증 — 사람 확인 전)"
REVIEW_COMMENT = (
    "사람 검토 후 not_validated 이상으로 올림 — state·match·cause_test는 사람만 바꾼다"
)
#: 초안 타임라인 출처 단계(§5.3 S5 `xsource.related_alarms`).
TIMELINE_STEP = "S5"

#: 원인 기본값 출처(초안 레코드 `cause_source`).
CAUSE_FROM_PLAYBOOK = "playbook"
CAUSE_FROM_HOLMESGPT = "holmesgpt"
#: (plans/198 W7 I-8) KEDB 부트스트랩 — 운영 라벨이 확정한 원인.
CAUSE_FROM_LABEL = "label"
CAUSE_EMPTY = "empty"

# ── 엔진 선택 (§5.0.1 7번 · G-27) ────────────────────────────────────────────

ENGINE_PLAYBOOK = "playbook"
ENGINE_HOLMESGPT = "holmesgpt"
ENGINE_BOTH = "both"
ENGINE_NEITHER = "neither"
ENGINE_CHOICES: tuple[str, ...] = (ENGINE_PLAYBOOK, ENGINE_HOLMESGPT, ENGINE_BOTH, ENGINE_NEITHER)
#: 「플레이북이 맞다」로 세는 선택(⑳ — 「플레이북」+「둘 다」).
ENGINE_PLAYBOOK_SIDE: frozenset[str] = frozenset({ENGINE_PLAYBOOK, ENGINE_BOTH})

# ── 알려진 오류 사용 피드백 (KCS Reuse is Review · §5.7.5 3) ──────────────────

KE_FEEDBACK_CORRECT = "correct"                    # 맞음
KE_FEEDBACK_INCORRECT = "incorrect"                # 아님
KE_FEEDBACK_NO_EFFECT = "workaround_ineffective"   # 우회 효과 없음
KE_FEEDBACK_LABELS: tuple[str, ...] = (
    KE_FEEDBACK_CORRECT, KE_FEEDBACK_INCORRECT, KE_FEEDBACK_NO_EFFECT,
)

#: 브리핑 `known_errors[].result` 중 「일치」(§5.7.6).
KE_RESULT_MATCH = "match"
#: 재발 신호 집계 창(일 · §5.7.3 — RCACopilot 재발 20일 내 근거 · 30일 표기).
RECURRENCE_DAYS = 30
#: 일치 0건 검토 대상 창(일 · §5.7.5 4).
UNMATCHED_DAYS = 180

# ── 포스트모템 작성 기준 (SRE Book 15장 · G-16) — 판정 가능한 것만 [제안 값] ──────
#
# SRE Book 5항(사용자 영향 임계 초과 · 데이터 손실 · 당직 개입 · 해결 시간 임계 초과 · 모니터링
# 실패) 중 본체 기록으로 결정적으로 판정할 수 있는 것만 둔다. 데이터 손실·모니터링 실패는 기록이
# 없고, 해결 시간은 조사·결정 기록에 해소 시각이 없어 판정하지 않는다. 채택 기준은 **전부** 충족해야
# 한다 — 자동 조사는 PAGE에서만 돌아 「PAGE」 단독은 거의 모든 건이 충족하기 때문이다.

PM_PAGE = "page"
PM_IMPACT = "impact"
#: 채택 기준 [제안 값].
POSTMORTEM_CRITERIA: tuple[str, ...] = (PM_PAGE, PM_IMPACT)
#: 「사용자 영향」으로 보는 조사 판정 등급 [제안 값] — 중요도 판정 어휘(심각) + 영문 표기.
POSTMORTEM_IMPACT_LEVELS: frozenset[str] = frozenset({"심각", "critical"})
#: 「당직 개입」 근사 — 결정 기록 티어(PAGE = 당직 호출).
POSTMORTEM_PAGE_TIER = "page"
#: PagerDuty 포스트모템 템플릿 절(§3.7 [공식 문서]).
PAGERDUTY_SECTIONS: tuple[str, ...] = (
    "Overview", "What Happened", "Contributing Factors", "Resolution", "Impact",
    "Responders", "Timeline", "How'd We Do?", "Action Items", "Messaging",
)
HUMAN_FILL = "(사람이 작성)"

# ── 매뉴얼 RAG 참고 인용 (§5.7.4 · G-15) ─────────────────────────────────────

REFERENCE_LIMIT = 3
EXCERPT_MAX_CHARS = 200
REFERENCES_NOTICE = "참고 · 판정 무관"

# 판독 이름·enum 값으로 받는 토큰 — 공백·따옴표가 섞인 값은 표현식에 넣지 않는다.
_NAME_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
_VALUE_TOKEN = re.compile(r"[A-Za-z0-9_.\-]+")
#: 판정 불가 enum(R-D `n/a`) — 성립한 판독이 아니다.
_NOT_APPLICABLE = "n/a"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def playbook_of(briefing: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """브리핑의 `playbook` 블록(플레이북 조사가 아니면 None)."""
    pb = _mapping(briefing).get("playbook")
    return pb if isinstance(pb, Mapping) and _text(pb.get("id")) else None


def known_error_ids(briefing: Mapping[str, Any] | None) -> list[str]:
    """브리핑에 실린 알려진 오류 id 목록(순서 유지 · 중복 제거)."""
    out: list[str] = []
    for item in _mapping(playbook_of(briefing)).get("known_errors") or []:
        ke_id = _text(_mapping(item).get("id"))
        if ke_id and ke_id not in out:
            out.append(ke_id)
    return out


def match_candidates(readings: Mapping[str, Any] | None) -> list[str]:
    """`readings.values`에서 성립한 판독 enum만 `이름 == '값'` 표현식으로(이름순).

    `None`(판정 불가)·`n/a`·문자열이 아닌 값·토큰 형식이 아닌 값은 넣지 않는다.
    """
    values = _mapping(_mapping(readings).get("values"))
    out: list[str] = []
    for name in sorted(str(k) for k in values):
        value = values.get(name)
        if not isinstance(value, str) or not value or value == _NOT_APPLICABLE:
            continue
        if _NAME_TOKEN.fullmatch(name) and _VALUE_TOKEN.fullmatch(value):
            out.append(f"{name} == '{value}'")
    return out


def top_hypothesis(playbook: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """종합 가설 중 1순위(`rank` 최소 · 없으면 첫 항목) — 제목 있는 것만."""
    hyps = [
        h for h in _mapping(_mapping(playbook).get("synthesis")).get("hypotheses") or []
        if isinstance(h, Mapping) and _text(h.get("title"))
    ]
    if not hyps:
        return None

    def _rank(h: Mapping[str, Any]) -> float:
        rank = h.get("rank")
        return float(rank) if isinstance(rank, (int, float)) and not isinstance(rank, bool) else 1e9

    return min(hyps, key=_rank)


def deep_dive_cause(briefing: Mapping[str, Any] | None) -> str:
    """HolmesGPT 결과(`deep_dive`)의 원인 문장 — 최상위 `deep_dive` → `playbook.deep_dive` 순.

    `deep_dive`는 브리핑 6요소 모양이라 `cause`를 읽는다(W4 계약 확정 전 가정 — 없으면 빈 문자열).
    """
    for holder in (_mapping(briefing), _mapping(playbook_of(briefing))):
        cause = _text(_mapping(holder.get("deep_dive")).get("cause"))
        if cause:
            return cause
    return ""


def cause_default(
    briefing: Mapping[str, Any] | None, engine_choice: str | None
) -> tuple[str, str]:
    """초안 원인 기본값 `(문장, 출처)` — 운영자 엔진 선택(§5.0.1 7번)을 따른다.

    - 선택 없음·`playbook`·`both` → 플레이북 1순위 가설 + 「미검증」 표기
    - `holmesgpt` → HolmesGPT 원인 + 「미검증」 표기(없으면 빈 칸)
    - `neither` → 빈 칸(두 결론 모두 아니라고 했다 — 사람이 채운다)
    """
    if engine_choice == ENGINE_NEITHER:
        return "", CAUSE_EMPTY
    if engine_choice == ENGINE_HOLMESGPT:
        cause = deep_dive_cause(briefing)
        return (f"{cause} {UNVERIFIED_MARK}", CAUSE_FROM_HOLMESGPT) if cause else ("", CAUSE_EMPTY)
    hyp = top_hypothesis(playbook_of(briefing))
    if hyp is None:
        return "", CAUSE_EMPTY
    return f"{_text(hyp.get('title'))} {UNVERIFIED_MARK}", CAUSE_FROM_PLAYBOOK


def timeline_rows(playbook: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """S5 섹션 표 → `[{열: 값}]`(없으면 빈 목록)."""
    for section in _mapping(playbook).get("sections") or []:
        sec = _mapping(section)
        if sec.get("step") != TIMELINE_STEP:
            continue
        table = _mapping(sec.get("table"))
        columns = [str(c) for c in table.get("columns") or []]
        rows: list[dict[str, Any]] = []
        for row in table.get("rows") or []:
            if isinstance(row, Sequence) and not isinstance(row, str) and columns:
                rows.append(dict(zip(columns, row, strict=False)))
        return rows
    return []


def alarm_issue(alarm: Mapping[str, Any] | None) -> str:
    """KE `issue`(증상 — 알람 원문): 알람명 + 조건 원문(있으면)."""
    info = _mapping(alarm)
    name = _text(info.get("alarm_name"))
    condition = _text(info.get("condition_log"))
    if name and condition:
        return f"{name} — {condition}"
    return name or condition


def build_draft(
    *,
    draft_id: str,
    investigation_id: str,
    briefing: Mapping[str, Any],
    alarm: Mapping[str, Any] | None,
    engine_choice: str | None,
    created_by: str,
    created_at: str,
) -> dict[str, Any]:
    """완료된 조사 브리핑 → KB 초안 레코드(`ke` = §5.7.3 스키마 순서 조각 + 메타).

    입력 브리핑의 `state`·`match`·`cause_test` 같은 키는 읽지 않는다 — 상태는 `wip` 고정, `match`는
    판독 enum에서만, `cause_test`·`workaround`는 빈 칸이다.

    Raises:
        ValueError: 브리핑에 플레이북 블록이 없을 때.
    """
    pb = playbook_of(briefing)
    if pb is None:
        raise ValueError("플레이북 브리핑이 아닙니다")
    playbook_id = _text(pb.get("id"))
    scope = _mapping(pb.get("scope"))
    runbook = _mapping(pb.get("runbook"))
    info = _mapping(alarm)
    cause, cause_source = cause_default(briefing, engine_choice)
    instance = _text(scope.get("alarm_instance"))
    solution = _text(scope.get("solution"))
    ke: dict[str, Any] = {
        "id": f"KE-DRAFT-{draft_id}",
        "title": _text(info.get("alarm_name")) or playbook_id,
        "state": DRAFT_STATE,
        "governance": DRAFT_GOVERNANCE,
        "issue": alarm_issue(info),
        "environment": {
            "playbook": playbook_id,
            "targets": {
                "solution": [solution] if solution else [],
                "instance_glob": [instance] if instance else [],
            },
            "time_window": {},
        },
        "match": {"all": match_candidates(_mapping(pb.get("readings")))},
        "cause": cause,
        "cause_test": {"all": []},
        "workaround": [],
        "permanent_fix": {"status": "open", "problem_ref": ""},
        "owner": _text(runbook.get("owner")),
        "last_reviewed": None,
        "review_every_days": DRAFT_REVIEW_EVERY_DAYS,
    }
    window = _mapping(scope.get("incident_window"))
    return {
        "draft_id": draft_id,
        "investigation_id": investigation_id,
        "playbook_id": playbook_id,
        "created_by": created_by,
        "created_at": created_at,
        "engine_choice": engine_choice,
        "cause_source": cause_source,
        "alarm": {
            k: info.get(k) for k in ("alarm_id", "alarm_name", "server_name", "tier")
            if info.get(k)
        },
        "scope": {
            "host": scope.get("host"),
            "polestar_server": scope.get("polestar_server"),
            "alarm_instance": scope.get("alarm_instance"),
            "incident_window": dict(window) if window else None,
        },
        "timeline": timeline_rows(pb),
        "ke": ke,
    }


def _q(value: Any) -> str:
    """YAML 큰따옴표 스칼라(JSON 문자열 표기는 YAML 큰따옴표 스칼라와 호환된다)."""
    return json.dumps("" if value is None else str(value), ensure_ascii=False)


def _flow_list(values: Iterable[Any]) -> str:
    return "[" + ", ".join(_q(v) for v in values) + "]"


def _comment(text: Any) -> str:
    return " ".join(str(text).split())


def draft_to_yaml(draft: Mapping[str, Any]) -> str:
    """초안 → 알려진 오류 YAML 조각(§5.7.3 스키마 순서 · `state: wip` 고정 · 검토 주석).

    `state`는 레코드 값이 아니라 상수(`DRAFT_STATE`)를 쓴다 —
    저장 레코드가 바뀌어도 내보내기는 wip다.
    타임라인은 스키마 밖이라 주석으로만 싣는다.
    """
    ke = _mapping(draft.get("ke"))
    env = _mapping(ke.get("environment"))
    targets = _mapping(env.get("targets"))
    fix = _mapping(ke.get("permanent_fix"))
    lines = [
        f"# KB 초안(알려진 오류 레코드 조각 · plans/187 §5.7.3) — {REVIEW_COMMENT}",
        f"# 출처 조사 {_comment(draft.get('investigation_id'))} · 초안 "
        f"{_comment(draft.get('draft_id'))} · 생성 {_comment(draft.get('created_at'))} · "
        f"원인 기본값 출처 {_comment(draft.get('cause_source'))}",
        f"id: {_q(ke.get('id'))}",
        f"title: {_q(ke.get('title'))}",
        f"state: {DRAFT_STATE}",
        f"governance: {DRAFT_GOVERNANCE}",
        f"issue: {_q(ke.get('issue'))}",
        "environment:",
        f"  playbook: {_q(env.get('playbook'))}",
        "  targets: {solution: " + _flow_list(targets.get("solution") or [])
        + ", instance_glob: " + _flow_list(targets.get("instance_glob") or []) + "}",
        "  time_window: {}  # 사람이 채운다(요일·시간대)",
        "match:",
        "  # 후보 — 이 조사에서 실제 성립한 판독 enum 전부 · 사람이 골라 남긴다",
        "  all: " + _flow_list(_mapping(ke.get("match")).get("all") or []),
        f"cause: {_q(ke.get('cause'))}",
        "cause_test:",
        "  # 사람이 채운다(KCS Cause Test — 플레이북 결과로 결정적 확인)",
        "  all: []",
        "workaround: []  # 사람이 채운다 — action·risk·verification·rollback(명령 리터럴 금지)",
        "permanent_fix: {status: " + _q(fix.get("status") or "open")
        + ", problem_ref: " + _q(fix.get("problem_ref")) + "}",
        f"owner: {_q(ke.get('owner'))}",
        "last_reviewed: null  # 사람이 검토한 날(YYYY-MM-DD)",
        f"review_every_days: {int(ke.get('review_every_days') or DRAFT_REVIEW_EVERY_DAYS)}",
    ]
    timeline = draft.get("timeline") or []
    if timeline:
        lines.append(f"# 타임라인({TIMELINE_STEP} · 참고 — 스키마 밖이라 주석으로만 싣는다)")
        for row in timeline:
            cells = " · ".join(f"{k}={_comment(v)}" for k, v in _mapping(row).items())
            lines.append(f"#   - {cells}")
    return "\n".join(lines) + "\n"


# ── 포스트모템 ───────────────────────────────────────────────────────────────


def evaluate_postmortem(
    *,
    tier: str | None,
    verdict_level: str | None,
    criteria: Sequence[str] = POSTMORTEM_CRITERIA,
    impact_levels: Iterable[str] = POSTMORTEM_IMPACT_LEVELS,
) -> dict[str, Any]:
    """포스트모템 작성 기준 평가 — `{eligible, criteria[{id,label,met,detail}], reason}`.

    채택 기준 **전부**를 충족해야 `eligible`이다. 미충족이면 `reason`에 빠진 기준을 적는다.
    """
    levels = frozenset(impact_levels)
    table = {
        PM_PAGE: (
            "당직 호출(PAGE)",
            str(tier or "").lower() == POSTMORTEM_PAGE_TIER,
            f"결정 기록 티어 {tier or '미상'}",
        ),
        PM_IMPACT: (
            "사용자 영향(조사 판정 등급)",
            bool(verdict_level) and str(verdict_level) in levels,
            f"조사 판정 등급 {verdict_level or '미상'} · 기준 {'/'.join(sorted(levels))}",
        ),
    }
    items = [
        {"id": cid, "label": table[cid][0], "met": table[cid][1], "detail": table[cid][2]}
        for cid in criteria if cid in table
    ]
    unmet = [i for i in items if not i["met"]]
    eligible = bool(items) and not unmet
    reason = "" if eligible else "포스트모템 작성 기준 미충족 — " + "; ".join(
        f"{i['label']}: {i['detail']}" for i in unmet
    )
    return {"eligible": eligible, "criteria": items, "reason": reason}


def _mask_deep(value: Any, mask_fn: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return mask_fn(value)
    if isinstance(value, Mapping):
        return {k: _mask_deep(v, mask_fn) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_deep(v, mask_fn) for v in value]
    return value


def _action_text(action: Any) -> str:
    if isinstance(action, str):
        return action.strip()
    item = _mapping(action)
    for key in ("action", "title", "text"):
        text = _text(item.get(key))
        if text:
            return text
    return ""


def render_postmortem(
    *,
    investigation_id: str,
    briefing: Mapping[str, Any],
    alarm: Mapping[str, Any] | None,
    evaluation: Mapping[str, Any],
    verdict_level: str | None,
    mask_fn: Callable[[str], str] | None = None,
) -> str:
    """PagerDuty 템플릿 절 Markdown(결정적 · 사람이 쓸 절은 빈 칸 표기).

    `mask_fn`을 주면 브리핑·알람 문자열을 값 단위로 가린 뒤 렌더한다(사용자 경로).
    """
    if mask_fn is not None:
        briefing = _mask_deep(dict(briefing), mask_fn)
        alarm = _mask_deep(dict(_mapping(alarm)), mask_fn)
    pb = _mapping(playbook_of(briefing))
    scope = _mapping(pb.get("scope"))
    info = _mapping(alarm)
    title = _text(info.get("alarm_name")) or _text(pb.get("id")) or investigation_id
    body: dict[str, list[str]] = {name: [] for name in PAGERDUTY_SECTIONS}
    body["Overview"] = [
        f"- 알람: {title}",
        f"- 대상: 호스트 {scope.get('host') or '미상'} · "
        f"인스턴스 {scope.get('alarm_instance') or '미상'}",
        f"- 플레이북: {pb.get('id') or '없음'} · 조사: {investigation_id}",
    ]
    for section in pb.get("sections") or []:
        sec = _mapping(section)
        verdict = sec.get("verdict")
        body["What Happened"].append(
            f"- {sec.get('step') or '?'} {sec.get('title') or ''} — "
            f"상태 {sec.get('status') or '미상'}"
            + (f" · 판정 {verdict}" if verdict else "")
        )
    for hyp in _mapping(pb.get("synthesis")).get("hypotheses") or []:
        h = _mapping(hyp)
        if _text(h.get("title")):
            label = h.get("status_label") or h.get("status") or "미검증"
            body["Contributing Factors"].append(
                f"- {h.get('rank') or '-'}. {h['title']} ({label}) — 미검증 가설"
            )
    body["Impact"] = [f"- 조사 판정 등급: {verdict_level or '미상'}"] + [
        f"- 작성 기준 충족: {c.get('label')} — {c.get('detail')}"
        for c in evaluation.get("criteria") or [] if _mapping(c).get("met")
    ]
    for row in timeline_rows(pb):
        body["Timeline"].append("- " + " · ".join(f"{k}={_comment(v)}" for k, v in row.items()))
    for action in _mapping(pb.get("synthesis")).get("actions") or []:
        text = _action_text(action)
        if text:
            body["Action Items"].append(f"- (제안) {text}")
    lines = [
        f"# 포스트모템 초안 — {title}",
        "",
        "> 조사 브리핑에서 결정적으로 내보낸 초안이다(비난 없는 원칙 · 사람이 검토·보완한다). "
        "원인은 미검증 가설이다.",
    ]
    for name in PAGERDUTY_SECTIONS:
        lines += ["", f"## {name}", *(body[name] or [HUMAN_FILL])]
    return "\n".join(lines) + "\n"


# ── 엔진 선택 통계 (G-27 · ⑳) ─────────────────────────────────────────────────


def latest_choices(choices: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """(조사, 사용자)별 최신 선택만(입력 순서 = 시간 순서 · 마지막이 이긴다)."""
    latest: dict[tuple[str, str], Mapping[str, Any]] = {}
    for rec in choices:
        key = (str(rec.get("investigation_id") or ""), str(rec.get("chosen_by") or ""))
        if key[0] and rec.get("choice") in ENGINE_CHOICES:
            latest.pop(key, None)
            latest[key] = rec
    return list(latest.values())


def engine_stats(
    choices: Iterable[Mapping[str, Any]],
    *,
    min_samples: int,
    switch_ratio: float,
) -> list[dict[str, Any]]:
    """유형(플레이북 id)별 표본 수·선택 분포·「플레이북」+「둘 다」 비율·전환 권고.

    권고는 표본 ≥ `min_samples` ∧ 비율 ≥ `switch_ratio`일 때만 참이다(자동 전환 없음 · 표시만).
    """
    by_type: dict[str, dict[str, int]] = {}
    for rec in latest_choices(choices):
        counts = by_type.setdefault(
            str(rec.get("playbook_id") or ""), dict.fromkeys(ENGINE_CHOICES, 0)
        )
        counts[str(rec["choice"])] += 1
    out: list[dict[str, Any]] = []
    for playbook_id in sorted(by_type):
        counts = by_type[playbook_id]
        samples = sum(counts.values())
        side = sum(counts[c] for c in ENGINE_PLAYBOOK_SIDE)
        out.append({
            "playbook_id": playbook_id,
            "samples": samples,
            "counts": counts,
            "playbook_ratio": round(side / samples, 4) if samples else None,
            # 부동소수 경계(24/30 = 0.8)를 정수 비교로 판정한다.
            "recommend_switch": samples >= min_samples and side >= switch_ratio * samples - 1e-9,
        })
    return out


# ── 알려진 오류 피드백 · 재발 · 검토 대상 ─────────────────────────────────────


def ke_feedback_review(
    feedback: Iterable[Mapping[str, Any]], *, streak: int
) -> list[dict[str, Any]]:
    """KE별 피드백 집계 — 끝에서부터 연속한 「아님」 수가 `streak` 이상이면 `needs_review`.

    입력 순서 = 시간 순서. 「아님」이 아닌 라벨이 끼면 연속이 끊긴다.
    """
    per: dict[str, dict[str, Any]] = {}
    for rec in feedback:
        ke_id = str(rec.get("ke_id") or "")
        label = rec.get("verdict")
        if not ke_id or label not in KE_FEEDBACK_LABELS:
            continue
        item = per.setdefault(ke_id, {
            "ke_id": ke_id, "counts": dict.fromkeys(KE_FEEDBACK_LABELS, 0),
            "incorrect_streak": 0, "last_ts": None,
        })
        item["counts"][label] += 1
        run = item["incorrect_streak"]
        item["incorrect_streak"] = run + 1 if label == KE_FEEDBACK_INCORRECT else 0
        item["last_ts"] = rec.get("ts")
    out = []
    for ke_id in sorted(per):
        item = per[ke_id]
        item["needs_review"] = item["incorrect_streak"] >= max(1, int(streak))
        out.append(item)
    return out


def parse_ts(value: Any) -> datetime | None:
    """ISO 8601 → aware datetime(시간대 없으면 UTC로 본다 · 실패 None)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def ke_activity(
    records: Iterable[Mapping[str, Any]], *, now: datetime, recurrence_days: int = RECURRENCE_DAYS
) -> dict[str, dict[str, Any]]:
    """조사 레코드(`{ts, briefing}` · 시간 순) → KE별 활동 —
    일치 건수(창 안)·최근 일치·처음/마지막 등장·
    최신 상태·검토 정보(브리핑에 실린 값만 · sre_agent 파일은 읽지 않는다)."""
    cutoff = now - timedelta(days=recurrence_days)
    out: dict[str, dict[str, Any]] = {}
    for rec in records:
        at = parse_ts(rec.get("ts"))
        for entry in _mapping(playbook_of(_mapping(rec.get("briefing")))).get("known_errors") or []:
            ke = _mapping(entry)
            ke_id = _text(ke.get("id"))
            if not ke_id:
                continue
            item = out.setdefault(ke_id, {
                "ke_id": ke_id, "count_30d": 0, "last_match": None,
                "first_seen": None, "last_seen": None,
            })
            if at is not None:
                item["first_seen"] = item["first_seen"] or at.isoformat()
                item["last_seen"] = at.isoformat()
            for key in ("state", "stale", "last_reviewed", "review_every_days", "owner"):
                if key in ke:
                    item[key] = ke.get(key)
            if ke.get("result") == KE_RESULT_MATCH and at is not None:
                item["last_match"] = at.isoformat()
                if at >= cutoff:
                    item["count_30d"] += 1
    return out


def recurrence_of(activity: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """브리핑 `known_errors[].recurrence` 모양(§5.7.6) — `{ke_id: {count_30d, last}}`."""
    return {
        ke_id: {"count_30d": int(item.get("count_30d") or 0), "last": item.get("last_match")}
        for ke_id, item in activity.items()
    }


def runbook_coverage(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """브리핑에서 본 플레이북 id 분포.

    유형별 조사 수·최근·런북 유무·검토 기한 경과(최신 브리핑)·담당.
    """
    per: dict[str, dict[str, Any]] = {}
    without = 0
    for rec in records:
        pb = playbook_of(_mapping(rec.get("briefing")))
        if pb is None:
            without += 1
            continue
        playbook_id = _text(pb.get("id"))
        runbook = pb.get("runbook")
        item = per.setdefault(playbook_id, {"playbook_id": playbook_id, "investigations": 0})
        item["investigations"] += 1
        item["last_seen"] = rec.get("ts")
        item["has_runbook"] = isinstance(runbook, Mapping) and bool(runbook)
        item["runbook_stale"] = bool(_mapping(runbook).get("stale"))
        item["owner"] = _text(_mapping(runbook).get("owner"))
    return {"by_playbook": [per[k] for k in sorted(per)], "without_playbook": without}


def _ke_overdue(item: Mapping[str, Any], today: date) -> str | None:
    if item.get("stale") is True:
        return "브리핑의 검토 기한 경과 표시"
    reviewed = _parse_date(item.get("last_reviewed"))
    days = item.get("review_every_days")
    if reviewed is not None and isinstance(days, int) and not isinstance(days, bool) and days > 0:
        due = reviewed + timedelta(days=days)
        if due < today:
            return (f"검토 기한 {due.isoformat()} 경과"
                    f"(최근 검토 {reviewed.isoformat()} · 주기 {days}일)")
    return None


def review_targets(
    *,
    coverage: Mapping[str, Any],
    activity: Mapping[str, Mapping[str, Any]],
    feedback_review: Sequence[Mapping[str, Any]],
    now: datetime,
    window_oldest: datetime | None,
    unmatched_days: int = UNMATCHED_DAYS,
) -> dict[str, Any]:
    """관리자 「검토 대상」 — 런북 기한 경과 · KE 기한 경과 · 「아님」 누적 · 일치 0건 N일.

    일치 0건 판정은 집계 창(조사 저장소 스캔 범위)이 N일보다 길 때만 한다 — 짧으면 판정하지 않고
    `unmatched_check.judgeable=False`와 사유를 낸다(침묵 누락 금지).
    """
    items: list[dict[str, Any]] = []
    for rb in coverage.get("by_playbook") or []:
        if rb.get("runbook_stale"):
            items.append({"kind": "runbook_stale", "id": rb["playbook_id"],
                          "owner": rb.get("owner") or "",
                          "reason": "런북 검토 기한 경과(최신 브리핑 표시)"})
    today = now.date()
    for ke_id in sorted(activity):
        reason = _ke_overdue(activity[ke_id], today)
        if reason:
            items.append({"kind": "ke_review_overdue", "id": ke_id, "reason": reason})
    for fb in feedback_review:
        if fb.get("needs_review"):
            items.append({"kind": "ke_incorrect_streak", "id": fb["ke_id"],
                          "reason": f"「아님」 연속 {fb['incorrect_streak']}회"})
    cutoff = now - timedelta(days=unmatched_days)
    judgeable = window_oldest is not None and window_oldest <= cutoff
    check: dict[str, Any] = {"judgeable": judgeable, "days": unmatched_days}
    if not judgeable:
        check["reason"] = (
            f"조사 저장소 집계 창이 {unmatched_days}일보다 짧아 판정하지 않음"
            f"(가장 오래된 기록 {window_oldest.isoformat() if window_oldest else '없음'})"
        )
    else:
        for ke_id in sorted(activity):
            item = activity[ke_id]
            first = parse_ts(item.get("first_seen"))
            last = parse_ts(item.get("last_match"))
            if first is not None and first <= cutoff and (last is None or last <= cutoff):
                items.append({"kind": "ke_unmatched", "id": ke_id,
                              "reason": f"일치 0건 {unmatched_days}일"
                                        + (f"(최근 일치 {item['last_match']})" if last else "")})
    return {"items": items, "unmatched_check": check}


# ── 매뉴얼 RAG 참고 인용 (G-15) ───────────────────────────────────────────────


def runbook_references(briefing: Mapping[str, Any] | None) -> list[str]:
    """런북 `references`(사람이 고른 문서 식별자 · 순서 유지)."""
    refs = _mapping(_mapping(playbook_of(briefing)).get("runbook")).get("references") or []
    return [r.strip() for r in refs if isinstance(r, str) and r.strip()]


def reference_query(briefing: Mapping[str, Any] | None) -> str:
    """결정적 검색 질의 — 플레이북 id + 런북 references + 대상 솔루션명(있으면) · LLM 없음."""
    pb = playbook_of(briefing)
    if pb is None:
        return ""
    parts = [_text(pb.get("id")), *runbook_references(briefing),
             _text(_mapping(pb.get("scope")).get("solution"))]
    return " ".join(p for p in parts if p)


def _excerpt(text: Any) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= EXCERPT_MAX_CHARS else flat[:EXCERPT_MAX_CHARS] + "…"


def merge_references(
    pinned: Sequence[str],
    hits: Sequence[Mapping[str, Any]],
    *,
    limit: int = REFERENCE_LIMIT,
) -> list[dict[str, Any]]:
    """참고 문서 최대 `limit`건 — 런북 `references`가 RAG 순위와 무관하게 먼저(§5.7.4).

    `hits`는 검색 순위 순 `{title, section, excerpt, href, doc_id}`. 고정 문서가 검색 결과에 있으면
    그 결과(발췌 포함)를 쓰고, 없으면 식별자만 싣는다(`pinned=True`).
    """
    out: list[dict[str, Any]] = []
    used: set[int] = set()
    for ref in pinned:
        idx = next(
            (i for i, h in enumerate(hits)
             if i not in used and ref in (h.get("href"), h.get("doc_id"))),
            None,
        )
        if idx is not None:
            used.add(idx)
            hit = hits[idx]
            out.append({"title": str(hit.get("title") or ref),
                        "section": str(hit.get("section") or ""),
                        "excerpt": _excerpt(hit.get("excerpt")),
                        "href": str(hit.get("href") or ref),
                        "pinned": True})
        else:
            out.append({"title": ref, "section": "", "excerpt": "", "href": ref, "pinned": True})
    for i, hit in enumerate(hits):
        if i in used:
            continue
        out.append({"title": str(hit.get("title") or ""), "section": str(hit.get("section") or ""),
                    "excerpt": _excerpt(hit.get("excerpt")), "href": str(hit.get("href") or ""),
                    "pinned": False})
    return out[: max(0, limit)]


def with_kb_extras(
    briefing: Mapping[str, Any],
    *,
    references: Sequence[Mapping[str, Any]],
    references_status: Mapping[str, Any],
    recurrence: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """응답용 브리핑 **사본**에 참고 문서(`playbook.references`)·KE 재발 신호를 붙인다(§5.7.6).

    원 브리핑(저장 레코드)은 바꾸지 않는다. 플레이북 블록이 없으면 사본만 돌려준다.
    """
    out = dict(briefing)
    pb = playbook_of(briefing)
    if pb is None:
        return out
    playbook = dict(pb)
    playbook["references"] = [dict(r) for r in references]
    playbook["references_status"] = {**references_status, "notice": REFERENCES_NOTICE}
    kes = []
    for entry in pb.get("known_errors") or []:
        ke = dict(_mapping(entry))
        ke_id = _text(ke.get("id"))
        if ke_id in recurrence:
            ke["recurrence"] = dict(recurrence[ke_id])
        kes.append(ke)
    if "known_errors" in pb:
        playbook["known_errors"] = kes
    out["playbook"] = playbook
    return out


__all__ = [
    "CAUSE_EMPTY",
    "CAUSE_FROM_HOLMESGPT",
    "CAUSE_FROM_LABEL",
    "CAUSE_FROM_PLAYBOOK",
    "DRAFT_STATE",
    "ENGINE_CHOICES",
    "KE_FEEDBACK_LABELS",
    "POSTMORTEM_CRITERIA",
    "REFERENCES_NOTICE",
    "alarm_issue",
    "build_draft",
    "cause_default",
    "draft_to_yaml",
    "engine_stats",
    "evaluate_postmortem",
    "ke_activity",
    "ke_feedback_review",
    "known_error_ids",
    "latest_choices",
    "match_candidates",
    "merge_references",
    "playbook_of",
    "recurrence_of",
    "reference_query",
    "render_postmortem",
    "review_targets",
    "runbook_coverage",
    "runbook_references",
    "timeline_rows",
    "top_hypothesis",
    "with_kb_extras",
]
