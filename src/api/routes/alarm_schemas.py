"""알람 API 요청·응답 스키마 (plans/166 A8 — `src/api/routes/alarm.py`에서 이동).

`alarm.py`가 이 모듈의 이름을 그대로 재노출한다(D-337 ①). 클래스 이름이 OpenAPI 스키마 이름이므로
이름을 바꾸지 않는다.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


def _default_test_db_id() -> str:
    """알람 테스트 요청의 db_id 기본값을 설정에서 읽는다.

    임포트 시점이 아니라 요청마다 평가되므로 `.env` 변경 후 캐시 무효화가 그대로 반영된다.
    """
    from src.config import load_config

    return load_config().alarm.default_test_db_id


class AlarmTestRequest(BaseModel):
    """알람 분석 테스트 요청.

    폴스타 단일행 JSON 템플릿 변수 이름과 동일하게 맞춰 실제 메시지를 그대로 붙여넣을 수 있도록 한다.
    """  # noqa: E501

    # ── 폴스타 알람 필드 (AlarmEvent와 1:1 대응) ──
    db_id: str = Field(
        default_factory=_default_test_db_id,
        description="dbId — 인스턴스 식별자 (생략 시 ALARM_DEFAULT_TEST_DB_ID 설정값)",
    )
    server_name: str = Field(default="", description="${platformName} — 폴스타 등록 서버명")
    hostname: str = Field(default="", description="${hostname} — 호스트네임")
    ip_address: str = Field(default="", description="${ipAddress} — IP 주소")
    resource_ancestry: str = Field(default="", description="${resourceAncestry} — 폴스타 트리 전체 경로")  # noqa: E501
    alarm_id: str = Field(default="TEST-001", description="${alarmId} — 중복 제거 키")
    severity: int = Field(
        default=2,
        ge=0,
        le=3,
        description="${severity} — 0=해소, 1=주의, 2=경고, 3=심각",
    )
    alarm_status: str = Field(
        default="NOT_ACK",
        description=(
            "${alarmStatus} — 폴스타 UI 인지(ACK) 상태 (NOT_ACK 등). "
            "해소 판정에 사용하지 않음 (해소는 severity=0 단독 기준)"
        ),
    )
    resource_type: str = Field(
        default="server.Server",
        description="${resourceType} — 예: server.Server, server.Cpus, network.NMSNode",
    )
    resource_name: str = Field(default="", description="${resourceName} — 자원 이름")
    alarm_name: str = Field(default="", description="${alarmName} — 알람 이름")
    alarm_time: str = Field(
        default="",
        description="${formatAlarmDate('yyyyMMddHHmmss')} — 알람 일시 (yyyyMMddHHmmss 형식, 비어있으면 현재 시각)",  # noqa: E501
    )
    conditions: str = Field(default="", description="${conditions} — 발생/해소 임계 조건 정의")
    condition_log: str = Field(default="", description="${conditionLog} — 이 알람이 울린 실제 값")
    is_clear: bool = Field(default=False, description="True이면 알람 해소 이벤트")

    # ── 테스트 제어 파라미터 ──
    dry_run: bool = Field(
        default=True,
        description=(
            "True: 알람 분석만 수행하고 실제 알림을 발송하지 않음 (발송 메시지 미리보기 포함). "
            "False: send_notification 설정에 따라 실제 발송 여부 결정."
        ),
    )
    send_notification: bool = Field(
        default=False,
        description=(
            "dry_run=False일 때만 유효. "
            "True: channels에 지정된 채널로 실제 알림 발송. "
            "False: 분석 결과만 반환하고 발송 안 함."
        ),
    )
    channels: list[str] | None = Field(
        default=None,
        description=(
            "알림 채널 오버라이드. "
            "null이면 서버 설정(ALARM_NOTIFICATION_CHANNELS_CSV) 사용. "
            "예: [\"workb\"], [\"workb\", \"webhook\"]"
        ),
    )
    push_to_ui: bool = Field(
        default=True,
        description="True이면 분석 결과를 웹 UI 채팅창에 알람 말풍선으로 실시간 표시한다.",
    )
    # ── Plan 47: 이력 패턴 분석 테스트 파라미터 ──
    query_history: bool = Field(
        default=True,
        description=(
            "폴스타 DB 이력 조회 수행 여부. 테스트 엔드포인트 기본값 True — "
            "패턴 근거표를 바로 확인할 수 있다. simulated_history 지정 시 그쪽 우선, "
            "false로 주면 이력 조회를 생략한다."
        ),
    )
    simulated_history: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "[{\"alarm_time\":\"yyyyMMddHHmmss\",\"severity\":3,"
            "\"alarm_status\":\"NOT_ACK\",\"resource_name\":\"...\"}] 형식. "
            "지정 시 DB 조회 대신 이 목록으로 통계 계산 — 이력 시나리오"
            "(주기/급증/첫 발생)를 임의 구성하여 LLM 응답 검증 가능."
        ),
    )
    # ── Plan 47-1: 영향 프로세스 보강 테스트 파라미터 ──
    query_process: bool = Field(
        default=True,
        description=(
            "폴스타 프로세스 API 호출 여부 (hostname 기준). 테스트 엔드포인트 기본값 True. "
            "CPU/메모리 발생 알람 + base_url 매핑이 있어야 실제 조회되며(그 외엔 자동 생략), "
            "simulated_processes 지정 시 그쪽 우선."
        ),
    )
    simulated_processes: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "지정 시 API 호출 없이 이 목록으로 select_top_processes 수행 — "
            "시나리오 검증·마스킹 확인용. "
            "예: [{\"name\":\"java\",\"pid\":12345,\"pmem\":38.0,\"p100cpu\":12.0,"
            "\"args\":\"--password=secret -jar app.jar\"}]"
        ),
    )

    model_config = {"json_schema_extra": {
        "example": {
            "db_id": "polestar_cm_gp",
            "server_name": "svr-infra-001",
            "hostname": "svr-infra-001.internal",
            "ip_address": "10.1.2.3",
            "resource_ancestry": "/Servers/Infrastructure/svr-infra-001/Cpus",
            "alarm_id": "ALARM-20260604-001",
            "severity": 3,
            "alarm_status": "NOT_ACK",
            "resource_type": "server.Cpus",
            "resource_name": "svr-infra-001-CPU",
            "alarm_name": "CPU 사용률 임계 초과",
            "alarm_time": "20260604143520",
            "conditions": "사용률 Threashold [TROUBLE (> 90.0 %), ATTENTION (>80.0 %), CLEAR (< 70.0 %)]",  # noqa: E501
            "condition_log": "사용률 Threashold [95.2 % (> 90.0 %)]",
            "is_clear": False,
            "dry_run": True,
            "send_notification": False,
            "channels": None,
        }
    }}


class AlarmRawTestRequest(BaseModel):
    """폴스타 TCP 소켓 원문 메시지로 알람 분석을 실행하는 요청.

    폴스타에서 실제로 전송되는 단일행 JSON을 그대로 붙여넣어
    소켓 수신 → 파싱 → 분석 전체 파이프라인을 시뮬레이션한다.
    """

    message: str = Field(
        description=(
            "폴스타 TCP 소켓으로 전달된 단일행 JSON 원문. "
            "예: {\"dbId\":\"polestar_cm_gp\",\"serverName\":\"svr-infra-001\",...}"
        ),
    )
    dry_run: bool = Field(default=True, description="True: 발송 없이 분석 결과 + 미리보기만 반환")
    send_notification: bool = Field(default=False, description="dry_run=False일 때만 유효. 실제 채널 발송 여부")  # noqa: E501
    channels: list[str] | None = Field(default=None, description="채널 오버라이드. null이면 서버 설정 사용")  # noqa: E501
    push_to_ui: bool = Field(default=True, description="True이면 분석 결과를 웹 UI에 실시간 표시")
    # ── Plan 47: 이력 패턴 분석 테스트 파라미터 ──
    query_history: bool = Field(
        default=True,
        description="폴스타 DB 이력 조회 수행 여부 (기본 True — 패턴 근거표 표시). simulated_history 지정 시 그쪽 우선",  # noqa: E501
    )
    simulated_history: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "[{\"alarm_time\":\"yyyyMMddHHmmss\",\"severity\":3,"
            "\"alarm_status\":\"NOT_ACK\",\"resource_name\":\"...\"}] 형식. "
            "지정 시 DB 조회 대신 이 목록으로 통계 계산."
        ),
    )
    # ── Plan 47-1: 영향 프로세스 보강 테스트 파라미터 ──
    query_process: bool = Field(
        default=True,
        description="폴스타 프로세스 API 호출 여부 (hostname 기준, 기본 True). CPU/메모리 발생 알람만 실제 조회",  # noqa: E501
    )
    simulated_processes: list[dict[str, Any]] | None = Field(
        default=None,
        description="지정 시 API 호출 없이 이 목록으로 select_top_processes 수행 (마스킹 확인용)",
    )

    model_config = {"json_schema_extra": {
        "example": {
            "message": (
                '{"dbId":"polestar_cm_gp","serverName":"svr-infra-001",'
                '"hostname":"svr-infra-001.internal","ipAddress":"10.1.2.3",'
                '"resourceAncestry":"/Servers/Infrastructure/svr-infra-001/Cpus",'
                '"alarmId":"1234567","severity":3,"alarmStatus":"NOT_ACK",'
                '"resourceType":"server.Cpus","alarmName":"CPU 사용률 임계 초과",'
                '"alarmTime":"20260602143520",'
                '"conditions":"사용률 Threashold [TROUBLE (> 90.0 %), ATTENTION (>80.0 %), CLEAR (< 70.0 %)]",'  # noqa: E501
                '"conditionLog":"사용률 Threashold [86.1 % (> 80.0 %)]"}'
            ),
            "dry_run": True,
            "send_notification": False,
            "channels": None,
            "push_to_ui": True,
        }
    }}


class _WorkbPreview(BaseModel):
    """WorkB 발송 미리보기."""
    title: str
    body: str
    recipients: str = Field(description="수신자 사번 목록 (쉼표 구분)")
    alias: str
    api_url: str | None


class _WebhookPreview(BaseModel):
    """Webhook 발송 미리보기."""
    url: str | None
    payload: dict[str, Any]


class NotificationPreview(BaseModel):
    """채널별 발송 메시지 미리보기."""
    workb: _WorkbPreview | None = None
    webhook: _WebhookPreview | None = None


class AlarmAnalysisOutput(BaseModel):
    """LLM 알람 분석 결과."""
    severity_label: str = Field(description="심각 | 경고 | 주의")
    summary: str = Field(description="LLM 생성 요약 (1~2문장)")
    probable_cause: str = Field(description="추정 원인")
    recommended_action: str = Field(description="권고 조치")
    # ── Plan 47: 패턴 분석 ──
    pattern_type: str | None = Field(
        default="",
        description="첫 발생 | 주기적 | 급증 | 산발적 (이력 분석 불가 시 빈 값)",
    )
    is_routine: bool | None = Field(
        default=None,
        description="True=일상적 반복 알람, None=판단 불가",
    )
    pattern_analysis: str | None = Field(
        default="",
        description="LLM 패턴 해석 (1~3문장)",
    )


class AlarmTestResponse(BaseModel):
    """알람 분석 테스트 응답."""
    alarm_id: str
    severity: int
    severity_label: str
    analysis: AlarmAnalysisOutput | None = Field(
        default=None,
        description="LLM 분석 결과. 분석 실패 시 null.",
    )
    notification_channels: list[str] = Field(description="사용된 채널 목록")
    notification_preview: NotificationPreview = Field(
        description="dry_run=True 또는 send_notification=False일 때 발송될 메시지 미리보기",
    )
    notifications_sent: dict[str, bool] | None = Field(
        default=None,
        description="실제 발송 시 채널별 성공 여부. dry_run=True이면 null.",
    )
    history_stats: dict[str, Any] | None = Field(
        default=None,
        description=(
            "이력 통계 요약 (Plan 47) — query_history=true 또는 simulated_history "
            "지정 시 채워짐. 통계 + 사전 분류(pre_classification) + source 포함."
        ),
    )
    process_snapshot: dict[str, Any] | None = Field(
        default=None,
        description=(
            "영향 프로세스 스냅샷 요약 (Plan 47-1) — query_process=true 또는 "
            "simulated_processes 지정 시 채워짐. 상위 N + 전체 건수 + 스냅샷 시각 + "
            "alarm_kind 포함. args는 마스킹된 값만 노출됨."
        ),
    )
    error: str | None = Field(default=None, description="분석 실패 시 오류 메시지")
    processing_time_ms: float


class AlarmMetricsResponse(BaseModel):
    """알람 노이즈 게이트 운영 지표 (Plan 52 §9 · Phase E3).

    decision_store(JSONL 감사)에서 산출 가능한 지표만 노출한다. ack/해소상관 계측이 없는
    MTTA/MTTR/사건전환율은 null로 두고 unavailable_metrics에 사유를 명시한다(환각 금지).
    """

    window_seconds: int = Field(description="집계 창(초)")
    total: int = Field(description="창 내 결정 총건수")
    by_tier: dict[str, int] = Field(description="티어별 건수")
    page_count: int
    ticket_count: int
    dashboard_count: int
    suppress_count: int
    actionable_ratio: float = Field(description="액션가능 비율 = (page+ticket)/total")
    suppress_ratio: float = Field(description="억제율 = suppress/total")
    last_event_age_seconds: float | None = Field(
        default=None, description="최근 결정 이후 경과(초). 기록 없으면 null"
    )
    meta_alerts: list[dict[str, Any]] = Field(
        description=(
            "억제기 메타경보(high_suppress_ratio/no_events/collection_failed_ratio). "
            "정상이면 빈 배열"
        )
    )
    mtta_seconds: float | None = Field(
        default=None,
        description="평균 확인 시간 = AVG(acked_at-created_at). 트래커 off면 null",
    )
    mttr_seconds: float | None = Field(
        default=None,
        description=(
            "하위호환 — incident_mttr_seconds와 동일 값(paged incident 운영자 MTTR). "
            "트래커 off면 null. auto_recovery_mttr_seconds(self-heal 편향지표)와 혼동 금지"
        ),
    )
    incident_mttr_seconds: float | None = Field(
        default=None,
        description=(
            "paged incident 운영자 MTTR = AVG(resolved_at-created_at) (PG). 트래커 off면 null"
        ),
    )
    auto_recovery_mttr_seconds: float | None = Field(
        default=None,
        description=(
            "자가복구(self-heal) 소요시간 평균 — **sev1..suppress_max 한정·sev3 제외 편향 부분지표** "  # noqa: E501
            "(decision_store JSONL). incident_mttr_seconds와 다름. 기록 없으면 null"
        ),
    )
    incident_conversion_rate: float | None = Field(
        default=None,
        description="사건전환율 = incidents/page_count (window SQL). 트래커 off면 null",
    )
    open_incident_count: int = Field(
        default=0, description="현재 열린(open) incident 수. 트래커 off면 0"
    )
    unavailable_metrics: dict[str, str] = Field(
        description="계산 불가 지표와 사유. 트래커 활성 시 해당 키 제거됨"
    )


class IncidentAckResponse(BaseModel):
    """incident ack 응답 (D-049)."""

    acked: bool = Field(description="ack 성공 여부(이미 ack/resolved면 False)")
    incident_id: int = Field(description="확인 대상 incident id")


class IncidentListResponse(BaseModel):
    """열린 incident 목록 응답 (D-049)."""

    incidents: list[dict[str, Any]] = Field(
        description="열린 incident 목록(트래커 off면 빈 배열)"
    )


class AlarmFeedbackRequest(BaseModel):
    """운영자 알람 피드백 요청 (Plan 52 E4).

    운영자가 알람 카드에서 매긴 라벨(유효/노이즈)을 few-shot 저장소에 적재한다.
    이 피드백은 이후 유사 알람의 LLM 액션가능성 자문(보조 입력)에만 쓰이며, 발송 판단은
    결정적 규칙이 내린다(승격 우선·재현율 우선).
    """

    alarm_name: str = Field(description="${alarmName} — 피드백 대상 알람 이름")
    label: str = Field(description="운영자 라벨 — 'noise'(노이즈) | 'valid'(유효)")
    resource_name: str = Field(default="", description="${resourceName} — 자원 이름(선택)")
    pattern_type: str = Field(default="", description="패턴 분류(첫 발생/주기적/급증/산발적, 선택)")
    server_name: str = Field(default="", description="${platformName} — 서버명(선택)")
    db_id: str = Field(default="", description="dbId — 폴스타 인스턴스 식별자(선택)")
    note: str = Field(default="", description="운영자 메모(선택)")
    severity: int | None = Field(default=None, description="심각도(선택)")
    investigation_id: str = Field(
        default="",
        description="조사 ID(선택 · plans/91 1-4) — '실제 원인은 X' 피드백을 자동 조사 결과와 잇는다. 감사 전용",  # noqa: E501
    )


class AlarmPromptSuggestRequest(BaseModel):
    """알람 카드의 LLM 조회 질의 추천 요청 (Plan 86 · D-192).

    프론트는 **결정적 축 매핑이 0건일 때만** 이 엔드포인트를 부른다 — 추천이 이미 있으면
    네트워크 요청 자체가 나가지 않는다(과금 경로를 미매핑 축으로 좁힌다).
    """

    target: str = Field(description="질의의 주어가 될 대상 서버 표기(등록명 우선)")
    alarm_name: str = Field(default="", description="${alarmName}")
    resource_type: str = Field(default="", description="${resourceType} — 결정적 매핑이 비는 축")
    resource_name: str = Field(default="", description="${resourceName}")
    severity_label: str = Field(default="", description="심각/경고/주의/해소")
    summary: str = Field(default="", description="LLM 요약")
    probable_cause: str = Field(default="", description="추정 원인")
    recommended_action: str = Field(default="", description="권고 조치 — 추천의 주 근거")
    pattern_type: str = Field(default="", description="첫 발생/주기적/급증/산발적")
    pattern_analysis: str = Field(default="", description="패턴 해석 — 추천의 보조 근거")
    db_id: str = Field(default="", description="dbId — 존 RBAC 판정용")


class AlarmPromptSuggestion(BaseModel):
    """추천 질의 1건. 형식은 프론트의 결정적 추천과 동일하다(CAPABILITY-MAP-86)."""

    label: str = Field(description="칩에 표시할 짧은 요지")
    text: str = Field(description="입력창에 들어갈 질의 전문")
    axis: str = Field(default="", description="판정에 쓰인 resource_type")
    source: str = Field(default="llm", description="deterministic | llm")


class AlarmPromptSuggestResponse(BaseModel):
    """추천 응답. 실패는 5xx가 아니라 `suggestion=null`이다 — 카드 렌더를 깨뜨리지 않는다."""

    suggestion: AlarmPromptSuggestion | None = Field(
        default=None, description="추천 질의(생성 실패·파싱 실패 시 null)"
    )


class AlarmFeedbackResponse(BaseModel):
    """운영자 알람 피드백 응답 (Plan 52 E4)."""

    recorded: bool = Field(description="피드백 적재 성공 여부")
    ts: str = Field(
        default="",
        description="적재 레코드의 타임스탬프(ISO8601) — 철회 요청의 target_ts로 쓴다 (Plan 83)",
    )


class AlarmFeedbackRetractRequest(BaseModel):
    """운영자 피드백 철회 요청 (Plan 83 A-5).

    tombstone 한 줄을 append해 원본 라벨을 few-shot 후보에서 제외한다 —
    파일을 재작성하지 않으므로 원본은 감사 추적용으로 남는다.
    """

    target_ts: str = Field(description="철회할 레코드의 ts(적재 응답의 ts 값)")
    alarm_name: str = Field(default="", description="대상 알람 이름(감사 표시용)")
    db_id: str = Field(default="", description="dbId — 존 권한 판정용(선택)")


class AlarmFeedbackRetractResponse(BaseModel):
    """피드백 철회 응답 (Plan 83)."""

    retracted: bool = Field(description="철회 tombstone 적재 성공 여부")


class AlarmFeedbackSummaryResponse(BaseModel):
    """피드백 라벨 집계 응답 (Plan 83 T13).

    같은 알람에 상반된 라벨이 쌓였는지 운영자가 보게 하는 용도다 — 판정 로직은 바꾸지 않는다.
    """

    items: list[dict[str, Any]] = Field(
        description="(알람명, 자원명)별 valid/noise 카운트 + 최근 라벨·작성자·시각"
    )
    episode_counts: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "크로스소스 사건 피드백 건수 {episode_split, demotion_needed} (plans/144 W5) — "
            "items(유효/노이즈)와 섞지 않고 따로 센다"
        ),
    )


class AlarmCapabilitiesResponse(BaseModel):
    """알람 UI 기능 가용성 (Plan 83 T5).

    카드 UI가 버튼 렌더 여부를 결정하기 위한 계약이다. **불리언·정수만** 노출한다 —
    경로·시크릿·엔드포인트 주소는 싣지 않는다(클라이언트가 .env를 추론하지 않게 한다).
    """

    feedback_enabled: bool = Field(
        description="피드백 버튼 가용 여부 = 게이트 AND LLM 액션가능성(503 조건과 동일 식)"
    )
    incident_tracking: bool = Field(description="incident 계측 활성 — 확인(ack) 버튼 전제")
    sse_bridge: bool = Field(description="워커→UI SSE 브리지 활성")
    suppress_stream: bool = Field(description="SUPPRESS 티어 SSE 발행 활성(관리자 전용 표시)")
    suppress_max_severity: int = Field(description="억제 허용 최대 심각도(그 위는 강등 불가)")
    prompt_suggest_enabled: bool = Field(
        default=False, description="알람 조회 질의 LLM 추천 활성 (Plan 86 · 기본 off)"
    )
    investigation_enabled: bool = Field(
        default=False,
        description=(
            "자동 조사 브리핑 조회 가용 = 게이트 AND 조사 트리거 AND 조사 서비스 URL 설정 "
            "(plans/153 W2 · 기본 off)"
        ),
    )
    investigation_followup_timeout_seconds: float = Field(
        default=1200.0,
        description=(
            "조사 후속 조회 전체 타임아웃(초) — 웹이 진행 중 조사를 다시 조회하는 상한 "
            "(NOISE_INVESTIGATION_FOLLOWUP_TIMEOUT_SECONDS · plans/153 W2)"
        ),
    )
