#!/usr/bin/env python3
"""질의 이력 저장소 초기 적재 CLI (Plan 67 트랙 N / N2, D-133).

검증된 질의-SQL 쌍 두 원천을 `query_history:{db_id}`(Redis)에 적재한다.

  ① 골드셋   `testdata/text2sql_gold/*.yaml` (items[].query / gold_sql / gold_smq)
  ② 프로필   `config/db_profiles/*.yaml` (query_examples[].question / sql)

두 원천 모두 **읽기 전용**으로만 다룬다(원본 무변경). 운영 실행 이력(`verified_runtime`)은
이 스크립트의 적재 대상이 아니다 — 사람 확인 게이트를 통과한 건만 `build_entry(...,
confirmed_by=...)`로 개별 편입한다(자동 편입 금지, 계획서 §3.3-N2).

Redis가 없으면 **명확한 에러와 함께 exit 1**로 끝난다(침묵 실패 금지). 원천 파싱만
확인하려면 `--dry-run`을 쓴다(Redis 불필요).

사용 예:
    python scripts/query_history_seed.py load --dry-run       # 파싱 결과만 출력
    python scripts/query_history_seed.py load                 # 전 DB 적재(병합)
    python scripts/query_history_seed.py load --db polestar_cm_gp --replace
    python scripts/query_history_seed.py stats                # 적재 현황 조회
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

GOLD_DIR = REPO_ROOT / "testdata" / "text2sql_gold"
PROFILE_DIR = REPO_ROOT / "config" / "db_profiles"

#: 원천별 출처 태그(질의 이력 항목의 source 필드).
SOURCE_GOLD = "gold"
SOURCE_PROFILE = "profile_example"


def _load_yaml(path: Path) -> dict:
    """YAML 파일을 읽어 dict로 반환한다(읽기 전용)."""
    import yaml

    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def collect_gold_entries(
    gold_dir: Path | None = None,
    *,
    verified_at: str | None = None,
) -> dict[str, list[dict]]:
    """골드셋 YAML에서 db_id별 이력 항목을 수집한다.

    항목의 `db_id`는 파일명이 아니라 각 item의 선언값을 따른다(파일 접두사와
    db_id가 다르다 — gp.yaml → polestar_cm_gp).

    Args:
        gold_dir: 골드셋 디렉터리(기본 testdata/text2sql_gold)
        verified_at: 항목에 기록할 검증 시각(미지정 시 현재 시각)

    Returns:
        {db_id: [항목, ...]}
    """
    from src.schema_cache.query_history import build_entry

    directory = gold_dir or GOLD_DIR
    result: dict[str, list[dict]] = {}
    for path in sorted(directory.glob("*.yaml")):
        data = _load_yaml(path)
        for item in data.get("items") or []:
            db_id = (item.get("db_id") or "").strip()
            query = (item.get("query") or "").strip()
            sql = (item.get("gold_sql") or "").strip()
            if not db_id or not query or not sql:
                continue
            entry = build_entry(
                query, sql,
                source=SOURCE_GOLD,
                smq=item.get("gold_smq"),
                verified_at=verified_at,
            )
            result.setdefault(db_id, []).append(entry)
    return result


def collect_profile_entries(
    profile_dir: Path | None = None,
    *,
    verified_at: str | None = None,
) -> dict[str, list[dict]]:
    """DB 프로필의 query_examples에서 db_id별 이력 항목을 수집한다.

    프로필의 db_id는 파일명 stem이다(`polestar_cm_gp.yaml` → `polestar_cm_gp`).

    Args:
        profile_dir: 프로필 디렉터리(기본 config/db_profiles)
        verified_at: 항목에 기록할 검증 시각(미지정 시 현재 시각)

    Returns:
        {db_id: [항목, ...]}
    """
    from src.schema_cache.query_history import build_entry

    directory = profile_dir or PROFILE_DIR
    result: dict[str, list[dict]] = {}
    for path in sorted(directory.glob("*.yaml")):
        db_id = path.stem
        data = _load_yaml(path)
        for example in data.get("query_examples") or []:
            query = (example.get("question") or "").strip()
            sql = (example.get("sql") or "").strip()
            if not query or not sql:
                continue
            entry = build_entry(
                query, sql, source=SOURCE_PROFILE, verified_at=verified_at,
            )
            result.setdefault(db_id, []).append(entry)
    return result


def collect_entries(
    *,
    gold_dir: Path | None = None,
    profile_dir: Path | None = None,
    verified_at: str | None = None,
) -> dict[str, list[dict]]:
    """두 원천을 합쳐 db_id별 이력 항목을 수집한다(질의-SQL 쌍 기준 중복 제거).

    동일 쌍이 양쪽 원천에 있으면 먼저 수집된 골드셋 항목을 남긴다(gold_smq 보존).

    Args:
        gold_dir: 골드셋 디렉터리
        profile_dir: 프로필 디렉터리
        verified_at: 항목에 기록할 검증 시각

    Returns:
        {db_id: [항목, ...]}
    """
    from src.schema_cache.query_history import make_entry_id

    merged: dict[str, list[dict]] = {}
    seen: dict[str, set[str]] = {}
    for source_map in (
        collect_gold_entries(gold_dir, verified_at=verified_at),
        collect_profile_entries(profile_dir, verified_at=verified_at),
    ):
        for db_id, entries in source_map.items():
            bucket = merged.setdefault(db_id, [])
            ids = seen.setdefault(db_id, set())
            for entry in entries:
                entry_id = make_entry_id(entry["query"], entry["sql"])
                if entry_id in ids:
                    continue
                ids.add(entry_id)
                bucket.append(entry)
    return merged


def _filter_dbs(collected: dict[str, list[dict]], db: str) -> dict[str, list[dict]]:
    """--db 인자로 대상 DB를 걸러낸다."""
    if db == "all":
        return collected
    return {k: v for k, v in collected.items() if k == db}


async def _open_store() -> Any:
    """이력 저장소를 연결해 반환한다(실패 시 예외 전파)."""
    from src.config import load_config
    from src.schema_cache.query_history import QueryHistoryStore

    config = load_config()
    store = QueryHistoryStore(config.redis)
    await store.connect()
    return store


def _redis_error(exc: Exception) -> int:
    """Redis 미가용을 명확한 사유와 함께 보고한다(침묵 실패 금지)."""
    print(
        "  [error] Redis에 연결할 수 없어 질의 이력을 적재하지 못했습니다.\n"
        f"          사유: {type(exc).__name__}: {exc}\n"
        "          REDIS_HOST/REDIS_PORT/REDIS_PASSWORD(.env)를 확인하거나, "
        "원천 파싱만 확인하려면 --dry-run을 사용하세요.",
        file=sys.stderr,
    )
    return 1


async def cmd_load(db: str, *, replace: bool, dry_run: bool) -> int:
    """원천을 파싱해 Redis에 적재한다."""
    collected = _filter_dbs(collect_entries(), db)
    if not collected:
        print(f"  [skip] 적재할 항목이 없습니다 (db={db})")
        return 0

    total = sum(len(v) for v in collected.values())
    for db_id, entries in sorted(collected.items()):
        by_source: dict[str, int] = {}
        for e in entries:
            by_source[e["source"]] = by_source.get(e["source"], 0) + 1
        detail = ", ".join(f"{k}={v}" for k, v in sorted(by_source.items()))
        print(f"  - {db_id}: {len(entries)}건 ({detail})")

    if dry_run:
        print(f"  [dry-run] 총 {total}건 — Redis 미기록")
        return 0

    try:
        store = await _open_store()
    except Exception as e:  # noqa: BLE001 — 사유를 그대로 노출하고 실패 처리
        return _redis_error(e)

    rc = 0
    try:
        for db_id, entries in sorted(collected.items()):
            try:
                saved = await store.save_entries(db_id, entries, replace=replace)
                print(f"  [ok] {db_id}: {saved}건 저장 (replace={replace})")
            except Exception as e:  # noqa: BLE001
                print(f"  [error] {db_id}: 저장 실패 — {e}", file=sys.stderr)
                rc = 1
    finally:
        await store.disconnect()
    return rc


async def cmd_stats(db: str) -> int:
    """적재 현황(DB별 항목 수)을 조회한다."""
    try:
        store = await _open_store()
    except Exception as e:  # noqa: BLE001
        return _redis_error(e)

    try:
        db_ids = sorted(_filter_dbs(collect_entries(), db))
        if not db_ids:
            print(f"  [skip] 대상 DB 없음 (db={db})")
            return 0
        for db_id in db_ids:
            count = await store.count(db_id)
            print(f"  - {db_id}: {count}건 (key={store.key(db_id)})")
    finally:
        await store.disconnect()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    """CLI 진입점."""
    parser = argparse.ArgumentParser(
        description="질의 이력 저장소 초기 적재 (Plan 67 N2)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_load = sub.add_parser("load", help="골드셋·프로필 예시 → Redis 적재")
    p_load.add_argument("--db", default="all", help="db_id 또는 all(기본)")
    p_load.add_argument(
        "--replace", action="store_true",
        help="기존 이력을 지우고 새로 쓴다(기본은 병합)",
    )
    p_load.add_argument(
        "--dry-run", action="store_true",
        help="Redis 없이 원천 파싱 결과만 출력",
    )

    p_stats = sub.add_parser("stats", help="적재 현황 조회")
    p_stats.add_argument("--db", default="all", help="db_id 또는 all(기본)")

    args = parser.parse_args(argv)
    if args.cmd == "load":
        return asyncio.run(
            cmd_load(args.db, replace=args.replace, dry_run=args.dry_run)
        )
    return asyncio.run(cmd_stats(args.db))


if __name__ == "__main__":
    raise SystemExit(main())
