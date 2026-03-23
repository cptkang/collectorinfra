"""입력 파서 노드.

사용자 입력을 분석하여 구조화된 요구사항을 추출한다.
Phase 1에서는 자연어 파싱만 구현하고, Phase 2에서 양식 파싱을 추가한다.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from src.config import AppConfig, load_config
from src.llm import create_llm
from src.prompts.input_parser import INPUT_PARSER_SYSTEM_PROMPT
from src.state import AgentState
from src.utils.json_extract import extract_json_from_response

logger = logging.getLogger(__name__)


async def input_parser(
    state: AgentState,
    *,
    llm: BaseChatModel | None = None,
    app_config: AppConfig | None = None,
) -> dict:
    """사용자 입력을 파싱하여 구조화된 요구사항을 추출한다.

    Phase 1: 자연어 질의 분석
    Phase 2: 양식 파일 구조 분석 추가

    Args:
        state: 현재 에이전트 상태
        llm: LLM 인스턴스 (외부 주입, 없으면 내부 생성)
        app_config: 앱 설정 (외부 주입, 없으면 내부 로드)

    Returns:
        업데이트할 State 필드:
        - parsed_requirements: 구조화된 요구사항 딕셔너리
        - template_structure: 양식 구조 (파일 업로드 시, Phase 2)
        - current_node: "input_parser"
        - error_message: None (정상 완료 시)
    """
    if app_config is None:
        app_config = load_config()
    if llm is None:
        llm = create_llm(app_config)

    try:
        # 1. 자연어 질의 파싱 (멀티턴 맥락 주입)
        context = state.get("conversation_context")
        parsed = await _parse_natural_language(
            llm, state["user_query"], conversation_context=context
        )
    except Exception as e:
        logger.error(f"입력 파싱 실패: {e}")
        # 최소한의 파싱 결과로 진행 (그래프가 중단되지 않도록)
        parsed = {
            "original_query": state["user_query"],
            "query_targets": [],
            "filter_conditions": [],
            "output_format": "text",
        }

    # 2. 파일 업로드 처리 (Phase 2)
    template: Optional[dict] = None
    if state.get("uploaded_file") and state.get("file_type"):
        template = _parse_uploaded_file(
            state["uploaded_file"],
            state["file_type"],
        )
        if template:
            parsed["output_format"] = state["file_type"]

    # 3. 시트명 추출
    target_sheets = _extract_target_sheets(parsed, state["user_query"])

    logger.info(
        "입력 파싱 완료: targets=%s, target_sheets=%s",
        parsed.get("query_targets", []),
        target_sheets,
    )

    return {
        "parsed_requirements": parsed,
        "template_structure": template,
        "target_sheets": target_sheets,
        "current_node": "input_parser",
        "error_message": None,
    }


async def _parse_natural_language(
    llm: BaseChatModel,
    user_query: str,
    *,
    conversation_context: dict | None = None,
) -> dict:
    """LLM을 사용하여 자연어 질의에서 요구사항을 추출한다.

    JSON 파싱 실패 시 1회 재시도한다.
    멀티턴 대화 시 이전 맥락을 프롬프트에 포함한다.

    Args:
        llm: LLM 인스턴스
        user_query: 사용자 자연어 질의 (한국어)
        conversation_context: 이전 대화 맥락 (멀티턴 시)

    Returns:
        구조화된 요구사항 딕셔너리
    """
    system_prompt = INPUT_PARSER_SYSTEM_PROMPT

    # 멀티턴: 이전 맥락 주입
    if conversation_context and conversation_context.get("turn_count", 0) > 1:
        context_section = (
            "\n\n## 이전 대화 맥락\n"
            f"- 이전 SQL: {conversation_context.get('previous_sql', '없음')}\n"
            f"- 이전 결과: {conversation_context.get('previous_results_summary', '없음')}\n"
            f"- 사용된 테이블: {', '.join(conversation_context.get('previous_tables', []))}\n"
            f"- 대화 턴: {conversation_context['turn_count']}번째\n\n"
            "사용자가 '그것', '아까', '위의', '그 중에서' 등 이전 대화를 참조하는 "
            "표현을 사용하면, 이전 맥락을 활용하여 요구사항을 해석하세요.\n"
            "'아까 결과를 Excel로' 같은 요청은 output_format을 'xlsx'로 설정하고, "
            "이전 SQL/테이블 정보를 활용하세요.\n"
        )
        system_prompt = system_prompt + context_section

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_query),
    ]

    parsed: dict = {}
    for attempt in range(2):  # 최대 2회 시도
        response = await llm.ainvoke(messages)
        parsed = extract_json_from_response(response.content) or {}
        if parsed and parsed.get("query_targets"):
            break
        # 재시도 시 힌트 추가
        messages.append(HumanMessage(
            content="반드시 유효한 JSON만 출력하세요. query_targets는 필수입니다."
        ))

    # 원본 질의 보존
    parsed["original_query"] = user_query

    # 기본값 설정
    parsed.setdefault("output_format", "text")
    parsed.setdefault("query_targets", [])
    parsed.setdefault("filter_conditions", [])
    parsed.setdefault("time_range", None)
    parsed.setdefault("aggregation", None)
    parsed.setdefault("limit", None)
    parsed.setdefault("field_mapping_hints", [])
    parsed.setdefault("target_db_hints", [])
    parsed.setdefault("synonym_registration", None)

    return parsed


def _parse_uploaded_file(
    file_data: bytes,
    file_type: str,
) -> Optional[dict]:
    """업로드된 양식 파일의 구조를 분석한다. (Phase 2)

    Args:
        file_data: 파일 바이너리 데이터
        file_type: "xlsx" | "docx"

    Returns:
        양식 구조 딕셔너리 또는 None
    """
    if file_type == "xlsx":
        try:
            from src.document.excel_parser import parse_excel_template

            return parse_excel_template(file_data)
        except ImportError:
            logger.warning("Excel 파서 미구현 (Phase 2)")
            return None
        except (ValueError, Exception) as e:
            logger.error("Excel 파일 파싱 실패: %s", e)
            return None
    elif file_type == "docx":
        try:
            from src.document.word_parser import parse_word_template

            return parse_word_template(file_data)
        except ImportError:
            logger.warning("Word 파서 미구현 (Phase 2)")
            return None
        except (ValueError, Exception) as e:
            logger.error("Word 파일 파싱 실패: %s", e)
            return None
    elif file_type == "doc":
        # .doc -> .docx 변환 후 처리
        converted = _convert_doc_to_docx(file_data)
        if converted is not None:
            try:
                from src.document.word_parser import parse_word_template

                return parse_word_template(converted)
            except (ImportError, ValueError, Exception) as e:
                logger.error(".doc 변환 후 파싱 실패: %s", e)
                return None
        else:
            logger.warning(
                ".doc 파일을 .docx로 변환할 수 없습니다. "
                ".docx 형식으로 변환하여 업로드해 주세요."
            )
            return None
    else:
        logger.warning(f"지원하지 않는 파일 형식: {file_type}")
        return None


def _extract_target_sheets(
    parsed: dict,
    user_query: str,
) -> Optional[list[str]]:
    """파싱 결과 또는 사용자 질의에서 대상 시트명을 추출한다.

    LLM 파싱 결과의 target_sheets를 우선 사용하고,
    없으면 정규식으로 사용자 질의에서 시트명을 추출한다.

    Args:
        parsed: LLM 파싱 결과
        user_query: 사용자 원본 질의

    Returns:
        시트명 목록 또는 None (전체 시트 대상)
    """
    # 1. LLM 파싱 결과에서 추출
    llm_sheets = parsed.get("target_sheets")
    if llm_sheets and isinstance(llm_sheets, list) and len(llm_sheets) > 0:
        return llm_sheets

    # 2. 정규식 폴백: 따옴표로 감싼 시트명 + "시트" 키워드
    patterns = [
        # '시트명' 시트, "시트명" 시트
        r"""['"\u2018\u2019\u201c\u201d]([^'"\u2018\u2019\u201c\u201d]+)['"\u2018\u2019\u201c\u201d]\s*시트""",
        # XX시트만, XX시트에
        r"""['"\u2018\u2019\u201c\u201d]([^'"\u2018\u2019\u201c\u201d]+)['"\u2018\u2019\u201c\u201d]\s*(?:시트)?\s*(?:만|에|를|의)""",
    ]
    sheets: list[str] = []
    for pattern in patterns:
        matches = re.findall(pattern, user_query)
        for match in matches:
            name = match.strip()
            if name and name not in sheets:
                sheets.append(name)

    return sheets if sheets else None


def _convert_doc_to_docx(file_data: bytes) -> Optional[bytes]:
    """`.doc` 파일을 `.docx`로 변환한다.

    libreoffice를 사용하여 변환한다.
    libreoffice가 설치되어 있지 않으면 None을 반환한다.

    Args:
        file_data: .doc 파일 바이너리

    Returns:
        .docx 바이너리 또는 None (변환 실패 시)
    """
    import subprocess
    import tempfile
    from pathlib import Path

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            doc_path = Path(tmpdir) / "input.doc"
            doc_path.write_bytes(file_data)

            result = subprocess.run(
                [
                    "libreoffice",
                    "--headless",
                    "--convert-to",
                    "docx",
                    "--outdir",
                    tmpdir,
                    str(doc_path),
                ],
                capture_output=True,
                timeout=30,
            )

            if result.returncode != 0:
                logger.warning(
                    ".doc -> .docx 변환 실패 (returncode=%d): %s",
                    result.returncode,
                    result.stderr.decode(errors="replace")[:200],
                )
                return None

            docx_path = Path(tmpdir) / "input.docx"
            if docx_path.exists():
                return docx_path.read_bytes()
            else:
                logger.warning(".doc -> .docx 변환 결과 파일을 찾을 수 없습니다.")
                return None

    except FileNotFoundError:
        logger.warning("libreoffice가 설치되어 있지 않아 .doc 변환을 수행할 수 없습니다.")
        return None
    except subprocess.TimeoutExpired:
        logger.warning(".doc -> .docx 변환 타임아웃 (30초)")
        return None
    except Exception as e:
        logger.warning(".doc -> .docx 변환 중 오류: %s", e)
        return None


