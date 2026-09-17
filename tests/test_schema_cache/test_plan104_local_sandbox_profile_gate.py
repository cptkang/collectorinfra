"""품질 게이트 — `environment: local_sandbox` 표기 프로필의 git 추적 금지 (plans/104 · 계약 §3).

로컬 샌드박스에서도 관리자 승인 적용은 `config/db_profiles/{db_id}.yaml`에 쓰되 `environment:
local_sandbox`를 붙인다. 이 표기가 붙은 프로필은 운영 정본 재료가 아니므로(D-214 ⑥ — 샌드박스에서
추출한 구조를 커밋하지 않는다) 추적 파일에 들어오면 이 테스트가 막는다.

검사 대상: `git ls-files config/db_profiles`의 추적 파일 각각의 **작업 트리 내용**과 **인덱스
내용**(`git show :<path>` — 스테이징만 하고 작업 트리를 되돌린 경우). 판정은 YAML 파싱 기준이라
주석 속 문자열은 표기로 보지 않는다. git이 없거나 저장소가 아니면 skip.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from src.domain.profile_merge import LOCAL_SANDBOX_ENVIRONMENT

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILES_REL = "config/db_profiles"

# YAML 파싱이 실패한 파일의 보수적 판정용 — 주석을 뗀 최상위 `environment:` 줄
_ENV_LINE_RE = re.compile(
    rf"""^environment\s*:\s*(['"]?){LOCAL_SANDBOX_ENVIRONMENT}\1\s*$"""
)


def declares_local_sandbox(text: str) -> bool:
    """프로필 원문이 최상위 `environment: local_sandbox`를 선언하는지 판정한다.

    YAML로 파싱해 최상위 매핑의 `environment` 값만 본다(주석·다른 키 값 속 문자열은 무시).
    파싱이 실패하면 주석을 뗀 최상위 줄을 정규식으로 보고 판정한다(깨진 파일도 놓치지 않게).
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        for line in text.splitlines():
            if line.lstrip().startswith("#"):
                continue
            code = line.split(" #", 1)[0].rstrip()
            if _ENV_LINE_RE.match(code):
                return True
        return False
    return isinstance(data, dict) and data.get("environment") == LOCAL_SANDBOX_ENVIRONMENT


class TestDeclaresLocalSandbox:
    @pytest.mark.parametrize(
        "text",
        [
            "source: manual\nenvironment: local_sandbox\npatterns: []\n",
            "environment: 'local_sandbox'\nsource: manual\n",
            '# 머리 주석\nenvironment: "local_sandbox"  # 로컬\n',
            # 파싱 실패 파일도 최상위 표기 줄이 있으면 잡는다
            "environment: local_sandbox\npatterns: [깨진\n",
        ],
    )
    def test_declared(self, text):
        assert declares_local_sandbox(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "source: manual\npatterns: []\n",
            "# environment: local_sandbox\nsource: manual\n",  # 주석 속 문자열
            "source: manual\nquery_guide: |\n  environment: local_sandbox 는 로컬 표기\n",
            "source: manual\npatterns:\n  - environment: local_sandbox\n",  # 최상위가 아님
            "source: manual\nenvironment: production\n",
            "- 목록 파일\n",
            "",
            "# environment: local_sandbox\npatterns: [깨진\n",  # 파싱 실패 + 주석뿐
        ],
    )
    def test_not_declared(self, text):
        assert declares_local_sandbox(text) is False


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, check=False, timeout=30
    )


def _require_git_repo(repo: Path) -> None:
    """git이 없거나 저장소가 아니면 skip."""
    if shutil.which("git") is None:
        pytest.skip("git 실행 파일 없음")
    inside = _git(repo, "rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip() != b"true":
        pytest.skip("git 저장소가 아님")


def find_local_sandbox_violations(repo: Path) -> list[str]:
    """추적 프로필 중 로컬 샌드박스 표기 항목(`<경로> (작업 트리|인덱스)`) — 경로 사전순."""
    listed = _git(repo, "ls-files", "-z", "--", PROFILES_REL)
    if listed.returncode != 0:
        pytest.skip(f"git ls-files 실패: {listed.stderr.decode(errors='replace').strip()}")
    violations: list[str] = []
    for rel in (p for p in listed.stdout.decode("utf-8").split("\0") if p):
        worktree = repo / rel
        if worktree.is_file():
            text = worktree.read_bytes().decode("utf-8", errors="replace")
            if declares_local_sandbox(text):
                violations.append(f"{rel} (작업 트리)")
        staged = _git(repo, "show", f":{rel}")
        if staged.returncode == 0 and declares_local_sandbox(
            staged.stdout.decode("utf-8", errors="replace")
        ):
            violations.append(f"{rel} (인덱스)")
    return violations


def test_tracked_profiles_do_not_declare_local_sandbox():
    _require_git_repo(REPO_ROOT)
    violations = find_local_sandbox_violations(REPO_ROOT)
    assert not violations, (
        "environment: local_sandbox 표기 프로필이 git에 추적되고 있습니다 — 로컬 샌드박스 "
        "승인본은 운영 정본 재료로 커밋하지 않습니다(D-214 ⑥). 추적 해제하거나 운영 환경에서 "
        "다시 승인하세요: " + ", ".join(violations)
    )


def test_gate_detects_worktree_and_index_in_scratch_repo(tmp_path):
    """게이트가 실제로 위반을 잡는지 tmp 저장소로 확인한다(프로젝트 저장소 무접촉 · 커밋 없음)."""
    if shutil.which("git") is None:
        pytest.skip("git 실행 파일 없음")
    repo = tmp_path / "repo"
    profiles = repo / PROFILES_REL
    profiles.mkdir(parents=True)
    if _git(repo, "init", "-q").returncode != 0:
        pytest.skip("git init 실패")
    marked = "source: manual\nenvironment: local_sandbox\n"
    clean = "# environment: local_sandbox\nsource: manual\n"

    (profiles / "staged_only.yaml").write_text(marked, encoding="utf-8")
    (profiles / "clean.yaml").write_text(clean, encoding="utf-8")
    (profiles / "untracked.yaml").write_text(marked, encoding="utf-8")
    added = _git(repo, "add", f"{PROFILES_REL}/staged_only.yaml", f"{PROFILES_REL}/clean.yaml")
    assert added.returncode == 0
    # 인덱스에만 표기가 남고 작업 트리는 되돌린 경우
    (profiles / "staged_only.yaml").write_text(clean, encoding="utf-8")
    # 작업 트리에서만 표기가 붙은 추적 파일
    (profiles / "clean.yaml").write_text(marked, encoding="utf-8")

    assert find_local_sandbox_violations(repo) == [
        f"{PROFILES_REL}/clean.yaml (작업 트리)",
        f"{PROFILES_REL}/staged_only.yaml (인덱스)",
    ]
