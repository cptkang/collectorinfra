"""유사어 시드 파일 생성·로드·내보내기 CLI (Plan 61 트랙 B 후속).

설계: docs/synonym_seed_migration_review.md
운영 절차: docs/synonym_seed_migration_guide.md

서브커맨드:
    derive  시맨틱 모델(config/semantic_models) + 프로필(config/db_profiles)에서
            시드 파일(config/synonym_seeds/{db_id}.yaml)을 **결정적으로 생성**.
            (Redis 불필요 — 순수 파일 변환. 생성물은 git 커밋 = 마이그레이션 아티팩트)
    load    시드 파일을 Redis에 병합 로드(합집합 — 기존 등록분 무손실, 재실행 안전).
    export  운영 Redis의 per-DB 사전을 시드 형식으로 내보내기(운영 누적분 이관용).
    audit   (읽기 전용) Redis 유사어 중 자동 등록 쓰기 가드 규칙 위반 항목을 찾는다(plans/120 F-5).
            저장 공간 3종 — DB별(`schema:{db_id}:synonyms`)·전역(`synonyms:global`)·EAV 속성명
            (`synonyms:eav_names`). 판정은 런타임 가드와 같은 함수
            (`synonym_registration_block_reason`).
    prune   audit 위반 중 자동 등록분만 제거한다. 기본은 dry-run 목록, `--apply`일 때만 삭제하며
            삭제 전에 백업 JSON을 먼저 쓴다. 사람 등록 출처(`operator` 등)는 건드리지 않는다.
            출처 태그가 없는 항목(전역·EAV 저장 공간 · 레거시 목록형)은 `--include-untagged`일 때만.
    restore prune 백업 JSON으로 되돌린다(기본 dry-run, `--apply`일 때만 쓴다).

사용 예:
    python scripts/synonym_seeds.py derive                    # 전 DB 일괄 생성
    python scripts/synonym_seeds.py derive --db polestar_cm_gp
    python scripts/synonym_seeds.py load --db polestar        # 시드 → Redis
    python scripts/synonym_seeds.py load --db all
    python scripts/synonym_seeds.py export --db polestar -o /tmp/polestar_export.yaml
    python scripts/synonym_seeds.py audit                     # 위반 표 (--json 가능)
    python scripts/synonym_seeds.py prune                     # 삭제 예정 목록(변경 없음)
    python scripts/synonym_seeds.py prune --apply             # 백업 후 삭제
    python scripts/synonym_seeds.py restore .cache/synonym_backups/<파일>.json --apply
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

SEMANTIC_DIR = REPO_ROOT / "config" / "semantic_models"
PROFILE_DIR = REPO_ROOT / "config" / "db_profiles"
SEED_DIR = REPO_ROOT / "config" / "synonym_seeds"
BACKUP_DIR = REPO_ROOT / ".cache" / "synonym_backups"

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


# ── 오염 진단·정리 (plans/120 F-5) ─────────────────────────────────────────
# 판정은 런타임 쓰기 가드(`src/document/synonym_write_guard.py`)와 같은 함수다(사본 금지).
# 출처 태그는 DB별 저장 공간에만 단어 단위로 있다(`{"words": [...], "sources": {단어: 태그}}`).
# 전역·EAV 속성명 저장 공간과 레거시 목록형 값은 태그가 없다 — 기본은 보존하고 표에 따로 적는다.

STORE_DB = "db"
STORE_GLOBAL = "global"
STORE_EAV = "eav_names"
STORE_LABELS = {STORE_DB: "DB별", STORE_GLOBAL: "전역", STORE_EAV: "EAV 속성명"}

#: 자동 등록 출처 — prune 삭제 대상.
#: 그 밖의 태그(`operator`·`user_corrected` 등 사람 등록)는 불가침.
AUTO_SOURCES = frozenset({"llm", "llm_inferred"})

ACTION_DELETE = "delete"
ACTION_KEEP_PROTECTED = "keep_protected"
ACTION_KEEP_UNTAGGED = "keep_untagged"
ACTION_LABELS = {
    ACTION_DELETE: "삭제",
    ACTION_KEEP_PROTECTED: "보존(사람 등록 출처)",
    ACTION_KEEP_UNTAGGED: "보존(태그 없음 — --include-untagged로 삭제)",
}

BACKUP_KIND = "synonym_prune_backup"


@dataclass(frozen=True)
class Violation:
    """쓰기 가드 규칙을 어긴 유사어 단어 1건."""

    store: str  # STORE_*
    redis_key: str  # Redis Hash 키
    db_id: str | None  # DB별 저장 공간만 — 전역·EAV는 None
    key: str  # Hash 필드: table.column · bare 컬럼명 · EAV 속성명
    word: str
    source: str | None  # 출처 태그(없으면 None)
    reason: str  # synonym_write_guard BLOCK_* 코드

    def action(self, include_untagged: bool) -> str:
        """prune 조치 — 자동 등록 출처만 삭제, 태그 없음은 옵션일 때만."""
        if self.source is None:
            return ACTION_DELETE if include_untagged else ACTION_KEEP_UNTAGGED
        if self.source in AUTO_SOURCES:
            return ACTION_DELETE
        return ACTION_KEEP_PROTECTED


def _structure_backup_root() -> Path:
    """구조 프로필 버전 이력 루트 — 캐시 매니저 `structure_store`와 같은 위치."""
    from src.config import load_config

    cache_dir = Path(load_config().schema_cache.cache_dir)
    if not cache_dir.is_absolute():
        cache_dir = REPO_ROOT / cache_dir
    return cache_dir.parent / "structure"


def _structure_meta_readonly(db_id: str, backup_root: Path) -> dict[str, Any] | None:
    """런타임(`get_structure_meta_or_profile`)과 같은 순서로 구조 선언을 읽되 Redis에 쓰지 않는다.

    승인 적용본(버전 이력의 최신 approved·rollback) → 프로필 파일 순이다. 런타임 경로는 적용본을
    Redis 캐시에 다시 쓰므로(`restore_applied`) 읽기 전용 진단은 버전 파일을 직접 읽는다.
    """
    from src.schema_cache.catalog_builder import load_structure_profile
    from src.schema_cache.structure_store import StructureStore

    profile: Any = None
    try:
        latest = StructureStore(None, backup_root).latest_applied_version(db_id)
        profile = latest.get("profile") if latest else None
    except Exception:  # noqa: BLE001 — db_id 형식 오류·버전 파일 손상은 프로필 폴백
        profile = None
    if not isinstance(profile, dict):
        profile = load_structure_profile(db_id, profiles_dir=str(PROFILE_DIR))
    if not isinstance(profile, dict):
        return None
    return {k: v for k, v in profile.items() if k != "source"}


def _tagged_words(raw: Any) -> tuple[list[str], dict[str, Any] | None]:
    """DB별·전역 값(JSON)에서 (단어 목록, 출처 태그 dict 또는 None)을 꺼낸다."""
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return [], None
    if isinstance(parsed, dict) and isinstance(parsed.get("words"), list):
        sources = parsed.get("sources")
        return [str(w) for w in parsed["words"]], sources if isinstance(sources, dict) else None
    if isinstance(parsed, list):
        return [str(w) for w in parsed], None
    return [], None


async def collect_violations(
    redis_cache: Any,
    db_ids: Iterable[str] | None = None,
    *,
    backup_root: Path | None = None,
) -> list[Violation]:
    """Redis 유사어 저장 공간을 읽어 쓰기 가드 규칙 위반 단어를 모은다(쓰기 0).

    Args:
        redis_cache: 연결된 `RedisSchemaCache`
        db_ids: DB별 저장 공간을 이 DB로 한정(None이면 전체 — 이때만 전역·EAV도 본다)
        backup_root: 구조 프로필 버전 이력 루트(None이면 설정에서 해소)

    Returns:
        위반 목록(저장 공간 → DB → 키 → 단어 순)
    """
    from src.document.synonym_write_guard import synonym_registration_block_reason
    from src.schema_cache.redis_cache import RedisSchemaCache

    client = redis_cache._redis
    only = set(db_ids) if db_ids is not None else None
    root = backup_root if backup_root is not None else _structure_backup_root()
    out: list[Violation] = []

    db_keys = sorted([k async for k in client.scan_iter(match="schema:*:synonyms")])
    for rkey in db_keys:
        db_id = RedisSchemaCache._db_id_from_key(rkey)
        if only is not None and db_id not in only:
            continue
        meta = _structure_meta_readonly(db_id, root)
        for col, raw in sorted((await client.hgetall(rkey)).items()):
            words, sources = _tagged_words(raw)
            for word in words:
                reason = synonym_registration_block_reason(word, col, structure_meta=meta)
                if reason:
                    source = sources.get(word) if sources is not None else None
                    out.append(Violation(STORE_DB, rkey, db_id, col, word, source, reason))

    if only is not None:
        return out

    eav_key = RedisSchemaCache.EAV_NAME_SYNONYMS_KEY
    eav_raw = await client.hgetall(eav_key)

    # 전역 키가 EAV 속성명이면 EAV 대상으로 판정한다 — EAV 등록은 전역에도 같은 속성명으로
    # 사본을 쓰고, 런타임 EAV 매칭이 그 사본 단어를 병합해 쓴다(`_apply_eav_synonym_mapping`).
    global_key = RedisSchemaCache.GLOBAL_SYNONYMS_KEY
    for col, raw in sorted((await client.hgetall(global_key)).items()):
        words, _ = _tagged_words(raw)
        target = f"EAV:{col}" if col in eav_raw else col
        for word in words:
            reason = synonym_registration_block_reason(word, target)
            if reason:
                out.append(Violation(STORE_GLOBAL, global_key, None, col, word, None, reason))

    for attr, raw in sorted(eav_raw.items()):
        words, _ = _tagged_words(raw)
        for word in words:
            reason = synonym_registration_block_reason(word, f"EAV:{attr}")
            if reason:
                out.append(Violation(STORE_EAV, eav_key, None, attr, word, None, reason))
    return out


def _print_violations(violations: list[Violation], include_untagged: bool) -> None:
    """위반 표와 요약을 출력한다."""
    from src.document.synonym_write_guard import BLOCK_REASON_LABELS

    if not violations:
        print("  위반 항목 없음")
        return
    # 탭 구분 — 월 구조 필드명에 '|'가 들어 있어 파이프 구분은 칸이 섞인다
    print("  " + "\t".join(("저장소", "DB", "키", "단어", "출처", "사유", "prune 조치")))
    for v in violations:
        print("  " + "\t".join((
            STORE_LABELS[v.store], v.db_id or "-", v.key, v.word, v.source or "(태그 없음)",
            BLOCK_REASON_LABELS[v.reason], ACTION_LABELS[v.action(include_untagged)],
        )))
    counts: dict[str, int] = {}
    for v in violations:
        label = ACTION_LABELS[v.action(include_untagged)]
        counts[label] = counts.get(label, 0) + 1
    summary = ", ".join(f"{k} {n}" for k, n in counts.items())
    print(f"  합계 {len(violations)}건 — {summary}")


def _violation_json(v: Violation, include_untagged: bool) -> dict[str, Any]:
    from src.document.synonym_write_guard import BLOCK_REASON_LABELS

    return {
        **asdict(v),
        "store_label": STORE_LABELS[v.store],
        "reason_label": BLOCK_REASON_LABELS[v.reason],
        "action": v.action(include_untagged),
    }


async def _connect_redis() -> Any:
    """설정의 Redis에 연결된 `RedisSchemaCache`를 만든다."""
    from src.config import load_config
    from src.schema_cache.redis_cache import RedisSchemaCache

    config = load_config()
    redis_cache = RedisSchemaCache(config.redis, config.schema_cache)
    await redis_cache.connect()
    return redis_cache


async def run_audit(
    redis_cache: Any, db_ids: list[str] | None, *, as_json: bool,
    backup_root: Path | None = None,
) -> int:
    """audit 본체(읽기 전용)."""
    violations = await collect_violations(redis_cache, db_ids, backup_root=backup_root)
    if as_json:
        payload = {
            "total": len(violations),
            "violations": [_violation_json(v, include_untagged=False) for v in violations],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"audit 대상: {'전체(DB별·전역·EAV 속성명)' if db_ids is None else db_ids}")
        _print_violations(violations, include_untagged=False)
    return 0


def _write_backup(
    backup_dir: Path, entries: list[dict[str, Any]], targets: list[Violation]
) -> Path:
    """삭제 전 원값 백업을 쓴다(동기화까지 마친 뒤 경로 반환 — 실패하면 예외로 삭제를 막는다)."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = backup_dir / f"synonym_prune_backup_{stamp}.json"
    n = 1
    while path.exists():
        path = backup_dir / f"synonym_prune_backup_{stamp}_{n}.json"
        n += 1
    payload = {
        "kind": BACKUP_KIND,
        "version": 1,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "entries": entries,
        "removed": [asdict(v) for v in targets],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    return path


async def _remove_eav_words(client: Any, eav_key: str, attr: str, words: list[str]) -> None:
    """EAV 속성명 저장 공간에서 단어를 뺀다(비면 필드 삭제) — 이 공간은 제거 API가 없다."""
    raw = await client.hget(eav_key, attr)
    current, _ = _tagged_words(raw)
    remaining = [w for w in current if w not in set(words)]
    if remaining:
        await client.hset(eav_key, attr, json.dumps(remaining, ensure_ascii=False))
    else:
        await client.hdel(eav_key, attr)


async def run_prune(
    redis_cache: Any,
    db_ids: list[str] | None,
    *,
    apply: bool,
    include_untagged: bool,
    backup_dir: Path,
    backup_root: Path | None = None,
) -> int:
    """prune 본체 — dry-run이 기본이고 `apply`면 백업을 먼저 쓴 뒤 삭제한다."""
    violations = await collect_violations(redis_cache, db_ids, backup_root=backup_root)
    print(f"prune 대상: {'전체(DB별·전역·EAV 속성명)' if db_ids is None else db_ids}")
    _print_violations(violations, include_untagged)
    targets = [v for v in violations if v.action(include_untagged) == ACTION_DELETE]
    if not targets:
        print("  삭제 대상 없음 — 변경 없음")
        return 0
    if not apply:
        print(f"  dry-run — 변경 없음. 삭제 대상 {len(targets)}건은 --apply로 삭제한다(백업 선행).")
        return 0

    client = redis_cache._redis
    groups: dict[tuple[str, str], list[Violation]] = {}
    for v in targets:
        groups.setdefault((v.redis_key, v.key), []).append(v)
    entries: list[dict[str, Any]] = []
    for rkey, field in groups:
        raw = await client.hget(rkey, field)
        if raw is not None:
            entries.append({"key": rkey, "field": field, "value": raw})
    try:
        path = _write_backup(backup_dir, entries, targets)
    except OSError as e:
        print(f"  [error] 백업 쓰기 실패 — 삭제하지 않았다: {e}")
        return 1
    print(f"  백업: {path}")

    removed = 0
    failed = False
    for (rkey, field), group in groups.items():
        words = list(dict.fromkeys(v.word for v in group))
        store = group[0].store
        if store == STORE_DB:
            ok = await redis_cache.remove_synonyms(group[0].db_id, field, words)
        elif store == STORE_GLOBAL:
            ok = await redis_cache.remove_global_synonym(field, words)
        else:
            await _remove_eav_words(client, rkey, field, words)
            ok = True
        if ok:
            removed += len(words)
        else:
            failed = True
            print(f"  [error] 삭제 실패: {rkey} {field} {words}")
    print(f"  삭제 {removed}건. 되돌리기: python scripts/synonym_seeds.py restore {path} --apply")
    return 1 if failed else 0


async def run_restore(redis_cache: Any, backup_path: Path, *, apply: bool) -> int:
    """restore 본체 — 백업의 원값으로 Hash 필드를 덮어쓴다(기본 dry-run).

    prune 뒤 같은 필드에 새로 등록된 단어는 원값으로 덮이면서 사라진다.
    """
    data = json.loads(backup_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("kind") != BACKUP_KIND:
        print(f"  [error] prune 백업 파일이 아니다: {backup_path}")
        return 1
    entries = [e for e in data.get("entries") or [] if isinstance(e, dict)]
    print(f"restore 대상: {backup_path} — 필드 {len(entries)}개")
    for e in entries:
        print(f"  {e.get('key')} | {e.get('field')}")
    if not apply:
        print("  dry-run — 변경 없음. 복원은 --apply")
        return 0
    client = redis_cache._redis
    for e in entries:
        await client.hset(e["key"], e["field"], e["value"])
    print(f"  복원 {len(entries)}개 필드")
    return 0


async def _with_redis(fn: Any, *args: Any, **kwargs: Any) -> int:
    """Redis에 연결해 본체를 실행하고 연결을 닫는다(연결 실패는 exit 1)."""
    try:
        redis_cache = await _connect_redis()
    except Exception as e:  # noqa: BLE001
        print(f"  [error] Redis 연결 실패: {e}")
        return 1
    try:
        return int(await fn(redis_cache, *args, **kwargs))
    finally:
        await redis_cache.disconnect()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="유사어 시드 생성·로드·내보내기 (Plan 61) · 오염 진단·정리·복원 (plans/120 F-5)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_derive = sub.add_parser("derive", help="시맨틱 모델·프로필 → 시드 파일 생성")
    p_derive.add_argument("--db", default="all", help="db_id 또는 all(기본)")

    p_load = sub.add_parser("load", help="시드 파일 → Redis 병합 로드")
    p_load.add_argument("--db", default="all", help="db_id 또는 all(기본)")

    p_export = sub.add_parser("export", help="Redis per-DB 사전 → 시드 형식 내보내기")
    p_export.add_argument("--db", required=True, help="db_id")
    p_export.add_argument("-o", "--output", required=True, help="출력 YAML 경로")

    p_audit = sub.add_parser("audit", help="(읽기 전용) 쓰기 가드 규칙 위반 유사어 목록")
    p_audit.add_argument(
        "--db", default="all", help="DB별 저장 공간 한정 db_id 또는 all(기본 — 전역·EAV 포함)"
    )
    p_audit.add_argument("--json", action="store_true", help="JSON으로 출력")

    p_prune = sub.add_parser("prune", help="위반 유사어 중 자동 등록분 제거(기본 dry-run)")
    p_prune.add_argument(
        "--db", default="all", help="DB별 저장 공간 한정 db_id 또는 all(기본 — 전역·EAV 포함)"
    )
    p_prune.add_argument("--apply", action="store_true", help="백업을 쓴 뒤 실제로 삭제")
    p_prune.add_argument(
        "--include-untagged", action="store_true",
        help="출처 태그가 없는 항목(전역·EAV 속성명·레거시 목록형)도 삭제",
    )
    p_prune.add_argument("--backup-dir", default=str(BACKUP_DIR), help="백업 JSON 디렉터리")

    p_restore = sub.add_parser("restore", help="prune 백업 JSON으로 복원(기본 dry-run)")
    p_restore.add_argument("backup", help="prune이 쓴 백업 JSON 경로")
    p_restore.add_argument("--apply", action="store_true", help="실제로 복원")

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

    if args.cmd == "audit":
        db_filter = None if args.db == "all" else [args.db]
        return asyncio.run(_with_redis(run_audit, db_filter, as_json=args.json))

    if args.cmd == "prune":
        db_filter = None if args.db == "all" else [args.db]
        return asyncio.run(
            _with_redis(
                run_prune, db_filter, apply=args.apply,
                include_untagged=args.include_untagged, backup_dir=Path(args.backup_dir),
            )
        )

    if args.cmd == "restore":
        return asyncio.run(_with_redis(run_restore, Path(args.backup), apply=args.apply))

    return 2


if __name__ == "__main__":
    sys.exit(main())
