"""아키텍처 정합성 테스트 (Plan 48 §9, test_arch_check).

orchestration 계층이 application/infrastructure를 호출하는 것은 허용되며,
역방향(application/infrastructure → orchestration) 의존이 없어야 한다.
arch_check error 위반이 0건이어야 한다.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ARCH_SCRIPT = PROJECT_ROOT / "scripts" / "arch_check.py"


def _load_arch_check():
    """scripts/arch_check.py를 모듈로 동적 로드한다 (scripts는 패키지가 아님).

    dataclass 정의가 cls.__module__로 sys.modules를 조회하므로,
    exec 전에 sys.modules에 등록해야 한다.
    """
    if "arch_check" in sys.modules:
        return sys.modules["arch_check"]
    spec = importlib.util.spec_from_file_location("arch_check", ARCH_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["arch_check"] = module
    spec.loader.exec_module(module)
    return module


def test_arch_check_ci_exit_zero():
    """`python scripts/arch_check.py --ci` → returncode 0 (error 위반 0건)."""
    result = subprocess.run(
        [sys.executable, str(ARCH_SCRIPT), "--ci"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"arch_check --ci 실패(returncode={result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_arch_check_no_error_violations():
    """check_project 직접 호출 → error severity 위반이 0건이다."""
    arch = _load_arch_check()
    result = arch.check_project(PROJECT_ROOT)
    errors = [v for v in result.violations if v.severity == "error"]
    assert not errors, "아키텍처 error 위반 발견:\n" + "\n".join(
        f"  {v.file}:{v.line} {v.from_layer}->{v.to_layer} ({v.reason})" for v in errors
    )


def test_orchestration_layer_mapping():
    """src.orchestration이 orchestration 계층으로 매핑되고, 허용 의존을 가진다."""
    arch = _load_arch_check()
    # 계층 매핑 확인
    assert arch.MODULE_LAYER_MAP["src.orchestration"] == "orchestration"
    # orchestration은 application/infrastructure를 호출할 수 있어야 함
    allowed = arch.ALLOWED_DEPS["orchestration"]
    assert "application" in allowed
    assert "infrastructure" in allowed
    # 역방향: application/infrastructure는 orchestration을 의존하면 안 됨
    assert "orchestration" not in arch.ALLOWED_DEPS["application"]
    assert "orchestration" not in arch.ALLOWED_DEPS["infrastructure"]


def _app_to_app_warnings(arch, tmp_path, from_rel: str, statement: str):
    """tmp 프로젝트에 파일 하나를 두고 application→application 경고만 모은다."""
    path = tmp_path / from_rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(statement + "\n", encoding="utf-8")
    return [
        v for v in arch.check_file(path, tmp_path)
        if v.from_layer == "application" and v.to_layer == "application"
    ]


@pytest.mark.parametrize(
    ("from_rel", "statement", "warned"),
    [
        # 노드 패키지 ↔ 같은 노드 패키지 — 경고
        ("src/nodes/a.py", "from src.nodes.b import x", True),
        ("noise_gate/application/nodes/a.py", "from noise_gate.application.nodes.b import x", True),
        # 노드 → 비노드 application 헬퍼 — 경고 없음
        ("src/nodes/a.py", "from src.db_adapters.polestar import x", False),
        ("noise_gate/application/nodes/a.py",
         "from noise_gate.application.server_identity import x", False),
        # 비노드 application 내부 — 경고 없음
        ("src/db_adapters/a.py", "from src.semantic.b import x", False),
        ("src/tools/a.py", "from src.nodes.b import x", False),
        # 서로 다른 노드 패키지 — 경고 없음
        ("src/nodes/a.py", "from noise_gate.application.nodes.b import x", False),
    ],
)
def test_node_to_node_warning_scope(tmp_path, from_rel, statement, warned):
    """D-331 ① — application→application 경고는 같은 노드 패키지 사이에서만 난다."""
    arch = _load_arch_check()
    found = _app_to_app_warnings(arch, tmp_path, from_rel, statement)
    assert bool(found) is warned
    for v in found:
        assert v.severity == "warning"
        assert "순수 헬퍼는 비노드 application 모듈로" in v.reason
