"""조사 저장소 (plans/153 W2 · D-325 G-1 (a)).

자동 조사 1건의 상태 전이(submitted → done/failed/timeout …)마다 JSONL 1행을 append 한다.
sre_agent는 잡을 종결 후 1시간(TTL)·무재기동일 때만 다시 내주므로, 웹(알람 카드·관제 드로어)이
브리핑을 다시 보려면 본체 쪽 저장이 필요하다. 판정 감사(`alarm_decisions.jsonl`)와 파일을
분리해 관제 tail 스캔에 큰 레코드가 끼지 않게 한다.

레코드 모양(§4.1 — 값 있는 선택 키만 싣는다):
    {investigation_id, alarm_ids[], alarm_db_ids?{alarm_id: db_id}, episode_id?,
     kind: "alarm"|"episode"|"diagnosis"|"manual", status, reason?, verdict?, briefing?,
     briefing_lines?[{label,text}], cross_source?, ts, source: "inline"|"followup"|"api_poll"|…,
     owner_sub?, parent_investigation_id?, focus?, origin?, subject?, parent_alarm_id?}

- (plans/155 G-7 · plans/187 W10) `kind="diagnosis"`는 알람 없는 진단(추가 조사)이다 —
  `alarm_ids`가 비고 연결 필드 `LINK_FIELDS`(소유자·부모 조사·초점·출처·대상)를 싣는다.
  값이 없으면 키를 쓰지 않으므로 알람 레코드는 종전과 비트 동일하다.
- (plans/187 W11 · G-28) `kind="manual"`은 사용자가 요청한 알람 다시 분석이다 —
  `alarm_ids=[그 알람]`·`parent_alarm_id`·`owner_sub`·`origin`
  (+ 있으면 `parent_investigation_id`)을 싣는 **새 레코드**라 원 조사 행을 덮어쓰지 않는다.
  `get_by_alarm`은 `include_manual=True`일 때만 이를 후보로 본다.

- 읽을 때 **최신 행이 이긴다**. 조회는 `alarm_ids` **멤버십**으로 한다 — 사건 조사(W4)는 한 조사에
  여러 알람이 걸린다.
- `alarm_db_ids`는 기록 시점에 아는 멤버 알람의 db_id다(조회 API의 존 검사가 판정 저장소 스캔에 덜
  의존하게 — 없으면 API가 판정 저장소로 폴백한다). 행 전체를 합친다.
- `episode_existing` 행은 "이 알람은 같은 사건의 기존 조사를 재사용했다"는 **연결 행**이다 —
  조사 상태를 덮어쓰지 않고 `alarm_ids`만 보탠다.
- 기록 실패는 warning 후 무시한다(발송 차단 금지 — `DecisionStore` 방식).

표준 라이브러리와 domain 렌더러(`render_briefing_lines`)만 의존한다.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from noise_gate.domain.investigation_briefing import render_briefing_lines
from noise_gate.infrastructure import jsonl_tail

logger = logging.getLogger(__name__)

#: 브리핑 원본 저장 상한(UTF-8 JSON 바이트). 넘으면 `limitations`에 잘림 표지를 넣고 줄인다.
MAX_BRIEFING_BYTES = 32 * 1024
TRUNCATED_NOTE = "브리핑이 32KB를 넘어 잘림 — 일부 항목이 빠지거나 줄었습니다"

STATUS_SUBMITTED = "submitted"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_TIMEOUT = "timeout"
STATUS_REJECTED = "rejected"
STATUS_EPISODE_EXISTING = "episode_existing"

#: 알람 없는 진단 레코드의 kind(plans/155 G-7 · plans/187 W10 추가 조사).
KIND_DIAGNOSIS = "diagnosis"
#: 사용자 요청 알람 분석 레코드의 kind(plans/187 W11 · G-28 「이 알람 분석」).
KIND_MANUAL = "manual"
#: 사용자 요청 조사 kind — 사용자별 상한(진행 1 · 시간당)을 함께 센다(D-335 ⑥ · G-31).
USER_REQUEST_KINDS: frozenset[str] = frozenset({KIND_DIAGNOSIS, KIND_MANUAL})

#: 사용자 요청 조사 레코드의 선택 연결 필드 — 값이 있을 때만 싣고, 종결 행(지연 poll)에도 옮긴다.
LINK_FIELDS: tuple[str, ...] = (
    "owner_sub", "parent_investigation_id", "focus", "origin", "subject", "parent_alarm_id",
)

#: 아직 종결되지 않은 상태 — API가 sre_agent에 지연 poll을 1회 해 볼 대상이다.
ACTIVE_STATUSES: frozenset[str] = frozenset({STATUS_SUBMITTED, STATUS_RUNNING})

# 호출부(트리거·후속·API)가 쓰는 원 상태 → 저장 상태 + 기본 사유.
# 호출부가 사유를 주면 그것이 이긴다.
# 여기 없는 값은 그대로 저장한다(침묵 누락 방지 — 새 상태가 생겨도 버리지 않는다).
_STATUS_MAP: dict[str, tuple[str, str | None]] = {
    "accepted": (STATUS_RUNNING, None),
    "duplicate": (STATUS_RUNNING, None),
    "stub": (STATUS_DONE, None),
    "not_found": (
        STATUS_FAILED,
        "조사 서비스에 잡이 없습니다 — 종결 후 보존 시간이 지났거나 서비스가 재기동했습니다",
    ),
    "down": (STATUS_FAILED, "조사 서비스 연결 실패"),
    "error": (STATUS_FAILED, "조사 서비스 응답에 조사 id가 없습니다"),
    "authz_denied": (STATUS_REJECTED, "호스트 인가 거부로 조사를 제출하지 않았습니다"),
    "target_unresolved": (
        STATUS_REJECTED, "조사 대상(hostname)을 확정하지 못해 제출하지 않았습니다"
    ),
    "followup_timeout": (STATUS_TIMEOUT, "후속 조회 타임아웃"),
    "followup_failed": (STATUS_FAILED, "후속 조회 실패"),
    "followup_no_client": (
        STATUS_FAILED, "조사 서비스 URL 미설정 — 결과를 조회할 수 없습니다"
    ),
}


def normalize_status(raw: str) -> tuple[str, str | None]:
    """원 상태를 저장 상태와 기본 사유로 바꾼다(모르는 값은 그대로 · 사유 없음)."""
    return _STATUS_MAP.get(raw, (raw, None))


#: poll 응답의 사유(`reason`·`error`)를 레코드 사유로 보존하는 종결 상태(submit/poll 계약 · D-123).
_REASON_POLL_STATUSES: frozenset[str] = frozenset({"failed", "rejected", "timeout", "stub"})


def terminal_poll_reason(res: Mapping[str, Any]) -> str | None:
    """poll 응답에서 저장할 사유 — 실패·거부·타임아웃·스텁이면 `reason`, 없으면 `error`.

    그 외 상태는 None이다.
    """
    if str(res.get("status") or "") not in _REASON_POLL_STATUSES:
        return None
    for key in ("reason", "error"):
        value = res.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def _clip_str(kept: dict[str, Any], key: str, value: str, max_bytes: int) -> str | None:
    """`kept`에 `key`로 넣어 상한 안에 드는 가장 긴 접두(+「…」)를 찾는다(이분 탐색)."""
    lo, hi = 0, len(value)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _json_size({**kept, key: value[:mid] + "…"}) <= max_bytes:
            lo = mid
        else:
            hi = mid - 1
    return value[:lo] + "…" if lo > 0 else None


def _clip_list(
    kept: dict[str, Any], key: str, value: list[Any], max_bytes: int
) -> list[Any] | None:
    """`kept`에 `key`로 넣어 상한 안에 드는 앞쪽 항목만 남긴다."""
    out: list[Any] = []
    for item in value:
        if _json_size({**kept, key: [*out, item]}) > max_bytes:
            break
        out.append(item)
    return out or None


#: (plans/187 W5a) 플레이북 축소 1단계에서 섹션 표에 남기는 앞 행 수.
PLAYBOOK_CLIP_TABLE_ROWS = 5
#: 플레이북을 줄여 담았을 때 `playbook.notes`에 남기는 표지(단계 번호를 붙인다).
PLAYBOOK_CLIPPED_NOTE = "저장 상한(32KB)으로 플레이북 일부 생략 — 축소 {level}단계"
#: 축소 마지막 단계에서도 남기는 섹션 머리 키.
_SECTION_HEAD_KEYS: tuple[str, ...] = (
    "step", "action", "title", "status", "verdict", "verdict_source",
)
#: `deep_dive`를 줄일 때 남기는 키(앞쪽이 상태 · 뒤쪽이 우선 보존 서술).
_DEEP_DIVE_KEEP_KEYS: tuple[tuple[str, ...], ...] = (
    ("status", "mode", "reason", "label", "summary", "cause"),
    ("status", "mode", "reason", "label"),
)


def _with_clip_note(pb: dict[str, Any], level: int) -> dict[str, Any]:
    notes = [*(pb.get("notes") or []), PLAYBOOK_CLIPPED_NOTE.format(level=level)]
    return {**pb, "notes": notes}


def _clip_section_rows(section: dict[str, Any]) -> dict[str, Any]:
    """섹션 표를 앞 `PLAYBOOK_CLIP_TABLE_ROWS`행으로 줄이고 잘렸으면 배지에 표시한다."""
    table = section.get("table")
    if not isinstance(table, dict):
        return section
    rows = table.get("rows")
    if not isinstance(rows, list) or len(rows) <= PLAYBOOK_CLIP_TABLE_ROWS:
        return section
    badge = f"표 일부 생략({len(rows)}행 중 {PLAYBOOK_CLIP_TABLE_ROWS}행)"
    return {
        **section,
        "table": {**table, "rows": rows[:PLAYBOOK_CLIP_TABLE_ROWS]},
        "badges": [*(section.get("badges") or []), badge],
    }


def _playbook_candidates(pb: dict[str, Any]) -> list[dict[str, Any]]:
    """플레이북을 저장 상한에 맞추는 결정적 축소 후보(덜 줄인 것부터). `synthesis`는 끝까지 둔다.

    ① 섹션 표 앞 N행 · `readings_detail`·`timings` 제거 ② 섹션의 표·소견 제거(머리·배지·사유·도구만)
    ③ `id`·`version`·`matched_by`·`scope`·`readings`·`synthesis`·`notes` + 섹션 머리만
    ④ ③에서 `scope`·`readings` 제거.
    """
    sections = [s for s in pb.get("sections") or [] if isinstance(s, dict)]
    lvl1 = {k: v for k, v in pb.items() if k not in ("readings_detail", "timings")}
    lvl1["sections"] = [_clip_section_rows(s) for s in sections]
    lvl2 = {**lvl1, "sections": [
        {k: v for k, v in s.items()
         if k not in ("table", "opinion", "opinion_guard", "opinion_dropped_reason")}
        for s in sections
    ]}
    heads = [{k: s[k] for k in _SECTION_HEAD_KEYS if k in s} for s in sections]
    lvl3_keys = ("id", "version", "matched_by", "scope", "readings", "synthesis", "notes")
    lvl3: dict[str, Any] = {k: pb[k] for k in lvl3_keys if k in pb}
    lvl3["sections"] = heads
    lvl4: dict[str, Any] = {k: v for k, v in lvl3.items() if k not in ("scope", "readings")}
    levels: list[dict[str, Any]] = [lvl1, lvl2, lvl3, lvl4]
    return [_with_clip_note(c, i) for i, c in enumerate(levels, start=1)]


def _dict_candidates(key: str, value: dict[str, Any]) -> list[dict[str, Any]]:
    """통째로 들어가지 않는 dict 값의 축소 후보 — 플레이북 브리핑 키만(그 밖은 종전대로 뺀다)."""
    if key == "playbook":
        return _playbook_candidates(value)
    if key == "deep_dive":
        return [{k: value[k] for k in keys if k in value} for keys in _DEEP_DIVE_KEEP_KEYS]
    return []


def clip_briefing(
    briefing: dict[str, Any], max_bytes: int = MAX_BRIEFING_BYTES
) -> dict[str, Any]:
    """브리핑 dict를 저장 상한 안으로 줄인다(넘지 않으면 원본 그대로).

    넘으면 `limitations`에 `TRUNCATED_NOTE`를 먼저 넣고(잘렸다는 사실은 반드시 남긴다), 원래 키
    순서대로 들어가는 만큼 담는다. 들어가지 않는 문자열은 접두로, 목록은 앞 항목만 남기고, 그 밖의
    값은 뺀다. 키 순서는 원본을 따른다.

    (plans/187 W5a) 플레이북 브리핑의 `playbook`·`deep_dive` dict는 통째로 빼지 않고 결정적으로
    줄여 담는다(`_playbook_candidates` — 가설·대응은 끝까지 보존 · 축소 단계는 `playbook.notes`에
    남는다). 가장 작은 후보도 들어가지 않으면 경고 로그를 남기고 뺀다(침묵 강등 금지).
    """
    if _json_size(briefing) <= max_bytes:
        return briefing
    raw_lims = briefing.get("limitations")
    if isinstance(raw_lims, list):
        lims = list(raw_lims)
    elif raw_lims in (None, "", False):
        lims = []
    else:
        lims = [raw_lims]
    lims.append(TRUNCATED_NOTE)
    kept: dict[str, Any] = {"limitations": lims}
    if _json_size(kept) > max_bytes:
        kept = {"limitations": [TRUNCATED_NOTE]}
    for key, value in briefing.items():
        if key == "limitations":
            continue
        if _json_size({**kept, key: value}) <= max_bytes:
            kept[key] = value
            continue
        clipped: Any = None
        if isinstance(value, str):
            clipped = _clip_str(kept, key, value, max_bytes)
        elif isinstance(value, list):
            clipped = _clip_list(kept, key, value, max_bytes)
        elif isinstance(value, dict) and key in ("playbook", "deep_dive"):
            clipped = next(
                (c for c in _dict_candidates(key, value)
                 if _json_size({**kept, key: c}) <= max_bytes),
                None,
            )
            if clipped is None:
                logger.warning("브리핑 저장 상한 — 축소해도 %s 키가 들어가지 않아 뺍니다", key)
        if clipped is not None:
            kept[key] = clipped
    order = list(briefing) + ([] if "limitations" in briefing else ["limitations"])
    return {k: kept[k] for k in order if k in kept}


def _verdict_level(rec: dict[str, Any]) -> str | None:
    """조사 판정 등급 — verdict(dict)의 level, 없으면 브리핑 severity.level."""
    verdict = rec.get("verdict")
    if isinstance(verdict, dict) and verdict.get("level"):
        return str(verdict["level"])
    briefing = rec.get("briefing")
    severity = briefing.get("severity") if isinstance(briefing, dict) else None
    if isinstance(severity, dict) and severity.get("level"):
        return str(severity["level"])
    return None


def _mask_nested(value: Any, mask_fn: Callable[[str], str]) -> Any:
    """JSON 값의 문자열을 재귀적으로 마스킹한다(숫자·불리언은 그대로)."""
    if isinstance(value, str):
        return mask_fn(value)
    if isinstance(value, list):
        return [_mask_nested(v, mask_fn) for v in value]
    if isinstance(value, dict):
        return {k: _mask_nested(v, mask_fn) for k, v in value.items()}
    return value


def _mask_lines(text: str, mask_fn: Callable[[str], str]) -> str:
    """여러 줄 평문을 줄 단위로 마스킹한다 — 마스킹 패턴이 값 전체 일치라 줄마다 대조한다."""
    return "\n".join(mask_fn(line) for line in text.split("\n"))


#: (plans/196 §5.3) 섹션의 개발자 사유 키 — 운영자 뷰에만 남는다.
SECTION_DEV_REASON_KEY = "reason_detail"
#: (plans/196 §5.3) `scope`의 개발자 사유 키 — 섹션 사유와 같이 운영자 뷰에만 남는다.
SCOPE_DEV_REASON_KEYS: frozenset[str] = frozenset({"polestar_reason_detail"})
#: (plans/187 W5a) 플레이북 브리핑 사용자 뷰에 싣는 키 — 6요소 원문은 싣지 않는다(D-325 ③).
PLAYBOOK_VIEW_KEYS: tuple[str, ...] = ("partial", "playbook", "deep_dive", "engine_agreement")
#: 마스킹하지 않는 구조 값의 키 — 식별자·enum(판독 맵 `readings` 포함). 그 외 문자열은 전부 가린다.
_PLAYBOOK_ENUM_KEYS: frozenset[str] = frozenset({
    "id", "version", "matched_by", "step", "action", "status", "verdict", "verdict_source",
    "rule_id", "mode", "result", "readings", "source", "key", "layer",
})


def _mask_playbook_value(value: Any, mask_fn: Callable[[str], str]) -> Any:
    """플레이북 값의 문자열을 줄 단위로 가린다 — enum 키의 값·숫자·불리언·dict 키는 그대로."""
    if isinstance(value, str):
        return _mask_lines(value, mask_fn)
    if isinstance(value, list):
        return [_mask_playbook_value(v, mask_fn) for v in value]
    if isinstance(value, dict):
        return {
            k: (v if k in _PLAYBOOK_ENUM_KEYS and not isinstance(v, list)
                else _mask_playbook_value(v, mask_fn))
            for k, v in value.items()
        }
    return value


def playbook_user_view(
    briefing: Mapping[str, Any] | None, mask_fn: Callable[[str], str]
) -> dict[str, Any] | None:
    """플레이북 브리핑(종결 `briefing` 또는 진행 중 `partial_briefing`)의 사용자 뷰(plans/187 W5a).

    `playbook` 키가 있을 때만 `PLAYBOOK_VIEW_KEYS`의 사본을 돌려준다. 섹션 표 셀·소견·종합 서술·
    가설 제목·대응 문장·`scope`·`notes`·런북·알려진 오류·심층 결과의 문자열을 `mask_fn`으로 가리고,
    구조 키·enum·수치는 그대로 둔다. 플레이북이 없으면 None(종전 사용자 뷰와 비트 동일).
    (plans/196 §5.3 · D-325 ③) 섹션의 개발자 사유 `reason_detail`과 `scope`의
    `SCOPE_DEV_REASON_KEYS`는 사용자 뷰에서 뺀다 — 운영자 뷰는 원 브리핑을 그대로 싣는다.
    """
    if not isinstance(briefing, Mapping) or not isinstance(briefing.get("playbook"), dict):
        return None
    view = {
        key: _mask_playbook_value(briefing[key], mask_fn)
        for key in PLAYBOOK_VIEW_KEYS
        if key in briefing
    }
    sections = view["playbook"].get("sections")
    if isinstance(sections, list):
        view["playbook"]["sections"] = [
            {k: v for k, v in sec.items() if k != SECTION_DEV_REASON_KEY}
            if isinstance(sec, dict) else sec
            for sec in sections
        ]
    scope = view["playbook"].get("scope")
    if isinstance(scope, dict):
        view["playbook"]["scope"] = {
            k: v for k, v in scope.items() if k not in SCOPE_DEV_REASON_KEYS
        }
    return view


def investigation_view(
    rec: dict[str, Any],
    *,
    mask_fn: Callable[[str], str] | None = None,
    operator_view: bool = False,
) -> dict[str, Any]:
    """저장 레코드를 화면용 요약으로 만든다(§4.2 — 알람 카드·관제 드로어 공용).

    - 사용자 뷰(기본 · fail-closed): `briefing_lines` 텍스트·`reason`·`cross_source`를 `mask_fn`으로
      가리고, 원 `briefing` dict는 싣지 않는다(G-3 (a) · D-247). (plans/187 W5a) 플레이북
      브리핑이면 `briefing`에 `playbook_user_view`(마스킹한 `playbook`·`deep_dive`·
      `engine_agreement`)만 싣는다.
    - 운영자 뷰: 원문 + 원 `briefing` dict.
    - 요청자 `owner_sub`는 운영자 뷰에만 싣는다(사용자 뷰 · fail-closed).
    """
    lines = [
        {"label": str(item.get("label", "")), "text": str(item.get("text", ""))}
        for item in rec.get("briefing_lines") or []
        if isinstance(item, dict)
    ]
    reason = rec.get("reason")
    cross_source = rec.get("cross_source")
    if not operator_view and mask_fn is not None:
        lines = [{"label": i["label"], "text": _mask_lines(i["text"], mask_fn)} for i in lines]
        if isinstance(reason, str):
            reason = mask_fn(reason)
        if cross_source is not None:
            cross_source = _mask_nested(cross_source, mask_fn)
    view: dict[str, Any] = {
        "status": rec.get("status") or "",
        "investigation_id": rec.get("investigation_id"),
        "kind": rec.get("kind") or "alarm",
        "episode_id": rec.get("episode_id"),
        "briefing_lines": lines,
        "cross_source": cross_source,
        "verdict_level": _verdict_level(rec),
        "reason": reason,
        "updated_at": rec.get("ts"),
        "source": rec.get("source"),
    }
    # 추가 조사 레코드의 연결 필드(알람 레코드에는 없어 응답이 종전과 같다). 대상(`subject`)은
    # 사용자 뷰에서 마스킹한다. 요청자 식별자(`owner_sub`)는 운영자 뷰에만 싣는다(plans/187 교정 ·
    # D-325 ③ — by-alarm이 다른 사용자의 요청 조사를 고를 수 있다).
    for key in LINK_FIELDS:
        value = rec.get(key)
        if not value:
            continue
        if key == "owner_sub" and not operator_view:
            continue
        if key == "subject" and not operator_view and mask_fn is not None:
            value = _mask_nested(value, mask_fn)
        view[key] = value
    if operator_view:
        view["briefing"] = rec.get("briefing")
    elif mask_fn is not None:
        # (plans/187 W5a · D-325 ③) 플레이북 브리핑이면 마스킹한 표시용 부분만 싣는다.
        user_briefing = playbook_user_view(rec.get("briefing"), mask_fn)
        if user_briefing is not None:
            view["briefing"] = user_briefing
    return view


class InvestigationStore:
    """조사 상태·브리핑을 JSONL로 적재하고 조사 id·알람 id로 최신 상태를 돌려주는 저장소."""

    def __init__(self, path: str, max_lines: int = 2000) -> None:
        """저장 경로와 조회 스캔 상한(파일 끝에서 되짚는 줄 수)을 받는다."""
        self.path = Path(path)
        self.max_lines = max(1, int(max_lines))

    def record(
        self,
        *,
        investigation_id: str | None,
        alarm_ids: Sequence[str],
        status: str,
        source: str,
        kind: str = "alarm",
        episode_id: str | None = None,
        reason: Any = None,
        verdict: Any = None,
        briefing: dict[str, Any] | None = None,
        ts: datetime | None = None,
        alarm_db_ids: Mapping[str, str | None] | None = None,
        owner_sub: str | None = None,
        parent_investigation_id: str | None = None,
        focus: str | None = None,
        origin: str | None = None,
        subject: Mapping[str, Any] | None = None,
        parent_alarm_id: str | None = None,
    ) -> None:
        """조사 상태 행을 1줄 append 한다(실패는 warning 후 무시 — 발송 차단 금지).

        `status`는 호출부의 원 상태(`normalize_status`로 저장 상태로 바꾼다)이고, `reason`을 주지
        않으면 상태별 기본 사유를 쓴다. 브리핑은 32KB로 줄이고 `briefing_lines`를 함께 남긴다.
        `alarm_db_ids`는 값 있는 항목만 싣는다(존 검사 근거 — 모르면 넘기지 않는다).
        `LINK_FIELDS`(추가 조사 — plans/187 W10)도 값이 있을 때만 싣는다(알람 레코드는 비트 동일).
        """
        stored_status, default_reason = normalize_status(status)
        rec: dict[str, Any] = {
            "investigation_id": investigation_id,
            "alarm_ids": [str(a) for a in alarm_ids if a],
            "kind": kind,
            "status": stored_status,
        }
        db_ids = {str(a): str(d) for a, d in (alarm_db_ids or {}).items() if a and d}
        if db_ids:
            rec["alarm_db_ids"] = db_ids
        if episode_id:
            rec["episode_id"] = episode_id
        links: dict[str, Any] = {
            "owner_sub": owner_sub,
            "parent_investigation_id": parent_investigation_id,
            "focus": focus,
            "origin": origin,
            "subject": dict(subject) if subject else None,
            "parent_alarm_id": parent_alarm_id,
        }
        rec.update({k: v for k, v in links.items() if v})
        reason_text = str(reason) if reason not in (None, "") else default_reason
        if reason_text:
            rec["reason"] = reason_text
        if isinstance(verdict, (dict, str)) and verdict:
            rec["verdict"] = verdict
        try:
            if isinstance(briefing, dict) and briefing:
                clipped = clip_briefing(briefing)
                rec["briefing"] = clipped
                rec["briefing_lines"] = [
                    {"label": label, "text": text}
                    for label, text in render_briefing_lines(clipped)
                ]
                if isinstance(clipped.get("cross_source"), dict):
                    rec["cross_source"] = clipped["cross_source"]
            rec["ts"] = (ts or datetime.now(UTC)).isoformat()
            rec["source"] = source
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(rec, ensure_ascii=False, default=str)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception as exc:  # noqa: BLE001 — 기록 실패가 통보를 막지 않는다
            logger.warning(
                "조사 저장소 기록 실패(무시): inv=%s status=%s %s",
                investigation_id, stored_status, exc,
            )

    def _tail(self) -> list[dict[str, Any]]:
        """파일 끝에서 max_lines 줄을 읽어 레코드 목록(오래된 것 → 최신)을 돌려준다.

        (plans/194 W2) 경로 단위 증분 캐시(`jsonl_tail`)로 읽는다 — 새로 붙은 바이트만 파싱한다.
        레코드 dict는 캐시와 공유하므로 이 클래스의 조회 메서드는 레코드를 고치지 않는다
        (사본만 만든다).
        """
        try:
            return jsonl_tail.tail_records(self.path, self.max_lines)
        except OSError as exc:
            logger.warning("조사 저장소 읽기 실패(무시): %s", exc)
            return []

    @staticmethod
    def _resolve(
        rows: list[dict[str, Any]], investigation_id: str
    ) -> dict[str, Any] | None:
        """한 조사의 최신 상태 — 최신 행이 이기고 `alarm_ids`·`alarm_db_ids`는 행 전체의 합집합
        (`alarm_ids`는 첫 등장 순).

        연결 행(`episode_existing`)은 상태를 덮지 않는다. 연결 행뿐이면 그 최신 행을 돌려준다.
        """
        latest: dict[str, Any] | None = None
        latest_link: dict[str, Any] | None = None
        alarm_ids: list[str] = []
        db_ids: dict[str, str] = {}
        for rec in rows:
            if rec.get("investigation_id") != investigation_id:
                continue
            for alarm_id in rec.get("alarm_ids") or []:
                if alarm_id not in alarm_ids:
                    alarm_ids.append(alarm_id)
            if isinstance(rec.get("alarm_db_ids"), dict):
                db_ids.update(rec["alarm_db_ids"])
            if rec.get("status") == STATUS_EPISODE_EXISTING:
                latest_link = rec
            else:
                latest = rec
        found = latest or latest_link
        if found is None:
            return None
        resolved = {**found, "alarm_ids": alarm_ids}
        if db_ids:
            resolved["alarm_db_ids"] = db_ids
        return resolved

    def get_by_investigation(self, investigation_id: str) -> dict[str, Any] | None:
        """조사 id의 최신 상태 레코드(없으면 None)."""
        if not investigation_id:
            return None
        return self._resolve(self._tail(), str(investigation_id))

    def list_diagnoses(
        self,
        *,
        owner_sub: str | None = None,
        parent_investigation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """`kind="diagnosis"` 조사의 최신 상태 목록(첫 접수 순 · 조사 id 있는 것만).

        `owner_sub`·`parent_investigation_id`를 주면 그 값과 같은 것만 남긴다(None = 거르지 않음).
        각 레코드에 첫 행 시각 `submitted_at`을 더한다(시간당 상한 계산용).
        스캔 범위는 `max_lines`다.
        """
        return self.list_requests(
            kinds=(KIND_DIAGNOSIS,),
            owner_sub=owner_sub,
            parent_investigation_id=parent_investigation_id,
        )

    def list_requests(
        self,
        *,
        kinds: Sequence[str] = tuple(USER_REQUEST_KINDS),
        owner_sub: str | None = None,
        parent_investigation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """`kinds`에 든 조사의 최신 상태 목록(첫 접수 순 · `submitted_at` 포함).

        기본은 사용자 요청 조사 전체(추가 조사 + 알람 다시 분석 — plans/187 W11 상한 공유).
        거르기 규칙은 `list_diagnoses`와 같다.
        """
        wanted = set(kinds)
        rows = self._tail()
        first_ts: dict[str, Any] = {}
        for rec in rows:
            investigation_id = rec.get("investigation_id")
            if investigation_id and rec.get("kind") in wanted:
                first_ts.setdefault(str(investigation_id), rec.get("ts"))
        out: list[dict[str, Any]] = []
        for investigation_id, ts in first_ts.items():
            resolved = self._resolve(rows, investigation_id)
            if resolved is None:
                continue
            if owner_sub is not None and resolved.get("owner_sub") != owner_sub:
                continue
            if (
                parent_investigation_id is not None
                and resolved.get("parent_investigation_id") != parent_investigation_id
            ):
                continue
            out.append({**resolved, "submitted_at": ts})
        return out

    def latest_records(self) -> list[dict[str, Any]]:
        """조사 id별 최신 상태 행(연결 행 제외 · 마지막 행 위치 순 = 시간 순) — 한 번만 훑는다.

        (plans/187 W9) KB 집계(런북 커버리지 · KE 재발 · 검토 대상) 재료다. 조사 id 없는 행은 뺀다.
        """
        latest: dict[str, dict[str, Any]] = {}
        for rec in self._tail():
            investigation_id = rec.get("investigation_id")
            if not investigation_id or rec.get("status") == STATUS_EPISODE_EXISTING:
                continue
            latest.pop(str(investigation_id), None)
            latest[str(investigation_id)] = rec
        return list(latest.values())

    def resolved_records(self) -> list[dict[str, Any]]:
        """조사 id별 최신 상태(`get_by_investigation`과 같은 병합 — 연결 행의 `alarm_ids`·
        `alarm_db_ids` 합집합) 전부 — 파일을 한 번만 훑는다(plans/194 W5 · 판정 목록 조사 상태).

        순서는 그 조사의 마지막 **상태 행** 위치 순(시간 순 · 연결 행뿐이면 마지막 연결 행 위치)이다
        — 연결 행이 붙었다고 오래된 조사가 뒤로 가지 않게 한다. 조사 id 없는 행은 뺀다.
        """
        rows: dict[str, list[dict[str, Any]]] = {}
        order: dict[str, tuple[int, int]] = {}  # id → (상태 행 있음, 위치)
        for idx, rec in enumerate(self._tail()):
            investigation_id = rec.get("investigation_id")
            if not investigation_id:
                continue
            key = str(investigation_id)
            rows.setdefault(key, []).append(rec)
            status_row = rec.get("status") != STATUS_EPISODE_EXISTING
            if status_row or order.get(key, (0, -1))[0] == 0:
                order[key] = (int(status_row), idx)
        out: list[tuple[int, dict[str, Any]]] = []
        for key, group in rows.items():
            resolved = self._resolve(group, group[0]["investigation_id"])
            if resolved is not None:
                out.append((order[key][1], resolved))
        return [rec for _, rec in sorted(out, key=lambda item: item[0])]

    def get_by_alarm(
        self, alarm_id: str, *, include_manual: bool = False
    ) -> dict[str, Any] | None:
        """알람이 `alarm_ids`에 든 조사 중 우선순위가 가장 높은 것의 상태 레코드(없으면 None).

        후보는 이 알람을 담은 조사들이다 — 조사 id별로 최신 상태를 풀고(연결 행이면 기존 조사의
        상태), 조사 id 없는 행(제출 전 거부·제출 실패 등)은 그 행 그대로 후보 1건이다.
        (plans/153 W6) 등급 순으로 고른다 — 실패한 종합 조사가 멤버의 단독 브리핑을 가리지 않게:

        1. `kind="episode"`이고 `done`인 종합 조사
        2. 그 알람 자신의 `kind="alarm"` 조사(`alarm_ids` 첫 원소가 이 알람 — 연결 행으로 붙은
           다른 알람의 조사는 제외)
        3. (plans/187 W11 · `include_manual=True`일 때만) 사용자 요청 알람 분석(`kind="manual"`)
        4. 나머지

        같은 등급 안에서는 최신 행(그 조사의 마지막 행 위치)이 이긴다.
        `include_manual=False`(기본)면 `kind="manual"` 레코드를 후보에서 빼 종전 결과와 같다.
        """
        candidates = self._alarm_candidates(str(alarm_id or ""), include_manual)
        best: tuple[int, int, dict[str, Any]] | None = None
        for idx, rec in candidates:
            grade = _grade(rec, str(alarm_id), manual_tier=include_manual)
            if best is None or (grade, idx) > (best[0], best[1]):
                best = (grade, idx, rec)
        return best[2] if best is not None else None

    def list_by_alarm(self, alarm_id: str) -> list[dict[str, Any]]:
        """알람에 걸린 조사 전부(사용자 요청 포함 · 조사 id 있는 것만) — 최신 행 순(최신 먼저).

        각 레코드에 첫 행 시각 `submitted_at`을 더한다(plans/187 W11 — 「이전 조사 N건」·합류 판정).
        """
        target = str(alarm_id or "")
        if not target:
            return []
        rows = self._tail()
        first_ts: dict[str, Any] = {}
        for rec in rows:
            investigation_id = rec.get("investigation_id")
            if investigation_id:
                first_ts.setdefault(str(investigation_id), rec.get("ts"))
        out = [
            (idx, {**rec, "submitted_at": first_ts.get(str(rec.get("investigation_id")))})
            for idx, rec in self._alarm_candidates(target, True, rows)
            if rec.get("investigation_id")
        ]
        out.sort(key=lambda item: item[0], reverse=True)
        return [rec for _, rec in out]

    def _alarm_candidates(
        self,
        target: str,
        include_manual: bool,
        rows: list[dict[str, Any]] | None = None,
    ) -> list[tuple[int, dict[str, Any]]]:
        """`get_by_alarm`·`list_by_alarm` 후보 — (그 조사의 마지막 행 위치, 최신 상태 레코드)."""
        if not target:
            return []
        if rows is None:
            rows = self._tail()
        member_ids = {
            str(rec["investigation_id"]) for rec in rows
            if rec.get("investigation_id") and target in (rec.get("alarm_ids") or [])
        }
        # 후보 → 마지막 행 위치. 조사 id 있는 후보는 그 조사의 모든 행 중 마지막 위치다.
        last_idx: dict[str, int] = {}
        loose: list[tuple[int, dict[str, Any]]] = []  # 조사 id 없는 행
        for idx, rec in enumerate(rows):
            investigation_id = rec.get("investigation_id")
            if investigation_id and str(investigation_id) in member_ids:
                last_idx[str(investigation_id)] = idx
            elif not investigation_id and target in (rec.get("alarm_ids") or []):
                loose.append((idx, rec))
        candidates: list[tuple[int, dict[str, Any]]] = list(loose)
        for investigation_id, idx in last_idx.items():
            resolved = self._resolve(rows, investigation_id)
            if resolved is not None:
                candidates.append((idx, resolved))
        if not include_manual:
            candidates = [c for c in candidates if c[1].get("kind") != KIND_MANUAL]
        return candidates


def _grade(rec: dict[str, Any], alarm_id: str, *, manual_tier: bool = False) -> int:
    """`get_by_alarm` 후보 등급(클수록 우선).

    3: 종결된 종합 조사 · 2: 자기 단독 조사 · 1: 사용자 요청 알람 분석(`manual_tier`일 때만) ·
    0: 나머지. `manual_tier=False`면 1이 없어 종전 순서와 같다.
    """
    kind = rec.get("kind") or "alarm"
    if kind == "episode" and rec.get("status") == STATUS_DONE:
        return 3
    if kind == "alarm" and (rec.get("alarm_ids") or [None])[0] == alarm_id:
        return 2
    if manual_tier and kind == KIND_MANUAL:
        return 1
    return 0


__all__ = [
    "ACTIVE_STATUSES",
    "MAX_BRIEFING_BYTES",
    "PLAYBOOK_CLIPPED_NOTE",
    "InvestigationStore",
    "KIND_DIAGNOSIS",
    "KIND_MANUAL",
    "LINK_FIELDS",
    "PLAYBOOK_VIEW_KEYS",
    "USER_REQUEST_KINDS",
    "TRUNCATED_NOTE",
    "clip_briefing",
    "investigation_view",
    "normalize_status",
    "playbook_user_view",
    "terminal_poll_reason",
]
