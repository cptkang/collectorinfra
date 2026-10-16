#!/usr/bin/env python3
"""설정 `getattr` 폴백 린트 (D-331 ② · plans/166 W1).

`getattr(<설정 객체>, "필드", 기본)`의 기본값이 `src/config.py` 필드 기본값과 **다르면**
위반으로 센다. 평시에는 `AppConfig`가 값을 채워 무해하지만, SimpleNamespace 테스트 설정이나
필드명 오타에서는 반대 방향 기본값으로 조용히 동작한다(Known Mistakes 2026-09-17).
존재하는 필드는 `cfg.<그룹>.<필드>`로 직접 접근하는 것이 원칙이다.

판정 규칙:
  - 필드 기본값은 `src/config.py`의 pydantic 설정 클래스 `model_fields`에서 읽는다
    (`default_factory`는 호출 결과). 필수 필드는 대상이 아니다.
  - 같은 이름의 필드가 여러 클래스에 **다른 기본값**으로 있으면 어느 그룹인지 알 수 없어 건너뛴다.
  - 기본 인자가 리터럴이 아니면(이름·호출 등) 건너뛴다. `None` 폴백(존재 확인 관용구)도 건너뛴다.
  - 건너뛴 것은 수만 보고한다. `src/config.py`에 없는 이름은 설정 접근이 아니므로 세지 않는다.

스캔 대상: `src/`·`noise_gate/`(패키지 안 `tests/`·`scripts/`와 `src/config.py` 제외 —
arch_check와 같은 범위).
`mcp_server`·`apm_gateway`·`sre_agent`의 자체 설정은 대상이 아니다(D-331 주의 ③).

사용법:
    python scripts/config_fallback_check.py              # 보고 모드(항상 종료 코드 0)
    python scripts/config_fallback_check.py --verbose    # 위반 위치 목록
    python scripts/config_fallback_check.py --json       # JSON 출력
    python scripts/config_fallback_check.py --ci         # 위반이 있으면 종료 코드 1
"""

from __future__ import annotations

import argparse
import ast
import inspect
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS: tuple[str, ...] = ("src", "noise_gate")
CONFIG_FILE = "src/config.py"
# plans/166 W2 I4 — 설정 클래스가 `src/config/` 패키지 하위 모듈로 나뉘었다. 옛 단일 파일 경로
# (`CONFIG_FILE`)는 합성 저장소 테스트가 쓰므로 함께 제외한다.
CONFIG_PACKAGE_DIR = "src/config/"

# 의도된 폴백 허용 목록 (파일, 필드) → 사유. 설정 대역(MagicMock·SimpleNamespace)에서
# 필드가 없으면 기능을 끄는 방어가 함수 계약(docstring)이고 테스트가 그것을 단언하는 곳만 둔다.
# 늘릴 때는 사유와 단언 테스트를 함께 적는다(plans/166 W1 · D-331 부기).
ALLOWED: dict[tuple[str, str], str] = {
    ("src/nodes/prompt_blocks.py", "prompt_token_budget"):
        "resolve_prompt_token_budget — 대역의 미정의 속성은 가드 비활성"
        "(test_multi_gate_token_budget::test_budget_absent_or_invalid_disables)",
    # plans/166 W2 N3 이동 뒤 실제 위치.
    # 옛 키는 test_config_fallback_check가 합성 경로로 단언해 남긴다.
    ("src/pipeline/prompt_blocks.py", "prompt_token_budget"):
        "resolve_prompt_token_budget — 대역의 미정의 속성은 가드 비활성"
        "(test_multi_gate_token_budget::test_budget_absent_or_invalid_disables)",
    ("src/db_adapters/template_assembler.py", "template_assembly"):
        "template_assembly_enabled — 실제 bool True일 때만 켬, 대역은 꺼짐"
        "(test_plan143_template_assembly::test_switch_off_no_call)",
}


@dataclass
class Finding:
    file: str
    line: int
    target: str
    field: str
    fallback: str
    default: str


@dataclass
class Report:
    checked_files: int = 0
    getattr_calls: int = 0
    violations: list[Finding] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def field_defaults() -> dict[str, list[Any]]:
    """`src.config` 패키지 설정 클래스의 필드명 → 서로 다른 기본값 목록."""
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from pydantic import BaseModel

    import src.config as config_module

    out: dict[str, list[Any]] = {}
    package = config_module.__name__
    for _, cls in inspect.getmembers(config_module, inspect.isclass):
        # 하위 모듈(`src.config.llm` 등)에 정의돼 패키지가 재노출한 클래스도 읽는다(plans/166 I4).
        in_package = cls.__module__ == package or cls.__module__.startswith(package + ".")
        if not in_package or not issubclass(cls, BaseModel):
            continue
        for name, info in cls.model_fields.items():
            if info.default_factory is not None:
                value = info.default_factory()  # type: ignore[call-arg]
            elif info.is_required():
                continue
            else:
                value = info.default
            if isinstance(value, BaseModel):
                continue  # nested 그룹(AppConfig.llm 등) — 필드 값 폴백 대상 아님
            bucket = out.setdefault(name, [])
            if not any(_same(value, v) for v in bucket):
                bucket.append(value)
    return out


def _same(a: Any, b: Any) -> bool:
    """bool·int를 섞지 않는 값 동치(0 == 0.0은 같다 · True == 1은 다르다)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    seq = (list, tuple, set, frozenset)
    if isinstance(a, seq) and isinstance(b, seq):
        return type(a) is type(b) and a == b or (not a and not b)
    return type(a) is type(b) and a == b


def iter_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for d in SCAN_DIRS:
        for p in sorted((root / d).rglob("*.py")):
            rel = p.relative_to(root).as_posix()
            if ("/tests/" in f"/{rel}" or "/scripts/" in f"/{rel}" or rel == CONFIG_FILE
                    or rel.startswith(CONFIG_PACKAGE_DIR)):
                continue
            files.append(p)
    return files


def check(root: Path = PROJECT_ROOT) -> Report:
    defaults = field_defaults()
    report = Report()
    for path in iter_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except SyntaxError:
            report.skip("구문 오류 파일")
            continue
        report.checked_files += 1
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "getattr" and len(node.args) == 3):
                continue
            name_arg = node.args[1]
            if not (isinstance(name_arg, ast.Constant) and isinstance(name_arg.value, str)):
                continue
            known = defaults.get(name_arg.value)
            if known is None:
                continue  # 설정 필드가 아닌 이름
            report.getattr_calls += 1
            if len(known) > 1:
                report.skip("기본값이 다른 동명 필드")
                continue
            try:
                fallback = ast.literal_eval(node.args[2])
            except (ValueError, SyntaxError, TypeError):
                report.skip("리터럴이 아닌 폴백")
                continue
            if fallback is None:
                report.skip("None 폴백")
                continue
            if _same(fallback, known[0]):
                continue
            if (rel, name_arg.value) in ALLOWED:
                report.skip("허용 목록")
                continue
            report.violations.append(Finding(
                file=rel, line=node.lineno, target=ast.unparse(node.args[0])[:40],
                field=name_arg.value, fallback=ast.unparse(node.args[2])[:40],
                default=repr(known[0])[:40],
            ))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="설정 getattr 폴백 린트 (D-331 ②)")
    parser.add_argument("--ci", action="store_true", help="위반이 있으면 종료 코드 1")
    parser.add_argument("--verbose", action="store_true", help="위반 위치 목록")
    parser.add_argument("--json", action="store_true", help="JSON 출력")
    args = parser.parse_args(argv)

    report = check()
    if args.json:
        print(json.dumps({
            "checked_files": report.checked_files,
            "getattr_calls": report.getattr_calls,
            "violations": [asdict(f) for f in report.violations],
            "skipped": report.skipped,
        }, ensure_ascii=False, indent=2))
    else:
        files = len({f.file for f in report.violations})
        fields = len({f.field for f in report.violations})
        print("설정 getattr 폴백 린트 (D-331 ②)")
        print(f"  검사 파일: {report.checked_files}"
              f" · 설정 필드 getattr(3인자): {report.getattr_calls}")
        print(f"  위반(폴백 ≠ 필드 기본값): {len(report.violations)}지점"
              f" · {fields}필드 · {files}파일")
        skipped = " · ".join(f"{k} {v}" for k, v in sorted(report.skipped.items())) or "없음"
        print(f"  판정 불가(건너뜀): {skipped}")
        if args.verbose or args.ci:
            for f in report.violations:
                print(f"  {f.file}:{f.line}  getattr({f.target}, {f.field!r}, {f.fallback})"
                      f"  ← 필드 기본값 {f.default}")
    return 1 if args.ci and report.violations else 0


if __name__ == "__main__":
    sys.exit(main())
