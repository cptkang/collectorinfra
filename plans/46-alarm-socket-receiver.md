# Plan 46: 외부 알람 소켓 수신 → LLM 분석 → 메시지 발송 기능 구현

> **작성일**: 2026-06-02
> **관련 Plan**: Plan 44 (alarm_query 의도 분리), Plan 45 (알람 심각도 0 해소)
> **상태**: 설계 완료 / 구현 대기

---

## 1. 목표

외부 알람 시스템(폴스타 모니터링 등)으로부터 TCP 소켓 통신으로 알람 이벤트를 수신하고,
이를 LLM으로 분석하여 운영자에게 자동 메시지를 발송하는 기능을 추가한다.

**발송 채널**

| 구분 | 채널 | 비고 |
|------|------|------|
| **현재** | worKB (KB One 사내메신저 쪽지) | 내부망에서 유일하게 사용 가능한 채널 |
| 추후 | 사내 Email | 사내 메일 서버(SMTP) 연동 — 외부망 없이 사용 가능 |
| 추후 | Generic Webhook | 내부 시스템 간 연동용 HTTP 콜백 (사용 방법은 Section 13 참고) |

> **Slack 제외 이유**: 외부망 접근이 불가능한 환경이므로 Slack Webhook은 지원하지 않는다.

현재는 worKB 단일 채널로 구현하되, 추후 복수 채널 동시 발송이 가능하도록 채널 추상화 구조로 설계한다.

---

## 2. 권장 아키텍처

### 2.1 구조 다이어그램

소켓 수신 서버(`alarm_server/`)를 에이전트 서버(`src/`)와 **별도 독립 프로세스**로 분리한다.
이는 기존 DB 접근 계층을 `mcp_server/`로 분리한 것과 동일한 설계 원칙이다.

```
[폴스타 알람 시스템]
       │ TCP socket (지속 연결)
       ▼
┌─────────────────────────────────────────┐
│  alarm_server/  (독립 프로세스)          │
│  python -m alarm_server                 │
│  - asyncio TCP 서버                     │
│  - 페이로드 파싱·정규화 (단일행 JSON)  │
│  - 연결 끊김 자동 재연결                │
│  - Redis Stream XADD                    │
└──────────────────┬──────────────────────┘
                   │ Redis Stream "alarm:raw" (프로세스 간 결합 지점)
                   ▼
       [Redis Stream: "alarm:raw"]
                   │ XREAD (consumer group)
                   ▼
┌─────────────────────────────────────────┐
│  Agent Server  (기존 FastAPI 프로세스)   │
│  src/alarm/ AlarmWorker                 │
│  - 중복 알람 제거 (alarm_id TTL 기반)   │
│  - 심각도 임계값 필터링                 │
│  - AlarmAnalysisGraph 호출              │
└──────────────────┬──────────────────────┘
                   │ graph.ainvoke()
                   ▼
┌─────────────────────────────────────────┐
│  AlarmAnalysisGraph                     │
│  (기존 쿼리 그래프와 독립된 서브그래프) │
│                                         │
│  alarm_analyzer ──────▶ notifier        │
│  (LLM: 심각도 분류,      (채널 선택,    │
│   원인 요약,              메시지 포맷,  │
│   대응 권고)              발송)         │
└─────────────────────────────────────────┘
                   │
                   ▼
     worKB (사내메신저 쪽지)  ← 현재 구현  [추후: Slack / 이메일 / Webhook]
```

### 2.2 alarm_server를 독립 프로세스로 분리하는 이유

`mcp_server/`가 DB 접근을 에이전트 서버로부터 분리한 것과 동일한 원칙이 여기에도 적용된다.

| 기준 | 통합(src/ 내부) | 분리(alarm_server/) |
|------|----------------|---------------------|
| 에이전트 재시작 시 알람 유실 | 유실 | Redis Stream에 보존 |
| 소켓 수신과 LLM 처리의 라이프사이클 | 결합 (하나가 죽으면 둘 다 중단) | 독립 (각자 재시작 가능) |
| 배포 단위 | 항상 함께 배포 | 독립 배포·스케일링 |
| 설정 관리 | `.env` 혼재 | 역할별 설정 파일 분리 |
| 로그·모니터링 | 에이전트 로그에 혼재 | 알람 수신 전용 로그 |

### 2.3 기존 인프라 재사용 포인트

| 기존 자산 | 재사용 방식 |
|-----------|------------|
| Redis (이미 배포) | `alarm:raw` Stream — 두 프로세스의 결합 지점 |
| `src/llm.py` LLM 팩토리 | AlarmWorker 내 분석 노드에서 동일 팩토리 사용 |
| FastAPI lifespan | AlarmWorker(소비자)만 등록 — 소켓 서버는 별도 프로세스 |
| `AuditService` | 알람 처리 이력 감사 로그 기록 |

### 2.4 기존 LangGraph 그래프를 재사용하지 않는 이유

| 항목 | 기존 쿼리 그래프 | 알람 분석 그래프 |
|------|----------------|----------------|
| 트리거 | 사용자 HTTP 요청 | Redis Stream 이벤트 |
| 상태(`AgentState`) | `user_query`, `schema_info`, `generated_sql` 등 16개 필드 | `alarm_event`, `analysis_result` |
| 흐름 | 16-노드 SQL 파이프라인 | 2-노드 분석→발송 |
| 출력 | 자연어 응답 + 파일 | 알림 메시지 |

---

## 3. 디렉토리 구조

```
alarm_server/                          # 외부 알람 수신 전용 서버 (mcp_server 유사 구조)
├── __init__.py
├── __main__.py                        # 진입점: python -m alarm_server
├── config.py                          # AlarmServerConfig (소켓·Redis 설정)
├── base_receiver.py                   # BaseReceiver 추상 클래스 — Redis 발행 공통 로직
├── tcp_receiver.py                    # TcpReceiver: asyncio TCP 소켓 수신 (현재 구현)
│                                      #   추후 추가: http_receiver.py (HTTP 웹훅 수신)
└── alarm_server.env                   # 소켓 서버 전용 환경변수 (.gitignore 대상)

src/alarm/                             # 에이전트 서버 내 알람 분석·발송
├── domain/
│   └── alarm.py                       # AlarmEvent, AlarmAnalysisResult dataclass
├── infrastructure/
│   └── redis_queue.py                 # Redis Stream XREAD 소비 전용
├── application/
│   ├── alarm_worker.py                # 큐 소비 → 그래프 호출 (중복 제거, 필터링)
│   └── nodes/
│       ├── alarm_analyzer.py          # LLM 분석 노드
│       └── alarm_notifier.py          # 알림 발송 노드 (Slack, worKB, 등)
├── orchestration/
│   └── alarm_graph.py                 # 경량 LangGraph 서브그래프
└── prompts/
    └── alarm_analyzer.py              # 알람 분석 시스템/유저 프롬프트
```

**`mcp_server/`와 `alarm_server/`의 역할 비교**:

| 항목 | `mcp_server/` | `alarm_server/` |
|------|--------------|-----------------|
| 역할 | DB 쿼리 게이트웨이 | 폴스타 알람 수신 게이트웨이 |
| 통신 방향 | 에이전트 → MCP → DB (pull) | 폴스타 → 소켓 서버 → Redis (push) |
| 에이전트와 결합 지점 | MCP SSE (`/sse` 엔드포인트) | Redis Stream (`alarm:raw`) |
| 설정 파일 | `mcp_server/config.toml` | `alarm_server/alarm_server.env` |

---

## 4. 도메인 모델

### 4.1 `src/alarm/domain/alarm.py`

`AlarmEvent` 필드는 실제 폴스타 전송 메시지 포맷(Section 6 참고)과 1:1 대응하도록 설계한다.

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

@dataclass
class AlarmEvent:
    # --- 식별자 ---
    db_id: str                          # 상수 (직접 기입) — 폴스타 인스턴스 식별자, DB의 db_id와 매핑
    server_name: str                    # ${platformName}  — 폴스타에 등록된 서버명, DB의 server_name과 매핑
    # --- 네트워크 정보 ---
    hostname: str                       # ${hostname}      — 호스트네임
    ip_address: str                     # ${ipAddress}     — IP 주소
    resource_ancestry: str              # ${resourceAncestry} — 폴스타 트리 전체 경로
    # --- 알람 상세 ---
    alarm_id: str                       # ${alarmId}       — 중복 제거 키
    severity: int                       # ${severity}      — 0=해소, 1=주의, 2=경고, 3=심각
    alarm_status: str                   # ${alarmStatus}   — '발생' / '해소'
    resource_type: str                  # ${resourceType}  — 'server.Server' 등
    alarm_name: str                     # ${alarmName}     — 알람 이름
    alarm_time: datetime                # ${formatAlarmDate('yyyyMMddHHmmss')} 파싱
    conditions: str                     # ${conditions}    — 발생/해소 임계 조건 정의
    condition_log: str                  # ${conditionLog}  — 이 알람이 울린 실제 값
    # --- 파생 필드 ---
    is_clear: bool = False              # alarm_status == '해소' 또는 severity == 0
    raw_payload: dict = field(default_factory=dict)  # 원본 JSON dict 보존

@dataclass
class AlarmAnalysisResult:
    alarm_event: AlarmEvent
    severity_label: str                 # "심각" / "경고" / "주의" / "해소"
    summary: str                        # LLM 생성 요약 (1~2문장)
    probable_cause: str                 # 추정 원인
    recommended_action: str             # 권고 조치
    notification_channels: list[str]    # ["workb"] 등 복수 채널
    notifications_sent: dict[str, bool] = field(default_factory=dict)  # 채널별 발송 결과
    error: Optional[str] = None
```

---

## 5. 구현 단계

### Step 1: alarm_server 구현

#### 1-1. `alarm_server/config.py`

에이전트 서버의 `AppConfig`와 독립된 전용 설정 클래스.

```python
from pydantic_settings import BaseSettings

class AlarmServerConfig(BaseSettings):
    """알람 소켓 수신 서버 전용 설정."""

    socket_host: str = "0.0.0.0"
    socket_port: int = 9100
    # Redis — 에이전트 서버의 동일 Redis를 가리켜야 함
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""
    stream_key: str = "alarm:raw"
    log_level: str = "INFO"

    model_config = {
        "env_prefix": "ALARM_SERVER_",
        "env_file": ["alarm_server.env", ".env"],  # 전용 env 우선, 없으면 공통 .env
        "extra": "ignore",
    }
```

**`alarm_server.env` 예시**:

```ini
# 알람 소켓 수신 서버 전용 설정
# 에이전트 서버의 .env와 독립 관리
ALARM_SERVER_SOCKET_HOST=0.0.0.0
ALARM_SERVER_SOCKET_PORT=9100
ALARM_SERVER_REDIS_HOST=localhost
ALARM_SERVER_REDIS_PORT=6379
ALARM_SERVER_REDIS_DB=0
# ALARM_SERVER_REDIS_PASSWORD=<비밀번호>
ALARM_SERVER_STREAM_KEY=alarm:raw
ALARM_SERVER_LOG_LEVEL=INFO
```

#### 1-2. `alarm_server/base_receiver.py`

수신 방식(TCP·HTTP 등)에 무관한 공통 인터페이스와 Redis 발행 로직을 제공한다.
추후 `HttpReceiver`를 추가할 때 `BaseReceiver`만 상속하면 발행 코드 중복 없이 구현 가능하다.

```python
import abc
import json
import logging
import redis.asyncio as aioredis
from alarm_server.config import AlarmServerConfig

logger = logging.getLogger(__name__)


class BaseReceiver(abc.ABC):
    """알람 수신기 추상 기반 클래스.

    수신 방식(TCP / HTTP 웹훅 등)이 달라도 Redis Stream 발행 인터페이스를 공유한다.
    """

    def __init__(self, config: AlarmServerConfig) -> None:
        self._config = config
        self._redis: aioredis.Redis | None = None

    async def _init_redis(self) -> None:
        self._redis = aioredis.from_url(
            f"redis://{self._config.redis_host}:{self._config.redis_port}",
            password=self._config.redis_password or None,
            db=self._config.redis_db,
        )

    async def _publish(self, payload: dict) -> None:
        """파싱된 알람을 Redis Stream에 발행한다."""
        await self._redis.xadd(
            self._config.stream_key,
            {"data": json.dumps(payload, ensure_ascii=False)},
        )
        logger.debug("알람 발행 완료: alarmId=%s severity=%s alarmStatus=%s",
                     payload.get("alarmId"), payload.get("severity"),
                     payload.get("alarmStatus"))

    @abc.abstractmethod
    async def start(self) -> None:
        """수신 루프를 시작한다 (서브클래스에서 구현)."""
        ...
```

#### 1-3. `alarm_server/tcp_receiver.py`

TCP 소켓 수신 구체 구현. 폴스타 → TCP 지속 연결 → 단일행 JSON 파싱 → Redis XADD.

```python
import asyncio
import json
import logging
from alarm_server.base_receiver import BaseReceiver

logger = logging.getLogger(__name__)


class TcpReceiver(BaseReceiver):
    """폴스타 알람 시스템으로부터 TCP 소켓 연결을 유지하며 알람을 수신한다."""

    async def start(self) -> None:
        await self._init_redis()
        server = await asyncio.start_server(
            self._handle_connection,
            self._config.socket_host,
            self._config.socket_port,
        )
        logger.info("TCP 알람 수신 서버 시작: %s:%s",
                    self._config.socket_host, self._config.socket_port)
        async with server:
            await server.serve_forever()

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        logger.info("알람 연결 수립: %s", peer)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                payload = self._parse(line)
                if payload:
                    await self._publish(payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("알람 수신 오류 (%s)", peer)
        finally:
            writer.close()
            logger.info("알람 연결 종료: %s", peer)

    def _parse(self, raw: bytes) -> dict | None:
        """수신 바이트를 dict으로 변환한다 (Section 6.1 단일행 JSON 기준)."""
        try:
            return json.loads(raw.decode("utf-8").strip())
        except Exception:
            logger.warning("알람 페이로드 파싱 실패: %r", raw[:200])
            return None
```

> **추후 HTTP 웹훅 수신 확장 시**: `alarm_server/http_receiver.py`에 `HttpReceiver(BaseReceiver)`를 구현하고,
> `AlarmServerConfig`에 `receiver_type: str = "tcp"` 필드를 추가해 `__main__.py`에서 분기한다.

#### 1-4. `alarm_server/__main__.py`

```python
import asyncio
import logging
from alarm_server.config import AlarmServerConfig
from alarm_server.tcp_receiver import TcpReceiver

def main() -> None:
    config = AlarmServerConfig()
    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    asyncio.run(TcpReceiver(config).start())

if __name__ == "__main__":
    main()
```

**기동 방법** (`mcp_server`와 동일한 패턴):

```bash
python -m alarm_server
```

---

### Step 2: 에이전트 서버 Config 추가 (`src/config.py`)

에이전트 서버는 Redis Stream **소비** 및 **알림 발송**만 담당한다.
소켓 수신 설정은 `alarm_server/config.py`에서 관리하므로 여기서는 제외한다.

```python
class AlarmConfig(BaseSettings):
    """에이전트 서버의 알람 분석·발송 설정."""

    enabled: bool = False
    redis_stream_key: str = "alarm:raw"
    redis_consumer_group: str = "alarm-workers"
    min_severity: int = 2                 # 처리할 최소 심각도 (1=주의, 2=경고, 3=심각)
    dedup_ttl_seconds: int = 300          # 중복 알람 억제 TTL (초)
    # 현재 지원 채널: workb만 사용 가능.
    # 추후 "slack,workb" 등 복수 지정 가능하도록 CSV 구조를 유지한다.
    notification_channels_csv: str = "workb"

    model_config = {"env_prefix": "ALARM_", "env_file": ".env", "extra": "ignore"}

    def get_notification_channels(self) -> list[str]:
        return [c.strip() for c in self.notification_channels_csv.split(",") if c.strip()]


class WorkbConfig(BaseSettings):
    """KB One 클라우드 포탈 worKB(사내메신저) 쪽지 발송 설정."""

    base_url: str = ""                    # 예: http://kbone-portal.internal:28080
    bearer_token: str = ""               # Bearer 인증 토큰
    system_div: str = ""                 # 시스템 구분자 (worKB 관리자로부터 발급)
    send_id: str = ""                    # 발송자 사번
    user_ids_csv: str = ""              # 기본 수신자 사번 목록 (쉼표 구분)
    alias: str = "[인프라알람]"          # 쪽지 제목 접두어 (실제 제목 = alias + msgTitle)
    # 심각도별 수신자 오버라이드 (비어있으면 user_ids_csv 공통 사용)
    critical_user_ids_csv: str = ""      # 심각도 3 전용 수신자
    warning_user_ids_csv: str = ""       # 심각도 2 전용 수신자
    timeout_seconds: int = 10

    model_config = {"env_prefix": "WORKB_", "env_file": [".env", ".encenv"], "extra": "ignore"}

    def get_user_ids(self, severity: int) -> str:
        """심각도에 맞는 수신자 목록을 반환한다."""
        if severity == 3 and self.critical_user_ids_csv:
            return self.critical_user_ids_csv
        if severity == 2 and self.warning_user_ids_csv:
            return self.warning_user_ids_csv
        return self.user_ids_csv
```

**`AppConfig`에 필드 추가**:
```python
alarm: AlarmConfig = AlarmConfig()
workb: WorkbConfig = WorkbConfig()
```

**에이전트 서버 `.env.example` 추가 항목**:

```ini
# ── 알람 분석·발송 설정 (에이전트 서버) ─────────
ALARM_ENABLED=false
ALARM_REDIS_STREAM_KEY=alarm:raw
ALARM_MIN_SEVERITY=2
ALARM_DEDUP_TTL_SECONDS=300
# 현재 지원 채널: workb (추후 slack 등 추가 시 "workb,slack" 형태로 지정)
ALARM_NOTIFICATION_CHANNELS_CSV=workb

# ── worKB 사내메신저 설정 (.encenv 권장) ─────────
WORKB_BASE_URL=http://kbone-portal.internal:28080
WORKB_BEARER_TOKEN=<발급된 Bearer 토큰>
WORKB_SYSTEM_DIV=<시스템 구분자>
WORKB_SEND_ID=<발송자 사번>
WORKB_USER_IDS_CSV=10001,10002,10003
WORKB_ALIAS=[인프라알람]
WORKB_CRITICAL_USER_IDS_CSV=10001,10002
WORKB_WARNING_USER_IDS_CSV=10001
```

---

### Step 3: 알람 분석 프롬프트 (`src/alarm/prompts/alarm_analyzer.py`)

> Section 6.5 참고 — 폴스타 템플릿 변수와 대응하는 최종 프롬프트 전문이 기술되어 있다.

`ALARM_ANALYZER_SYSTEM_PROMPT`와 `ALARM_ANALYZER_USER_TEMPLATE` 두 상수를 정의하며,
유저 템플릿은 `alarm_name`, `alarm_description`, `alarm_definition`, `hostname`,
`resource_name`, `resource_description`, `resource_type`, `severity`, `severity_label`,
`condition_log` 10개 필드를 받는다.

---

### Step 4: 알람 분석 그래프 (`src/alarm/orchestration/alarm_graph.py`)

기존 쿼리 그래프와 독립된 2-노드 경량 LangGraph 서브그래프.

```python
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END
from src.alarm.domain.alarm import AlarmEvent, AlarmAnalysisResult

class AlarmState(TypedDict):
    alarm_event: AlarmEvent
    analysis_result: Optional[AlarmAnalysisResult]
    error: Optional[str]

def build_alarm_graph(config):
    from src.alarm.application.nodes.alarm_analyzer import alarm_analyzer_node
    from src.alarm.application.nodes.alarm_notifier import alarm_notifier_node

    builder = StateGraph(AlarmState)
    builder.add_node("alarm_analyzer", alarm_analyzer_node)
    builder.add_node("alarm_notifier", alarm_notifier_node)
    builder.set_entry_point("alarm_analyzer")
    builder.add_edge("alarm_analyzer", "alarm_notifier")
    builder.add_edge("alarm_notifier", END)
    return builder.compile()
```

---

### Step 5: 애플리케이션 노드

#### 5-1. `src/alarm/application/nodes/alarm_analyzer.py`

```python
import json
import logging
from src.llm import get_llm
from src.alarm.prompts.alarm_analyzer import (
    ALARM_ANALYZER_SYSTEM_PROMPT, ALARM_ANALYZER_USER_TEMPLATE
)
from src.alarm.domain.alarm import AlarmAnalysisResult

logger = logging.getLogger(__name__)
_SEVERITY_LABELS = {1: "주의", 2: "경고", 3: "심각"}

async def alarm_analyzer_node(state: dict, config: dict) -> dict:
    event = state["alarm_event"]
    cfg = config["configurable"]["app_config"]
    llm = get_llm(cfg)

    severity_label = _SEVERITY_LABELS.get(event.severity, "해소" if event.is_clear else "알 수 없음")
    user_msg = ALARM_ANALYZER_USER_TEMPLATE.format(
        db_id=event.db_id,
        server_name=event.server_name,
        hostname=event.hostname,
        ip_address=event.ip_address,
        resource_ancestry=event.resource_ancestry,
        resource_type=event.resource_type,
        alarm_name=event.alarm_name,
        alarm_id=event.alarm_id,
        severity=event.severity,
        severity_label=severity_label,
        alarm_status=event.alarm_status,
        alarm_time=event.alarm_time.strftime("%Y-%m-%d %H:%M:%S"),
        conditions=event.conditions,
        condition_log=event.condition_log,
    )
    try:
        response = await llm.ainvoke([
            {"role": "system", "content": ALARM_ANALYZER_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ])
        parsed = json.loads(response.content)
        result = AlarmAnalysisResult(
            alarm_event=event,
            severity_label=parsed["severity_label"],
            summary=parsed["summary"],
            probable_cause=parsed["probable_cause"],
            recommended_action=parsed["recommended_action"],
            notification_channels=cfg.alarm.get_notification_channels(),
        )
        return {"analysis_result": result}
    except Exception as e:
        logger.exception("알람 LLM 분석 실패: alarm_id=%s", event.alarm_id)
        return {"error": str(e)}
```

#### 5-2. `src/alarm/application/nodes/alarm_notifier.py`

복수 채널에 순차 발송하며, 각 채널의 성공/실패를 `notifications_sent`에 개별 기록한다.

```python
import logging
import httpx
from src.alarm.domain.alarm import AlarmAnalysisResult

logger = logging.getLogger(__name__)

async def alarm_notifier_node(state: dict, config: dict) -> dict:
    result: AlarmAnalysisResult = state.get("analysis_result")
    if not result or state.get("error"):
        return {}

    cfg = config["configurable"]["app_config"]

    for channel in result.notification_channels:
        try:
            if channel == "workb":
                await _send_workb(cfg.workb, result)
            else:
                # 현재 worKB 외 채널 미지원 — 추후 Slack/email 채널 추가 시 여기에 분기 추가
                logger.warning("지원하지 않는 알림 채널 무시: %s (현재 지원: workb)", channel)
                result.notifications_sent[channel] = False
                continue
            result.notifications_sent[channel] = True
            logger.info("알람 알림 발송 완료: alarm_id=%s channel=%s",
                        result.alarm_event.alarm_id, channel)
        except Exception:
            result.notifications_sent[channel] = False
            logger.exception("알람 알림 발송 실패: alarm_id=%s channel=%s",
                             result.alarm_event.alarm_id, channel)

    return {"analysis_result": result}


async def _send_workb(workb_cfg, result: AlarmAnalysisResult) -> None:
    """worKB 사내메신저 쪽지 발송.

    실제 쪽지 제목: "{alias} {msgTitle}" 형태로 사용자 쪽지창에 표시된다.
    """
    ev = result.alarm_event
    msg_title = f"[{result.severity_label}] {ev.resource_name} ({ev.hostname})"
    msg_body = (
        f"알람명: {ev.alarm_name}\n"
        f"설명: {ev.alarm_description}\n"
        f"자원: {ev.resource_name} ({ev.resource_type})\n"
        f"컨디션: {ev.condition_log}\n\n"
        f"요약: {result.summary}\n"
        f"원인: {result.probable_cause}\n"
        f"권고 조치: {result.recommended_action}"
    )
    payload = {
        "systemDiv": workb_cfg.system_div,
        "msgTitle": msg_title,
        "msgBody": msg_body,
        "sendId": workb_cfg.send_id,
        "userIds": workb_cfg.get_user_ids(ev.severity),
        "alias": workb_cfg.alias,
    }
    headers = {
        "Authorization": f"Bearer {workb_cfg.bearer_token}",
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
    }
    url = f"{workb_cfg.base_url.rstrip('/')}/api/sendWorkbMsg"
    async with httpx.AsyncClient(timeout=workb_cfg.timeout_seconds) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()

```

---

### Step 6: AlarmWorker (`src/alarm/application/alarm_worker.py`)

```python
import asyncio
import json
import logging
from datetime import datetime
import redis.asyncio as aioredis
from src.alarm.domain.alarm import AlarmEvent
from src.alarm.orchestration.alarm_graph import build_alarm_graph

logger = logging.getLogger(__name__)

class AlarmWorker:
    """Redis Stream에서 알람을 소비하여 분석 그래프를 실행한다."""

    def __init__(self, config):
        self._config = config
        self._graph = None

    async def run(self) -> None:
        if not self._config.alarm.enabled:
            logger.info("알람 워커 비활성 (ALARM_ENABLED=false)")
            return

        r = aioredis.from_url(
            f"redis://{self._config.redis.host}:{self._config.redis.port}",
            password=self._config.redis.password or None,
            db=self._config.redis.db,
        )
        stream_key = self._config.alarm.redis_stream_key
        group = self._config.alarm.redis_consumer_group

        try:
            await r.xgroup_create(stream_key, group, id="0", mkstream=True)
        except Exception:
            pass  # 이미 존재

        self._graph = build_alarm_graph(self._config)
        dedup: dict[str, float] = {}
        logger.info("알람 워커 시작 (stream=%s group=%s)", stream_key, group)

        while True:
            try:
                results = await r.xreadgroup(group, "worker-1", {stream_key: ">"}, count=10, block=2000)
            except asyncio.CancelledError:
                break
            if not results:
                continue
            for _, messages in results:
                for msg_id, fields in messages:
                    await self._process(r, stream_key, group, msg_id, fields, dedup)

        await r.aclose()

    async def _process(self, r, stream_key, group, msg_id, fields, dedup) -> None:
        try:
            payload = json.loads(fields[b"data"])
            alarm_time_str = payload.get("alarmTime", "")
            try:
                alarm_time = datetime.strptime(alarm_time_str, "%Y%m%d%H%M%S")
            except ValueError:
                alarm_time = datetime.now()

            alarm_status = payload.get("alarmStatus", "")
            severity = int(payload["severity"])
            is_clear = (alarm_status == "해소" or severity == 0)

            event = AlarmEvent(
                db_id=payload.get("dbId", ""),
                server_name=payload.get("serverName", ""),
                hostname=payload.get("hostname", ""),
                ip_address=payload.get("ipAddress", ""),
                resource_ancestry=payload.get("resourceAncestry", ""),
                alarm_id=str(payload["alarmId"]),
                severity=severity,
                alarm_status=alarm_status,
                resource_type=payload.get("resourceType", ""),
                alarm_name=payload.get("alarmName", ""),
                alarm_time=alarm_time,
                conditions=payload.get("conditions", ""),
                condition_log=payload.get("conditionLog", ""),
                is_clear=is_clear,
                raw_payload=payload,
            )
            if self._is_duplicate(event, dedup):
                await r.xack(stream_key, group, msg_id)
                return
            if event.severity < self._config.alarm.min_severity:
                await r.xack(stream_key, group, msg_id)
                return

            await self._graph.ainvoke(
                {"alarm_event": event, "analysis_result": None, "error": None},
                config={"configurable": {"app_config": self._config}},
            )
        except Exception:
            logger.exception("알람 처리 실패: msg_id=%s", msg_id)
        finally:
            await r.xack(stream_key, group, msg_id)

    def _is_duplicate(self, event: AlarmEvent, dedup: dict) -> bool:
        import time
        now = time.time()
        ttl = self._config.alarm.dedup_ttl_seconds
        last = dedup.get(event.alarm_id)
        if last and now - last < ttl:
            return True
        dedup[event.alarm_id] = now
        for k in [k for k, v in dedup.items() if now - v >= ttl]:
            del dedup[k]
        return False
```

---

### Step 7: FastAPI lifespan 통합 (`src/api/server.py`)

소켓 수신은 `alarm_server/`가 담당하므로 lifespan에는 **AlarmWorker(소비자)만** 등록한다.

**변경 위치**: `server.py` `yield` 바로 앞

```python
# 알람 분석 워커 시작 (ALARM_ENABLED=true 인 경우에만)
alarm_worker_task = None
if config.alarm.enabled:
    from src.alarm.application.alarm_worker import AlarmWorker
    alarm_worker_task = asyncio.create_task(AlarmWorker(config).run())
    logger.info("알람 분석 워커 시작 (stream=%s)", config.alarm.redis_stream_key)

yield

# 종료 시 알람 워커 정리
if alarm_worker_task:
    alarm_worker_task.cancel()
    try:
        await alarm_worker_task
    except asyncio.CancelledError:
        pass
```

---

### Step 8: 관리 API (선택, `src/api/routes/alarm.py`)

| 엔드포인트 | 메서드 | 설명 |
|-----------|--------|------|
| `/api/v1/alarm/status` | GET | 워커 상태, 큐 길이 조회 |
| `/api/v1/alarm/test` | POST | 테스트 알람 수동 주입 (Redis Stream에 직접 XADD) |
| `/api/v1/alarm/config` | GET | 현재 알람 설정 조회 |

---

## 6. 폴스타 메시지 포맷 및 파싱 설계

폴스타는 알람 발생/해소 시 TCP 액션으로 에이전트 서버에 알람 메시지를 전송한다.
폴스타 템플릿은 자유롭게 지정할 수 있으므로, **단일행 JSON 형식**을 채택한다.

### 6.1 폴스타 등록 템플릿 (단일행 JSON)

폴스타 알람 액션의 메시지 본문에 아래 형식으로 등록한다.
`dbId` 값은 폴스타 인스턴스마다 **상수로 직접 기입**한다 (템플릿 변수 아님).

**템플릿 형식:**
```
{"dbId":"<인스턴스_DB_ID>","serverName":"${platformName}","hostname":"${hostname}","ipAddress":"${ipAddress}","resourceAncestry":"${resourceAncestry}","alarmId":"${alarmId}","severity":${severity},"alarmStatus":"${alarmStatus}","resourceType":"${resourceType}","resourceName":"${resourceName}","alarmName":"${alarmName}","alarmTime":"${formatAlarmDate('yyyyMMddHHmmss')}","conditions":"${conditions}","conditionLog":"${conditionLog}"}
```

**인스턴스별 등록 예시:**

| 폴스타 인스턴스 | 등록 템플릿의 `dbId` 값 |
|---------------|------------------------|
| 김포 폴스타 | `"dbId":"polestar_cm_gp"` |
| 본사 폴스타 | `"dbId":"polestar"` |

**폴스타 템플릿 변수 → `AlarmEvent` 필드 매핑:**

| 원천 | JSON 키 | `AlarmEvent` 필드 | 설명 |
|------|---------|-------------------|------|
| 상수 (직접 기입) | `dbId` | `db_id` | 폴스타 인스턴스 식별자 — DB의 `db_id`와 매핑 |
| `${platformName}` | `serverName` | `server_name` | 폴스타에 등록된 서버 이름 — DB의 `server_name`과 매핑 |
| `${hostname}` | `hostname` | `hostname` | 호스트네임 |
| `${ipAddress}` | `ipAddress` | `ip_address` | IP 주소 |
| `${resourceAncestry}` | `resourceAncestry` | `resource_ancestry` | 폴스타 트리 전체 경로 |
| `${alarmId}` | `alarmId` | `alarm_id` | 알람 고유 ID (중복 제거 키) |
| `${severity}` | `severity` | `severity` | 0=해소, 1=주의, 2=경고, 3=심각 |
| `${alarmStatus}` | `alarmStatus` | `alarm_status` | `발생` / `해소` |
| `${resourceType}` | `resourceType` | `resource_type` | `server.Server`, `server.Cpus` 등 |
| `${resourceName}` | `resourceName` | `resource_name` | 자원 이름 |
| `${alarmName}` | `alarmName` | `alarm_name` | 알람 이름 |
| `${formatAlarmDate('yyyyMMddHHmmss')}` | `alarmTime` | `alarm_time` | 알람 일시 → `datetime` 변환 |
| `${conditions}` | `conditions` | `conditions` | 발생/해소 조건 정의 |
| `${conditionLog}` | `conditionLog` | `condition_log` | 이 알람이 울리게 된 조건 값 |
| — | — | `is_clear` | 파생: `alarm_status == '해소'` 또는 `severity == 0` |
| — | — | `raw_payload` | 원본 JSON dict 보존 |

### 6.2 메시지 예시 (발생 알람 — 김포 폴스타)

폴스타가 템플릿 변수를 렌더링한 결과 (`dbId`는 상수로 기입):

```json
{"dbId":"polestar_cm_gp","serverName":"svr-infra-001","hostname":"svr-infra-001.internal","ipAddress":"10.1.2.3","resourceAncestry":"/Servers/Infrastructure/svr-infra-001/Cpus","alarmId":"1234567","severity":3,"alarmStatus":"발생","resourceType":"server.Cpus","alarmName":"CPU 사용률 임계 초과","alarmTime":"20260602143520","conditions":"사용률 Threashold [TROUBLE (> 90.0 %), ATTENTION (>80.0 %), CLEAR (< 70.0 %)]","conditionLog":"사용률 Threashold [86.1 % (> 80.0 %)]"}
```

### 6.3 메시지 예시 (해소 알람 — 김포 폴스타)

```json
{"dbId":"polestar_cm_gp","serverName":"svr-infra-001","hostname":"svr-infra-001.internal","ipAddress":"10.1.2.3","resourceAncestry":"/Servers/Infrastructure/svr-infra-001/Cpus","alarmId":"1234567","severity":0,"alarmStatus":"해소","resourceType":"server.Cpus","alarmName":"CPU 사용률 임계 초과","alarmTime":"20260602145208","conditions":"사용률 Threashold [TROUBLE (> 90.0 %), ATTENTION (>80.0 %), CLEAR (< 70.0 %)]","conditionLog":"사용률 Threashold [65.2 % (< 70.0 %)]"}
```

- `severity = 0`, `alarmStatus = "해소"` → `is_clear = True`
- `conditions`는 발생·해소 조건이 모두 포함된 동일 정의
- `conditionLog`는 해소 시점의 실제 측정값 — 해소 조건(`CLEAR < 70.0 %`) 충족 확인 가능

### 6.4 파싱 구현 (`alarm_server/tcp_receiver.py`)

JSON 형식이므로 `_parse()`는 `json.loads()` 한 줄로 완료된다.
복잡한 정규식·앵커 탐색이 전혀 필요 없다.

```python
def _parse(self, raw: bytes) -> dict | None:
    try:
        return json.loads(raw.decode("utf-8").strip())
    except Exception:
        logger.warning("알람 페이로드 파싱 실패: %r", raw[:200])
        return None
```

> **JSON 이스케이프 안전성**: `conditions`·`conditionLog`·`alarmName` 값에 큰따옴표(`"`)나
> 백슬래시(`\`)가 포함되면 JSON 파싱이 깨진다. 폴스타의 알람 조건은 수치·괄호·퍼센트로만
> 구성되므로 실용적 위험은 낮다. 만약 문제가 발생하면 파이프(`|`) 구분 포맷으로 전환한다.

### 6.5 LLM 분석 프롬프트 (`src/alarm/prompts/alarm_analyzer.py`)

```python
ALARM_ANALYZER_SYSTEM_PROMPT = """당신은 인프라 모니터링 알람을 분석하는 전문가입니다.
주어진 알람 정보를 바탕으로 다음을 JSON으로 응답하세요:

{
    "severity_label": "심각" | "경고" | "주의" | "해소",
    "summary": "알람 요약 (1~2문장, 한국어, 장비명·알람명·심각도 포함)",
    "probable_cause": "추정 원인 (conditions와 conditionLog 근거, 1~2문장)",
    "recommended_action": "권고 조치 (구체적, 1~3문장)"
}

규칙:
- 심각도: 3=심각, 2=경고, 1=주의, 0=해소
- conditions(발생/해소 임계 정의)와 conditionLog(실제 측정값)를 원인 분석의 핵심 근거로 사용
- 해소 알람(alarmStatus=해소)인 경우 severity_label은 "해소"로 출력
- JSON 이외의 텍스트를 절대 출력하지 말 것
"""

ALARM_ANALYZER_USER_TEMPLATE = """알람 정보:
- DB: {db_id} / 서버: {server_name} ({hostname}, {ip_address})
- 자원 경로: {resource_ancestry}
- 자원 종류: {resource_type}
- 알람명: {alarm_name}
- 알람 ID: {alarm_id}
- 심각도: {severity} ({severity_label})
- 알람 상태: {alarm_status}
- 알람 일시: {alarm_time}
- 임계 조건: {conditions}
- 조건 로그: {condition_log}
"""
```

---

## 7. worKB 쪽지 발송 상세 설계

### 7.1 API 명세

| 항목 | 값 |
|------|-----|
| URL | `{WORKB_BASE_URL}/api/sendWorkbMsg` |
| Method | `POST` |
| Content-Type | `application/json; charset=utf-8` |

**Request Header**:
```json
{
  "Authorization": "Bearer <WORKB_BEARER_TOKEN>",
  "Content-Type": "application/json; charset=utf-8",
  "Accept": "application/json"
}
```

**Request Body**:
```json
{
  "systemDiv": "<WORKB_SYSTEM_DIV>",
  "msgTitle": "[심각] svr-infra-001 (svr-infra-001.internal)",
  "msgBody": "알람명: CPU 사용률 임계값 초과\n...\n요약: ...\n원인: ...\n권고 조치: ...",
  "sendId": "<WORKB_SEND_ID>",
  "userIds": "10001,10002,10003",
  "alias": "[인프라알람]"
}
```

> **실제 쪽지 제목**: `alias + " " + msgTitle` 형태로 표시됨
> 예: `[인프라알람] [심각] svr-infra-001 (svr-infra-001.internal)`

### 7.2 수신자(userIds) 선정 전략

| 단계 | 방식 | 구현 위치 |
|------|------|----------|
| **1단계 (즉시)** | 설정 파일 고정 수신자 (`WORKB_USER_IDS_CSV`) | `WorkbConfig.get_user_ids()` |
| **2단계 (심각도 분기)** | 심각도별 수신자 분리 (`WORKB_CRITICAL_USER_IDS_CSV`) | `WorkbConfig.get_user_ids(severity)` |
| **3단계 (고도화, 추후)** | 폴스타 `CMM_ALARM_DEF_NOTI_USER`에서 사번 조회 → worKB 발송 | alarm_notifier + DB 조회 |

### 7.3 Bearer 토큰 보안 관리

- `WORKB_BEARER_TOKEN`은 **`.encenv`** 파일에 저장 (기존 `LLMConfig`, `AdminConfig` 패턴과 동일)
- Git에 절대 커밋하지 않을 것

### 7.4 발송 메시지 포맷 예시

```
제목: [인프라알람] [심각] svr-infra-001 (svr-infra-001.internal)

본문:
알람명: CPU 사용률 임계값 초과
설명: 서버 CPU 사용률이 설정된 임계값을 초과하였습니다.
자원: 인프라서버-001 (server.Server)
컨디션: CPU Usage: 95.3% (Threshold: 90%)

요약: 인프라서버-001(svr-infra-001)에서 심각 수준의 CPU 임계값 초과 알람이 발생했습니다.
원인: CPU 사용률이 95.3%로 설정된 임계값 90%를 초과하였습니다.
권고 조치: 해당 서버의 프로세스 목록을 확인하고 CPU 과부하 프로세스를 식별하여 조치하시기 바랍니다.
```

---

## 8. 의존성 추가

`pyproject.toml`의 기존 의존성으로 대부분 충족된다.

| 의존성 | 현재 상태 | 비고 |
|--------|-----------|------|
| `redis[hiredis]>=5.0.0` | 이미 포함 | Redis Streams 지원 포함 |
| `httpx>=0.27.0` | 이미 포함 | Slack / worKB / webhook 발송용 |
| `langgraph>=0.2.0` | 이미 포함 | 알람 분석 서브그래프 |
| `pydantic-settings>=2.0` | 이미 포함 | `AlarmServerConfig` |
| `asyncio` | 표준 라이브러리 | 소켓 수신 |

추가 설치 불필요.

---

## 9. 아키텍처 의사결정 — `docs/02_decision.md` 업데이트

> 구현 완료 후 `D-030`으로 추가 기재 필요.

| 항목 | 내용 |
|------|------|
| **결정** | 알람 소켓 수신을 `alarm_server/`(독립 프로세스)로 분리, 에이전트 서버(`src/alarm/`)와 Redis Stream으로 연결 |
| **근거** | `mcp_server/`로 DB 접근을 분리한 것과 동일한 원칙 — 라이프사이클 독립, 설정 분리, 독립 배포 |
| **대안** | 에이전트 서버 내 asyncio 태스크로 통합 (설정·로그 혼재, 에이전트 재시작 시 소켓 끊김) |
| **알람 조회 vs 알람 수신** | DB 내 알람 데이터 조회는 Plan 44의 `alarm_query` 의도로 처리, 실시간 수신은 `alarm_server/` 소켓으로 처리 — 두 기능 명확히 분리 |
| **폴스타 메시지 포맷** | 단일행 JSON — 폴스타 템플릿 변수 12개(`${platformName}` 등)를 JSON 키-값으로 구성, `json.loads()` 파싱, `AlarmEvent` 직접 매핑 |
| **worKB 토큰 관리** | `.encenv` 파일 저장 (기존 `LLMConfig`, `AdminConfig` 패턴 동일) |

---

## 10. 작업 체크리스트

### Phase 1: alarm_server (소켓 수신 서버)

- [ ] `alarm_server/__init__.py` 생성
- [ ] `alarm_server/config.py`: `AlarmServerConfig` 구현 (`ALARM_SERVER_` 접두사)
- [ ] `alarm_server/base_receiver.py`: `BaseReceiver` 추상 클래스 구현 (Redis 발행 공통 로직)
- [ ] `alarm_server/tcp_receiver.py`: `TcpReceiver(BaseReceiver)` 구현
  - [ ] `_parse()`: 단일행 JSON `json.loads()` (Section 6.1 템플릿 기준)
  - [ ] 폴스타 관리자에게 Section 6.1 템플릿 한 줄 전달 — TCP 알람 액션 메시지 본문으로 등록 요청
  - [ ] 파싱 단위 테스트 작성 (발생 알람 / 해소 알람 각 1건, Section 6.2~6.3 예시 활용)
- [ ] `alarm_server/__main__.py`: 진입점 구현 — `TcpReceiver` 사용 (`python -m alarm_server`)
- [ ] `alarm_server.env`: 소켓 서버 전용 환경변수 파일 작성 (`.gitignore` 등록)

### Phase 2: 에이전트 서버 기반 구조 (src/)

- [ ] `src/config.py`: `AlarmConfig` 클래스 추가 (소켓 설정 제외, 분석·발송 설정만)
- [ ] `src/config.py`: `WorkbConfig` 클래스 추가 및 `AppConfig`에 `alarm`, `workb` 필드 등록
- [ ] `.env.example`: 알람 분석·발송 및 worKB 환경변수 항목 추가
- [ ] `src/alarm/domain/alarm.py`: `AlarmEvent`, `AlarmAnalysisResult` dataclass 작성
- [ ] `src/alarm/prompts/alarm_analyzer.py`: 시스템 프롬프트 + 유저 템플릿 작성 (Section 6.5 기준)

### Phase 3: 에이전트 서버 핵심 로직

- [ ] `src/alarm/application/nodes/alarm_analyzer.py`: LLM 분석 노드 구현
- [ ] `src/alarm/application/nodes/alarm_notifier.py`: 발송 노드 구현
  - [ ] `_send_workb()` 구현 (Section 7 API 명세 기준)
  - [ ] 미지원 채널 수신 시 경고 로그 처리 (추후 채널 추가를 위한 분기 구조 유지)
- [ ] `src/alarm/orchestration/alarm_graph.py`: 2-노드 LangGraph 서브그래프 구성
- [ ] `src/alarm/application/alarm_worker.py`: Redis Stream 소비 + 중복 제거 + 그래프 호출
- [ ] `src/api/server.py`: lifespan에 `AlarmWorker`(소비자만) 백그라운드 태스크 추가

### Phase 4: 검증

- [ ] `python -m alarm_server` 단독 기동 확인
- [ ] 테스트 JSON 페이로드 TCP 전송 → Redis Stream 적재 확인 (`redis-cli XLEN alarm:raw`)
- [ ] `ALARM_ENABLED=false` 상태에서 에이전트 서버 기존 기능 회귀 없음 확인
- [ ] `ALARM_ENABLED=true` 상태에서 에이전트 서버 기동 → AlarmWorker 시작 로그 확인
- [ ] 테스트 알람 엔드포인트 (`/api/v1/alarm/test`) 또는 Redis XADD로 알람 주입 후 worKB 쪽지 수신 확인
  - [ ] worKB 쪽지 수신 확인 (실제 사번으로 테스트)
  - [ ] 미지원 채널 지정 시 경고 로그만 출력되고 처리 계속 확인
- [ ] LLM 분석 결과에 요약/원인/권고 포함 여부 확인
- [ ] 동일 `alarm_id` TTL 내 재발송 시 중복 알림 미발송 확인
- [ ] `min_severity` 미만 알람 무시 확인
- [ ] `WORKB_CRITICAL_USER_IDS_CSV` 설정 시 심각도 3 알람이 별도 수신자에게만 발송 확인
- [ ] worKB API 오류 시 Slack 발송은 독립적으로 성공 확인 (채널별 독립 실패 처리)
- [ ] `alarm_server` 재시작 후 Redis Stream 미소비 메시지 정상 처리 확인
- [ ] `scripts/arch_check.py` 계층 위반 없음 확인

---

## 11. 추후 확장 고려 사항

| 항목 | 내용 |
|------|------|
| **추가 알림 채널** | 사내 Email(SMTP) 추가 — `alarm_notifier.py`에 `_send_email()` 구현체만 추가하면 됨. Generic Webhook은 Section 13 가이드 참고 |
| **HTTP 웹훅 수신기** | `HttpReceiver(BaseReceiver)` 구현 — `alarm_server/http_receiver.py` 추가, `AlarmServerConfig.receiver_type` 필드로 TCP/HTTP 선택 |
| **worKB 수신자 동적 조회** | 폴스타 `CMM_ALARM_DEF_NOTI_USER`에서 알람 담당자 사번 조회 → worKB 발송 (수신자 고정 설정 대체) |
| **다중 소스 수신** | `alarm_server`를 복수 인스턴스화하거나 멀티 수신기를 동시 기동하여 여러 알람 시스템 동시 수신 |
| **알람 이력 저장** | `AlarmAnalysisResult`를 감사 DB(`audit_logs`)에 기록하여 분석 이력 관리 |
| **에스컬레이션** | 동일 장비에서 N분 내 심각 알람 M회 발생 시 추가 채널 발송 |
| **알람 억제 규칙** | 점검 시간대, 특정 장비 그룹 알람 무시 규칙 설정 |
| **알람 조회 연계** | 실시간 수신 알람과 Plan 44의 `alarm_query` DB 조회 결과를 UI에서 통합 표시 |

---

## 12. 폴스타 DB 연계 고도화 — 일상 알람 판별 및 해소 알람 매칭

> **결론 요약**: 두 기능 모두 **기술적으로 가능**하다.
> 기존 DBHub(MCP) + 쿼리 파이프라인 인프라를 재사용하므로 신규 인프라 추가 없이 구현할 수 있다.
> Phase 1(기본 수신·발송) 안정화 후 Phase 2에서 추가 구현을 권장한다.

---

### 12.1 일상적 알람 판별 (패턴 기반 분류)

#### 목표

알람 수신 시 폴스타 DB의 알람 이력을 조회하여, "매일 새벽 반복되는 일상적 알람"인지 "갑자기 발생한 이상 알람"인지를 LLM이 판단할 수 있도록 컨텍스트를 제공한다.

#### 가능 여부

**가능.** 폴스타 DB에는 알람 이력 테이블(`CMM_ALARM_LOG` 또는 동등 테이블)이 존재하며,
`source_db_id`로 식별되는 DB에 기존 DBHub(MCP) 경로로 쿼리할 수 있다.

#### 구현 방식

`AlarmAnalysisGraph`에 `alarm_context_enricher` 노드를 추가한다.

```
alarm_context_enricher → alarm_analyzer → alarm_notifier
```

**`alarm_context_enricher` 노드 역할**:
- `event.source_db_id` + `event.alarm_definition` + `event.hostname`을 키로 최근 7일 이력 조회
- 조회 결과를 `AlarmState.historical_context`에 저장
- DB 조회 실패 시 `historical_context = None`으로 넘기고 분석은 계속 진행 (graceful degradation)

**이력 조회 쿼리 예시** (폴스타 알람 이력 테이블 기준):

```sql
SELECT
    DATE(alarm_time)      AS alarm_date,
    COUNT(*)              AS count,
    MIN(alarm_time)       AS first_time,
    MAX(alarm_time)       AS last_time
FROM CMM_ALARM_LOG
WHERE alarm_def_id = :alarm_definition
  AND hostname     = :hostname
  AND alarm_time  >= NOW() - INTERVAL '7 days'
GROUP BY DATE(alarm_time)
ORDER BY alarm_date DESC;
```

**`AlarmState` 확장**:

```python
class AlarmState(TypedDict):
    alarm_event: AlarmEvent
    historical_context: Optional[str]      # 이력 조회 결과 (LLM 프롬프트에 주입)
    analysis_result: Optional[AlarmAnalysisResult]
    error: Optional[str]
```

**LLM 프롬프트 확장 (이력 컨텍스트 주입)**:

```
[알람 이력 — 최근 7일]
- 2026-05-27: 3건 (02:05 ~ 02:18)
- 2026-05-28: 4건 (02:03 ~ 02:22)
- 2026-05-29: 3건 (02:07 ~ 02:19)
- 2026-05-31: 0건
- 2026-06-01: 1건 (14:35)  ← 오늘 낮 시간대 발생 (일상 패턴과 다름)

위 이력을 참고하여 이 알람이 일상적·반복적 알람인지, 이상한 시간대에 발생한 주의 알람인지 판단하십시오.
```

**LLM 응답 스키마 확장**:

```json
{
    "severity_label": "경고",
    "is_routine": false,
    "routine_note": "평소 새벽 2시대에 발생하던 알람이 낮 14시에 발생 — 비정상 패턴",
    "summary": "...",
    "probable_cause": "...",
    "recommended_action": "..."
}
```

#### 주의 사항

- 폴스타 알람 이력 테이블명/컬럼명은 실제 스키마 확인 후 조정 필요 (`alarm_query` 의도 활용)
- 이력 조회 타임아웃은 5초 이내로 제한 (알람 처리 전체 지연 방지)
- 이력이 없는 신규 알람(`count == 0`)은 "첫 발생"으로 표시

---

### 12.2 해소 알람 처리 및 매칭

#### 목표

폴스타로부터 알람 해소 이벤트를 수신했을 때, 기존에 발생한 알람과 매칭하여
"어떤 알람이 해소되었고, 얼마나 지속되었는지"를 worKB 쪽지로 운영자에게 통보한다.

#### 가능 여부

**가능.** Plan 45(알람 심각도 0 해소)에서 폴스타는 알람 해소 시 심각도 0 이벤트를 발송함을 확인하였다.
`AlarmWorker`가 Redis에 활성 알람 상태를 관리하면 발생↔해소를 매칭할 수 있다.

#### 해소 알람 판별 조건

폴스타 설정에 따라 아래 중 하나로 해소 이벤트가 전달된다:

| 방식 | 판별 조건 |
|------|----------|
| 심각도 0 | `payload["severity"] == 0` |
| 알람명 패턴 | `alarm_name`에 `"clear"`, `"해소"`, `"resolved"` 포함 |
| 별도 필드 | `payload.get("isClear") == True` (커스텀 템플릿 활용 시) |

**`AlarmEvent` 확장**:

```python
@dataclass
class AlarmEvent:
    ...
    is_clear: bool = False               # True이면 해소 알람
    triggered_at: Optional[datetime] = None  # 매칭 후 채워지는 원본 발생 시각
```

#### 매칭 전략 — Redis 기반 활성 알람 상태

```python
# 알람 발생 시 Redis에 등록 (TTL 24시간)
await r.setex(
    f"active_alarm:{event.alarm_id}",
    ex=86400,
    value=datetime.now(tz=timezone.utc).isoformat(),
)

# 해소 알람 수신 시 매칭
if event.is_clear:
    raw = await r.get(f"active_alarm:{event.alarm_id}")
    if raw:
        triggered_at = datetime.fromisoformat(raw.decode())
        event.triggered_at = triggered_at
        duration = datetime.now(tz=timezone.utc) - triggered_at
        await r.delete(f"active_alarm:{event.alarm_id}")
        # LLM에 "해소 + 지속 시간" 컨텍스트 제공
    else:
        # 이력 없음 — Redis TTL 만료 또는 서버 재시작으로 소실
        # 폴스타 DB에서 발생 시각 조회 시도 (fallback)
        pass
```

#### worKB 해소 알람 쪽지 예시

```
제목: [인프라알람] [해소] svr-infra-001 (svr-infra-001.internal)

알람명: CPU 사용률 임계값 초과 (해소)
자원: 인프라서버-001 (server.Server)

발생 시각: 2026-06-02 14:35:20
해소 시각: 2026-06-02 14:52:08
지속 시간: 16분 48초

요약: svr-infra-001의 CPU 임계값 초과 알람이 해소되었습니다.
조치 결과: CPU 사용률이 정상 범위로 회복되었습니다.
```

#### 구현 변경 포인트

| 위치 | 변경 내용 |
|------|----------|
| `AlarmWorker._process()` | 수신 페이로드에서 `is_clear` 판별 후 `AlarmEvent.is_clear` 설정 |
| `AlarmWorker._process()` | 발생 알람 → Redis `SETEX active_alarm:{alarm_id}` |
| `AlarmWorker._process()` | 해소 알람 → Redis 조회 후 `event.triggered_at` 설정, `DEL active_alarm:{alarm_id}` |
| `alarm_analyzer.py` | `is_clear=True`인 경우 별도 "해소" 분석 프롬프트 사용 |
| `alarm_notifier.py` | 해소 알람 쪽지에 "발생 시각 / 지속 시간" 포함 |
| `AlarmState` | `is_clear: bool` 필드 추가 |

#### 폴스타 DB Fallback (Redis 이력 소실 시)

`AlarmWorker` 재시작 시 Redis 활성 알람 상태가 소실될 수 있다.
이 경우 폴스타 DB 알람 이력 테이블에서 `alarm_id` 기준으로 발생 시각을 역조회하는 fallback을 구현할 수 있다 (12.1의 DB 조회 인프라 재사용).

#### 구현 단계

Phase 2 이후. 기본 수신·분석·발송(Phase 1~4) 완료 및 안정화 후 아래 순서로 추가한다:

1. `AlarmEvent.is_clear`, `AlarmEvent.triggered_at` 필드 추가
2. `AlarmWorker._process()`에 발생/해소 분기 로직 추가
3. 해소 알람 전용 프롬프트 작성 (`alarm_analyzer_clear.py`)
4. `alarm_notifier.py`에 해소 알람 쪽지 포맷 추가
5. 폴스타 관리자와 해소 알람 템플릿 형식 확인 (심각도 0 전달 방식 협의)

---

## 13. Generic Webhook 채널 사용 가이드

Generic Webhook은 worKB·사내 Email 외에 **내부 시스템 간 연동**이 필요할 때 사용하는 범용 HTTP 콜백 채널이다.
예: 사내 ITSM 티켓 자동 생성, 사내 공지 게시판 API, 커스텀 알람 집계 시스템 연동 등.

### 13.1 설정 방법

**`.env`에 추가**:

```ini
# Generic Webhook 설정
ALARM_NOTIFICATION_CHANNELS_CSV=workb,webhook   # worKB와 동시 발송 시
ALARM_WEBHOOK_URL=http://내부시스템.internal/api/alarm-event
ALARM_WEBHOOK_SECRET=<선택: HMAC 서명용 시크릿>
```

> `ALARM_WEBHOOK_URL`이 비어있으면 webhook 채널은 자동 무시된다.

### 13.2 전송 페이로드 명세

`alarm_notifier.py`의 `_send_webhook()`이 HTTP POST로 전송하는 JSON 구조:

```json
{
  "alarm_id": "1234567",
  "severity": 3,
  "severity_label": "심각",
  "alarm_name": "CPU 사용률 임계값 초과",
  "hostname": "svr-infra-001",
  "resource_name": "인프라서버-001",
  "resource_type": "server.Server",
  "condition_log": "CPU Usage: 95.3% (Threshold: 90%)",
  "summary": "인프라서버-001에서 심각 수준의 CPU 임계값 초과 알람이 발생했습니다.",
  "probable_cause": "CPU 사용률이 95.3%로 임계값 90%를 초과하였습니다.",
  "recommended_action": "해당 서버의 프로세스 목록을 확인하고 CPU 과부하 프로세스를 조치하십시오.",
  "source_db_id": "polestar",
  "is_clear": false,
  "triggered_at": null
}
```

**Request Header**:

```
Content-Type: application/json; charset=utf-8
X-Alarm-Source: collectorinfra
X-Alarm-Signature: <HMAC-SHA256 서명 (ALARM_WEBHOOK_SECRET 설정 시)>
```

### 13.3 수신 측 구현 요구사항

Generic Webhook을 수신하는 내부 시스템은 아래 조건을 충족해야 한다:

| 항목 | 요구사항 |
|------|----------|
| **엔드포인트** | `POST {ALARM_WEBHOOK_URL}` 수신 가능 |
| **응답** | HTTP 2xx 반환 (실패 시 `alarm_notifier`가 오류 기록 후 다음 채널 계속) |
| **타임아웃** | 10초 이내 응답 (기본값, `ALARM_WEBHOOK_TIMEOUT_SECONDS`로 조정 가능) |
| **서명 검증** | `ALARM_WEBHOOK_SECRET` 설정 시 `X-Alarm-Signature` 헤더로 HMAC-SHA256 검증 권장 |
| **멱등성** | `alarm_id` 기준 중복 수신 처리 권장 (네트워크 재시도 대비) |

### 13.4 구현 위치

`alarm_notifier.py`에 아래 함수를 추가한다 (`_send_workb()`와 동일한 패턴):

```python
async def _send_webhook(cfg_alarm, result: AlarmAnalysisResult) -> None:
    url = cfg_alarm.webhook_url
    if not url:
        raise ValueError("ALARM_WEBHOOK_URL이 설정되지 않았습니다.")
    ev = result.alarm_event
    payload = {
        "alarm_id": ev.alarm_id,
        "severity": ev.severity,
        "severity_label": result.severity_label,
        "alarm_name": ev.alarm_name,
        "hostname": ev.hostname,
        "resource_name": ev.resource_name,
        "resource_type": ev.resource_type,
        "condition_log": ev.condition_log,
        "summary": result.summary,
        "probable_cause": result.probable_cause,
        "recommended_action": result.recommended_action,
        "source_db_id": ev.source_db_id,
        "is_clear": ev.is_clear,
        "triggered_at": ev.triggered_at.isoformat() if ev.triggered_at else None,
    }
    headers = {"Content-Type": "application/json; charset=utf-8", "X-Alarm-Source": "collectorinfra"}
    timeout = getattr(cfg_alarm, "webhook_timeout_seconds", 10)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
```

**`AlarmConfig`에 추가할 필드** (Generic Webhook 활성화 시):

```python
webhook_url: str = ""                  # 비어있으면 webhook 채널 자동 무시
webhook_timeout_seconds: int = 10
```
