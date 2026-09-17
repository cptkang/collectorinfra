"""에이전트 설정 — .env 로딩은 pydantic-settings 필드로만 판정한다 (os.getenv 금지)."""

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# 실 조사 LLM 게이트의 스텁 사유 — 사용자·감사에 그대로 노출된다(침묵 금지 · D-123 ⑦ · D-230).
# 키 부재 문구는 종전 그대로다(본체 `tests/test_briefing_contract.py`가 같은 리터럴을 쓴다).
STUB_LLM_KEY_ABSENT = "조사 미실행 — LLM 키 부재(스텁)"
STUB_LLM_DISABLED = "조사 미실행 — 조사 LLM 비활성(INVESTIGATION_LLM_ENABLED=false · 스텁)"


class AgentSettings(BaseSettings):
    # env_file은 CWD 기준 (".env", ".encenv") — 레포 루트에서 기동하면 collectorinfra
    # 보안파일(.encenv)의 키를 그대로 재사용하고, 패키지 분리 후에는 자체 .env/.encenv가
    # 같은 규약으로 동작한다. populate_by_name=True는 테스트의 필드명 kwarg 생성 보장.
    model_config = SettingsConfigDict(
        env_file=(".env", ".encenv"), populate_by_name=True, extra="ignore"
    )

    model: str = "anthropic/claude-sonnet-5"
    api_key: SecretStr | None = None
    # 결정적 가드(실측): 다중 메트릭 실 조사는 10 step으로 `Too many LLM calls -
    # exceeded max_steps`로 미완주(포커스 질의는 20에서 완주, 브로드 트리아지는 30도 초과).
    # 마진으로 40 — 상한 도달 시 DiagnosisAgent.ask가 구조화 미완주로 graceful 반환하고
    # (하드 실패 금지·Plan 02 §12-④), dispatcher 전체 타임아웃(300s)이 하드 백스톱.
    max_steps: int = 40

    # 폴스타 MCP 접속 설정 (Plan 06 §94 · D-119). mcp_server(Plan 04)가 노출하는
    # SSE 엔드포인트로, DiagnosisAgent(mcp_servers=...)에 등록해 소비한다.
    # (D-119) prometheus_url·prometheus_auth_header는 여기 두지 않는다 —
    # Prometheus 접속 설정은 mcp_server 측(config.toml·서버 .env)으로 일원화한다.
    # 토큰은 SecretStr로 pydantic 필드로만 판정한다(env: POLESTAR_MCP_TOKEN,
    # 미설정 시 None → 무인증 로컬 픽스처 경로).
    polestar_mcp_url: str = "http://localhost:9099/sse"
    polestar_mcp_token: SecretStr | None = None

    # 개발·테스트 LLM — Gemini API (D-120). 운영 LLM(model)과 분리한다.
    # 기본값 gemini-3.5-flash — 2026-07-28 ListModels 실측 채택: D-021 권장이던
    # gemini-2.0-flash는 서버측 퇴역(404 실측), gemini-2.5-*는 D-021 사용 금지,
    # gemini-3.1-pro는 preview만 존재. 3.5-flash는 실 API tool-calling 왕복 검증 완료
    # (문서 권장치가 아니라 가용 목록 실측으로 확정할 것). gemini_api_key는 SecretStr | None
    # 으로 pydantic 필드로만 판정한다(env: GEMINI_API_KEY 또는 LLM_GEMINI_API_KEY —
    # 후자는 collectorinfra .encenv 보안파일 규약(LLM_ prefix) 재사용, 미설정 시 None →
    # 스모크·e2e 보류).
    investigation_llm_model: str = "gemini/gemini-3.5-flash"
    gemini_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("GEMINI_API_KEY", "LLM_GEMINI_API_KEY"),
    )

    # ── 실 조사 LLM 게이트 (D-230 · D-123 ⑦ 개정) — tri-state ──
    # None(기본) = 종전 판정 그대로(`gemini_api_key`가 있어야 실 조사 · 비트 동일)
    # True       = 키 없이도 실 조사(사내 vLLM 등 — 종전의 `GEMINI_API_KEY=dummy` 우회를 대체)
    # False      = 키가 있어도 항상 명시 스텁
    # 판정은 `investigation_llm_stub_reason()` 한 곳이다(dispatcher 2 · stub executor 1 · `holmes_ready` 1).
    # ⚠ `.env`에 빈 값(INVESTIGATION_LLM_ENABLED=)·`null`을 두면 None이 아니라 로드 실패다 — 줄을 두지 않는다.
    # **None(키 추론) 경로 만료일 2027-03-17**(D-161 ① · 6개월) — 운영·개발 배선이 전부 명시값으로 옮겨졌는지
    # 보고 None 경로 삭제(명시 필수화) 또는 사유부 연장을 판정한다. True/False 자체는 상시 운영 스위치다.
    investigation_llm_enabled: bool | None = None

    # 사내 OpenAI 호환 엔드포인트(vLLM 등)의 base_url — litellm `api_base`로 전달된다.
    # None이면 프로바이더 기본 경로(Gemini 등 SaaS)를 쓴다. 사내 FabriX(KBGenAIChat)는
    # OpenAI 비호환이라 여기 넣을 수 없다 — 조사 LLM은 tool-calling 되는 엔드포인트여야 한다
    # (holmes ToolCallingLLM이 매 호출에 tools/tool_choice를 싣고 폴백이 없다).
    # env: API_BASE. holmes 0.36.0 Config가 api_base 필드를 보유함을 실측 확인했다.
    api_base: str | None = None

    # ── holmes 토큰 예산 — 출력 상한·컨텍스트 창 (2026-09-17 실측) ──
    # litellm 목록에 없는 모델(사내 vLLM·로컬 OpenAI 호환 서버의 served name)이면 holmes가 출력 상한을
    # max(64000, 컨텍스트×12%) · 컨텍스트를 200000으로 잡고 **매 요청 max_tokens=64000**을 보낸다
    # (`holmes/core/llm.py` get_maximum_output_token·completion). OpenAI 호환 서버는 요청값이 서버 기본
    # 상한보다 우선하므로 퇴행 루프 한 번이 64000토큰 생성을 붙든다. 컨텍스트가 실제 창보다 크면 압축이
    # 늦게 발동해 서버 초과 오류로 끝난다(D-213 실패 유형).
    # holmes 자체 조정 수단은 같은 이름의 **프로세스 환경변수**(임포트 시 1회 읽음)뿐이라 `.env`에 적으면
    # 먹지 않는다(env_file은 os.environ 미주입). 필드 이름을 holmes env와 같게 두어 셸 env·`.env`
    # 어느 쪽에 적어도 같은 값이 들어오고, 설정되면 DiagnosisAgent가 인스턴스에 적용한다(holmes env보다 우선).
    # None(기본) = holmes 동작 그대로(비트 동일). 둘은 함께 준다 — 출력 예약분이 컨텍스트 이상이면
    # holmes가 매 호출을 컨텍스트 초과로 거부한다. vLLM은 입력+출력 ≤ --max-model-len이어야 한다(D-213).
    override_max_content_size: int | None = Field(default=None, gt=0)
    override_max_output_token: int | None = Field(default=None, gt=0)

    def investigation_llm_stub_reason(self) -> str | None:
        """실 조사 LLM 게이트 — 스텁 사유를 돌려준다(None이면 실 조사 가능).

        게이트 4곳이 이 함수 하나만 본다. 조사함수 미주입·dispatcher 미배선은 호출부 고유 사유라
        여기서 판정하지 않는다(이 함수가 None일 때만 호출부가 자기 사유를 붙인다).
        """
        if self.investigation_llm_enabled is False:
            return STUB_LLM_DISABLED
        if self.investigation_llm_enabled is None and self.gemini_api_key is None:
            return STUB_LLM_KEY_ABSENT
        return None

    # 조사 서비스(interface/mcp_service) 정적 Bearer 토큰 (Plan 05 §5-인증).
    # None이면 무인증(로컬/개발). SecretStr로 pydantic 필드로만 판정한다
    # (env: SERVICE_BEARER_TOKEN, os.getenv 금지). 협의 후 mTLS 승격 여지.
    service_bearer_token: SecretStr | None = None

    # ── 조사 dispatcher 폭주 방지 가드 (Plan 02 §4·§10 — 전부 기본 off/보수값) ──
    # 스칼라 필드이므로 nested Field(default_factory=...)는 불요(중첩 모델 없음).
    # 조사 1건 **전체** 타임아웃(per-call 아님·asyncio.wait_for). HolmesGPT 다단계
    # 조사를 감안해 300s(원본 45s보다 상향). collectorinfra MCP 동기 타임아웃(60s)보다
    # 길어 submit/poll 비동기 잡 패턴이 성립한다.
    investigation_timeout_seconds: int = 300
    # 동일 fingerprint 재조사 최소 간격(초). None이면 TTL dedup off
    # (JobStore active-fingerprint dedup만 동작). 켜면 완료된 조사도 이 간격 내 재조사 억제.
    investigation_dedup_ttl_seconds: float | None = None
    # 동시 조사 상한(세마포어). 보수적 기본 2.
    investigation_max_concurrent: int = 2
    # 시간당 조사 횟수 상한. None이면 예산 가드 off. 초과 시 신규 조사 거부.
    investigation_hourly_budget: int | None = None
    # ── 비선두 system 메시지 강등 가드 (D-213 후속) — **기본 on(명시적 예외)** ──
    # Qwen 채팅 템플릿(vLLM)은 system을 맨 앞에만 허용하는데, holmes 컨텍스트 압축이
    # 압축 결과 말미에 role="system" 안내를 붙여 `System message must be at the beginning.`
    # (BadRequestError)으로 조사가 전건 실패한다(폐쇄망 실측 2026-09-11 — 압축이 발동하는
    # 모든 조사에서 재현). 가드는 0번이 아닌 system의 role만 user로 바꾸고 content는 보존한다.
    # **기본 off가 아닌 이유**(plans/80 §5.4-③ 예외): 교정할 것이 없으면 원본 객체를 그대로
    # 통과시키는 no-op이라 정상 경로가 비트 동일하고, off는 곧 "계속 실패"를 뜻한다.
    # **만료일 2027-03-11**(D-161 C1) — holmes 수정·모델 교체 시 유지/삭제를 그때 판정한다.
    system_message_position_fix: bool = True
    # 중요도 2차 판정(severity_judge) 활성화. 기본 off — 켜야 도구 원시 출력
    # 시그니처 매칭을 수행한다(escalate-only). off면 게이트 판정을 그대로 승계.
    severity_judge_enabled: bool = False
    # 조치 권고(remediation_recommender) 활성화 (Plan 02 §9 · D-011). 기본 off —
    # 켜면 매칭된 시그니처에서 조치 후보를 결정적으로 도출해 브리핑 권고에 싣는다.
    # **제시 전용**: 실행 코드 경로는 어느 배치에도 존재하지 않는다(D-003·테스트 고정).
    # severity_judge가 off면 매칭 시그니처가 없어 권고도 비어 있다(근거 없는 권고 금지).
    remediation_recommender_enabled: bool = False
    # 조사 지침 추가 문구(plans/50 G5 · D-197). 기본 None — 운영자가 .env로 넣는 자유 지침이며
    # Plan 51 §6 플레이북의 편입점이다. `investigation_guidance.build_guidance`가 말미에 덧붙인다.
    investigation_guidance_extra: str | None = None
    # 사건 구간 증거 사전수집 + 결정적 상관(plans/50 G4 · D-197). 기본 off — 켜면 잡에 reference_time이
    # 있을 때 조사 전에 mcp_server 도구(전 알람 1 + 지표 4)를 코드가 호출해 선행 신호·타임라인을 계산하고
    # 조사 지침·브리핑에 싣는다. LLM 호출 0. 실패해도 조사를 막지 않는다(상관 없음 + 감사).
    evidence_correlation_enabled: bool = False
    # plans/91 1-2(C′-2): 사전수집 배치 말미에 변경 이력 1건을 더해 "변경 직후" 가설·타임라인을 만든다.
    # 기본 off = 배치·결과·to_dict 키 집합 비트 동일. **만료일 2027-03-10**(D-161 C1) — 운영 실측 후 on 또는 삭제.
    evidence_change_overlay_enabled: bool = False
    # plans/91 1-3(C′-1): 페이로드 meta.root_resource_name(E4 root 서버)을 연관 서버로 소비해 알람 1건씩 추가 수집.
    # 상한(대수). 0 = off(비트 동일). U-I 확정 기본 상한 3은 on 전환 시 값. **만료일 2027-03-10**(D-161 C1).
    evidence_correlation_related_hosts: int = 0
    # baseline 기간 수(granularity 단위 — 시간 지표면 24 = 직전 24시간).
    evidence_baseline_periods: int = 24
    # 사전수집 MCP 배치 타임아웃(초) — SSE 연결·호출 단위에 같이 적용.
    evidence_prefetch_timeout_seconds: float = 20.0
