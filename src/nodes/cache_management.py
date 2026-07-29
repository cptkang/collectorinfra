"""캐시 관리 전용 노드.

시멘틱 라우터에서 캐시 관리 의도로 분류된 프롬프트를 처리한다.
사용자의 자연어 요청을 분석하여 캐시 생성/갱신/조회/삭제를 수행한다.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from src.config import AppConfig, load_config
from src.llm import create_llm
from src.prompts.cache_management import CACHE_MANAGEMENT_PARSE_PROMPT
from src.schema_cache.cache_manager import get_cache_manager
from src.state import AgentState
from src.utils.json_extract import extract_json_from_response

logger = logging.getLogger(__name__)


async def cache_management(
    state: AgentState,
    *,
    llm: BaseChatModel | None = None,
    app_config: AppConfig | None = None,
) -> dict:
    """캐시 관리 요청을 처리한다.

    사용자의 자연어 프롬프트에서 action, db_id, target을 LLM으로 추출하고,
    SchemaCacheManager를 통해 작업을 수행한다.

    Args:
        state: 현재 에이전트 상태
        llm: LLM 인스턴스
        app_config: 앱 설정

    Returns:
        업데이트할 State 필드
    """
    if app_config is None:
        app_config = load_config()
    if llm is None:
        llm = create_llm(app_config)

    user_query = state["user_query"]
    cache_mgr = get_cache_manager(app_config)

    # pending_synonym_reuse가 있고 사용자가 재활용 응답을 한 경우
    pending_reuse = state.get("pending_synonym_reuse")

    try:
        # LLM으로 의도 파싱
        parsed = await _parse_cache_intent(llm, user_query)
        action = parsed.get("action", "status")
        db_id = parsed.get("db_id")

        # 멀티턴: db_id가 없으면 이전 턴의 db_id를 자동 추론
        if not db_id:
            context = state.get("conversation_context")
            if context and context.get("previous_db_id"):
                db_id = context["previous_db_id"]
                logger.info(
                    "cache_management: db_id 자동 추론 (previous_db_id=%s)", db_id
                )
        target_table = parsed.get("target_table")
        target_column = parsed.get("target_column")
        words = parsed.get("words")
        seed_words = parsed.get("seed_words")
        description = parsed.get("description")
        reuse_mode = parsed.get("reuse_mode")

        # pending_synonym_reuse 상태에서 reuse-synonym action인 경우
        if action == "reuse-synonym" and pending_reuse:
            response_text = await _handle_reuse_synonym(
                cache_mgr=cache_mgr,
                llm=llm,
                pending_reuse=pending_reuse,
                reuse_mode=reuse_mode or "reuse",
            )
            return {
                "final_response": response_text,
                "current_node": "cache_management",
                "error_message": None,
                "pending_synonym_reuse": None,  # 대기 상태 해제
            }

        # 작업 수행
        result = await _execute_cache_action(
            action=action,
            db_id=db_id,
            target_table=target_table,
            target_column=target_column,
            words=words,
            seed_words=seed_words,
            description=description,
            cache_mgr=cache_mgr,
            app_config=app_config,
            llm=llm,
        )

        # result가 dict면 pending_synonym_reuse 포함 가능
        if isinstance(result, dict):
            return {
                "final_response": result.get("response_text", ""),
                "current_node": "cache_management",
                "error_message": None,
                "pending_synonym_reuse": result.get("pending_synonym_reuse"),
            }

        return {
            "final_response": result,
            "current_node": "cache_management",
            "error_message": None,
        }

    except Exception as e:
        logger.error("캐시 관리 실패: %s", e)
        return {
            "final_response": f"캐시 관리 중 오류가 발생했습니다: {str(e)}",
            "current_node": "cache_management",
            "error_message": str(e),
        }


async def _parse_cache_intent(
    llm: BaseChatModel,
    user_query: str,
) -> dict:
    """LLM으로 캐시 관리 의도를 파싱한다.

    Args:
        llm: LLM 인스턴스
        user_query: 사용자 질의

    Returns:
        파싱된 의도 딕셔너리
    """
    prompt = CACHE_MANAGEMENT_PARSE_PROMPT.format(user_query=user_query)
    response = await llm.ainvoke([HumanMessage(content=prompt)])

    parsed = extract_json_from_response(response.content)
    if isinstance(parsed, dict):
        return parsed

    # 파싱 실패 시 기본값
    return {"action": "status", "db_id": None}


async def _execute_cache_action(
    action: str,
    db_id: Optional[str],
    target_table: Optional[str],
    target_column: Optional[str],
    words: Optional[list[str]],
    seed_words: Optional[list[str]],
    description: Optional[str],
    cache_mgr: Any,
    app_config: AppConfig,
    llm: BaseChatModel,
) -> str | dict:
    """캐시 관리 작업을 수행하고 응답 텍스트를 생성한다.

    Args:
        action: 수행할 작업
        db_id: 대상 DB 식별자
        target_table: 대상 테이블 (선택)
        target_column: 대상 컬럼 (선택)
        words: 유사 단어 목록 (add/remove/update 시)
        seed_words: 사용자 제공 예시 (generate-global-synonyms 시)
        description: 컬럼 설명 텍스트 (update-description 시)
        cache_mgr: SchemaCacheManager
        app_config: 앱 설정
        llm: LLM 인스턴스

    Returns:
        사용자에게 보여줄 응답 텍스트 또는 dict (pending_synonym_reuse 포함 시)
    """
    if action == "status":
        return await _handle_status(cache_mgr, db_id)
    elif action == "generate":
        return await _handle_generate(cache_mgr, app_config, db_id)
    elif action == "generate-descriptions":
        return await _handle_generate_descriptions(
            cache_mgr, app_config, llm, db_id
        )
    elif action == "generate-synonyms":
        return await _handle_generate_synonyms(
            cache_mgr, app_config, llm, db_id, target_table
        )
    elif action == "generate-global-synonyms":
        return await _handle_generate_global_synonyms(
            cache_mgr, llm, target_column, seed_words, db_id
        )
    elif action == "generate-db-description":
        return await _handle_generate_db_description(
            cache_mgr, llm, db_id
        )
    elif action == "set-db-description":
        description_text = target_column or ""
        return await _handle_set_db_description(
            cache_mgr, db_id, description_text
        )
    elif action == "update-description":
        return await _handle_update_description(
            cache_mgr, target_column, description
        )
    elif action == "db-guide":
        return await _handle_db_guide(cache_mgr)
    elif action == "invalidate":
        return await _handle_invalidate(cache_mgr, db_id)
    elif action == "list-synonyms":
        return await _handle_list_synonyms(
            cache_mgr, app_config, db_id, target_column
        )
    elif action == "add-synonym":
        return await _handle_add_synonym(
            cache_mgr, app_config, db_id, target_column, words
        )
    elif action == "remove-synonym":
        return await _handle_remove_synonym(
            cache_mgr, app_config, db_id, target_column, words
        )
    elif action == "update-synonym":
        return await _handle_update_synonym(
            cache_mgr, app_config, db_id, target_column, words
        )
    else:
        return f"알 수 없는 캐시 관리 작업입니다: {action}"


async def _handle_status(cache_mgr: Any, db_id: Optional[str]) -> str:
    """캐시 상태 조회를 처리한다."""
    if db_id:
        status = await cache_mgr.get_status(db_id)
        if status.backend == "none":
            return f"{db_id} DB의 캐시가 존재하지 않습니다."
        return (
            f"{db_id} DB 캐시 상태:\n"
            f"- 백엔드: {status.backend}\n"
            f"- 테이블: {status.table_count}개\n"
            f"- fingerprint: {status.fingerprint[:16]}...\n"
            f"- 설명 상태: {status.description_status}\n"
            f"- 설명 수: {status.description_count}개\n"
            f"- 유사 단어: {status.synonym_count}개 컬럼\n"
            f"- 캐시 시각: {status.cached_at}"
        )

    statuses = await cache_mgr.get_all_status()
    if not statuses:
        return "캐시된 DB가 없습니다."

    lines = ["전체 DB 캐시 상태:\n"]
    for s in statuses:
        lines.append(
            f"- {s.db_id}: {s.backend}, "
            f"테이블 {s.table_count}개, "
            f"설명 {s.description_status}"
        )
    lines.append(f"\nRedis 연결: {'활성' if cache_mgr.redis_available else '비활성'}")
    return "\n".join(lines)


async def _handle_generate(
    cache_mgr: Any,
    app_config: AppConfig,
    db_id: Optional[str],
) -> str:
    """캐시 생성/갱신을 처리한다."""
    from src.db import get_db_client

    db_ids = [db_id] if db_id else app_config.multi_db.get_active_db_ids()
    if not db_ids:
        db_ids = ["_default"]

    results = []
    for did in db_ids:
        try:
            async with get_db_client(app_config, db_id=did) as client:
                result = await cache_mgr.refresh_cache(did, client)
                results.append(
                    f"- {did}: {result.status} "
                    f"(테이블 {result.table_count}개, "
                    f"fingerprint: {result.fingerprint[:16]}...)"
                )
        except Exception as e:
            results.append(f"- {did}: 오류 ({e})")

    return "캐시 생성/갱신 결과:\n" + "\n".join(results)


async def _handle_generate_descriptions(
    cache_mgr: Any,
    app_config: AppConfig,
    llm: BaseChatModel,
    db_id: Optional[str],
) -> str:
    """컬럼 설명 생성을 처리한다."""
    from src.schema_cache.description_generator import DescriptionGenerator

    db_ids = [db_id] if db_id else app_config.multi_db.get_active_db_ids()
    generator = DescriptionGenerator(llm)
    results = []

    for did in db_ids:
        schema_dict = await cache_mgr.get_schema(did)
        if schema_dict is None:
            results.append(f"- {did}: 캐시 없음")
            continue

        descriptions, synonyms = await generator.generate_for_db(schema_dict)
        await cache_mgr.save_descriptions(did, descriptions)
        await cache_mgr.save_synonyms(did, synonyms)

        # DB별 유사 단어를 글로벌 사전에 동기화
        merged = await cache_mgr.sync_global_synonyms(did)
        results.append(
            f"- {did}: 설명 {len(descriptions)}개, "
            f"유사 단어 {len(synonyms)}개 생성"
            + (f", 글로벌 사전 {merged}개 컬럼 동기화" if merged else "")
        )

    return "컬럼 설명 생성 결과:\n" + "\n".join(results)


async def _handle_generate_synonyms(
    cache_mgr: Any,
    app_config: AppConfig,
    llm: BaseChatModel,
    db_id: Optional[str],
    target_table: Optional[str],
) -> str:
    """유사 단어 생성을 처리한다."""
    from src.schema_cache.description_generator import DescriptionGenerator

    if not db_id:
        return "유사 단어 생성에는 대상 DB를 지정해야 합니다."

    schema_dict = await cache_mgr.get_schema(db_id)
    if schema_dict is None:
        return f"{db_id} DB의 캐시가 존재하지 않습니다. 먼저 캐시를 생성하세요."

    generator = DescriptionGenerator(llm)

    # 특정 테이블만 필터링
    if target_table:
        tables = schema_dict.get("tables", {})
        if target_table not in tables:
            return f"테이블 '{target_table}'을 찾을 수 없습니다."
        filtered_schema = {
            "tables": {target_table: tables[target_table]},
        }
    else:
        filtered_schema = schema_dict

    descriptions, synonyms = await generator.generate_for_db(filtered_schema)
    await cache_mgr.save_descriptions(db_id, descriptions)
    await cache_mgr.save_synonyms(db_id, synonyms)

    # DB별 유사 단어를 글로벌 사전에 동기화
    merged = await cache_mgr.sync_global_synonyms(db_id)

    # 결과 포맷
    lines = [f"{db_id} DB의 유사 단어를 생성했습니다.\n"]
    for col, words in sorted(synonyms.items()):
        lines.append(f"- {col}: {', '.join(words)}")
    if merged:
        lines.append(f"\n글로벌 사전에 {merged}개 컬럼 동기화 완료")

    return "\n".join(lines)


async def _handle_generate_global_synonyms(
    cache_mgr: Any,
    llm: BaseChatModel,
    target_column: Optional[str],
    seed_words: Optional[list[str]],
    db_id: Optional[str],
) -> str | dict:
    """글로벌 유사 단어 LLM 생성을 처리한다.

    글로벌 사전에 정확히 일치하는 컬럼명이 없는 경우,
    유사 필드 자동 탐색 및 재활용 제안을 수행한다 (Smart Synonym Reuse).
    """
    if not target_column:
        return "유사 단어를 생성할 대상 컬럼을 지정해야 합니다."

    bare_col = target_column.split(".")[-1] if "." in target_column else target_column

    # 1. 글로벌 사전에서 정확 매칭 확인
    global_syns = await cache_mgr.get_global_synonyms()

    if bare_col not in global_syns:
        # 2. LLM으로 기존 글로벌 컬럼 중 유사 필드 탐색 (Smart Synonym Reuse)
        similar = await cache_mgr.find_similar_global_columns(bare_col, llm)

        if similar:
            # 유사 필드 발견 -> 사용자에게 재활용 제안
            lines = [f"{bare_col} 컬럼이 글로벌 사전에 없습니다."]
            lines.append("기존 유사 컬럼을 발견했습니다:\n")
            for s in similar:
                desc = s.get("description", "")
                words = s.get("words", [])
                lines.append(f"  - {s['column']}: {desc}")
                if words:
                    lines.append(f"    유사 단어: {', '.join(words[:8])}")
            lines.append("")
            lines.append("기존 유사 단어를 재활용하시겠습니까?")
            lines.append('- 재활용: "재활용" 또는 "hostname 유사 단어 재활용"')
            lines.append('- 새로 생성: "새로 생성"')
            lines.append('- 병합 (기존 + 신규): "병합"')

            pending = {
                "target_column": bare_col,
                "target_db_id": db_id,
                "suggestions": similar,
            }
            return {
                "response_text": "\n".join(lines),
                "pending_synonym_reuse": pending,
            }

    # 3. 정확 매칭이 있거나 유사 필드가 없으면 LLM으로 생성
    result = await cache_mgr.generate_global_synonyms(
        bare_col, llm, seed_words=seed_words
    )

    words = result.get("words", [])
    desc = result.get("description", "")

    lines = [f"{bare_col} 필드의 글로벌 유사 단어를 생성했습니다."]
    if desc:
        lines.append(f"  설명: {desc}")
    lines.append(f"  유사 단어: {', '.join(words)}")
    lines.append(f"  ({len(words)}개 등록, source: llm)")

    return "\n".join(lines)


async def _handle_reuse_synonym(
    cache_mgr: Any,
    llm: BaseChatModel,
    pending_reuse: dict,
    reuse_mode: str,
) -> str:
    """유사 필드 재활용 응답을 처리한다.

    Args:
        cache_mgr: SchemaCacheManager
        llm: LLM 인스턴스
        pending_reuse: 재활용 대기 상태
        reuse_mode: "reuse" | "new" | "merge"

    Returns:
        응답 텍스트
    """
    target_column = pending_reuse.get("target_column", "")
    suggestions = pending_reuse.get("suggestions", [])

    if not suggestions:
        return "재활용할 유사 컬럼 정보가 없습니다."

    source_column = suggestions[0].get("column", "")

    if reuse_mode == "reuse":
        result = await cache_mgr.reuse_synonyms(
            source_column, target_column, mode="copy"
        )
        words = result.get("words", [])
        desc = result.get("description", "")
        lines = [
            f"{source_column}의 유사 단어를 {target_column}에 재활용했습니다."
        ]
        if desc:
            lines.append(f"  설명: {desc}")
        lines.append(f"  유사 단어: {', '.join(words)}")
        return "\n".join(lines)

    elif reuse_mode == "new":
        result = await cache_mgr.generate_global_synonyms(
            target_column, llm
        )
        words = result.get("words", [])
        desc = result.get("description", "")
        lines = [f"{target_column} 필드의 유사 단어를 새로 생성했습니다."]
        if desc:
            lines.append(f"  설명: {desc}")
        lines.append(f"  유사 단어: {', '.join(words)}")
        return "\n".join(lines)

    elif reuse_mode == "merge":
        result = await cache_mgr.reuse_synonyms(
            source_column, target_column, mode="merge", llm=llm
        )
        words = result.get("words", [])
        desc = result.get("description", "")
        lines = [
            f"{source_column}의 유사 단어와 새로 생성한 유사 단어를 "
            f"{target_column}에 병합했습니다."
        ]
        if desc:
            lines.append(f"  설명: {desc}")
        lines.append(f"  유사 단어: {', '.join(words)}")
        return "\n".join(lines)

    else:
        return f"알 수 없는 재활용 모드: {reuse_mode}"


async def _handle_update_description(
    cache_mgr: Any,
    target_column: Optional[str],
    description: Optional[str],
) -> str:
    """글로벌 컬럼 설명을 수정한다."""
    if not target_column:
        return "설명을 수정할 대상 컬럼을 지정해야 합니다."
    if not description:
        return "새 설명 텍스트를 입력해야 합니다."

    bare_name = (
        target_column.split(".", 1)[-1]
        if "." in target_column
        else target_column
    )

    # 이전 설명 조회
    old_desc = await cache_mgr.get_global_description(bare_name)

    success = await cache_mgr.update_global_description(bare_name, description)
    if success:
        lines = [f"{bare_name} 컬럼의 설명을 업데이트했습니다."]
        if old_desc:
            lines.append(f"  - 이전: {old_desc}")
        lines.append(f"  - 변경: {description}")
        return "\n".join(lines)
    return f"{bare_name} 컬럼 설명 업데이트에 실패했습니다."


async def _handle_generate_db_description(
    cache_mgr: Any,
    llm: BaseChatModel,
    db_id: Optional[str],
) -> str:
    """DB 설명을 LLM으로 자동 생성한다."""
    from src.schema_cache.description_generator import DescriptionGenerator

    generator = DescriptionGenerator(llm)

    if db_id:
        db_ids = [db_id]
    else:
        statuses = await cache_mgr.get_all_status()
        db_ids = [s.db_id for s in statuses if s.backend != "none"]

    if not db_ids:
        return "캐시된 DB가 없습니다. 먼저 캐시를 생성하세요."

    results = []
    for did in db_ids:
        schema_dict = await cache_mgr.get_schema(did)
        if schema_dict is None:
            results.append(f"- {did}: 캐시 없음")
            continue

        description = await generator.generate_db_description(did, schema_dict)
        if description:
            await cache_mgr.save_db_description(did, description)
            results.append(f"- {did}: {description}")
        else:
            results.append(f"- {did}: 설명 생성 실패")

    return "DB 설명 생성 결과:\n" + "\n".join(results)


async def _handle_set_db_description(
    cache_mgr: Any,
    db_id: Optional[str],
    description: str,
) -> str:
    """DB 설명을 수동으로 설정한다."""
    if not db_id:
        return "DB 식별자를 지정해야 합니다."
    if not description:
        return "설명 텍스트를 입력해야 합니다."

    success = await cache_mgr.save_db_description(db_id, description)
    if success:
        return f"{db_id} DB 설명을 설정했습니다: {description}"
    return f"{db_id} DB 설명 설정에 실패했습니다."


async def _handle_db_guide(cache_mgr: Any) -> str:
    """DB 목록과 설명을 안내한다."""
    db_descriptions = await cache_mgr.get_db_descriptions()

    if not db_descriptions:
        # DB 설명이 없으면 캐시 상태에서 DB 목록만 반환
        statuses = await cache_mgr.get_all_status()
        if not statuses:
            return "현재 등록된 DB가 없습니다."
        lines = ["사용 가능한 DB 목록:\n"]
        for s in statuses:
            lines.append(f"- {s.db_id} (테이블 {s.table_count}개)")
        lines.append("\nDB 설명이 아직 생성되지 않았습니다. 'DB 설명을 생성해줘'로 생성할 수 있습니다.")
        return "\n".join(lines)

    lines = ["사용 가능한 DB 목록:\n"]
    for db_id, desc in sorted(db_descriptions.items()):
        lines.append(f"- **{db_id}**: {desc}")

    return "\n".join(lines)


async def _handle_invalidate(
    cache_mgr: Any,
    db_id: Optional[str],
) -> str:
    """캐시 삭제를 처리한다 (글로벌 사전만 보존)."""
    if db_id:
        success = await cache_mgr.invalidate(db_id)
        return (
            f"{db_id} 캐시 삭제 {'성공' if success else '실패'} "
            f"(글로벌 사전만 보존됩니다)"
        )
    else:
        count = await cache_mgr.invalidate_all()
        return f"전체 캐시 {count}개 삭제 완료 (글로벌 사전만 보존됩니다)"


async def _handle_list_synonyms(
    cache_mgr: Any,
    app_config: AppConfig,
    db_id: Optional[str],
    target_column: Optional[str],
) -> str:
    """유사 단어 목록 조회를 처리한다. description도 함께 표시."""
    lines: list[str] = []
    redis_warning = (
        "\n\n⚠️ Redis에 연결할 수 없어 로컬 파일 캐시를 사용합니다. "
        "LLM으로 생성한 유사 단어는 표시되지 않을 수 있습니다."
        if not cache_mgr.redis_available
        else ""
    )

    if target_column:
        # 특정 컬럼의 유사 단어 + description 조회
        bare_name = target_column.split(".", 1)[-1] if "." in target_column else target_column
        bare_name_lower = bare_name.lower()

        # 글로벌 사전 조회 (full - description 포함)
        global_full = await cache_mgr.get_global_synonyms_full()

        # 1. 정확 매칭 (대소문자 무관)
        matched_key = None
        for key in global_full:
            if key.lower() == bare_name_lower:
                matched_key = key
                break

        # 2. 정확 매칭 실패 시 역방향 탐색: bare_name이 다른 컬럼의 유사어 목록에 있는지 확인
        if matched_key is None:
            for key, entry in global_full.items():
                words_lower = [w.lower() for w in entry.get("words", [])]
                if bare_name_lower in words_lower:
                    matched_key = key
                    lines.append(f"[안내] '{bare_name}'은 '{key}' 컬럼의 유사 단어입니다.")
                    break

        global_entry = global_full.get(matched_key, {}) if matched_key else {}
        global_words = global_entry.get("words", [])
        global_desc = global_entry.get("description", "")
        display_name = matched_key or bare_name

        if global_desc:
            lines.append(f"[설명] {global_desc}")
        if global_words:
            lines.append(f"[글로벌 유사 단어] {', '.join(global_words)}")

        # 활성 DB별 조회 (대소문자 무관 비교)
        active_db_ids = app_config.multi_db.get_active_db_ids()
        for did in active_db_ids:
            db_syns = await cache_mgr.get_synonyms(did)
            for col_key, words in db_syns.items():
                col_bare = col_key.split(".", 1)[-1] if "." in col_key else col_key
                if col_bare.lower() == bare_name_lower or col_key == target_column:
                    lines.append(f"[{did}] {col_key}: {', '.join(words)}")

        if not lines:
            return f"'{target_column}' 컬럼의 유사 단어가 없습니다.{redis_warning}"
        return f"{display_name} 컬럼 정보:\n" + "\n".join(lines) + redis_warning

    elif db_id:
        # 특정 DB의 유사 단어 조회
        db_syns = await cache_mgr.get_synonyms(db_id)
        if not db_syns:
            return f"{db_id} DB의 유사 단어가 없습니다.{redis_warning}"
        lines.append(f"{db_id} DB의 유사 단어 목록:\n")
        for col, words in sorted(db_syns.items()):
            lines.append(f"- {col}: {', '.join(words)}")
        return "\n".join(lines) + redis_warning

    else:
        # 글로벌 유사 단어 전체 조회 (description 포함)
        global_full = await cache_mgr.get_global_synonyms_full()
        if not global_full:
            return f"글로벌 유사 단어 사전이 비어 있습니다.{redis_warning}"
        lines.append("글로벌 유사 단어 사전:\n")
        for col, entry in sorted(global_full.items()):
            words = entry.get("words", [])
            desc = entry.get("description", "")
            line = f"- {col}: {', '.join(words)}"
            if desc:
                line += f" ({desc})"
            lines.append(line)
        return "\n".join(lines) + redis_warning


async def _handle_add_synonym(
    cache_mgr: Any,
    app_config: AppConfig,
    db_id: Optional[str],
    target_column: Optional[str],
    words: Optional[list[str]],
) -> str:
    """유사 단어 추가를 처리한다."""
    if not cache_mgr.redis_available:
        return "Redis에 연결할 수 없어 유사 단어를 추가할 수 없습니다. Redis 상태를 확인해 주세요."
    if not target_column:
        return (
            "유사 단어를 추가할 **컬럼명**을 지정해 주세요.\n\n"
            "**유사 단어란?**\n"
            "사용자가 자연어로 질의할 때 DB 컬럼명 대신 사용할 수 있는 동의어입니다.\n"
            "예를 들어 `hostname` 컬럼에 '서버명', '호스트' 등을 등록하면,\n"
            "\"서버명이 web01인 장비 조회\"처럼 질의할 때 자동으로 `hostname` 컬럼으로 매핑됩니다.\n\n"
            "**요청 형식:**\n"
            "`{컬럼명}에 '{유사단어}' 유사 단어를 추가해줘`\n\n"
            "**예시:**\n"
            "- `hostname에 '서버이름', '호스트' 유사 단어를 추가해줘`\n"
            "- `usage_pct에 '사용률' 유사 단어를 추가해줘`\n"
            "- `avail_status에 '가용성', '상태' 유사 단어를 추가해줘`"
        )
    if not words:
        return (
            f"추가할 **유사 단어**를 지정해 주세요.\n\n"
            f"**요청 형식:**\n"
            f"`{target_column}에 '유사단어1', '유사단어2' 유사 단어를 추가해줘`\n\n"
            f"**예시:**\n"
            f"- `{target_column}에 '서버이름', '호스트명' 유사 단어를 추가해줘`"
        )

    bare_name = target_column.split(".", 1)[-1] if "." in target_column else target_column
    results: list[str] = []

    # 글로벌 사전에 추가
    success = await cache_mgr.add_global_synonym(bare_name, words)
    if success:
        results.append("- 글로벌 사전에 등록 완료")

    # 활성 DB 중 해당 컬럼이 있는 DB의 synonyms에도 동기화
    active_db_ids = [db_id] if db_id else app_config.multi_db.get_active_db_ids()
    for did in active_db_ids:
        schema_dict = await cache_mgr.get_schema(did)
        if schema_dict is None:
            continue
        # 해당 DB에서 target_column 찾기
        for table_name, table_data in schema_dict.get("tables", {}).items():
            for col in table_data.get("columns", []):
                if col["name"] == bare_name:
                    col_key = f"{table_name}.{col['name']}"
                    await cache_mgr.add_synonyms(
                        did, col_key, words, source="operator"
                    )
                    results.append(f"- {did} DB ({col_key})에 동기화 완료")

    words_str = ", ".join(f"'{w}'" for w in words)
    return (
        f"{words_str}을(를) {target_column}의 유사 단어로 추가했습니다.\n"
        + "\n".join(results)
    )


async def _handle_remove_synonym(
    cache_mgr: Any,
    app_config: AppConfig,
    db_id: Optional[str],
    target_column: Optional[str],
    words: Optional[list[str]],
) -> str:
    """유사 단어 삭제를 처리한다."""
    if not cache_mgr.redis_available:
        return "Redis에 연결할 수 없어 유사 단어를 삭제할 수 없습니다. Redis 상태를 확인해 주세요."
    if not target_column:
        return (
            "유사 단어를 삭제할 **컬럼명**을 지정해 주세요.\n\n"
            "**요청 형식:**\n"
            "`{컬럼명}에서 '{유사단어}' 유사 단어를 삭제해줘`\n\n"
            "**예시:**\n"
            "- `hostname에서 '서버이름' 유사 단어를 삭제해줘`\n"
            "- `usage_pct에서 '사용률', '사용비율' 유사 단어를 삭제해줘`\n\n"
            "현재 등록된 유사 단어를 확인하려면: `hostname의 유사 단어를 보여줘`"
        )
    if not words:
        return (
            f"삭제할 **유사 단어**를 지정해 주세요.\n\n"
            f"**요청 형식:**\n"
            f"`{target_column}에서 '유사단어1', '유사단어2' 유사 단어를 삭제해줘`\n\n"
            f"현재 등록된 유사 단어 확인: `{target_column}의 유사 단어를 보여줘`"
        )

    bare_name = target_column.split(".", 1)[-1] if "." in target_column else target_column
    results: list[str] = []

    # 글로벌 사전에서 삭제
    success = await cache_mgr.remove_global_synonym(bare_name, words)
    if success:
        results.append("- 글로벌 사전에서 삭제 완료")

    # 활성 DB에서도 삭제
    active_db_ids = [db_id] if db_id else app_config.multi_db.get_active_db_ids()
    for did in active_db_ids:
        db_syns = await cache_mgr.get_synonyms(did)
        for col_key in db_syns:
            col_bare = col_key.split(".", 1)[-1] if "." in col_key else col_key
            if col_bare == bare_name:
                await cache_mgr.remove_synonyms(did, col_key, words)
                results.append(f"- {did} DB ({col_key})에서 삭제 완료")

    words_str = ", ".join(f"'{w}'" for w in words)
    return (
        f"{words_str}을(를) {target_column}의 유사 단어에서 삭제했습니다.\n"
        + "\n".join(results)
    )


async def _handle_update_synonym(
    cache_mgr: Any,
    app_config: AppConfig,
    db_id: Optional[str],
    target_column: Optional[str],
    words: Optional[list[str]],
) -> str:
    """유사 단어 교체를 처리한다 (기존 전체 삭제 후 새로 설정)."""
    if not cache_mgr.redis_available:
        return "Redis에 연결할 수 없어 유사 단어를 변경할 수 없습니다. Redis 상태를 확인해 주세요."
    if not target_column:
        return (
            "유사 단어를 교체할 **컬럼명**을 지정해 주세요.\n\n"
            "**교체란?** 기존에 등록된 유사 단어를 모두 지우고 새 목록으로 덮어씁니다.\n"
            "일부만 바꾸려면 추가/삭제를 각각 사용하세요.\n\n"
            "**요청 형식:**\n"
            "`{컬럼명}의 유사 단어를 '{단어1}', '{단어2}'로 변경해줘`\n\n"
            "**예시:**\n"
            "- `hostname의 유사 단어를 '서버명', '호스트명', '장비명'으로 변경해줘`\n"
            "- `usage_pct의 유사 단어를 '사용률', 'CPU 사용률'로 변경해줘`"
        )
    if not words:
        return (
            f"새로 설정할 **유사 단어 목록**을 지정해 주세요.\n\n"
            f"**요청 형식:**\n"
            f"`{target_column}의 유사 단어를 '단어1', '단어2'로 변경해줘`\n\n"
            f"현재 등록된 유사 단어 확인: `{target_column}의 유사 단어를 보여줘`"
        )

    bare_name = target_column.split(".", 1)[-1] if "." in target_column else target_column

    # 글로벌 사전 교체 (description 보존)
    # 1. 기존 description 보존
    old_desc = await cache_mgr.get_global_description(bare_name)
    # 2. 새 단어로 교체 저장
    entry: dict = {"words": words}
    if old_desc:
        entry["description"] = old_desc
    await cache_mgr.save_global_synonyms({bare_name: entry})

    # 활성 DB에서도 교체
    active_db_ids = [db_id] if db_id else app_config.multi_db.get_active_db_ids()
    for did in active_db_ids:
        db_syns = await cache_mgr.get_synonyms(did)
        for col_key in db_syns:
            col_bare = col_key.split(".", 1)[-1] if "." in col_key else col_key
            if col_bare == bare_name:
                tagged = {
                    "words": words,
                    "sources": {w: "operator" for w in words},
                }
                await cache_mgr.save_synonyms(did, {col_key: tagged})

    words_str = ", ".join(words)
    return f"{target_column}의 유사 단어를 [{words_str}](으)로 교체했습니다."
