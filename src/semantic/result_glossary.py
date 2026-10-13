"""조회 결과 칼럼 용어 사전 조회(plans/160 §2.3 B-1 · D-327).

**무엇인가.** 결과 표 머리글(칼럼명) 하나의 설명을 사람이 관리하는 원천에서 찾는다. 찾지 못하면
None을 돌려주고, LLM 생성은 호출자(`result_explainer`)가 한다 — 이 모듈은 LLM을 부르지 않고
어떤 원천에도 쓰지 않는다(LLM 자동 등록 금지 · D-327).

**원천과 순서.**

- DB 원천(결과 출처의 db_id마다 차례로): ① 구조 프로필(`config/db_profiles/{db_id}.yaml` —
  `patterns[].entity_columns[]`·`patterns[].known_attributes[]`의 name/description · 사람이 작성)
  ② 지식 설명 파일(`config/knowledge/{db_id}/column_descriptions.yaml` · 큐레이션)
  ③ 스키마 캐시 설명(주입된 제공자 — 운영에서는 `SchemaCacheManager.get_descriptions`).
  사람이 직접 쓴 원천을 앞에 둔다(③은 관리 화면에서 LLM이 만든 설명이 섞일 수 있다).
- 중립 키 사전(`config/result_glossary.yaml` — 본체 소유 · 첫 호출 때 1회 적재).
- APM 결과면 중립 키 사전을 DB 원천보다 먼저 본다 — DB 스코프가 직전 턴에서 승계돼 남아 있을
  때 같은 이름의 DB 칼럼 설명이 끼어드는 것을 막는다. 그 밖에는 DB 원천이 먼저다.

**꼬리 일치.** 결과 행에는 테이블명이 없으므로 `"table.column"` 키의 칼럼 부분만 대소문자 무시로
비교한다. 서로 다른 설명이 여럿이면 키 정렬 순 첫 것을 쓰고 `origin`에 「외 N종」을 적는다.

계층: application. 스키마 리터럴 금지(`overfit_check` 스캔 대상).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from src.schema_cache.catalog_builder import load_structure_profile
from src.schema_cache.knowledge_descriptions import (
    KNOWLEDGE_ROOT,
    load_knowledge_descriptions,
)
from src.schema_cache.structure_store import validate_db_id

logger = logging.getLogger(__name__)

#: 중립 키 사전 경로(작업 디렉터리와 무관하게 저장소 기준).
GLOSSARY_PATH = Path(__file__).resolve().parents[2] / "config" / "result_glossary.yaml"
GLOSSARY_VERSION = 1

#: db_id → {"table.column": 설명}. 운영에서는 `SchemaCacheManager.get_descriptions`.
DescriptionProvider = Callable[[str], Awaitable[Mapping[str, str]]]


@dataclass(frozen=True)
class ResultSource:
    """결과의 출처 — 사전 원천 선택·캐시 키·프롬프트 라벨에 쓴다."""

    db_ids: tuple[str, ...] = ()
    is_apm: bool = False

    @property
    def key(self) -> str:
        """캐시 키용 문자열(같은 출처면 결과가 달라도 같다)."""
        return ("apm|" if self.is_apm else "db|") + ",".join(self.db_ids)

    @property
    def label(self) -> str:
        """프롬프트에 싣는 출처 시스템 라벨."""
        parts: list[str] = []
        if self.is_apm:
            parts.append("애플리케이션 성능 모니터링(APM) 조회 결과")
        if self.db_ids:
            parts.append(f"데이터베이스 조회 결과({', '.join(self.db_ids)})")
        return " · ".join(parts) or "조회 결과(출처 미상)"


def result_source(stored: Mapping[str, Any]) -> ResultSource:
    """저장된 응답 dict에서 출처를 읽는다.

    `db_scope.db_ids`(목록)와 `apm_source_scope`·`apm_source_clarification` 존재로 판단한다.
    """
    scope = stored.get("db_scope")
    raw_ids = scope.get("db_ids") if isinstance(scope, Mapping) else None
    db_ids = tuple(str(d) for d in raw_ids or () if d)
    is_apm = bool(stored.get("apm_source_scope") or stored.get("apm_source_clarification"))
    return ResultSource(db_ids=db_ids, is_apm=is_apm)


@lru_cache(maxsize=4)
def load_glossary(path: Path = GLOSSARY_PATH) -> dict[str, dict[str, str]]:
    """중립 키 사전을 `{키: {"text", "unit"}}`로 읽는다(경로별 1회 적재).

    파일이 없거나 형식이 틀리면 WARNING 후 빈 dict. 설명이 빈 항목은 건너뛴다.
    """
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        logger.warning("결과 용어 사전 읽기 실패 — 빈 사전으로 둔다 (%s): %s", path, e)
        return {}
    if (
        not isinstance(data, dict)
        or data.get("version") != GLOSSARY_VERSION
        or not isinstance(data.get("terms"), dict)
    ):
        logger.warning("결과 용어 사전 형식 오류 — 빈 사전으로 둔다 (%s)", path)
        return {}
    out: dict[str, dict[str, str]] = {}
    for key, entry in data["terms"].items():
        if not isinstance(entry, dict):
            continue
        text = entry.get("text")
        if isinstance(text, str) and text.strip():
            out[str(key)] = {"text": text.strip(), "unit": str(entry.get("unit") or "").strip()}
    return out


def _hit(text: str, origin: str, unit: str = "") -> dict[str, str]:
    return {"source": "glossary", "text": text, "unit": unit, "origin": origin}


def _glossary_hit(column: str, glossary: Mapping[str, Mapping[str, str]]) -> dict[str, str] | None:
    entry = glossary.get(column)
    if entry is None:
        lowered = column.lower()
        entry = next((v for k, v in glossary.items() if k.lower() == lowered), None)
    if entry is None:
        return None
    return _hit(entry["text"], "result_glossary", entry.get("unit", ""))


def _profile_hit(column: str, db_id: str) -> dict[str, str] | None:
    """구조 프로필의 entity_columns·known_attributes에서 이름이 같은 첫 설명."""
    try:
        profile = load_structure_profile(db_id)
    except Exception as e:  # noqa: BLE001 — 원천 하나의 실패가 다른 원천 조회를 막지 않는다
        logger.warning("결과 용어 — 프로필 읽기 실패 (db_id=%s): %s", db_id, e)
        return None
    if not isinstance(profile, dict):
        return None
    lowered = column.lower()
    for pattern in profile.get("patterns") or []:
        if not isinstance(pattern, dict):
            continue
        for section in ("entity_columns", "known_attributes"):
            for item in pattern.get(section) or []:
                if not isinstance(item, dict):
                    continue
                desc = item.get("description")
                if (
                    str(item.get("name") or "").lower() == lowered
                    and isinstance(desc, str)
                    and desc.strip()
                ):
                    return _hit(desc.strip(), f"db_profile:{db_id}:{section}")
    return None


def _tail_hit(
    column: str, descriptions: Mapping[str, str], origin_prefix: str
) -> dict[str, str] | None:
    """`"table.column"` 키의 칼럼 부분이 같은(대소문자 무시) 설명 — 여럿이면 정렬 순 첫 것."""
    lowered = column.lower()
    matches = sorted(
        (str(key), text.strip())
        for key, text in descriptions.items()
        if str(key).rpartition(".")[2].lower() == lowered
        and isinstance(text, str)
        and text.strip()
    )
    if not matches:
        return None
    key, text = matches[0]
    distinct = {t for _, t in matches}
    origin = f"{origin_prefix}:{key}"
    if len(distinct) > 1:
        origin += f" 외 {len(distinct) - 1}종"
    return _hit(text, origin)


async def _db_hit(
    column: str,
    db_ids: Sequence[str],
    provider: DescriptionProvider | None,
    knowledge_root: Path,
) -> dict[str, str] | None:
    for db_id in db_ids:
        try:
            validate_db_id(db_id)
        except ValueError:
            logger.warning("결과 용어 — db_id 형식 오류로 건너뜀: %r", db_id)
            continue
        if hit := _profile_hit(column, db_id):
            return hit
        knowledge = load_knowledge_descriptions(knowledge_root, db_id)
        if knowledge and (hit := _tail_hit(column, knowledge, f"knowledge:{db_id}")):
            return hit
        if provider is None:
            continue
        try:
            cached = await provider(db_id)
        except Exception as e:  # noqa: BLE001 — 캐시 장애는 사전 미적중으로 본다
            logger.warning("결과 용어 — 스키마 캐시 설명 조회 실패 (db_id=%s): %s", db_id, e)
            continue
        if cached and (hit := _tail_hit(column, cached, f"schema_cache:{db_id}")):
            return hit
    return None


async def lookup_term(
    column: str,
    source: ResultSource,
    *,
    description_provider: DescriptionProvider | None = None,
    glossary: Mapping[str, Mapping[str, str]] | None = None,
    knowledge_root: Path = KNOWLEDGE_ROOT,
) -> dict[str, str] | None:
    """칼럼 설명을 사람이 관리하는 원천에서 찾는다 — 미적중이면 None(LLM 호출 없음).

    Returns:
        `{"source": "glossary", "text", "unit", "origin"}` 또는 None
    """
    terms = load_glossary() if glossary is None else glossary
    if source.is_apm and (hit := _glossary_hit(column, terms)):
        return hit
    if hit := await _db_hit(column, source.db_ids, description_provider, knowledge_root):
        return hit
    if not source.is_apm:
        return _glossary_hit(column, terms)
    return None
