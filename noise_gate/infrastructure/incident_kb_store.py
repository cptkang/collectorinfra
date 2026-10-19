"""장애 대응 지식(KB) 기록 저장소 (plans/187 W9 · §5.7.5 · D-341 ⑩⑮).

KB 초안 · 알려진 오류 사용 피드백(맞음/아님/우회 효과 없음) · 운영자 엔진 선택을 JSONL 1줄씩
append 한다(`type` = `kb_draft` | `ke_feedback` | `engine_choice`). `FeedbackStore`(운영자 피드백)를
미러링한 경량 저장소다 — 조회는 파일 끝 `max_lines` 창만 읽고, 상한을 넘으면 오래된 절반으로
`<path>.1`을 **덮어쓴다**(회전 1세대 · plans/187 교정 M6 — `.1`이 무한히 자라지 않게).
- 저장은 append여도 집계 재료는 dedupe 한다 — KE 피드백은 (KE, 조사, 사용자)별 최신 1건만 돌려준다
  (같은 사용자의 반복 기록이 「아님 연속」 집계를 왜곡하지 않게).

- **조사 저장소와 파일을 나눈다**: 조사 저장소는 「조사 id의 최신 행이 상태를 이긴다」 규칙이라 같은
  id의 초안 행이 섞이면 조사 상태를 덮는다.
- **sre_agent 로더 경로에 쓰지 않는다**(§5.7.5 2 · 런타임 쓰기 0): 저장 경로가 저장소의 `sre_agent/`
  아래로 풀리면 생성 단계에서 거부한다(`ForbiddenKbPathError`). 초안은 관리자가 YAML 조각을 내려받아
  저장소 리뷰로만 편입한다.
- 기록 실패는 warning 후 `False`를 돌려준다 — 호출부(API)가 사유를 노출한다(침묵 수용 금지).

표준 라이브러리만 사용한다(순수 infrastructure).
"""

from __future__ import annotations

import json
import logging
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TYPE_DRAFT = "kb_draft"
TYPE_KE_FEEDBACK = "ke_feedback"
TYPE_ENGINE_CHOICE = "engine_choice"

_DEFAULT_MAX_LINES = 20000

#: 쓰기 금지 루트 — 저장소의 sre_agent 패키지(플레이북·알려진 오류 로더 경로가 그 아래에 있다).
FORBIDDEN_ROOT = Path(__file__).resolve().parents[2] / "sre_agent"


class ForbiddenKbPathError(ValueError):
    """KB 기록 경로가 sre_agent 아래로 풀릴 때 — 런타임 KB 파일 쓰기 차단."""


def assert_writable_path(path: str | Path) -> Path:
    """기록 경로 검증 — sre_agent 아래(심볼릭 링크 해석 후)면 `ForbiddenKbPathError`."""
    resolved = Path(path).resolve()
    if resolved == FORBIDDEN_ROOT or FORBIDDEN_ROOT in resolved.parents:
        raise ForbiddenKbPathError(f"KB 기록 경로가 sre_agent 아래입니다(쓰기 금지): {resolved}")
    return resolved


class IncidentKbStore:
    """KB 초안·KE 피드백·엔진 선택을 JSONL로 적재·조회한다."""

    def __init__(self, path: str, max_lines: int = _DEFAULT_MAX_LINES) -> None:
        """저장 경로·회전 상한(= 조회 창)을 받는다.

        Raises:
            ForbiddenKbPathError: 경로가 sre_agent 아래일 때.
        """
        self.path = assert_writable_path(path)
        self.max_lines = max(int(max_lines), 1)

    # ── 쓰기 ────────────────────────────────────────────────────────────────

    def record_draft(self, draft: dict[str, Any], *, ts: datetime | None = None) -> bool:
        """KB 초안 1건(`build_draft` 결과)을 적재한다."""
        return self._append({"type": TYPE_DRAFT, "ts": _iso(ts), **draft})

    def record_ke_feedback(
        self,
        *,
        ke_id: str,
        investigation_id: str,
        verdict: str,
        labeled_by: str,
        playbook_id: str = "",
        note: str = "",
        ts: datetime | None = None,
    ) -> bool:
        """알려진 오류 사용 피드백 1건 — `labeled_by`는 감사·회수용이다."""
        rec: dict[str, Any] = {
            "type": TYPE_KE_FEEDBACK, "ts": _iso(ts), "ke_id": ke_id,
            "investigation_id": investigation_id, "verdict": verdict, "labeled_by": labeled_by,
        }
        if playbook_id:
            rec["playbook_id"] = playbook_id
        if note:
            rec["note"] = note
        return self._append(rec)

    def record_engine_choice(
        self,
        *,
        investigation_id: str,
        choice: str,
        playbook_id: str,
        chosen_by: str,
        ts: datetime | None = None,
    ) -> bool:
        """운영자 엔진 선택 1건 — 같은 (조사, 사용자)는 최신 행이 이긴다(조회 시 해석)."""
        return self._append({
            "type": TYPE_ENGINE_CHOICE, "ts": _iso(ts), "investigation_id": investigation_id,
            "choice": choice, "playbook_id": playbook_id, "chosen_by": chosen_by,
        })

    # ── 읽기 ────────────────────────────────────────────────────────────────

    def list_drafts(self) -> list[dict[str, Any]]:
        """초안 목록(최신 먼저)."""
        return list(reversed(self._of_type(TYPE_DRAFT)))

    def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        """초안 id의 레코드(없으면 None)."""
        for rec in reversed(self._of_type(TYPE_DRAFT)):
            if rec.get("draft_id") == draft_id:
                return rec
        return None

    def ke_feedback(self) -> list[dict[str, Any]]:
        """KE 피드백 — (KE, 조사, 사용자)별 최신 1건만(그 최신 행 위치 = 시간 순).

        저장은 append지만 같은 사용자가 같은 조사의 같은 KE에 반복 기록해도 집계에는 1건이다
        (plans/187 교정 M6 — 플러드가 「아님 연속」 검토 대상 판정을 왜곡하지 않게).
        """
        latest: dict[tuple[str, str, str], dict[str, Any]] = {}
        for rec in self._of_type(TYPE_KE_FEEDBACK):
            key = (
                str(rec.get("ke_id") or ""),
                str(rec.get("investigation_id") or ""),
                str(rec.get("labeled_by") or ""),
            )
            latest.pop(key, None)
            latest[key] = rec
        return list(latest.values())

    def engine_choices(self) -> list[dict[str, Any]]:
        """엔진 선택 전부(시간 순 — (조사, 사용자)별 최신 해석은 domain `latest_choices`)."""
        return self._of_type(TYPE_ENGINE_CHOICE)

    def engine_choice_of(self, investigation_id: str, chosen_by: str | None) -> str | None:
        """요청자(`chosen_by`) 본인의 조사 최신 엔진 선택 — 없으면 None.

        다른 사용자의 선택으로 폴백하지 않는다(plans/187 교정 M6). `chosen_by`가 비면 None.
        """
        if not chosen_by:
            return None
        mine: str | None = None
        for rec in self._of_type(TYPE_ENGINE_CHOICE):
            if rec.get("investigation_id") != investigation_id:
                continue
            if rec.get("chosen_by") == chosen_by:
                mine = rec.get("choice")
        return mine

    # ── 내부 ────────────────────────────────────────────────────────────────

    def _of_type(self, kind: str) -> list[dict[str, Any]]:
        return [r for r in self._read_window() if r.get("type") == kind]

    def _append(self, record: dict[str, Any]) -> bool:
        """한 줄 append 후 필요 시 회전(실패는 warning 후 False)."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record, ensure_ascii=False, default=str)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as exc:
            logger.warning("KB 기록 실패: type=%s %s", record.get("type"), exc)
            return False
        self._rotate_if_needed()
        return True

    def _read_window(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                window = deque(fh, maxlen=self.max_lines)
        except OSError as exc:
            logger.warning("KB 기록 조회 실패(빈 결과): %s", exc)
            return []
        rows: list[dict[str, Any]] = []
        for line in window:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                rows.append(rec)
        return rows

    def _rotate_if_needed(self) -> None:
        """상한을 넘으면 오래된 절반으로 `<path>.1`을 덮어쓴다(회전 1세대 — 직전 회전분만)."""
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                lines = fh.readlines()
            if len(lines) <= self.max_lines:
                return
            keep = max(len(lines) // 2, 1)
            with Path(str(self.path) + ".1").open("w", encoding="utf-8") as fh:
                fh.writelines(lines[:-keep])
            with self.path.open("w", encoding="utf-8") as fh:
                fh.writelines(lines[-keep:])
        except OSError as exc:
            logger.warning("KB 기록 회전 실패(무시): %s", exc)


def _iso(ts: datetime | None) -> str:
    return (ts or datetime.now(UTC)).isoformat()


__all__ = [
    "FORBIDDEN_ROOT",
    "ForbiddenKbPathError",
    "IncidentKbStore",
    "TYPE_DRAFT",
    "TYPE_ENGINE_CHOICE",
    "TYPE_KE_FEEDBACK",
    "assert_writable_path",
]
