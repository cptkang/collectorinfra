"""유사어 시드 파일 생성·로드·내보내기 CLI (Plan 61 트랙 B 후속).

설계: docs/synonym_seed_migration_review.md
운영 절차: docs/synonym_seed_migration_guide.md

서브커맨드:
    derive  시맨틱 모델(config/semantic_models) + 프로필(config/db_profiles)에서
            시드 파일(config/synonym_seeds/{db_id}.yaml)을 **결정적으로 생성**.
            (Redis 불필요 — 순수 파일 변환. 생성물은 git 커밋 = 마이그레이션 아티팩트)
    load    시드 파일을 Redis에 병합 로드(합집합 — 기존 등록분 무손실, 재실행 안전).
    export  운영 Redis의 per-DB 사전을 시드 형식으로 내보내기(운영 누적분 이관용).

사용 예:
    python scripts/synonym_seeds.py derive                    # 전 DB 일괄 생성
    python scripts/synonym_seeds.py derive --db polestar_cm_gp
    python scripts/synonym_seeds.py load --db polestar        # 시드 → Redis
    python scripts/synonym_seeds.py load --db all
    python scripts/synonym_seeds.py export --db polestar -o /tmp/polestar_export.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

SEMANTIC_DIR = REPO_ROOT / "config" / "semantic_models"
PROFILE_DIR = REPO_ROOT / "config" / "db_profiles"
SEED_DIR = REPO_ROOT / "config" / "synonym_seeds"

# 단어 최소 길이 — E5-1 매칭 가드(빈문자열·1글자 제외)와 정합
MIN_WORD_LEN = 2

SEED_HEADER = """\
# ============================================================================
# 유사어 시드 — {db_id} (생성물 — 직접 편집 금지)
# ============================================================================
# 생성: python scripts/synonym_seeds.py derive --db {db_id}
# 원천(단일 출처): config/semantic_models/{db_id}.yaml (dimensions.aliases,
#   pattern_b.measures.aliases, pattern_c.severity_map)
#   + config/db_profiles/{db_id}.yaml (known_attributes[].synonyms)
# 어휘를 추가하려면 이 파일이 아니라 **시맨틱 모델의 aliases**에 추가한 뒤
# derive를 재실행한다(사전 이중 관리 금지 — D-067 단일 출처).
# 반영: python scripts/synonym_seeds.py load --db {db_id}
#   (합집합 병합 — 기존 LLM 발견·운영자 등록 단어 무손실, 재실행 안전)
# ============================================================================
"""


def _clean_words(words: list[Any]) -> list[str]:
    """단어 목록을 정규화한다(공백 제거·최소 길이·중복 제거·정렬은 호출부)."""
    out: list[str] = []
    for w in words or []:
        s = str(w).strip()
        if len(s) >= MIN_WORD_LEN and s not in out:
            out.append(s)
    return out


def _schema_prefix(db_id: str) -> str:
    """db_id의 스키마 접두사를 결정한다(레지스트리 단일 출처, D-057).

    정본은 `config/db_registry.yaml`의 `db_schema`이며, 미등재 DB는 무스키마("")로
    둔다. 과거에는 db_id 접미사(`…b0`)로 폴스타 스키마를 추측하는 휴리스틱 폴백이
    있었으나, 레지스트리 단일화로 제거했다(편향 검토 §2-8 / Plan 67 R2).
    """
    from src.routing.db_schema import get_schema_prefix

    return get_schema_prefix(db_id)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rel(path: Path) -> str:
    """리포지토리 기준 상대 경로(밖이면 절대 경로 그대로)."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def derive_seed(db_id: str) -> Optional[Path]:
    """시맨틱 모델·프로필에서 db_id의 시드 파일을 결정적으로 생성한다.

    매핑 규칙(리뷰 문서 §2.1):
    - 패턴 A direct dimension → {prefix}cmm_resource.{column}
    - 패턴 A eav dimension    → eav_names[attribute] + {prefix}core_config_prop.name
    - 패턴 B measures aliases → {prefix}cmm_metric_stat_[h,d,m].{avg컬럼}
    - 패턴 C severity_map     → {prefix}cmm_alarm.alarmseverity + column_values
    - 프로필 known_attributes → eav_names 병합

    Returns:
        생성된 시드 파일 경로(원천 파일 부재 시 None)
    """
    import yaml

    sm_path = SEMANTIC_DIR / f"{db_id}.yaml"
    if not sm_path.exists():
        print(f"  [skip] 시맨틱 모델 없음: {sm_path}")
        return None
    model = yaml.safe_load(sm_path.read_text(encoding="utf-8")) or {}

    prefix = _schema_prefix(db_id)
    column_synonyms: dict[str, list[str]] = {}
    eav_names: dict[str, list[str]] = {}
    column_values: dict[str, dict[str, dict]] = {}
    derived_from = [{"file": _rel(sm_path), "sha256": _sha256(sm_path)}]

    def add_col(key: str, words: list[str]) -> None:
        cur = column_synonyms.setdefault(key, [])
        for w in words:
            if w not in cur:
                cur.append(w)

    def add_eav(name: str, words: list[str]) -> None:
        cur = eav_names.setdefault(name, [])
        for w in words:
            if w not in cur:
                cur.append(w)

    # ── 패턴 A: dimensions ──────────────────────────────────────────────
    pattern_a = model.get("pattern_a") or {}
    for dim in pattern_a.get("dimensions") or []:
        aliases = _clean_words(dim.get("aliases") or [])
        if not aliases:
            continue
        if dim.get("source") == "direct" and dim.get("column"):
            add_col(f"{prefix}cmm_resource.{dim['column']}", aliases)
        elif dim.get("source") == "eav" and dim.get("attribute"):
            add_eav(str(dim["attribute"]), aliases)
            # EAV 용어가 스키마 보충 게이트에서 core_config_prop을 올리도록
            add_col(f"{prefix}core_config_prop.name", aliases)

    # ── 패턴 B: measures ────────────────────────────────────────────────
    pattern_b = model.get("pattern_b") or {}
    metric_tables = pattern_b.get("metric_tables") or {}
    avg_col = (pattern_b.get("value_columns") or {}).get("avg", "avg_val")
    measure_words: list[str] = []
    for m in pattern_b.get("measures") or []:
        for w in _clean_words(m.get("aliases") or []):
            if w not in measure_words:
                measure_words.append(w)
    if measure_words:
        for table in metric_tables.values():
            add_col(f"{prefix}{table}.{avg_col}", measure_words)

    # ── 패턴 C: severity_map ────────────────────────────────────────────
    pattern_c = model.get("pattern_c") or {}
    severity_map = pattern_c.get("severity_map") or {}
    sev_words = _clean_words(list(severity_map.keys()))
    if sev_words:
        add_col(f"{prefix}cmm_alarm.alarmseverity", sev_words)
        column_values["cmm_alarm.alarmseverity"] = {
            word: {"op": "=", "value": value}
            for word, value in severity_map.items()
            if len(str(word).strip()) >= MIN_WORD_LEN
        }

    # ── 프로필 known_attributes ─────────────────────────────────────────
    profile_path = PROFILE_DIR / f"{db_id}.yaml"
    if profile_path.exists():
        profile = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
        for attr in profile.get("known_attributes") or []:
            words = _clean_words(attr.get("synonyms") or [])
            if attr.get("name") and words:
                add_eav(str(attr["name"]), words)
        derived_from.append(
            {"file": _rel(profile_path), "sha256": _sha256(profile_path)}
        )

    # ── 결정적 출력(정렬) ───────────────────────────────────────────────
    payload = {
        "version": "1.0",
        "db_id": db_id,
        "derived_from": derived_from,
        "source_tag": "operator",
        "column_synonyms": {k: sorted(v) for k, v in sorted(column_synonyms.items())},
        "eav_names": {k: sorted(v) for k, v in sorted(eav_names.items())},
        "column_values": {
            k: dict(sorted(v.items())) for k, v in sorted(column_values.items())
        },
    }

    SEED_DIR.mkdir(parents=True, exist_ok=True)
    out = SEED_DIR / f"{db_id}.yaml"
    body = yaml.safe_dump(
        payload, allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    out.write_text(SEED_HEADER.format(db_id=db_id) + body, encoding="utf-8")
    n_words = sum(len(v) for v in column_synonyms.values()) + sum(
        len(v) for v in eav_names.values()
    )
    print(
        f"  [ok] {_rel(out)} — column_keys={len(column_synonyms)}, "
        f"eav_names={len(eav_names)}, column_values={len(column_values)}, words={n_words}"
    )
    return out


def _all_db_ids() -> list[str]:
    return sorted(p.stem for p in SEMANTIC_DIR.glob("*.yaml"))


async def _make_loader():
    """Redis 연결된 SynonymLoader를 생성한다(load/export 공용)."""
    from src.config import load_config
    from src.schema_cache.redis_cache import RedisSchemaCache
    from src.schema_cache.synonym_loader import SynonymLoader

    config = load_config()
    redis_cache = RedisSchemaCache(config.redis, config.schema_cache)
    await redis_cache.connect()
    return SynonymLoader(redis_cache=redis_cache), redis_cache


async def cmd_load(db_ids: list[str]) -> int:
    loader, redis_cache = await _make_loader()
    rc = 0
    try:
        for db_id in db_ids:
            seed = SEED_DIR / f"{db_id}.yaml"
            if not seed.exists():
                print(f"  [skip] 시드 없음: {seed} (derive 먼저 실행)")
                continue
            result = await loader.load_seed_yaml(str(seed))
            print(f"  [{result.status}] {db_id}: {result.message}")
            if result.status == "error":
                rc = 1
    finally:
        close = getattr(redis_cache, "disconnect", None) or getattr(
            redis_cache, "close", None
        )
        if close:
            maybe = close()
            if asyncio.iscoroutine(maybe):
                await maybe
    return rc


async def cmd_export(db_id: str, output: str) -> int:
    loader, redis_cache = await _make_loader()
    try:
        ok = await loader.export_seed_yaml(db_id, output)
        print(f"  [{'ok' if ok else 'error'}] {db_id} → {output}")
        return 0 if ok else 1
    finally:
        close = getattr(redis_cache, "disconnect", None) or getattr(
            redis_cache, "close", None
        )
        if close:
            maybe = close()
            if asyncio.iscoroutine(maybe):
                await maybe


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="유사어 시드 생성·로드·내보내기 (Plan 61)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_derive = sub.add_parser("derive", help="시맨틱 모델·프로필 → 시드 파일 생성")
    p_derive.add_argument("--db", default="all", help="db_id 또는 all(기본)")

    p_load = sub.add_parser("load", help="시드 파일 → Redis 병합 로드")
    p_load.add_argument("--db", default="all", help="db_id 또는 all(기본)")

    p_export = sub.add_parser("export", help="Redis per-DB 사전 → 시드 형식 내보내기")
    p_export.add_argument("--db", required=True, help="db_id")
    p_export.add_argument("-o", "--output", required=True, help="출력 YAML 경로")

    args = parser.parse_args(argv)

    if args.cmd == "derive":
        targets = _all_db_ids() if args.db == "all" else [args.db]
        print(f"derive 대상: {targets}")
        made = [derive_seed(d) for d in targets]
        return 0 if any(made) else 1

    if args.cmd == "load":
        targets = _all_db_ids() if args.db == "all" else [args.db]
        print(f"load 대상: {targets}")
        return asyncio.run(cmd_load(targets))

    if args.cmd == "export":
        return asyncio.run(cmd_export(args.db, args.output))

    return 2


if __name__ == "__main__":
    sys.exit(main())
