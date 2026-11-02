"""담당 업무 어휘 선언 파일 로더 (plans/201 W1 · D-354).

정본은 `config/duty_areas.yaml` 하나다. 판정은 `src.domain.duty_areas`가 하고, 이 모듈은 파일을
읽어 어휘 객체로 넘기기만 한다(`src/domain/change_terms.py`의 선언 파일 선례 — 읽기와 판정 분리).

**파일 부재·손상은 빈 어휘 + 경고 1줄**이다 — 예외로 앱을 죽이지 않고, 조용히 넘기지도 않는다.
빈 어휘면 가입·관리 화면에 고를 영역이 없을 뿐이다. 프로세스당 1회 읽는다(재시작 반영).
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import yaml

from src.domain.duty_areas import DutyAreaVocabulary, parse_vocabulary

logger = logging.getLogger(__name__)

#: 선언 파일 정본 경로.
DUTY_AREAS_FILE = Path(__file__).resolve().parents[2] / "config" / "duty_areas.yaml"


def read_duty_area_vocabulary(path: Path) -> DutyAreaVocabulary:
    """선언 파일을 읽어 어휘 객체로 바꾼다(캐시 없음 · 테스트 주입점).

    Args:
        path: 선언 파일 경로

    Returns:
        어휘. 파일 부재·YAML 손상·최상위 형식 오류면 빈 어휘(경고 1줄).
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("담당 업무 어휘 파일 없음 — 빈 어휘로 동작: %s", path)
        return DutyAreaVocabulary()
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("담당 업무 어휘 파일 읽기 실패 — 빈 어휘로 동작: %s (%s)", path, exc)
        return DutyAreaVocabulary()
    if raw is not None and not isinstance(raw, dict):
        logger.warning(
            "담당 업무 어휘 파일 형식 오류(최상위가 매핑 아님) — 빈 어휘로 동작: %s", path
        )
        return DutyAreaVocabulary()
    return parse_vocabulary(raw)


@lru_cache(maxsize=1)
def load_duty_area_vocabulary() -> DutyAreaVocabulary:
    """정본 선언 파일의 어휘(프로세스당 1회 읽음)."""
    return read_duty_area_vocabulary(DUTY_AREAS_FILE)
