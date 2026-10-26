"""알람·노이즈 게이트 설정 — 알람 조회·Workb 통보·노이즈 캔슬링 게이트.

plans/166 W2 I4 — 옛 `src/config.py`에서 클래스를 그대로 옮겼다. 공개 경로는 `src.config`
그대로다(패키지 `__init__`이 이 모듈의 클래스를 재노출한다 · D-337 ①).
"""

# 옮긴 주석·문자열의 긴 줄은 이동 전부터 있던 것이다 — 바이트 그대로 두려고 파일 단위로 끈다.
# ruff: noqa: E501

from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings


class AlarmConfig(BaseSettings):
    """에이전트 서버의 알람 분석·발송 설정.

    소켓 수신 설정은 noise_gate/alarm_server/config.py에서 관리.
    에이전트 서버는 Redis Stream 소비 및 알림 발송만 담당.
    """

    enabled: bool = False
    redis_stream_key: str = "alarm:raw"
    redis_consumer_group: str = "alarm-workers"
    min_severity: int = 2                 # 처리할 최소 심각도 (0=해소, 1=주의, 2=경고, 3=심각)
    dedup_ttl_seconds: int = 300          # 중복 알람 억제 TTL (초)
    # ── D-184: 처리 실패 메시지 dead-letter ──
    # 워커가 파싱·처리 예외로 ACK 폐기하기 전에 원문+사유를 별도 스트림에 보관한다.
    # (2026-08-25 폐쇄망: 폴스타 severity 한글 라벨 → int() 실패 → 전량 침묵 폐기 재발 방지)
    # 상시 동작 — `_enabled` 플래그는 두지 않는다(D-162 §6 플래그 부채 원칙, 2026-08-27 정리).
    dead_letter_stream_key: str = "alarm:dead"
    dead_letter_maxlen: int = 1000        # XADD MAXLEN ~ (근사 상한)
    # ── D-188: 서버 식별 역조회(hostname → 폴스타 등록 서버명·IP) ──
    # 템플릿이 platformName/ipAddress를 지원하지 않아(EL1008E) cmm_resource를 hostname으로 역조회한다.
    # 조회 결과로 server_name(템플릿이 hostname을 준 경우만)·ip_address(빈 경우만)를 승격한다.
    # 상시 동작 — `_enabled` 플래그는 두지 않는다(실패·타임아웃은 graceful로 충분).
    server_identity_timeout_seconds: float = 3.0
    server_identity_cache_ttl_seconds: int = 3600   # Redis 캐시 TTL(초, 0=미캐시)
    # 현재 지원 채널: workb만 사용 가능.
    # 추후 "slack,workb" 등 복수 지정 가능하도록 CSV 구조를 유지한다.
    notification_channels_csv: str = "workb"
    # Generic Webhook 채널 설정 (비어있으면 webhook 채널 자동 무시)
    webhook_url: str = ""
    webhook_timeout_seconds: int = 10
    # 알람 분석 테스트 API(POST /alarm/test)에서 db_id를 생략했을 때 쓸 기본 인스턴스
    # 식별자. 라우트에 상수로 박혀 있던 값을 설정으로 옮긴 것이다(Plan 67 Phase 0 ⑫).
    default_test_db_id: str = "polestar_b0"

    # ── Plan 47: 폴스타 DB 이력 기반 패턴 분석 ──
    history_enabled: bool = True              # 이력 조회 + 패턴 분석 활성화
    history_lookback_days: int = 90           # 패턴 분석 조회 기간 — 일·주·월 주기 3회 관측 가능한 최소 기간 (Plan 47 §3.2)
                                              # 월 주기 작업이 많은 환경은 180까지 확장 가능
    history_max_rows: int = 2000              # 조회 행 수 상한 (일 10건 빈발 알람 × 90일 = 900건 수용, truncated 플래그 연동)
    history_cache_ttl_seconds: int = 300      # 조회 결과 단기 캐시 TTL (0이면 캐시 비활성)
    enrich_timeout_seconds: int = 5           # enricher 전체 타임아웃
    burst_threshold_24h: int = 5              # 급증 판정 24h 최소 건수

    # ── Plan 47-1: 영향 프로세스 보강 (CPU/메모리 알람) ──
    process_enrich_enabled: bool = True
    # db_id=base_url 매핑 (CSV — .env JSON 회피, notification_channels_csv 패턴과 동일).
    # 내부망 시스템이라 scheme는 http:// (TLS 없음). 인증 불필요 (Plan 47-1 §2/§9).
    # 운영 호스트는 코드에 두지 않는다 — `.env`의 ALARM_PROCESS_API_BASE_URLS_CSV로만 주입한다
    # (폴스타 편향 검토 §2-4 / Plan 67 Phase 0 ⑪). 미설정이면 base_url 조회가 None이라
    # 프로세스 보강이 대상 db_id에서 비활성된다.
    process_api_base_urls_csv: str = ""
    process_api_timeout_seconds: int = 3      # 추가 외부 호출 — 이력보다 짧게
    process_top_n: int = 5                     # 표시할 상위 프로세스 수
    # 인증·TLS 설정 없음 — 내부 시스템 http, 비로그인 조회 (Plan 47-1 §9)

    # Prometheus(node_exporter 메트릭) 조회 — 프로세스 API와 동일 db_id→base_url CSV 패턴.
    # node_exporter는 직접 조회하지 않고 앞단 Prometheus HTTP Query API를 read-only GET (D-003).
    # Plan 60 E3 baseline / Plan 64 §4.5 / docs/aiops_benchmark/l3_host_collection_mechanism.md.
    # ★ 예비 코드(2026-09-10 사용자 확정 · plans/91 1-10): 아래 3키를 읽는 프로덕션 코드가 **없다**(호출부 0건 —
    #   noise_gate/infrastructure/prometheus_client.py는 테스트만 생성). 값을 채워도 동작이 바뀌지 않는다.
    #   **판정 기한 2027-02-20**(D-161 C1): 운영 Prometheus(plans/91 1-12) 해소 시 docs/27 §3.3.3 배선, 미해소 시 §3.3.4 삭제.
    prometheus_enabled: bool = False           # 옵트인 — 비활성 시 조회 경로 미진입(회귀 0) · 현재 소비처 0(예비)
    prometheus_base_urls_csv: str = ""         # "db_id=http://prom:9090,..." (미설정 시 전부 None)
    prometheus_timeout_seconds: int = 3        # 추가 외부 호출 — 이력보다 짧게(프로세스 API와 동일)

    model_config = {"env_prefix": "ALARM_", "env_file": ".env", "extra": "ignore"}

    def get_notification_channels(self) -> list[str]:
        """활성 알림 채널 목록을 반환한다."""
        return [c.strip() for c in self.notification_channels_csv.split(",") if c.strip()]

    def get_process_api_base_url(self, db_id: str) -> str | None:
        """db_id에 매핑된 프로세스 API base_url을 반환한다 (없으면 None — Plan 47-1).

        매핑 형식: "db_id1=http://host1,db_id2=http://host2" (CSV, '=' 구분).
        잘못된 항목(= 미포함)은 무시한다.
        """
        for pair in self.process_api_base_urls_csv.split(","):
            pair = pair.strip()
            if not pair or "=" not in pair:
                continue
            key, _, url = pair.partition("=")
            if key.strip() == db_id:
                return url.strip() or None
        return None

    def get_prometheus_base_url(self, db_id: str) -> str | None:
        """db_id(존)에 매핑된 Prometheus base_url을 반환한다 (없으면 None).

        매핑 형식: "db_id1=http://prom1:9090,db_id2=http://prom2:9090" (CSV, '=' 구분).
        get_process_api_base_url와 동일 규칙 — 존별 Prometheus 분리를 지원한다.
        """
        for pair in self.prometheus_base_urls_csv.split(","):
            pair = pair.strip()
            if not pair or "=" not in pair:
                continue
            key, _, url = pair.partition("=")
            if key.strip() == db_id:
                return url.strip() or None
        return None


class WorkbConfig(BaseSettings):
    """KB One 클라우드 포탈 worKB(사내메신저) 쪽지 발송 설정.

    민감 정보(bearer_token)는 .encenv 파일에 저장 권장.
    """

    base_url: str = ""                    # 예: http://kbone-portal.internal:28080
    bearer_token: str = ""               # Bearer 인증 토큰 (.encenv 권장)
    system_div: str = ""                 # 시스템 구분자 (worKB 관리자로부터 발급)
    send_id: str = ""                    # 발송자 사번
    user_ids_csv: str = ""              # 기본 수신자 사번 목록 (쉼표 구분)
    alias: str = "[인프라알람]"          # 쪽지 제목 접두어 (실제 제목 = alias + " " + msgTitle)
    # 심각도별 수신자 오버라이드 (비어있으면 user_ids_csv 공통 사용)
    critical_user_ids_csv: str = ""      # 심각도 3 전용 수신자
    warning_user_ids_csv: str = ""       # 심각도 2 전용 수신자
    timeout_seconds: int = 10

    model_config = {"env_prefix": "WORKB_", "env_file": [".env", ".encenv"], "extra": "ignore"}

    def get_user_ids(self, severity: int) -> str:
        """심각도에 맞는 수신자 목록을 반환한다.

        Args:
            severity: 알람 심각도 (1=주의, 2=경고, 3=심각)

        Returns:
            쉼표 구분 수신자 사번 문자열
        """
        if severity == 3 and self.critical_user_ids_csv:
            return self.critical_user_ids_csv
        if severity == 2 and self.warning_user_ids_csv:
            return self.warning_user_ids_csv
        return self.user_ids_csv


class NoiseGateConfig(BaseSettings):
    """알람 노이즈 캔슬링 게이트 설정 (Plan 52 §8.5).

    AlarmConfig/WorkbConfig와 동일하게 AppConfig의 **형제 필드**로 분리한다.
    AlarmConfig 안에 중첩하지 않는다(이중 중첩 시 env 로딩이 복잡해지고 충돌 위험).
    접근 경로: cfg.noise_gate.* (env_prefix="NOISE_").

    전 기능 기본 비활성(enable_noise_gate=False) — 활성 전에는 기존 알람 발송 경로가
    바이트 단위로 무변경이어야 한다(회귀 0). E1에서 미사용인 필드(E2/E3)도 향후
    자리예약으로 정의해 둔다.
    """

    enable_noise_gate: bool = False           # 전체 게이트 옵트인 (E1)
    suppress_max_severity: int = 2            # 억제 허용 상한 (심각도 3은 항상 PAGE)
    importance_value_map_csv: str = ""        # IMPORTANCE_ID 코드→라벨 매핑 CSV ("1=낮음,2=보통,3=높음"), 빈값=전부 보통
    self_heal_window_seconds: int = 300       # 자가복구 상관 창 (E1)
    debounce_seconds: int = 0                 # (E2) 상태 안정화 (0=미사용)
    flap_high_threshold: float = 20.0         # (E2) 플래핑 시작 % (Nagios 기본)
    flap_low_threshold: float = 5.0           # (E2) 플래핑 종료 %
    flapping_enabled: bool = False            # (E2) 플래핑 억제 on/off (상태 진동 보류)
    dependency_suppression: bool = False      # (E2) 의존성 억제 on/off
    # ── Plan 60 E4: 토폴로지 의존성 그래프 다홉 연쇄 억제 (D-080) ──
    # dependency_suppression과 AND — 다홉은 의존성 억제의 상위 모드(둘 다 True여야 다홉 조회 유발).
    # off(기본)면 게이트·enricher·repo 경로 비트 동일(회귀 0). gp/yd(PostgreSQL)만 지원, b0(DB2)는 1홉 폴백.
    multi_hop_cascade_enabled: bool = False   # (E4) 다홉 연쇄 억제 on/off
    topology_cache_ttl_seconds: int = 86400   # (E4) 정적 엣지 그래프 캐시 TTL(24h — 변경 드묾)
    topology_max_hops: int = 5                # (E4) BFS 홉 상한(순환 방어 이중 안전 겸 비용 가드)
    inhibition_enabled: bool = False          # (E2) 인히비션 on/off (상위 심각도 음소거)
    inhibition_window_seconds: int = 300      # (E2) 상위 심각도 활성 간주 창
    storm_grouping_enabled: bool = False      # (E2) 스톰 그룹핑 on/off (동일 서버 다발 억제)
    storm_window_seconds: int = 60            # (E2) 스톰 사건창 (초)
    storm_threshold: int = 5                  # (E2) 창 내 이 수 초과 시 스톰(대표 외 억제)
    # ── Plan 60 E2: 크로스-호스트 이벤트 상관 (D-078) — db_id(존) 경계 내 상관(§8 B-6) ──
    # 존 간(gp↔yd) 상관은 금지(공통 원인 실증 후 확장). storm(동일 서버)과 독립 병존 플래그.
    # off(기본)면 detection 미수행 → _detect_storm 비트 동일·게이트 무변경(회귀 0).
    cross_host_correlation_enabled: bool = False  # (E2) 크로스-호스트 상관 on/off
    correlation_sim_threshold: float = 0.5    # (E2) Jaccard 필드유사도 임계(이상이면 동일 클러스터)
    correlation_window_seconds: int = 120     # (E2) 상관 사건창(초) — 밖 클러스터 만료 sweep
    correlation_min_cluster_size: int = 2     # (E2) 대표 포함 이 수 이상 멤버부터 억제 개시
    correlation_buffer_max: int = 1000        # (E2) 스코프별 활성 클러스터 상한(메모리 가드)
    correlation_field_weights_csv: str = ""   # (E2) 필드 가중(예약 — 위상 가중은 E4 이후 단계적)
    # ── Plan 60 E2 위상 가중(E4 토폴로지 인접성) — 옵트인 하위 플래그 ──
    # cross_host_correlation_enabled의 하위 정밀화. off(기본)면 adjacent 미주입·topo_weight=0.0
    # → match_cluster 점수가 현행 필드 Jaccard와 **비트동일**(회귀 0). on이면 워커가 event.db_id로
    # 그래프를 로드해 인접 클러스터에 보너스 주입(존 내 매칭 정밀화). correlation.py는 topology 미의존.
    correlation_topology_weight_enabled: bool = False  # (E2) 위상 가중 on/off
    correlation_topology_weight: float = 0.2  # (E2) 인접 토폴로지 노드 유사도 보너스 크기
    business_hours_csv: str = ""              # (E3) 업무시간 (시간대 강등용)
    repeat_interval_seconds: int = 14400      # 재발생 재통보 간격 (4h, E1 dedup TTL)
    sev3_repeat_interval_seconds: int = 14400  # (§6.1) 심각도3 재통보 간격(기본=공통, 운영서 단축)
    # (plans/144 Q-4 · D-320) 같은 지문이 재통보 간격 안에 **직전 통보보다 높은 심각도**로 오면
    # 지문 dedup에서 버리지 않고 게이트까지 보낸다(예: sev2 → sev3면 심각도3 단락으로 PAGE).
    # **기본 on — 「신규 플래그 기본 off = 비트 동일」 원칙의 예외**: off(종전 동작)는 같은 지문의
    # sev2가 통보된 뒤 4h 안에 온 sev3를 버려 D-048 「심각도3 절대 PAGE」가 깨진 채로 남는다.
    # 통과한 이벤트가 기록 심각도를 갱신하므로 추가 통과는 지문·창당 최대 2회(1→2→3)이고, 같거나
    # 낮은 심각도의 반복은 종전대로 버린다. false면 종전 동작과 비트 동일. 기동 시 1회 해석.
    dedup_severity_rise_bypass: bool = True
    recurrence_audit_every_n: int = 1         # (Plan 60 E1) 재발생 억제 감사 적재 샘플링(1=매번, count%N==0만 적재)
    noise_context_timeout_seconds: float = 3.0
    noise_context_cache_ttl_seconds: int = 300
    meta_alert_suppress_ratio: float = 0.9    # (E3) 억제율 이 값 초과 시 메타경보
    meta_alert_window_seconds: int = 3600     # (E3) 메타경보·운영지표 집계 창 (1h)
    meta_alert_min_events: int = 1            # (E3) 창 내 이벤트 수가 이 값 미만이면 무수신 메타경보
    # (plans/162 R4 · D-330 ③) 창 내 결정 중 신호 수집 실패 비율이 이 값을 넘으면 메타경보
    # collection_failed_ratio — DB 지연·미등록 db_id 등 원인을 고치게 한다. 0(기본) = 끔(비트 동일).
    meta_alert_collection_failed_ratio: float = 0.0
    enable_ai_severity_boost: bool = False    # (E3) AI 메시지 심각도 보강 (상향 전용)
    ai_severity_escalate_only: bool = True    # (E3) True=상향만 (하향 억제 금지) — 안전 고정 권장
    # ── Plan 60 E3: 동적 baseline 이상탐지 (D-079) — 순수 Python Holt-Winters(stdlib only) ──
    # baseline 이탈(잔차 z-score)을 ai_message_severity **상향 후보**로 공급한다(게이트 무변경).
    # 기존 enable_ai_severity_boost와 **AND 조건**(둘 다 True여야 analyzer 후처리가 상향 반영).
    # off(기본)면 enricher gather 태스크·키셋 불변·analyzer 무변경(회귀 0). CPU·메모리만 1차 범위.
    dynamic_baseline_enabled: bool = False    # (E3) 동적 baseline 이상탐지 on/off
    anomaly_z_high: float = 3.0               # (E3) 잔차 z-score 상향 임계(이상이면 상향 후보)
    anomaly_min_periods: int = 3              # (E3) 적합 최소 주기 수(미만이면 계산 skip→None)
    anomaly_baseline_cache_ttl_seconds: int = 3600  # (E3) baseline 파라미터 Redis 캐시 TTL
    anomaly_stl_enabled: bool = False         # (E3 2차) STL 분해 이상탐지, statsmodels optional·폴백 HW
    # (Plan 67 R3-(v) 인접·편향 검토 §2-9) 알람 kind→메트릭 소스 매핑 오버라이드.
    # 형식 "kind=resource_type:definition_name,…" (예 "cpu=server.Cpus:Utilization").
    # 빈값(기본)이면 어댑터 기본 매핑을 사용한다 — 벤더 스키마 상수는 domain이 아니라
    # 어댑터/설정에 둔다(domain 계층 벤더 중립화). 스칼라라 .env JSON 회피(CSV 관례 답습).
    anomaly_metric_source_map_csv: str = ""   # (R3-v) kind→(resource_type:definition_name) 오버라이드
    # ── Plan 60 B-7 로컬 임베딩(§15.3 L-2/L-4) — §15.4 D-035 경계: 주석 전용·판정 불변·폐쇄망 local-only ──
    # 임베딩은 관측성·주석·설명 전용이며 결정적 게이트 판정(SUPPRESS/PAGE)·억제 지문(compute_fingerprint)을
    # 절대 바꾸지 않는다. 전부 옵트인(기본 off). embedding_model_path는 폐쇄망 오프라인 반입 모델의 로컬
    # 디렉토리 경로여야 하며(미설정·비디렉토리=hub 이름→inert·런타임 다운로드 금지), 미가용 시 호출부는
    # 임베딩 주석을 건너뛰고 기존 경로가 비트 동일하게 동작한다(회귀 0).
    semantic_dedup_annotation_enabled: bool = False   # (B-7 L-2) 의미적 근접중복 주석 on/off
    topology_text_fusion_enabled: bool = False        # (B-7 L-4) 토폴로지+텍스트 융합 주석 on/off
    embedding_model_path: str = ""            # (B-7) 로컬 모델 디렉토리(미설정/비디렉토리→inert·다운로드 금지)
    embedding_similarity_threshold: float = 0.87  # (B-7 L-2·D-114) 근접중복 주석 임계 — 확정 모델 multilingual-e5-small 실측 분포(이질 max 0.852 < 0.87 < 근접 min 0.893) 기준. 모델 교체 시 재튜닝(bge-m3는 ~0.65).
    embedding_timeout_seconds: float = 2.0    # (B-7) 임베딩 hot-path 예산(초)
    enable_llm_actionability: bool = False    # (E4) LLM 피드백 few-shot 액션가능성 판단
    feedback_store_path: str = "logs/alarm_feedback.jsonl"   # (E4) 운영자 피드백 저장
    feedback_store_enabled: bool = True                      # (E4) 피드백 적재 on/off
    actionability_fewshot_count: int = 3                     # (E4) few-shot 예시 최대 개수
    feedback_store_max_lines: int = 20000                    # (Plan 83) 피드백 회전 상한 = 조회 창
    # ── Plan 86 (D-192): 알람 카드의 LLM 추천 질의 ──
    # 결정적 축 매핑이 비는 알람(management.*/platform.*/미매핑)에서만 호출한다.
    # **기본 off** — 켜면 알람 카드 조회 경로에 과금이 붙는다(D-127). 결정적 추천이
    # 있으면 프론트가 아예 호출하지 않으므로, on이어도 호출 빈도는 미매핑 축에 한정된다.
    alarm_prompt_llm_suggest_enabled: bool = False
    # ── E5: deepagents Advisory Enricher (agentic 보조 분석기, §8.5/§8.7, D-048.7) ──
    # 전부 옵트인(기본 off) — enable_agentic_enricher=False면 E1~E4 배선·발송 판단 무변경.
    # 판단은 결정적 notification_policy가 하고, enricher는 signals(승격 전용)만 보강한다.
    enable_agentic_enricher: bool = False     # (E5) 보조 분석기 옵트인
    agentic_enricher_fallback: str = "semantic_routing"  # (E5) vLLM 미서빙 시: "semantic_routing" | "deterministic_only"
    agentic_enricher_timeout_seconds: float = 8.0  # (E5) enricher 전체 타임아웃(초, 초과 시 no-op)
    agentic_enricher_max_tool_calls: int = 5  # (E5) 트랙 B ReAct 루프 도구 호출 상한
    agentic_enricher_message_alarms_only: bool = True  # (E5) LogMonitor/보안/앱 로그 한정
    resolved_to_dashboard: bool = False       # 독립 해소(severity 0)를 DASHBOARD로 표시할지 (E1)
    decision_store_path: str = "logs/alarm_decisions.jsonl"
    decision_store_enabled: bool = True
    # (Plan 54) 관제 화면 조회가 파일 끝에서 되짚는 줄 수 상한 — 감사 파일은 그대로 두고
    # 화면 응답만 보호한다. 운영 지표(aggregate/meta_alerts)는 종전대로 전량 스캔한다.
    decision_store_max_lines: int = 20000
    # (plans/153 W1 · G-7 (a)) 판정 레코드에 AI 분석 서술
    # (summary·probable_cause·recommended_action · 각 500자)을 `analysis` 필드로 가산한다
    # — 게이트 앞에서 이미 만든 분석을 옮길 뿐 추가 LLM 0.
    # 판정(티어·사유·우선순위·단계)과 무관하다. 기본 off면 레코드가 현행과 비트 동일하다.
    decision_analysis_text_enabled: bool = False
    # ── Plan 54 모듈 4: 운영자 침묵(Silence) 규칙 ──
    # 기본 off — 켜기 전에는 워커가 규칙을 조회조차 하지 않아 판정이 현행과 비트 동일하다.
    # 심각도 3은 이 기능이 켜져 있어도 어떤 규칙으로도 침묵되지 않는다(게이트 단락이 앞선다).
    silence_enabled: bool = False
    silence_store_path: str = "logs/alarm_silences.jsonl"
    silence_max_duration_seconds: int = 604800   # 규칙 1건의 최대 존속 기간(7일)
    silence_cache_ttl_seconds: int = 10          # 워커의 활성 규칙 캐시 TTL(hot-path 보호)
    ticket_batch_queue_path: str = "logs/alarm_ticket_queue.jsonl"   # (E3) TICKET 일배치 요약 큐
    ticket_batch_queue_enabled: bool = True   # (E3) TICKET 티어를 일배치 요약 큐에 적재할지
    # ── 결함 ⑬: TICKET 일배치 요약의 소비 측(D-048.9) — noise_gate/orchestration/ticket_summary ──
    # 요약 발송은 밖으로 나가는 새 동작이라 기본 off(현행과 발송 비트 동일). 켜면 매일
    # hour시(로컬)에 큐를 읽어 worKB 기본 수신자에게 요약 1통을 보내고, 보낸 항목은 큐에서 지운다.
    ticket_batch_summary_enabled: bool = False
    ticket_batch_summary_hour: int = 9        # 요약 발송 시각(로컬 0~23시)
    # 큐 보존 한도(줄) — **명시적 예외: 기본 on**. 발송 여부와 무관하게 기동 시·매일 오래된 줄을
    # 지운다. 큐는 요약 대기열일 뿐 감사 정본이 아니다(판단 감사는 decision_store — D-048.4)라서
    # 지워도 기록이 사라지지 않고, 기본 off로 두면 무한 증가(결함 ⑬)가 기본 설정에서 그대로 남는다.
    # 0이면 정리하지 않는다(종전 동작).
    ticket_batch_queue_max_lines: int = 20000
    # ── E3 후속: 워커→UI 실시간 SSE Redis pub/sub 브리지 (D-048.9 한계 해소) ──
    # 워커는 cross-process라 API의 in-memory alarm_bus를 공유 못 함 → Redis pub/sub로 중계.
    # 기본 off면 워커 경로 티어 SSE는 로그 폴백(E3 무변경, 회귀 0). 스칼라라 .env JSON 회피.
    sse_bridge_enabled: bool = False          # (E3 후속) 워커→UI 실시간 SSE 브리지 on/off
    sse_bridge_channel: str = "alarm:sse"     # (E3 후속) SSE 브리지 Redis pub/sub 채널명
    # (Plan 83) SUPPRESS 티어 SSE 발행 — 감사 레벨(관리자 전용 표시)의 전제.
    # 기본 off면 현행과 비트 동일(발행 0). 억제율이 높은 환경에선 최대 트래픽원이다.
    sse_suppressed_enabled: bool = False      # (Plan 83) SUPPRESS SSE 발행 on/off
    # ── D-049: ack/incident 라이프사이클 계측 (PostgreSQL 단일 저장소) ──
    # 기본 off면 incident 트래커 미기동 → /alarm/metrics는 기존 null 동작 유지(회귀 0).
    # 활성 시 워커는 incident 이벤트를 Redis로 발행, API 단일 라이터가 PG에 영속한다.
    incident_tracking_enabled: bool = False   # (D-049) incident 계측 on/off
    incident_event_channel: str = "alarm:incident"  # (D-049) incident 이벤트 Redis pub/sub 채널명
    # ── Plan 60 E6: 통보 컨텍스트 보강 — 메시지 기반 L1 조회·첨부 (D-105, Plan 64 §4.8 공유) ──
    # 기본 off면 신규 kind(disk/network/process/log) 보강 미수집·미첨부 → 통보 비트동일(회귀 0).
    # 기존 process_enrich_enabled(CPU/메모리) 경로와 독립 — cpu/메모리 표는 불변.
    message_enrichment_enabled: bool = False  # (E6) 메시지 기반 L1 보강 첨부 옵트인
    enrichment_min_tier: str = "PAGE"         # (E6) 이 티어 이상 통보 결정 시에만 보강 첨부
    enrichment_l1_timeout_seconds: float = 3.0  # (E6) L1 추가 조회(host-wide 프로세스) 상한
    enrichment_profile_map_csv: str = ""      # (E6) kind→요지 제목 오버라이드 ("disk=...,log=...")
    # ── plans/91 1-6 · Plan 60 §18 E8 (나)안: post-gate **비차단** L3 보강 (D-189 허용목록 명령 · U-H 확정) ──
    # 기본 off면 수집·후속 발송·감사 전부 미수행 → 통보 경로 비트동일(회귀 0). 게이트 동기 경계 probe는 없다(폐기 —
    # `gate_l3_probe_*` 신설 금지). **만료일 2027-02-20**(D-161 C1 · docs/flag_audit.md 통일 기한) — 기한 도래 시 on 확정 또는 삭제.
    l3_enrichment_enabled: bool = False         # (E8) PAGE 통보 뒤 kind별 허용목록 명령으로 L3 수집 → 결정적 요지 후속 발송
    l3_audit_enabled: bool = False              # (E8) 수집 명령·상태지문·전이를 decision_store에 감사(type="l3_state")
    l3_host_access_mode: str = "allowlist_exec"  # (E8) 실행 채널 — D-189 경로 B. 현재 allowlist_exec(ssh)만 구현
    l3_profile_map_csv: str = ""                # (E8) kind→프로파일 오버라이드("memory=memory,process=process") — 미지정 시 kind 동명
    l3_command_timeout_seconds: float = 20.0    # (E8) 명령당 상한 — 부하 가드 접두(`timeout 20 nice -n 10`)와 같은 값
    l3_ssh_user: str = ""                       # (E8) allowlist_exec ssh 사용자(빈 값이면 현재 사용자)
    l3_max_inflight: int = 4                    # (E8) 동시 L3 수집 태스크 상한(알람 폭주 시 스폰 차단·사유 로그)
    # ── Plan 60 E5: 변경/구성 이벤트 상관 (D-081 초안 → 착수 시 D-109 재부여) ──
    # 기본 off면 변경 피드 조회·오버레이 미수행 → noise_ctx 신규 키 None·게이트 step9 비트동일(회귀 0).
    # 억제가 아니라 승격 — 변경 근접 알람은 promote 신호로만 추가된다(원인성 판단·재현율 우선, §7.2).
    # 1차 소스 = 폴스타 cmm_resource_lifecycle_history(읽기전용, gp/yd PostgreSQL). b0(DB2)는 미조회.
    change_correlation_enabled: bool = False  # (E5) 변경 상관 on/off (변경 피드 조회·오버레이)
    change_window_seconds: int = 3600         # (E5) 알람 직전 이 창(초) 내 변경만 원인 후보로 오버레이
    # ── Plan 60 E7: 실측 ITSM 사례 기반 텍스트·주석 신호 보완 (D-116, §17) ──
    # 전부 옵트인(기본 off) → off면 현행 게이트·워커·파서 비트동일(회귀 0). 결정적 1차·LLM
    # annotate-only(기존 alarm_analyzer 재사용·신규 모델 반입 없음)·읽기전용·심각도3 단락 불변.
    # E7-a 주석 하베스팅(§17.3): dedup 억제 시 ACK 이전에 계획작업/해소/운영자접수 주석을 추출해
    #   record_recurrence(annotation=…) 최상위 필드로 원 인시던트에 보존(재통보 0·억제≠삭제 텍스트 확장).
    annotation_harvest_enabled: bool = False  # (E7-a) 주석 하베스팅 on/off (재발신 억제 시 주석 추출·감사)
    # E7-a 코로보레이션 게이팅(B-9): planned_work AND (resolution OR E2 클러스터 소속 OR E5 change_nearby)
    #   동시 충족 시에만 후속 동종 알람 DASHBOARD 강등(SUPPRESS 아님·E4 하이브리드 정합). 주석 단독은
    #   강등 없이 첨부만 — 텍스트 단독으로 억제강화 금지(재현율 우선). annotation_harvest_enabled와 독립.
    annotation_planned_suppress: bool = False  # (E7-a) 계획-무해 주석 DASHBOARD 강등 on/off (코로보레이션 게이팅)
    # ── Plan 67 R3-(v) · D-132: 운영자 주석 3분류를 LLM으로 전환 (기본 OFF) ──
    # 운영자 손글 한국어는 정규식 어휘를 벗어난다("이상무"·"문제없음" 미매칭). 분류기는
    # application 계층(annotation_classifier.py)에 있고 domain은 라벨 enum만 소비한다(D-035 예외).
    # **알람 유입량만큼 LLM 호출이 발생**하므로 기본 OFF — ON 전환은 과금 판단이 필요한
    # 운영 결정이다(D-127). OFF면 워커는 기존 정규식 추출을 그대로 호출한다(비트동일·회귀 0).
    # 실패·타임아웃·파싱불가는 정규식 분류로 강등하고 사유를 로그로 남긴다(침묵 강등 금지).
    annotation_llm_classification_enabled: bool = False  # (R3-v) 주석 LLM 분류 on/off
    annotation_llm_timeout_seconds: float = 3.0   # (R3-v) 분류 1건 타임아웃(초, 초과 시 정규식 강등)
    annotation_llm_cache_max: int = 500           # (R3-v) 주석 해시 캐시 항목 상한(메모리 가드)
    annotation_llm_cache_ttl_seconds: int = 3600  # (R3-v) 캐시 항목 TTL(초, 만료 sweep 기준)
    # E7-b 비알람 사전분류(§17.4): 알람 마커 부재 + 비알람 마커(승인/요청/바랍니다 등) 존재 시 SUPPRESS.
    #   애매하면 알람 간주(재현율 우선). 게이트 step0.5(step1 이전)에서 결정적 마커로 판정·감사.
    non_alarm_filter_enabled: bool = False    # (E7-b) 비알람(승인/안내성) 사전 억제 on/off
    # E7-c 파서 견고성(§17.5): 이질 포맷(호스트 접두 없음·네트워크 장비) graceful 폴백 — 침묵 드롭·크래시
    #   금지, 미식별 severity는 보수적 처리(드롭 방지). 알려진 포맷 파싱 결과는 비트동일(신규 폴백만 추가).
    format_tolerant_parsing_enabled: bool = False  # (E7-c) 이질 포맷 graceful 폴백 파싱 on/off
    # E7-d 사이트 상관 차원(§17.6): correlation.signature_tokens에 사이트/위치 토큰을 가중 차원으로 추가
    #   (워커가 site 토큰 산출·주입, domain은 값만 소비·순수성 유지). 존 경계(B-6) 불변. off면 extra=""로
    #   현행 필드 Jaccard와 비트동일. chattering(fleeting/repeating) 감사 라벨은 annotation_harvest_enabled 하.
    correlation_site_dimension_enabled: bool = False  # (E7-d) E2 사이트/위치 상관 차원 on/off
    # ── Plan 64 CW-A: 자동 조사 트리거 훅 (D-118 · sre_agent MCP submit/poll · Plan 60 §14.2) ──
    # 게이트가 tier ≥ investigation_trigger_min_tier(기본 PAGE)로 결정한 직후, notification_gate와
    # 분리된 트리거 노드가 sre_agent 조사 서비스에 비차단 submit → poll(전체 타임아웃 내) → 브리핑을
    # 통보에 첨부 + decision_store 감사한다. 게이트 판정·라우팅은 무변경(별도 노드·전체 타임아웃 가드).
    # 기본 off면 트리거 노드 미배선 → 게이트·통보 경로 비트동일(회귀 0)·브리핑 미생성. 서비스 다운/
    # 타임아웃/거부는 graceful 실패(통보·판정 정상 완료·사유 구조화 감사, 침묵 금지). 조사는 읽기전용·
    # 조치 없음(D-003) — 브리핑 수신·첨부만.
    # D-347: 운영 기본 = 수동 전용(false). 켜면 PAGE마다 자동 조사 — 부하 주의.
    investigation_trigger_enabled: bool = False   # (CW-A) 자동 조사 트리거 옵트인
    investigation_trigger_min_tier: str = "PAGE"  # (CW-A) 이 티어 이상 결정 시에만 트리거(enrichment_min_tier와 동일 규칙)
    investigation_service_url: str = "http://localhost:9098/sse"  # (CW-A) sre_agent 조사 서비스 MCP SSE 엔드포인트
    investigation_service_token: SecretStr = SecretStr("")  # (CW-A) 정적 Bearer 토큰(없으면 무헤더 — 3-D에서 강제)
    investigation_mcp_call_timeout_seconds: float = 10.0   # (CW-A) submit/poll 개별 호출 타임아웃(초)
    investigation_poll_interval_seconds: float = 1.0       # (CW-A) poll 재조회 간격(초)
    investigation_total_timeout_seconds: float = 45.0      # (CW-A) 전체(submit+poll) 타임아웃 가드(per-call 아님·§3.2)
    # ── Plan 66 3-E: 즉시통보 + 후속 브리핑 (D-124 설계 노트 정련 · Plan 64 §6.2 "후속 메시지") ──
    # 기본(off)은 CW-A 인라인 첨부 — 트리거 노드가 poll 완주까지 기다렸다가 브리핑을 실어 통보를
    # 한 번 보낸다. 실 LLM 조사는 수십~수백 초라 PAGE 통보가 그만큼 지연된다. on이면 트리거는
    # submit까지만 하고(통보 즉시 발송·지연 0) 백그라운드 태스크가 poll 완주 후 **브리핑을 후속
    # 메시지로 별도 발송**한다. 후속 전달은 자체 클라이언트로 수행한다 — 워커의 공유 클라이언트를
    # 쓰면 다음 알람의 connect/disconnect와 경합한다(워커는 알람을 직렬 처리·클라이언트 1개 공유).
    # off면 CW-A 경로 비트동일(회귀 0). 후속 발송 실패·타임아웃은 감사에 사유 기록(침묵 금지)하고
    # 통보·판정에는 영향을 주지 않는다. 읽기전용·조치 없음(D-003).
    investigation_followup_enabled: bool = False           # (3-E) 즉시통보+후속 브리핑 옵트인
    investigation_followup_timeout_seconds: float = 1200.0  # (3-E) 후속 poll 전체 타임아웃(초) — sre_agent investigation_timeout_seconds(600)보다 커야 한다 · 권장 max(600, 2×조사 상한)(D-344 ④) · W22
    investigation_followup_max_inflight: int = 8           # (3-E) 동시 진행 후속 태스크 상한(알람 폭주 시 spawn 차단·사유 로그)
    # (plans/187 W5a · §5.6 ④) 플레이북 후속 메시지의 조사 대화 링크 base(`<base>/?investigation=<id>`).
    # 비면 링크 대신 「웹에서 확인」 문구만 싣는다. 플레이북 브리핑이 없으면 읽지 않는다(비트 동일).
    investigation_web_base_url: str = ""
    # ── plans/153 W2 · D-325 G-1 (a): 조사 저장소 (브리핑 웹 조회용 별도 JSONL) ──
    # 조사 상태 전이(submitted → done/failed/timeout …)와 브리핑 원본(32KB 상한)을 1행씩 남긴다.
    # 판정 감사(`decision_store_path`)와 파일을 나눠 관제 tail 스캔에 큰 레코드가 끼지 않게 한다.
    # 워커는 조사 트리거가 켜졌을 때만 저장소를 만든다 — 트리거 off면 파일 생성 0(비트 동일).
    investigation_store_path: str = "logs/alarm_investigations.jsonl"
    # 조회(`/alarm/investigations/*` · 관제 결정 추적의 `investigation` 블록)가 파일 끝에서 되짚는
    # 줄 수 상한. 브리핑 행은 수 KB~32KB라 판정 감사(20000)보다 작게 잡는다.
    investigation_store_max_lines: int = 2000
    # ── plans/153 W4 · D-325 G-5 (a)·G-6 (a): 조사 페이로드 사건 스냅샷 · 사건당 1회 종합 조사 ──
    # 켜면 사건 추적기(cross_source_mode≠off — shadow도 판정 불변으로 사건만 만든다)에 묶인
    # 알람의 조사 페이로드에 `meta.episode`(제출 시점 사건 스냅샷)·`meta.cross_source`(이 알람의
    # 결합 판정)를 싣고, 사건에 폴스타·제니퍼 멤버와 결합 링크가 처음 갖춰지면(통보 멤버 ≥1)
    # 그 사건에 대해 `kind="episode"` 종합 조사를 1회 제출한다. 판정·티어·사유는 바꾸지 않는다
    # (관측·조사 입력만). 조사 트리거(investigation_trigger_enabled)가 꺼져 있으면 아무것도
    # 하지 않는다. 기본 off = 비트 동일.
    investigation_episode_context_enabled: bool = False
    # ── Plan 64 CW-B: pull 위임 (deepagents/시멘틱 라우팅 fault_diagnosis 의도 → sre_diagnose) ──
    # 사용자가 "○○ 서버 원인 분석해줘"류로 장애 진단을 요청하면(pull), 시멘틱 라우터가
    # fault_diagnosis 의도로 분류하고 신규 노드가 sre_agent에 sre_diagnose(question, server?, host?,
    # db?)를 위임 → poll(전체 타임아웃 내) → 자연어 진단 응답을 반환한다(sre-agent/05 §3·§7).
    # 연결 설정은 CW-A의 investigation_service_url/token/타임아웃을 재사용한다(단일 서비스).
    # 기본 off면 라우터 프롬프트에 fault_diagnosis 미노출·노드 미배선 → 라우팅 비트동일(회귀 0).
    # 서비스 다운/타임아웃/거부는 graceful — 침묵 폴백 없이 사유를 담은 자연어 응답을 돌려준다(D-003 읽기전용).
    fault_diagnosis_enabled: bool = False   # (CW-B) 장애 진단 pull 위임 옵트인
    # (plans/187 W12 · D-341 ㉕㉖ · G-32·G-33) 채팅 증상 질의 조사 — on이면 fault_diagnosis가 동기 대기 대신
    # 결정적 라우터(대상 1건 × 증상 유형 1건 → 플레이북 잡(가상 알람 맥락 · 저장 kind=manual·origin=chat),
    # 아니면 비동기 sre_diagnose(kind=diagnosis))로 접수만 하고 조사 카드 ref를 돌려준다. 상한은 W10/W11
    # 사용자별 카운터 공유. env 이름은 계획서 확정값(접두 없음). 기본 off = fault_diagnosis 비트 동일.
    sre_chat_investigation_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("SRE_CHAT_INVESTIGATION_ENABLED", "sre_chat_investigation_enabled"),
    )
    # ── plans/187 W10 · D-341 ⑭ (G-21): 조사 대화의 「추가 조사 요청」 (plans/155 공용부 위) ──
    # 알람 조사 1건(부모)에서 결정적 초점 + 보충 한 줄로 sre_diagnose를 비동기 접수하고, 부모 브리핑의
    # 판정 요약(prior_findings — LLM 서술 제외)을 함께 넘긴다. 결과는 조사 저장소 kind=diagnosis.
    # 인가는 HOST_AUTHZ_MODE(기본 admin_only). 기본 off면 접수·목록 라우트가 404(미등록과 같은 응답)이고
    # 저장소에 신규 키를 쓰지 않는다(비트 동일). 이름은 3-E `investigation_followup_*`(후속 브리핑)와
    # 겹치지 않게 `additional`로 둔다.
    investigation_additional_enabled: bool = False          # (G-21) 추가 조사 요청 옵트인
    investigation_additional_user_hourly_limit: int = 3     # (G-21 · D-335 ⑥) 사용자별 시간당 접수 상한
    investigation_additional_note_max_chars: int = 200      # (G-21) 보충 한 줄 길이 상한(자)
    # ── plans/187 W11 · D-341 ㉑~㉔ (G-28~G-31): 「이 알람 분석」 — 사용자 요청 알람 조사 ──
    # 결정 저장소의 알람(등급 무관 · 30일)을 자동 조사와 같은 페이로드로 다시 조사한다(새 레코드
    # kind=manual · 원 조사 불변 · 통보·승격 없음). 권한은 알람 존 범위 사용자(fail-closed). 상한은 위
    # 추가 조사와 같은 사용자별 카운터(진행 1 · 시간당 investigation_additional_user_hourly_limit)를
    # 공유한다. 같은 알람에 진행 중이거나 join_minutes 안의 사용자 요청 조사가 있으면 그 id로 합류한다.
    # off면 접수 라우트가 404이고 by-alarm 응답이 종전과 비트 동일하다.
    # D-347 (사용자 확정 2026-10-26): 알람 조사는 「수동 전용」 — 자동 조사(trigger)는 끄고 사람이
    # 고른 알람만 조사한다. 기본 on은 D-162 「신규 플래그 기본 off」의 명시 예외다 — 자동 조사 off에서
    # 조사를 시작하는 동선이 이것뿐이고, `.env`는 미추적이라 코드 기본으로 둔다. 켜져 있으면 판정
    # 기록에 원 알람 시각·hostname·resource_type이 함께 남는다(다시 분석의 재료).
    investigation_manual_enabled: bool = True               # (G-28 · D-347) 사용자 요청 알람 분석
    investigation_manual_join_minutes: int = 10             # (G-31) 같은 알람 합류 창(분) [제안 값]
    # ── plans/187 W9 · D-341 ⑩⑮⑳ (G-14~G-16 · G-27): 장애 대응 지식(KB) 쓰기·참조 측 ──
    # 조사 완료 건의 KB 초안(state=wip 고정 · 결정적 채움 · LLM 0) · YAML 조각 내보내기 · 포스트모템
    # 내보내기(기준 충족 건만) · 알려진 오류 사용 피드백 · 엔진 선택 기록·통계 · 관리자 검토 대상 목록 ·
    # 조회 응답의 매뉴얼 RAG 참고 인용(참고 목록만 — 판정·종합 입력 아님). 기록은 본체 별도 JSONL
    # (`investigation_kb_store_path`)에만 하고 sre_agent 로더 경로에는 쓰지 않는다(런타임 쓰기 0).
    # 기본 off면 접수·조회 라우트가 404·관리자 라우터 미등록이고 조사 조회 응답이 종전과 비트 동일하다.
    investigation_kb_enabled: bool = False                  # (W9) KB 쓰기·참조 측 옵트인
    investigation_kb_store_path: str = "logs/alarm_incident_kb.jsonl"  # (W9) 초안·KE 피드백·엔진 선택 JSONL
    investigation_kb_store_max_lines: int = 20000           # (W9) 회전 상한 = 조회 창(FeedbackStore 방식)
    investigation_kb_scan_max_lines: int = 10000            # (W9) 관리자 집계가 조사 저장소 끝에서 되짚는 줄 수 [제안 값]
    investigation_kb_incorrect_streak: int = 3              # (W9 · §5.7.5 3) 「아님」 연속 N회 → 재검토 대상 [제안 값]
    investigation_kb_engine_min_samples: int = 30           # (G-27 · ⑳) 유형별 선택 표본 하한 [제안 값]
    investigation_kb_engine_switch_ratio: float = 0.8       # (G-27 · ⑳) 「플레이북」+「둘 다」 비율 하한 [제안 값]
    # ── plans/187 W14 · D-341 ㉙㉚㉛ (G-39·G-41·G-42): 조사 진행 실시간 허브·SSE ──
    # 본체 in-process 허브(`alarm_bus`와 같은 단일 프로세스 전제)가 미종결 조사마다 폴러 1개로 sre_agent에
    # `sre_get_investigation(activity_since)`를 묻고(보는 사람 수와 무관) 실행 기록을 SSE
    # (`/alarm/investigations/stream`)로 나눠 보낸다. 종결 시 요약(발췌·AI 메모 제외)을 조사 저장소 옆
    # `investigation_activity.jsonl`에 1행 남긴다. 기본 off면 stream·active 404 · 조회 경로의
    # `activity_since`는 무시(종전 응답 · 종전 지연 poll 그대로 — 비트 동일).
    investigation_live_view_enabled: bool = False           # (W14) 실시간 진행 화면 옵트인
    investigation_live_poll_seconds: float = Field(default=5.0, ge=1.0)  # (W14) 조사당 poll 간격(초) [제안 값]
    # ── Plan 64 CW-C: escalate-only 후속 통보 승격 (poll verdict.escalate 소비) ──
    # investigation_trigger의 poll 결과 구조화 verdict(ImportanceVerdict: level/confidence/escalate/
    # signals — sre-agent/02 §6)에서 escalate=True면, 통보에 "중요도 상향(자동 조사)" 안내 블록을
    # 첨부(승격)한다. **escalate-only** — 게이트 판정(tier/routing/decision)은 소급 변경·하향하지
    # 않고 상향 신호만 안내한다(Plan 64 §5.1 역방향 계약). 기본 off면 escalation 미생성 → 통보
    # 본문 비트동일(회귀 0). investigation_trigger_enabled가 켜져 poll이 돌 때만 verdict가 존재한다.
    fault_escalation_enabled: bool = False  # (CW-C) escalate-only 후속 통보 승격 옵트인
    # ── plans/87 J4: `app_impact` 승격 — APM 게이트웨이 MCP `apm_events` (D-195 ② · R-7) ──
    # 폴스타 알람(비 APM)이 매트릭스 단계에서 DASHBOARD·TICKET으로 판정되면, 같은 hostname·사건창
    # [alarm_time − window, alarm_time]의 제니퍼 fatal 이벤트를 게이트웨이에 묻고 있으면 PAGE로
    # **승격만** 한다. 억제 단계·SUPPRESS·심각도3 단락은 건드리지 않는다. 호출 실패·오류 응답은
    # 판정 불변 + 사유 로그·감사. 기본 off(또는 URL 비움)면 클라이언트 미생성·호출 0 → 게이트 판정
    # 비트동일(회귀 0). 토큰은 게이트웨이 Bearer(`APM_GATEWAY_BEARER_TOKEN`)이며 제니퍼 토큰이
    # 아니다(제니퍼 자격증명은 게이트웨이에만).
    app_impact_enabled: bool = False          # (J4) app_impact 승격 옵트인
    apm_mcp_url: str = ""                     # (J4) 게이트웨이 MCP SSE 엔드포인트 — 비면 off
    apm_mcp_token: SecretStr = SecretStr("")  # (J4) 게이트웨이 정적 Bearer 토큰(비면 무헤더)
    app_impact_window_minutes: int = 10       # (J4) 사건창(분) — apm_events lookback_minutes
    # ── plans/144 W1: 제니퍼(APM) 알람 유형 정책 — 노이즈 컨텍스트 공급자·지속 조건·정규화 유형 ──
    # 켜면 제니퍼 알람(dbId jennifer·jennifer_<소스>)의 노이즈 컨텍스트를
    # `config/apm_noise_policy.yaml`(고정 경로 — 설정 키 없음)로 채우고, 지문·해소 짝맞춤에
    # 정규화 유형을 쓰며, 지표형 경고(심각도2)에 지속 조건 강등(하한 DASHBOARD)을 건다.
    # 심각도3 단락·폴스타 경로는 불변. 기본 off면 제니퍼 알람은 현행 그대로(신호 수집 실패 →
    # 보수적 PAGE · 지문 원문) — 비트 동일. 기동 시 1회 해석.
    apm_noise_policy_enabled: bool = False    # (plans/144) 제니퍼 유형 정책 경로 옵트인
    # ── plans/144 W3: 폴스타×제니퍼 크로스소스 사건 상관 (D-317) ──
    # 같은 존·같은 호스트 키·사건창 안의 폴스타 알람과 제니퍼 이벤트를 사건(episode)으로 묶고,
    # 규칙 표(`cross_source_rules_path`)의 원인→증상 방향이 확인되면 증상을 DASHBOARD로 강등한다
    # (SUPPRESS 없음 · 심각도3 불변 · 매핑 신뢰도 약하면 묶기만). 모드 사다리:
    # off(기본 · 사건 추적·판정 모두 없음 → 비트 동일) · shadow(판정 불변 · 「했을 조치」만 감사) ·
    # annotate(사건 묶음·통보문 주석 · 판정 불변) · enforce(강등 실행 — 규칙 행 `enforce: true`만).
    cross_source_mode: Literal["off", "shadow", "annotate", "enforce"] = "off"
    cross_source_rules_path: str = "config/cross_source_rules.yaml"  # (W3) 규칙 표 경로
    episode_idle_seconds: int = 900           # (W3) 마지막 알람 뒤 이 시간이 지나면 사건 종료
    # ── plans/144 W4: app_impact 사후 승격 · 제니퍼 정상 강등 shadow (D-317) ──
    # 사후 승격: 폴스타 알람이 DASHBOARD·TICKET으로 판정된 뒤 같은 사건에 제니퍼 fatal·critical이
    # 늦게 오면 그 폴스타 알람을 PAGE로 한 번 더 통보한다(알람당 1회 · 사건이 열려 있을 때만 ·
    # app_impact_enabled와 cross_source_mode ∈ {annotate, enforce} 필요 — shadow는 통보 불변).
    # 정상 강등 shadow: 폴스타 CPU·메모리 경고(심각도2) 때 같은 호스트의 제니퍼 이벤트가 창 안에
    # 0건이고 게이트웨이가 정상 응답했으면 감사에
    # 「강등했을 것」만 남긴다(판정 불변). 둘 다 기본 off = 비트 동일.
    app_impact_late_promotion_enabled: bool = False  # (W4) 늦게 온 제니퍼 심각 이벤트 사후 승격
    apm_healthy_demotion_shadow: bool = False  # (W4) 제니퍼 정상 → 폴스타 경고 강등 shadow 기록
    # ── plans/162 · D-330: 마지막 관문 「PAGE 필요성 재판정」(page_review) ──
    # 앞 15단계가 PAGE로 정한 알람만 결정적 규칙(LLM 미사용)으로 다시 본다.
    # 열린 사건 반복(R1) · 같은 호스트 진행 중 사건(R2) · 다수 호스트 동시 발생의 대표 외(R3)면
    # TICKET(억제 아님 · 감사 기록 유지). 새 사건 첫 PAGE · 심각도 상승 · 앱 영향 fatal ·
    # 미해소 리마인더 · 운영자 지정 알람은 PAGE.
    # 모드: off(기본 · 평가·워커 상태 없음 → 비트 동일) · shadow(판정 불변 · 「했을 강등」만
    # 감사) · enforce(강등 실행). D-035·D-048 원 문구는 enforce 전까지 운영 동작이다.
    # 기동 시 1회 해석.
    page_review_mode: Literal["off", "shadow", "enforce"] = "off"  # (plans/162) 모드
    page_review_host_window_minutes: int = 30     # (plans/162 R2) 같은 호스트 사건 창(분)
    page_review_fanout_window_minutes: int = 10   # (plans/162 R3) 동시 발생 창(분)
    page_review_fanout_page_max: int = 1          # (plans/162 R3) 창 안 대표 PAGE 호스트 수
    page_review_reminder_hours: int = 24          # (plans/162 보존 4) 미해소 리마인더 간격(시)
    page_review_reopen_quiet_minutes: int = 30    # (plans/162 R1) 해소 뒤 재발 유예(분)
    # (plans/162 F4 · D-330 R1 해소 대용) 해소 이벤트가 오지 않는 출처(제니퍼)의 사건을 마지막으로
    # 본 뒤(판정·지문 중복 재발 포함) 이 시간(분)이 지나면 닫힌 것으로 본다 — 다음 PAGE는 새 사건.
    # 0(기본) = 끔 = 비트 동일 · 음수는 0. 값은 내부망 2회차 재발 간격 분포로 정한다.
    # 재통보 창(repeat_interval_seconds) 뒤 재통보에만 효력 · R3 창과 무관(값은 R3 창보다 크게).
    page_review_idle_close_minutes: int = 0
    page_review_always_page_csv: str = ""         # (plans/162 보존 5) 항상 PAGE 알람명 글롭 CSV
    # (plans/162 R4 · D-330 ③) 신호 수집 실패 시 PAGE는 심각도3만 — 그 밖은 중요도 「보통」으로
    # 매트릭스를 탄다(sev2 → TICKET · D-048 「실패 시 보수적 PAGE」 개정). 기본 off = 현행 보수
    # PAGE와 비트 동일(D-048 원 문구는 on 전까지 운영 동작). off + page_review_mode=shadow면
    # 판정 불변으로 「했을 티어」만 감사에 남긴다. 기동 시 1회 해석.
    collection_failed_page_sev3_only: bool = False
    # (plans/162 W5 · D-336) 제니퍼 정책 경로(노이즈 컨텍스트 출처 apm_policy)의 실효 심각도 3은
    # 「심각도3 단락」에서 유형 정책 표(config/apm_noise_policy.yaml)를 본다 — `notify: page` 행만
    # PAGE, 그 밖(표 밖 유형·default)은 TICKET(DASHBOARD 아래로 내리지 않음 · D-317 ①). 폴스타·
    # 수집 실패는 현행 단락 PAGE. 기본 off = 비트 동일(D-035 원 문구가 운영 동작). 기동 시 1회 해석.
    apm_sev3_policy_enabled: bool = False

    model_config = {"env_prefix": "NOISE_", "env_file": ".env", "extra": "ignore"}

    @property
    def page_review_always_page_globs(self) -> list[str]:
        """page_review_always_page_csv → 알람명 글롭 목록(plans/162 보존 5). 빈 항목은 버린다."""
        return [g.strip() for g in self.page_review_always_page_csv.split(",") if g.strip()]

    @property
    def importance_value_map(self) -> dict[str, str]:
        """importance_value_map_csv → {코드: 라벨} 파싱. 잘못된 항목은 무시한다.

        형식: "1=낮음,2=보통,3=높음" (CSV, '=' 구분). 빈 값이면 빈 dict.
        decide_notification이 map_importance에 넘기는 매핑이며, 미매핑 코드는
        정책 계층에서 '보통'으로 보수 처리된다(§6.3).
        """
        result: dict[str, str] = {}
        for pair in self.importance_value_map_csv.split(","):
            pair = pair.strip()
            if not pair or "=" not in pair:
                continue
            code, _, label = pair.partition("=")
            code = code.strip()
            label = label.strip()
            if code and label:
                result[code] = label
        return result
