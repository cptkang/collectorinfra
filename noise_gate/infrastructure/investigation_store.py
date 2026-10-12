"""조사 저장소 (plans/150 W2 · D-323 G-1 (a)).

자동 조사 1건의 상태 전이(submitted → done/failed/timeout …)마다 JSONL 1행을 append 한다.
sre_agent는 잡을 종결 후 1시간(TTL)·무재기동일 때만 다시 내주므로, 웹(알람 카드·관제 드로어)이
브리핑을 다시 보려면 본체 쪽 저장이 필요하다. 판정 감사(`alarm_decisions.jsonl`)와 파일을
분리해 관제 tail 스캔에 큰 레코드가 끼지 않게 한다.

레코드 모양(§4.1 — 값 있는 선택 키만 싣는다):
    {investigation_id, alarm_ids[], alarm_db_ids?{alarm_id: db_id}, episode_id?,
     kind: "alarm"|"episode", status, reason?, verdict?, briefing?,
     briefing_lines?[{label,text}], cross_source?, ts, source: "inline"|"followup"|"api_poll"}

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
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from noise_gate.domain.investigation_briefing import render_briefing_lines

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


def clip_briefing(
    briefing: dict[str, Any], max_bytes: int = MAX_BRIEFING_BYTES
) -> dict[str, Any]:
    """브리핑 dict를 저장 상한 안으로 줄인다(넘지 않으면 원본 그대로).

    넘으면 `limitations`에 `TRUNCATED_NOTE`를 먼저 넣고(잘렸다는 사실은 반드시 남긴다), 원래 키
    순서대로 들어가는 만큼 담는다. 들어가지 않는 문자열은 접두로, 목록은 앞 항목만 남기고, 그 밖의
    값은 뺀다. 키 순서는 원본을 따른다.
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


def investigation_view(
    rec: dict[str, Any],
    *,
    mask_fn: Callable[[str], str] | None = None,
    operator_view: bool = False,
) -> dict[str, Any]:
    """저장 레코드를 화면용 요약으로 만든다(§4.2 — 알람 카드·관제 드로어 공용).

    - 사용자 뷰(기본 · fail-closed): `briefing_lines` 텍스트·`reason`·`cross_source`를 `mask_fn`으로
      가리고, 원 `briefing` dict는 싣지 않는다(G-3 (a) · D-247).
    - 운영자 뷰: 원문 + 원 `briefing` dict.
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
    if operator_view:
        view["briefing"] = rec.get("briefing")
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
    ) -> None:
        """조사 상태 행을 1줄 append 한다(실패는 warning 후 무시 — 발송 차단 금지).

        `status`는 호출부의 원 상태(`normalize_status`로 저장 상태로 바꾼다)이고, `reason`을 주지
        않으면 상태별 기본 사유를 쓴다. 브리핑은 32KB로 줄이고 `briefing_lines`를 함께 남긴다.
        `alarm_db_ids`는 값 있는 항목만 싣는다(존 검사 근거 — 모르면 넘기지 않는다).
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
        """파일 끝에서 max_lines 줄을 읽어 레코드 목록(오래된 것 → 최신)을 돌려준다."""
        if not self.path.exists():
            return []
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                lines = deque(fh, maxlen=self.max_lines)
        except OSError as exc:
            logger.warning("조사 저장소 읽기 실패(무시): %s", exc)
            return []
        out: list[dict[str, Any]] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
        return out

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

    def get_by_alarm(self, alarm_id: str) -> dict[str, Any] | None:
        """알람이 `alarm_ids`에 든 조사 중 우선순위가 가장 높은 것의 상태 레코드(없으면 None).

        후보는 이 알람을 담은 조사들이다 — 조사 id별로 최신 상태를 풀고(연결 행이면 기존 조사의
        상태), 조사 id 없는 행(제출 전 거부·제출 실패 등)은 그 행 그대로 후보 1건이다.
        (plans/150 W6) 등급 순으로 고른다 — 실패한 종합 조사가 멤버의 단독 브리핑을 가리지 않게:

        1. `kind="episode"`이고 `done`인 종합 조사
        2. 그 알람 자신의 `kind="alarm"` 조사(`alarm_ids` 첫 원소가 이 알람 — 연결 행으로 붙은
           다른 알람의 조사는 제외)
        3. 나머지

        같은 등급 안에서는 최신 행(그 조사의 마지막 행 위치)이 이긴다.
        """
        target = str(alarm_id or "")
        if not target:
            return None
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
        best: tuple[int, int, dict[str, Any]] | None = None
        for idx, rec in candidates:
            grade = _grade(rec, target)
            if best is None or (grade, idx) > (best[0], best[1]):
                best = (grade, idx, rec)
        return best[2] if best is not None else None


def _grade(rec: dict[str, Any], alarm_id: str) -> int:
    """`get_by_alarm` 후보 등급(클수록 우선).

    2: 종결된 종합 조사 · 1: 자기 단독 조사 · 0: 나머지.
    """
    kind = rec.get("kind") or "alarm"
    if kind == "episode" and rec.get("status") == STATUS_DONE:
        return 2
    if kind == "alarm" and (rec.get("alarm_ids") or [None])[0] == alarm_id:
        return 1
    return 0


__all__ = [
    "ACTIVE_STATUSES",
    "MAX_BRIEFING_BYTES",
    "InvestigationStore",
    "TRUNCATED_NOTE",
    "clip_briefing",
    "investigation_view",
    "normalize_status",
    "terminal_poll_reason",
]
