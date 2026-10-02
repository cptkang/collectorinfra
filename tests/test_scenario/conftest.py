"""시나리오 하네스 테스트 공용 픽스처 (plans/94 §10).

전부 무과금이다. 네트워크 접속도 서버 기동도 하지 않는다 - 로더/단언기/리포트/분석기는
순수 함수 경계로 설계돼 있어 프로세스 없이 검증된다.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _form_memory_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """러너의 폼필 확인 이력 조회(plans/120 V-4)가 로컬 Redis 에 닿지 않게 한다.

    실 모드 러너는 업로드 시나리오마다 이력을 읽는다. 테스트는 "닿지 못함"(샌드박스)으로 고정하고,
    그 경로를 시험하는 테스트만 `monkeypatch` 로 다시 바꾼다.
    """
    from scripts.scenario import runner

    def unreachable(_work: object) -> None:
        raise RuntimeError("테스트 - Redis 미사용")

    monkeypatch.setattr(runner, "_with_form_memory", unreachable)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


@pytest.fixture()
def profiles_path(tmp_path: Path) -> Path:
    return write(
        tmp_path / "profiles.yaml",
        "profiles:\n  baseline: {}\n  optin_query:\n    TEXT2SQL_MULTI_CANDIDATE: \"true\"\n",
    )


@pytest.fixture()
def scenario_dir(tmp_path: Path) -> Path:
    path = tmp_path / "scenarios"
    path.mkdir()
    return path


GOOD_GROUP = """version: 1
group:
  id: T
  name: "테스트군"
  latency_target_ms: 10000
scenarios:
  - id: T-01
    plans: [94]
    title: "정상"
    turns:
      - send: {query: "서버 목록"}
        expect: {status: completed}
"""
