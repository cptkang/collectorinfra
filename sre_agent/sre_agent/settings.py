"""에이전트 설정 — .env 로딩은 pydantic-settings 필드로만 판정한다 (os.getenv 금지)."""

from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 실 조사 LLM 게이트의 스텁 사유 — 사용자·감사에 그대로 노출된다(침묵 금지 · D-123 ⑦ · D-230).
# 키 부재 문구는 종전 그대로다(본체 `tests/test_briefing_contract.py`가 같은 리터럴을 쓴다).
STUB_LLM_KEY_ABSENT = "조사 미실행 — LLM 키 부재(스텁)"
STUB_LLM_DISABLED = "조사 미실행 — 조사 LLM 비활성(INVESTIGATION_LLM_ENABLED=false · 스텁)"

# MCP 도구 호출 1건 상한(초) 기본값 — `evidence_prefetch_timeout_seconds` 필드 기본값과
# `mcp_tool_client` 폴백이 이 상수 하나를 쓴다(plans/187 W22 · D-341 부기 13).
TOOL_CALL_TIMEOUT_S = 60.0


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
    # (하드 실패 금지·Plan 02 §12-④), dispatcher 전체 타임아웃(600s)이 하드 백스톱.
    max_steps: int = 40

    # 폴스타 MCP 접속 설정 (Plan 06 §94 · D-119). mcp_server(Plan 04)가 노출하는
    # SSE 엔드포인트로, DiagnosisAgent(mcp_servers=...)에 등록해 소비한다.
    # (D-119) prometheus_url·prometheus_auth_header는 여기 두지 않는다 —
    # Prometheus 접속 설정은 mcp_server 측(config.toml·서버 .env)으로 일원화한다.
    # 토큰은 SecretStr로 pydantic 필드로만 판정한다(env: POLESTAR_MCP_TOKEN,
    # 미설정 시 None → 무인증 로컬 픽스처 경로).
    polestar_mcp_url: str = "http://localhost:9099/sse"
    polestar_mcp_token: SecretStr | None = None
    # 제니퍼 APM 게이트웨이 MCP(plans/87 J3 · D-274 ⑦ · SPEC-apm-sre-agent §2) — `apm_gateway`의 SSE 엔드포인트
    # (예: http://127.0.0.1:9096/sse). 비면 등록하지 않는다 → `_build_mcp_servers()` 결과가 종전과 같다.
    # 토큰은 `polestar_mcp_token`과 같은 SecretStr(env: APM_MCP_TOKEN · 게이트웨이 APM_GATEWAY_BEARER_TOKEN과 일치).
    apm_mcp_url: str = ""
    apm_mcp_token: SecretStr | None = None

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
    # 조사 서비스 listen 호스트 (env: SERVICE_HOST). 기본 127.0.0.1 = 종전 동작.
    # FastMCP Host 헤더 검사와 uvicorn 바인딩이 같은 값을 쓴다 — 0.0.0.0으로 열면
    # service_bearer_token을 함께 설정할 것.
    service_host: str = "127.0.0.1"
    # 설정 관리 API(`/admin/config` · plans/191 · D-345) 관리 토큰. None(기본) = 관리 라우트
    # 미등록(현행 비트 동일). env는 `SRE_AGENT_ADMIN_TOKEN`이다 — 필드명을 `admin_token`으로 두면
    # 레포 루트 CWD 기동 시 본체 `.env`의 `ADMIN_TOKEN`을 읽는다. validation_alias로도 막히지
    # 않는다(populate_by_name=True면 pydantic-settings가 필드명 env도 읽는다 — 2026-10-21 실측).
    # service_bearer_token과 같은 값이면 등록하지 않는다.
    sre_agent_admin_token: SecretStr | None = None

    # ── 조사 dispatcher 폭주 방지 가드 (Plan 02 §4·§10 — 전부 기본 off/보수값) ──
    # 스칼라 필드이므로 nested Field(default_factory=...)는 불요(중첩 모델 없음).
    # 조사 1건 **전체** 타임아웃(per-call 아님·asyncio.wait_for). HolmesGPT 다단계
    # 조사를 감안해 상향(원본 45s → 300s → 600s · plans/187 W22 · D-341 부기 13 — 내부망
    # 스텝·소견기 타임아웃 결손 대책). collectorinfra MCP 동기 타임아웃(60s)보다
    # 길어 submit/poll 비동기 잡 패턴이 성립한다.
    # 시간 사슬(이 프로세스 안 — 기동 시 `timechain_warnings`가 검사): 도구 1건 60 < 스텝 기본
    # 90 ≤ 대상 확정 스텝 120 ≤ 수집 예산 240 < 조사 전체 600 · 예산 240 + 소견 60 + 종합 90
    # = 390 ≤ 600.
    # 검사 밖(다른 프로세스): 게이트웨이 제니퍼 API 1건 30s(< 도구 1건 60s) · 본체 후속 poll
    # 상한 600s(≥ 조사 전체여야 결과를 받는다).
    investigation_timeout_seconds: int = 600
    # 동일 fingerprint 재조사 최소 간격(초). None이면 TTL dedup off
    # (JobStore active-fingerprint dedup만 동작). 켜면 완료된 조사도 이 간격 내 재조사 억제.
    investigation_dedup_ttl_seconds: float | None = None
    # 동시 조사 상한(세마포어). 보수적 기본 2.
    investigation_max_concurrent: int = 2
    # 동시 상한 슬롯 대기 잡 상한(plans/194 W3 · D-347). 대기 수가 이 값 이상이면 대기 스레드를
    # 만들지 않고 `rejected(queue_full)`로 즉시 확정한다(슬롯을 타임아웃 뒤 버려진 조사 스레드가
    # 붙들고 있으면 `service_overloaded`). None = 무제한(종전 동작).
    # **기본 4는 D-162(기본 off) 예외** — 근거: 대기 스레드가 무제한 누적돼 조사 서비스가 행에
    # 빠졌다(동시 상한 2 · 슬롯 대기 무기한 acquire).
    investigation_max_queued: int | None = 4
    # 타임아웃으로 버린 조사 스레드가 슬롯·L-4 호스트 키를 붙드는 최대 시간(초 · plans/194 W3 ·
    # D-347). 버린 스레드는 협조적 중단 신호를 받고 끝날 때까지 슬롯·키를 붙든다(실부하 계상) —
    # 이 시간을 넘기면 강제 반환하고 감사 `zombie_released`를 남긴다
    # (스레드는 데몬이라 계속 돌 수 있다).
    investigation_abandon_grace_seconds: float = Field(default=120.0, ge=0)
    # 시간당 조사 횟수 상한(전역). 초과 시 신규 조사 거부(`hourly_budget_exceeded`) ·
    # 심층 parallel은 conditional로 강등. None이면 예산 가드 off(.env로는 못 준다 — 큰 값으로
    # 늘린다). **기본 20은 D-162(기본 off) 예외 — 사용자 확정 2026-10-26 · D-347 ②**(종전 None)
    # — 근거: 수동 조사(사용자별 시간당 3건)가 늘어도 조사 서비스 LLM 비용·부하에 전역 상한을 둔다.
    investigation_hourly_budget: int | None = 20
    # 조사 감사 JSONL 경로(plans/154 W1 · §4.7 — 벤치 감사를 운영 감사와 분리).
    # None이면 기본 `sre_agent/.data/investigation_audit.jsonl`(비트 동일).
    # 값이 있으면 JobStore·dispatcher가 그 파일에만 쓴다.
    investigation_audit_path: str | None = None

    @field_validator("investigation_audit_path")
    @classmethod
    def _blank_audit_path_is_default(cls, value: str | None) -> str | None:
        """빈 값(공백 포함)은 None(기본 경로)으로 본다.

        `INVESTIGATION_AUDIT_PATH=`처럼 비워 두면 `Path("")`가 현재 디렉토리가 되어 감사 쓰기가
        실패하고 기동 시 `recover_on_start`가 `IsADirectoryError`로 죽는다.
        """
        if value is not None and not value.strip():
            return None
        return value
    # ── 비선두 system 메시지 강등 가드 (D-213 후속) — **기본 on(명시적 예외)** ──
    # Qwen 채팅 템플릿(vLLM)은 system을 맨 앞에만 허용하는데, holmes 컨텍스트 압축이
    # 압축 결과 말미에 role="system" 안내를 붙여 `System message must be at the beginning.`
    # (BadRequestError)으로 조사가 전건 실패한다(폐쇄망 실측 2026-09-11 — 압축이 발동하는
    # 모든 조사에서 재현). 가드는 0번이 아닌 system의 role만 user로 바꾸고 content는 보존한다.
    # **기본 off가 아닌 이유**(plans/80 §5.4-③ 예외): 교정할 것이 없으면 원본 객체를 그대로
    # 통과시키는 no-op이라 정상 경로가 비트 동일하고, off는 곧 "계속 실패"를 뜻한다.
    # **만료일 2027-03-11**(D-161 C1) — holmes 수정·모델 교체 시 유지/삭제를 그때 판정한다.
    system_message_position_fix: bool = True
    # ── Gemma 도구 호출 마크업 클라이언트 측 파싱 (plans/188 C8) — **기본 on(명시적 예외)** ──
    # 사내 vLLM(Gemma 4 계열)의 `--tool-call-parser`가 모델과 맞지 않으면 도구 호출이 `tool_calls`가
    # 아니라 content 본문(`<|tool_call>call:이름{…}<tool_call|>`)으로 돌아와 조사가 1턴에서 끝난다
    # (폐쇄망 실측 2026-10-19). 가드는 그 블록을 해석해 OpenAI 형식 tool_calls 사본으로 바꾼다.
    # **기본 off가 아닌 이유**(plans/80 §5.4-③ 예외): 마크업이 없으면 원본 객체를 그대로 통과시키는
    # no-op이라 정상 경로가 비트 동일하고, off는 곧 "파서 불일치 환경에서 조사 전건 실패"를 뜻한다.
    # 해석 실패면 원본을 그대로 둬 W4 방어(미결 종결)가 작동한다.
    # **만료일 2027-04-19**(D-161 C1) — vLLM 파서 교정·모델 교체 시 유지/삭제를 그때 판정한다.
    tool_call_markup_parse_enabled: bool = True
    # ── bash 쓰기 형태 거부 가드 (D-235) — **기본 on(명시적 예외)** ──
    # holmes bash 허용목록은 prefix 매칭이라 `uptime`이 허용이면 `uptime > /tmp/x`도 통과한다(실측).
    # 설정으로 막을 수 없어(`allow`·`deny`·`builtin_allowlist`뿐 · deny도 prefix) 검증 경계에서 거부한다.
    # **기본 off가 아닌 이유**(plans/80 §5.4-③ 예외): 쓰기 형태가 아니면 원본 판정 객체를 그대로 돌려주는
    # no-op이라 읽기 조사 경로가 비트 동일하고, off는 곧 "읽기 전용 프로파일이 파일을 쓸 수 있다"를 뜻한다.
    # **만료일 2027-03-21**(D-161 ①) — holmes가 리다이렉션을 자체 판정하게 되면 유지/삭제를 그때 판정한다.
    # 설치는 holmes 클래스 경계 교체라 **프로세스 전역·1회**다(같은 프로세스에서 한 에이전트라도 켜면 유지).
    bash_write_guard_enabled: bool = True
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
    # OpenMetrics 서술 노트(plans/92 O3 · R-12). 기본 off = 조사 지침 문자열 비트 동일
    # (프롬프트 접두·KV 캐시 불변). 켜면 `build_guidance`가 플레이북 뒤·자유 지침 앞에
    # `OPENMETRICS_NOTE`를 붙인다(`om_*`는 현재 상태로만 서술).
    # 운영 메모: mcp_server `EXPOSE_OPENMETRICS_TOOLS=true` 배치와 **함께** 켠다 — 도구가 없는데
    # 노트만 켜면 없는 도구를 가리킨다. **만료일 2027-03-22**(D-161 ① · 6개월) — OM 배치 운영
    # 실측 후 상시화 또는 삭제를 판정한다.
    openmetrics_guidance_enabled: bool = False
    # Prometheus 호스트 앵커 지침(plans/189 W5). 기본 off = 조사 지침 문자열 비트 동일.
    # 켜면 `build_guidance`가 OpenMetrics 노트 자리 뒤·APM 노트 앞에 `PROMETHEUS_ANCHOR_NOTE`
    # (hostname 의미 · source=알람 dbId · 0건 reason 처리 — 「정상」 서술 금지)를 붙인다.
    # 운영 메모: mcp_server `PROMETHEUS_ANCHOR_MODE=polestar_host` 배치와 **함께** 켠다 — nodename
    # 모드에서 OM 확장 `prom_metric_instant`의 `source`는 메트릭 소스(auto|prometheus|exporter)라
    # dbId를 넘기면 안 된다. **만료일 2027-04-20**(D-161 ① · 6개월).
    prometheus_anchor_guidance_enabled: bool = False
    # APM 조사 지침(plans/87 J3 · SPEC-apm-sre-agent §3.3). 기본 off = 조사 지침·브리핑 비트 동일.
    # 켜면 사건창 앵커에 apm_* 구간 도구 4종 · APM 조사 순서·현재값·폴백 노트 · (apm 사건이면) APM 플레이북 ·
    # 브리핑 소스 라벨·APM 한계 · 정체 가드(같은 도구·같은 인자 3회 → 미결 — 사후 판정)가 붙는다.
    # 운영 메모: `APM_MCP_URL` 배치와 함께 켠다 — URL이 비면 지침·브리핑에 "APM 미가용" 폴백 사유가 실린다.
    # **만료일 2027-03-29**(D-161 ① · 6개월) — J0-O 운영 실측 후 상시화 또는 삭제를 판정한다.
    apm_guidance_enabled: bool = False
    # APM 판정 승격(plans/87 J3 · D-195 ② · D-274 ⑤). 기본 off = 판정 불변. 켜면 도구 원시 출력 중
    # source_kind=apm_api JSON의 was_signals를 Signal로 옮겨 escalate-only 판정에 합친다(WAS 규칙 재구현 없음).
    # severity_judge_enabled와 함께 켠다 — judge가 꺼져 있으면 합칠 판정이 없다.
    # **만료일 2027-03-29**(D-161 ① · 6개월).
    apm_signatures_enabled: bool = False
    # 크로스소스 조사(plans/153 W5 · §4.6·§4.7 · D-325 ⑨). 기본 off = 페이로드
    # `meta.episode`를 무시해 질문·지침·브리핑·감사가 비트 동일.
    # 켜면 `meta.episode`(noise_gate 사건 추적기 스냅샷 — 여기서 사건을 재구성하지 않는다 ·
    # G-5 (a))에 인프라·애플리케이션 멤버가 모두 있을 때 크로스소스 질문 · OS+APM 플레이북
    # 병기 · 사건 멤버 타임라인 · 규칙 가설 · 브리핑 `cross_source` 블록 · 감사 키
    # (episode_id·kind·cross_source_links)가 붙는다. APM 트리거 사전수집은
    # `meta.episode.infra_db_ids[0]`를 폴스타 소스로 쓴다(없으면 폴스타 축 생략 —
    # 이 부분은 플래그 무관 R-4).
    # 운영 메모: `APM_MCP_URL`과 함께 켠다 — 비면 브리핑 한계에
    # "애플리케이션(APM) 도구 미연결"이 강제로 실린다.
    # **만료일 2027-04-12**(D-161 ① · 6개월) — 사건 결합 운영 실측(plans/144 W6 shadow) 후
    # 상시화 또는 삭제를 판정한다.
    cross_source_investigation_enabled: bool = False
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
    # 사전수집 MCP 배치 타임아웃(초) — SSE 연결·호출 단위에 같이 적용. 플레이북 스텝의 도구 1건
    # 상한이기도 하다(같은 배치 콜러블). 20 → 60(plans/187 W22 · D-341 부기 13): 게이트웨이 제니퍼
    # API 1건 상한이 30s로 오르고 도구 하나가 API를 2회 이상 순차로 부를 수 있어 그보다 충분히 크게.
    # 스텝 기본 상한(90s)보다 작아야 한다.
    evidence_prefetch_timeout_seconds: float = TOOL_CALL_TIMEOUT_S

    # ── 알람 유형별 조사 플레이북(plans/187 W4 · D-341 ①⑮~⑲) ──
    # 기본 off = 현행 비트 동일. off면 아래 플레이북 설정(심층 모드·디렉토리·예산)을
    # 코드가 읽지 않는다. on이면 알람 조사 잡(kind=alarm)이 플레이북 엔진(결정적 수집 →
    # 판독 → 가설 → 단계 소견 → 종합)으로 돌고, HolmesGPT는 `sre_deep_dive_mode`에 따라
    # 심층 조사로만 돈다. 사전수집(evidence_prefetch)은 생략하고 `job.correlation`은
    # 플레이북 S4·S5 결과로 채운다(G-24). 진단 잡(sre_diagnose)은 그대로다.
    sre_playbook_enabled: bool = False
    # 심층 조사 운영 모드(G-22) — off(플레이북만) · conditional(1순위 판단 보류 ·
    # 결손 ≥2 · fallback일 때만) · parallel(한 잡 안 두 엔진 동시 · HolmesGPT엔 정적
    # 지시만). 도입 초기 parallel(§5.0.1). 유형별 YAML `deep_dive.mode`가 우선한다.
    # fallback 플레이북은 모드와 무관하게 심층을 항상 돈다.
    sre_deep_dive_mode: Literal["off", "conditional", "parallel"] = "parallel"
    # 플레이북 YAML 디렉토리. None이면 패키지 기본(`sre_agent/config/playbooks/`).
    # 기동 시 1회 읽는다.
    sre_playbook_dir: str | None = None
    # 플레이북 예산(초) — 수집 상한(§8 수용 기준 6). 조사 전체 타임아웃(600s) 안에서
    # 쓰고, 수집 뒤 남은 시간을 소견기 타임아웃에 나눈다. 120 → 240(plans/187 W22 · D-341 부기 13):
    # 대상 확정 스텝 120s + 뒤 단계(스텝 90s)가 한 번에 들어가고 결손 스텝 재시도 여유를 남긴다.
    sre_playbook_budget_s: float = Field(default=240.0, gt=0)
    # ── 플레이북 APM 호출 지연 교정(plans/190 · D-344) ──
    # X-View 사전 확인 상한(W2) — 기간 모드 `apm_app_health` 호출에 게이트웨이 옵트인 인자
    # `xview_confirm_over`로 싣는다. 예상 X-View 호출(1분 조각 × 소스·도메인)이 이 수를 넘으면
    # 게이트웨이는 데이터 호출 없이 `status: confirm_required`를 돌려주고, 플레이북은 시 단위 통계
    # (`apm_period_compare`)로 대체한 뒤 `[한계]`로 노출한다(침묵 강등 금지).
    # **기본 10 — 기본 off 원칙의 예외**: off면 폐쇄망에서 보고된 정체(도구 1건 8~11분 · 2026-10-20)가
    # 그대로 남는다(D-296 부기 ⓓ 「플래그 없음」 선례). None 또는 0 이하 = 인자 미전송(종전 비트 동일).
    apm_xview_confirm_over: int | None = 10
    # APM 게이트웨이 도구 호출 상한(초 · W3). None = 종전대로 `evidence_prefetch_timeout_seconds`
    # (비트 동일). 플레이북 수집 예산 `sre_playbook_budget_s` 안에서 쓰인다.
    apm_tool_timeout_seconds: float | None = None
    # 「스텝 타임아웃」으로 결손된 스텝을 수집 단계 뒤 남은 예산 안에서 1회씩 다시 돈다
    # (plans/187 W22 · D-341 부기 13 · 결정적 · LLM 0). 잡 도구 캐시를 그대로 써 이미 받은 호출은
    # 다시 부르지 않는다. 대상 확정 스텝이 재시도로 성공해도 범위는 바꾸지 않는다
    # (뒤 단계는 이미 실행됐다).
    # **기본 on은 사용자 대책 요청(2026-10-23)에 따른 예외(D-161 기본 off 원칙의 예외)** — 근거:
    # 내부망 S0·S1 스텝 타임아웃 결손이 반복됐다. off면 종전과 비트 동일.
    # 한계: 첫 시도에서 오류로 끝난 호출(도구 1건 상한 60s 초과 포함)은 잡 캐시에 오류로 남아
    # 재시도가 다시 부르지 않는다 — 재시도가 살리는 것은 스텝 상한에 걸려 아직 못 낸·기다리던
    # 호출이다.
    sre_playbook_retry_timed_out_steps: bool = True
    # 배치 콜러블이 한 MCP 세션 안에서 동시에 내는 도구 호출 상한
    # (plans/187 W21 P1 · D-341 부기 12). 1 = 종전 순차.
    # **기본 4는 사용자 확정 예외(D-161 기본 off 원칙의 예외)** — 근거: was.latency S0가
    # 최대 13건을 순차로 내고 호출당 상한 20s(W22에서 60s)라 스텝 상한을 넘어 결손이 났다
    # (내부망 2026-10-22).
    # 게이트웨이 부하는 4로 묶는다. 사전수집(evidence_prefetch)도 같은 배치 콜러블을 써
    # 함께 적용된다. 실효 동시 호출 = 스텝 워커 4 × 이 값 × 동시 조사 수 — 그래서 상한은 8이다.
    sre_tool_batch_concurrency: int = Field(default=4, ge=1, le=8)
    # 프로세스 전역 도구 동시 호출 상한(축별 — 폴스타 MCP · APM 각각 · plans/194 W3 · D-347).
    # 배치 안 상한(`sre_tool_batch_concurrency`)은 그대로 두고, 그 위에 모든 배치·조사가 나눠 쓰는
    # 전역 세마포어를 건다. 자리를 도구 1건 상한 안에 못 얻으면 그 호출만 결손
    # (`tool_concurrency_wait_timeout`). None = 전역 상한 없음(종전 동작).
    # **기본 8은 D-162(기본 off) 예외** — 근거: 실효 동시 호출이 스텝 워커 4 × 배치 4 × 동시
    # 조사 수로 곱해져 조회 대상(게이트웨이·mcp_server)에 상한이 없었다.
    sre_tool_global_concurrency: int | None = Field(default=8, ge=1)
    # 단계 소견 LLM 1건 타임아웃(초 · plans/187 W21 P5 · D-341 부기 12). 종전 20s 코드 고정에서
    # 내부망 「소견기 타임아웃 20s」가 반복됐다. 45 → 60(plans/187 W22 · D-341 부기 13):
    # 조사 전체 600s − 수집 예산 240s = 최소 360s가 남고, 소견 동시 4 × 약 3회차 × 60s = 180s
    # + 종합 90s = 270s가 그 안에 든다. 남은 시간이 적으면 dispatcher의 기존 축소 로직이 줄인다.
    sre_narrator_step_timeout_s: float = Field(default=60.0, gt=0)
    # 종합(S9) LLM 1건 타임아웃(초 · plans/187 W22 · D-341 부기 13). 종전 30s 코드 고정
    # (`NarratorLimits.synthesis_timeout_s`)을 설정으로 올렸다 — 종합 입력이 단계 소견 전부라
    # 단계 소견(60s)보다 길다. 수집 예산 + 소견 + 종합 = 240 + 60 + 90 = 390 ≤ 조사 전체 600.
    sre_narrator_synthesis_timeout_s: float = Field(default=90.0, gt=0)
    # APM 소스 ID → 폴스타 db_id 매핑(plans/187 W21 P6 · D-341 부기 12).
    # 예 {"cm_prd":"polestar_cm_gp"}. 페이로드에 `meta.episode.infra_db_ids`가 없을 때만
    # 대체로 쓰고, 그 사실을 근거 문장으로 남긴다.
    # 기본 빈 매핑 = 현행 동일(폴스타 축 미확정). `.env`에는 JSON으로 쓴다(인라인 주석 금지).
    sre_playbook_polestar_source_map: dict[str, str] = Field(default_factory=dict)

    # ── 조사 잡 실행 기록(plans/187 W13 · §5.6.3 ⓑ · D-341 부기 4 ㉘) ──
    # 기본 off = 기록 자체를 하지 않고 `sre_get_investigation(activity_since=…)`를 무시해 응답이
    # 비트 동일. 켜면 접수된 잡마다 메모리 링 버퍼(이벤트 300 · 발췌·AI 메모 1건 2KB · 잡당 256KB)에
    # 동시 상한 대기·스텝·도구 호출·소견 LLM·HolmesGPT 스트림(도구·판단 회차·AI 메모)을 남기고,
    # `activity_since ≥ 0` 조회에 `activity`·`phase`·`queue_position`·`elapsed_s`를 더한다.
    # 한 줄·발췌·메모는 버퍼에 넣기 전에 비밀값을 가린다(G-36). 잡 TTL(1시간)과 함께 사라진다.
    sre_activity_log_enabled: bool = False


def timechain_warnings(
    settings: AgentSettings, *, step_default_s: float, max_step_timeout_s: float | None
) -> list[str]:
    """조사 시간 사슬의 어긋난 관계를 문장으로 모은다(plans/187 W22 · D-341 부기 13 · 순수 함수).

    관계: 도구 1건 < 스텝 기본 · 스텝 기본 ≤ 수집 예산 · 플레이북 스텝 `timeout_s` 최대값
    ≤ 수집 예산 · 수집 예산 < 조사 전체 · 수집 예산 + 소견 단계 + 종합 ≤ 조사 전체.
    정합이면 빈 목록이다.
    게이트웨이(제니퍼 API 1건)·본체(후속 poll) 상한은 다른 프로세스라 검사하지 않는다.
    `step_default_s`는 실행기 기본값, `max_step_timeout_s`는 적재된 플레이북 스텝 `timeout_s`의
    최대값(없으면 None)이다 — 둘 다 응용 계층 값이라 호출자가 넘긴다.
    """
    tool = float(settings.evidence_prefetch_timeout_seconds)
    budget = float(settings.sre_playbook_budget_s)
    total = float(settings.investigation_timeout_seconds)
    narr = float(settings.sre_narrator_step_timeout_s)
    synth = float(settings.sre_narrator_synthesis_timeout_s)
    out: list[str] = []
    if not tool < step_default_s:
        out.append(f"도구 1건 {tool:g}s ≥ 스텝 기본 {step_default_s:g}s")
    if not step_default_s <= budget:
        out.append(f"스텝 기본 {step_default_s:g}s > 수집 예산 {budget:g}s")
    if max_step_timeout_s is not None and not max_step_timeout_s <= budget:
        out.append(f"플레이북 스텝 timeout_s 최대 {max_step_timeout_s:g}s > 수집 예산 {budget:g}s")
    if not budget < total:
        out.append(f"수집 예산 {budget:g}s ≥ 조사 전체 {total:g}s")
    if not budget + narr + synth <= total:
        out.append(
            f"수집 예산 {budget:g}s + 소견 {narr:g}s + 종합 {synth:g}s = "
            f"{budget + narr + synth:g}s > 조사 전체 {total:g}s"
        )
    return out
