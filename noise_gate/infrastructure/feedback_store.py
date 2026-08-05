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
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 운영자 라벨 — noise(노이즈)|valid(유효)만 허용한다(그 외는 기록·후보에서 제외).
_VALID_LABELS = ("noise", "valid")


class FeedbackStore:
    """운영자 피드백을 JSONL로 적재하고 유사 과거 알람 라벨을 few-shot으로 조회한다."""

    def __init__(self, path: str, enabled: bool = True) -> None:
        """저장 경로와 활성 여부를 받는다.

        enabled=False면 record_feedback/find_similar가 부작용 없이 no-op 동작한다
        (record는 미기록, find_similar는 빈 리스트 반환).
        """
        self.path = Path(path)
        self.enabled = enabled

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
        ts: Optional[datetime] = None,
    ) -> None:
        """운영자 피드백을 JSONL 한 줄로 append 한다.

        label은 "noise"|"valid"만 허용하며, 그 외 값은 기록하지 않고 warning 후 무시한다.
        디렉토리는 자동 생성하며, 기록 실패(OSError) 시 logger.warning 후 무시한다
        (발송·응답 차단 금지). enabled=False면 no-op.
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
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record, ensure_ascii=False)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as exc:  # 디스크/권한 등 — 응답을 막지 않고 경고만
            logger.warning("피드백 기록 실패(무시): %s", exc)

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

        반환 dict 키(표시용): label, alarm_name, resource_name, pattern, note.
        파일 없음·비활성·alarm_name 미지정·읽기 실패(OSError)면 빈 리스트를 반환한다(graceful).
        """
        if not self.enabled or not alarm_name or not self.path.exists():
            return []
        # (가점, 원-순서) 튜플로 정렬한다 — 가점 우선, 동점 시 최신(원-순서 큰 값) 우선.
        candidates: list[tuple[int, int, dict]] = []
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                for order, line in enumerate(fh):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # 손상된 줄은 건너뜀
                    if rec.get("label") not in _VALID_LABELS:
                        continue
                    if rec.get("alarm_name") != alarm_name:
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
        except OSError as exc:
            logger.warning("피드백 조회 실패(무시): %s", exc)
            return []
        # 가점 내림차순 → 최신(원-순서 내림차순) 우선
        candidates.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return [item for _, _, item in candidates[: max(limit, 0)]]
