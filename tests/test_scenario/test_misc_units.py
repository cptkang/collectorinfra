"""나머지 단위 — 프로파일 로더·provenance·모의 서버 해석기 (plans/94).

통합 테스트가 덮는 경로라도 **실패했을 때 어디가 틀렸는지** 알려면 단위가 필요하다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.scenario.catalog import Catalog, CatalogError, Group, Scenario, Turn, load_profiles
from scripts.scenario.mockserver import _Resolver, _payload_for
from scripts.scenario.report import load_rows, load_run_meta
from scripts.scenario.runner import RunConfig, next_run_id, run_meta
from scripts.scenario.server import ProfileStatus
from tests.test_scenario.conftest import write


# --- 프로파일 로더 --------------------------------------------------------

def test_baseline_없는_프로파일은_거부된다(tmp_path: Path) -> None:
    """운영 설정 기준선이 사라지면 비교 대상이 없어진다."""
    path = write(tmp_path / "p.yaml", "profiles:\n  optin: {}\n")
    with pytest.raises(CatalogError) as exc:
        load_profiles(path)
    assert any("baseline" in error for error in exc.value.errors)


def test_문자열이_아닌_값은_거부된다(tmp_path: Path) -> None:
    """환경변수로 주입되므로 문자열이어야 한다 - true 는 bool 로 파싱된다."""
    path = write(tmp_path / "p.yaml", "profiles:\n  baseline: {}\n  x:\n    FLAG: true\n")
    with pytest.raises(CatalogError) as exc:
        load_profiles(path)
    assert any("문자열" in error for error in exc.value.errors)


def test_빈_프로파일은_허용된다(tmp_path: Path) -> None:
    path = write(tmp_path / "p.yaml", "profiles:\n  baseline:\n")
    assert load_profiles(path) == {"baseline": {}}


def test_파일이_없으면_사유와_함께_거부된다(tmp_path: Path) -> None:
    with pytest.raises(CatalogError) as exc:
        load_profiles(tmp_path / "없음.yaml")
    assert "없다" in exc.value.errors[0]


def test_저장소_프로파일이_로드된다() -> None:
    profiles = load_profiles()
    assert "baseline" in profiles and profiles["baseline"] == {}
    assert all(isinstance(v, str) for m in profiles.values() for v in m.values())


# --- provenance -----------------------------------------------------------

def test_run_meta_는_재현에_필요한_것을_전부_남긴다() -> None:
    """커밋·dirty·환경·플랫폼 없이 나온 결과는 재현할 수 없다(§4.5).

    run_meta 는 설정 스냅샷을 위해 load_config() 를 부른다. 그 함수는 lru_cache(maxsize=1)
    라(config.py:1383) 여기서 채운 캐시가 뒤 테스트로 새면 남의 판정을 바꾼다 - 테스트가
    제 뒤를 치우는 것이 원칙이다.
    """
    from src.config import load_config

    load_config.cache_clear()
    catalog = Catalog(groups={}, scenarios=[], profiles={"baseline": {}})
    try:
        meta = run_meta(RunConfig(mode="mock", env="sandbox"), catalog)
    finally:
        load_config.cache_clear()
    for key in ("run_id", "mode", "env", "provider", "commit", "dirty",
                "started_at", "repeat", "platform", "host"):
        assert key in meta, f"provenance 에 {key} 가 없다"
    assert meta["provider"] == "mock"       # 모의 실행은 프로바이더를 속이지 않는다


def test_run_id_는_경로_길이를_고려해_짧다() -> None:
    """깊은 run 디렉터리에서 260자 제한에 걸린다(부록 A.2)."""
    assert len(next_run_id()) <= 16


def test_resume_는_기존_run_id_를_그대로_쓴다() -> None:
    assert RunConfig(resume_from="20260911-000001").resolved_run_id() == "20260911-000001"


def test_ProfileStatus_는_사유를_직렬화한다() -> None:
    status = ProfileStatus(name="baseline", port=1, reasons=["헬스 실패: x"])
    payload = status.as_dict()
    assert payload["valid"] is False
    assert payload["reasons"] == ["헬스 실패: x"]


# --- 리포트 입력 ----------------------------------------------------------

def test_raw가_없으면_빈_목록이다(tmp_path: Path) -> None:
    assert load_rows(tmp_path) == []


def test_깨진_JSONL_줄은_건너뛴다(tmp_path: Path) -> None:
    (tmp_path / "raw.jsonl").write_text(
        '{"scenario_id":"A-01"}\n깨진줄\n\n{"scenario_id":"A-02"}\n',
        encoding="utf-8", newline="\n",
    )
    rows = load_rows(tmp_path)
    assert [r["scenario_id"] for r in rows] == ["A-01", "A-02"]


def test_run_json이_없어도_리포트가_돈다(tmp_path: Path) -> None:
    meta = load_run_meta(tmp_path)
    assert meta["meta"] == {} and meta["skipped"] == []


# --- 모의 서버 해석기 -----------------------------------------------------

def _catalog() -> Catalog:
    scenario = Scenario(
        id="F-01", group="F", plans=[82], title="t",
        turns=[Turn({"query": "전체 서버 OS"}, {}), Turn({"selected_db_ids": ["gp"]}, {})],
        mock={"turns": [{"response": "1턴"}, {"response": "2턴", "row_count": 7}]},
    )
    return Catalog(groups={"F": Group(id="F", name="f", latency_target_ms=1)},
                   scenarios=[scenario], profiles={"baseline": {}})


def test_1턴은_질의로_2턴은_스레드로_되짚는다() -> None:
    """구조화 필드만 보내는 턴에는 질의 문자열이 없다."""
    resolver = _Resolver(_catalog())
    scenario, turn = resolver.resolve("전체 서버 OS", "t1")
    assert scenario is not None and scenario.id == "F-01" and turn == 1
    scenario, turn = resolver.resolve(None, "t1")
    assert scenario is not None and scenario.id == "F-01" and turn == 2


def test_스레드가_다르면_다시_1턴이다() -> None:
    resolver = _Resolver(_catalog())
    resolver.resolve("전체 서버 OS", "t1")
    _scenario, turn = resolver.resolve("전체 서버 OS", "t2")
    assert turn == 1


def test_모르는_질의는_canned_응답으로_떨어진다() -> None:
    resolver = _Resolver(_catalog())
    scenario, turn = resolver.resolve("처음 보는 질의", "t9")
    assert scenario is None and turn == 1
    payload = _payload_for(None, 1, "처음 보는 질의")
    assert payload["row_count"] == 5 and "[mock]" in payload["response"]


def test_턴별_mock_블록이_적용된다() -> None:
    scenario = _catalog().scenarios[0]
    assert _payload_for(scenario, 1, "q")["response"] == "1턴"
    assert _payload_for(scenario, 2, "q")["row_count"] == 7


def test_카탈로그가_없어도_해석기는_죽지_않는다() -> None:
    """카탈로그가 깨져도 모의 서버는 떠야 사유를 볼 수 있다."""
    resolver = _Resolver(None)
    assert resolver.resolve("무엇이든", "t") == (None, 1)
