"""조사 실행 기록 요약 보관소 (plans/187 W14 · §5.6.3 ⓒ 「보관」 · G-37).

조사가 종결되면 본체 실시간 허브가 실행 기록 **요약**을 JSONL 1줄로 남긴다 — sre_agent 잡이 TTL로
사라진 뒤에도 「무엇을 조회했나」를 다시 볼 수 있게 한다. 요약은 한 줄(`label`)·상태·소요·행 수만
담는다: 발췌(`excerpt`)·AI 메모(`ai_note` 이벤트 · `note`)는 싣지 않는다(발췌는 잡 TTL까지만 —
G-36·G-38).

`IncidentKbStore`(FeedbackStore 방식)를 미러링한 경량 저장소다 — 조회는 파일 끝 `max_lines` 창만
읽고, 상한을 넘으면 오래된 절반으로 `<path>.1`을 **덮어쓴다**(회전 1세대). 기록 실패는 warning 후
`False`(호출부가 로그로 남긴다 — 화면 갱신을 막지 않는다).

표준 라이브러리만 사용한다(순수 infrastructure).
"""

from __future__ import annotations

import json
import logging
from collections import deque
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: 기본 파일 이름 — 조사 저장소(`investigation_store_path`)와 같은 폴더에 둔다.
DEFAULT_FILENAME = "investigation_activity.jsonl"
#: 회전 상한 = 조회 창. 한 줄은 이벤트 300건 요약이라 수십 KB까지 커질 수 있어 작게 잡는다.
_DEFAULT_MAX_LINES = 1000
#: 요약에 남기는 이벤트 키(발췌·AI 메모 제외).
SUMMARY_EVENT_KEYS: tuple[str, ...] = (
    "seq", "at", "engine", "kind", "step", "tool", "label", "status", "elapsed_ms", "rows",
    "purpose",
)
#: 요약에서 통째로 빼는 이벤트 종류(G-38 — AI 메모는 보관하지 않는다).
EXCLUDED_KINDS: frozenset[str] = frozenset({"ai_note"})


def summarize_events(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """실행 기록 이벤트를 보관용 요약으로 줄인다(순수 함수 — AI 메모 제외 · 키 화이트리스트)."""
    out: list[dict[str, Any]] = []
    for ev in events:
        if not isinstance(ev, Mapping) or ev.get("kind") in EXCLUDED_KINDS:
            continue
        out.append({k: ev[k] for k in SUMMARY_EVENT_KEYS if k in ev and ev[k] is not None})
    return out


class InvestigationActivityStore:
    """종결 조사의 실행 기록 요약을 JSONL로 적재·조회한다."""

    def __init__(self, path: str | Path, max_lines: int = _DEFAULT_MAX_LINES) -> None:
        """저장 경로·회전 상한(= 조회 창)을 받는다."""
        self.path = Path(path)
        self.max_lines = max(int(max_lines), 1)

    def record_summary(
        self,
        *,
        investigation_id: str,
        status: str,
        events: Iterable[Mapping[str, Any]],
        truncated: bool = False,
        ts: datetime | None = None,
    ) -> bool:
        """종결 조사 1건의 요약을 1줄 적재한다(이벤트는 `summarize_events`로 줄인다)."""
        record = {
            "ts": (ts or datetime.now(UTC)).isoformat(),
            "investigation_id": str(investigation_id),
            "status": str(status),
            "truncated": bool(truncated),
            "events": summarize_events(events),
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record, ensure_ascii=False, default=str)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as exc:
            logger.warning("조사 실행 기록 요약 기록 실패: inv=%s %s", investigation_id, exc)
            return False
        self._rotate_if_needed()
        return True

    def get_summary(self, investigation_id: str) -> dict[str, Any] | None:
        """조사 id의 최신 요약(없으면 None)."""
        if not investigation_id:
            return None
        for rec in reversed(self._read_window()):
            if rec.get("investigation_id") == investigation_id:
                return rec
        return None

    def _read_window(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                window = deque(fh, maxlen=self.max_lines)
        except OSError as exc:
            logger.warning("조사 실행 기록 요약 조회 실패(빈 결과): %s", exc)
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
            logger.warning("조사 실행 기록 요약 회전 실패(무시): %s", exc)


__all__ = [
    "DEFAULT_FILENAME",
    "EXCLUDED_KINDS",
    "InvestigationActivityStore",
    "SUMMARY_EVENT_KEYS",
    "summarize_events",
]
