"""생성 템플릿 어댑터 — 관리자가 승인한 DB 전용 규칙 섹션을 범용 시스템 프롬프트에 끼운다 (D-294).

폴스타 어댑터처럼 DB 전용 지식을 쿼리 생성 프롬프트에 싣지만, **DB 전용 코드가 아니라 데이터 파일을
읽는 범용 어댑터**다(plans/133 §2 — Python 코드 생성은 하지 않는다 · D-214 ② 불변):

- 담당: `config/knowledge/{db_id}/prompt_template.yaml`(자산 자동 생성 승인본 · `AssetFileStore`
  기록)에 비어 있지 않은 `section`이 있고 `db_id`가 일치하는 DB. 폴스타 어댑터가 먼저 등록돼 폴스타
  담당 DB는 영향이 없다.
- 템플릿: 범용 템플릿(`QUERY_GENERATOR_SYSTEM_TEMPLATE` — 안전 규칙·출력 형식·자리표시자는 코드
  소유)의 구조 안내 자리 뒤에 「DB 전용 규칙(관리자 승인 생성본)」을 넣는다. 섹션의 중괄호는 승인 전
  검증이 막지만 `.format()` 충돌을 피하려 한 번 더 이스케이프한다.
- 전용 검증기는 없다(`validator_checks` = `[]`) · 알람 결정적 조립 같은 폴스타 기능은 붙지 않는다.
- 파일은 (수정 시각, 크기)로 캐시한다 — 승인·되돌리기 뒤 다음 질의부터 반영되고, 질의마다 다시 읽지
  않는다.
- 멀티 DB 경로에서도 경로 대칭 플래그와 무관하게 적용한다(`multi_path_always`) — 신규 기능이라
  보호할 종전 동작이 없고, 단일·멀티 비대칭을 만들지 않기 위해서다.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from src.prompts.query_generator import QUERY_GENERATOR_SYSTEM_TEMPLATE
from src.schema_cache.asset_store import ASSET_PATHS

logger = logging.getLogger(__name__)

_DB_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_SECTION_TITLE = "## DB 전용 규칙 (관리자 승인 생성본)"


def compose_template(section: str) -> str:
    """범용 템플릿의 구조 안내 뒤에 DB 전용 규칙 섹션을 넣은 템플릿(``.format`` 자리표시자 유지)."""
    escaped = section.strip().replace("{", "{{").replace("}", "}}")
    return QUERY_GENERATOR_SYSTEM_TEMPLATE.replace(
        "{structure_guide}", "{structure_guide}\n\n" + _SECTION_TITLE + "\n\n" + escaped, 1,
    )


class BoundGeneratedTemplate:
    """한 DB에 묶인 생성 템플릿 어댑터(`get_adapter`가 돌려주는 것)."""

    name = "generated_template"
    multi_path_always = True

    def __init__(self, db_id: str, section: str) -> None:
        self.db_id = db_id
        self.section = section

    def owns(self, db_id: str | None, polestar_db_ids: set[str] | None = None) -> bool:
        return db_id == self.db_id

    def system_template(self, routing_intent: str | None) -> str | None:
        return compose_template(self.section)

    def validator_checks(
        self, user_query: str | None = None, *, time_resolution: dict[str, Any] | None = None,
    ) -> list[Callable[[str], list[str]]]:
        return []


class GeneratedTemplateAdapter:
    """레지스트리에 등록되는 생성 템플릿 어댑터 — 담당 판정과 DB별 바인딩."""

    name = "generated_template"
    multi_path_always = True

    def __init__(self, root: Path | None = None) -> None:
        """어댑터를 만든다.

        Args:
            root: 현행 파일 기준 경로(없으면 작업 디렉터리 — 앱은 저장소 루트에서 뜬다)
        """
        self._root = root
        self._cache: dict[str, tuple[tuple[int, int], str | None]] = {}

    def _path(self, db_id: str) -> Path:
        return (self._root or Path.cwd()) / ASSET_PATHS["prompt_template"].format(db_id=db_id)

    def section(self, db_id: str | None) -> str | None:
        """승인된 DB 전용 규칙 섹션(없거나 읽을 수 없으면 None)."""
        if not db_id or not _DB_ID_RE.fullmatch(db_id):
            return None
        path = self._path(db_id)
        try:
            stat = path.stat()
        except OSError:
            self._cache.pop(db_id, None)
            return None
        stamp = (stat.st_mtime_ns, stat.st_size)
        cached = self._cache.get(db_id)
        if cached is not None and cached[0] == stamp:
            return cached[1]
        section: str | None = None
        try:
            data: Any = yaml.safe_load(path.read_bytes().decode("utf-8"))
            if (
                isinstance(data, dict) and data.get("db_id") == db_id
                and isinstance(data.get("section"), str) and data["section"].strip()
            ):
                section = data["section"]
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
            logger.warning("DB 전용 규칙 섹션 읽기 실패 (%s): %s", path, e)
        self._cache[db_id] = (stamp, section)
        return section

    def owns(self, db_id: str | None, polestar_db_ids: set[str] | None = None) -> bool:
        return self.section(db_id) is not None

    def bind(self, db_id: str | None) -> BoundGeneratedTemplate | None:
        """담당 DB에 묶인 어댑터(섹션이 없으면 None)."""
        section = self.section(db_id)
        return BoundGeneratedTemplate(str(db_id), section) if section is not None else None

    def system_template(self, routing_intent: str | None) -> str | None:
        return None  # DB를 모르는 상태에서는 쓰지 않는다 — `bind`로 묶은 어댑터를 쓴다

    def validator_checks(
        self, user_query: str | None = None, *, time_resolution: dict[str, Any] | None = None,
    ) -> list[Callable[[str], list[str]]]:
        return []
