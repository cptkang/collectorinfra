"""플레이북 YAML 로더 (plans/187 §5.2 · D-341).

디렉토리의 `*.yaml`을 파일명 순으로 읽어 `domain.playbook.parse_playbook`으로 검증한다.
개별 파일 실패는 경고 로그를 남기고 그 플레이북만 비활성한다(나머지는 그대로 쓴다).
fallback 플레이북이 없거나 무효면 결과의 `error`에 사유를 싣는다 — 호출자(W4 배선)가
기동 경고로 노출한다(침묵 금지).

기본 경로는 패키지 루트의 `config/playbooks/`(`sre_agent/config/playbooks/`)다.
경로는 함수 인자로 받는다 — 설정 키 배선은 W4에서 한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from sre_agent.domain.playbook import FALLBACK_ID, Playbook, parse_playbook

logger = logging.getLogger(__name__)

#: 기본 플레이북 디렉토리 — `investigation_jobs._DEFAULT_AUDIT_PATH`와 같은
#: 패키지 루트 기준(parents[2]).
DEFAULT_PLAYBOOK_DIR = Path(__file__).resolve().parents[2] / "config" / "playbooks"


@dataclass(frozen=True)
class PlaybookLoadResult:
    """로드 결과.

    playbooks: 유효한 플레이북(파일명 순 · ID 중복은 먼저 읽은 것만).
    rejected:  (파일명, 사유) — 비활성된 파일.
    error:     fallback 부재·무효 등 플레이북 세트 단위 오류(정상이면 None).
    """

    playbooks: tuple[Playbook, ...] = ()
    rejected: tuple[tuple[str, str], ...] = ()
    error: str | None = None

    @property
    def fallback(self) -> Playbook | None:
        """fallback 플레이북(없으면 None)."""
        return next((pb for pb in self.playbooks if pb.is_fallback), None)


def load_playbooks(directory: Path | str = DEFAULT_PLAYBOOK_DIR) -> PlaybookLoadResult:
    """디렉토리의 `*.yaml`을 읽어 검증한다. 예외를 내지 않는다 — 실패는 결과에 싣는다."""
    root = Path(directory)
    if not root.is_dir():
        msg = f"플레이북 디렉토리 없음: {root}"
        logger.warning(msg)
        return PlaybookLoadResult(error=msg)

    playbooks: list[Playbook] = []
    rejected: list[tuple[str, str]] = []
    seen_ids: set[str] = set()
    for path in sorted(root.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("최상위가 매핑이 아니다")
            pb = parse_playbook(data)
        # ValidationError·ExprSyntaxError는 ValueError 하위다.
        except (OSError, yaml.YAMLError, ValueError) as exc:
            reason = _reason(exc)
            logger.warning("플레이북 비활성 %s: %s", path.name, reason)
            rejected.append((path.name, reason))
            continue
        if pb.id in seen_ids:
            reason = f"ID 중복 {pb.id!r}"
            logger.warning("플레이북 비활성 %s: %s", path.name, reason)
            rejected.append((path.name, reason))
            continue
        seen_ids.add(pb.id)
        playbooks.append(pb)

    error = None
    if FALLBACK_ID not in seen_ids:
        bad = [name for name, _ in rejected if Path(name).stem == FALLBACK_ID]
        error = "fallback 플레이북 무효" if bad else "fallback 플레이북 없음"
        logger.warning("%s: %s", error, root)
    return PlaybookLoadResult(playbooks=tuple(playbooks), rejected=tuple(rejected), error=error)


def _reason(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        )
    return f"{type(exc).__name__}: {exc}"
