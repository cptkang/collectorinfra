#!/usr/bin/env python3
"""생성 카탈로그 vs 기존 시맨틱 모델 동등성 diff 리포트 (Plan 67 R1).

``src.schema_cache.catalog_builder``가 구조 정본(config/db_profiles)+큐레이션(config/knowledge)에서
만든 카탈로그를, 현행 수기 사본인 ``config/semantic_models/{db_id}.yaml``과 **필드 단위로** 비교한다.
차이 0(또는 전건 사유 명시)이 R1 전환의 통과 기준이다 — 이 스크립트가 그 실측 근거다.

사용법:
    python scripts/catalog_diff.py                 # 전체 DB 리포트
    python scripts/catalog_diff.py --db polestar_cm_gp
    python scripts/catalog_diff.py --ci            # 차이가 있으면 exit 1
    python scripts/catalog_diff.py --dump polestar_cm_gp   # 생성 카탈로그를 YAML로 출력

비교 규칙:
    - dimension의 ``aliases``는 **집합 비교**한다. 별칭은 dimension 인덱스에서 전부 같은
      dimension을 가리키므로(``_dimension_index``) dimension 내부 순서에는 의미가 없다.
      반면 dimension **목록 순서**는 별칭 충돌 시 선착순 해소에 영향을 주므로 순서까지 비교한다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402

from src.schema_cache.catalog_builder import (  # noqa: E402
    build_catalog,
    load_knowledge_overrides,
)

SEMANTIC_DIR = REPO_ROOT / "config" / "semantic_models"
PROFILE_DIR = REPO_ROOT / "config" / "db_profiles"
KNOWLEDGE_DIR = REPO_ROOT / "config" / "knowledge"


def _load_yaml(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def _dim_key(dim: dict) -> str:
    return str(dim.get("name"))


def _diff_dimensions(generated: list[dict], expected: list[dict]) -> list[str]:
    """dimension 목록을 이름·순서·필드 단위로 비교한다."""
    diffs: list[str] = []
    gen_names = [_dim_key(d) for d in generated]
    exp_names = [_dim_key(d) for d in expected]
    if gen_names != exp_names:
        missing = [n for n in exp_names if n not in gen_names]
        added = [n for n in gen_names if n not in exp_names]
        if missing:
            diffs.append(f"pattern_a.dimensions 누락: {missing}")
        if added:
            diffs.append(f"pattern_a.dimensions 추가: {added}")
        if not missing and not added:
            diffs.append(f"pattern_a.dimensions 순서 상이: 생성={gen_names} 기대={exp_names}")

    gen_by = {_dim_key(d): d for d in generated}
    for exp in expected:
        name = _dim_key(exp)
        gen = gen_by.get(name)
        if gen is None:
            continue
        for field in sorted(set(exp) | set(gen)):
            gval, eval_ = gen.get(field), exp.get(field)
            if field == "aliases":
                if set(gval or []) != set(eval_ or []):
                    only_e = sorted(set(eval_ or []) - set(gval or []))
                    only_g = sorted(set(gval or []) - set(eval_ or []))
                    diffs.append(
                        f"dimension[{name}].aliases 상이 — 기대에만 {only_e} / 생성에만 {only_g}")
            elif gval != eval_:
                diffs.append(f"dimension[{name}].{field} 상이 — 생성={gval!r} 기대={eval_!r}")
    return diffs


def _diff_value(path: str, generated: Any, expected: Any) -> list[str]:
    """dimension 외 필드를 재귀 비교한다."""
    diffs: list[str] = []
    if isinstance(generated, dict) and isinstance(expected, dict):
        for key in sorted(set(generated) | set(expected)):
            if key not in generated:
                diffs.append(f"{path}.{key} 누락 — 기대={expected[key]!r}")
            elif key not in expected:
                diffs.append(f"{path}.{key} 추가 — 생성={generated[key]!r}")
            else:
                diffs.extend(_diff_value(f"{path}.{key}", generated[key], expected[key]))
    elif generated != expected:
        diffs.append(f"{path} 상이 — 생성={generated!r} 기대={expected!r}")
    return diffs


def build_for(db_id: str) -> tuple[Optional[dict], Optional[str]]:
    """db_id의 카탈로그를 생성한다. 실패 시 (None, 사유)를 돌려준다."""
    overrides = load_knowledge_overrides(db_id, knowledge_dir=str(KNOWLEDGE_DIR))
    profile_id = overrides.get("structure_from") or db_id
    profile = _load_yaml(PROFILE_DIR / f"{profile_id}.yaml")
    if profile is None:
        return None, f"구조 정본 없음: config/db_profiles/{profile_id}.yaml"
    if not overrides:
        return None, f"큐레이션 오버라이드 없음: config/knowledge/{db_id}/catalog.yaml"
    return build_catalog(profile, db_id=db_id, overrides=overrides), None


def diff_db(db_id: str) -> tuple[list[str], Optional[str]]:
    """생성 카탈로그와 시맨틱 모델의 차이 목록을 만든다."""
    expected = _load_yaml(SEMANTIC_DIR / f"{db_id}.yaml")
    if expected is None:
        return [], f"시맨틱 모델 없음: config/semantic_models/{db_id}.yaml"
    generated, err = build_for(db_id)
    if err:
        return [], err

    diffs: list[str] = []
    for key in sorted(set(generated) | set(expected)):
        if key not in generated:
            diffs.append(f"{key} 누락(생성 카탈로그에 없음)")
        elif key not in expected:
            diffs.append(f"{key} 추가(시맨틱 모델에 없음)")
        elif key == "pattern_a":
            gen_a, exp_a = generated[key], expected[key]
            diffs.extend(_diff_dimensions(
                gen_a.get("dimensions") or [], exp_a.get("dimensions") or []))
            for sub in sorted((set(gen_a) | set(exp_a)) - {"dimensions"}):
                diffs.extend(_diff_value(f"pattern_a.{sub}", gen_a.get(sub), exp_a.get(sub)))
        else:
            diffs.extend(_diff_value(key, generated[key], expected[key]))
    return diffs, None


def _target_db_ids(only: Optional[str]) -> list[str]:
    if only:
        return [only]
    return sorted(p.stem for p in SEMANTIC_DIR.glob("*.yaml"))


def main() -> int:
    parser = argparse.ArgumentParser(description="카탈로그 동등성 diff 리포트")
    parser.add_argument("--db", help="특정 db_id만 검사")
    parser.add_argument("--ci", action="store_true", help="차이가 있으면 exit 1")
    parser.add_argument("--json", action="store_true", help="JSON 출력")
    parser.add_argument("--dump", metavar="DB_ID", help="생성 카탈로그를 YAML로 출력하고 종료")
    args = parser.parse_args()

    if args.dump:
        catalog, err = build_for(args.dump)
        if err:
            print(f"생성 실패: {err}", file=sys.stderr)
            return 1
        print(yaml.safe_dump(catalog, allow_unicode=True, sort_keys=False))
        return 0

    results: dict[str, dict] = {}
    total = 0
    for db_id in _target_db_ids(args.db):
        diffs, err = diff_db(db_id)
        results[db_id] = {"error": err, "diffs": diffs}
        total += len(diffs) + (1 if err else 0)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("=" * 70)
        print("카탈로그 동등성 diff (생성 카탈로그 vs config/semantic_models)")
        print("=" * 70)
        for db_id, result in results.items():
            if result["error"]:
                print(f"\n[{db_id}] 검사 불가 — {result['error']}")
                continue
            if not result["diffs"]:
                print(f"\n[{db_id}] 차이 0 — 동등")
                continue
            print(f"\n[{db_id}] 차이 {len(result['diffs'])}건")
            for line in result["diffs"]:
                print(f"  - {line}")
        print(f"\n총 차이 {total}건")

    if args.ci and total:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
