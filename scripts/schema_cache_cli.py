#!/usr/bin/env python3
"""독립 실행 스키마 캐시 관리 CLI.

FastAPI 서버 없이 직접 캐시를 관리할 수 있는 CLI 스크립트.

사용법:
    python scripts/schema_cache_cli.py generate [--db-id DB_ID] [--force]
    python scripts/schema_cache_cli.py generate-descriptions [--db-id DB_ID]
    python scripts/schema_cache_cli.py status
    python scripts/schema_cache_cli.py show --db-id DB_ID
    python scripts/schema_cache_cli.py invalidate [--db-id DB_ID | --all]
    python scripts/schema_cache_cli.py db-description [--db-id DB_ID]
    python scripts/schema_cache_cli.py db-description --generate [--db-id DB_ID]
    python scripts/schema_cache_cli.py db-description --db-id DB_ID --set "설명 텍스트"
    python scripts/schema_cache_cli.py db-description --db-id DB_ID --delete
    python scripts/schema_cache_cli.py synonyms --db-id DB_ID [--all | --column COL]
    python scripts/schema_cache_cli.py synonyms --db-id DB_ID --generate [--column COL]
    python scripts/schema_cache_cli.py synonyms --db-id DB_ID --column COL --remove WORD
    python scripts/schema_cache_cli.py load-synonyms [--file FILE] [--no-merge]
    python scripts/schema_cache_cli.py export-synonyms --output FILE [--format yaml|json]
    python scripts/schema_cache_cli.py synonym-status
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# 프로젝트 루트를 path에 추가
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))


async def cmd_generate(args: argparse.Namespace) -> None:
    """캐시 생성/갱신 커맨드."""
    from src.config import load_config
    from src.db import get_db_client
    from src.schema_cache.cache_manager import get_cache_manager

    config = load_config()
    cache_mgr = get_cache_manager(config)

    db_ids = [args.db_id] if args.db_id else config.multi_db.get_active_db_ids()
    if not db_ids:
        db_ids = ["_default"]

    for db_id in db_ids:
        print(f"\n[{db_id}] 캐시 생성/갱신 시작...")
        try:
            async with get_db_client(config, db_id=db_id) as client:
                result = await cache_mgr.refresh_cache(
                    db_id, client, force=args.force
                )
                print(f"  상태: {result.status}")
                print(f"  테이블 수: {result.table_count}")
                print(f"  fingerprint: {result.fingerprint}")
                print(f"  메시지: {result.message}")
        except Exception as e:
            print(f"  오류: {e}")

    await cache_mgr.disconnect()


async def cmd_generate_descriptions(args: argparse.Namespace) -> None:
    """컬럼 설명 생성 커맨드."""
    from src.config import load_config
    from src.llm import create_llm
    from src.schema_cache.cache_manager import get_cache_manager
    from src.schema_cache.description_generator import DescriptionGenerator

    config = load_config()
    cache_mgr = get_cache_manager(config)

    db_ids = [args.db_id] if args.db_id else config.multi_db.get_active_db_ids()
    if not db_ids:
        db_ids = ["_default"]

    llm = create_llm(config)
    generator = DescriptionGenerator(llm)

    for db_id in db_ids:
        print(f"\n[{db_id}] 컬럼 설명 생성 시작...")
        schema_dict = await cache_mgr.get_schema(db_id)
        if schema_dict is None:
            print(f"  캐시가 없습니다. 먼저 generate 명령을 실행하세요.")
            continue

        descriptions, synonyms = await generator.generate_for_db(schema_dict)
        await cache_mgr.save_descriptions(db_id, descriptions)
        await cache_mgr.save_synonyms(db_id, synonyms)
        print(f"  설명: {len(descriptions)}개 컬럼")
        print(f"  유사 단어: {len(synonyms)}개 컬럼")

    await cache_mgr.disconnect()


async def cmd_status(args: argparse.Namespace) -> None:
    """캐시 상태 조회 커맨드."""
    from src.config import load_config
    from src.schema_cache.cache_manager import get_cache_manager

    config = load_config()
    cache_mgr = get_cache_manager(config)

    statuses = await cache_mgr.get_all_status()
    if not statuses:
        print("캐시된 DB가 없습니다.")
        await cache_mgr.disconnect()
        return

    print(f"\n{'DB ID':<20} {'Backend':<10} {'Tables':<8} {'Desc':<10} {'Fingerprint':<20} {'Cached At'}")
    print("-" * 90)
    for s in statuses:
        print(
            f"{s.db_id:<20} {s.backend:<10} {s.table_count:<8} "
            f"{s.description_status:<10} {s.fingerprint[:16]+'...' if s.fingerprint else 'N/A':<20} "
            f"{s.cached_at or 'N/A'}"
        )

    print(f"\nRedis 연결: {'활성' if cache_mgr.redis_available else '비활성'}")
    await cache_mgr.disconnect()


async def cmd_show(args: argparse.Namespace) -> None:
    """캐시 상세 조회 커맨드."""
    from src.config import load_config
    from src.schema_cache.cache_manager import get_cache_manager

    config = load_config()
    cache_mgr = get_cache_manager(config)

    if not args.db_id:
        print("--db-id 옵션이 필요합니다.")
        await cache_mgr.disconnect()
        return

    schema_dict = await cache_mgr.get_schema(args.db_id)
    if schema_dict is None:
        print(f"캐시가 존재하지 않습니다: {args.db_id}")
        await cache_mgr.disconnect()
        return

    descriptions = await cache_mgr.get_descriptions(args.db_id)
    synonyms = await cache_mgr.get_synonyms(args.db_id)

    tables = schema_dict.get("tables", {})
    for table_name, table_data in tables.items():
        columns = table_data.get("columns", [])
        print(f"\n=== {table_name} ({len(columns)}개 컬럼) ===")
        for col in columns:
            col_key = f"{table_name}.{col['name']}"
            desc = descriptions.get(col_key, "")
            syns = synonyms.get(col_key, [])
            type_str = col.get("type", "unknown")
            print(f"  {col['name']} ({type_str})", end="")
            if desc:
                print(f" -- {desc}", end="")
            if syns:
                print(f" [유사: {', '.join(syns)}]", end="")
            print()

    await cache_mgr.disconnect()


async def cmd_invalidate(args: argparse.Namespace) -> None:
    """캐시 삭제 커맨드."""
    from src.config import load_config
    from src.schema_cache.cache_manager import get_cache_manager

    config = load_config()
    cache_mgr = get_cache_manager(config)

    if args.all:
        count = await cache_mgr.invalidate_all()
        print(f"전체 캐시 {count}개 삭제 완료")
    elif args.db_id:
        success = await cache_mgr.invalidate(args.db_id)
        print(f"캐시 삭제 {'성공' if success else '실패'}: {args.db_id}")
    else:
        print("--db-id 또는 --all 옵션이 필요합니다.")

    await cache_mgr.disconnect()


async def cmd_db_description(args: argparse.Namespace) -> None:
    """DB 설명 관리 커맨드."""
    from src.config import load_config
    from src.schema_cache.cache_manager import get_cache_manager

    config = load_config()
    cache_mgr = get_cache_manager(config)

    if args.generate:
        # LLM으로 DB 설명 생성
        from src.llm import create_llm
        from src.schema_cache.description_generator import DescriptionGenerator

        llm = create_llm(config)
        generator = DescriptionGenerator(llm)

        if args.db_id:
            db_ids = [args.db_id]
        else:
            statuses = await cache_mgr.get_all_status()
            db_ids = [s.db_id for s in statuses if s.backend != "none"]

        if not db_ids:
            print("캐시된 DB가 없습니다.")
            await cache_mgr.disconnect()
            return

        for db_id in db_ids:
            print(f"\n[{db_id}] DB 설명 생성 중...")
            schema_dict = await cache_mgr.get_schema(db_id)
            if schema_dict is None:
                print(f"  캐시 없음. 먼저 generate 명령을 실행하세요.")
                continue

            description = await generator.generate_db_description(db_id, schema_dict)
            if description:
                await cache_mgr.save_db_description(db_id, description)
                print(f"  설명: {description}")
            else:
                print(f"  생성 실패")

    elif args.set:
        # 수동 설정
        if not args.db_id:
            print("--db-id 옵션이 필요합니다.")
            await cache_mgr.disconnect()
            return
        success = await cache_mgr.save_db_description(args.db_id, args.set)
        if success:
            print(f"DB 설명 설정 완료: {args.db_id} -> {args.set}")
        else:
            print("DB 설명 설정 실패")

    elif args.delete:
        # 삭제
        if not args.db_id:
            print("--db-id 옵션이 필요합니다.")
            await cache_mgr.disconnect()
            return
        success = await cache_mgr.delete_db_description(args.db_id)
        print(f"DB 설명 삭제 {'성공' if success else '실패'}: {args.db_id}")

    else:
        # 조회
        if args.db_id:
            desc = await cache_mgr.get_db_description(args.db_id)
            if desc:
                print(f"{args.db_id}: {desc}")
            else:
                print(f"{args.db_id}: (설명 없음)")
        else:
            descriptions = await cache_mgr.get_db_descriptions()
            if not descriptions:
                print("DB 설명이 없습니다.")
            else:
                print("\nDB 설명 목록:")
                for db_id, desc in sorted(descriptions.items()):
                    print(f"  {db_id}: {desc}")

    await cache_mgr.disconnect()


async def cmd_synonyms(args: argparse.Namespace) -> None:
    """유사 단어 관리 커맨드."""
    from src.config import load_config
    from src.schema_cache.cache_manager import get_cache_manager

    config = load_config()
    cache_mgr = get_cache_manager(config)

    if not args.db_id:
        print("--db-id 옵션이 필요합니다.")
        await cache_mgr.disconnect()
        return

    if args.generate:
        # LLM으로 유사 단어 생성
        from src.llm import create_llm
        from src.schema_cache.description_generator import DescriptionGenerator

        schema_dict = await cache_mgr.get_schema(args.db_id)
        if schema_dict is None:
            print(f"캐시가 없습니다: {args.db_id}")
            await cache_mgr.disconnect()
            return

        llm = create_llm(config)
        generator = DescriptionGenerator(llm)
        descriptions, synonyms = await generator.generate_for_db(schema_dict)
        await cache_mgr.save_descriptions(args.db_id, descriptions)
        await cache_mgr.save_synonyms(args.db_id, synonyms)
        print(f"유사 단어 생성 완료: {len(synonyms)}개 컬럼")

    elif args.remove:
        # 특정 유사 단어 삭제
        if not args.column:
            print("--column 옵션이 필요합니다.")
            await cache_mgr.disconnect()
            return
        if (
            cache_mgr.backend == "redis"
            and await cache_mgr.ensure_redis_connected()
        ):
            await cache_mgr._redis_cache.remove_synonyms(
                args.db_id, args.column, [args.remove]
            )
            print(f"유사 단어 삭제: {args.column} -> '{args.remove}'")
        else:
            print("Redis가 연결되지 않아 유사 단어를 삭제할 수 없습니다.")

    else:
        # 조회
        synonyms = await cache_mgr.get_synonyms(args.db_id)
        if args.column:
            words = synonyms.get(args.column, [])
            print(f"{args.column}: {', '.join(words) if words else '(없음)'}")
        else:
            if not synonyms:
                print(f"유사 단어가 없습니다: {args.db_id}")
            else:
                for col, words in sorted(synonyms.items()):
                    print(f"  {col}: {', '.join(words)}")

    await cache_mgr.disconnect()


async def cmd_load_synonyms(args: argparse.Namespace) -> None:
    """글로벌 유사단어 사전 파일 로드 커맨드."""
    from src.config import load_config
    from src.schema_cache.redis_cache import RedisSchemaCache
    from src.schema_cache.synonym_loader import SynonymLoader

    config = load_config()
    redis_cache = RedisSchemaCache(config.redis, config.schema_cache)
    await redis_cache.connect()

    loader = SynonymLoader(redis_cache=redis_cache)
    file_path = args.file  # None이면 load_auto가 기본 경로 사용
    merge = not args.no_merge

    result = await loader.load_auto(file_path=file_path, merge=merge)
    print(f"\n상태: {result.status}")
    print(f"파일: {result.file_path}")
    print(f"컬럼 유사단어: {result.columns_loaded}개")
    print(f"RESOURCE_TYPE 유사단어: {result.resource_types_loaded}종")
    print(f"EAV NAME 유사단어: {result.eav_names_loaded}종")
    print(f"총 유사단어 수: {result.total_words}개")
    print(f"병합 모드: {'예' if result.merge_mode else '아니오 (덮어쓰기)'}")
    if result.errors:
        print(f"오류: {result.errors}")
    print(f"메시지: {result.message}")

    await redis_cache.disconnect()


async def cmd_export_synonyms(args: argparse.Namespace) -> None:
    """글로벌 유사단어를 파일로 내보내기 커맨드."""
    from src.config import load_config
    from src.schema_cache.redis_cache import RedisSchemaCache
    from src.schema_cache.synonym_loader import SynonymLoader

    config = load_config()
    redis_cache = RedisSchemaCache(config.redis, config.schema_cache)
    await redis_cache.connect()

    loader = SynonymLoader(redis_cache=redis_cache)
    output_path = args.output

    # 형식 판별
    fmt = args.format
    if not fmt:
        if output_path.endswith('.json'):
            fmt = 'json'
        else:
            fmt = 'yaml'

    if fmt == 'json':
        success = await loader.export_to_json(output_path)
    else:
        success = await loader.export_to_yaml(output_path)

    if success:
        print(f"내보내기 완료: {output_path} ({fmt})")
    else:
        print(f"내보내기 실패")

    await redis_cache.disconnect()


async def cmd_synonym_status(args: argparse.Namespace) -> None:
    """글로벌 유사단어 상태 조회 커맨드."""
    from src.config import load_config
    from src.schema_cache.redis_cache import RedisSchemaCache

    config = load_config()
    redis_cache = RedisSchemaCache(config.redis, config.schema_cache)
    await redis_cache.connect()

    # 글로벌 유사단어
    global_syns = await redis_cache.load_global_synonyms_full()
    rt_syns = await redis_cache.load_resource_type_synonyms()
    eav_syns = await redis_cache.load_eav_name_synonyms()

    print("\n=== 글로벌 유사단어 사전 상태 ===")
    print(f"컬럼 유사단어 (synonyms:global): {len(global_syns)}개 컬럼")
    if global_syns:
        total_words = sum(len(v.get('words', [])) if isinstance(v, dict) else len(v) for v in global_syns.values())
        print(f"  총 유사단어 수: {total_words}개")
        # 상위 5개 표시
        for i, (col, info) in enumerate(sorted(global_syns.items())):
            if i >= 5:
                print(f"  ... ({len(global_syns) - 5}개 더)")
                break
            words = info.get('words', []) if isinstance(info, dict) else info
            print(f"  {col}: {', '.join(words[:3])}{'...' if len(words) > 3 else ''}")

    print(f"\nRESOURCE_TYPE 유사단어 (synonyms:resource_types): {len(rt_syns)}종")
    if rt_syns:
        for i, (rt, words) in enumerate(sorted(rt_syns.items())):
            if i >= 5:
                print(f"  ... ({len(rt_syns) - 5}개 더)")
                break
            print(f"  {rt}: {', '.join(words[:3])}{'...' if len(words) > 3 else ''}")

    print(f"\nEAV NAME 유사단어 (synonyms:eav_names): {len(eav_syns)}종")
    if eav_syns:
        for i, (name, words) in enumerate(sorted(eav_syns.items())):
            if i >= 5:
                print(f"  ... ({len(eav_syns) - 5}개 더)")
                break
            print(f"  {name}: {', '.join(words[:3])}{'...' if len(words) > 3 else ''}")

    await redis_cache.disconnect()


def main() -> None:
    """CLI 진입점."""
    parser = argparse.ArgumentParser(
        description="스키마 캐시 관리 CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="실행할 명령")

    # generate
    gen_parser = subparsers.add_parser("generate", help="캐시 생성/갱신")
    gen_parser.add_argument("--db-id", help="대상 DB 식별자")
    gen_parser.add_argument("--force", action="store_true", help="강제 갱신")

    # generate-descriptions
    desc_parser = subparsers.add_parser(
        "generate-descriptions", help="컬럼 설명 생성"
    )
    desc_parser.add_argument("--db-id", help="대상 DB 식별자")

    # status
    subparsers.add_parser("status", help="캐시 상태 조회")

    # show
    show_parser = subparsers.add_parser("show", help="캐시 상세 조회")
    show_parser.add_argument("--db-id", required=True, help="대상 DB 식별자")

    # invalidate
    inv_parser = subparsers.add_parser("invalidate", help="캐시 삭제")
    inv_parser.add_argument("--db-id", help="대상 DB 식별자")
    inv_parser.add_argument("--all", action="store_true", help="전체 삭제")

    # db-description
    dbdesc_parser = subparsers.add_parser("db-description", help="DB 설명 관리")
    dbdesc_parser.add_argument("--db-id", help="대상 DB 식별자")
    dbdesc_parser.add_argument("--generate", action="store_true", help="LLM으로 생성")
    dbdesc_parser.add_argument("--set", help="수동 설정할 설명 텍스트")
    dbdesc_parser.add_argument("--delete", action="store_true", help="설명 삭제")

    # synonyms
    syn_parser = subparsers.add_parser("synonyms", help="유사 단어 관리")
    syn_parser.add_argument("--db-id", required=True, help="대상 DB 식별자")
    syn_parser.add_argument("--column", help="컬럼 (table.column)")
    syn_parser.add_argument("--generate", action="store_true", help="LLM으로 생성")
    syn_parser.add_argument("--remove", help="삭제할 유사 단어")

    # load-synonyms
    load_syn_parser = subparsers.add_parser("load-synonyms", help="글로벌 유사단어 사전 파일 로드")
    load_syn_parser.add_argument("--file", help="YAML/JSON 파일 경로 (기본: config/global_synonyms.yaml)")
    load_syn_parser.add_argument("--no-merge", action="store_true", help="기존 데이터 덮어쓰기 (기본: 병합)")

    # export-synonyms
    export_syn_parser = subparsers.add_parser("export-synonyms", help="글로벌 유사단어를 파일로 내보내기")
    export_syn_parser.add_argument("--output", required=True, help="출력 파일 경로")
    export_syn_parser.add_argument("--format", choices=["yaml", "json"], help="출력 형식 (기본: yaml)")

    # synonym-status
    subparsers.add_parser("synonym-status", help="글로벌 유사단어 상태 조회")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    command_map = {
        "generate": cmd_generate,
        "generate-descriptions": cmd_generate_descriptions,
        "status": cmd_status,
        "show": cmd_show,
        "invalidate": cmd_invalidate,
        "db-description": cmd_db_description,
        "synonyms": cmd_synonyms,
        "load-synonyms": cmd_load_synonyms,
        "export-synonyms": cmd_export_synonyms,
        "synonym-status": cmd_synonym_status,
    }

    handler = command_map.get(args.command)
    if handler:
        asyncio.run(handler(args))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
