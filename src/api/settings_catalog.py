"""설정 카탈로그 — `AppConfig` 인트로스펙션 기반 웹UI 설정 스키마 (Plan 68 Phase 1 / D-129).

카탈로그의 단일 원천(SSOT)은 `src/config.py`의 pydantic 모델이다. `.env`/`.env.example`은
열거 원천이 아니라 **현재값·설명의 원천**으로만 사용한다(파일에 없는 config 전용 필드가 59개
존재하므로 파일 파싱만으로는 전체 옵션을 열거할 수 없다 — Plan 68 §1.1).

핵심 규칙:
- env 키 = `validation_alias.choices[0]`이 있으면 그 값, 없으면 `env_prefix + 필드명.upper()`
- 시크릿(`SecretStr`·`.encenv` 관리 키)은 **웹UI 편집 차단** — `.env` 수정이 기능적으로 무효이기
  때문이다(우선순위: OS env > `.encenv` > `.env` > 코드 기본값, Plan 68 §1.2).
- 반영 시점(`requires_restart`·`apply_mode`)은 소비 지점 분석 확정표(§1.3·§6.2)를 상수로
  내장한다. 미분류 신규 필드는 보수적으로 "재시작 필요"로 취급한다.
- `apply_mode` 3분류(Plan 68 §6 Phase 4): `immediate`(저장 후 다음 요청부터) /
  `reload`(저장 후 `POST /admin/settings/reload`로 반영) / `restart`(재시작 유일).
"""

from __future__ import annotations

import json
import typing
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import UnionType
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings

from src.config import AppConfig

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_EXAMPLE_FILE = _PROJECT_ROOT / ".env.example"
_ENCENV_EXAMPLE_FILE = _PROJECT_ROOT / ".encenv.example"
_ENCENV_FILE = _PROJECT_ROOT / ".encenv"

#: top-level(그룹 없는) 필드를 담는 가상 그룹 키.
TOP_LEVEL_GROUP = "general"

#: 아코디언 표시 순서 (Plan 68 부록 A.1~A.20).
#: ※ `build_catalog`는 이 튜플에 있는 그룹만 응답에 싣는다 — AppConfig에 하위 설정을 추가하면
#:   반드시 여기와 GROUP_TITLES에 등재할 것. (Plan 71 `polestar_rest`·Plan 74 `drm`이 누락되어
#:   인덱스에는 있으나 UI에서 조회·수정 불가였던 사례 — 2026-08-25 정정, D-175 부기.)
#:   `test_settings_catalog.test_t2_group_and_field_counts`가 AppConfig 하위 설정 전수 등재를 고정한다.
GROUP_ORDER: tuple[str, ...] = (
    "llm", "orchestrator", "dbhub", "query", "synonym", "text2sql",
    "security", "server", "admin", "auth", "multi_db", "redis",
    "schema_cache", "audit", "observability", "alarm", "workb", "noise_gate",
    "polestar_rest", "drm", TOP_LEVEL_GROUP,
)

GROUP_TITLES: dict[str, str] = {
    "llm": "LLM",
    "orchestrator": "오케스트레이터(deepagents)",
    "dbhub": "DBHub(MCP)",
    "query": "쿼리 정책",
    "synonym": "동의어 매칭",
    "text2sql": "Text2SQL",
    "security": "보안 마스킹",
    "server": "API 서버",
    "admin": "운영자 인증",
    "auth": "사용자 인증",
    "multi_db": "멀티 DB",
    "redis": "Redis",
    "schema_cache": "스키마 캐시",
    "audit": "감사 로그",
    "observability": "실행 로그·트레이스",
    "alarm": "알람",
    "workb": "worKB 발송",
    "noise_gate": "노이즈 게이트",
    "polestar_rest": "폴스타 실시간 REST",
    "drm": "DRM 해제",
    TOP_LEVEL_GROUP: "전역",
}

#: 그룹 내 하위 구획(소제목). 알람·노이즈 게이트는 필드 수가 많아 구획으로 나눈다(부록 A.15/A.17).
SECTION_BY_KEY: dict[str, str] = {
    # --- 알람 ---
    "ALARM_HISTORY_ENABLED": "Plan 47 이력",
    "ALARM_HISTORY_LOOKBACK_DAYS": "Plan 47 이력",
    "ALARM_HISTORY_MAX_ROWS": "Plan 47 이력",
    "ALARM_HISTORY_CACHE_TTL_SECONDS": "Plan 47 이력",
    "ALARM_PROCESS_ENRICH_ENABLED": "Plan 47-1 프로세스",
    "ALARM_PROCESS_API_BASE_URLS_CSV": "Plan 47-1 프로세스",
    "ALARM_PROCESS_API_TIMEOUT_SECONDS": "Plan 47-1 프로세스",
    "ALARM_PROCESS_TOP_N": "Plan 47-1 프로세스",
    "ALARM_PROMETHEUS_ENABLED": "Prometheus",
    "ALARM_PROMETHEUS_BASE_URLS_CSV": "Prometheus",
    "ALARM_PROMETHEUS_TIMEOUT_SECONDS": "Prometheus",
    # --- 노이즈 게이트 ---
    "NOISE_ENABLE_NOISE_GATE": "E1 기본",
    "NOISE_SUPPRESS_MAX_SEVERITY": "E1 기본",
    "NOISE_IMPORTANCE_VALUE_MAP_CSV": "E1 기본",
    "NOISE_SELF_HEAL_WINDOW_SECONDS": "E1 기본",
    "NOISE_REPEAT_INTERVAL_SECONDS": "E1 기본",
    "NOISE_SEV3_REPEAT_INTERVAL_SECONDS": "E1 기본",
    "NOISE_NOISE_CONTEXT_TIMEOUT_SECONDS": "E1 기본",
    "NOISE_NOISE_CONTEXT_CACHE_TTL_SECONDS": "E1 기본",
    "NOISE_RESOLVED_TO_DASHBOARD": "E1 기본",
    "NOISE_DECISION_STORE_PATH": "E1 기본",
    "NOISE_DECISION_STORE_ENABLED": "E1 기본",
    "NOISE_DEBOUNCE_SECONDS": "E2 억제",
    "NOISE_FLAP_HIGH_THRESHOLD": "E2 억제",
    "NOISE_FLAP_LOW_THRESHOLD": "E2 억제",
    "NOISE_FLAPPING_ENABLED": "E2 억제",
    "NOISE_DEPENDENCY_SUPPRESSION": "E2 억제",
    "NOISE_INHIBITION_ENABLED": "E2 억제",
    "NOISE_INHIBITION_WINDOW_SECONDS": "E2 억제",
    "NOISE_STORM_GROUPING_ENABLED": "E2 억제",
    "NOISE_STORM_WINDOW_SECONDS": "E2 억제",
    "NOISE_STORM_THRESHOLD": "E2 억제",
    "NOISE_META_ALERT_SUPPRESS_RATIO": "E3 메타·보강",
    "NOISE_META_ALERT_WINDOW_SECONDS": "E3 메타·보강",
    "NOISE_META_ALERT_MIN_EVENTS": "E3 메타·보강",
    "NOISE_ENABLE_AI_SEVERITY_BOOST": "E3 메타·보강",
    "NOISE_AI_SEVERITY_ESCALATE_ONLY": "E3 메타·보강",
    "NOISE_BUSINESS_HOURS_CSV": "E3 메타·보강",
    "NOISE_TICKET_BATCH_QUEUE_PATH": "E3 메타·보강",
    "NOISE_TICKET_BATCH_QUEUE_ENABLED": "E3 메타·보강",
    "NOISE_ENABLE_LLM_ACTIONABILITY": "E4 피드백",
    "NOISE_FEEDBACK_STORE_PATH": "E4 피드백",
    "NOISE_FEEDBACK_STORE_ENABLED": "E4 피드백",
    "NOISE_ACTIONABILITY_FEWSHOT_COUNT": "E4 피드백",
    "NOISE_ENABLE_AGENTIC_ENRICHER": "E5 agentic enricher",
    "NOISE_AGENTIC_ENRICHER_FALLBACK": "E5 agentic enricher",
    "NOISE_AGENTIC_ENRICHER_TIMEOUT_SECONDS": "E5 agentic enricher",
    "NOISE_AGENTIC_ENRICHER_MAX_TOOL_CALLS": "E5 agentic enricher",
    "NOISE_AGENTIC_ENRICHER_MESSAGE_ALARMS_ONLY": "E5 agentic enricher",
    "NOISE_MULTI_HOP_CASCADE_ENABLED": "Plan 60 E4 토폴로지",
    "NOISE_TOPOLOGY_CACHE_TTL_SECONDS": "Plan 60 E4 토폴로지",
    "NOISE_TOPOLOGY_MAX_HOPS": "Plan 60 E4 토폴로지",
    "NOISE_CROSS_HOST_CORRELATION_ENABLED": "Plan 60 E2 크로스-호스트 상관",
    "NOISE_CORRELATION_SIM_THRESHOLD": "Plan 60 E2 크로스-호스트 상관",
    "NOISE_CORRELATION_WINDOW_SECONDS": "Plan 60 E2 크로스-호스트 상관",
    "NOISE_CORRELATION_MIN_CLUSTER_SIZE": "Plan 60 E2 크로스-호스트 상관",
    "NOISE_CORRELATION_BUFFER_MAX": "Plan 60 E2 크로스-호스트 상관",
    "NOISE_CORRELATION_FIELD_WEIGHTS_CSV": "Plan 60 E2 크로스-호스트 상관",
    "NOISE_CORRELATION_TOPOLOGY_WEIGHT_ENABLED": "Plan 60 E2 크로스-호스트 상관",
    "NOISE_CORRELATION_TOPOLOGY_WEIGHT": "Plan 60 E2 크로스-호스트 상관",
    "NOISE_RECURRENCE_AUDIT_EVERY_N": "Plan 60 E1 재발 감사",
    "NOISE_DYNAMIC_BASELINE_ENABLED": "Plan 60 E3 동적 baseline",
    "NOISE_ANOMALY_Z_HIGH": "Plan 60 E3 동적 baseline",
    "NOISE_ANOMALY_MIN_PERIODS": "Plan 60 E3 동적 baseline",
    "NOISE_ANOMALY_BASELINE_CACHE_TTL_SECONDS": "Plan 60 E3 동적 baseline",
    "NOISE_ANOMALY_STL_ENABLED": "Plan 60 E3 동적 baseline",
    "NOISE_SEMANTIC_DEDUP_ANNOTATION_ENABLED": "Plan 60 B-7 임베딩 주석",
    "NOISE_TOPOLOGY_TEXT_FUSION_ENABLED": "Plan 60 B-7 임베딩 주석",
    "NOISE_EMBEDDING_MODEL_PATH": "Plan 60 B-7 임베딩 주석",
    "NOISE_EMBEDDING_SIMILARITY_THRESHOLD": "Plan 60 B-7 임베딩 주석",
    "NOISE_EMBEDDING_TIMEOUT_SECONDS": "Plan 60 B-7 임베딩 주석",
    "NOISE_SSE_BRIDGE_ENABLED": "SSE 브리지",
    "NOISE_SSE_BRIDGE_CHANNEL": "SSE 브리지",
    "NOISE_INCIDENT_TRACKING_ENABLED": "D-049 incident 계측",
    "NOISE_INCIDENT_EVENT_CHANNEL": "D-049 incident 계측",
    "NOISE_MESSAGE_ENRICHMENT_ENABLED": "Plan 60 E6 통보 보강",
    "NOISE_ENRICHMENT_MIN_TIER": "Plan 60 E6 통보 보강",
    "NOISE_ENRICHMENT_L1_TIMEOUT_SECONDS": "Plan 60 E6 통보 보강",
    "NOISE_ENRICHMENT_PROFILE_MAP_CSV": "Plan 60 E6 통보 보강",
    "NOISE_CHANGE_CORRELATION_ENABLED": "Plan 60 E5 변경 상관",
    "NOISE_CHANGE_WINDOW_SECONDS": "Plan 60 E5 변경 상관",
    "NOISE_ANNOTATION_HARVEST_ENABLED": "Plan 60 E7 ITSM 보완",
    "NOISE_ANNOTATION_PLANNED_SUPPRESS": "Plan 60 E7 ITSM 보완",
    "NOISE_NON_ALARM_FILTER_ENABLED": "Plan 60 E7 ITSM 보완",
    "NOISE_FORMAT_TOLERANT_PARSING_ENABLED": "Plan 60 E7 ITSM 보완",
    "NOISE_CORRELATION_SITE_DIMENSION_ENABLED": "Plan 60 E7 ITSM 보완",
    "NOISE_INVESTIGATION_TRIGGER_ENABLED": "Plan 64 CW-A/B/C 자동조사",
    "NOISE_INVESTIGATION_TRIGGER_MIN_TIER": "Plan 64 CW-A/B/C 자동조사",
    "NOISE_INVESTIGATION_SERVICE_URL": "Plan 64 CW-A/B/C 자동조사",
    "NOISE_INVESTIGATION_SERVICE_TOKEN": "Plan 64 CW-A/B/C 자동조사",
    "NOISE_INVESTIGATION_MCP_CALL_TIMEOUT_SECONDS": "Plan 64 CW-A/B/C 자동조사",
    "NOISE_INVESTIGATION_POLL_INTERVAL_SECONDS": "Plan 64 CW-A/B/C 자동조사",
    "NOISE_INVESTIGATION_TOTAL_TIMEOUT_SECONDS": "Plan 64 CW-A/B/C 자동조사",
    "NOISE_FAULT_DIAGNOSIS_ENABLED": "Plan 64 CW-A/B/C 자동조사",
    "NOISE_FAULT_ESCALATION_ENABLED": "Plan 64 CW-A/B/C 자동조사",
}

#: `.encenv.example` 파싱 외에 수동으로 시크릿 취급하는 키.
#: - LLM_OLLAMA_API_KEY: `LLMConfig.model_post_init`가 `LLM_API_KEY`(.encenv)로 보정한다.
#: - DBHUB_BEARER_TOKEN / ORCHESTRATOR_API_KEY: 전송 인증 크레덴셜(Plan 68 §1.1).
_MANUAL_SECRET_KEYS: frozenset[str] = frozenset({
    "LLM_OLLAMA_API_KEY",
    "DBHUB_BEARER_TOKEN",
    "ORCHESTRATOR_API_KEY",
})

#: `.encenv.example`을 읽을 수 없을 때 쓰는 폴백(파일 부재로 시크릿이 편집 가능해지는 것을 막는다).
_ENCENV_FALLBACK_KEYS: frozenset[str] = frozenset({
    "ADMIN_JWT_SECRET", "ADMIN_PASSWORD", "AUTH_JWT_SECRET",
    "LLM_API_KEY", "LLM_FABRIX_API_KEY", "LLM_FABRIX_CLIENT_KEY",
    "LLM_GEMINI_API_KEY", "REDIS_PASSWORD", "WORKB_BEARER_TOKEN",
})

#: 값 자체는 편집 가능하지만 화면 표시는 마스킹하는 키(비밀번호를 포함할 수 있는 접속 문자열).
SENSITIVE_VALUE_KEYS: frozenset[str] = frozenset({
    "AUTH_AUTH_DB_URL",
    "DB_CONNECTION_STRING",
})

#: 저장 후 **재시작 없이** 반영되는 필드(Plan 68 §1.3 확정표). 그 외 전부 재시작 필요.
IMMEDIATE_KEYS: frozenset[str] = frozenset({
    "DBHUB_BEARER_TOKEN",                        # (c′) DBHubConfig 부분 kwargs 재구성 시 매번 재독
    "SYNONYM_FUZZY_MATCH",                       # (c) document/field_mapper.py 요청 시 fresh load_config
    "SYNONYM_SEMANTIC_MATCH",
    "SYNONYM_SEMANTIC_CONFIDENCE_MIN",
    "SYNONYM_MATCH_CONFIDENCE_MIN",
    "SYNONYM_GOVERNANCE",                        # (c) redis_cache.py
    "SCHEMA_CACHE_AUTO_GENERATE_DESCRIPTIONS",   # (c) cache_manager.py
})

#: 저장 후 **설정 리로드**(`POST /admin/settings/reload` — app.state.config 교체 + 그래프
#: 재빌드 + 스키마 캐시·질의 이력·임베더 싱글톤 리셋 + 로그 레벨 재적용 + 운영 게이트 재실행)로
#: 재시작 없이 반영되는 필드(Plan 68 §6 Phase 4 / §6.2 소비 지점 재실측 2026-07-30).
#: IMMEDIATE_KEYS·RELOADABLE_KEYS 어느 쪽에도 없는 필드는 재시작 필요(보수적 기본) —
#: 소비처가 기동 캡처(AlarmWorker·SSE 브리지·감사 태스크·CORS·uvicorn·체크포인터·인증 풀)를
#: 포함하면 등재하지 않았다.
#: ※ 비대칭 예외: LLM_*·DBHUB_*·ACTIVE_DB_IDS는 알람 워커(기동 캡처 config)가 함께 소비한다 —
#: 질의/API 경로는 리로드로 반영되지만 알람 워커 경로는 재시작까지 이전 값이다(리로드 응답이
#: 해당 변경 시 경고를 명시한다).
RELOADABLE_KEYS: frozenset[str] = frozenset({
    # --- top-level: 그래프 토폴로지·빌드 시점 판독 (그래프 재빌드로 반영) ---
    "ENABLE_SEMANTIC_ROUTING",
    "ENABLE_INTENT_ORCHESTRATION",
    "ENABLE_DEEPAGENTS_PACKAGE",
    "MAX_REPLAN",
    "WORKER_PROVIDER_OVERRIDE",
    "POLESTAR_DB_IDS",
    "ENABLE_SQL_APPROVAL",
    "ENABLE_STRUCTURE_APPROVAL",
    "ACTIVE_DB_IDS",            # ※알람 비대칭
    "LOG_LEVEL",                # 리로드가 setup_logging을 재호출
    # --- server: 라우트 요청 시점 판독 (state.config 교체로 반영) ---
    "API_QUERY_TIMEOUT",
    "API_FILE_QUERY_TIMEOUT",
    # --- admin/auth: 라우트·의존성 요청 시점 판독. AUTH_ENABLED는 리로드가 운영
    #     게이트(_validate_production_secrets)를 재실행하므로 fail-closed 유지 ---
    "ADMIN_JWT_EXPIRE_HOURS",
    "AUTH_ENABLED",
    "AUTH_JWT_EXPIRE_HOURS",
    "AUTH_MAX_LOGIN_ATTEMPTS",
    "AUTH_LOCKOUT_MINUTES",
    "AUTH_PASSWORD_MIN_LENGTH",
    # --- llm: 빌드 시 create_llm (시크릿 키 제외) ※알람 비대칭 ---
    "LLM_PROVIDER",
    "LLM_MODEL",
    "LLM_OLLAMA_BASE_URL",
    "LLM_OLLAMA_TIMEOUT",
    "LLM_GEMINI_MODEL",
    "LLM_FABRIX_BASE_URL",
    "LLM_FABRIX_CHAT_MODEL",
    # --- orchestrator: 빌드 시 create_orchestrator_llm·deep_agent 조립 ---
    "ORCHESTRATOR_PROVIDER",
    "ORCHESTRATOR_BASE_URL",
    "ORCHESTRATOR_MODEL",
    "ORCHESTRATOR_TIMEOUT",
    "ORCHESTRATOR_HEALTH_TIMEOUT",
    "ORCHESTRATOR_VERIFY_SSL",
    "ORCHESTRATOR_RECURSION_LIMIT",
    "ORCHESTRATOR_MAX_TOOL_RESULT_TOKENS",
    "ORCHESTRATOR_ENABLE_THINKING",
    # --- dbhub: 노드가 매 호출 클라이언트 생성(전역 캐시 없음 실측) ※알람 비대칭 ---
    "DBHUB_SERVER_URL",
    "DBHUB_SOURCE_NAME",
    "DBHUB_MCP_CALL_TIMEOUT",
    # --- query/text2sql/security/synonym: 노드 partial 주입 (그래프 재빌드로 반영) ---
    "QUERY_DEFAULT_LIMIT",
    "QUERY_SUFFICIENCY_REQUIRED_THRESHOLD",
    "TEXT2SQL_SEMANTIC_COMPOSE",
    "TEXT2SQL_SEMANTIC_FALLBACK",
    "TEXT2SQL_FALLBACK_CONFIDENCE_MIN",
    "TEXT2SQL_MULTI_CANDIDATE",
    "TEXT2SQL_CANDIDATE_COUNT",
    "TEXT2SQL_CANDIDATE_STRATEGIES",
    "TEXT2SQL_COMPLEXITY_GATE",
    "TEXT2SQL_SELECTION",
    "TEXT2SQL_GENERIC_LLM_MAPPING",
    "TEXT2SQL_QUERY_HISTORY_FEWSHOT",
    "TEXT2SQL_QUERY_HISTORY_TOP_K",
    "TEXT2SQL_QUERY_HISTORY_MIN_SCORE",
    "TEXT2SQL_STEPWISE_DERIVATION",
    "TEXT2SQL_STEPWISE_MAX_ROUNDS",
    "TEXT2SQL_STEPWISE_MAX_TOOL_CALLS",
    "TEXT2SQL_STEPWISE_TIMEOUT_SECONDS",
    "TEXT2SQL_HYPERNYM_AMBIGUITY",
    "SECURITY_SENSITIVE_COLUMNS",
    "SECURITY_MASK_PATTERN",
    "SECURITY_MASK_IP",
    "SECURITY_MASK_EMAIL",
    "SYNONYM_VALUE_RETRIEVAL",
    "SYNONYM_MAX_SYNONYM_SUPPLEMENT_TABLES",
    # --- synonym 임베더 래치: reset_embedder_state()로 반영 ---
    "SYNONYM_SEMANTIC_BACKEND",
    "SYNONYM_SEMANTIC_MODEL_PATH",
    "SYNONYM_SEMANTIC_VLLM_BASE_URL",
    "SYNONYM_SEMANTIC_VLLM_MODEL",
    "SYNONYM_SEMANTIC_VLLM_VERIFY_SSL",
    # --- schema_cache 싱글톤: reset_cache_manager()로 반영 ---
    "SCHEMA_CACHE_BACKEND",
    "SCHEMA_CACHE_CACHE_DIR",
    "SCHEMA_CACHE_ENABLED",
    "SCHEMA_CACHE_FINGERPRINT_TTL_SECONDS",
    # --- redis: 소비처가 두 싱글톤(스키마 캐시·질의 이력)뿐인 필드만 ---
    "REDIS_SSL",
    "REDIS_SOCKET_TIMEOUT",
    # --- alarm/noise_gate: 라우트 전용 판독 필드만 (워커 소비 필드는 전부 재시작) ---
    "ALARM_DEFAULT_TEST_DB_ID",
    "NOISE_META_ALERT_SUPPRESS_RATIO",
    "NOISE_META_ALERT_WINDOW_SECONDS",
    "NOISE_META_ALERT_MIN_EVENTS",
    "NOISE_FORMAT_TOLERANT_PARSING_ENABLED",
    "NOISE_FAULT_DIAGNOSIS_ENABLED",  # 그래프 빌드 시점 게이팅 — 워커 미소비 실측
})

#: config에 정의됐지만 현재 코드가 읽지 않는 필드(Plan 68 §1.5-4). UI 기본 숨김 + "미소비" 뱃지.
UNCONSUMED_KEYS: frozenset[str] = frozenset({
    "ORCHESTRATOR_MAX_INPUT_TOKENS",
    "ORCHESTRATOR_CONTEXT_BUDGET_RATIO",
    "QUERY_MAX_RETRY_COUNT",
    "QUERY_SUFFICIENCY_OPTIONAL_THRESHOLD",
    "SECURITY_PARTIAL_MASK_COLUMNS",
    "AUTH_DEFAULT_ALLOWED_DB_IDS",
    "AUDIT_SENSITIVE_TABLES",
    "AUDIT_NIGHT_ALERT_START",
    "AUDIT_NIGHT_ALERT_END",
    "ALARM_PROMETHEUS_ENABLED",
    "NOISE_DEBOUNCE_SECONDS",
    "NOISE_CORRELATION_FIELD_WEIGHTS_CSV",
    "NOISE_BUSINESS_HOURS_CSV",
    "NOISE_AI_SEVERITY_ESCALATE_ONLY",
    "CONVERSATION_MAX_TURNS",
    "CONVERSATION_TTL_HOURS",
    # --- §6.2 소비 지점 재실측(2026-07-30)로 확인된 추가 미소비 ---
    "ORCHESTRATOR_MAX_HISTORY_TURNS",       # context_resolver.py:26 주석 참조뿐 (D-129 부기 확인 건)
    "SYNONYM_DECAY_DAYS",                   # redis_cache.py:958 인자만 존재 — config에서 넘기는 호출부 없음
    "ALARM_PROMETHEUS_BASE_URLS_CSV",       # PrometheusClient가 src/에서 미생성 (테스트에서만 생성)
    "ALARM_PROMETHEUS_TIMEOUT_SECONDS",     # 동상
})

#: `str` 타입이지만 허용값이 config.py 주석에 명시된 필드 — enum 위젯으로 노출한다(부록 A의 `※`).
ENUM_OVERRIDES: dict[str, list[str]] = {
    "SCHEMA_CACHE_BACKEND": ["redis", "file"],
    "NOISE_AGENTIC_ENRICHER_FALLBACK": ["semantic_routing", "deterministic_only"],
    "NOISE_ENRICHMENT_MIN_TIER": ["PAGE", "TICKET", "DASHBOARD", "SUPPRESS"],
    "NOISE_INVESTIGATION_TRIGGER_MIN_TIER": ["PAGE", "TICKET", "DASHBOARD", "SUPPRESS"],
}

#: 필드명이 `*_csv`가 아니지만 쉼표 목록으로 다루는 키.
CSV_KEYS: frozenset[str] = frozenset({"POLESTAR_DB_IDS"})

#: `.env.example`에 설명이 없는 config 전용 필드 중 동작상 함정이 있는 항목 보강.
DESCRIPTION_OVERRIDES: dict[str, str] = {
    "ENABLE_SEMANTIC_ROUTING": (
        "시멘틱 라우팅 활성화. 미설정(auto)이면 ACTIVE_DB_IDS가 하나라도 있을 때 자동 활성화된다."
    ),
    "ENABLE_INTENT_ORCHESTRATION": (
        "의도 분해 오케스트레이션(사다리 2단). 미설정(auto)이면 멀티 DB 환경에서 자동 활성화된다. "
        "상위 단(ENABLE_DEEPAGENTS_PACKAGE)이 성립하면 true여도 2단은 배선되지 않는다. "
        "구 이름 ENABLE_DEEPAGENT_ORCHESTRATION도 계속 인식하지만 2027-02-20 폐기 예정이다."
    ),
    "ACTIVE_DB_IDS": "활성 DB 식별자 목록. 그래프 토폴로지를 결정하므로 변경 영향이 크다.",
    "DB_CONNECTION_STRING": "direct 백엔드의 PostgreSQL 접속 문자열. 'DB 연결 설정' 탭에서도 편집할 수 있다.",
    "AUTH_AUTH_DB_URL": "사용자 인증 DB 접속 문자열. 비밀번호가 포함될 수 있어 화면에서는 마스킹된다.",
    "ADMIN_USERNAME": "운영자(break-glass) 계정 아이디. 비밀번호·시크릿은 .encenv에서 관리한다.",
    "ORCHESTRATOR_MAX_INPUT_TOKENS": "제어 평면 입력 토큰 상한. 현재 코드가 읽지 않는다(미소비).",
    "ORCHESTRATOR_CONTEXT_BUDGET_RATIO": "상한 대비 트리밍 시작 비율. 현재 코드가 읽지 않는다(미소비).",
    "QUERY_MAX_RETRY_COUNT": "재시도 상한. 현재 graph.py의 하드코딩 값(3)이 쓰인다(미소비).",
    "CONVERSATION_MAX_TURNS": "대화 최대 턴 수. 현재 코드가 읽지 않는다(미소비).",
    "CONVERSATION_TTL_HOURS": "대화 세션 유효 시간. 현재 코드가 읽지 않는다(미소비).",
}

_MASK_VALUE = "********"


# ──────────────────────────────────────────────
# 스키마 응답 모델
# ──────────────────────────────────────────────


class SettingSchemaItem(BaseModel):
    """설정 1건의 카탈로그 메타 + 현재값."""

    env_key: str
    group_key: str
    field_name: str
    type: str  # bool|tristate|int|float|enum|json_list|csv|string|secret
    enum_choices: Optional[list[str]] = None
    optional: bool = False  # enum에서 "(미설정)" 선택 가능 여부
    section: Optional[str] = None
    default: Optional[str] = None          # 코드 기본값의 .env 표기 (None = 미설정)
    file_value: Optional[str] = None       # .env 실존 값 (None = 파일에 없음 = 기본값 사용 중)
    effective_value: Optional[str] = None  # 실제 적용 중인 값 (OS env/.encenv 반영)
    override: Optional[str] = None         # None | "os" | "encenv"
    is_secret: bool = False
    is_sensitive: bool = False
    is_set: bool = False                   # 시크릿의 "설정됨/미설정" 표시용
    requires_restart: bool = True
    apply_mode: str = "restart"            # immediate | reload | restart
    consumed: bool = True
    description: Optional[str] = None


class SettingGroupSchema(BaseModel):
    """설정 그룹(아코디언 1개)."""

    group_key: str
    title: str
    settings: list[SettingSchemaItem]


class SettingsSchemaResponse(BaseModel):
    """`GET /admin/settings/schema` 응답."""

    groups: list[SettingGroupSchema]
    env_file_path: str
    warnings: list[str] = []


@dataclass(frozen=True)
class FieldSpec:
    """인트로스펙션으로 확정된 필드 명세(값 무관)."""

    env_key: str
    group_key: str
    field_name: str
    type: str
    enum_choices: Optional[list[str]]
    optional: bool
    default: Optional[str]
    is_secret: bool
    is_sensitive: bool
    requires_restart: bool
    apply_mode: str  # immediate | reload | restart
    consumed: bool
    section: Optional[str]
    description: Optional[str]
    group_cls: Optional[type[BaseSettings]]  # 그룹 dry-run 대상 (top-level은 None)


@dataclass(frozen=True)
class FieldError:
    """검증 실패 1건."""

    key: str
    message: str


# ──────────────────────────────────────────────
# 인트로스펙션
# ──────────────────────────────────────────────


def _parse_env_keys(path: Path) -> set[str]:
    """env 형식 파일에서 키 집합을 추출한다(주석·빈 줄 무시)."""
    if not path.exists():
        return set()
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        keys.add(stripped.split("=", 1)[0].strip())
    return keys


@lru_cache(maxsize=1)
def encenv_managed_keys() -> frozenset[str]:
    """`.encenv`에서 관리하는 키 집합(= 웹UI 편집이 기능적으로 무효인 키)."""
    return frozenset(_parse_env_keys(_ENCENV_EXAMPLE_FILE) | _ENCENV_FALLBACK_KEYS)


@lru_cache(maxsize=1)
def encenv_present_keys() -> frozenset[str]:
    """실제 `.encenv` 파일에 등재된 키(오버라이드 출처 추정용)."""
    return frozenset(_parse_env_keys(_ENCENV_FILE))


def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    """`X | None` → `(X, True)`. 그 외는 `(annotation, False)`."""
    origin = typing.get_origin(annotation)
    if origin is Union or origin is UnionType:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0], True
    return annotation, False


def _env_key_of(prefix: str, field_name: str, field: Any) -> str:
    """필드의 대표 env 키를 결정한다(alias 우선)."""
    alias = field.validation_alias
    choices = getattr(alias, "choices", None)
    if choices:
        return str(choices[0])
    if isinstance(alias, str):
        return alias
    return f"{prefix}{field_name.upper()}"


def _detect_type(
    env_key: str, field_name: str, annotation: Any, is_secret: bool
) -> tuple[str, Optional[list[str]], bool]:
    """(카탈로그 타입, enum choices, optional) 을 판정한다."""
    if is_secret:
        return "secret", None, False

    base, optional = _unwrap_optional(annotation)

    if base is bool:
        return ("tristate" if optional else "bool"), None, optional
    if typing.get_origin(base) is Literal:
        return "enum", [str(v) for v in typing.get_args(base)], optional
    if env_key in ENUM_OVERRIDES:
        return "enum", list(ENUM_OVERRIDES[env_key]), optional
    if base is int:
        return "int", None, optional
    if base is float:
        return "float", None, optional
    if typing.get_origin(base) is list:
        return "json_list", None, optional
    if field_name.endswith("_csv") or env_key in CSV_KEYS:
        return "csv", None, optional
    return "string", None, optional


def serialize_value(field_type: str, raw: Any) -> Optional[str]:
    """런타임 값을 `.env` 표기 문자열로 직렬화한다(None = 미설정)."""
    if raw is None:
        return None
    if isinstance(raw, SecretStr):
        secret = raw.get_secret_value()
        return secret if secret else None
    if field_type in ("bool", "tristate") or isinstance(raw, bool):
        return "true" if raw else "false"
    if field_type == "json_list" or isinstance(raw, (list, tuple)):
        return json.dumps(list(raw), ensure_ascii=False)
    return str(raw)


@lru_cache(maxsize=1)
def field_index() -> dict[str, FieldSpec]:
    """env 키 → `FieldSpec` 인덱스를 만든다(카탈로그 SSOT)."""
    encenv = encenv_managed_keys()
    descriptions = parse_env_example_descriptions()
    index: dict[str, FieldSpec] = {}

    def add(group_key: str, group_cls: Optional[type[BaseSettings]], prefix: str,
            field_name: str, field: Any) -> None:
        env_key = _env_key_of(prefix, field_name, field)
        annotation = field.annotation
        is_secret = (
            annotation is SecretStr
            or env_key in encenv
            or env_key in _MANUAL_SECRET_KEYS
        )
        field_type, choices, optional = _detect_type(env_key, field_name, annotation, is_secret)
        default = None if is_secret else serialize_value(field_type, field.default)
        if env_key in IMMEDIATE_KEYS:
            apply_mode = "immediate"
        elif env_key in RELOADABLE_KEYS:
            apply_mode = "reload"
        else:
            apply_mode = "restart"
        index[env_key] = FieldSpec(
            env_key=env_key,
            group_key=group_key,
            field_name=field_name,
            type=field_type,
            enum_choices=choices,
            optional=optional,
            default=default,
            is_secret=is_secret,
            is_sensitive=is_secret or env_key in SENSITIVE_VALUE_KEYS,
            requires_restart=env_key not in IMMEDIATE_KEYS,
            apply_mode=apply_mode,
            consumed=env_key not in UNCONSUMED_KEYS,
            section=SECTION_BY_KEY.get(env_key),
            description=DESCRIPTION_OVERRIDES.get(env_key) or descriptions.get(env_key),
            group_cls=group_cls,
        )

    for name, field in AppConfig.model_fields.items():
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseSettings):
            prefix = str(annotation.model_config.get("env_prefix", ""))
            for sub_name, sub_field in annotation.model_fields.items():
                add(name, annotation, prefix, sub_name, sub_field)
        else:
            add(TOP_LEVEL_GROUP, None, "", name, field)

    return index


@lru_cache(maxsize=1)
def parse_env_example_descriptions() -> dict[str, str]:
    """`.env.example`의 키 직전 연속 주석 블록을 도움말로 매핑한다."""
    if not _ENV_EXAMPLE_FILE.exists():
        return {}

    result: dict[str, str] = {}
    block: list[str] = []
    for line in _ENV_EXAMPLE_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            block = []
            continue
        if stripped.startswith("#"):
            text = stripped.lstrip("#").strip()
            if not text or set(text) <= {"=", "-", "─"}:
                continue  # 장식용 구분선
            if text.startswith("===") and text.endswith("==="):
                block = []  # 섹션 제목 — 개별 키 설명이 아니다
                continue
            block.append(text)
            continue
        if "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if block:
                result[key] = " ".join(block)
            block = []
    return result


# ──────────────────────────────────────────────
# 검증 (결정적 3단: sanitize → 타입 → 그룹 dry-run)
# ──────────────────────────────────────────────


def validate_updates(updates: dict[str, str]) -> list[FieldError]:
    """저장 요청 값을 결정적으로 검증한다(시크릿·미지 키·sanitize·타입).

    그룹 dry-run은 파일 전체 맥락이 필요하므로 `dry_run_updates`에서 별도로 수행한다.

    Args:
        updates: {env_key: 값 문자열}

    Returns:
        실패 항목 목록(빈 리스트면 통과)
    """
    index = field_index()
    errors: list[FieldError] = []

    for key, value in updates.items():
        spec = index.get(key)
        if spec is None:
            errors.append(FieldError(key, "카탈로그에 없는 설정 키입니다. 저장할 수 없습니다."))
            continue
        if spec.is_secret:
            errors.append(FieldError(
                key, ".encenv에서 관리하는 시크릿입니다 — 웹UI 수정은 반영되지 않습니다.",
            ))
            continue
        if not isinstance(value, str):
            errors.append(FieldError(key, "값은 문자열이어야 합니다."))
            continue

        sanitize_error = _sanitize_error(value)
        if sanitize_error:
            errors.append(FieldError(key, sanitize_error))
            continue

        type_error = _type_error(spec, value)
        if type_error:
            errors.append(FieldError(key, type_error))

    return errors


def _sanitize_error(value: str) -> Optional[str]:
    """`.env` 한 줄로 안전하게 쓸 수 있는 값인지 검사한다."""
    if "\n" in value or "\r" in value:
        return "값에 개행을 포함할 수 없습니다."
    if "#" in value:
        return "값에 '#'을 포함할 수 없습니다(.env 인라인 주석 금지)."
    return None


def _type_error(spec: FieldSpec, value: str) -> Optional[str]:
    """카탈로그 타입 기준으로 값 형식을 검사한다."""
    if spec.type in ("bool", "tristate"):
        if value not in ("true", "false"):
            return "true 또는 false(소문자)만 사용할 수 있습니다."
        return None
    if spec.type == "enum":
        choices = spec.enum_choices or []
        if value not in choices:
            return f"허용값이 아닙니다: {', '.join(choices)}"
        return None
    if spec.type == "int":
        try:
            int(value)
        except ValueError:
            return "정수만 입력할 수 있습니다."
        return None
    if spec.type == "float":
        try:
            float(value)
        except ValueError:
            return "숫자만 입력할 수 있습니다."
        return None
    if spec.type == "json_list":
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return 'JSON 배열 형식이어야 합니다 (예: ["a","b"]).'
        if not isinstance(parsed, list):
            return 'JSON 배열 형식이어야 합니다 (예: ["a","b"]).'
        if any(not isinstance(item, str) for item in parsed):
            return "배열 항목은 모두 문자열이어야 합니다."
        return None
    return None


_ERROR_FIELD_PATTERN = 'field "'


def dry_run_updates(merged: dict[str, str], changed_keys: list[str]) -> list[FieldError]:
    """변경 키가 속한 그룹을 실제 pydantic 모델로 재구성해 본다(§3.2-6).

    `AppConfig(_env_file=...)`는 nested 그룹에 전파되지 않으므로(default_factory가 자체
    env_file을 쓴다) **그룹 클래스 단위**로 재구성한다. top-level 필드는 `AppConfig`로 검사한다.

    Args:
        merged: 저장 후 `.env`가 가지게 될 전체 키-값
        changed_keys: 이번 요청에서 변경·초기화되는 키

    Returns:
        실패 항목 목록(빈 리스트면 통과)
    """
    import tempfile

    from pydantic import ValidationError
    from pydantic_settings.exceptions import SettingsError

    index = field_index()
    targets: dict[str, type[BaseSettings]] = {}
    for key in changed_keys:
        spec = index.get(key)
        if spec is None:
            continue
        targets[spec.group_key] = spec.group_cls or AppConfig

    if not targets:
        return []

    errors: list[FieldError] = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_file = Path(tmp_dir) / "env"
        tmp_file.write_text(
            "".join(f"{k}={v}\n" for k, v in merged.items()), encoding="utf-8",
        )
        for group_key, cls in targets.items():
            field_to_key = {
                spec.field_name: spec.env_key
                for spec in index.values()
                if spec.group_key == group_key
            }
            try:
                cls(_env_file=str(tmp_file))
            except ValidationError as exc:
                for err in exc.errors():
                    field_name = str(err["loc"][0]) if err["loc"] else ""
                    errors.append(FieldError(
                        field_to_key.get(field_name, field_name),
                        f"설정 검증 실패: {err.get('msg', '값이 올바르지 않습니다.')}",
                    ))
            except SettingsError as exc:
                # 예: 'error parsing value for field "sensitive_columns" from source ...'
                message = str(exc)
                field_name = ""
                if _ERROR_FIELD_PATTERN in message:
                    field_name = message.split(_ERROR_FIELD_PATTERN, 1)[1].split('"', 1)[0]
                errors.append(FieldError(
                    field_to_key.get(field_name, group_key),
                    "설정 파싱 실패: 값 형식을 확인하세요(list 필드는 JSON 배열).",
                ))
    return errors


# ──────────────────────────────────────────────
# 카탈로그 조립
# ──────────────────────────────────────────────


def mask_value(env_key: str, value: Optional[str]) -> Optional[str]:
    """민감 값을 화면 표시용으로 마스킹한다(접속 문자열은 비밀번호 구간만)."""
    if not value:
        return value
    if "://" in value and "@" in value:
        scheme, _, rest = value.partition("://")
        credential, _, host = rest.partition("@")
        if ":" in credential:
            user, _, _password = credential.partition(":")
            return f"{scheme}://{user}:{_MASK_VALUE}@{host}"
    return _MASK_VALUE


def _values_equivalent(spec: FieldSpec, left: Optional[str], right: Optional[str]) -> bool:
    """파일값과 실효값이 실질적으로 같은지 비교한다(표기 차이 무시)."""
    if left == right:
        return True
    if left is None or right is None:
        return False
    if spec.type in ("bool", "tristate"):
        truthy = ("true", "1", "yes", "on")
        return (left.strip().lower() in truthy) == (right.strip().lower() in truthy)
    if spec.type == "json_list":
        try:
            return json.loads(left) == json.loads(right)
        except (json.JSONDecodeError, TypeError):
            return False
    if spec.type in ("int", "float"):
        try:
            return float(left) == float(right)
        except ValueError:
            return False
    return left.strip() == right.strip()


def _effective_value(config: AppConfig, spec: FieldSpec) -> Optional[str]:
    """실행 중 설정 객체에서 해당 필드의 실효값을 읽어 직렬화한다."""
    holder: Any = config
    if spec.group_key != TOP_LEVEL_GROUP:
        holder = getattr(config, spec.group_key, None)
        if holder is None:
            return None
    raw = getattr(holder, spec.field_name, None)
    return serialize_value(spec.type, raw)


def diff_effective_keys(old: AppConfig, new: AppConfig) -> list[str]:
    """두 설정 인스턴스의 실효값이 다른 env 키 목록을 반환한다(리로드 diff용).

    시크릿은 값 직렬화 자체를 피하기 위해 비교에서 제외한다. 반환은 **키 이름만** —
    값은 응답·감사 어디에도 싣지 않는다(Plan 68 §6 Phase 4).
    """
    changed: list[str] = []
    for key, spec in field_index().items():
        if spec.is_secret:
            continue
        if not _values_equivalent(
            spec, _effective_value(old, spec), _effective_value(new, spec)
        ):
            changed.append(key)
    return changed


def _secret_is_set(config: Optional[AppConfig], spec: FieldSpec,
                   file_values: dict[str, str], os_environ: dict[str, str]) -> bool:
    """시크릿이 어디서든 실제로 설정돼 있는지 판정한다(값은 노출하지 않는다)."""
    if config is not None and spec.env_key in ("ADMIN_JWT_SECRET", "AUTH_JWT_SECRET"):
        # 미설정 시 개발용 랜덤 생성이 들어가므로 명시 주입 여부 플래그로 판정한다.
        holder = getattr(config, spec.group_key, None)
        explicit = getattr(holder, "_jwt_secret_explicit", None)
        if explicit is not None:
            return bool(explicit)
    if file_values.get(spec.env_key):
        return True
    if os_environ.get(spec.env_key):
        return True
    if spec.env_key in encenv_present_keys():
        return True
    if config is not None:
        return bool(_effective_value(config, spec))
    return False


def _cluster_by_section(items: list[SettingSchemaItem]) -> list[SettingSchemaItem]:
    """같은 구획(section)의 항목을 연속 배치한다(구획 최초 등장 순서·구획 내 정의 순서 유지).

    config.py 필드 정의 순서는 구획을 오가며 섞여 있어(예: 노이즈 게이트 E2 억제 사이에
    Plan 60 E4 토폴로지가 끼어듦) 그대로 노출하면 UI에 같은 구획 소제목이 반복
    표시된다(실측: 노이즈 게이트 소제목 21회 / 고유 구획 16개). 안정 정렬이라
    무구획(None) 항목 묶음과 각 구획 내부의 상대 순서는 정의 순서 그대로다.
    """
    order: dict[Optional[str], int] = {}
    for item in items:
        if item.section not in order:
            order[item.section] = len(order)
    return sorted(items, key=lambda item: order[item.section])


def build_catalog(
    file_values: dict[str, str],
    config: Optional[AppConfig],
    os_environ: dict[str, str],
    env_file_path: str,
    warnings: Optional[list[str]] = None,
) -> SettingsSchemaResponse:
    """카탈로그 + 현재값을 조립한다.

    Args:
        file_values: `.env` 파싱 결과
        config: 실효값 산출용 `AppConfig` 인스턴스(실패 시 None — 카탈로그는 계속 제공)
        os_environ: OS 환경변수(오버라이드 출처 추정)
        env_file_path: `.env` 절대경로
        warnings: 응답에 실을 경고 목록

    Returns:
        스키마 응답
    """
    index = field_index()
    encenv_keys = encenv_present_keys()
    grouped: dict[str, list[SettingSchemaItem]] = {key: [] for key in GROUP_ORDER}

    for spec in index.values():
        file_value = file_values.get(spec.env_key)
        effective = _effective_value(config, spec) if config is not None else None

        override: Optional[str] = None
        expected = file_value if file_value is not None else spec.default
        if effective is not None and not _values_equivalent(spec, expected, effective):
            if spec.env_key in os_environ:
                override = "os"
            elif spec.env_key in encenv_keys:
                override = "encenv"

        if spec.is_secret:
            item_file_value = None
            item_effective = None
        elif spec.is_sensitive:
            item_file_value = mask_value(spec.env_key, file_value)
            item_effective = mask_value(spec.env_key, effective)
        else:
            item_file_value = file_value
            item_effective = effective

        grouped.setdefault(spec.group_key, []).append(SettingSchemaItem(
            env_key=spec.env_key,
            group_key=spec.group_key,
            field_name=spec.field_name,
            type=spec.type,
            enum_choices=spec.enum_choices,
            optional=spec.optional,
            section=spec.section,
            default=None if spec.is_secret else spec.default,
            file_value=item_file_value,
            effective_value=item_effective,
            override=override,
            is_secret=spec.is_secret,
            is_sensitive=spec.is_sensitive,
            is_set=_secret_is_set(config, spec, file_values, os_environ) if spec.is_secret
            else bool(file_value),
            requires_restart=spec.requires_restart,
            apply_mode=spec.apply_mode,
            consumed=spec.consumed,
            description=spec.description,
        ))

    groups = [
        SettingGroupSchema(
            group_key=group_key,
            title=GROUP_TITLES.get(group_key, group_key),
            settings=_cluster_by_section(grouped[group_key]),
        )
        for group_key in GROUP_ORDER
        if grouped.get(group_key)
    ]
    return SettingsSchemaResponse(
        groups=groups,
        env_file_path=env_file_path,
        warnings=warnings or [],
    )
