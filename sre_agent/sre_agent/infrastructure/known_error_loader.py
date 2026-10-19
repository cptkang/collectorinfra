"""알려진 오류(KEDB) YAML 로더 (plans/187 W8 §5.7.3 · §5.7.5 2 · D-341 ⑨).

디렉토리의 `*.yaml`을 파일명 순으로 읽어 `domain.known_error.parse_known_error`로 검증한다.
개별 파일 실패는 경고 로그를 남기고 그 레코드만 비활성한다(나머지는 그대로 쓴다). 디렉토리가 없으면
빈 목록이다(알려진 오류가 없는 것은 정상 — 경고만).

**읽기 전용** — 이 모듈에는 쓰기 API가 없다. 레코드는 저장소 YAML + 리뷰로만 바뀌고(D-341 ⑨ ·
런타임 쓰기 0), KB 초안(wip)은 본체 조사 저장소에 남아 이 경로에 존재하지 않는다(§5.7.5 2).
`state: validated` 레코드의 `owner`·`last_reviewed` 누락은 스키마가 거부한다(쓰기 지점 차단).

기본 경로는 패키지 루트의 `config/known_errors/`(`sre_agent/config/known_errors/`)다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from sre_agent.domain.known_error import KnownError, parse_known_error

logger = logging.getLogger(__name__)

#: 기본 알려진 오류 디렉토리 — 플레이북 로더와 같은 패키지 루트 기준(parents[2]).
DEFAULT_KNOWN_ERROR_DIR = Path(__file__).resolve().parents[2] / "config" / "known_errors"


@dataclass(frozen=True)
class KnownErrorLoadResult:
    """로드 결과.

    known_errors: 유효한 레코드(파일명 순 · ID 중복은 먼저 읽은 것만).
    rejected:     (파일명, 사유) — 비활성된 파일.
    """

    known_errors: tuple[KnownError, ...] = ()
    rejected: tuple[tuple[str, str], ...] = ()


def load_known_errors(directory: Path | str = DEFAULT_KNOWN_ERROR_DIR) -> KnownErrorLoadResult:
    """디렉토리의 `*.yaml`을 읽어 검증한다. 예외를 내지 않는다 — 실패는 결과에 싣는다."""
    root = Path(directory)
    if not root.is_dir():
        logger.warning("알려진 오류 디렉토리 없음(0건으로 진행): %s", root)
        return KnownErrorLoadResult()

    records: list[KnownError] = []
    rejected: list[tuple[str, str]] = []
    seen_ids: set[str] = set()
    for path in sorted(root.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("최상위가 매핑이 아니다")
            ke = parse_known_error(data)
        # ValidationError·ExprSyntaxError는 ValueError 하위다.
        except (OSError, yaml.YAMLError, ValueError) as exc:
            reason = _reason(exc)
            logger.warning("알려진 오류 비활성 %s: %s", path.name, reason)
            rejected.append((path.name, reason))
            continue
        if ke.id in seen_ids:
            reason = f"ID 중복 {ke.id!r}"
            logger.warning("알려진 오류 비활성 %s: %s", path.name, reason)
            rejected.append((path.name, reason))
            continue
        seen_ids.add(ke.id)
        records.append(ke)
    return KnownErrorLoadResult(known_errors=tuple(records), rejected=tuple(rejected))


def _reason(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        )
    return f"{type(exc).__name__}: {exc}"
