"""매핑 보고서 생성/파싱 모듈.

field_mapper의 매핑 결과를 구조화된 Markdown 보고서로 생성하고,
사용자가 수정한 보고서를 파싱하여 변경사항을 추출한다.

계층: infrastructure (src/document/)
의존성: 없음 (순수 함수, IO 없음)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)


def generate_mapping_report(
    field_names: list[str],
    mapping_result: Any,
    template_name: str | None = None,
    llm_inference_details: list[dict] | None = None,
) -> str:
    """매핑 결과를 구조화된 Markdown 보고서로 생성한다.

    순수 함수로 IO가 없으며 테스트가 용이하다.
    mapping_result는 MappingResult 객체를 Any로 받아 순환 import를 방지한다.

    Args:
        field_names: 양식 필드명 목록
        mapping_result: MappingResult 객체
            - column_mapping: {field: "TABLE.COLUMN" | None}
            - db_column_mapping: {db_id: {field: "TABLE.COLUMN"}}
            - mapping_sources: {field: "hint"|"synonym"|"eav_synonym"|"llm_inferred"}
        template_name: 원본 양식 파일명 (없으면 "(알 수 없음)")
        llm_inference_details: LLM 추론 매핑 상세 목록
            - [{field, db_id, column, matched_synonym, confidence, reason}]

    Returns:
        Markdown 형식의 매핑 보고서 문자열
    """
    column_mapping: dict[str, Optional[str]] = getattr(
        mapping_result, "column_mapping", {}
    )
    db_column_mapping: dict[str, dict[str, str]] = getattr(
        mapping_result, "db_column_mapping", {}
    )
    mapping_sources: dict[str, str] = getattr(
        mapping_result, "mapping_sources", {}
    )

    # DB 역조회 맵 생성: field -> db_id
    field_db_map: dict[str, str] = _build_field_db_map(db_column_mapping)

    # LLM 추론 상세를 필드명으로 빠르게 조회할 수 있는 맵
    llm_details_map: dict[str, dict] = {}
    if llm_inference_details:
        for detail in llm_inference_details:
            field_key = detail.get("field", "")
            if field_key:
                llm_details_map[field_key] = detail

    # 통계 계산
    total = len(field_names)
    mapped_count = sum(
        1 for f in field_names if column_mapping.get(f) is not None
    )
    percent = round(mapped_count / total * 100) if total > 0 else 0

    # 보고서 생성
    lines: list[str] = []

    # 헤더
    lines.append("# 필드 매핑 보고서")
    lines.append("")
    lines.append(f"> 생성일시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> 원본 양식: {template_name or '(알 수 없음)'}")
    lines.append(f"> 매핑 성공: {mapped_count}/{total} 필드 ({percent}%)")
    lines.append("")

    # 매핑 결과 요약 테이블
    lines.append("## 매핑 결과 요약")
    lines.append("")
    lines.append("| # | 양식 필드 | 매핑 대상 | DB | 매핑 방법 | 신뢰도 |")
    lines.append("|---|----------|----------|-----|----------|--------|")

    llm_inferred_rows: list[tuple[int, str]] = []  # (index, field)

    for idx, field in enumerate(field_names, start=1):
        col = column_mapping.get(field)
        source = mapping_sources.get(field, "-")
        db_id = field_db_map.get(field, "-")

        if col is not None:
            target_display = col
            method_display = source
        else:
            target_display = "(매핑 불가)"
            db_id = "-"
            method_display = "-"

        # 신뢰도: LLM 추론 매핑인 경우에만 표시
        confidence = "-"
        if source == "llm_inferred" and field in llm_details_map:
            confidence = llm_details_map[field].get("confidence", "-")

        lines.append(
            f"| {idx} | {field} | {target_display} | {db_id} | {method_display} | {confidence} |"
        )

        # LLM 추론 상세 섹션용 수집
        if source == "llm_inferred" and field in llm_details_map:
            llm_inferred_rows.append((idx, field))

    lines.append("")

    # LLM 추론 매핑 상세 섹션
    if llm_inferred_rows:
        lines.append("## LLM 추론 매핑 상세")
        lines.append("")

        for row_idx, field in llm_inferred_rows:
            detail = llm_details_map[field]
            col = column_mapping.get(field, "")
            reason = detail.get("reason", "(근거 없음)")
            matched_synonym = detail.get("matched_synonym", "-")
            confidence = detail.get("confidence", "-")

            lines.append(f"### {row_idx}. {field} -> {col}")
            lines.append(f"- **매칭 근거**: {reason}")
            lines.append(f"- **매칭된 유사어**: {matched_synonym}")
            lines.append(f"- **신뢰도**: {confidence}")
            lines.append("")

    # 피드백 안내
    lines.append("## 피드백 안내")
    lines.append("")
    lines.append(
        "매핑을 수정하려면 이 파일을 다운로드하여 '매핑 결과 요약' 테이블을 직접 편집한 후 업로드하세요."
    )
    lines.append("- 매핑 대상 컬럼을 수정하면 Redis에 반영됩니다.")
    lines.append("- 행을 삭제하면 해당 매핑이 Redis에서 제거됩니다.")
    lines.append("- 매핑 불가 항목에 컬럼을 추가하면 새 매핑이 등록됩니다.")
    lines.append("")

    return "\n".join(lines)


def parse_mapping_report(md_content: str) -> list[dict]:
    """매핑 보고서 MD에서 매핑 테이블을 파싱한다.

    '매핑 결과 요약' 섹션의 테이블을 찾아 각 행을 파싱한다.
    파싱 실패 시 빈 리스트를 반환한다 (graceful).

    Args:
        md_content: Markdown 형식의 매핑 보고서 문자열

    Returns:
        파싱된 매핑 목록:
        [{"index": 1, "field": "서버명", "column": "CMM_RESOURCE.HOSTNAME",
          "db_id": "polestar", "method": "synonym", "confidence": "-"}]
        매핑 불가 항목은 column=None으로 변환된다.
    """
    if not md_content or not md_content.strip():
        return []

    try:
        return _parse_mapping_table(md_content)
    except Exception as e:
        logger.warning("매핑 보고서 파싱 실패: %s", e)
        return []


# === Private helpers ===


def _build_field_db_map(
    db_column_mapping: dict[str, dict[str, str]],
) -> dict[str, str]:
    """db_column_mapping에서 field -> db_id 역조회 맵을 생성한다.

    Args:
        db_column_mapping: {db_id: {field: "TABLE.COLUMN"}}

    Returns:
        {field: db_id}
    """
    field_db: dict[str, str] = {}
    for db_id, field_map in db_column_mapping.items():
        for field in field_map:
            if field not in field_db:
                field_db[field] = db_id
    return field_db


def _parse_mapping_table(md_content: str) -> list[dict]:
    """MD 본문에서 '매핑 결과 요약' 테이블을 파싱한다.

    Args:
        md_content: 전체 MD 문자열

    Returns:
        파싱된 매핑 행 목록
    """
    results: list[dict] = []

    # '매핑 결과 요약' 섹션 찾기
    lines = md_content.split("\n")
    in_table = False
    header_found = False
    separator_found = False

    for line in lines:
        stripped = line.strip()

        # 테이블 헤더 행 감지: | # | 양식 필드 | ...
        if not in_table and re.match(
            r"\|\s*#\s*\|\s*양식 필드\s*\|", stripped
        ):
            header_found = True
            in_table = True
            continue

        # 구분선 감지: |---|---|...
        if in_table and not separator_found and re.match(
            r"\|[-\s|]+\|", stripped
        ):
            separator_found = True
            continue

        # 데이터 행 파싱
        if in_table and separator_found:
            # 테이블 끝 감지: 빈 줄 또는 테이블 형식이 아닌 행
            if not stripped or not stripped.startswith("|"):
                break

            row = _parse_table_row(stripped)
            if row is not None:
                results.append(row)

    return results


def _parse_table_row(line: str) -> dict | None:
    """단일 테이블 행을 파싱한다.

    기대 형식: | # | 양식 필드 | 매핑 대상 | DB | 매핑 방법 | 신뢰도 |

    Args:
        line: MD 테이블의 한 행

    Returns:
        파싱된 딕셔너리 또는 None (파싱 실패 시)
    """
    # 앞뒤 | 제거 후 | 로 분할
    cells = [c.strip() for c in line.strip("|").split("|")]

    if len(cells) < 6:
        return None

    try:
        index = int(cells[0].strip())
    except (ValueError, IndexError):
        return None

    field = cells[1].strip()
    column_raw = cells[2].strip()
    db_id = cells[3].strip()
    method = cells[4].strip()
    confidence = cells[5].strip()

    # "(매핑 불가)" 또는 빈 값은 None으로 변환
    column: str | None = None
    if column_raw and column_raw != "(매핑 불가)":
        column = column_raw

    # db_id가 "-"이면 None 처리하지 않고 그대로 반환 (호출자가 판단)
    return {
        "index": index,
        "field": field,
        "column": column,
        "db_id": db_id if db_id != "-" else None,
        "method": method if method != "-" else None,
        "confidence": confidence if confidence != "-" else None,
    }
