"""설명 정본 파일 읽기 — ``config/knowledge/{db_id}/column_descriptions.yaml``.

D-314 ④ · plans/141 §4.5.

외부망에서 Claude Code가 쓰고 빌더가 내는 컬럼 설명 파일이다. 형식(빌더와의 계약)::

    # 머리 주석
    version: 1
    origin: claude_code
    descriptions:
      "<table>.<column>": "<설명>"

적재(Redis에 없는 컬럼만 채움)는 `SchemaCacheManager`가 스키마 로드 때 한다 — 이 모듈은 파일을
읽고 형식을 확인할 뿐이다. 파일이 없으면 None(무동작), 형식 오류도 WARNING 후 None이다.

계층: infrastructure(`src/schema_cache`). 스키마 리터럴 금지(`overfit_check` 스캔 대상).
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from src.schema_cache.structure_store import validate_db_id

logger = logging.getLogger(__name__)

#: 지식 루트(작업 디렉터리와 무관하게 저장소 기준)
KNOWLEDGE_ROOT = Path(__file__).resolve().parents[2] / "config" / "knowledge"
FILE_NAME = "column_descriptions.yaml"
FORMAT_VERSION = 1
ORIGIN_CLAUDE_CODE = "claude_code"


def descriptions_path(knowledge_root: Path, db_id: str) -> Path:
    """설명 정본 파일 경로."""
    return Path(knowledge_root) / validate_db_id(db_id) / FILE_NAME


def load_knowledge_descriptions(knowledge_root: Path, db_id: str) -> dict[str, str] | None:
    """설명 정본 파일을 ``{"table.column": 설명}``으로 읽는다.

    파일이 없으면 None. 읽기 실패 · ``version``/``origin`` 불일치 · ``descriptions``가 매핑이
    아니면 WARNING 후 None. 항목 단위로 키가 ``table.column`` 형식이 아니거나 설명이 빈 문자열이
    아니면 그 항목만 건너뛴다(건수 로그).
    """
    path = descriptions_path(knowledge_root, db_id)
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        logger.warning("설명 정본 파일 읽기 실패 — 적재 안 함 (%s): %s", path, e)
        return None
    if (
        not isinstance(data, dict)
        or data.get("version") != FORMAT_VERSION
        or data.get("origin") != ORIGIN_CLAUDE_CODE
        or not isinstance(data.get("descriptions"), dict)
    ):
        logger.warning(
            "설명 정본 파일 형식 오류 — 적재 안 함 (%s): "
            "version=%s·origin=%s·descriptions 매핑 필요",
            path, FORMAT_VERSION, ORIGIN_CLAUDE_CODE,
        )
        return None
    out: dict[str, str] = {}
    for key, text in data["descriptions"].items():
        table, _, column = str(key).rpartition(".")
        if table and column and isinstance(text, str) and text.strip():
            out[str(key)] = text
    skipped = len(data["descriptions"]) - len(out)
    if skipped:
        logger.warning("설명 정본 파일 항목 형식 오류 %d건 건너뜀 (%s)", skipped, path)
    return out
