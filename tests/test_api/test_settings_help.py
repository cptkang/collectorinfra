"""설정 옵션 상세 도움말 검증 (D-191).

- 커버리지 게이트: 1차 큐레이션 대상 그룹은 **전 항목**에 사람이 쓴 설명이 있어야 한다.
  카탈로그에 필드가 늘면 이 테스트가 먼저 실패해 YAML 누락을 잡는다.
- 자동 파생: 큐레이션이 없는 키도 도움말이 비지 않는다.
- 「반영 시점과 제약」은 YAML이 아니라 카탈로그 실측에서 나온다(문서-코드 괴리 방지).
- 외부 네트워크·LLM 호출 0건(D-127).
"""

from __future__ import annotations

import glob
import re
from pathlib import Path

import pytest
import yaml

from src.api.settings_catalog import GROUP_TITLES, field_index
from src.api.settings_help import (
    _axis_for,
    build_help,
    curated_index,
    curated_keys,
    reset_help_cache,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_HELP_DIR = _PROJECT_ROOT / "config" / "settings_help"

#: 큐레이션 대상 — **전 그룹**이다(2026-08-31 2차 확대). 1차는 4개 축 228건이었으나
#: 사용자 지시로 전 항목을 코드 실측 기반으로 작성했다. 자동 파생은 이제 폴백일 뿐,
#: 정상 상태에서는 모든 항목이 사람이 쓴 설명을 갖는다.
CURATED_GROUPS: frozenset[str] = frozenset(GROUP_TITLES)


@pytest.fixture(autouse=True)
def _fresh_cache():
    """YAML 캐시를 매 테스트마다 비운다."""
    reset_help_cache()
    yield
    reset_help_cache()


# --- T1: 큐레이션 파일 자체 ---


def test_t1_every_help_yaml_parses():
    """도움말 YAML은 전부 파싱되고 settings 매핑을 가진다."""
    files = sorted(glob.glob(str(_HELP_DIR / "*.yaml")))
    assert files, "도움말 YAML이 하나도 없다"
    for path in files:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        assert isinstance(data, dict), path
        assert isinstance(data.get("settings"), dict), f"{path}: settings 매핑 없음"


def test_t1_curated_keys_all_exist_in_catalog():
    """큐레이션한 키는 전부 실제 설정이어야 한다(오타·폐기 키 차단)."""
    unknown = sorted(curated_keys() - set(field_index()))
    assert not unknown, f"카탈로그에 없는 키를 큐레이션했다: {unknown}"


def test_t1_related_links_resolve():
    """`related`가 가리키는 키는 실제로 존재해야 한다(패널의 깨진 링크 차단)."""
    index = set(field_index())
    broken: list[str] = []
    for env_key, entry in curated_index().items():
        for target in entry.get("related") or []:
            if target not in index:
                broken.append(f"{env_key} -> {target}")
    assert not broken, f"존재하지 않는 설정을 참조한다: {broken}"


# --- T2: 커버리지 게이트 ---


def test_t2_curated_groups_have_full_coverage():
    """모든 설정이 사람이 쓴 설명을 갖는다(커버리지 100%).

    config.py에 필드가 추가되면 이 테스트가 먼저 실패한다 — 설명 없는 항목이
    조용히 늘어나는 것을 막는다.
    """
    curated = curated_keys()
    missing = sorted(
        key for key, spec in field_index().items()
        if spec.group_key in CURATED_GROUPS and key not in curated
    )
    assert not missing, (
        f"큐레이션 누락 {len(missing)}건 — config/settings_help/에 추가할 것: {missing}"
    )


def test_t2_curated_entries_have_required_sections():
    """큐레이션 항목은 최소한 요약과 동작 서술을 갖는다."""
    thin = [
        key for key, entry in curated_index().items()
        if not str(entry.get("summary") or "").strip()
        or not str(entry.get("behavior") or "").strip()
    ]
    assert not thin, f"summary/behavior가 빈 항목: {thin}"


def test_t2_every_curated_entry_has_an_example():
    """모든 큐레이션 항목이 구체 사례를 갖는다.

    "이 값을 이렇게 두면 무엇이 벌어지는가"는 사례 없이는 전달되지 않는다 —
    사용자 요구가 그 지점이었으므로 게이트로 고정한다.
    """
    missing = sorted(k for k, e in curated_index().items() if not str(e.get("example") or "").strip())
    assert not missing, f"사례 누락 {len(missing)}건: {missing[:20]}"


def test_t2_no_unquotable_scalar_traps():
    """값이 YAML 특수문자로 시작하면 파싱이 깨진다 — 블록 스칼라로 써야 한다.

    한국어 설명에서 `"사용률"처럼 …`·백틱 시작 문장이 실제로 세 번 파일을 통째로
    무효화했다. 로더가 예외를 삼키고 경고만 남기므로 CI에서 잡는다.
    """
    import yaml as _yaml

    offenders: list[str] = []
    for path in sorted(glob.glob(str(_HELP_DIR / "*.yaml"))):
        for lineno, line in enumerate(open(path, encoding="utf-8"), 1):
            m = re.match(r"^\s*(-?\s*)(\w+):\s+(\S.*)$", line)
            if not m:
                continue
            value = m.group(3)
            if value.startswith((">-", "|-", ">", "|", "[", "{")):
                continue
            try:
                _yaml.safe_load(f"{m.group(2)}: {value}")
            except Exception:
                offenders.append(f"{Path(path).name}:{lineno}")
    assert not offenders, f"블록 스칼라로 바꿔야 하는 값: {offenders}"


# --- T3: 자동 파생 ---


def test_t3_every_catalog_key_yields_help():
    """카탈로그의 모든 키가 도움말을 낸다 — 패널이 비는 항목이 없다."""
    for key in field_index():
        help_entry = build_help(key)
        assert help_entry is not None, key
        assert help_entry.summary.strip(), f"{key}: 요약이 비었다"
        assert help_entry.operational, f"{key}: 반영 시점 안내가 비었다"


def test_t3_unknown_key_returns_none():
    assert build_help("NO_SUCH_SETTING_KEY") is None


def test_t3_derived_source_for_uncurated_key(monkeypatch):
    """큐레이션이 없으면 source가 derived다."""
    monkeypatch.setattr("src.api.settings_help.curated_index", lambda: {})
    spec_key = next(iter(field_index()))
    assert build_help(spec_key).source == "derived"


def test_t3_bool_derivation_lists_both_values(monkeypatch):
    monkeypatch.setattr("src.api.settings_help.curated_index", lambda: {})
    help_entry = build_help("ENABLE_SQL_APPROVAL")
    assert [o.value for o in help_entry.options] == ["true", "false"]
    assert [o.is_default for o in help_entry.options] == [False, True]


def test_t3_tristate_derivation_offers_auto(monkeypatch):
    monkeypatch.setattr("src.api.settings_help.curated_index", lambda: {})
    values = [o.value for o in build_help("ENABLE_SEMANTIC_ROUTING").options]
    assert values == ["(미설정)", "true", "false"]


def test_t3_enum_derivation_lists_choices(monkeypatch):
    monkeypatch.setattr("src.api.settings_help.curated_index", lambda: {})
    values = [o.value for o in build_help("LOG_LEVEL").options]
    assert values == ["DEBUG", "INFO", "WARNING", "ERROR"]


@pytest.mark.parametrize(
    "env_key,expected_axis",
    [
        ("LLM_OLLAMA_TIMEOUT", "시간 예산"),
        ("QUERY_DEFAULT_LIMIT", "조회·처리 상한"),
        ("MAX_REPLAN", "재시도 예산"),
        ("NOISE_ANOMALY_BASELINE_CACHE_TTL_SECONDS", "캐시 수명"),
        ("NOISE_STORM_WINDOW_SECONDS", "관측 시간창"),
        ("AUDIT_RETENTION_DAYS", "보존 기간"),
        # 재귀 상한은 "조회 상한"이 아니라 반복 예산이다 — 축 순서가 이를 보장한다
        ("ORCHESTRATOR_RECURSION_LIMIT", "재시도 예산"),
    ],
)
def test_t3_numeric_axis_detection(env_key, expected_axis):
    """숫자형 설정은 키 이름으로 트레이드오프 축이 판정된다."""
    axis = _axis_for(env_key)
    assert axis is not None, env_key
    assert axis.name == expected_axis


def test_t3_sample_count_is_not_mistaken_for_interval():
    """`MIN_PERIODS`(표본 수)를 실행 주기로 오분류하지 않는다."""
    assert _axis_for("NOISE_ANOMALY_MIN_PERIODS") is None


def test_t3_unknown_axis_yields_no_direction(monkeypatch):
    """축을 모르는 숫자 설정에는 방향 설명을 지어내지 않는다."""
    monkeypatch.setattr("src.api.settings_help.curated_index", lambda: {})
    help_entry = build_help("AUDIT_NIGHT_ALERT_START")
    assert _axis_for("AUDIT_NIGHT_ALERT_START") is None
    assert help_entry.options == []
    assert help_entry.performance is None


# --- T4: 「반영 시점과 제약」은 항상 카탈로그 실측 ---


def test_t4_apply_mode_text_matches_catalog():
    """반영 시점 문구가 카탈로그의 apply_mode를 따른다."""
    index = field_index()
    restart_key = next(k for k, s in index.items() if s.apply_mode == "restart")
    reload_key = next(k for k, s in index.items() if s.apply_mode == "reload")
    assert "재시작" in build_help(restart_key).operational[0]
    assert "설정 리로드" in build_help(reload_key).operational[0]


def test_t4_os_override_warns_even_when_curated():
    """큐레이션 항목에도 오버라이드 경고가 자동으로 붙는다."""
    notes = build_help("NOISE_ENABLE_NOISE_GATE", {"override": "os"}).operational
    assert any("OS 환경변수" in n for n in notes)


def test_t4_encenv_override_warns():
    notes = build_help("LOG_LEVEL", {"override": "encenv"}).operational
    assert any(".encenv" in n for n in notes)


def test_t4_unconsumed_field_is_flagged():
    """미소비 필드는 '바꿔도 동작이 달라지지 않는다'를 명시한다."""
    unconsumed_key = next(k for k, s in field_index().items() if not s.consumed)
    assert any("미소비" in n for n in build_help(unconsumed_key).operational)


def test_t4_secret_field_is_flagged():
    secret_key = next(k for k, s in field_index().items() if s.is_secret)
    assert any(".encenv" in n for n in build_help(secret_key).operational)


# --- T5: 큐레이션 값과 실측의 경계 ---


def test_t5_default_marker_comes_from_catalog_not_yaml(monkeypatch):
    """`is_default`는 YAML이 아니라 카탈로그 기본값으로 채운다."""
    monkeypatch.setattr(
        "src.api.settings_help.curated_index",
        lambda: {
            "ENABLE_SQL_APPROVAL": {
                "summary": "s",
                "behavior": "b",
                # YAML이 잘못된 기본 표시를 담고 있어도 무시돼야 한다
                "options": [
                    {"value": "true", "effect": "e", "is_default": True},
                    {"value": "false", "effect": "e"},
                ],
            }
        },
    )
    options = {o.value: o for o in build_help("ENABLE_SQL_APPROVAL").options}
    assert options["true"].is_default is False
    assert options["false"].is_default is True


def test_t5_current_value_marks_matching_option():
    options = {
        o.value: o
        for o in build_help("LOG_LEVEL", {"effective_value": "DEBUG"}).options
    }
    assert options["DEBUG"].is_current is True
    assert options["INFO"].is_current is False


def test_t5_partial_curation_falls_back_to_derived(monkeypatch):
    """일부 절만 쓴 항목은 나머지를 자동 파생으로 메운다."""
    monkeypatch.setattr(
        "src.api.settings_help.curated_index",
        lambda: {"LLM_OLLAMA_TIMEOUT": {"summary": "직접 쓴 요약", "behavior": "직접 쓴 동작"}},
    )
    help_entry = build_help("LLM_OLLAMA_TIMEOUT")
    assert help_entry.source == "curated"
    assert help_entry.summary == "직접 쓴 요약"
    assert help_entry.options, "빠진 절은 자동 파생으로 채워야 한다"
    assert help_entry.performance is not None


def test_t5_broken_yaml_does_not_break_help(monkeypatch, tmp_path):
    """도움말 YAML이 깨져도 설정 화면 전체가 막히지 않는다."""
    bad = tmp_path / "broken.yaml"
    bad.write_text("settings: [not, a, mapping]\n", encoding="utf-8")
    monkeypatch.setattr("src.api.settings_help._HELP_DIR", tmp_path)
    reset_help_cache()
    assert curated_index() == {}
    assert build_help("LOG_LEVEL") is not None


# --- T6: 엔드포인트 ---


@pytest.mark.asyncio
async def test_t6_endpoint_returns_help_for_known_key():
    from src.api.routes.admin import get_setting_help

    result = await get_setting_help("NOISE_ENABLE_NOISE_GATE", {"sub": "admin"})
    assert result.env_key == "NOISE_ENABLE_NOISE_GATE"
    assert result.source == "curated"
    assert result.options


@pytest.mark.asyncio
async def test_t6_endpoint_404_for_unknown_key():
    from fastapi import HTTPException

    from src.api.routes.admin import get_setting_help

    with pytest.raises(HTTPException) as exc:
        await get_setting_help("NOT_A_SETTING", {"sub": "admin"})
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_t6_endpoint_survives_catalog_failure(monkeypatch):
    """현재값 메타를 못 구해도 도움말 본문은 나간다."""
    from src.api.routes import admin as admin_module

    def _boom(**_kwargs):
        raise RuntimeError("카탈로그 실패")

    monkeypatch.setattr(admin_module, "build_catalog", _boom)
    result = await admin_module.get_setting_help("LOG_LEVEL", {"sub": "admin"})
    assert result.env_key == "LOG_LEVEL"
    assert result.operational
