"""경계 불변식 테스트 (D-118) — sre_agent ↔ collectorinfra src 양방향 import 0.

- 정방향: sre_agent 패키지·scripts가 `src.*` / `collectorinfra.*`를 import하지 않음.
- 역방향: (co-located 시) collectorinfra `src/`가 `sre_agent`를 import하지 않음.
"""

import ast
from pathlib import Path

import pytest

_TOP = Path(__file__).resolve().parents[1]          # collectorinfra/sre_agent/ (최상위)
_PACKAGE = _TOP / "sre_agent"                        # 파이썬 패키지
_SCRIPTS = _TOP / "scripts"
_COLLECTORINFRA_SRC = _TOP.parent / "src"            # collectorinfra/src (co-located 시)

# sre_agent 안에서 금지되는 최상위 모듈 (collectorinfra 본체)
_FORBIDDEN_TOP = frozenset({"src", "collectorinfra"})


def _absolute_imported_tops(py_file: Path) -> set[str]:
    """파일에서 절대 import된 최상위 모듈명 집합을 반환한다(상대 import 제외)."""
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    tops: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                tops.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                tops.add(node.module.split(".", 1)[0])
    return tops


def test_sre_agent_does_not_import_collectorinfra():
    offenders: list[str] = []
    for py_file in list(_PACKAGE.rglob("*.py")) + list(_SCRIPTS.rglob("*.py")):
        for top in _absolute_imported_tops(py_file):
            if top in _FORBIDDEN_TOP:
                offenders.append(f"{py_file.relative_to(_TOP)}: import {top}")
    assert offenders == [], f"sre_agent가 collectorinfra 모듈을 import함: {offenders}"


def test_collectorinfra_src_does_not_import_sre_agent():
    if not _COLLECTORINFRA_SRC.is_dir():
        pytest.skip("collectorinfra src 부재 — 분리 완료 상태(역방향 경계 대상 없음)")
    offenders: list[str] = []
    for py_file in _COLLECTORINFRA_SRC.rglob("*.py"):
        try:
            tops = _absolute_imported_tops(py_file)
        except (SyntaxError, UnicodeDecodeError):
            continue
        if "sre_agent" in tops:
            offenders.append(str(py_file.relative_to(_COLLECTORINFRA_SRC)))
    assert offenders == [], f"collectorinfra src가 sre_agent를 import함: {offenders}"


# ── 수용 기준 (Plan 05 §8-③) — run_service 유일 엔트리 · 수신부/게이트 코드 부재 ──


def test_run_service_is_sole_entry():
    """패키지 내 `if __name__ == '__main__'` 엔트리는 run_service.py 하나뿐이다(§2·§5·§8-③).

    scripts/(개발 도구)는 서비스 엔트리가 아니므로 대상에서 제외한다.
    """
    entries: list[str] = []
    for py_file in _PACKAGE.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        if '__name__ == "__main__"' in text or "__name__ == '__main__'" in text:
            entries.append(py_file.name)
    assert entries == ["run_service.py"], f"패키지 엔트리는 run_service 하나여야 함: {entries}"


def test_no_gate_or_ingestion_modules():
    """통합으로 소멸한 수신부·게이트 엔트리 코드가 패키지에 존재하지 않는다(§2·§8-③)."""
    forbidden_names = {"run_gate.py", "receiver.py", "ingest.py", "webhook.py", "gate.py"}
    forbidden_symbols = ("def run_gate", "def run_receiver", "webhook_receiver", "def ingest_alarm")
    offenders: list[str] = []
    for py_file in _PACKAGE.rglob("*.py"):
        if py_file.name in forbidden_names:
            offenders.append(f"금지 모듈: {py_file.name}")
        text = py_file.read_text(encoding="utf-8")
        for sym in forbidden_symbols:
            if sym in text:
                offenders.append(f"{py_file.name}: {sym}")
    assert offenders == [], f"수신부/게이트 코드가 존재함: {offenders}"


def test_no_remediation_execution_path():
    """읽기전용·자동 조치 없음(D-003) — 조사 실행부에 조치 실행 API 호출 경로가 없다."""
    forbidden = ("subprocess.run", "subprocess.Popen", "os.system", "systemctl restart", "kill -")
    offenders: list[str] = []
    for py_file in _PACKAGE.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        for sym in forbidden:
            if sym in text:
                offenders.append(f"{py_file.name}: {sym}")
    assert offenders == [], f"조치 실행 경로가 존재함: {offenders}"
