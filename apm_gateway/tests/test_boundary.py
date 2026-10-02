"""경계 불변식 (D-274 ③ · plans/87 J1 [v3] 수용 기준 · G-11).

- 양방향 import 0: `apm_gateway` ↔ `src`·`noise_gate`·`sre_agent`·`mcp_server`(AST —
  `sre_agent/tests/test_boundary.py` 방식).
- 계층 방향: domain → config → adapters(infrastructure) → application → interface → entry.
  `scripts/arch_check.py`는 2단 중첩 모듈 이름을 해석하지 못해(파일 경로 →
  `apm_gateway.apm_gateway.*`, import 이름 → `apm_gateway.*`) 편입하지 않았다 — G-11 판정 기준 ②에
  따라 같은 규칙을 여기서 검사한다.
- 벤더 리터럴 격리: Open API 경로·필드명은 `adapters/jennifer/`에만.
- 설정에 폴스타 DB 연결 문자열 0건 · 엔트리는 `__main__` 하나 · 조치 실행 경로 0건.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

GATEWAY = Path(__file__).resolve().parents[1]  # apm_gateway/
PACKAGE = GATEWAY / "apm_gateway"
REPO = GATEWAY.parent

_FORBIDDEN_FROM_GATEWAY = frozenset(
    {"src", "noise_gate", "sre_agent", "mcp_server", "collectorinfra"}
)
_OTHER_PACKAGES = {
    "src": REPO / "src",
    "noise_gate": REPO / "noise_gate",
    "sre_agent": REPO / "sre_agent" / "sre_agent",
    "mcp_server": REPO / "mcp_server" / "mcp_server",
}

# 계층 순위(낮을수록 안쪽). 같은 순위 또는 더 안쪽만 import할 수 있다.
_LAYER_RANK = {
    "apm_gateway.domain": 0,
    "apm_gateway.config": 1,
    "apm_gateway.adapters": 3,
    "apm_gateway.application": 4,
    "apm_gateway.interface": 6,
    "apm_gateway.__main__": 7,
}


def _imports(py_file: Path) -> list[str]:
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append(node.module)
    return names


def _module_name(py_file: Path) -> str:
    parts = list(py_file.relative_to(GATEWAY).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _rank(module: str) -> int | None:
    best, best_len = None, 0
    for prefix, rank in _LAYER_RANK.items():
        if (module == prefix or module.startswith(prefix + ".")) and len(prefix) > best_len:
            best, best_len = rank, len(prefix)
    return best


def _gateway_sources() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


def test_gateway_does_not_import_other_packages():
    offenders = []
    targets = (
        _gateway_sources()
        + sorted((GATEWAY / "tests").rglob("*.py"))
        + sorted((GATEWAY / "testdata").rglob("*.py"))
    )
    for py_file in targets:
        for name in _imports(py_file):
            if name.split(".", 1)[0] in _FORBIDDEN_FROM_GATEWAY:
                offenders.append(f"{py_file.relative_to(GATEWAY)}: {name}")
    assert offenders == []


@pytest.mark.parametrize("package", sorted(_OTHER_PACKAGES))
def test_other_packages_do_not_import_gateway(package):
    root = _OTHER_PACKAGES[package]
    if not root.is_dir():
        pytest.skip(f"{package} 부재")
    offenders = []
    for py_file in root.rglob("*.py"):
        try:
            names = _imports(py_file)
        except (SyntaxError, UnicodeDecodeError):
            continue
        if any(n.split(".", 1)[0] == "apm_gateway" for n in names):
            offenders.append(str(py_file.relative_to(REPO)))
    assert offenders == []


def test_layer_direction():
    violations = []
    for py_file in _gateway_sources():
        if py_file.name == "__init__.py":
            continue
        module = _module_name(py_file)
        own = _rank(module)
        assert own is not None, f"계층 미지정 모듈: {module}"
        for name in _imports(py_file):
            if not name.startswith("apm_gateway."):
                continue
            target = _rank(name)
            if target is not None and target > own:
                violations.append(f"{module} → {name}")
    assert violations == []


def test_domain_is_stdlib_only():
    offenders = []
    for py_file in (PACKAGE / "domain").rglob("*.py"):
        for name in _imports(py_file):
            top = name.split(".", 1)[0]
            if top != "apm_gateway" and top not in sys.stdlib_module_names:
                offenders.append(f"{py_file.name}: {name}")
    assert offenders == []


_VENDOR_PATH = re.compile(r"""["']/(api|api-v2|restapi)/""")
_VENDOR_FIELDS = (
    "heapUsed",
    "gcTimeUsage",
    "activeDBConnection",
    "errorType",
    "eventLevel",
    "hostName",
)


def test_vendor_literals_only_in_adapter():
    offenders = []
    adapter = PACKAGE / "adapters" / "jennifer"
    for py_file in _gateway_sources():
        if adapter in py_file.parents:
            continue
        text = py_file.read_text(encoding="utf-8")
        if _VENDOR_PATH.search(text):
            offenders.append(f"{py_file.relative_to(GATEWAY)}: Open API 경로")
        for field in _VENDOR_FIELDS:
            if re.search(rf"[\"']{field}[\"']", text):
                offenders.append(f"{py_file.relative_to(GATEWAY)}: {field}")
    assert offenders == []


def test_no_polestar_db_connection_settings():
    text = (GATEWAY / ".env.example").read_text(encoding="utf-8") + (
        PACKAGE / "config.py"
    ).read_text(encoding="utf-8")
    for needle in (
        "postgresql://",
        "postgres://",
        "db2://",
        "DATABASE_URL",
        "_CONNECTION=",
        "POLESTAR_DB",
    ):
        assert needle not in text, needle


def test_single_entry_point():
    entries = [
        p.relative_to(PACKAGE).as_posix()
        for p in _gateway_sources()
        if '__name__ == "__main__"' in p.read_text(encoding="utf-8")
    ]
    assert entries == ["__main__.py"]


def test_no_execution_path():
    """읽기 전용 — 조치 실행 경로(프로세스 실행·셸)가 없다(L2 실행기는 게이트웨이 밖 — D-274 ⑧)."""
    forbidden = ("subprocess", "os.system", "os.popen", "asyncio.create_subprocess")
    offenders = [
        f"{p.name}: {f}"
        for p in _gateway_sources()
        for f in forbidden
        if f in p.read_text(encoding="utf-8")
    ]
    assert offenders == []
