"""설정 카탈로그·설정 저장 API 검증 (Plan 68 Phase 1 / D-129 · T1~T6).

- 실 `.env`는 건드리지 않는다: `src.api.routes.admin._ENV_FILE`을 tmp_path로 치환한다.
- 외부 네트워크·LLM 호출 0건(D-127).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.routes.admin import (
    EnvUpdateRequest,
    _read_env_file,
    get_settings_schema,
    update_settings,
)
from src.api.settings_catalog import (
    IMMEDIATE_KEYS,
    UNCONSUMED_KEYS,
    dry_run_updates,
    encenv_managed_keys,
    field_index,
    validate_updates,
)
from src.config import AppConfig, LLMConfig, MultiDBConfig, SecurityConfig

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ADMIN = {"sub": "admin"}


# --- 테스트 하네스 ---


class _CapturingAuditService:
    """감사 기록을 가로채 검증에 사용한다."""

    def __init__(self) -> None:
        self.entries: list = []

    async def log(self, entry) -> None:
        self.entries.append(entry)


def _request(audit_service=None):
    """라우트 직접 호출용 가짜 Request (test_plan59a.py 패턴)."""
    state = SimpleNamespace(audit_service=audit_service, audit_repo=None)
    return SimpleNamespace(
        app=SimpleNamespace(state=state),
        state=SimpleNamespace(client_ip="127.0.0.1", request_id="req-1"),
    )


def _use_env_file(monkeypatch, tmp_path: Path, content: str) -> Path:
    """실 `.env` 대신 임시 파일을 쓰도록 라우트 모듈을 격리한다."""
    env_file = tmp_path / ".env"
    env_file.write_text(content, encoding="utf-8")
    monkeypatch.setattr("src.api.routes.admin._ENV_FILE", env_file)
    return env_file


def _file_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        keys.add(stripped.split("=", 1)[0].strip())
    return keys


# --- T1: 커버리지 게이트 ---


def test_t1_catalog_covers_all_env_file_keys():
    """카탈로그가 `.env` ∪ `.env.example`의 모든 키(시크릿 제외)를 포함해야 한다.

    신규 키가 config.py 없이 파일에만 추가되면 이 테스트가 실패한다.
    """
    env_keys = _file_keys(_PROJECT_ROOT / ".env")
    example_keys = _file_keys(_PROJECT_ROOT / ".env.example")
    union = env_keys | example_keys
    assert len(union) >= 160, f"파일 키 실파싱이 비정상적으로 적다: {len(union)}"

    catalog_keys = set(field_index())
    missing = sorted(union - catalog_keys - encenv_managed_keys())
    assert missing == [], f"카탈로그에 없는 설정 키: {missing}"


def test_t1_env_example_has_no_duplicate_keys():
    """중복 키는 혼란 요인 — `.env`에 같은 키가 두 번 등재되면 안 된다."""
    lines = (_PROJECT_ROOT / ".env").read_text(encoding="utf-8").splitlines()
    seen: dict[str, int] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        seen[key] = seen.get(key, 0) + 1
    duplicates = {k: n for k, n in seen.items() if n > 1}
    assert duplicates == {}, f".env 중복 키: {duplicates}"


async def test_t1_schema_endpoint_returns_catalog(monkeypatch, tmp_path):
    """스키마 엔드포인트가 그룹 전체와 파일값을 함께 돌려준다."""
    env_file = _use_env_file(monkeypatch, tmp_path, "LLM_MODEL=from-file\n")
    response = await get_settings_schema(_ADMIN)

    assert len(response.groups) == 18
    assert response.env_file_path == str(env_file)
    items = {
        item.env_key: item
        for group in response.groups for item in group.settings
    }
    assert len(items) == 226
    assert items["LLM_MODEL"].file_value == "from-file"
    assert items["ORCHESTRATOR_TIMEOUT"].file_value is None  # 파일 미존재 = 기본값 사용 중
    assert items["ADMIN_PASSWORD"].file_value is None and items["ADMIN_PASSWORD"].is_secret


async def test_t1_schema_warns_when_env_file_missing(monkeypatch, tmp_path):
    """`.env`가 없으면 경고로 알린다(침묵 금지)."""
    monkeypatch.setattr("src.api.routes.admin._ENV_FILE", tmp_path / "absent.env")
    response = await get_settings_schema(_ADMIN)
    assert any(".env 파일이 없습니다" in warning for warning in response.warnings)


# --- T2: 인트로스펙션 ---


def test_t2_group_and_field_counts():
    """그룹 17개 + top-level 15필드 = 226필드.

    D-129 등재 시점 224 → ORCHESTRATOR_RECURSION_LIMIT 추가(Plan 67 Phase 0 ③)로 225
    → ALARM_DEFAULT_TEST_DB_ID 추가(Plan 67 Phase 0 ⑫)로 226.
    """
    index = field_index()
    group_keys = {spec.group_key for spec in index.values()}
    assert len(group_keys) == 18  # 17 그룹 + 전역
    assert len(index) == 226
    assert len([s for s in index.values() if s.group_key == "general"]) == 15


def test_t2_alias_key_used_for_multi_db():
    """`validation_alias`가 있으면 첫 alias가 대표 env 키가 된다."""
    index = field_index()
    assert "ACTIVE_DB_IDS" in index
    assert "MULTI_DB_ACTIVE_DB_IDS_CSV" not in index
    assert index["ACTIVE_DB_IDS"].group_key == "multi_db"
    assert index["ACTIVE_DB_IDS"].type == "csv"


def test_t2_type_detection():
    """타입 판정: bool·tristate·enum·숫자·json_list·csv·secret."""
    index = field_index()
    assert index["SECURITY_MASK_IP"].type == "bool"
    assert index["ENABLE_DEEPAGENT_ORCHESTRATION"].type == "tristate"
    assert index["LLM_PROVIDER"].type == "enum"
    assert index["LLM_PROVIDER"].enum_choices == ["ollama", "fabrix", "gemini"]
    assert index["LOG_LEVEL"].enum_choices == ["DEBUG", "INFO", "WARNING", "ERROR"]
    assert index["SCHEMA_CACHE_BACKEND"].enum_choices == ["redis", "file"]  # 수동 choices 보강
    assert index["LLM_OLLAMA_TIMEOUT"].type == "int"
    assert index["QUERY_SUFFICIENCY_REQUIRED_THRESHOLD"].type == "float"
    assert index["SECURITY_SENSITIVE_COLUMNS"].type == "json_list"
    assert index["ALARM_NOTIFICATION_CHANNELS_CSV"].type == "csv"
    assert index["POLESTAR_DB_IDS"].type == "csv"
    assert index["LLM_MODEL"].type == "string"


def test_t2_secret_detection():
    """SecretStr·.encenv 관리 키·post_init 보정 키는 시크릿으로 잠긴다."""
    index = field_index()
    secrets = {key for key, spec in index.items() if spec.is_secret}
    assert secrets == {
        "ADMIN_JWT_SECRET", "ADMIN_PASSWORD", "AUTH_JWT_SECRET",
        "DBHUB_BEARER_TOKEN", "LLM_FABRIX_API_KEY", "LLM_FABRIX_CLIENT_KEY",
        "LLM_GEMINI_API_KEY", "LLM_OLLAMA_API_KEY", "ORCHESTRATOR_API_KEY",
        "REDIS_PASSWORD", "WORKB_BEARER_TOKEN",
        "NOISE_INVESTIGATION_SERVICE_TOKEN",  # SecretStr 자동 판정
    }
    assert index["NOISE_INVESTIGATION_SERVICE_TOKEN"].type == "secret"
    assert index["ADMIN_PASSWORD"].default is None  # 시크릿은 기본값도 노출하지 않는다


def test_t2_private_attr_and_property_excluded():
    """PrivateAttr(`_jwt_secret_explicit`)·property는 카탈로그에 없다."""
    index = field_index()
    assert not any(spec.field_name.startswith("_") for spec in index.values())
    assert "ADMIN__JWT_SECRET_EXPLICIT" not in index
    assert "NOISE_IMPORTANCE_VALUE_MAP" not in index  # property


# --- T3: 저장 왕복 ---


async def test_t3_round_trip_preserves_comments_and_order(monkeypatch, tmp_path):
    """타입별 저장 → 재파싱 → 그룹 재구성 값 일치, 주석·순서·무관 키 보존."""
    env_file = _use_env_file(monkeypatch, tmp_path, (
        "# 헤더 주석\n"
        "LLM_PROVIDER=ollama\n"
        "\n"
        "# 무관 키\n"
        "UNRELATED_KEY=keep-me\n"
        "SECURITY_MASK_IP=false\n"
    ))

    response = await update_settings(
        _request(),
        EnvUpdateRequest(settings={
            "LLM_PROVIDER": "gemini",
            "SECURITY_MASK_IP": "true",
            "SECURITY_SENSITIVE_COLUMNS": '["password","token"]',
            "ACTIVE_DB_IDS": "polestar,itsm",
            "LLM_OLLAMA_TIMEOUT": "240",
        }),
        _ADMIN,
    )

    assert set(response.updated_keys) == {
        "LLM_PROVIDER", "SECURITY_MASK_IP", "SECURITY_SENSITIVE_COLUMNS",
        "ACTIVE_DB_IDS", "LLM_OLLAMA_TIMEOUT",
    }

    text = env_file.read_text(encoding="utf-8")
    assert "# 헤더 주석" in text and "# 무관 키" in text
    assert "UNRELATED_KEY=keep-me" in text
    # 기존 키는 원래 위치를 유지하고, 신규 키만 뒤에 붙는다
    assert text.index("LLM_PROVIDER") < text.index("UNRELATED_KEY")

    values = _read_env_file()
    assert values["LLM_PROVIDER"] == "gemini"
    assert values["SECURITY_SENSITIVE_COLUMNS"] == '["password","token"]'

    # 그룹 재구성 값 일치 (실 .env·OS env 누수 차단을 위해 tmp 파일만 사용)
    assert LLMConfig(_env_file=str(env_file)).provider == "gemini"
    assert LLMConfig(_env_file=str(env_file)).ollama_timeout == 240
    assert SecurityConfig(_env_file=str(env_file)).sensitive_columns == ["password", "token"]
    assert MultiDBConfig(_env_file=str(env_file)).get_active_db_ids() == ["polestar", "itsm"]


async def test_t3_backup_created(monkeypatch, tmp_path):
    """저장 시 `.env.bak` 백업이 남는다."""
    env_file = _use_env_file(monkeypatch, tmp_path, "LLM_MODEL=old-model\n")
    await update_settings(
        _request(), EnvUpdateRequest(settings={"LLM_MODEL": "new-model"}), _ADMIN,
    )
    backup = env_file.parent / ".env.bak"
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == "LLM_MODEL=old-model\n"
    assert _read_env_file()["LLM_MODEL"] == "new-model"


async def test_t3_duplicate_keys_cleaned(monkeypatch, tmp_path):
    """중복 등재 키는 첫 줄 위치에 마지막 유효값만 남기고 정리된다."""
    env_file = _use_env_file(monkeypatch, tmp_path, (
        "NOISE_ENABLE_NOISE_GATE=false\n"
        "LLM_MODEL=a\n"
        "NOISE_ENABLE_NOISE_GATE=true\n"
    ))
    await update_settings(
        _request(), EnvUpdateRequest(settings={"LLM_MODEL": "b"}), _ADMIN,
    )
    lines = [
        line for line in env_file.read_text(encoding="utf-8").splitlines()
        if line.startswith("NOISE_ENABLE_NOISE_GATE")
    ]
    assert lines == ["NOISE_ENABLE_NOISE_GATE=true"]  # 마지막 유효값 보존


async def test_t3_reset_keys_remove_line(monkeypatch, tmp_path):
    """reset_keys는 `.env`에서 줄을 제거해 코드 기본값으로 되돌린다."""
    env_file = _use_env_file(monkeypatch, tmp_path, (
        "LLM_MODEL=custom\n"
        "LLM_OLLAMA_TIMEOUT=999\n"
    ))
    response = await update_settings(
        _request(), EnvUpdateRequest(reset_keys=["LLM_OLLAMA_TIMEOUT"]), _ADMIN,
    )
    assert response.reset_keys == ["LLM_OLLAMA_TIMEOUT"]
    assert "LLM_OLLAMA_TIMEOUT" not in env_file.read_text(encoding="utf-8")
    assert LLMConfig(_env_file=str(env_file)).ollama_timeout == 180  # 코드 기본값


async def test_t3_tristate_reset_to_auto(monkeypatch, tmp_path):
    """tristate(auto)는 줄 제거로 표현된다."""
    env_file = _use_env_file(monkeypatch, tmp_path, "ENABLE_DEEPAGENT_ORCHESTRATION=false\n")
    await update_settings(
        _request(), EnvUpdateRequest(reset_keys=["ENABLE_DEEPAGENT_ORCHESTRATION"]), _ADMIN,
    )
    assert env_file.read_text(encoding="utf-8").strip() == ""


# --- T4: 검증 거부 ---


@pytest.mark.parametrize(
    ("key", "value", "reason"),
    [
        ("SECURITY_SENSITIVE_COLUMNS", "password,token", "JSON 배열"),
        ("SECURITY_SENSITIVE_COLUMNS", '{"a":1}', "JSON 배열"),
        ("LLM_PROVIDER", "openai", "허용값"),
        ("LLM_MODEL", "abc # 주석", "'#'"),
        ("LLM_MODEL", "abc\ndef", "개행"),
        ("SECURITY_MASK_IP", "True", "true 또는 false"),
        ("NOT_A_REAL_SETTING", "1", "카탈로그에 없는"),
        ("ADMIN_PASSWORD", "hunter2", ".encenv"),
        ("LLM_OLLAMA_TIMEOUT", "abc", "정수"),
        ("QUERY_SUFFICIENCY_REQUIRED_THRESHOLD", "높음", "숫자"),
    ],
)
async def test_t4_invalid_values_rejected(monkeypatch, tmp_path, key, value, reason):
    """형식 오류는 저장 전에 400 + 필드별 사유로 차단된다."""
    env_file = _use_env_file(monkeypatch, tmp_path, "LLM_MODEL=orig\n")
    with pytest.raises(HTTPException) as exc_info:
        await update_settings(_request(), EnvUpdateRequest(settings={key: value}), _ADMIN)

    assert exc_info.value.status_code == 400
    detail = exc_info.value.detail
    errors = detail["errors"] if isinstance(detail, dict) else []
    assert [e["key"] for e in errors] == [key]
    assert reason in errors[0]["message"]
    # 저장 전 차단 — 파일이 그대로여야 한다
    assert env_file.read_text(encoding="utf-8") == "LLM_MODEL=orig\n"
    assert not (env_file.parent / ".env.bak").exists()


def test_t4_dry_run_maps_validation_error_to_env_key():
    """그룹 재구성 실패(ValidationError)는 필드명이 아니라 env 키로 보고된다."""
    errors = dry_run_updates({"LLM_OLLAMA_TIMEOUT": "abc"}, ["LLM_OLLAMA_TIMEOUT"])
    assert [e.key for e in errors] == ["LLM_OLLAMA_TIMEOUT"]


def test_t4_dry_run_maps_settings_error_to_env_key():
    """list 필드 JSON 파싱 실패는 ValidationError가 아니라 SettingsError로 올라온다."""
    errors = dry_run_updates(
        {"SECURITY_SENSITIVE_COLUMNS": "not-json"}, ["SECURITY_SENSITIVE_COLUMNS"],
    )
    assert [e.key for e in errors] == ["SECURITY_SENSITIVE_COLUMNS"]
    assert "JSON 배열" in errors[0].message


def test_t4_dry_run_passes_for_valid_values():
    """정상 값이면 dry-run은 통과한다."""
    assert dry_run_updates(
        {"LLM_PROVIDER": "gemini", "CHECKPOINT_BACKEND": "sqlite"},
        ["LLM_PROVIDER", "CHECKPOINT_BACKEND"],
    ) == []


async def test_t4_top_level_enum_rejected(monkeypatch, tmp_path):
    """top-level Literal 필드도 허용값 밖이면 저장 전에 차단된다."""
    env_file = _use_env_file(monkeypatch, tmp_path, "LLM_MODEL=orig\n")
    with pytest.raises(HTTPException) as exc_info:
        await update_settings(
            _request(), EnvUpdateRequest(settings={"CHECKPOINT_BACKEND": "mysql"}), _ADMIN,
        )
    assert exc_info.value.status_code == 400
    assert env_file.read_text(encoding="utf-8") == "LLM_MODEL=orig\n"


def test_t4_validate_updates_returns_field_errors():
    """`validate_updates`는 키별 사유를 담은 목록을 돌려준다."""
    errors = validate_updates({"LLM_PROVIDER": "openai", "LLM_MODEL": "ok"})
    assert [e.key for e in errors] == ["LLM_PROVIDER"]


# --- T5: 민감 가드 ---


async def test_t5_masked_value_is_ignored(monkeypatch, tmp_path):
    """마스킹 값(`********`)을 그대로 돌려받으면 원값을 보존한다."""
    env_file = _use_env_file(
        monkeypatch, tmp_path,
        "AUTH_AUTH_DB_URL=postgresql://user:realpass@localhost:5432/auth\n",
    )
    response = await update_settings(
        _request(),
        EnvUpdateRequest(settings={
            "AUTH_AUTH_DB_URL": "postgresql://user:********@localhost:5432/auth",
            "LLM_MODEL": "new",
        }),
        _ADMIN,
    )
    assert response.ignored_keys == ["AUTH_AUTH_DB_URL"]
    assert "realpass" in env_file.read_text(encoding="utf-8")
    assert _read_env_file()["LLM_MODEL"] == "new"


async def test_t5_audit_records_old_new_for_plain_keys(monkeypatch, tmp_path):
    """비민감 키는 이전값→새값 쌍을 감사에 남긴다."""
    _use_env_file(monkeypatch, tmp_path, "LLM_MODEL=old-model\n")
    audit = _CapturingAuditService()
    await update_settings(
        _request(audit),
        EnvUpdateRequest(settings={"LLM_MODEL": "new-model", "LLM_OLLAMA_TIMEOUT": "300"}),
        _ADMIN,
    )
    assert len(audit.entries) == 1
    extra = audit.entries[0].extra
    assert extra["changes"]["LLM_MODEL"] == {"old": "old-model", "new": "new-model"}
    assert extra["changes"]["LLM_OLLAMA_TIMEOUT"] == {"old": None, "new": "300"}  # 파일 미존재 = 기본값
    assert audit.entries[0].event == "settings_update"
    assert audit.entries[0].user_id == "admin"


async def test_t5_audit_omits_sensitive_values(monkeypatch, tmp_path):
    """민감 키는 키 이름만 남기고 값은 감사에 일절 기록하지 않는다."""
    _use_env_file(monkeypatch, tmp_path, "AUTH_AUTH_DB_URL=postgresql://u:before@h:5432/d\n")
    audit = _CapturingAuditService()
    await update_settings(
        _request(audit),
        EnvUpdateRequest(settings={"AUTH_AUTH_DB_URL": "postgresql://u:s3cr3t@h:5432/d"}),
        _ADMIN,
    )
    entry = audit.entries[0]
    assert entry.extra["sensitive_keys"] == ["AUTH_AUTH_DB_URL"]
    assert "AUTH_AUTH_DB_URL" not in entry.extra["changes"]
    serialized = json.dumps(entry.to_dict(), ensure_ascii=False)
    assert "s3cr3t" not in serialized
    assert "before" not in serialized


async def test_t5_audit_unavailable_is_surfaced(monkeypatch, tmp_path):
    """감사 저장소가 없으면 응답 메시지에 명시한다(침묵 금지)."""
    _use_env_file(monkeypatch, tmp_path, "LLM_MODEL=old\n")
    response = await update_settings(
        _request(), EnvUpdateRequest(settings={"LLM_MODEL": "new"}), _ADMIN,
    )
    assert "감사 기록 불가" in response.message


def test_t5_catalog_hides_secret_values(monkeypatch, tmp_path):
    """스키마 응답에 시크릿 값이 실리지 않는다(설정됨 여부만)."""
    from src.api.settings_catalog import build_catalog

    catalog = build_catalog(
        file_values={"ADMIN_PASSWORD": "hunter2", "LLM_MODEL": "llama"},
        config=None,
        os_environ={},
        env_file_path="/tmp/.env",
    )
    items = {
        item.env_key: item
        for group in catalog.groups for item in group.settings
    }
    assert items["ADMIN_PASSWORD"].file_value is None
    assert items["ADMIN_PASSWORD"].effective_value is None
    assert items["ADMIN_PASSWORD"].is_set is True
    assert items["LLM_MODEL"].file_value == "llama"
    assert "hunter2" not in catalog.model_dump_json()


def test_t5_catalog_masks_connection_string():
    """접속 문자열은 비밀번호 구간만 마스킹해 노출한다."""
    from src.api.settings_catalog import build_catalog

    catalog = build_catalog(
        file_values={"AUTH_AUTH_DB_URL": "postgresql://user:realpass@h:5432/db"},
        config=None,
        os_environ={},
        env_file_path="/tmp/.env",
    )
    item = next(
        item for group in catalog.groups for item in group.settings
        if item.env_key == "AUTH_AUTH_DB_URL"
    )
    assert item.is_sensitive is True
    assert item.is_secret is False
    assert item.file_value == "postgresql://user:********@h:5432/db"
    assert "realpass" not in catalog.model_dump_json()


def test_t5_catalog_flags_os_override():
    """OS env가 파일값을 덮어쓰면 오버라이드 출처를 표시한다."""
    from src.api.settings_catalog import build_catalog

    config = AppConfig(_env_file=None)
    config.log_level = "DEBUG"
    catalog = build_catalog(
        file_values={"LOG_LEVEL": "INFO"},
        config=config,
        os_environ={"LOG_LEVEL": "DEBUG"},
        env_file_path="/tmp/.env",
    )
    item = next(
        item for group in catalog.groups for item in group.settings
        if item.env_key == "LOG_LEVEL"
    )
    assert item.file_value == "INFO"
    assert item.effective_value == "DEBUG"
    assert item.override == "os"


# --- T6: 반영 시점 메타 ---


def test_t6_requires_restart_matches_immediate_list():
    """즉시 반영 예외 필드만 requires_restart=False 여야 한다(§1.3 확정표)."""
    index = field_index()
    immediate = {key for key, spec in index.items() if not spec.requires_restart}
    assert immediate == set(IMMEDIATE_KEYS)
    assert index["LLM_PROVIDER"].requires_restart is True
    assert index["SYNONYM_FUZZY_MATCH"].requires_restart is False
    assert index["SYNONYM_SEMANTIC_BACKEND"].requires_restart is True  # 임베더 영구 래치


def test_t6_unconsumed_fields_flagged():
    """미소비 필드 16개는 consumed=False로 구분된다."""
    index = field_index()
    unconsumed = {key for key, spec in index.items() if not spec.consumed}
    assert unconsumed == set(UNCONSUMED_KEYS)
    assert len(unconsumed) == 16


def test_t6_semantic_routing_is_tristate():
    """게이트 4 교정 후 ENABLE_SEMANTIC_ROUTING은 auto/true/false 3상이 된다."""
    assert field_index()["ENABLE_SEMANTIC_ROUTING"].type == "tristate"


# --- 게이트 4: ENABLE_SEMANTIC_ROUTING `bool | None` 교정 (Plan 68 §5) ---


def _config_with_dbs(monkeypatch, *, db_ids: str, flag=...):
    """OS env 누수를 막고 멀티 DB 목록·플래그를 명시한 config를 만든다."""
    monkeypatch.delenv("ENABLE_SEMANTIC_ROUTING", raising=False)
    kwargs = {} if flag is ... else {"enable_semantic_routing": flag}
    return AppConfig(
        _env_file=None,
        multi_db=MultiDBConfig(_env_file=None, active_db_ids_csv=db_ids),
        **kwargs,
    )


def test_semantic_routing_auto_enabled_for_multi_db(monkeypatch):
    """미설정 + ACTIVE_DB_IDS 존재 → 자동 활성화(기존 동작 보존)."""
    assert _config_with_dbs(monkeypatch, db_ids="polestar").enable_semantic_routing is True


def test_semantic_routing_auto_disabled_without_db_ids(monkeypatch):
    """미설정 + ACTIVE_DB_IDS 없음 → 비활성(기존 동작 보존)."""
    assert _config_with_dbs(monkeypatch, db_ids="").enable_semantic_routing is False


def test_semantic_routing_explicit_false_respected(monkeypatch):
    """명시적 false는 ACTIVE_DB_IDS가 있어도 존중된다(무력화 버그 교정)."""
    config = _config_with_dbs(monkeypatch, db_ids="polestar", flag=False)
    assert config.enable_semantic_routing is False


def test_semantic_routing_explicit_true_respected(monkeypatch):
    """명시적 true는 ACTIVE_DB_IDS가 없어도 존중된다."""
    config = _config_with_dbs(monkeypatch, db_ids="", flag=True)
    assert config.enable_semantic_routing is True


def test_semantic_routing_reads_env_file_false(monkeypatch, tmp_path):
    """`.env`의 false가 무시되지 않는다(os.getenv 판정 제거 회귀 방지)."""
    monkeypatch.delenv("ENABLE_SEMANTIC_ROUTING", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("ENABLE_SEMANTIC_ROUTING=false\n", encoding="utf-8")
    config = AppConfig(
        _env_file=str(env_file),
        multi_db=MultiDBConfig(_env_file=None, active_db_ids_csv="polestar"),
    )
    assert config.enable_semantic_routing is False


async def test_t6_response_splits_restart_and_immediate(monkeypatch, tmp_path):
    """저장 응답이 재시작 필요 키와 즉시 반영 키를 분리해 알린다."""
    _use_env_file(monkeypatch, tmp_path, "")
    response = await update_settings(
        _request(),
        EnvUpdateRequest(settings={
            "LLM_MODEL": "x",                 # 재시작 필요
            "SYNONYM_FUZZY_MATCH": "true",    # 즉시 반영
        }),
        _ADMIN,
    )
    assert response.requires_restart_keys == ["LLM_MODEL"]
    assert response.applied_immediately_keys == ["SYNONYM_FUZZY_MATCH"]
