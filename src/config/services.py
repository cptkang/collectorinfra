"""기타 서비스 설정 — 서버·관측(로그)·DRM·문서 RAG·결과 그래프·포탈 도움말.

plans/166 W2 I4 — 옛 `src/config.py`에서 클래스를 그대로 옮겼다. 공개 경로는 `src.config`
그대로다(패키지 `__init__`이 이 모듈의 클래스를 재노출한다 · D-337 ①).
"""

# 옮긴 주석·문자열의 긴 줄은 이동 전부터 있던 것이다 — 바이트 그대로 두려고 파일 단위로 끈다.
# ruff: noqa: E501

from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings


class ServerConfig(BaseSettings):
    """API 서버 설정."""

    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = ["*"]
    # 일반 질의 **처리 상한**(초) — 요청 후 **첫 답변(표 또는 첫 토큰)까지**의 상한이다(D-267 ⑦ ·
    # 2026-09-28 · 종전 "요청 전체 상한"). 첫 답변 뒤에는 아래 토큰 간 idle·전달 연장으로만 끊는다.
    # 60 → 120(2026-09-21 사용자 확정): 은행존 DB2 질의가 SQL 재생성 1회로 60.6s에 끊겼다.
    # 관리자 설정 화면(API_QUERY_TIMEOUT)에서 바꾸고 설정 리로드로 반영된다.
    query_timeout: int = 120
    file_query_timeout: int = 120
    # plans/119 트랙 T · D-267 ⑥⑦ — **플래그 없이 기본 동작**(D-162 예외). 1차는 설정 상수다(G-6 —
    # 적응형 예산은 재측정 R2 뒤 재판단).
    # 서술 예약(초): 조회 마감 = 처리 마감 − 이 값. 조회 단계는 조회 마감을 넘겨 새 LLM 작업(SQL
    # 재생성·재계획 후속)을 시작하지 않는다. 15는 plans/119 §5.4.3 T-2의 추정(요약 2~3줄)이다 —
    # 단계 타임라인(T-0) 실측 p90으로 다시 정한다.
    answer_reserve_sec: int = 15
    # 첫 답변 뒤 토큰 간 idle 상한(초) — 이 시간 동안 답변 토큰이 없으면 끊는다(G-7 초기값 30).
    stream_idle_timeout_sec: int = 30
    # 전달 연장(초) — 처리 상한 + 이 값이 무한 대기 방지 전체 상한이다(G-7 초기값 60 · D-242 유지).
    stream_delivery_grace_sec: int = 60
    # plans/89 · D-204: SSE 진행 신호. **기본 on** — plans/80 §5.4-③(신규 플래그 기본 off)의
    # 명시 예외다. 근거: ①추가되는 이벤트(progress·heartbeat)는 부가적이고 구 클라이언트는
    # 미지 type을 무시한다(app.js SSE 파서 실측) ②off면 이 기능이 푸는 증상("커서만 깜박임",
    # 무이벤트 최대 API_QUERY_TIMEOUT초)이 운영에서 그대로 남는다 ③프론트와 동시 배포된다.
    sse_progress_events: bool = True
    # 무이벤트 구간에 heartbeat를 내는 주기(초). 0 이하면 하트비트 없음. heartbeat는 어떤 상한
    # (처리 상한·토큰 간 idle·전체 상한 — plans/119 T-5)도 되돌리지 않고, 0이어도 판정은 제때 한다.
    sse_heartbeat_interval_sec: int = 5
    # plans/82 v7 R-2 · D-249: 존 동시 조회에서 먼저 끝난 존 그룹의 **행 미리보기** 행 수.
    # 0이면 행 없이 "은행존 12건 완료 · 3.2s" 알림만 낸다. 행은 최종 응답과 같은 `DataMasker`로
    # **마스킹한 뒤** 나간다. **기본 10** — 사용자 결정(2026-09-22, 부분 결과 즉시 노출을 행
    # 미리보기까지)으로 plans/80 §5.4-③의 명시 예외다. sse_progress_events가 off면 함께 꺼진다.
    sse_group_preview_rows: int = 10

    model_config = {"env_prefix": "API_", "env_file": ".env", "extra": "ignore"}


class ObservabilityConfig(BaseSettings):
    """실행 SQL 파일 로그·실패 트레이스 설정 (D-140/D-141).

    감사 로그(AuditConfig)와 목적이 다르다 — 감사는 "누가 무엇을 했는가"의 규정 준수 기록이고,
    여기는 "왜 실패했는가"의 진단 재료다. 보존 기간도 트레이스가 더 짧다(실패 건만 쌓임).
    """

    sql_log_enabled: bool = True          # 실행 SQL을 logs/sql/에 파일 기록
    sql_log_retention_days: int = 30      # SQL 로그 보존 일수 (0 이하면 정리 비활성)
    trace_enabled: bool = True            # 실패 요청 단계 트레이스 수집
    trace_retention_days: int = 14        # 트레이스 보존 일수 (0 이하면 정리 비활성)
    # 요청당 링버퍼 단계 상한. 노드 20개 × 재시도 3회 + 여유를 감안한 값으로, 초과 시
    # 가장 오래된 단계부터 밀어낸다(in-memory 버퍼는 bound 필수 — Known Mistakes).
    trace_max_steps: int = 200
    # ── plans/92 트랙 B-1(O4): 본체 자기 관측 `GET /api/v1/metrics`(Prometheus 노출 형식) ──
    # on이면 라우트와 HTTP 계측 미들웨어를 등록한다. 기본 off = 라우트 부재(404)·미들웨어 미추가로
    # 현행과 비트 동일. 기동 시 1회 해석(리로드 무효 — 재시작 필요).
    # **만료일 2027-03-22**(D-161 ① · 6개월) — 본체를 긁는 수집기가 생겼는지로
    # 삭제 또는 사유부 연장.
    metrics_endpoint_enabled: bool = False
    # 정적 Bearer 토큰(G-5 (ii)). on인데 비어 있으면 503으로 거부한다
    # (fail-closed — 무인증 노출 금지).
    # SecretStr: 웹UI 편집 차단·repr 마스킹 — `.env` 전용 그룹의 정적 Bearer 전례
    # (`NoiseGateConfig.investigation_service_token`)와 같다.
    metrics_bearer_token: SecretStr = SecretStr("")

    model_config = {"env_prefix": "OBS_", "env_file": ".env", "extra": "ignore"}


class DrmConfig(BaseSettings):
    """Softcamp ServiceLinker DRM 해제 설정 (Plan 74 / D-156).

    기본 비활성(enabled=False) — 개발 PC·CI는 DRM 모듈 설치가 불가하므로
    Passthrough로 동작하고, 운영(RHEL 9.6)에서만 활성화한다.
    softcamp.properties의 내용(LinkSystemIP 등)은 scsl.jar가 직접 읽는 파일이라
    env로 대체 불가 — 여기에는 경로만 담는다 (계획서 §2.7).
    """

    enabled: bool = False
    java_bin: str = "java"              # Java 1.8+ (반입은 JDK 21)
    wrapper_path: str = ""              # tools/drm-wrapper/Decrypt.java 절대경로 (단일 소스 실행)
    scsl_jar_path: str = ""             # scsl.jar (소프트캠프 제공) 절대경로
    properties_path: str = ""           # 02_ServiceLinker/softcamp.properties 절대경로
    key_file_path: str = ""             # 04_KeyFile/keyDAC_SVR0.sc 절대경로
    group_id: str = "SECURITYDOMAIN"    # 가이드 "수정금지" 리터럴 — 변경 대비 주입 가능
    temp_dir: str = ""                  # 빈 값이면 시스템 temp 하위 drm_scsl/ 자동 생성
    timeout_sec: int = 20

    model_config = {"env_prefix": "DRM_", "env_file": ".env", "extra": "ignore"}


class RagConfig(BaseSettings):
    """문서 RAG(FabriX Retrieval Connector) 설정 (plans/126 · W1).

    **접속 정보는 컬렉션별 정적 필드**다(`RAG_<COLLECTION>_*`). 자산 ID·토큰·엔드포인트가
    리트리벌 발급마다 회전하므로(plans/126 §3.1b) 설정이 소유하고, 코퍼스의 **의미**는
    `config/rag_collections.yaml`이 소유한다. 교체 절차는 `docs/32_rag_retrieval_runbook.md`.

    동적 키 이름(`RAG_CONN_<ID>_*`)을 쓰지 않는 이유: 설정 카탈로그·관리자 화면·도움말 가드가
    pydantic 필드 introspection에 의존해(D-129) 런타임 생성 키는 화면에도 검증에도 오르지 않는다.
    코퍼스 **추가**는 여기에 4필드를 더하는 1회 작업이고, **회전**은 코드를 건드리지 않는다.

    검색 파라미터(HyDE·top_k·threshold·rerank)는 **플랫폼 전속**이라 이 그룹에 없다(§3.3).
    채팅 라우팅(어떤 질의를 문서로 보낼지)은 `plans/127`이 `plans/125` 계약 위에 얹었다 —
    `chat_routing_enabled`가 켜졌을 때만 2단 조건부 처리기 `doc_query`가 분해 목록에 오른다.
    """

    enabled: bool = False                    # 기능 전체 옵트인(D-162 — off면 현행 비트 동일)

    # ── 접속 4종 × 컬렉션 (docs/32 §1) ──────────────────────────────
    # 전산관리규정 / 본부 전산관리매뉴얼
    hq_manual_endpoint: str = ""             # 엔드포인트 URL 전문(조립 금지 — 발급마다 다르다)
    hq_manual_token: str = ""                # x-openapi-token
    hq_manual_client_key: str = ""           # x-generative-ai-client
    hq_manual_retrieval_id: str = ""         # 자산 ID(가장 자주 바뀐다)
    # 아키텍처 설계문서
    arch_docs_endpoint: str = ""
    arch_docs_token: str = ""
    arch_docs_client_key: str = ""
    arch_docs_retrieval_id: str = ""

    # ── 엔진 예산·정책 ─────────────────────────────────────────────
    collections_file: str = ""               # 비면 config/rag_collections.yaml
    timeout: int = 12                        # 호출 1건 read 상한(초)
    total_timeout: int = 20                  # 벽시계 총상한(초, D-198 교훈)
    max_collections_per_turn: int = 2        # 우리가 정하는 유일한 "건수" 축
    max_doc_chars: int = 4000                # 문서 1건 본문 상한(절단 시 고지)
    max_context_chars: int = 24000           # 근거 총 문자 예산
    answer_max_chars: int = 1200             # 서술 길이 상한(지연 ∝ 출력 길이)
    cache_ttl: int = 300                     # 0이면 캐시 비활성
    # 모든 대상 문서군이 0건이면 검색을 다시 하는 횟수(plans/141 W2 · D-314). 플랫폼 HyDE 가상 답변이
    # 호출마다 달라 같은 요청이 회차마다 0건/정상으로 갈렸다(본부 매뉴얼 24% 0건 실측). 오류·타임아웃·
    # 자산 폐기·PII 차단은 재시도하지 않는다. **기본 1 = 「신규 플래그 기본 off」(plans/80 §5.4-③ ·
    # D-162)의 명시 예외**(사용자 확정 G-3 (a)): 실측 결함 교정이고, 동작이 달라지는 경우는 「모든
    # 대상 문서군 0건」뿐이며, 0이면 종전 동작과 같다.
    empty_retries: int = 1
    doc_url_base: str = ""                   # 비면 링크 미노출(url이 상대 경로다)
    # 채팅 라우팅 스위치(plans/127 G-4 (b)) — 켜면 2단 분해가 문서 질의를 `doc_query`로 보낸다
    # (`enabled`와 둘 다 켜져야 한다 · 기본 off). 분해 프롬프트 접두를 바꾸는 값이라 기동 시 1회
    # 해석한다(`RELOADABLE_KEYS` 미등재 = 재시작 반영). T-4 명시 접두 스위치를 이것으로 교체했다
    # (플래그 순증 0 — D-162 주의 · D-251 ⑥과의 관계는 결정 기록 참조).
    chat_routing_enabled: bool = False
    # 민감 문서군(`sensitive: true`)을 열 수 있는 비관리자 사용자(쉼표 구분).
    # **빈 값 = 아무도 없음**이다(민감 자료의 기본값은 닫힘 · D-232 정합 · W5).
    sensitive_allowed_users: str = ""

    model_config = {"env_prefix": "RAG_", "env_file": ".env", "extra": "ignore"}


class ResultVizConfig(BaseSettings):
    """조회 결과 그래프 보기 · 칼럼 용어 사전 · 값 해석 (plans/160 · D-327).

    둘 다 **기본 off**(D-162) — 끄면 응답 `chart`는 None이고 해당 API는 404라 현행과 같다.
    기동 시 1회 해석한다(`RELOADABLE_KEYS` 미등재 = 재시작 반영).
    """

    # 그래프 보기 — 응답 `chart` 신호 · `GET /query/{id}/chart`
    chart_enabled: bool = False
    # 칼럼 용어 사전 · 값 해석 — 격자 머리글·셀 링크 · `/glossary` · `/interpret`
    explain_enabled: bool = False

    model_config = {"env_prefix": "RESULT_VIZ_", "env_file": ".env", "extra": "ignore"}


class PortalHelpConfig(BaseSettings):
    """포탈 기능 도움말 `portal_help` 설정 (plans/142 · D-315).

    켜져 있고 매뉴얼 색인(`src/static/manual/help_index.json` — 매뉴얼 빌드 산출물)이 성립할 때만
    1·2단에 처리기·분해 절·1단 도구·명시 명령(③.9)·`general_inference` 되돌림(G-5 (b))이 붙는다.
    색인이 없거나 손상이면 켜도 비활성이고 기동 로그 1줄로 사유를 남긴다(침묵 금지).
    """

    # **기본 on = 「신규 플래그 기본 off」(plans/80 §5.4-③ · D-162)의 명시 예외** — 사용자 확정
    # 2026-10-13 · plans/142 G-7 · D-315. 근거: 매뉴얼은 무인증 공개 자료(D-252 ①)이고 데이터 소스가
    # 아니며, 켠 상태가 사용법·포탈 기능 질문 밖의 기존 질의를 바꾸지 않는지 회귀로 확인한다.
    # 분해 프롬프트 접두·1단 도구 목록을 바꾸는 값이라 기동 시 1회 해석한다(재시작 반영).
    enabled: bool = True

    model_config = {"env_prefix": "PORTAL_HELP_", "env_file": ".env", "extra": "ignore"}
