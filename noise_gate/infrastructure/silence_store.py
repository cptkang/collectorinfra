"""침묵 규칙 저장소 (Plan 54 모듈 4).

운영자가 만든 침묵 규칙을 JSONL로 **append만** 하고, 조회 시 재생(replay)해 현재 상태를
만든다. 해제는 파일을 고치는 대신 `revoke` 레코드를 덧붙인다 — 감사 무결(누가 언제 무엇을
걸었고 풀었는지)이 규칙 자체보다 오래 남아야 하기 때문이다(`FeedbackStore` 철회 전례).

기록·조회 실패가 알람 처리를 막아서는 안 되므로 실패는 logger.warning 후 무시한다.
표준 라이브러리(json/pathlib/datetime/logging/uuid)만 사용한다.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from noise_gate.domain.silence import SilenceRule, is_active

logger = logging.getLogger(__name__)

_OP_CREATE = "create"
_OP_REVOKE = "revoke"


class SilenceStore:
    """침묵 규칙을 append-only JSONL로 적재하고 활성 규칙을 재생해 돌려준다."""

    def __init__(self, path: str, enabled: bool = True) -> None:
        """저장 경로와 활성 여부를 받는다.

        enabled=False면 조회는 빈 목록, 기록은 no-op다(플래그 off 시 회귀 0).
        """
        self.path = Path(path)
        self.enabled = enabled

    def create(
        self,
        *,
        db_id: str = "",
        server_name: str = "",
        alarm_name: str = "",
        resource_name: str = "",
        max_severity: int,
        reason: str,
        created_by: str,
        expires_at: datetime,
        now: Optional[datetime] = None,
    ) -> SilenceRule:
        """침묵 규칙을 만들어 적재하고 그 규칙을 돌려준다.

        **검증은 호출자(API 계층)의 책임**이다 — 이 저장소는 받은 것을 그대로 적재한다.
        도메인 매처가 전체 침묵을 다시 한 번 막으므로(`silence.matches`), 잘못 적재된 규칙이
        실제로 알람을 삼키지는 않는다.

        Args:
            db_id·server_name·alarm_name·resource_name: 매처 글롭(빈 문자열=무조건 일치).
            max_severity: 침묵 허용 심각도 상한.
            reason: 침묵 사유(감사·화면 표시).
            created_by: 행위자.
            expires_at: 만료 시각.
            now: 생성 시각(테스트 주입용).

        Returns:
            생성된 규칙.
        """
        created_at = now or datetime.now(timezone.utc)
        rule = SilenceRule(
            id=f"slc_{uuid.uuid4().hex[:12]}",
            db_id=db_id,
            server_name=server_name,
            alarm_name=alarm_name,
            resource_name=resource_name,
            max_severity=int(max_severity),
            reason=reason,
            created_by=created_by,
            created_at=created_at,
            expires_at=expires_at,
        )
        self._append({"op": _OP_CREATE, **rule.to_dict()})
        return rule

    def revoke(self, rule_id: str, *, revoked_by: str, now: Optional[datetime] = None) -> bool:
        """규칙을 해제한다(tombstone append — 원 레코드는 남는다).

        Args:
            rule_id: 규칙 id.
            revoked_by: 행위자.
            now: 해제 시각(테스트 주입용).

        Returns:
            해제 대상이 실제로 존재했으면 True(없거나 이미 해제면 False).
        """
        target = str(rule_id or "")
        if not target:
            return False
        existing = {r.id for r in self.list_rules(include_inactive=True)}
        if target not in existing:
            return False
        if any(r.id == target and r.revoked_at is not None for r in self.list_rules(include_inactive=True)):
            return False
        self._append({
            "op": _OP_REVOKE,
            "id": target,
            "revoked_at": (now or datetime.now(timezone.utc)).isoformat(),
            "revoked_by": revoked_by,
        })
        return True

    def list_rules(
        self, *, include_inactive: bool = False, now: Optional[datetime] = None
    ) -> list[SilenceRule]:
        """규칙 목록을 등록 순으로 돌려준다.

        Args:
            include_inactive: True면 만료·해제된 규칙도 포함한다(관리 화면 이력용).
            now: 활성 판정 기준 시각.

        Returns:
            SilenceRule 목록(파일 부재·비활성이면 빈 목록).
        """
        if not self.enabled or not self.path.exists():
            return []

        rules: dict[str, dict] = {}
        order: list[str] = []
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # 손상된 줄은 건너뛴다
                    if not isinstance(rec, dict):
                        continue
                    rule_id = str(rec.get("id", ""))
                    if not rule_id:
                        continue
                    op = rec.get("op")
                    if op == _OP_CREATE:
                        if rule_id not in rules:
                            order.append(rule_id)
                        rules[rule_id] = rec
                    elif op == _OP_REVOKE and rule_id in rules:
                        rules[rule_id]["revoked_at"] = rec.get("revoked_at")
                        rules[rule_id]["revoked_by"] = rec.get("revoked_by")
        except OSError as exc:
            logger.warning("침묵 규칙 읽기 실패(무시): %s", exc)
            return []

        moment = now or datetime.now(timezone.utc)
        result: list[SilenceRule] = []
        for rule_id in order:
            rule = self._to_rule(rules[rule_id])
            if rule is None:
                continue
            if include_inactive or is_active(rule, moment):
                result.append(rule)
        return result

    def active_rules(self, *, now: Optional[datetime] = None) -> list[SilenceRule]:
        """지금 유효한 규칙만 돌려준다(게이트 hot-path용)."""
        return self.list_rules(include_inactive=False, now=now)

    def _append(self, record: dict) -> None:
        """레코드 1줄을 append 한다(실패는 경고 후 무시)."""
        if not self.enabled:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("침묵 규칙 기록 실패(무시): %s", exc)

    @staticmethod
    def _to_rule(rec: dict) -> Optional[SilenceRule]:
        """레코드를 SilenceRule로 복원한다(필수 시각 파싱 실패면 None)."""
        created_at = _parse_ts(rec.get("created_at"))
        expires_at = _parse_ts(rec.get("expires_at"))
        if created_at is None or expires_at is None:
            return None
        try:
            max_severity = int(rec.get("max_severity", 0))
        except (TypeError, ValueError):
            return None
        return SilenceRule(
            id=str(rec.get("id", "")),
            db_id=str(rec.get("db_id", "") or ""),
            server_name=str(rec.get("server_name", "") or ""),
            alarm_name=str(rec.get("alarm_name", "") or ""),
            resource_name=str(rec.get("resource_name", "") or ""),
            max_severity=max_severity,
            reason=str(rec.get("reason", "") or ""),
            created_by=str(rec.get("created_by", "") or ""),
            created_at=created_at,
            expires_at=expires_at,
            revoked_at=_parse_ts(rec.get("revoked_at")),
        )


def _parse_ts(raw) -> Optional[datetime]:  # noqa: ANN001
    """ISO 8601 문자열을 datetime으로 파싱한다(실패·None이면 None)."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except (ValueError, TypeError):
        return None
