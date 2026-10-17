"""설정 로드 모듈.

환경변수에서 애플리케이션 설정을 읽어온다.
pydantic-settings를 사용하여 타입 안전한 설정 관리를 제공한다.

plans/166 W2 I4: 설정 클래스는 응집 단위 하위 모듈(`llm`·`data_access`·`text2sql`·
`security`·`alarm`·`services`)로 나눴고 여기서 전부 재노출한다(호출부 무변경 · D-337 ①).
`AppConfig`·`load_config`는 테스트가 `src.config`에서 patch하는 seam이라 이 모듈에 둔다.
"""


# 옮긴 주석·문자열의 긴 줄은 이동 전부터 있던 것이다 — 바이트 그대로 두려고 파일 단위로 끈다.
# ruff: noqa: E501

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings

from src.config.alarm import AlarmConfig as AlarmConfig
from src.config.alarm import NoiseGateConfig as NoiseGateConfig
from src.config.alarm import WorkbConfig as WorkbConfig
from src.config.data_access import DBHubConfig as DBHubConfig
from src.config.data_access import MultiDBConfig as MultiDBConfig
from src.config.data_access import PolestarRestConfig as PolestarRestConfig
from src.config.data_access import QueryConfig as QueryConfig
from src.config.data_access import RedisConfig as RedisConfig
from src.config.data_access import SchemaCacheConfig as SchemaCacheConfig
from src.config.llm import LLMConfig as LLMConfig
from src.config.llm import OrchestratorConfig as OrchestratorConfig
from src.config.security import AdminConfig as AdminConfig
from src.config.security import AuditConfig as AuditConfig
from src.config.security import AuthConfig as AuthConfig
from src.config.security import HostAuthzConfig as HostAuthzConfig
from src.config.security import SecurityConfig as SecurityConfig
from src.config.services import DrmConfig as DrmConfig
from src.config.services import ObservabilityConfig as ObservabilityConfig
from src.config.services import PortalHelpConfig as PortalHelpConfig
from src.config.services import RagConfig as RagConfig
from src.config.services import ResultVizConfig as ResultVizConfig
from src.config.services import ServerConfig as ServerConfig
from src.config.text2sql import CompositeConfig as CompositeConfig
from src.config.text2sql import IntentFrameConfig as IntentFrameConfig
from src.config.text2sql import RouterConfig as RouterConfig
from src.config.text2sql import SynonymMatchConfig as SynonymMatchConfig
from src.config.text2sql import Text2SQLConfig as Text2SQLConfig

logger = logging.getLogger(__name__)


class AppConfig(BaseSettings):
    """애플리케이션 전체 설정을 통합 관리한다."""

    # nested config는 반드시 default_factory로 선언한다 — 인스턴스 기본값(`= LLMConfig()`)은
    # 클래스 정의(모듈 임포트) 시점의 env로 고정되어, 이후 os.environ 변경 +
    # load_config.cache_clear() 재로드가 반영되지 않는다(2026-07-15 E1 하네스 A/B 무효화 실측).
    llm: LLMConfig = Field(default_factory=LLMConfig)
    orchestrator: OrchestratorConfig = Field(default_factory=OrchestratorConfig)
    dbhub: DBHubConfig = Field(default_factory=DBHubConfig)
    query: QueryConfig = Field(default_factory=QueryConfig)
    synonym: SynonymMatchConfig = Field(default_factory=SynonymMatchConfig)   # Plan 61 트랙 B: 동의어 매칭 보강
    text2sql: Text2SQLConfig = Field(default_factory=Text2SQLConfig)          # Plan 61 트랙 C: 결정적 SQL 조합
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    admin: AdminConfig = Field(default_factory=AdminConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    multi_db: MultiDBConfig = Field(default_factory=MultiDBConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    schema_cache: SchemaCacheConfig = Field(default_factory=SchemaCacheConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)  # D-140/D-141: SQL 파일 로그·실패 트레이스
    alarm: AlarmConfig = Field(default_factory=AlarmConfig)
    workb: WorkbConfig = Field(default_factory=WorkbConfig)
    noise_gate: NoiseGateConfig = Field(default_factory=NoiseGateConfig)  # Plan 52: 알람 노이즈 캔슬링 게이트 (형제 필드)
    polestar_rest: PolestarRestConfig = Field(default_factory=PolestarRestConfig)  # Plan 71: 실시간 사용률 API
    drm: DrmConfig = Field(default_factory=DrmConfig)  # Plan 74: 양식 업로드 DRM 해제
    composite: CompositeConfig = Field(default_factory=CompositeConfig)  # Plan 78: 복합 질의 호스트 조사
    router: RouterConfig = Field(default_factory=RouterConfig)  # Plan 79 트랙 B·C: 라우터 2단 분리·신뢰도
    host_authz: HostAuthzConfig = Field(default_factory=HostAuthzConfig)  # Plan 78 W3-5: 호스트 인가
    # plans/107: 의도 프레임·정규 질의
    intent_frame: IntentFrameConfig = Field(default_factory=IntentFrameConfig)
    # plans/126: 문서 RAG(FabriX Retrieval) — 기본 off · 라우팅 미포함(plans/125 소유)
    rag: RagConfig = Field(default_factory=RagConfig)
    # plans/160: 조회 결과 그래프 보기·값 해석 — 기본 off
    result_viz: ResultVizConfig = Field(default_factory=ResultVizConfig)
    # plans/142: 포탈 기능 도움말(매뉴얼 근거) — 기본 on(G-7 (b) 예외 · 근거는 클래스 주석)
    portal_help: PortalHelpConfig = Field(default_factory=PortalHelpConfig)
    checkpoint_backend: Literal["sqlite", "postgres"] = "sqlite"
    checkpoint_db_url: str = "checkpoints.db"

    # DB 직접 연결 설정 (DBHub 대안 / 레거시 단일 DB)
    db_backend: Literal["dbhub", "direct"] = "direct"
    db_connection_string: str = ""

    # 시멘틱 라우팅 활성화 여부
    # None(미입력) = 멀티 DB 환경이면 자동 활성화, 단일/레거시면 비활성
    # True/False = 명시적 강제(.env·OS env 모두 반영). 종전 os.getenv 판정은 `.env`의 false를
    # 무시해 ACTIVE_DB_IDS가 있으면 강제 활성화되는 버그가 있었다(Known Mistakes 2026-06-10).
    enable_semantic_routing: bool | None = None

    # 의도 분해 오케스트레이션(사다리 2단 = 트랙 A) 활성화 여부 (Plan 48 / D-037)
    # None(미입력) = **on** — 기준 경로(기준 운영 단)는 2단 intent_orchestration이다(D-251 ① ·
    # 2026-09-23 · D-225 ④ "미입력 = off"를 개정). DB 등록 상태와는 무관하다 — 종전(D-037)의
    # "멀티 DB 환경이면 자동 on"은 DB 등록만으로 단이 바뀌는 경로라 폐지된 채로 둔다(X-T11).
    # True/False = 명시적 강제(.env·OS env 모두 반영). 사다리 상위 단(deep_agent)이 성립하면
    # 이 플래그가 true여도 2단 노드는 등록되지 않는다 — docs/21_orchestration_ladder.md §1·§2
    #
    # 개명(D-162 / plans/70 L2): `enable_deepagent_orchestration` → `enable_intent_orchestration`.
    # 구 이름이 가리키는 것은 **2단(트랙 A)** 인데 1단 플래그 `enable_deepagents_package`
    # (트랙 B)와 이름이 뒤섞여 오독을 유발했다 — plans/70 v1 오판의 원인 중 하나다.
    # 구 환경변수명은 AliasChoices로 계속 받는다(하위호환). **폐기 기한 2027-02-20**(D-161 ①).
    enable_intent_orchestration: bool | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "ENABLE_INTENT_ORCHESTRATION",
            "ENABLE_DEEPAGENT_ORCHESTRATION",  # 구 이름 — 2027-02-20 폐기 예정
        ),
    )

    # plans/121 묶음 B — **신규 플래그 없이 기본 동작을 바꾼 결함 교정**
    # (D-162 예외 · D-272 ③ · D-273). 대상: 2단 턴 초기화(TP-1.1 — `create_followup_input` 6키 ·
    # 부분 결과는 이번 계획 행만 1.1b) · 계획 요약 필드(TP-0.1) ·
    # 재계획 신규 task 교정·DAG(TP-1.3) · 닫힌 어휘(TP-1.7) ·
    # 병합 판정 선행과 병합 경로 침묵 손실 교정(TP-4.1·11.6) · 병합 출처(TP-11.3) ·
    # 0건 퍼널 사유 전달(TP-11.5) · 재계획 예시 1(TP-11.4) · 테이블 목록 정렬(TP-11.10).
    # 근거: 현행 동작 자체가 결함이고 끄는 쪽이 결함 보존이라 스위치를 두지 않는다 —
    # 한 재측정 묶음으로 기준선(R1) 대비 잰다(D-251 ⑦).
    # 묶음 C도 확정 게이트대로 플래그 없이 바꿨다(D-273 ⑥~⑪): 존 재진입 복합 계획 복원(TP-1.2 ·
    # G-30) · 폴백 경과 노트 플래그 무관(TP-1.6 — D-224 ⑥ 부분 개정) · 2단 병합 불성립 복합 턴의
    # 단계별 답변(TP-4.5 · G-7 ① — D-062 개정).
    # 결과 기반 재계획 최대 반복 (무한 루프 방지, R-A3/R-11)
    max_replan: int = 3
    # 예산 인지 재계획 프롬프트(plans/119 T-6 · 문헌 L-9) — **기본 off = 현행과 비트 동일**(D-162 ·
    # D-267 ⑥). on이면 재계획 평가 LLM 입력에 조회 마감까지 남은 시간·남은 재계획 횟수·"지금
    # 결과로 종결 가능"을 싣는다. LLM 행동 의존이라 결정적 가드(T-1~T-4) 뒤에 arm으로 잰다.
    replan_budget_prompt_enabled: bool = False

    # 사다리 3단 계획 루프 (plans/103 P2 · plans/111 C-4·C-5) — **기본 off = 현행 3단과 비트 동일**
    # (plans/80 §5.4-③). on이면 3단 빌드에 plan → normalize → dispatch ─(Send)→ task_run → join →
    # [replan 루프] → finalize 노드를 등록하고, 라우터가 계획 필요(`needs_plan`)를 함께 낸다.
    # 계획 필요로 판정된(또는 순차 표지가 있는) 데이터·알람 질의만 이 루프로 가고 단일 의도는 현행
    # 직결 체인 그대로다. SQL 승인이 켜져 있으면 진입하지 않는다(승인 게이트 우회 금지 — 103 P3 전).
    # 켜면 3단의 `sequential_runner`(D-203 3·4단 러너)는 등록하지 않는다 — 같은 질의를
    # 이 루프가 맡는다.
    # 기동 시 1회 해석한다(요청 시점 변경 금지 — 그래프 노드 구성·라우터 프롬프트 접두가 흔들린다).
    tier3_plan_loop_enabled: bool = False

    # ── 교차 시스템 질의 (plans/102 · D-224) — 둘 다 **기본 off = 현행과 비트 동일** ──────
    # 기동 시 1회 해석한다(요청 시점 변경 금지 — 그래프 노드 구성·프롬프트 접두가 흔들린다).
    # 값 기반 키 브리지(§3.2·§3.3): 선행 결과의 서버 키를 컬럼명이 아니라 값(호스트명·FQDN·IP)으로
    # 판정하고, 대상 DB 매니페스트(`config/db_profiles/{db_id}.yaml` `entity_keys`)로 대상 컬럼을
    # 코드가 확정한다. off면 종전 컬럼명 판정(D-100)과 "동등한 식별 컬럼" 위임 문구 그대로다.
    cross_system_key_bridge_enabled: bool = False
    # 식별자 소재 프로브(§3.4): 3단 `semantic_router` 직후에 `entity_locator` 노드를 등록해
    # 명시 식별자가 어느 시스템에 있는지 고정 조회로 먼저 확인한다. off면 노드·엣지 미등록.
    cross_system_probe_enabled: bool = False

    # Plan 49 / D-037 트랙 B: deepagents 실제 패키지(vLLM 오케스트레이터 + FabriX 워커) 활성화 여부.
    # 명시적 opt-in(기본 False) — vLLM 인프라가 필요하므로 자동 활성화하지 않는다.
    # True + vLLM 가용 시 트랙 B, 그 외(False/미서빙)는 semantic_router 사용
    # (가용성 분기 — deep_agent.select_orchestration_backend).
    enable_deepagents_package: bool = False

    # 테스트 전용 — deepagent(트랙 B) 경로 검증 시 워커(데이터 평면) LLM provider를 강제 교체.
    # 미설정(None)=운영 그대로(config.llm.provider, 보통 fabrix). "gemini" 지정 시 deepagent
    # 경로 전체(input_parser/field_mapper + deep_agent 워커)가 gemini로 동작 → FabriX 없이 검증.
    # 오케스트레이터(제어 평면)는 ORCHESTRATOR_PROVIDER로 별도 지정 (Plan 49 §4.7 / D-037).
    worker_provider_override: Literal["ollama", "fabrix", "gemini"] | None = None

    # ── 구조화 출력 백엔드 (Plan 79 트랙 E-3 / D-169) ────────────────────────
    # LLM 응답을 타입 계약(pydantic)으로 받고, 검증 실패 시 오류를 모델에 되먹여 재질의한다.
    # "none"(기본)이면 어댑터가 관여하지 않아 **현행 동작과 비트동일**하다.
    # instructor는 optional extra(`structured`)이며 미설치 시 graceful 강등된다(로그 남김).
    # ⚠ 기동 시 1회 해석한다(plans/78 P14) — 요청 시점에 바꾸면 프롬프트 접두가 흔들려
    #   KV 캐시가 무효화된다.
    structured_output_backend: Literal["none", "instructor"] = "none"

    # 재시도 횟수. 총 LLM 호출은 이 값 + 1회다(instructor `max_retries` 규약 실측).
    # 라우터는 지연에 민감하므로 1로 시작하고 실측 후 조정한다(응답시간 목표: 단순 <10s).
    structured_output_max_retries: int = 1

    # ── 웹 UI 기본 테마 ────────────────────────────────────────────────
    # 사용자·운영자 화면의 **전역 기본 테마**. 운영자 대시보드 헤더의 테마 토글이
    # 이 값을 저장하며, 각 페이지 head의 부트스트랩(static/js/theme.js)이
    # `GET /api/v1/ui/theme`로 읽어 `<html data-theme>`에 반영한다.
    # 개인이 헤더 토글로 고른 값(localStorage)은 이 기본값보다 우선한다.
    ui_default_theme: Literal["light", "dark"] = "light"

    # Polestar 전용 프롬프트를 적용할 DB ID (콤마 구분으로 복수 지정 가능)
    # .env에서 POLESTAR_DB_IDS=polestar,polestar2 로 설정하면
    # active_db_id가 이 목록에 포함될 때 Polestar 전용 시스템 프롬프트를 사용한다.
    # 비어있으면 전용 프롬프트를 사용하지 않음 (범용 프롬프트 적용).
    polestar_db_ids: str = ""

    def get_polestar_db_ids(self) -> set[str]:
        """Polestar 전용 프롬프트를 적용할 DB ID 집합을 반환한다."""
        if not self.polestar_db_ids:
            return set()
        return {x.strip() for x in self.polestar_db_ids.split(",") if x.strip()}

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Phase 3: 멀티턴 대화 / Human-in-the-loop
    enable_sql_approval: bool = False         # SQL 승인 기능 활성화
    conversation_max_turns: int = 20          # 대화 최대 턴 수
    conversation_ttl_hours: int = 24          # 대화 세션 유효 시간

    # populate_by_name: validation_alias가 붙은 필드(enable_intent_orchestration)를
    # **필드명으로도** 주입할 수 있게 한다. 없으면 AppConfig(enable_intent_orchestration=False)가
    # 조용히 무시되고 .env 값으로 떨어진다(실측 2026-08-24 — 전체 스위트에서만 드러난 회귀).
    # MultiDBConfig가 ACTIVE_DB_IDS alias에 같은 설정을 쓰는 것과 동일한 이유다.
    model_config = {"env_file": ".env", "extra": "ignore", "populate_by_name": True}

    def model_post_init(self, __context: object) -> None:
        """시멘틱 라우팅 및 오케스트레이션 활성화를 자동 판단한다."""
        # 플래그 미입력(None)이면 멀티 DB 연결이 하나라도 설정된 경우 자동 활성화한다.
        # 명시적 true/false는 pydantic-settings가 .env·OS env에서 필드로 직접 읽어 존중한다
        # (enable_intent_orchestration과 동일 방식 — os.getenv 미사용).
        # tri-state 자동 해석이 발동했는지 기록한다(D-161 / plans/70 P0-1).
        # 덮어쓴 뒤에는 명시 설정과 구별할 수 없으므로 여기서만 남길 수 있다.
        # 운영 경로가 DB 등록 상태에 종속된다는 사실이 기동 로그에 드러나야 한다.
        object.__setattr__(
            self,
            "_orchestration_resolved_by",
            # 2단 플래그만 미입력이면 DB 등록 상태와 무관하게 코드 기본값(on)이다(D-251 ①).
            "auto_multidb" if self.enable_semantic_routing is None
            else "code_default" if self.enable_intent_orchestration is None
            else "explicit_env",
        )

        # 구 환경변수명 사용을 알린다(개명 D-162 / plans/70 L2, 폐기 2027-02-20).
        # 여기서 os.environ을 보는 것은 **설정값 판정이 아니라 폐기 예고**다 —
        # 값은 위 pydantic 필드가 이미 결정했다(Known Mistakes 2026-06-10 준수).
        #
        # 침묵 손실 경로가 하나 있다: 두 키가 함께 있을 때 **어느 쪽이 이기는지가
        # pydantic-settings 버전에 따라 뒤집힌다**.
        #   2026-08-24 실측: AliasChoices의 별칭 순서가 이겨 `.env` 신 키가 우선
        #   2026-08-28 실측(pydantic-settings 2.15.0): **소스 우선순위가 이겨 OS env 구 키가 우선**
        # 그래서 경고에 규칙을 못박지 않는다 — 규칙을 적으면 라이브러리가 바뀌는 순간
        # 경고가 운영자에게 **거짓을 말한다**(실제로 그렇게 돼 있었다). 대신 **구 키 원값과
        # 실제 적용값을 나란히** 보여주고, 둘이 어긋나면 그 사실을 명시한다.
        _legacy_raw = os.environ.get("ENABLE_DEEPAGENT_ORCHESTRATION")
        if _legacy_raw is not None:
            _applied = self.enable_intent_orchestration
            _legacy_parsed = {"true": True, "1": True, "yes": True,
                              "false": False, "0": False, "no": False}.get(
                _legacy_raw.strip().lower()
            )
            _conflict = (
                _legacy_parsed is not None and _applied is not None
                and _legacy_parsed != _applied
            )
            logger.warning(
                "ENABLE_DEEPAGENT_ORCHESTRATION은 ENABLE_INTENT_ORCHESTRATION으로 개명됐습니다"
                "(2027-02-20 폐기 예정). 구 키 값=%s · **실제 적용값=%s**.%s "
                "두 키를 동시에 두지 마세요 — 어느 쪽이 이기는지는 pydantic-settings 버전에 "
                "따라 달라집니다(2.15.0 실측: OS env 구 키가 `.env` 신 키를 이깁니다). "
                "구 키를 제거하고 신 키만 남기세요 — docs/21_orchestration_ladder.md",
                _legacy_raw, _applied,
                " ⚠ 구 키 값이 적용되지 않았습니다." if _conflict else "",
            )

        # 자동 해석이 발동하면 경고를 남긴다(plans/70 L3). 덮어쓰고 나면 명시 설정과
        # 구별할 수 없으므로 여기가 유일한 기회다. 값만 알려주는 경고는 조치로 이어지지
        # 않으므로, "무엇이 무엇에 종속되는가"를 함께 말한다.
        if self.enable_semantic_routing is None:
            logger.warning(
                "enable_semantic_routing 미입력 → 멀티 DB 등록 여부로 자동 결정합니다"
                "(등록 %d건 → %s). "
                "실행 경로가 DB 등록 상태에 종속되므로, 고정하려면 .env에 명시하세요 "
                "— docs/21_orchestration_ladder.md §6",
                len(self.multi_db.get_active_db_ids()),
                bool(self.multi_db.get_active_db_ids()),
            )
        if self.enable_intent_orchestration is None:
            logger.warning(
                "enable_intent_orchestration 미입력 → on으로 확정합니다(2단 기준 · D-251). "
                "3단(비교 arm)으로 돌리려면 .env에 ENABLE_INTENT_ORCHESTRATION=false를 명시하세요 "
                "— docs/21_orchestration_ladder.md §6",
            )

        if self.enable_semantic_routing is None:
            self.enable_semantic_routing = bool(self.multi_db.get_active_db_ids())

        # D-251 ①(2026-09-23 · D-225 ④ 개정): 플래그 미입력(None)이면 **항상 on**이다 — 기준
        # 운영 단은 2단 intent_orchestration이고 3단은 비교 arm이다. DB 등록 상태와 무관하게
        # 고정한다 — 종전(Plan 48 / D-037)의 "멀티 DB면 자동 on"은 DB 등록만으로 단이 바뀌는
        # 경로였다(X-T11).
        # 명시적 true/false는 pydantic-settings가 .env·OS env에서 필드로 직접 읽어 그대로 존중한다
        # (os.getenv 미사용 — .env-only 설정도 반영, Known Mistakes 2026-06-10).
        if self.enable_intent_orchestration is None:
            self.enable_intent_orchestration = True


@lru_cache(maxsize=1)
def load_config() -> AppConfig:
    """설정을 로드하여 반환한다.

    싱글톤 패턴으로 동일한 설정 인스턴스를 재사용한다.

    Returns:
        애플리케이션 설정
    """
    config = AppConfig()
    logger.info("애플리케이션 설정 로드 완료")
    return config
