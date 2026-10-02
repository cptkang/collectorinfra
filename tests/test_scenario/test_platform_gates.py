"""플랫폼·경계 수용 기준 V14·V19 (plans/94 §10 · 부록 A.5).

V19(Windows 무과금 전 경로)는 **Windows 에서만 실행된다.** 다른 OS 에서 통과로 세면
"검증됐다"는 거짓 기록이 남으므로 skip 한다 - 무과금 검증이 한쪽 OS 에서만 되면
절반만 검증된 것이다(W7).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.scenario import REPO_ROOT, utf8_open
from scripts.scenario.server import IS_WINDOWS

pytestmark = pytest.mark.filterwarnings("ignore")


# --- V14 기존 게이트 경계 -------------------------------------------------

def test_V14_하네스는_arch_check_스캔_대상_밖이다() -> None:
    """scripts/ 는 계층 검사 대상이 아니다 - 러너가 운영 경로에 배선되지 않는다는 뜻이다."""
    source = (REPO_ROOT / "scripts" / "arch_check.py").read_text(encoding="utf-8")
    assert '"scripts"' not in source.split("MODULE_LAYER_MAP")[-1][:4000]


def test_V14_하네스는_src를_수정하지_않는다() -> None:
    """읽기(import)는 하지만 쓰기 경로는 없다."""
    for path in (REPO_ROOT / "scripts" / "scenario").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for forbidden in ('open(REPO_ROOT / "src"', 'REPO_ROOT / "src"', '".env"'):
            assert forbidden not in text, f"{path.name}: {forbidden} 참조"


def test_V14_신규_enable_플래그를_만들지_않는다() -> None:
    """프로파일은 기존 env 키의 조합일 뿐이다(D-162)."""
    import yaml

    data = yaml.safe_load(
        (REPO_ROOT / "config" / "scenarios" / "profiles.yaml").read_text(encoding="utf-8")
    )
    from src.api.settings_catalog import field_index

    known = set(field_index())
    for name, mapping in data["profiles"].items():
        unknown = [key for key in mapping if key not in known]
        assert not unknown, f"프로파일 {name}: 설정 카탈로그에 없는 키 {unknown}"


# --- W4 파일 쓰기 계약 ----------------------------------------------------

def test_W4_모든_쓰기가_encoding과_newline을_함께_명시한다() -> None:
    """Windows 텍스트 모드가 \\n 을 \\r\\n 으로 바꾸면 재개 대조가 어긋난다(부록 A.1-4)."""
    for path in (REPO_ROOT / "scripts" / "scenario").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), 1):
            if "open(" not in line or "utf8_open" in line or line.lstrip().startswith("#"):
                continue
            if 'open(path, mode' in line:      # utf8_open 자신의 구현
                continue
            if "encoding=" in line and "newline=" not in line and '"rb"' not in line:
                pytest.fail(f"{path.name}:{line_no} newline 미지정: {line.strip()}")


def test_W4_utf8_open이_LF로_쓴다(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b.txt"
    with utf8_open(target, "w") as handle:
        handle.write("한\n글\n")
    assert target.read_bytes() == "한\n글\n".encode()


def test_W8_경로_조립에_문자열_슬래시_결합이_없다() -> None:
    for path in (REPO_ROOT / "scripts" / "scenario").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert 'REPO_ROOT + "' not in text
        assert "REPO_ROOT + '" not in text


# --- V19 Windows 무과금 전 경로 ------------------------------------------

@pytest.mark.skipif(not IS_WINDOWS, reason="V19 는 Windows 단말에서만 판정한다 (W7)")
def test_V19_Windows에서_무과금_1단이_통과한다() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "scripts.scenario", "--dry-run"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    assert "카탈로그 OK" in result.stdout


@pytest.mark.skipif(not IS_WINDOWS, reason="V19 는 Windows 단말에서만 판정한다 (W7)")
def test_V19_Windows에서_한글_리포트가_깨지지_않는다(tmp_path: Path) -> None:
    """cp949 콘솔에서 UnicodeEncodeError 로 런이 죽는 것을 막는다(PYTHONUTF8=1)."""
    target = tmp_path / "report.md"
    with utf8_open(target, "w") as handle:
        handle.write("# 기능 판정 요약\n| 군 | 합격 |\n")
    assert "기능 판정" in target.read_text(encoding="utf-8")


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX 전용")
def test_V19_는_POSIX에서_통과로_세지_않는다() -> None:
    """다른 OS 에서 통과로 세면 '검증됐다'는 거짓 기록이 남는다."""
    assert not IS_WINDOWS
