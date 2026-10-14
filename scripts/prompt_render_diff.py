#!/usr/bin/env python3
"""프롬프트 정본 렌더 전후 diff 리포트 (Plan 67 R1-2 / R1 잔여).

폴스타 시스템 프롬프트를 두 가지로 만들어 비교한다:

    기존(폴백 상수) : ``POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE`` — 지식이 문자열에 박혀 있던 형태
    정본 렌더       : ``render_system_template()`` — 지식을 config/knowledge 카탈로그에서 렌더

diff 0이면 무해 전환(플래그 불요), 차이가 있으면 **전건 사유를 명시**해야 전환할 수 있다
(계획서 §6.2 리스크 3 — 프롬프트 바이트 변화는 LLM 출력 분포를 바꾼다).

R1 잔여분(Template A/B SQL 예제·알람 템플릿)은 diff≠0 블록이 섞여 있어 옵트인 플래그
``TEXT2SQL_PROMPT_KNOWLEDGE_RENDER`` 뒤에 두었다. ``--blocks``가 마커 단위로 ON/OFF를
비교하고 차이 나는 블록마다 사유(`_BLOCK_REASONS`)를 붙여 리포트한다.

사용법:
    python scripts/prompt_render_diff.py             # 기본(플래그 OFF) 렌더 diff — 0이어야 정상
    python scripts/prompt_render_diff.py --ci        # 차이가 있으면 exit 1
    python scripts/prompt_render_diff.py --blocks    # 플래그 ON/OFF 블록별 비교 + 사유
    python scripts/prompt_render_diff.py --blocks --profile polestar_cm_gp
                                                    # 구조 정본까지 주입한 ON 렌더(패턴 A 포함)
    python scripts/prompt_render_diff.py --prompts <base_sha>
                                                    # src/prompts/*.py 모듈 상수를 base와 대조
                                                    # (일치·변경·신설·삭제 표 · 보고 전용)

프롬프트 지식 변경 이력 (정본 대조로 발견·교정한 건 — 전건 사유 명시):
    2026-07-29 Template A/B의 호스트명·IP 조회를 EAV(`cc.name='Hostname'`/`'IPaddress'`)에서
        직접 컬럼(`c.hostname`/`c.ipaddress`)으로 교정. 사유: 해당 EAV 속성은 실측상 비어 있어
        (D-058/D-061, 카탈로그도 두 속성을 제외) 예제대로 생성한 SQL은 호스트명·IP가 NULL이 됐다.
        Template B의 `hi` 서브쿼리는 그 빈 값이 조인 키(`ON svr.ipaddress = hi.ipaddress`)여서
        CPU 코어수·메모리 용량까지 함께 NULL이 되는 경로였다. R1 정본 일원화가 드러낸 사본 드리프트.
    2026-07-30 Template B `hi` 서브쿼리 조인 키를 값 컬럼(`svr.ipaddress = hi.ipaddress`)에서
        서버 식별자(`svr.id = hi.id`, hi.id = COALESCE(platform_resource_id, id))로 교정
        (플래그 ON에서만 적용 — 팀 리드 판단 대기). 사유: IP는 값 컬럼이라 NULL·중복·다중 NIC가
        가능해 조인 키로 취약하다(위 2026-07-29 교정은 값이 비어 조인이 깨지는 것을 막았을 뿐,
        키 자체의 취약성은 남았다). id 조인은 조립기(`assembler.py`의 부모 조인
        `ON parent.id = COALESCE(c.platform_resource_id, c.id)`)와 기존 validator 테스트 픽스처
        (`tests/test_nodes/test_query_validator_left_join_demotion.py`의 `) hi ON svr.id = hi.id`)가
        쓰는 정본 형태와 일치한다. 조인에만 쓰였던 `hi.ipaddress` 줄은 함께 제거(고아 컬럼).
        실측: 폴스타 validator 7종 + 공용 validate_sql 모두 OFF/ON 동일하게 통과(오류·경고 0).
        ※ 같은 형태가 `config/db_profiles/polestar_{cm_gp,cm_yd,b0}.yaml`의 query_examples에도
        남아 있다(각 파일 1건) — 프로필은 본 작업 범위 밖이라 미수정, 별건 판단 필요.
        ※ 값 컬럼 조인을 잡는 `check_value_column_join`(신설)은 **같은 플래그 뒤**에 등록한다 —
        현행 예제를 위반으로 잡으므로 예제 교정 없이 등록하면 LLM이 예제대로 생성한 SQL이
        매번 반려된다(예제와 검증은 함께 움직인다).

렌더 전환에서 **제외**한 블록(사유):
    [심각도 매핑] 표면어 목록 — 프롬프트가 정본 `severity_map`의 **상위집합**이다(정본에 없는
        `해제`·`cleared`·`normal`·`notice`·대문자형까지 가르친다). 렌더하면 그 표면어가 소실되므로
        정본을 상위집합으로 확장하기 전에는 전환하지 않는다(`config/knowledge`는 본 작업 범위 밖).
        역방향 드리프트(정본에 있는 표기가 프롬프트에서 빠지는 것)는 테스트가 감시한다.
    알람 테이블 목록 — 테이블명은 프로필 `alarm_allowed_tables`에서 파생 가능하지만 블록의 본체는
        컬럼 열거·운영 주의사항(정본에 없는 큐레이션)이라 렌더 시 손실이 크다. 테이블명이 프로필
        선언을 벗어나지 않는지만 테스트로 감시한다(프롬프트 바이트 무변경).
    Template B measure SELECT alias 접두사(`cpu_min` 등) — 표현이므로 정본에 두지 않는다. 단
        resource_type·definition_name·값 컬럼은 정본에서 렌더하며 결과가 현행과 바이트 일치한다.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.db_adapters.polestar.prompts import (  # noqa: E402
    POLESTAR_ALARM_QUERY_GENERATOR_SYSTEM_TEMPLATE,
    POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE,
    knowledge_blocks,
    render_alarm_system_template,
    render_system_template,
)

#: 플래그 ON에서 현행 문구와 달라지는 블록의 사유(전건 명시 — 없는 블록은 diff 0이어야 한다).
_BLOCK_REASONS: dict[str, str] = {
    "[[metric_definition_note]]": (
        "지표 설명을 정본 measure 별칭으로 통일. 종전 'CPU/메모리/파일시스템 사용률'(축약)이 "
        "정본 별칭 'CPU 사용률/메모리 사용률/파일시스템 사용률'로 확장된다 — 의미 동일, 표기만 변경."
    ),
    "[[hi_ipaddress_line]]": (
        "hi 서브쿼리의 ipaddress 컬럼은 조인 키 전용이었으므로 조인 키 교정과 함께 제거"
        "(외부 SELECT·GROUP BY는 svr.ipaddress를 쓰므로 참조 고아 없음)."
    ),
    "[[hi_join_condition]]": (
        "값 컬럼 조인(svr.ipaddress = hi.ipaddress) → 서버 식별자 조인(svr.id = hi.id) 교정. "
        "IP는 NULL·중복 가능한 값이라 조인 키로 취약 — 상세 사유는 모듈 상단 변경 이력 참조."
    ),
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _unified(before: str, after: str, *, from_label: str, to_label: str, context: int = 2) -> list[str]:
    return list(difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=from_label,
        tofile=to_label,
        n=context,
    ))


def _changed_lines(diff: list[str]) -> int:
    return sum(
        1 for line in diff
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    )


def _load_profile_catalog(db_id: str) -> dict:
    """구조 정본(db_profiles)까지 주입한 카탈로그 — 패턴 A(EAV) 블록 렌더에 필요하다."""
    from src.schema_cache.catalog_builder import (
        build_catalog,
        load_knowledge_overrides,
        load_structure_profile,
    )

    return build_catalog(
        load_structure_profile(db_id),
        db_id=db_id,
        overrides=load_knowledge_overrides(db_id),
    )


def _report_default() -> int:
    """플래그 OFF 렌더가 현행 상수와 바이트 동일한지 확인한다(R1-2 무해 전환 실증)."""
    pairs = (
        ("데이터 조회", POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE,
         render_system_template(knowledge_render=False)),
        ("알람 조회", POLESTAR_ALARM_QUERY_GENERATOR_SYSTEM_TEMPLATE,
         render_alarm_system_template(knowledge_render=False)),
    )
    changed = 0
    for label, before, after in pairs:
        diff = _unified(before, after, from_label=f"{label} 폴백 상수(전)", to_label=f"{label} 정본 렌더(후)")
        print(f"\n[{label}] 전 {len(before)}자 / 후 {len(after)}자  sha256={_sha(after)[:16]}")
        if not diff:
            print("  차이 0 — 바이트 동일(무해 전환)")
            continue
        changed += _changed_lines(diff)
        print(f"  차이 {_changed_lines(diff)}행")
        sys.stdout.writelines(diff)
    return changed


def _report_blocks(catalog: dict | None, *, label: str) -> int:
    """마커 단위로 플래그 ON/OFF를 비교하고, 차이 나는 블록마다 사유를 붙인다."""
    off = knowledge_blocks(catalog, knowledge_render=False)
    on = knowledge_blocks(catalog, knowledge_render=True)

    identical = [m for m in off if off[m] == on[m]]
    changed = [m for m in off if off[m] != on[m]]

    print(f"\n[블록 비교 — {label}] 총 {len(off)}블록 / 동일 {len(identical)} / 변경 {len(changed)}")
    print("  · 동일(정본 렌더가 현행과 바이트 일치): " + ", ".join(sorted(identical)))

    missing_reason = 0
    for marker in changed:
        reason = _BLOCK_REASONS.get(marker)
        print(f"\n  · 변경 {marker}")
        if reason:
            print(f"    사유: {reason}")
        else:
            missing_reason += 1
            print("    사유: **미기재 — 전건 사유 명시 규약 위반**")
        for line in _unified(off[marker], on[marker], from_label="OFF", to_label="ON", context=0):
            if not line.startswith(("---", "+++", "@@")):
                print(f"    {line.rstrip()}")

    for template_label, before, render in (
        ("데이터 조회", POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE, render_system_template),
        ("알람 조회", POLESTAR_ALARM_QUERY_GENERATOR_SYSTEM_TEMPLATE, render_alarm_system_template),
    ):
        after = render(catalog, knowledge_render=True)
        diff = _unified(before, after, from_label=f"{template_label} OFF", to_label=f"{template_label} ON")
        print(f"\n[{template_label}] ON 전체 diff {_changed_lines(diff)}행  sha256={_sha(after)[:16]}")
        sys.stdout.writelines(diff)

    return missing_reason


# ──────────────────────────────────────────────
# --prompts: src/prompts/*.py 모듈 상수 base 대조 (plans/156 W5 — 159 W2 수동 절차의 도구화)
# ──────────────────────────────────────────────

_PROMPTS_DIR = "src/prompts"
_CONST_NAME = re.compile(r"_?[A-Z][A-Z0-9_]*")
#: 평가에 쓰는 순수 str 메서드(인자도 상수 식일 때만) — `"…".format(…)`·`X.removesuffix(…)` 꼴
_STR_METHODS: frozenset[str] = frozenset({
    "format", "removeprefix", "removesuffix", "replace", "strip", "lstrip", "rstrip",
})


def _eval_str(node: ast.expr, known: dict[str, str]) -> str | None:
    """문자열 상수 식을 평가한다 — 리터럴·`+` 연결·앞서 정의된 같은 모듈 이름·순수 str 메서드.

    f-string·임포트한 이름·그 밖의 호출은 None(평가 불가)이다. 소스를 실행하지 않는다.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return known.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _eval_str(node.left, known), _eval_str(node.right, known)
        return None if left is None or right is None else left + right
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _STR_METHODS
    ):
        owner = _eval_str(node.func.value, known)
        args = [_eval_arg(a, known) for a in node.args]
        kwargs = {k.arg: _eval_arg(k.value, known) for k in node.keywords}
        if owner is None or None in args or None in kwargs.values() or None in kwargs:
            return None
        try:
            result = getattr(owner, node.func.attr)(*args, **kwargs)
        except (KeyError, IndexError, ValueError, TypeError):  # 「{x}」 자리 불일치 등
            return None
        return result if isinstance(result, str) else None
    return None


def _eval_arg(node: ast.expr, known: dict[str, str]) -> str | int | None:
    """str 메서드 인자 — 문자열 상수 식 또는 정수 리터럴."""
    if isinstance(node, ast.Constant) and type(node.value) is int:
        return node.value
    return _eval_str(node, known)


def _module_constants(source: str) -> tuple[dict[str, str], list[str]]:
    """모듈 수준 대문자 이름 문자열 상수 — (평가된 상수, 평가 불가 이름). 소스는 AST로만 읽는다.

    평가 불가는 문자열 식으로 보이는 것(f-string · `+` 연결 · 메서드 호출)만 센다 —
    `frozenset({...})` 같은 비문자열 상수는 대상이 아니다.
    """
    known: dict[str, str] = {}
    constants: dict[str, str] = {}
    skipped: list[str] = []
    for stmt in ast.parse(source).body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target, value = stmt.targets[0], stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            target, value = stmt.target, stmt.value
        else:
            continue
        if not isinstance(target, ast.Name):
            continue
        text = _eval_str(value, known)
        if text is not None:
            known[target.id] = text  # 소문자 이름도 뒤 상수의 재료로는 쓴다
        if not _CONST_NAME.fullmatch(target.id):
            continue
        if text is not None:
            constants[target.id] = text
        elif isinstance(value, ast.JoinedStr | ast.BinOp) or (
            isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
        ):
            skipped.append(target.id)
    return constants, skipped


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True,
    ).stdout


def _base_prompt_sources(sha: str, root: Path) -> dict[str, str]:
    """`<sha>:src/prompts/**.py` 원문 — 파일 경로(저장소 상대) → 소스."""
    names = _git(root, "ls-tree", "-r", "--name-only", sha, "--", _PROMPTS_DIR).splitlines()
    return {n: _git(root, "show", f"{sha}:{n}") for n in names if n.endswith(".py")}


def _worktree_prompt_sources(root: Path) -> dict[str, str]:
    """작업 트리의 src/prompts/**.py 원문 — 파일 경로(저장소 상대) → 소스."""
    return {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted((root / _PROMPTS_DIR).rglob("*.py"))
    }


def _compare_prompt_constants(
    base: dict[str, str], head: dict[str, str]
) -> tuple[list[tuple[str, str, str, int | None, int | None]], list[str]]:
    """파일별 상수를 대조한다 — (행[(상태, 파일, 상수, 전 길이, 후 길이)], 평가 불가 목록)."""
    rows: list[tuple[str, str, str, int | None, int | None]] = []
    skipped: list[str] = []
    for path in sorted(set(base) | set(head)):
        before, skip_b = _module_constants(base[path]) if path in base else ({}, [])
        after, skip_a = _module_constants(head[path]) if path in head else ({}, [])
        skipped += [f"{path}:{n}" for n in sorted(set(skip_b) | set(skip_a))]
        for name in sorted(set(before) | set(after)):
            b, a = before.get(name), after.get(name)
            status = (
                "신설" if b is None else "삭제" if a is None else "일치" if a == b else "변경"
            )
            rows.append((
                status, path, name,
                None if b is None else len(b), None if a is None else len(a),
            ))
    return rows, skipped


def _report_prompts(sha: str, root: Path = REPO_ROOT) -> int:
    """`src/prompts/*.py` 모듈 상수를 base 커밋과 대조해 표로 낸다(보고 전용 — 사유 요구 없음)."""
    try:
        base = _base_prompt_sources(sha, root)
    except subprocess.CalledProcessError as exc:
        print(f"git 조회 실패({sha}): {exc.stderr.strip()}")
        return 2
    rows, skipped = _compare_prompt_constants(base, _worktree_prompt_sources(root))
    counts = {s: sum(1 for r in rows if r[0] == s) for s in ("일치", "변경", "신설", "삭제")}
    print(f"\n[src/prompts 상수 대조 — base {sha} ↔ 작업 트리] 총 {len(rows)}개 / "
          + " / ".join(f"{k} {v}" for k, v in counts.items()))
    for status, path, name, b, a in rows:
        if status == "일치":
            continue
        size = f"{'-' if b is None else b}자 → {'-' if a is None else a}자"
        print(f"  {status}  {path}  {name}  ({size})")
    if not any(r[0] != "일치" for r in rows):
        print("  변경 0 — 전 상수 바이트 일치")
    if skipped:
        print(f"  평가 불가(비교 제외 — f-string·호출 등) {len(skipped)}건: " + ", ".join(skipped))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="프롬프트 정본 렌더 diff 리포트")
    parser.add_argument("--ci", action="store_true", help="차이(또는 사유 미기재)가 있으면 exit 1")
    parser.add_argument("--blocks", action="store_true",
                        help="옵트인 플래그 ON/OFF를 블록 단위로 비교하고 사유를 리포트")
    parser.add_argument("--profile", metavar="DB_ID",
                        help="구조 정본(db_profiles/{DB_ID}.yaml)까지 주입해 패턴 A 블록도 렌더")
    parser.add_argument("--prompts", metavar="BASE_SHA",
                        help="src/prompts/*.py 모듈 상수를 BASE_SHA와 대조(일치·변경·신설·삭제 표)")
    args = parser.parse_args()

    if args.prompts:
        print("=" * 78)
        print("src/prompts 모듈 상수 — base 대조")
        print("=" * 78)
        return _report_prompts(args.prompts)

    print("=" * 78)
    print("폴스타 시스템 프롬프트 — 정본 렌더 diff")
    print("=" * 78)

    if not args.blocks:
        changed = _report_default()
        return 1 if (args.ci and changed) else 0

    catalog = _load_profile_catalog(args.profile) if args.profile else None
    label = f"구조 정본 주입: {args.profile}" if args.profile else "큐레이션 정본만(_base)"
    if catalog is not None and not catalog.get("pattern_a"):
        print(f"  주의: {args.profile} 프로필에서 패턴 A를 만들지 못했다 — EAV 블록은 리터럴 유지")
    if catalog is None:
        print("  참고: 패턴 A(EAV 구성·속성)는 구조 정본이 필요해 이 모드에서는 리터럴이 유지된다"
              " (--profile 로 확인)")
    missing_reason = _report_blocks(catalog, label=label)
    if missing_reason:
        print(f"\n사유 미기재 블록 {missing_reason}건 — 전환 전 사유를 등재하라")
    return 1 if (args.ci and missing_reason) else 0


if __name__ == "__main__":
    sys.exit(main())
