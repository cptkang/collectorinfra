"""운영자 피드백 저장소 (Plan 52 Phase E4 — LLM 액션가능성 few-shot).

운영자가 알람에 매긴 라벨(유효/노이즈)을 JSONL 1줄로 append 적재하고, 유사 과거 알람의
라벨을 few-shot 예시로 조회한다(alarm_name·resource_name·pattern 기준, §3.9). 조회 결과는
LLM 액션가능성 자문(alarm_analyzer)의 보조 입력으로만 쓰이며, 발송 판단은 결정적 규칙이
내린다(재현율 우선·승격 비대칭).

decision_store.py를 미러링한 경량 저장소다. 기록·조회 실패가 알람 발송·응답을 막아서는
안 되므로 실패는 logger.warning 후 무시한다(graceful degradation).

표준 라이브러리(json/pathlib/datetime/logging)만 사용한다. domain·외부 패키지 import 금지
(순수 infrastructure).
"""

from __future__ import annotations

import json
import logging
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 운영자 라벨 — noise(노이즈)|valid(유효)만 허용한다(그 외는 기록·후보에서 제외).
_VALID_LABELS = ("noise", "valid")

# (Plan 83 T4) 철회 tombstone 라벨. 파일 재작성 없이 append만으로 라벨을 무효화한다
# — append-only 감사 원칙을 지키면서 오클릭을 되돌리기 위한 유일한 수단이다.
_RETRACT_LABEL = "retract"

# (Plan 83 T4) 회전 기본 상한. 조회는 파일 끝에서 이 줄 수만큼만 창으로 삼으므로,
# 회전 전까지는 전체 스캔과 결과가 동일하다(등가성 테스트로 고정).
_DEFAULT_MAX_LINES = 20000


class FeedbackStore:
    """운영자 피드백을 JSONL로 적재하고 유사 과거 알람 라벨을 few-shot으로 조회한다."""

    def __init__(
        self, path: str, enabled: bool = True, max_lines: int = _DEFAULT_MAX_LINES
    ) -> None:
        """저장 경로·활성 여부·회전 상한을 받는다.

        enabled=False면 record_feedback/find_similar가 부작용 없이 no-op 동작한다
        (record는 미기록, find_similar는 빈 리스트 반환).
        max_lines는 적재 회전 상한이자 조회 창(window) 크기다 — 창 안에서는 전체 스캔과
        결과가 같고, 상한을 넘으면 오래된 절반이 `<path>.1`로 밀린다(2세대·Plan 83 T4).
        """
        self.path = Path(path)
        self.enabled = enabled
        self.max_lines = max(int(max_lines), 1)

    def record_feedback(
        self,
        *,
        label: str,
        alarm_name: str,
        resource_name: str = "",
        pattern: str = "",
        server_name: str = "",
        db_id: str = "",
        severity: Optional[int] = None,
        note: str = "",
        labeled_by: str = "",
        ts: Optional[datetime] = None,
    ) -> None:
        """운영자 피드백을 JSONL 한 줄로 append 한다.

        label은 "noise"|"valid"만 허용하며, 그 외 값은 기록하지 않고 warning 후 무시한다.
        디렉토리는 자동 생성하며, 기록 실패(OSError) 시 logger.warning 후 무시한다
        (발송·응답 차단 금지). enabled=False면 no-op.

        labeled_by(Plan 83 T4)는 **감사 전용**이다 — 누가 남긴 라벨인지 추적하고 오염 시
        회수하기 위한 필드이며, few-shot 프롬프트 렌더에는 싣지 않는다(작성자 이름이 LLM
        판단에 개입할 이유가 없다).
        """
        if not self.enabled:
            return
        if label not in _VALID_LABELS:
            logger.warning("허용되지 않은 피드백 라벨(무시): %r", label)
            return
        when = ts or datetime.now(timezone.utc)
        record = {
            "ts": when.isoformat(),
            "label": label,
            "alarm_name": alarm_name,
            "resource_name": resource_name,
            "pattern": pattern,
            "server_name": server_name,
            "db_id": db_id,
            "severity": severity,
            "note": note,
            "labeled_by": labeled_by,
        }
        self._append(record)

    def record_retract(
        self,
        *,
        target_ts: str,
        alarm_name: str = "",
        labeled_by: str = "",
        ts: Optional[datetime] = None,
    ) -> None:
        """라벨 철회를 tombstone 한 줄로 append 한다 (Plan 83 T4).

        파일을 **재작성하지 않는다** — 원본 레코드는 감사 추적용으로 남고 `find_similar`
        후보에서만 빠진다(append-only 원칙 · 동시 쓰기 위험 회피). target_ts가 비면 no-op.
        """
        if not self.enabled or not target_ts:
            return
        when = ts or datetime.now(timezone.utc)
        self._append({
            "ts": when.isoformat(),
            "label": _RETRACT_LABEL,
            "target_ts": target_ts,
            "alarm_name": alarm_name,
            "labeled_by": labeled_by,
        })

    def find_similar(
        self,
        *,
        alarm_name: str,
        resource_name: str = "",
        pattern: str = "",
        limit: int = 3,
    ) -> list[dict]:
        """유사 과거 피드백을 랭킹·최신 우선으로 최대 limit개 반환한다(few-shot 예시).

        alarm_name 일치(필수)를 후보로 삼되, resource_name/pattern이 함께 일치하면 가점을
        주어 우선 랭킹한다. 동점이면 최신(파일 뒤쪽) 항목을 우선한다.

        조회 범위는 파일 끝에서 max_lines 줄(창)이다 — 회전 상한과 같은 값이므로 회전 전까지는
        전체 스캔과 결과가 동일하다(Plan 83 T4). 철회(tombstone)된 레코드는 제외한다.

        반환 dict 키(표시용): label, alarm_name, resource_name, pattern, note.
        파일 없음·비활성·alarm_name 미지정·읽기 실패(OSError)면 빈 리스트를 반환한다(graceful).
        """
        if not self.enabled or not alarm_name or not self.path.exists():
            return []
        rows = self._read_window()
        if rows is None:
            return []
        # 철회 대상 ts 집합 — 원본은 파일에 남지만 후보에서 뺀다.
        retracted = {
            r.get("target_ts")
            for r in rows
            if r.get("label") == _RETRACT_LABEL and r.get("target_ts")
        }
        # (가점, 원-순서) 튜플로 정렬한다 — 가점 우선, 동점 시 최신(원-순서 큰 값) 우선.
        candidates: list[tuple[int, int, dict]] = []
        for order, rec in enumerate(rows):
            if rec.get("label") not in _VALID_LABELS:
                continue
            if rec.get("alarm_name") != alarm_name:
                continue
            if rec.get("ts") in retracted:
                continue
            score = 0
            if resource_name and rec.get("resource_name") == resource_name:
                score += 2
            if pattern and rec.get("pattern") == pattern:
                score += 1
            candidates.append((
                score,
                order,
                {
                    "label": rec.get("label", ""),
                    "alarm_name": rec.get("alarm_name", ""),
                    "resource_name": rec.get("resource_name", ""),
                    "pattern": rec.get("pattern", ""),
                    "note": rec.get("note", ""),
                },
            ))
        # 가점 내림차순 → 최신(원-순서 내림차순) 우선
        candidates.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return [item for _, _, item in candidates[: max(limit, 0)]]

    def summarize(self, *, limit: int = 100) -> list[dict]:
        """(alarm_name, resource_name)별 라벨 집계를 최근 활동 순으로 반환한다 (Plan 83 T13).

        여러 운영자가 같은 알람에 **상반된 라벨**을 남기면 조회 랭킹상 최신 1건이 이기는데,
        그 상충을 사람이 볼 수 있게 하는 것이 목적이다 — **판정 로직은 바꾸지 않는다**.
        철회(tombstone)된 레코드는 집계에서 빠진다(find_similar와 같은 규약).

        반환 dict 키: alarm_name, resource_name, valid, noise, last_label, last_labeled_by, last_ts.
        비활성·파일 없음·읽기 실패면 빈 리스트(graceful).
        """
        if not self.enabled or not self.path.exists():
            return []
        rows = self._read_window()
        if rows is None:
            return []
        retracted = {
            r.get("target_ts")
            for r in rows
            if r.get("label") == _RETRACT_LABEL and r.get("target_ts")
        }
        agg: dict[tuple, dict] = {}
        for rec in rows:
            label = rec.get("label")
            if label not in _VALID_LABELS or rec.get("ts") in retracted:
                continue
            key = (rec.get("alarm_name", ""), rec.get("resource_name", ""))
            item = agg.setdefault(key, {
                "alarm_name": key[0],
                "resource_name": key[1],
                "valid": 0,
                "noise": 0,
                "last_label": "",
                "last_labeled_by": "",
                "last_ts": "",
            })
            item[label] += 1
            # 파일 순서가 곧 시간 순서다(append-only) — 마지막 것이 최신
            item["last_label"] = label
            item["last_labeled_by"] = rec.get("labeled_by", "")
            item["last_ts"] = rec.get("ts", "")
        ordered = sorted(agg.values(), key=lambda i: i["last_ts"], reverse=True)
        return ordered[: max(limit, 0)]

    # ─── 내부 helper (Plan 83 T4) ────────────────────────────────────────────

    def _append(self, record: dict) -> None:
        """레코드 한 줄을 append하고 필요 시 회전한다(실패는 warning 후 무시).

        회전 검사는 append 시점에만 한다 — 적재는 운영자 클릭 빈도(드묾)라 이 비용이
        문제가 되지 않는다. hot path인 조회는 `_read_window`가 창만 읽는다.
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record, ensure_ascii=False)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as exc:  # 디스크/권한 등 — 응답을 막지 않고 경고만
            logger.warning("피드백 기록 실패(무시): %s", exc)
            return
        self._rotate_if_needed()

    def _read_window(self) -> Optional[list[dict]]:
        """파일 끝에서 max_lines 줄을 읽어 파싱 결과를 순서대로 반환한다.

        손상된 줄은 건너뛴다. 읽기 실패(OSError)면 None(호출자는 빈 리스트 반환).
        """
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                window = deque(fh, maxlen=self.max_lines)
        except OSError as exc:
            logger.warning("피드백 조회 실패(무시): %s", exc)
            return None
        rows: list[dict] = []
        for line in window:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # 손상된 줄은 건너뜀
        return rows

    def _rotate_if_needed(self) -> None:
        """상한을 넘으면 오래된 절반을 `<path>.1`로 밀어낸다(2세대 회전).

        회전은 조회 창(max_lines)과 같은 상한을 쓰므로, 회전 이전 구간의 조회 결과는
        전체 스캔과 동일하다. 실패는 warning 후 무시한다(적재를 막지 않는다).
        """
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                lines = fh.readlines()
            if len(lines) <= self.max_lines:
                return
            keep = max(len(lines) // 2, 1)
            archive = Path(str(self.path) + ".1")
            with archive.open("a", encoding="utf-8") as fh:
                fh.writelines(lines[:-keep])
            with self.path.open("w", encoding="utf-8") as fh:
                fh.writelines(lines[-keep:])
        except OSError as exc:
            logger.warning("피드백 회전 실패(무시): %s", exc)
