"""e2e 조사 LLM 배선 선택 검증 — 실 LLM·네트워크 없이 (2026-09-17 결함: API_BASE를 줘도 Gemini로 조립 · D-229/D-230).

`test_investigation_e2e.py`는 파일 전체가 RUN_E2E 게이트라 그 안에 둘 수 없어 여기서 고정한다.
"""

from sre_agent.settings import AgentSettings
from test_investigation_e2e import _llm_ready, _llm_wiring


def _base(**kw) -> AgentSettings:
    defaults = dict(model="openai/served-name", api_key=None, api_base=None,
                    investigation_llm_model="gemini/gemini-3.5-flash", gemini_api_key=None)
    return AgentSettings(_env_file=None, **{**defaults, **kw})


def test_api_base_selects_operational_wiring_not_gemini():
    base = _base(api_base="http://127.0.0.1:8000/v1", api_key="dummy", gemini_api_key="real-key",
                 override_max_content_size=30000, override_max_output_token=2048)

    s = AgentSettings(_env_file=None, **_llm_wiring(base))

    assert s.model == "openai/served-name"            # investigation_llm_model(Gemini) 아님
    assert s.api_base == "http://127.0.0.1:8000/v1"
    assert s.api_key.get_secret_value() == "dummy"     # Gemini 키가 새지 않는다
    assert s.gemini_api_key is None                    # 운영 배선에는 Gemini 키를 싣지 않는다
    assert (s.override_max_content_size, s.override_max_output_token) == (30000, 2048)
    assert s.investigation_llm_stub_reason() is None   # 게이트는 플래그로 연다(D-230 · 더미 키 불요)
    assert _llm_ready(base) is True


def test_without_api_base_gemini_path_unchanged():
    base = _base(gemini_api_key="g-key")

    s = AgentSettings(_env_file=None, **_llm_wiring(base))

    assert (s.model, s.api_key, s.gemini_api_key) == ("gemini/gemini-3.5-flash", base.gemini_api_key, base.gemini_api_key)
    assert s.investigation_llm_enabled is None         # 종전 판정(키 유무) 그대로
    assert _llm_ready(base) is True
    assert _llm_ready(_base()) is False                 # 키도 API_BASE도 없으면 skip
    assert _llm_ready(_base(gemini_api_key="")) is False
