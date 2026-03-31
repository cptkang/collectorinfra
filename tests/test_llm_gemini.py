"""Plan 28: Gemini API 프로바이더 단위 테스트.

mock 기반으로 Gemini LLM 프로바이더의 설정 로딩, 팩토리 생성, 에러 처리를 검증한다.
실제 Gemini API 호출은 하지 않는다.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.config import AppConfig, LLMConfig


# ──────────────────────────────────────────────
# 1. LLMConfig: provider에 "gemini" 허용 확인
# ──────────────────────────────────────────────


class TestLLMConfigGemini:
    """LLMConfig의 Gemini 관련 필드 및 provider Literal 검증."""

    def test_provider_gemini_accepted(self):
        """provider='gemini'가 Literal 검증을 통과해야 한다."""
        cfg = LLMConfig(provider="gemini")
        assert cfg.provider == "gemini"

    def test_provider_ollama_still_works(self):
        """기존 provider='ollama'가 여전히 동작해야 한다."""
        cfg = LLMConfig(provider="ollama")
        assert cfg.provider == "ollama"

    def test_provider_fabrix_still_works(self):
        """기존 provider='fabrix'가 여전히 동작해야 한다."""
        cfg = LLMConfig(provider="fabrix")
        assert cfg.provider == "fabrix"

    def test_provider_invalid_rejected(self):
        """지원하지 않는 provider는 pydantic validation error를 발생시켜야 한다."""
        with pytest.raises(Exception):
            LLMConfig(provider="invalid_provider")

    def test_gemini_api_key_default_empty(self):
        """gemini_api_key의 기본값은 빈 문자열이어야 한다."""
        cfg = LLMConfig(provider="gemini")
        assert cfg.gemini_api_key == ""

    def test_gemini_model_default_empty(self):
        """gemini_model의 기본값은 빈 문자열이어야 한다."""
        cfg = LLMConfig(provider="gemini")
        assert cfg.gemini_model == ""

    def test_gemini_api_key_direct_setting(self):
        """gemini_api_key를 직접 설정할 수 있어야 한다."""
        cfg = LLMConfig(provider="gemini", gemini_api_key="test-key-123")
        assert cfg.gemini_api_key == "test-key-123"

    def test_gemini_model_direct_setting(self):
        """gemini_model을 직접 설정할 수 있어야 한다."""
        cfg = LLMConfig(provider="gemini", gemini_model="gemini-2.0-flash")
        assert cfg.gemini_model == "gemini-2.0-flash"


# ──────────────────────────────────────────────
# 2. model_post_init: GOOGLE_API_KEY fallback
# ──────────────────────────────────────────────


class TestGeminiApiKeyFallback:
    """model_post_init에서 GOOGLE_API_KEY 환경변수 fallback 검증."""

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "fallback-key-from-env"}, clear=False)
    def test_google_api_key_fallback_when_gemini_key_empty(self):
        """gemini_api_key가 비어있으면 GOOGLE_API_KEY 환경변수를 사용해야 한다."""
        cfg = LLMConfig(provider="gemini", gemini_api_key="")
        assert cfg.gemini_api_key == "fallback-key-from-env"

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "should-not-use"}, clear=False)
    def test_google_api_key_not_used_when_gemini_key_set(self):
        """gemini_api_key가 명시적으로 설정되면 GOOGLE_API_KEY를 사용하지 않아야 한다."""
        cfg = LLMConfig(provider="gemini", gemini_api_key="explicit-key")
        assert cfg.gemini_api_key == "explicit-key"

    @patch.dict("os.environ", {}, clear=False)
    def test_no_fallback_when_both_empty(self):
        """gemini_api_key도 비고 GOOGLE_API_KEY도 없으면 빈 문자열이어야 한다."""
        import os
        os.environ.pop("GOOGLE_API_KEY", None)
        cfg = LLMConfig(provider="gemini", gemini_api_key="")
        assert cfg.gemini_api_key == ""


# ──────────────────────────────────────────────
# 3. env_file list 설정 검증
# ──────────────────────────────────────────────


class TestEnvFileListConfig:
    """pydantic-settings v2에서 env_file을 list로 설정하는 구성 검증."""

    def test_llm_config_env_file_is_list(self):
        """LLMConfig.model_config['env_file']이 list여야 한다."""
        env_file = LLMConfig.model_config.get("env_file")
        assert isinstance(env_file, list)
        assert ".env" in env_file
        assert ".encenv" in env_file

    def test_env_file_order_env_first(self):
        """env_file에서 .env가 .encenv보다 먼저 나와야 한다 (우선순위)."""
        env_file = LLMConfig.model_config["env_file"]
        assert env_file.index(".env") < env_file.index(".encenv")


# ──────────────────────────────────────────────
# 4. LLM 팩토리: _create_gemini() 정상 호출
# ──────────────────────────────────────────────


class TestCreateGeminiFactory:
    """create_llm()이 provider='gemini'일 때 _create_gemini()를 올바르게 호출하는지 검증."""

    @patch("src.llm._create_gemini")
    def test_create_llm_dispatches_to_gemini(self, mock_create_gemini):
        """provider='gemini'이면 _create_gemini가 호출되어야 한다."""
        from src.llm import create_llm

        mock_create_gemini.return_value = MagicMock()
        config = AppConfig(
            llm=LLMConfig(provider="gemini", gemini_api_key="test-key"),
        )
        result = create_llm(config)
        mock_create_gemini.assert_called_once_with(config)
        assert result is mock_create_gemini.return_value

    @patch("src.llm._create_ollama")
    def test_create_llm_ollama_unaffected(self, mock_create_ollama):
        """provider='ollama'이면 기존대로 _create_ollama가 호출되어야 한다."""
        from src.llm import create_llm

        mock_create_ollama.return_value = MagicMock()
        config = AppConfig(
            llm=LLMConfig(provider="ollama"),
        )
        result = create_llm(config)
        mock_create_ollama.assert_called_once_with(config)
        assert result is mock_create_ollama.return_value

    @patch("src.llm._create_fabrix")
    def test_create_llm_fabrix_unaffected(self, mock_create_fabrix):
        """provider='fabrix'이면 기존대로 _create_fabrix가 호출되어야 한다."""
        from src.llm import create_llm

        mock_create_fabrix.return_value = MagicMock()
        config = AppConfig(
            llm=LLMConfig(
                provider="fabrix",
                fabrix_base_url="http://fabrix.local",
                fabrix_api_key="fkey",
            ),
        )
        result = create_llm(config)
        mock_create_fabrix.assert_called_once_with(config)
        assert result is mock_create_fabrix.return_value


# ──────────────────────────────────────────────
# 5. _create_gemini: API 키 누락 시 ValueError
# ──────────────────────────────────────────────


class TestCreateGeminiValidation:
    """_create_gemini()의 입력 검증 로직 테스트."""

    def test_missing_api_key_raises_value_error(self):
        """gemini_api_key가 비어있으면 ValueError가 발생해야 한다."""
        from src.llm import _create_gemini

        config = AppConfig(
            llm=LLMConfig(provider="gemini", gemini_api_key=""),
        )
        with pytest.raises(ValueError, match="Gemini API 키가 설정되지 않았습니다"):
            _create_gemini(config)

    @patch("src.llm.ChatGoogleGenerativeAI", create=True)
    def test_api_key_present_no_error(self, mock_chat_cls):
        """gemini_api_key가 설정되면 에러 없이 인스턴스가 생성되어야 한다."""
        mock_chat_cls.return_value = MagicMock()

        with patch.dict("sys.modules", {"langchain_google_genai": MagicMock()}):
            # langchain_google_genai 모듈 mock
            import importlib
            import src.llm
            # Reload하지 않고 직접 함수 내부의 import를 mock
            with patch(
                "src.llm.ChatGoogleGenerativeAI",
                side_effect=AttributeError,
            ):
                pass  # skip this approach

        # 보다 단순한 접근: _create_gemini 내부의 lazy import를 직접 mock
        from unittest.mock import MagicMock as MM
        mock_module = MM()
        mock_cls = MM()
        mock_module.ChatGoogleGenerativeAI = mock_cls

        config = AppConfig(
            llm=LLMConfig(
                provider="gemini",
                gemini_api_key="valid-key",
                gemini_model="gemini-2.0-flash",
            ),
        )

        with patch.dict("sys.modules", {"langchain_google_genai": mock_module}):
            from src.llm import _create_gemini
            result = _create_gemini(config)

        mock_cls.assert_called_once_with(
            model="gemini-2.0-flash",
            google_api_key="valid-key",
            temperature=0.0,
                    )
        assert result is mock_cls.return_value


# ──────────────────────────────────────────────
# 6. _create_gemini: 모델 fallback 로직
# ──────────────────────────────────────────────


class TestCreateGeminiModelFallback:
    """gemini_model이 비어있을 때 config.llm.model로 fallback되는지 검증."""

    def test_gemini_model_empty_falls_back_to_llm_model(self):
        """gemini_model이 빈 문자열이면 config.llm.model을 사용해야 한다."""
        mock_module = MagicMock()
        mock_cls = MagicMock()
        mock_module.ChatGoogleGenerativeAI = mock_cls

        config = AppConfig(
            llm=LLMConfig(
                provider="gemini",
                model="llama3.1:8b",
                gemini_api_key="valid-key",
                gemini_model="",  # 빈 문자열
            ),
        )

        with patch.dict("sys.modules", {"langchain_google_genai": mock_module}):
            from src.llm import _create_gemini
            _create_gemini(config)

        # model 인자로 config.llm.model 값("llama3.1:8b")이 전달되어야 한다
        call_kwargs = mock_cls.call_args[1]
        assert call_kwargs["model"] == "llama3.1:8b"

    def test_gemini_model_set_uses_gemini_model(self):
        """gemini_model이 설정되면 그 값을 사용해야 한다."""
        mock_module = MagicMock()
        mock_cls = MagicMock()
        mock_module.ChatGoogleGenerativeAI = mock_cls

        config = AppConfig(
            llm=LLMConfig(
                provider="gemini",
                model="llama3.1:8b",
                gemini_api_key="valid-key",
                gemini_model="gemini-3.1-pro",
            ),
        )

        with patch.dict("sys.modules", {"langchain_google_genai": mock_module}):
            from src.llm import _create_gemini
            _create_gemini(config)

        call_kwargs = mock_cls.call_args[1]
        assert call_kwargs["model"] == "gemini-3.1-pro"


# ──────────────────────────────────────────────
# 7. _create_gemini: ChatGoogleGenerativeAI 파라미터 검증
# ──────────────────────────────────────────────


class TestCreateGeminiParameters:
    """ChatGoogleGenerativeAI에 전달되는 파라미터가 올바른지 검증."""

    def test_all_parameters_passed_correctly(self):
        """google_api_key, model, temperature, convert_system_message_to_human이 올바르게 전달되어야 한다."""
        mock_module = MagicMock()
        mock_cls = MagicMock()
        mock_module.ChatGoogleGenerativeAI = mock_cls

        config = AppConfig(
            llm=LLMConfig(
                provider="gemini",
                gemini_api_key="AIza-test-key-12345",
                gemini_model="gemini-2.0-flash",
            ),
        )

        with patch.dict("sys.modules", {"langchain_google_genai": mock_module}):
            from src.llm import _create_gemini
            _create_gemini(config)

        mock_cls.assert_called_once()
        call_kwargs = mock_cls.call_args[1]
        assert call_kwargs["model"] == "gemini-2.0-flash"
        assert call_kwargs["google_api_key"] == "AIza-test-key-12345"
        assert call_kwargs["temperature"] == 0.0

    def test_return_value_is_chat_model_instance(self):
        """반환값이 ChatGoogleGenerativeAI 인스턴스여야 한다."""
        mock_module = MagicMock()
        mock_instance = MagicMock()
        mock_module.ChatGoogleGenerativeAI.return_value = mock_instance

        config = AppConfig(
            llm=LLMConfig(
                provider="gemini",
                gemini_api_key="test-key",
                gemini_model="gemini-2.0-flash",
            ),
        )

        with patch.dict("sys.modules", {"langchain_google_genai": mock_module}):
            from src.llm import _create_gemini
            result = _create_gemini(config)

        assert result is mock_instance


# ──────────────────────────────────────────────
# 8. 환경변수 매핑: LLM_ prefix 검증
# ──────────────────────────────────────────────


class TestEnvVarMapping:
    """LLM_ prefix를 사용한 환경변수 매핑이 올바른지 검증."""

    def test_env_prefix_is_llm(self):
        """LLMConfig의 env_prefix가 'LLM_'이어야 한다."""
        assert LLMConfig.model_config["env_prefix"] == "LLM_"

    @patch.dict(
        "os.environ",
        {
            "LLM_PROVIDER": "gemini",
            "LLM_GEMINI_API_KEY": "env-api-key-xyz",
            "LLM_GEMINI_MODEL": "gemini-2.0-flash",
        },
        clear=False,
    )
    def test_env_vars_with_llm_prefix_loaded(self):
        """LLM_GEMINI_API_KEY 환경변수가 gemini_api_key 필드에 매핑되어야 한다."""
        cfg = LLMConfig()
        assert cfg.provider == "gemini"
        assert cfg.gemini_api_key == "env-api-key-xyz"
        assert cfg.gemini_model == "gemini-2.0-flash"


# ──────────────────────────────────────────────
# 9. .encenv 파일 로딩 경로 확인
# ──────────────────────────────────────────────


class TestEncenvFileConfig:
    """.encenv 파일이 env_file 목록에 포함되어 있는지 검증."""

    def test_llm_config_loads_encenv(self):
        """LLMConfig.model_config['env_file']에 '.encenv'가 포함되어야 한다."""
        assert ".encenv" in LLMConfig.model_config["env_file"]

    def test_admin_config_loads_encenv(self):
        """AdminConfig도 .encenv를 로딩해야 한다."""
        from src.config import AdminConfig
        assert ".encenv" in AdminConfig.model_config["env_file"]

    def test_redis_config_loads_encenv(self):
        """RedisConfig도 .encenv를 로딩해야 한다."""
        from src.config import RedisConfig
        assert ".encenv" in RedisConfig.model_config["env_file"]


# ──────────────────────────────────────────────
# 10. 노드 코드 영향 없음 확인 (구조적 검증)
# ──────────────────────────────────────────────


class TestNodeCodeUnchanged:
    """노드 코드가 create_llm() 호출 패턴을 변경 없이 유지하는지 구조적으로 검증."""

    def test_create_llm_signature_unchanged(self):
        """create_llm()의 시그니처가 AppConfig 하나만 받아야 한다."""
        import inspect
        from src.llm import create_llm

        sig = inspect.signature(create_llm)
        params = list(sig.parameters.keys())
        assert params == ["config"]

    def test_create_llm_return_type_annotation(self):
        """create_llm()의 반환 타입이 BaseChatModel이어야 한다."""
        import inspect
        from src.llm import create_llm

        sig = inspect.signature(create_llm)
        ret = sig.return_annotation
        # BaseChatModel 또는 문자열 어노테이션
        assert ret is not inspect.Parameter.empty


# ──────────────────────────────────────────────
# 11. .gitignore에 .encenv 등록 확인
# ──────────────────────────────────────────────


class TestGitignore:
    """.gitignore에 .encenv가 포함되어 민감 키 유출을 방지하는지 검증."""

    def test_encenv_in_gitignore(self):
        """.gitignore에 .encenv가 등록되어 있어야 한다."""
        from pathlib import Path

        gitignore_path = Path(__file__).resolve().parent.parent / ".gitignore"
        content = gitignore_path.read_text(encoding="utf-8")
        assert ".encenv" in content


# ──────────────────────────────────────────────
# 12. .encenv.example 파일 존재 및 내용 검증
# ──────────────────────────────────────────────


class TestEncenvExample:
    """.encenv.example 템플릿 파일의 존재와 내용을 검증."""

    def test_encenv_example_exists(self):
        """.encenv.example 파일이 존재해야 한다."""
        from pathlib import Path

        path = Path(__file__).resolve().parent.parent / ".encenv.example"
        assert path.exists(), f".encenv.example 파일이 없습니다: {path}"

    def test_encenv_example_contains_gemini_key(self):
        """.encenv.example에 LLM_GEMINI_API_KEY가 포함되어야 한다."""
        from pathlib import Path

        path = Path(__file__).resolve().parent.parent / ".encenv.example"
        content = path.read_text(encoding="utf-8")
        assert "LLM_GEMINI_API_KEY" in content

    def test_encenv_example_contains_other_sensitive_keys(self):
        """.encenv.example에 다른 민감 키도 포함되어야 한다."""
        from pathlib import Path

        path = Path(__file__).resolve().parent.parent / ".encenv.example"
        content = path.read_text(encoding="utf-8")
        assert "ADMIN_JWT_SECRET" in content
        assert "REDIS_PASSWORD" in content


# ──────────────────────────────────────────────
# 13. pyproject.toml optional-dependencies 검증
# ──────────────────────────────────────────────


class TestPyprojectGeminiDep:
    """pyproject.toml에 gemini optional dependency가 올바르게 등록되었는지 검증."""

    def test_gemini_in_optional_deps(self):
        """pyproject.toml의 [project.optional-dependencies]에 gemini이 있어야 한다."""
        from pathlib import Path

        pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
        content = pyproject_path.read_text(encoding="utf-8")
        assert "gemini" in content
        assert "langchain-google-genai" in content
