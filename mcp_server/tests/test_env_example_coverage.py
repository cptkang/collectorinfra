"""`.env.example`이 실제 오버라이드 표면을 빠짐없이 문서화하는지 고정한다 (D-181 후속 · docs/27 §8.1).

## 왜 이 테스트가 필요한가

`.env.example`은 배포자가 **"무엇을 설정할 수 있는가"를 알게 되는 유일한 창구**다. `config.toml`에도
주석은 있지만 보안 값(`MCP_BEARER_TOKEN`·`PROMETHEUS_AUTH_HEADER`)은 애초에 TOML에 쓰면 안 되므로
**`.env.example`에만 있을 수 있는 키**가 존재한다. 2026-08-28 실측에서 오버라이드 키 14종 중
**9종이 누락**돼 있었고, 그중에는 다음이 포함됐다:

    MCP_BEARER_TOKEN     미설정 시 인증 없이 뜬다 — 존재를 모르면 그 상태를 정상으로 오인한다
    EXPOSE_RAW_PROMQL    D-122가 "조사 배치는 반드시 false"라고 요구하는 키
    PROMETHEUS_URL       미설정 시 PromQL 도구 전건 실패 — 원인이 설정 누락임을 알 길이 없다

키를 한 번 채워 넣는 것만으로는 **다음에 키가 늘 때 같은 공백이 다시 생긴다.** 그래서 목록을
복제하지 않고 `_apply_env_overrides`의 **소스에서 직접 추출**해 대조한다(복제한 목록은 그 자체가
다음 번 누락 지점이다 — 본체 `settings_catalog` 전수 회귀와 같은 발상).

## mcp 패키지에 의존하지 않는다

`config.py`는 `mcp`를 임포트하지 않으므로 이 파일에는 임포트 가드가 없다. D-181에서 `mcp` 2.x가
`mcp.server.fastmcp`를 없앴을 때 가드가 걸린 테스트들이 **전건 skip으로 조용히 통과**했는데,
이 테스트는 그 경로로 무력화되지 않는다.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from mcp_server import config as config_mod
from mcp_server.config import _apply_env_overrides, _load_toml

_REPO = Path(__file__).resolve().parents[1]
_ENV_EXAMPLE = _REPO / ".env.example"
_CONFIG_TOML = _REPO / "config.toml"

#: `.env.example`에서 `KEY=` / `# KEY=` 두 형태를 모두 문서화로 인정한다
#: (예제 파일은 선택 키를 주석 처리해 제시하는 것이 관례다).
_DOCUMENTED_RE = re.compile(r"^\s*#?\s*([A-Z][A-Z0-9_]{2,})\s*=", re.MULTILINE)

#: `_apply_env_overrides` 소스에서 환경변수 키로 쓰인 대문자 리터럴.
_ENV_KEY_RE = re.compile(r'"([A-Z][A-Z0-9_]{2,})"')

#: 키가 아니라 값 후보로 등장하는 리터럴(불리언 파싱의 "1"/"TRUE" 등)을 제외한다.
_NOT_ENV_KEYS = {"TCPIP"}


def _documented_keys() -> set[str]:
    """`.env.example`이 제시하는 환경변수 키 집합."""
    return set(_DOCUMENTED_RE.findall(_ENV_EXAMPLE.read_text(encoding="utf-8")))


def _override_keys() -> set[str]:
    """`_apply_env_overrides`가 실제로 읽는 환경변수 키 집합 (소스에서 추출).

    목록을 테스트에 복제하지 않는 것이 핵심이다 — 복제하면 그 사본이 다음 누락 지점이 된다.
    """
    src = inspect.getsource(_apply_env_overrides)
    return {k for k in _ENV_KEY_RE.findall(src) if k not in _NOT_ENV_KEYS}


def _toml_source_names() -> set[str]:
    """`config.toml`이 정의한 데이터소스 이름 (대문자).

    `_load_toml`은 dict가 아니라 `AppServerConfig`를 돌려준다(실측) — `.sources`는
    `SourceConfig` 리스트다.
    """
    return {s.name.upper() for s in _load_toml(_CONFIG_TOML).sources if s.name}


# =====================================================================
# 추출기 자체의 자가 검증 — 추출이 비면 아래 단언들이 공허하게 통과한다
# =====================================================================

def test_extractor_finds_the_known_keys():
    """★ 추출기가 실제로 키를 뽑는지 먼저 고정한다.

    정규식이 아무것도 못 잡으면 커버리지 단언이 **빈 집합 ⊆ 무엇이든**으로 통과해버린다.
    알려진 대표 키 몇 개로 추출기가 살아 있음을 보장한다.
    """
    keys = _override_keys()
    assert {"SERVER_PORT", "MCP_BEARER_TOKEN", "PROMETHEUS_URL",
            "EXPOSE_RAW_PROMQL"} <= keys
    assert len(keys) >= 10


def test_env_example_is_parseable():
    """`.env.example`에서 키를 실제로 뽑아낸다(파일 경로·형식 자가 검증)."""
    assert _ENV_EXAMPLE.is_file(), f"{_ENV_EXAMPLE} 없음"
    assert "SERVER_PORT" in _documented_keys()


# =====================================================================
# 커버리지 — 여기가 본체
# =====================================================================

def test_every_override_key_is_documented():
    """★ `_apply_env_overrides`가 읽는 키는 전부 `.env.example`에 있어야 한다.

    누락은 "존재를 모르는 설정"을 만든다 — 특히 인증(`MCP_BEARER_TOKEN`)과 배치별 게이트
    (`EXPOSE_*` · D-122)는 모르면 잘못된 기본값으로 뜬 것을 정상으로 오인한다.
    """
    missing = sorted(_override_keys() - _documented_keys())
    assert not missing, (
        f"`.env.example`에 누락된 오버라이드 키: {missing}\n"
        "→ mcp_server/.env.example에 (주석 형태라도) 추가하고 기본값·배치별 권장값을 적을 것."
    )


@pytest.mark.parametrize("name", ["polestar_cm_gp", "polestar_cm_yd", "polestar_b0"])
def test_defined_sources_have_documented_connection_key(name):
    """`config.toml`이 정의한 소스의 연결 키가 문서화돼 있어야 한다.

    실제 `mcp_server/.env`가 쓰고 있는데 예제에 없으면 **현행 운영 설정을 예제만 보고
    재현할 수 없다**(2026-08-28 실측: gp·yd가 그 상태였다).
    """
    assert f"{name.upper()}_CONNECTION" in _documented_keys()


def test_no_ghost_connection_keys():
    """★ 역방향 — 예제의 `*_CONNECTION`은 전부 `config.toml`의 소스에 대응해야 한다.

    대응 소스가 없는 키는 **값을 채워도 조용히 무시된다**(오류도 로그도 없다).
    2026-08-28 실측에서 `ITAM_CONNECTION`이 이 상태였다 — `config/db_registry.yaml`에는
    itam DB가 등록돼 있으나 `config.toml`에 `[[sources]] name="itam"`이 없었다.
    """
    defined = _toml_source_names()
    ghosts = sorted(
        k for k in _documented_keys()
        if k.endswith("_CONNECTION") and k[: -len("_CONNECTION")] not in defined
    )
    assert not ghosts, (
        f"config.toml에 대응 소스가 없는 연결 키: {ghosts}\n"
        "→ [[sources]]를 추가하거나 예제에서 제거할 것 (설정해도 무시되는 키다)."
    )


def test_prometheus_keys_documented_together():
    """Prometheus 4키는 함께 문서화한다 — URL만 알고 timeout·인증을 모르면 반쪽이다."""
    documented = _documented_keys()
    for key in ("PROMETHEUS_URL", "PROMETHEUS_AUTH_HEADER",
                "PROMETHEUS_QUERY_TIMEOUT", "EXPOSE_RAW_PROMQL"):
        assert key in documented, f"{key} 미문서화 (docs/27 §3.1)"
