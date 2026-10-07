"""외부망 자산 빌더 — 반출 카탈로그로 ITAM 설정 파일을 만든다 (plans/140 W3 · D-311 ③⑤).

반출 run 디렉터리(`schema_catalog.yaml` · 있으면 `code_samples.yaml`)와 저장소 시드 정의를 읽어
다음을 쓴다. LLM·DB·Redis 0.

- `testdata/itam_bench/closed/itam_schema.json` — 앱 스키마 캐시 형식
  (`PersistentSchemaCache.save`) · 기본키·타입 반영 · 표본 행 없음. `install_cache=True`면
  `.cache/schema/itam_schema.json`에도 쓴다(기존 파일은 시각 접미 백업).
- `config/db_profiles/itam.yaml` — `allowed_tables`(반출 승인 프로필 − 기본 제외) ·
  `table_definitions`(시드 초안 + 반출된 내부망 편집분 · `manual` 우선 · 정의 검증 오류 0일
  때만) · 근거 있는 `entity_keys`(P1 값 형식) · `relationships`(P1 값 겹침 채택분) ·
  `query_rules`(P1 값 형식).
  `query_guide`·코드값·코드 라벨·예시·패턴은 쓰지 않는다(코드값 정본은 반입 뒤 내부망 P1 승인).
- `config/synonym_seeds/itam.yaml` — 반출 컬럼에 DB 주석 근거가 있을 때만.

**치환값 차단** — 반출에 `code_samples.yaml`(치환 코드값 · 외부망 테스트 전용)이 있으면 쓸 파일의
본문 전체(문자열 잎·매핑 키 · 파싱 못 하면 텍스트 줄)에 그 값이 토큰으로 나올 때 쓰기를 거부한다
(위치만 출력 · 값은 출력하지 않는다 · 감사 L-3·L-5). 카탈로그 식별자와 같은 치환값은 대조에서 빼고
수만 요약에 남긴다.

**로컬 샌드박스 보존(G-3)** — 덮어쓰기 전에 현 프로필이 로컬 샌드박스 승인본
(`environment: local_sandbox`)이면 `testdata/itam/db_profile.local_sandbox.yaml`로 바이트 그대로
복사한다(이미 있으면 덮지 않음).

자산 조립은 P1과 같은 순수 함수(`src.domain.schema_inference`)를 부른다 — 사본을 두지 않는다.

**P2 경로(`--p2` · plans/140 W5)** — 「DB 구조」 탭 P2 잡(`run_asset_llm`)과 같은 조각(요약 →
쿼리 예시·DB 전용 규칙 섹션 LLM 초안 → 결정적 검증·DB 실행)을 반출 카탈로그로 돌린다. 실행 성공분
중 치환 코드값이 리터럴로 나오는 예시는 빼고(수만 보고), 섹션은 통째로 버린다. 남은 예시는 프로필
`query_examples`, 섹션은 `AssetFileStore`와 같은 경로·형식
(`config/knowledge/itam/prompt_template.yaml`)에 쓴다. 근거(검증 통과분)가 0이면 키·파일을
만들지 않는다. LLM 과금 평면이면 실행하지 않는다(D-127).

**지식 오버레이(plans/141 W4 · D-314 ②)** — 원천 디렉터리(기본
`testdata/itam_bench/closed/knowledge`)가 있으면 `knowledge.avalidate_dir`로 검증해 **통과한 active
항목만** 옮긴다: 프로필 `query_guide`(K1) · `query_examples`(K2 — P2 예시 뒤 · 같은 질문은
앞선 것) · `query_rules`(K6 정의 파생 — P1 규칙 우선 합집합) ·
`config/knowledge/itam/prompt_template.yaml`(K4 — P2 섹션보다 우선) ·
`column_descriptions.yaml`(K3 설명) · `query_templates.yaml`(K8 — 통과분 0이면 파일을 만들지
않는다) · 유사어 시드(K3 — DB 주석 근거·기존 파일 우선 합집합). 검증 실패 항목은 빼고
사유를 출력한다. 모의 DB에 못 붙으면 `knowledge_static_only`일 때만 정적 통과분을 쓰고, 아니면
오버레이를 건너뛴다고 출력한다. 원천이 없거나 통과 항목 0이면 종전 산출과 바이트 같다.

**철회 반영** — 검증을 실제로 한 빌드(정적 전용 포함)에서 이번 통과분이 0인 지식 산출 파일
(K3 설명 · K8 템플릿 · K4 섹션)은 머리 주석 첫 줄이 오버레이 표지(`KNOWLEDGE_FILE_MARKER`)일 때만
지운다(런타임은 「파일 없음 = 무동작」) — 표지가 없으면(P2 · 「DB 구조」 탭 승인본 등) 남기고
출력한다. 유사어 시드는 원천에 있으나 이번에 통과하지 못한 낱말을 기존 파일에서 걷어 낸다(DB 주석
근거 낱말은 남긴다 · 남는 낱말이 0이면 파일을 지운다). 원천이 없거나 모의 DB 미연결로 건너뛴
빌드는 아무것도 지우지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from src.domain import schema_inference as inference
from src.domain.profile_merge import LOCAL_SANDBOX_ENVIRONMENT, MANUAL_SOURCE, merge_profile
from src.domain.schema_snapshot import bare_name, build_snapshot
from src.domain.table_definitions import (
    ORIGIN_MANUAL,
    PROFILE_KEY,
    parse_import_document,
    validate_table_definitions,
)
from src.schema_cache.asset_store import ASSET_PATHS
from src.schema_cache.knowledge_descriptions import FILE_NAME as DESCRIPTIONS_FILE_NAME
from src.schema_cache.knowledge_descriptions import FORMAT_VERSION as DESCRIPTIONS_VERSION
from src.schema_cache.knowledge_descriptions import ORIGIN_CLAUDE_CODE
from src.schema_cache.persistent_cache import PersistentSchemaCache
from src.schema_cache.structure_store import _dump_yaml_exact

from . import DB_ID, REPO_ROOT

#: 조회 대상에서 기본 제외하는 테이블 — 계정 비밀번호 칸 보유(plans/139 권고 · `keep_excluded`로 끔)
DEFAULT_EXCLUDED_TABLES: tuple[str, ...] = ("tcdmsif81",)

CATALOG_FILE = "schema_catalog.yaml"
CODE_SAMPLES_FILE = "code_samples.yaml"

SEED_DEFINITIONS_REL = Path("testdata/itam_bench/closed/table_definitions.yaml")
SCHEMA_SEED_REL = Path("testdata/itam_bench/closed/itam_schema.json")
PROFILE_REL = Path("config/db_profiles") / f"{DB_ID}.yaml"
SYNONYM_SEED_REL = Path("config/synonym_seeds") / f"{DB_ID}.yaml"
LOCAL_SANDBOX_PROFILE_REL = Path("testdata/itam/db_profile.local_sandbox.yaml")
INSTALL_CACHE_REL = Path(".cache/schema") / f"{DB_ID}_schema.json"
#: DB 전용 규칙 섹션 파일 — 「DB 구조」 탭 승인과 같은 경로
#: (`AssetFileStore` · 생성 템플릿 어댑터가 읽음)
SECTION_REL = Path(ASSET_PATHS["prompt_template"].format(db_id=DB_ID))
#: 설명 정본 파일(D-314 ④ — 스키마 로드 때 Redis에 없는 컬럼만 채운다)
DESCRIPTIONS_REL = Path("config/knowledge") / DB_ID / DESCRIPTIONS_FILE_NAME
#: 조립 템플릿 파일(D-314 ③ — `template_assembler.TEMPLATE_PATH`와 같은 경로)
TEMPLATES_REL = Path("config/knowledge") / DB_ID / "query_templates.yaml"
#: 지식 오버레이가 쓴 파일(K3 설명 · K4 섹션 · K8 템플릿)의 머리 주석 첫 줄 접두 — 철회 정리 판별
KNOWLEDGE_FILE_MARKER = "# plans/141 W4 외부망 자산 빌더"
#: P2 기본 엔진(레지스트리 `itam` 엔진)
P2_ENGINE = "mariadb"

#: 값(코드값·조건 리터럴)이 들어갈 수 있는 프로필·시드 키 — 근거 없이는 만들지 않는 칸(치환값 차단은
#: 이 칸에 한정하지 않고 본문 전체를 본다 · 감사 L-3)
VALUE_KEYS: tuple[str, ...] = (
    "query_rules", "query_examples", "code_values", "code_labels", "column_values", "patterns",
    "query_guide",
)
#: 스키마 캐시 차이·치환값 대조에서 뺄 키(저장 시각)
_TIMESTAMP_KEYS = ("_cached_at", "_cached_at_iso")

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_INPUT = 2  # 입력 오류 · P2 DB 연결 실패


class BuildError(Exception):
    """빌드 중단 — `exit_code`와 사람이 읽을 사유(값 없음)."""

    def __init__(self, message: str, exit_code: int = EXIT_REFUSED) -> None:
        super().__init__(message)
        self.exit_code = exit_code


# ──────────────────────────────────────────────
# 입력
# ──────────────────────────────────────────────


def load_export(run_dir: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """반출 run의 카탈로그와 (있으면) 치환 코드값 파일을 읽는다.

    Raises:
        BuildError: 카탈로그 없음·형식 오류(`EXIT_INPUT`)
    """
    path = Path(run_dir) / CATALOG_FILE
    if not path.is_file():
        raise BuildError(f"반출 카탈로그가 없습니다: {path}", EXIT_INPUT)
    catalog = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(catalog, dict) or not isinstance(catalog.get("tables"), Mapping):
        raise BuildError(f"반출 카탈로그에 `tables` 매핑이 없습니다: {path}", EXIT_INPUT)
    samples_path = Path(run_dir) / CODE_SAMPLES_FILE
    samples = None
    if samples_path.is_file():
        loaded = yaml.safe_load(samples_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise BuildError(f"{CODE_SAMPLES_FILE} 형식 오류: 매핑이 아닙니다", EXIT_INPUT)
        samples = loaded
    return catalog, samples


def catalog_columns(catalog: Mapping[str, Any]) -> dict[str, list[str]]:
    """``{테이블: [컬럼 이름…]}`` — 카탈로그 순서."""
    return {
        str(t): [str(c.get("name")) for c in (info.get("columns") or [])]
        for t, info in catalog["tables"].items()
    }


def substituted_values(samples: Mapping[str, Any] | None) -> set[str]:
    """`code_samples.yaml`의 치환 코드값·라벨 문자열 집합(빈 문자열 제외)."""
    found: set[str] = set()
    columns = (samples or {}).get("columns") or {}
    for entry in columns.values() if isinstance(columns, Mapping) else ():
        if not isinstance(entry, Mapping):
            continue
        for value in entry.get("values") or []:
            found.add(str(value))
        for pair in entry.get("labels") or []:
            for value in pair if isinstance(pair, (list, tuple)) else (pair,):
                found.add(str(value))
    found.discard("")
    return found


# ──────────────────────────────────────────────
# 자산 조립(순수)
# ──────────────────────────────────────────────


def allowed_tables(
    catalog: Mapping[str, Any], *, keep_excluded: bool = False
) -> tuple[list[str], list[str]]:
    """반출 승인 프로필 `allowed_tables` − 기본 제외 → ``(조회 대상, 뺀 테이블)``.

    Raises:
        BuildError: 반출에 승인 프로필 `allowed_tables`가 없음(`EXIT_INPUT`)
    """
    approved = catalog.get("approved_profile") or {}
    tables = approved.get("allowed_tables") if isinstance(approved, Mapping) else None
    if not isinstance(tables, list):
        raise BuildError("반출 카탈로그에 승인 프로필 `allowed_tables`가 없습니다", EXIT_INPUT)
    excluded = set() if keep_excluded else {bare_name(t) for t in DEFAULT_EXCLUDED_TABLES}
    kept = [str(t) for t in tables if bare_name(str(t)) not in excluded]
    dropped = [str(t) for t in tables if bare_name(str(t)) in excluded]
    return kept, dropped


def table_definitions(
    seed_document: Any,
    catalog: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]], dict[str, int]]:
    """시드 초안 + 반출 내부망 편집분 → 검증한 정의.

    병합은 승인 병합 규칙(`merge_profile` 규칙 8)을 그대로 쓴다 — 반출 정의를 base로, 시드를
    초안으로 두어 base의 `manual` 항목이 이기고, 그 밖의 항목은 시드가 교체하며 한쪽에만 있는
    테이블은 남는다.

    Returns:
        ``(유효 정의, 오류, 출처별 건수)`` — 스냅샷 컬럼은 반출 카탈로그 컬럼이다
    """
    seed = parse_import_document(seed_document)
    approved = catalog.get("approved_profile") or {}
    exported = approved.get(PROFILE_KEY) if isinstance(approved, Mapping) else None
    if isinstance(exported, Mapping) and exported:
        merged = merge_profile(
            {PROFILE_KEY: dict(exported)}, {PROFILE_KEY: seed}, local_sandbox=False
        )[PROFILE_KEY]
    else:
        merged = seed
    valid, errors = validate_table_definitions(merged, catalog_columns(catalog))
    counts: dict[str, int] = {}
    for entry in valid.values():
        origin = str(entry.get("origin"))
        counts[origin] = counts.get(origin, 0) + 1
    return valid, errors, counts


def _value_profile(raw: Any) -> inference.ValueProfile | None:
    """반출 컬럼 `profile`(비율) → `ValueProfile`(형식 비율이 없거나 오류면 None)."""
    if not isinstance(raw, Mapping) or raw.get("error"):
        return None
    formats = raw.get("formats")
    total = raw.get("total")
    if not isinstance(formats, Mapping) or not isinstance(total, int):
        return None
    return inference.ValueProfile(
        total=total,
        date8=float(formats.get("date8") or 0.0),
        datetime14=float(formats.get("datetime14") or 0.0),
        ipv4=float(formats.get("ipv4") or 0.0),
        hostname=float(formats.get("hostname") or 0.0),
        multi_value=float(formats.get("multi_value") or 0.0),
        mixed_case=bool(raw.get("mixed_case")),
        flag=tuple(str(v) for v in raw.get("flag") or ()),
    )


def _db_comment(column: Mapping[str, Any]) -> str | None:
    """컬럼 의미가 DB 주석일 때만 그 문장(LLM 설명·출처 불명 설명은 근거가 아니다)."""
    if column.get("meaning_source") == "db_comment" and column.get("meaning"):
        return str(column["meaning"])
    return None


def evidence_assets(catalog: Mapping[str, Any], allowed: Sequence[str]) -> dict[str, Any]:
    """P1 근거(값 형식 비율 · 값 겹침 채택)로 `entity_keys`·`relationships`·`query_rules`.

    조회 대상 테이블만 본다. 근거가 없는 자산은 결과에 키를 넣지 않는다.
    """
    scope = {bare_name(t) for t in allowed}
    columns: dict[str, dict[str, Any]] = {}
    rows: dict[str, int | None] = {}
    relation_evidence: list[dict[str, Any]] = []
    for table, info in catalog["tables"].items():
        if bare_name(str(table)) not in scope:
            continue
        rows[str(table)] = info.get("rows_estimate")
        for column in info.get("columns") or []:
            profile = _value_profile(column.get("profile"))
            if profile is None:
                continue
            columns[f"{table}.{column.get('name')}"] = {
                "type": column.get("type"), "comment": _db_comment(column), "profile": profile,
                "error": None,
            }
        for rel in info.get("relations") or []:
            if not isinstance(rel, Mapping) or rel.get("kind") != "p1" or rel.get("error"):
                continue
            parent = rel.get("to")
            if not parent or bare_name(str(parent)) not in scope:
                continue
            pairs = [p for p in rel.get("columns") or [] if isinstance(p, (list, tuple))]
            relation_evidence.append({
                "child": rel.get("from") or table, "parent": parent,
                "child_columns": [p[0] for p in pairs], "parent_columns": [p[1] for p in pairs],
                "origin": rel.get("origin"), "overlap": rel.get("overlap"),
                "accepted": bool(rel.get("accepted")),
            })
    rules, entity = inference.rules_and_entity_candidates(columns)
    out: dict[str, Any] = {}
    entity_keys = inference.entity_keys_asset(entity, rows)
    if entity_keys:
        out["entity_keys"] = entity_keys
    relationships = inference.accepted_relationships(relation_evidence)
    if relationships:
        out["relationships"] = relationships
    if rules:
        out["query_rules"] = rules
    return out


def synonym_seeds(catalog: Mapping[str, Any]) -> dict[str, Any] | None:
    """DB 주석 근거 유사어 시드(`_apply_seeds`의 data 모양 · `column_values` 없음).

    근거가 0이면 None.
    """
    comments = {
        f"{table}.{column.get('name')}": text
        for table, info in catalog["tables"].items()
        for column in info.get("columns") or []
        if (text := _db_comment(column))
    }
    synonyms = inference.comment_synonyms(comments)
    if not synonyms:
        return None
    return {"version": "1.0", "db_id": DB_ID, "source_tag": "operator", "column_synonyms": synonyms}


def schema_cache_dict(catalog: Mapping[str, Any]) -> dict[str, Any]:
    """반출 카탈로그 → 앱 스키마 캐시 `schema` 본문(기본키 · 선언 FK · 타입 · 표본 행 없음)."""
    tables: dict[str, Any] = {}
    relationships: list[dict[str, str]] = []
    for table, info in catalog["tables"].items():
        keys = {str(k) for k in info.get("key") or []}
        references: dict[str, str] = {}
        for rel in info.get("relations") or []:
            if isinstance(rel, Mapping) and rel.get("kind") == "declared" and rel.get("to"):
                for pair in rel.get("columns") or []:
                    target = f"{rel['to']}.{pair[1]}"
                    if str(pair[0]) not in references:
                        references[str(pair[0])] = target
                        relationships.append({"from": f"{table}.{pair[0]}", "to": target})
        tables[str(table)] = {
            "columns": [
                {
                    "name": str(col.get("name")),
                    "type": str(col.get("type") or ""),
                    "nullable": bool(col.get("nullable", True)),
                    "primary_key": str(col.get("name")) in keys,
                    "foreign_key": str(col.get("name")) in references,
                    "references": references.get(str(col.get("name"))),
                }
                for col in info.get("columns") or []
            ],
            "row_count_estimate": info.get("rows_estimate"),
            "sample_data": [],
        }
    return {"tables": tables, "relationships": relationships}


def render_schema_cache(catalog: Mapping[str, Any]) -> str:
    """`PersistentSchemaCache.save`(빈 임시 디렉터리 — 기존 부가 필드 이월 없음) + DB 설명."""
    description = (catalog.get("db_description") or {}).get("text")
    with tempfile.TemporaryDirectory() as tmp:
        cache = PersistentSchemaCache(cache_dir=tmp)
        if not cache.save(DB_ID, schema_cache_dict(catalog)):
            raise BuildError("스키마 캐시 저장 실패")
        if description:
            cache.update_field(DB_ID, "_db_description", str(description))
        return (Path(tmp) / f"{DB_ID}_schema.json").read_text(encoding="utf-8")


def schema_cache_diff(old_text: str | None, new_text: str) -> list[str]:
    """두 스키마 캐시의 차이 요약(저장 시각 제외 · 값 없이 키·건수만)."""
    if old_text is None:
        return ["기존 파일 없음"]
    old, new = json.loads(old_text), json.loads(new_text)
    out: list[str] = []
    for key in sorted(set(old) | set(new)):
        if key in _TIMESTAMP_KEYS or key == "schema" or old.get(key) == new.get(key):
            continue
        out.append(f"최상위 `{key}` 다름")
    old_tables = (old.get("schema") or {}).get("tables") or {}
    new_tables = (new.get("schema") or {}).get("tables") or {}
    if set(old_tables) != set(new_tables):
        out.append(
            f"테이블 집합 다름(기존 {len(old_tables)} · 새 {len(new_tables)})"
        )
    changed = [
        t for t in sorted(set(old_tables) & set(new_tables)) if old_tables[t] != new_tables[t]
    ]
    if changed:
        out.append(f"내용이 다른 테이블 {len(changed)}개(예: {', '.join(changed[:3])})")
    old_rel = (old.get("schema") or {}).get("relationships")
    new_rel = (new.get("schema") or {}).get("relationships")
    if old_rel != new_rel:
        out.append(f"relationships 다름({len(old_rel or [])} → {len(new_rel or [])})")
    return out


# ──────────────────────────────────────────────
# 머리말 · 렌더
# ──────────────────────────────────────────────


def profile_header(
    run_id: str, generated_at: str, *, counts: Mapping[str, int], dropped: Sequence[str],
    evidence: Mapping[str, Any], p2: Mapping[str, Any] | None = None,
    knowledge: Mapping[str, Any] | None = None,
) -> str:
    """프로필 머리말 — 출처 run · 빌더 · 생성 시각 · 직접 커밋 경로 · 자산별 근거 종류.

    `p2`(`build_p2` 결과)가 있으면 쓰는 P2 자산 줄을, `knowledge`(지식 오버레이)가 있으면 원천 run ·
    자산별 건수 · 검증 방식 줄을 더한다(둘 다 없으면 W3 머리말과 바이트 동일).
    """
    seed_count = sum(n for origin, n in counts.items() if origin != ORIGIN_MANUAL)
    lines = [
        "# plans/140 W3 외부망 자산 빌더(python -m scripts.itam_bench --build-assets)",
        f"# 출처 반출 run {run_id} · 생성 {generated_at}",
        "# 직접 커밋 경로 — D-311 · 내부망 승인·실행 검증 없음",
        "# 자산별 근거",
        "#   allowed_tables     반출 승인 프로필"
        + (f"(기본 제외 {', '.join(dropped)} — 계정 비밀번호 칸 · plans/139)" if dropped else ""),
        f"#   table_definitions  시드 초안 {seed_count}건(이름 기반 「(추정)」 포함 · D-308)"
        f" + 반출 내부망 편집분(manual) {counts.get(ORIGIN_MANUAL, 0)}건",
        "#   → table_definitions가 있으면 정의 기반 선별(최대 8개 · D-308)이 켜진다."
        " 없으면 조회 대상이",
        "#     프롬프트에 전부 실려 입력 한도를 넘을 수 있다(plans/139)",
    ]
    if "entity_keys" in evidence:
        lines.append("#   entity_keys        P1 값 형식 비율")
    if "relationships" in evidence:
        lines.append("#   relationships      P1 값 겹침(채택분)")
    if "query_rules" in evidence:
        lines.append("#   query_rules        P1 값 형식 비율")
    if p2 and p2.get("query_examples"):
        lines.append(
            "#   query_examples     P2 LLM 초안(모의 DB 실행 성공분 · 치환 코드 리터럴 제외 "
            f"{int(p2.get('excluded_substituted') or 0)}건)"
        )
    if p2 and p2.get("section"):
        lines.append(
            "#   prompt_template    P2 LLM 초안(구조 검사·섹션 SQL 모의 DB 실행 통과) → "
            f"{SECTION_REL}"
        )
    if knowledge:
        lines += knowledge_header_lines(knowledge)
    lines.append("# 코드값·코드 라벨은 반입 뒤 내부망 P1 승인으로 넣는다(이 파일에 없음)")
    return "\n".join(lines) + "\n"


def knowledge_header_lines(knowledge: Mapping[str, Any]) -> list[str]:
    """지식 오버레이 근거 줄 — 원천 run · 검증 방식 · 자산별 건수(0건 자산은 뺀다)."""
    c = knowledge["counts"]
    lines = [
        "# 지식 오버레이(plans/141 W4 · D-314 ②) — 원천 testdata/itam_bench/closed/knowledge"
        " 검증 통과 active 항목만",
        f"#   원천 근거 run {', '.join(knowledge['runs']) or '-'}"
        f" · 검증 {knowledge['verification']}",
    ]
    rows = (
        ("query_guide", "query_guide", "K1 가이드"),
        ("query_examples", "query_examples", "K2 예시"),
        ("query_rules", "query_rules", "K6 정의 kind 파생(P1 규칙 우선 합집합)"),
        ("prompt_section", "prompt_template", f"K4 규칙 섹션 → {SECTION_REL}"),
        ("column_descriptions", "descriptions", f"K3 설명 → {DESCRIPTIONS_REL}"),
        ("synonyms", "synonyms", f"K3 유사어 → {SYNONYM_SEED_REL}"),
        ("query_templates", "templates", f"K8 조립 템플릿 → {TEMPLATES_REL}"),
    )
    for key, label, text in rows:
        if c.get(key):
            lines.append(f"#   {label:<18} {text} {c[key]}건")
    return lines


def knowledge_file_header(title: str, run_id: str, generated_at: str,
                          knowledge: Mapping[str, Any]) -> str:
    """지식 오버레이 파일(K3 설명 · K4 섹션 · K8 템플릿) 머리말."""
    return (
        f"{KNOWLEDGE_FILE_MARKER}(python -m scripts.itam_bench --build-assets)"
        f" — {title} — {DB_ID}\n"
        f"# 출처 반출 run {run_id} · 원천 근거 run {', '.join(knowledge['runs']) or '-'}"
        f" · 생성 {generated_at}\n"
        "# 직접 커밋 경로 — D-314 ② · 원천 testdata/itam_bench/closed/knowledge"
        " 검증 통과 active 항목만"
        f" · 검증 {knowledge['verification']}\n"
    )


def seeds_header(run_id: str, generated_at: str, knowledge: Mapping[str, Any] | None = None) -> str:
    """유사어 시드 머리말(`knowledge`가 유사어를 실으면 원천 줄을 더한다)."""
    text = (
        f"# plans/140 W3 외부망 자산 빌더 · 출처 반출 run {run_id} · 생성 {generated_at}\n"
        "# 직접 커밋 경로 — D-311 · 근거: 반출 컬럼 DB 주석 라벨\n"
    )
    if knowledge and knowledge["counts"].get("synonyms"):
        text += (
            "# 지식 오버레이(plans/141 W4 · D-314 ②) — K3 유사어 "
            f"{knowledge['counts']['synonyms']}건 · 원천 근거 run "
            f"{', '.join(knowledge['runs']) or '-'} · 검증 {knowledge['verification']}"
            " · 기존 낱말 우선\n"
        )
    return text


def render_section_file(run_id: str, generated_at: str, p2: Mapping[str, Any]) -> str:
    """DB 전용 규칙 섹션 파일 — `AssetFileStore.apply`와 같은 형식(주석 머리말 + ``db_id``·
    ``section``·``generated`` · 로컬 샌드박스 표지 없음)."""
    header = (
        "# plans/140 W5 외부망 자산 빌더(python -m scripts.itam_bench --build-assets --p2)"
        f" — DB 전용 규칙 섹션 — {DB_ID}\n"
        f"# 출처 반출 run {run_id} · 생성 {generated_at}\n"
        "# 직접 커밋 경로 — D-311 · 근거: P2 LLM 초안 · 구조 검사(길이·중괄호·펜스·식별자 실존) · "
        "섹션 SQL 모의 DB 실행 성공 · 치환 코드 리터럴 없음\n"
    )
    body = {
        "db_id": DB_ID,
        "section": p2["section"],
        "generated": {
            "builder": "plans/140 W5", "run_id": run_id,
            "snapshot_hash": p2.get("snapshot_hash"),
        },
    }
    return header + _dump_yaml_exact(body)


def render_knowledge_section_file(
    run_id: str, generated_at: str, knowledge: Mapping[str, Any], snapshot_hash: Any
) -> str:
    """K4 섹션 파일 — `render_section_file`과 같은 본문 형식
    (``db_id``·``section``·``generated``)."""
    body = {
        "db_id": DB_ID,
        "section": knowledge["section"],
        "generated": {"builder": "plans/141 W4", "run_id": run_id, "snapshot_hash": snapshot_hash},
    }
    return knowledge_file_header("DB 전용 규칙 섹션(K4)", run_id, generated_at, knowledge) + (
        _dump_yaml_exact(body)
    )


def render_descriptions_file(
    run_id: str, generated_at: str, knowledge: Mapping[str, Any]
) -> str:
    """K3 설명 정본 파일 — `knowledge_descriptions.load_knowledge_descriptions` 형식."""
    body = {
        "version": DESCRIPTIONS_VERSION,
        "origin": ORIGIN_CLAUDE_CODE,
        "descriptions": dict(knowledge["descriptions"]),
    }
    return knowledge_file_header("컬럼 설명 정본(K3)", run_id, generated_at, knowledge) + (
        _dump_yaml_exact(body)
    )


def render_templates_file(run_id: str, generated_at: str, knowledge: Mapping[str, Any]) -> str:
    """K8 조립 템플릿 파일 — 런타임 형식(``version: 1`` · ``templates``) · 원천 항목 그대로."""
    from src.domain.query_templates import TEMPLATE_FILE_VERSION

    body = {
        "version": TEMPLATE_FILE_VERSION, "templates": [dict(t) for t in knowledge["templates"]],
    }
    return knowledge_file_header("조립 템플릿(K8)", run_id, generated_at, knowledge) + (
        _dump_yaml_exact(body)
    )


# ──────────────────────────────────────────────
# 치환값 차단
# ──────────────────────────────────────────────


#: 적중 위치 경로에 그대로 싣는 키 형식(그 밖의 키·치환값 토큰을 품은 키는 `[#순번]` — 감사 L-5)
_SAFE_PATH_KEY = re.compile(r"^[A-Za-z0-9_.가-힣]+$")
_LATIN_WORD = "A-Za-z0-9_"


def _boundary(ch: str, side: str) -> str:
    """값 끝 글자 군에 맞는 토큰 경계(라틴·숫자는 앞뒤 비라틴·숫자·밑줄 · 한글은 앞뒤 비한글).

    숫자로 시작·끝나는 값은 날짜·시각·소수·IP 조각(`2026-10`·`09:04`·`0.97`·`10.0.0.1`)의 일부로는
    보지 않는다 — 생성 시각·비율 같은 빌더 산출 수가 우연히 겹쳐 거부되지 않게 한다.
    """
    if ch.isascii() and (ch.isalnum() or ch == "_"):
        if side == "left":
            return f"(?<![{_LATIN_WORD}])" + (r"(?<!\d[.:/-])" if ch.isdigit() else "")
        return f"(?![{_LATIN_WORD}])" + (r"(?![.:/-]\d)" if ch.isdigit() else "")
    if "가" <= ch <= "힣":
        return "(?<![가-힣])" if side == "left" else "(?![가-힣])"
    return ""


def _token_patterns(values: Iterable[str]) -> list[re.Pattern[str]]:
    """치환값 → 토큰 경계 대조 정규식(경계 종류별 한 개 · 긴 값 먼저)."""
    groups: dict[tuple[str, str], list[str]] = {}
    for value in values:
        if value:
            key = (_boundary(value[0], "left"), _boundary(value[-1], "right"))
            groups.setdefault(key, []).append(value)
    return [
        re.compile(
            left + "(?:" + "|".join(re.escape(v) for v in sorted(vs, key=len, reverse=True))
            + ")" + right
        )
        for (left, right), vs in groups.items()
    ]


def _token_hit(text: str, patterns: Sequence[re.Pattern[str]]) -> bool:
    return any(p.search(text) for p in patterns)


def _leaves(
    value: Any, path: str, patterns: Sequence[re.Pattern[str]]
) -> Iterable[tuple[str, str, str]]:
    """``(경로, 종류, 글)`` — 매핑 키와 문자열 잎 전부. 수·참거짓 스칼라는 빌더가 계산한 지표
    (`priority`·`overlap`·행 수)라 대조하지 않는다. 경로에는 안전한 키만 싣는다(감사 L-5)."""
    if isinstance(value, Mapping):
        for position, (key, item) in enumerate(value.items()):
            text = str(key)
            safe = bool(_SAFE_PATH_KEY.match(text)) and not _token_hit(text, patterns)
            child = f"{path}.{text}" if safe else f"{path}[#{position}]"
            yield child, "키", text
            yield from _leaves(item, child, patterns)
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            yield from _leaves(item, f"{path}[{i}]", patterns)
    elif isinstance(value, str):
        yield path, "스칼라", value


def substitution_hits(
    files: Mapping[str, tuple[str, Mapping[str, Any] | None]], values: set[str]
) -> list[str]:
    """쓸 파일에서 치환값이 나온 **위치만**(값 없음) — 토큰 경계 대조(감사 L-3).

    - 파싱한 본문이 있으면 본문 **전체**의 문자열 잎과 매핑 키를 토큰 단위로 대조한다(칸 한정 없음 ·
      YAML 평문 스칼라·숫자 리터럴·IN 목록·백틱 포함). 텍스트의 나머지는 빌더 머리 주석(출처 run·
      시각·건수)과 YAML 문법뿐이라 다시 보지 않는다.
    - 파싱한 본문이 없으면 텍스트를 줄마다 토큰 단위로 대조한다.
    - 토큰 경계: 라틴·숫자 값은 앞뒤가 `[A-Za-z0-9_]`가 아닐 때, 한글 값은 앞뒤가 한글이 아닐 때
      (숫자 값은 날짜·시각·소수 조각 제외 — `_boundary`).

    Args:
        files: ``{파일 이름: (텍스트, 파싱한 본문 또는 None)}``
        values: 치환값·라벨 집합(식별자와 같은 값은 호출부가 미리 뺀다 — `blocked_values`)
    """
    hits: list[str] = []
    patterns = _token_patterns(v for v in values if v)
    if not patterns:
        return hits
    for name, (text, data) in files.items():
        if data is None:
            for number, line in enumerate(text.splitlines(), start=1):
                if _token_hit(line, patterns):
                    hits.append(f"{name}:{number} 값 토큰")
            continue
        for path, kind, leaf in _leaves(data, "", patterns):
            if _token_hit(leaf, patterns):
                hits.append(f"{name} {path.lstrip('.') or '(최상위)'} {kind}")
    return hits


def blocked_values(
    samples: Mapping[str, Any] | None, catalog: Mapping[str, Any],
    input_texts: Iterable[str] = (),
) -> tuple[set[str], int]:
    """빌더 차단 대조 집합 — 치환값·라벨에서 카탈로그 식별자(테이블·컬럼 이름)와 같은(casefold) 값과
    식별자 안에 토큰으로 든 값(`주소1`의 `1`처럼 한글·라틴이 섞인 이름)을 뺀다 — 식별자는 산출물
    곳곳(스키마 캐시·`key_columns`·`related`)에 그대로 나오므로 이름을 치환값으로 오검출하지 않게
    한다(감사 L-3). 반출 카탈로그 글(`input_texts` — 내부망에서 치환과 무관하게 만든
    정의·주석·설명)에 이미 토큰으로 있는 값도 뺀다 — 우연 일치다. 외부망에서 사람이 고치는
    시드 정의·유사어 시드는 넣지 않는다(치환값을 옮겨 적는 경로가 바로 막을 대상이다). 대조
    대상(산출 본문)은 줄이지 않는다.

    Returns:
        ``(대조 집합, 식별자·카탈로그 글 때문에 뺀 수)``
    """
    values = substituted_values(samples)
    names = {
        str(name)
        for table, columns in catalog_columns(catalog).items()
        for name in (table, *columns)
    }
    folded = {name.casefold() for name in names}
    joined = "\n".join([*sorted(names), *input_texts])
    kept = {
        v for v in values
        if v.casefold() not in folded and not _token_hit(joined, _token_patterns([v]))
    }
    return kept, len(values) - len(kept)


def catalog_texts(catalog: Mapping[str, Any]) -> list[str]:
    """`blocked_values`의 우연 일치 기준 글 — 반출 카탈로그 본문(치환 파일 제외)."""
    return [json.dumps(catalog, ensure_ascii=False, default=str)]


# ──────────────────────────────────────────────
# P2 — LLM 초안 · 결정적 검증 · 치환값 제외 (plans/140 W5)
# ──────────────────────────────────────────────


def _p2_snapshot(catalog: Mapping[str, Any], allowed: Sequence[str]) -> dict[str, Any]:
    """반출 카탈로그 → 스냅샷(`build_snapshot`) — 조회 대상 테이블만(기본 제외 테이블은 예시
    SQL이 참조할 수 없게 스키마에서 뺀다)."""
    snapshot = build_snapshot(schema_cache_dict(catalog))
    scope = {bare_name(t) for t in allowed}
    tables = {
        t: data for t, data in (snapshot.get("tables") or {}).items() if bare_name(t) in scope
    }
    return {**snapshot, "tables": tables, "table_count": len(tables)}


def _p2_comments(catalog: Mapping[str, Any]) -> dict[str, str]:
    """요약용 DB 주석 — ``{테이블: 주석, "테이블.컬럼": 주석}``(DB 주석 출처만)."""
    out: dict[str, str] = {}
    for table, info in catalog["tables"].items():
        if text := _db_comment(info):
            out[str(table)] = text
        for column in info.get("columns") or []:
            if text := _db_comment(column):
                out[f"{table}.{column.get('name')}"] = text
    return out


async def build_p2(
    export: Mapping[str, Any],
    *,
    llm: Any,
    client: Any,
    sql_checker: Callable[[str, Mapping[str, Any], str, str], list[str]] | None,
    code_values: set[str],
    engine: str = P2_ENGINE,
    keep_excluded: bool = False,
) -> dict[str, Any]:
    """P2 잡(`run_asset_llm`)과 같은 조각으로 쿼리 예시·DB 전용 규칙 섹션을 만든다(쓰기 없음).

    반출 카탈로그(`load_export`의 카탈로그)에서 스냅샷·근거 자산(`evidence_assets`)·DB 주석으로
    요약 → LLM 2회 → 예시는 실행 성공분, 섹션은 구조 검사·SQL 실행 통과분만 → 치환 코드값이
    리터럴로 나오는 예시는 빼고 섹션은 통째로 버린다.

    Args:
        export: 반출 카탈로그
        llm: ``ainvoke`` 가능한 LLM
        client: 연결된 DB 클라이언트(``execute_sql``)
        sql_checker: LLM SQL 검증기(없으면 실행하지 않는다 — 서비스와 같은 닫힌 실패)
        code_values: 치환 코드값·라벨 집합(`substituted_values` · 1회차 반출이면 빈 집합)
        engine: SQL 엔진(행 제한 절)
        keep_excluded: 기본 제외 테이블을 조회 대상에 남긴다

    Returns:
        ``{"query_examples", "section"(없으면 None), "checks", "excluded_substituted",
        "section_substituted", "snapshot_hash"}`` — ``checks``에는 LLM 원문이 들어 있으니 출력하지
        않는다(수만 출력)
    """
    from src.prompts.asset_generation import PROMPT_SECTION_PROMPT, QUERY_EXAMPLES_PROMPT
    from src.schema_cache.asset_generation_service import (
        EXAMPLE_COUNT,
        SECTION_MAX_CHARS,
        SQL_CHECK_LIMIT,
        _ask,
        _SqlCheck,
        _validate_examples,
        _validate_section,
        build_asset_summary,
    )
    from src.schema_cache.db_structure_service import schema_dict_from_snapshot
    from src.utils.sql_dialect import row_limit_clause

    allowed, _ = allowed_tables(export, keep_excluded=keep_excluded)
    snapshot = _p2_snapshot(export, allowed)
    assets = {"allowed_tables": allowed, **evidence_assets(export, allowed)}
    summary = build_asset_summary(snapshot, assets, _p2_comments(export))
    limit_clause = row_limit_clause(engine, SQL_CHECK_LIMIT)

    examples_raw = await _ask(llm, QUERY_EXAMPLES_PROMPT.format(
        engine=engine, count=EXAMPLE_COUNT, summary=summary, limit_clause=limit_clause,
    ))
    section_raw = await _ask(llm, PROMPT_SECTION_PROMPT.format(
        engine=engine, summary=summary, limit_clause=limit_clause, max_chars=SECTION_MAX_CHARS,
    ))
    check = _SqlCheck(client, sql_checker, schema_dict_from_snapshot(snapshot), engine, DB_ID)
    examples, example_checks = await _validate_examples(check, examples_raw)
    section, section_check = await _validate_section(check, section_raw, snapshot)

    kept: list[dict[str, str]] = []
    excluded = 0
    for example in examples:
        text = f"{example['question']}\n{example['sql']}"
        if substitution_hits({"example": (text, {"query_examples": [example]})}, code_values):
            excluded += 1
        else:
            kept.append(example)
    section_substituted = bool(
        section_check["passed"] and substitution_hits({"section": (section, None)}, code_values)
    )
    return {
        "query_examples": kept,
        "section": section if section_check["passed"] and not section_substituted else None,
        "checks": {"query_examples": example_checks, "prompt_template": section_check},
        "excluded_substituted": excluded,
        "section_substituted": section_substituted,
        "snapshot_hash": snapshot.get("hash"),
    }


def p2_billing_refusal(cfg: Any) -> str | None:
    """두 LLM 평면 중 과금 평면이 있으면 거부 사유(없으면 None) — 키 존재와 무관(D-127 · D-222).

    판정은 시나리오 사전 점검의 단일 정의(`scripts.scenario.preflight.external_planes`)를 쓴다.
    설정을 못 읽은 평면은 과금으로 본다.
    """
    from scripts.scenario.preflight import external_planes

    worker = str(getattr(getattr(cfg, "llm", None), "provider", "") or "").strip().lower()
    orchestrator = (
        str(getattr(getattr(cfg, "orchestrator", None), "provider", "") or "").strip().lower()
    )
    planes = external_planes(worker or "unknown()", orchestrator or "unknown()")
    if not planes:
        return None
    return (
        f"과금 평면 {planes} — P2 LLM 초안을 만들지 않는다. 두 평면을 로컬 MLX 등 비과금으로 "
        "바꿔 실행한다(D-127)"
    )


@dataclass
class P2Deps:
    """P2 실행 의존성 — LLM 팩토리 · DB 클라이언트 팩토리(async 컨텍스트) · SQL 검증기 · 엔진."""

    llm_factory: Callable[[], Any]
    client_factory: Callable[[], AbstractAsyncContextManager[Any]]
    sql_checker: Callable[[str, Mapping[str, Any], str, str], list[str]] | None
    engine: str = P2_ENGINE


def default_p2_deps(cfg: Any) -> P2Deps:
    """앱 설정의 기본 의존성 — `create_llm(cfg)` · `itam` 소스 클라이언트 · 「DB 구조」 탭 검증기.

    서비스 기본값(`AdminServiceBase._default_llm`·`_default_client`)과 조립부 검증기
    (`asset_sql_checker`)를 그대로 쓴다.
    """
    from src.api.routes.db_structure import asset_sql_checker
    from src.db import get_db_client
    from src.llm import create_llm

    return P2Deps(
        llm_factory=lambda: create_llm(cfg),
        client_factory=lambda: get_db_client(cfg, db_id=DB_ID),
        sql_checker=asset_sql_checker,
    )


async def _run_p2(
    catalog: Mapping[str, Any], code_values: set[str], deps: P2Deps, *, keep_excluded: bool
) -> dict[str, Any]:
    """DB에 연결해 연결 상태를 확인한 뒤 `build_p2`를 돈다.

    Raises:
        BuildError: 연결 실패·연결 비정상(`EXIT_INPUT` — 침묵 폴백 없음)
    """
    async with AsyncExitStack() as stack:
        try:
            client = await stack.enter_async_context(deps.client_factory())
            healthy = bool(await client.health_check())
        except Exception as e:  # noqa: BLE001 — 연결 실패는 사유로 끝낸다(침묵 폴백 금지)
            raise BuildError(
                f"{DB_ID} 소스 연결 실패: {type(e).__name__}: {e}", EXIT_INPUT
            ) from e
        if not healthy:
            raise BuildError(
                f"{DB_ID} 소스 연결 상태 비정상(health_check 실패) — 모의 DB·MCP 서버를 확인한다",
                EXIT_INPUT,
            )
        return await build_p2(
            catalog, llm=deps.llm_factory(), client=client, sql_checker=deps.sql_checker,
            code_values=code_values, engine=deps.engine, keep_excluded=keep_excluded,
        )


# ──────────────────────────────────────────────
# 실행
# ──────────────────────────────────────────────


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def preserve_local_sandbox(repo_root: Path) -> str:
    """현 프로필이 로컬 샌드박스 승인본이면 보존본으로 바이트 복사(이미 있으면 그대로).

    Returns:
        결과 문구
    """
    current = repo_root / PROFILE_REL
    target = repo_root / LOCAL_SANDBOX_PROFILE_REL
    if target.exists():
        return f"보존본 있음(그대로) — {LOCAL_SANDBOX_PROFILE_REL}"
    if not current.is_file():
        return "현 프로필 없음 — 보존 생략"
    try:
        parsed = yaml.safe_load(current.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        parsed = None
    if not (isinstance(parsed, Mapping) and parsed.get("environment") == LOCAL_SANDBOX_ENVIRONMENT):
        return "현 프로필이 로컬 샌드박스 승인본이 아님 — 보존 생략"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(current, target)
    return f"로컬 샌드박스 승인본 보존 → {LOCAL_SANDBOX_PROFILE_REL}"


def build(
    run_dir: Path, *, keep_excluded: bool = False, repo_root: Path = REPO_ROOT,
    generated_at: str | None = None, p2_result: Mapping[str, Any] | None = None,
    knowledge: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """쓰기 없이 산출물 텍스트와 요약을 만든다(검사 포함).

    `p2_result`(`build_p2` 결과)가 있으면 근거 있는 예시를 프로필 `query_examples`에, 섹션을
    `SECTION_REL`에 더한다(근거 0이면 키·파일 없음). 치환값 차단 검사는 섹션 파일까지 덮는다.
    `knowledge`(`knowledge.overlay_from_result` — 통과한 active 항목만)가 있으면 지식 오버레이를
    더한다(모듈 독스트링). 둘 다 없으면 W3 산출과 바이트 동일하다. `knowledge`가 있으면(검증 수행 —
    통과 0이어도) 철회 반영 대상도 고른다(통과 0이면 산출 파일은 오버레이 없는 빌드와 같다).

    Returns:
        ``{"files": {상대 경로: 텍스트}, "remove": [지울 상대 경로], "summary": {...}}`` —
        표지가 없어 남긴 파일은 ``summary["knowledge_kept"]``

    Raises:
        BuildError: 입력 오류 · 정의 검증 오류 · 치환값 검출
    """
    run_dir = Path(run_dir)
    catalog, samples = load_export(run_dir)
    run_id = run_dir.name
    stamp = generated_at or time.strftime("%Y-%m-%dT%H:%M:%S%z")

    allowed, dropped = allowed_tables(catalog, keep_excluded=keep_excluded)
    seed_path = repo_root / SEED_DEFINITIONS_REL
    if not seed_path.is_file():
        raise BuildError(f"시드 정의가 없습니다: {SEED_DEFINITIONS_REL}", EXIT_INPUT)
    seed_doc = yaml.safe_load(seed_path.read_text(encoding="utf-8"))
    definitions, def_errors, def_counts = table_definitions(seed_doc, catalog)
    if def_errors:
        detail = "; ".join(f"{t}: {msgs[0]}" for t, msgs in list(def_errors.items())[:5])
        raise BuildError(f"테이블 정의 검증 오류 {len(def_errors)}건 — 쓰지 않습니다({detail})")

    evidence = evidence_assets(catalog, allowed)
    profile: dict[str, Any] = {"source": MANUAL_SOURCE, "allowed_tables": allowed}
    profile.update(evidence)
    validated = knowledge is not None
    withdrawn_synonyms = (knowledge or {}).get("withdrawn_synonyms") or {}
    if knowledge is not None and not _overlay_size(knowledge):
        knowledge = None  # 통과 0 — 산출은 오버레이 없는 빌드와 같다(철회 반영만)
    k = knowledge or {}
    if k.get("query_rules"):  # K6 — P1 규칙 우선 합집합
        rules = list(profile.get("query_rules") or [])
        profile["query_rules"] = rules + [r for r in k["query_rules"] if r not in rules]
    if k.get("query_guide"):
        profile["query_guide"] = k["query_guide"]
    examples = [dict(e) for e in (p2_result or {}).get("query_examples") or []]
    seen = {str(e.get("question") or "").strip() for e in examples}
    examples += [dict(e) for e in k.get("query_examples") or [] if e["question"] not in seen]
    if examples:
        profile["query_examples"] = examples
    profile[PROFILE_KEY] = definitions
    profile_text = profile_header(
        run_id, stamp, counts=def_counts, dropped=dropped, evidence=evidence, p2=p2_result,
        knowledge=knowledge,
    ) + _dump_yaml_exact(profile)

    files: dict[str, str] = {
        str(SCHEMA_SEED_REL): render_schema_cache(catalog),
        str(PROFILE_REL): profile_text,
    }
    parsed: dict[str, tuple[str, Mapping[str, Any] | None]] = {
        str(SCHEMA_SEED_REL): (files[str(SCHEMA_SEED_REL)], None),
        str(PROFILE_REL): (profile_text, profile),
    }
    remove: list[str] = []
    seeds = synonym_seeds(catalog)
    p1_words = {
        key: set(words) for key, words in ((seeds or {}).get("column_synonyms") or {}).items()
    }
    if k.get("synonyms"):  # K3 — DB 주석 근거 낱말 우선 합집합
        if seeds is None:
            seeds = _empty_seeds()
        for key, words in k["synonyms"].items():
            merged = seeds["column_synonyms"].setdefault(key, [])
            merged += [w for w in words if w not in merged]
    # 철회 반영 — 원천에 있으나 이번에 통과하지 못한 낱말(DB 주석 근거 낱말은 남긴다)
    retract = {
        key: {w for w in words if w not in p1_words.get(key, set())}
        for key, words in withdrawn_synonyms.items()
    }
    existing = repo_root / SYNONYM_SEED_REL
    base_synonyms: Mapping[str, Any] = {}
    if existing.is_file() and (seeds is not None or retract):
        base = yaml.safe_load(existing.read_text(encoding="utf-8")) or {}
        base_synonyms = base.get("column_synonyms") or {}
    retracting = any(
        w in retract.get(key, ()) for key, words in base_synonyms.items() for w in words or []
    )
    if seeds is None and retracting:
        seeds = _empty_seeds()
    if seeds is not None:
        for key, words in base_synonyms.items():
            merged = seeds["column_synonyms"].setdefault(key, [])
            merged[:0] = [w for w in words if w not in merged and w not in retract.get(key, ())]
        if retracting:
            seeds["column_synonyms"] = {
                key: words for key, words in seeds["column_synonyms"].items() if words
            }
            if not seeds["column_synonyms"]:
                seeds = None
                remove.append(str(SYNONYM_SEED_REL))
    if seeds is not None:
        seeds_text = seeds_header(run_id, stamp, knowledge) + _dump_yaml_exact(seeds)
        files[str(SYNONYM_SEED_REL)] = seeds_text
        parsed[str(SYNONYM_SEED_REL)] = (seeds_text, seeds)
    section_text = None
    if k.get("section"):  # K4가 P2 섹션보다 우선
        section_text = render_knowledge_section_file(
            run_id, stamp, k, _p2_snapshot(catalog, allowed).get("hash")
        )
    elif p2_result and p2_result.get("section"):
        section_text = render_section_file(run_id, stamp, p2_result)
    if section_text is not None:
        files[str(SECTION_REL)] = section_text
        parsed[str(SECTION_REL)] = (section_text, yaml.safe_load(section_text))
    for rel, key, render in (
        (DESCRIPTIONS_REL, "descriptions", render_descriptions_file),
        (TEMPLATES_REL, "templates", render_templates_file),
    ):
        if k.get(key):
            text = render(run_id, stamp, k)
            files[str(rel)] = text
            parsed[str(rel)] = (text, yaml.safe_load(text))
    kept: list[str] = []
    if validated:  # 철회 반영 — 이번에 쓰지 않는 지식 산출 파일
        for rel in (SECTION_REL, DESCRIPTIONS_REL, TEMPLATES_REL):
            path = repo_root / rel
            if str(rel) in files or not path.is_file():
                continue
            (remove if _overlay_written(path) else kept).append(str(rel))

    schema_cache = json.loads(files[str(SCHEMA_SEED_REL)])
    for key in _TIMESTAMP_KEYS:  # 저장 시각은 빌더가 찍는 값이다
        schema_cache.pop(key, None)
    parsed[str(SCHEMA_SEED_REL)] = (files[str(SCHEMA_SEED_REL)], schema_cache)
    blocked, identifier_skipped = blocked_values(samples, catalog, catalog_texts(catalog))
    hits = substitution_hits(parsed, blocked)
    if hits:
        raise BuildError(
            f"치환 코드값이 커밋 대상 파일에 {len(hits)}곳 나옵니다 — 쓰지 않습니다(위치: "
            + "; ".join(hits[:10]) + ")"
        )

    old_cache = repo_root / SCHEMA_SEED_REL
    summary = {
        "run_id": run_id,
        "tables": len(catalog["tables"]),
        "columns": sum(len(c) for c in catalog_columns(catalog).values()),
        "allowed_tables": len(allowed),
        "excluded": dropped,
        "table_definitions": len(definitions),
        "definition_origins": def_counts,
        "definition_errors": len(def_errors),
        "entity_keys": len((evidence.get("entity_keys") or {}).get("keys") or []),
        "relationships": len(evidence.get("relationships") or []),
        "query_rules": len(evidence.get("query_rules") or []),
        "synonym_seeds": len((seeds or {}).get("column_synonyms") or {}),
        "code_samples": samples is not None,
        "code_samples_identifier_skipped": identifier_skipped,
        **({"p2": {
            "query_examples": len(p2_result.get("query_examples") or []),
            "excluded_substituted": int(p2_result.get("excluded_substituted") or 0),
            "section": bool(p2_result.get("section")),
            "section_substituted": bool(p2_result.get("section_substituted")),
        }} if p2_result is not None else {}),
        **({"knowledge": {
            **dict(knowledge["counts"]),
            "runs": list(knowledge["runs"]),
            "verification": knowledge["verification"],
            "p2_section_replaced": bool(k.get("section") and (p2_result or {}).get("section")),
        }} if knowledge is not None else {}),
        "schema_cache_diff": schema_cache_diff(
            old_cache.read_text(encoding="utf-8") if old_cache.is_file() else None,
            files[str(SCHEMA_SEED_REL)],
        ),
        "knowledge_kept": kept,
    }
    return {"files": files, "remove": remove, "summary": summary}


def _overlay_size(knowledge: Mapping[str, Any]) -> int:
    from .knowledge import overlay_size

    return overlay_size(knowledge)


def _empty_seeds() -> dict[str, Any]:
    return {"version": "1.0", "db_id": DB_ID, "source_tag": "operator", "column_synonyms": {}}


def _overlay_written(path: Path) -> bool:
    """머리 주석 첫 줄이 지식 오버레이 표지인가(읽지 못하면 False — 지우지 않는다)."""
    try:
        with path.open(encoding="utf-8") as f:
            return f.readline().startswith(KNOWLEDGE_FILE_MARKER)
    except (OSError, UnicodeDecodeError):
        return False


def knowledge_overlay(
    run_dir: Path,
    *,
    repo_root: Path,
    knowledge_dir: Path,
    static_only: bool,
    deps: Any,
    keep_excluded: bool,
) -> dict[str, Any] | None:
    """원천 디렉터리를 검증해 통과한 active 항목만 오버레이 재료로 돌려준다(쓰기 없음).

    대조 기준은 이번 빌드와 같다 — 반출 카탈로그 · 조회 대상 · 병합한 정의. 치환값 대조는 이번
    반출과 원천 근거 run(반출 run 디렉터리의 형제)의 `code_samples.yaml`, 근거 리터럴 대조도 같은
    run들(`knowledge.load_evidence_literals`). 사유는 모두 출력한다
    (침묵 없음).

    Returns:
        오버레이 재료(`knowledge.overlay_from_result` — 통과 항목 0이어도 돌려준다: 검증을 했으니
        `build`가 철회를 반영한다) 또는 None — 원천 없음 · 모의 DB 미연결(정적 전용 아님) — 검증
        미수행이라 아무것도 지우지 않는다

    Raises:
        BuildError: 반출·시드 입력 오류(`build`와 같은 검사)
    """
    from . import knowledge as kn

    if not Path(knowledge_dir).is_dir():
        print(f"  지식 오버레이: 원천 디렉터리 없음({knowledge_dir}) — 종전 산출 그대로")
        return None
    catalog, samples = load_export(Path(run_dir))
    allowed, _ = allowed_tables(catalog, keep_excluded=keep_excluded)
    seed_doc = yaml.safe_load((repo_root / SEED_DEFINITIONS_REL).read_text(encoding="utf-8"))
    definitions = table_definitions(seed_doc, catalog)[0]
    schema = schema_cache_dict(catalog)
    runs = kn.evidence_runs(Path(knowledge_dir))
    results_root = Path(run_dir).parent
    code_values = blocked_values(samples, catalog, catalog_texts(catalog))[0]
    code_values |= kn.load_code_values(runs, results_root)[0]
    code_samples = kn.code_samples_map(samples)
    for key, values in kn.load_code_samples(runs, results_root).items():
        merged = code_samples.setdefault(key, [])
        merged += [v for v in values if v not in merged]
    result, reason = asyncio.run(kn._run_with_db(
        Path(knowledge_dir), deps, static_only,
        catalog=schema, allowed=allowed, definitions=definitions,
        code_values=code_values, code_samples=code_samples,
        evidence_literals=kn.load_evidence_literals(runs | {Path(run_dir).name}, results_root)[0],
    ))
    print(f"  지식 오버레이 검증 — 원천 {knowledge_dir}")
    kn.print_summary(result, reason=reason)
    if reason and not static_only:
        print(
            f"  지식 오버레이: 건너뜀 — 모의 DB 미연결({reason}). 정적 검사 통과분만 쓰려면 "
            "--knowledge-static-only"
        )
        return None
    overlay = kn.overlay_from_result(
        Path(knowledge_dir), result, kn.schema_columns(schema),
        verification=kn.VERIFIED_STATIC if static_only else kn.VERIFIED_DB,
    )
    if not kn.overlay_size(overlay):
        print("  지식 오버레이: 통과 항목 0 — 종전 산출 그대로(기존 지식 산출 파일은 철회 반영)")
    return overlay


def run_build(
    run_dir: Path, *, install_cache: bool = False, keep_excluded: bool = False,
    repo_root: Path = REPO_ROOT, p2: bool = False, p2_deps: P2Deps | None = None,
    knowledge_dir: Path | None = None, knowledge_static_only: bool = False,
    knowledge_deps: Any = None,
) -> int:
    """반출 run으로 자산을 만들어 쓰고 요약을 출력한다 → 종료 코드(0 성공 · 1 거부 · 2 입력·연결
    오류).

    Args:
        run_dir: 반출 run 디렉터리(`schema_catalog.yaml` 보유)
        install_cache: `.cache/schema/itam_schema.json`에도 쓴다(기존 파일 시각 접미 백업)
        keep_excluded: 기본 제외 테이블(`DEFAULT_EXCLUDED_TABLES`)을 조회 대상에 남긴다
        repo_root: 저장소 루트(테스트는 tmp)
        p2: P2 LLM 초안(쿼리 예시·DB 전용 규칙 섹션)도 만들어 쓴다 — `p2_deps` 필수(과금 평면
            판정은 호출부 `p2_billing_refusal`)
        p2_deps: P2 의존성(`default_p2_deps(cfg)` · 테스트는 가짜)
        knowledge_dir: 지식 원천 디렉터리(기본 ``repo_root/testdata/itam_bench/closed/knowledge``
            — 없으면 오버레이 없음)
        knowledge_static_only: 모의 DB 없이 정적 검사 통과분만 오버레이한다(머리 주석에 표기)
        knowledge_deps: 지식 검증 DB 의존성(`knowledge.default_deps()` · 없으면 정적 전용이 아닐 때
            오버레이를 건너뛴다)
    """
    from .knowledge import KNOWLEDGE_DIR_REL

    repo_root = Path(repo_root)
    p2_result: dict[str, Any] | None = None
    overlay: dict[str, Any] | None = None
    try:
        result = build(run_dir, keep_excluded=keep_excluded, repo_root=repo_root)
        if p2:
            if p2_deps is None:
                raise BuildError("--p2 의존성(LLM·DB 클라이언트)이 없습니다")
            # W3 검사(정의 오류·치환값)를 통과한 뒤에만 LLM을 부른다
            catalog, samples = load_export(Path(run_dir))
            p2_result = asyncio.run(_run_p2(
                catalog, blocked_values(samples, catalog, catalog_texts(catalog))[0], p2_deps,
                keep_excluded=keep_excluded,
            ))
            result = build(
                run_dir, keep_excluded=keep_excluded, repo_root=repo_root, p2_result=p2_result
            )
        # 지식 오버레이도 W3 검사를 통과한 뒤에만 검증한다
        overlay = knowledge_overlay(
            run_dir, repo_root=repo_root,
            knowledge_dir=Path(knowledge_dir) if knowledge_dir else repo_root / KNOWLEDGE_DIR_REL,
            static_only=knowledge_static_only, deps=knowledge_deps, keep_excluded=keep_excluded,
        )
        if overlay is not None:
            result = build(
                run_dir, keep_excluded=keep_excluded, repo_root=repo_root, p2_result=p2_result,
                knowledge=overlay,
            )
    except BuildError as e:
        print(f"[build-assets] 거부: {e}")
        return e.exit_code

    preserved = preserve_local_sandbox(repo_root)
    s = result["summary"]
    for rel, text in result["files"].items():
        if rel == str(SCHEMA_SEED_REL) and not s["schema_cache_diff"]:
            continue  # 저장 시각만 다르면 다시 쓰지 않는다(커밋 파일 잡음 방지)
        _atomic_write(repo_root / rel, text)
    for rel in result["remove"]:
        (repo_root / rel).unlink(missing_ok=True)
    installed = None
    if install_cache:
        target = repo_root / INSTALL_CACHE_REL
        if target.exists():
            backup = target.with_name(f"{target.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
            shutil.copyfile(target, backup)
            installed = f"{INSTALL_CACHE_REL} (백업 {backup.name})"
        else:
            installed = str(INSTALL_CACHE_REL)
        _atomic_write(target, result["files"][str(SCHEMA_SEED_REL)])

    print(f"[build-assets] 반출 run {s['run_id']} — 테이블 {s['tables']} · 컬럼 {s['columns']}")
    print(
        f"  조회 대상 {s['allowed_tables']}"
        + (f" (기본 제외 {', '.join(s['excluded'])})" if s["excluded"] else "")
    )
    print(
        f"  테이블 정의 {s['table_definitions']} (출처 {s['definition_origins']}) · "
        f"검증 오류 {s['definition_errors']}"
    )
    print(
        f"  근거 자산 — entity_keys {s['entity_keys']} · relationships {s['relationships']} · "
        f"query_rules {s['query_rules']} · 유사어 시드 {s['synonym_seeds']}"
        f" · 치환 코드값 파일 "
        + (
            "있음(차단 검사 통과 · 식별자 때문에 대조 제외 "
            f"{s['code_samples_identifier_skipped']}건)"
            if s["code_samples"] else "없음"
        )
    )
    print(f"  스키마 캐시 차이(저장 시각 제외): {'; '.join(s['schema_cache_diff']) or '없음'}")
    print(f"  {preserved}")
    for rel in result["files"]:
        unchanged = rel == str(SCHEMA_SEED_REL) and not s["schema_cache_diff"]
        print(f"  {'그대로(저장 시각 외 차이 없음)' if unchanged else '씀'}: {rel}")
    if installed:
        print(f"  씀: {installed}")
    for rel in result["remove"]:
        print(f"  지움(철회 반영 — 이번 지식 통과분 0): {rel}")
    for rel in s["knowledge_kept"]:
        print(
            f"  주의: {rel} 남김 — 지식 오버레이 산출 표지가 없다"
            "(P2 · 「DB 구조」 탭 승인본일 수 있다 · 이번 지식 통과분 0)"
        )
    if str(SYNONYM_SEED_REL) not in (*result["files"], *result["remove"]):
        print("  유사어 시드: DB 주석 근거 0 — 파일을 만들지 않음")
    if p2_result is not None:
        _print_p2(p2_result)
    if "knowledge" in s:
        _print_knowledge(s["knowledge"])
    return EXIT_OK


def _print_knowledge(k: Mapping[str, Any]) -> None:
    """지식 오버레이 요약 — 건수만."""
    print(
        f"  지식 오버레이({k['verification']} · 원천 근거 run {', '.join(k['runs']) or '-'}) — "
        f"K1 가이드 {k['query_guide']} · K2 예시 {k['query_examples']} · "
        f"K6 파생 규칙 {k['query_rules']} · K4 섹션 {k['prompt_section']} · "
        f"K3 설명 {k['column_descriptions']} · K3 유사어 {k['synonyms']} · "
        f"K8 템플릿 {k['query_templates']}"
    )
    if not k["query_templates"]:
        print(f"  K8 템플릿: 통과분 0 — {TEMPLATES_REL}을 만들지 않음(런타임 무동작)")
    if k["p2_section_replaced"]:
        print("  P2 DB 전용 규칙 섹션은 K4 섹션으로 대체했다")


def _print_p2(p2_result: Mapping[str, Any]) -> None:
    """P2 요약 — 건수만(질문·SQL·섹션 원문과 치환값은 출력하지 않는다)."""
    checks = p2_result.get("checks") or {}
    candidates = [
        c for c in checks.get("query_examples") or [] if isinstance(c, Mapping) and "sql" in c
    ]
    executed_ok = sum(1 for c in candidates if c.get("error") is None)
    section_check = checks.get("prompt_template") or {}
    kept = len(p2_result.get("query_examples") or [])
    print(
        f"  P2 쿼리 예시 — 후보 {len(candidates)} · 실행 성공 {executed_ok} · "
        f"치환 코드 리터럴 제외 {int(p2_result.get('excluded_substituted') or 0)} · 씀 {kept}"
        + ("" if candidates else " (LLM 응답이 비었거나 JSON 배열이 아님)")
    )
    if kept == 0:
        print("  P2 쿼리 예시: 근거 0 — query_examples 키를 만들지 않음")
    if p2_result.get("section"):
        print(f"  P2 DB 전용 규칙 섹션 — 구조 검사·SQL 실행 통과 · 씀: {SECTION_REL}")
    elif p2_result.get("section_substituted"):
        print("  P2 DB 전용 규칙 섹션: 치환 코드 리터럴 포함 — 통째로 버림(파일을 만들지 않음)")
    else:
        print(
            "  P2 DB 전용 규칙 섹션: 검증 실패 "
            f"{len(section_check.get('errors') or [])}건 — 파일을 만들지 않음"
        )
