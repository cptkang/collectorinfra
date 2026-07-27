# 05. `sre_agent` 조사 서비스 — MCP 노출·패키지 경계·분리 준비 (Investigation Service Boundary)

> 작성일: 2026-07-24 · **이관일: 2026-07-24** (SREAgent → collectorinfra `plans/sre-agent/`, 통합 결정: collectorinfra D-118 / SREAgent D-021)
> **요건**: 사용자 지시(2026-07-24) — "이 에이전트는 향후 collectorinfra 에이전트에서 연동하여 사용할 수 있어야 한다"(D-016). SREAgent는 독립 실행뿐 아니라 **collectorinfra의 알람 파이프라인·챗 오케스트레이션이 호출하는 조사 서비스**로 동작해야 한다.
> **관련 계획**: `plans/01-event-noise-gate.md`(§8 트리거 계약 — 본 계획이 외부 공용 계약으로 승격), `plans/02-incident-investigation-holmesgpt.md`(노출 대상 기능), `plans/04-polestar-mcp-integration.md`(폴스타 접근 — 연동 배치 시 인스턴스 공유 옵션)
> **관련 결정**: D-013(폴스타 연동 MCP 일원화), D-016(collectorinfra 연동 요건·MCP 서비스 노출)
> **신규 결정(본 계획 예약, 구현 착수 시 등재)**: D-017(조사 잡 비동기 계약 — submit/poll), D-018(배치 구성 — 모노레포 내 별도 프로세스·별도 venv). ※ 이관 후 등재는 **collectorinfra `docs/02_decision.md` 번호 체계**를 grep해 그쪽 최댓값+1로 부여한다(이 D-번호들은 SREAgent 체계의 예약 인용).
> **상태**: 계획(미구현) — **통합 갱신(2026-07-24)**: 전제가 "원격 별도 프로젝트 연동"에서 **"동일 저장소(모노레포) 내 독립 패키지 서비스"**로 바뀌었다. MCP 경계(submit/poll·`contract_version`)는 분리 가능 구성의 핵심으로 **그대로 유지**하고, 배치 프로파일 S(독립 모드)는 소멸한다(§2 갱신). 본 계획이 `sre_agent/` 패키지 경계·계약의 **기준 문서**다(폴더 경계 원칙은 README와 공동).
> **번호 체계 주의**: 본 문서의 D-번호는 SREAgent(이관 전) 결정 체계의 인용 — collectorinfra D-번호와 무관(폴더 README 참조).

---

## 1. 개요 — 왜 MCP 서비스 노출인가

collectorinfra는 이미 **MCP 클라이언트 패턴을 보유**한다(`src/dbhub/client.py` — SSE `sse_client`→`ClientSession`→`call_tool`, 재연결·타임아웃 포함). 따라서 SREAgent의 조사 기능을 MCP 서버로 노출하면 collectorinfra 쪽은 **기존 클라이언트 코드 패턴 그대로** 소비할 수 있고, 이 세션에서 확정된 "연동은 MCP로"(D-013) 방향과도 일관된다.

기각한 대안: ① Python 패키지 임베드 — collectorinfra와 SREAgent의 의존성(holmesgpt/LangGraph 스택)·Python 버전이 충돌할 수 있고 프로세스 경계가 사라져 장애 격리 불가, 기각. ② REST API 신설 — 가능하나 MCP 클라이언트가 이미 있는 소비자에게 별도 클라이언트를 요구, 후순위(필요 시 FastMCP 앞에 얇은 REST 어댑터 추가 여지만 남김).

```
[collectorinfra]                                  [SREAgent]
alarm_worker / notification_gate ── PAGE 판정 ──┐
deepagents 오케스트레이션(챗 의도) ──────────────┤ MCP(SSE)   ┌────────────────────────┐
                                                ├──────────▶│ sre-agent MCP 서비스     │
   (기존 DBHubClient와 동일한                    │           │  (interface 계층·FastMCP) │
    SSE MCP 클라이언트 패턴으로 호출)            │           │  → dispatcher(Plan 02 §4) │
                                                │           │  → HolmesGPT 조사        │
브리핑 수신 → alarm_notifier 통보 첨부 ◀────────┘           │  → 결정적 후처리·브리핑    │
                                                            └───────────┬────────────┘
                                                                        │ MCP(SSE)
                                                            폴스타 MCP 서버(Plan 04)
```

그림의 하향 경로는 그대로 유효하다 — 조사의 하향 데이터 경로는 Plan 06(D-019)을 따르되, **(collectorinfra D-119) Prometheus 접근도 같은 `mcp_server`가 노출하는 PromQL 도구로 일원화**되어 하향 의존은 `mcp_server` 하나다(holmesgpt 내장 `prometheus/metrics` toolset 직결 미채택 — Plan 06 §3).

**통합 후에도 MCP 프로세스 경계를 유지하는 이유(분리 준비의 핵심)**:

1. **의존성·런타임 격리**: `sre_agent`는 holmesgpt 스택(Python 3.13, SREAgent 실측)을, collectorinfra 본체는 LangGraph 스택(>=3.11)을 쓴다 — 같은 프로세스에 두면 D-016에서 기각한 "패키지 임베드"의 충돌 리스크가 그대로 재현된다. `mcp_server/`(자체 pyproject·venv·프로세스) 전례를 따른다.
2. **장애 격리**: 조사(LLM 호출·최대 300s)가 알람 파이프라인·챗을 물귀신하지 않는다.
3. **분리 절차의 단순성**: 향후 별도 프로젝트 분리 = `sre_agent/` 폴더를 새 저장소로 복사 + 호출측 URL 설정 변경. 계약(§3·§4) 무변경.

그림의 왼쪽·오른쪽 박스 구분은 통합 후에도 동일하다 — 오른쪽이 같은 저장소의 `sre_agent/` 패키지 **별도 프로세스**가 될 뿐이다.

## 2. 배치 구성 (D-018 예약 — 통합 갱신: 단일 프로파일)

통합으로 구 S(독립)/I(연동) 구분은 소멸하고(독립 모드는 Plan 01 대체와 함께 폐기), **단일 배치 구성**만 남는다:

| 구성 요소 | 프로세스·환경 | 역할 |
|---|---|---|
| collectorinfra 본체(수신·게이트·통보·챗) | 기존 | PAGE 판정·통보. `sre_agent` 호출은 MCP 클라이언트(기존 `DBHubClient` 패턴) |
| **`sre_agent` 조사 서비스** | **별도 프로세스·별도 venv**(`sre_agent/` 독립 패키지, 엔트리 `run_service` 단일) | §3 도구 노출 → HolmesGPT 조사 → 결정적 후처리 → 브리핑 반환 |
| `mcp_server`(폴스타) | 기존 | 하향 데이터 접근(Plan 04 고수준 도구 확장 포함) |

- 게이트 중복 판정을 만들지 않는다 — 노이즈 상속(dedup·클러스터·연쇄 억제)은 collectorinfra 게이트가 전담. dispatcher의 자체 가드(dedup TTL·동시 상한·시간당 예산)는 그대로 동작한다 — 호출자를 신뢰하되 폭주는 자체 방어.
- 기동 단위 분리 원칙은 계승: `sre_agent`의 엔트리는 `run_service` 하나뿐이다(구 `run_gate`·수신부 엔트리는 통합으로 소멸).

## 3. 노출 도구 계약 (D-017 예약 — 비동기 잡 패턴)

조사는 최대 `investigation_timeout_seconds`(기본 300s)까지 걸리는 반면, collectorinfra의 MCP 호출 타임아웃은 기본 60s(실측)다. 동기 호출로는 계약이 성립하지 않으므로 **submit/poll 비동기 잡 패턴**을 계약으로 한다:

| 도구 | 인자 | 반환(JSON) |
|---|---|---|
| `sre_investigate_alarm` | `payload`(§4 트리거 계약 JSON), `wait_seconds=0` | `{investigation_id, status: accepted\|duplicate\|rejected, reason?}` — 즉시 반환. fingerprint dedup TTL 내 중복이면 기존 id 반환(`duplicate`). `wait_seconds>0`이면 그 시간까지 완료를 대기 후 §진행 상태 포함 반환(경계 유지: 클라이언트 타임아웃보다 작게) |
| `sre_get_investigation` | `investigation_id` | `{status: running\|done\|failed\|timeout, briefing?, verdict?, tool_calls_summary?, tokens?, cost?, error?}` — `briefing`은 Plan 02 §7의 6요소 구조화 JSON |
| `sre_diagnose` | `question, server_name?, hostname?, db_id?` | pull형 자연어 진단(챗 의도 위임용). 동일 잡 패턴 — `{investigation_id, ...}` 반환 후 poll |
| `sre_list_investigations` | `limit=20` | 최근 잡 요약(감사 JSONL 기반) |
| `sre_health` | — | `{status, version, contract_version, holmes_ready, polestar_mcp_reachable}` — **collectorinfra `health_check_tool` 지정 대상**(인자 없음) |

- **콜백은 두지 않는다**(1차): collectorinfra 알람 파이프라인은 비차단 fire-and-forget 후 poll이 가능하고, 콜백은 양방향 네트워크·인증을 추가로 요구한다. 필요가 실측되면 후속 결정으로.
- 잡 상태는 in-memory + 감사 JSONL 이중 기록. in-memory dict는 값 bound + 키 만료 sweep(Known Mistakes 원칙). 재기동 시 running 잡은 `failed(reason=restart)`로 확정하고 감사에 남긴다 — 침묵 유실 금지.

## 4. 트리거 페이로드 계약 (Plan 01 §8의 외부 공용 승격)

Plan 01 §8의 페이로드를 **내부 게이트·외부 호출 공용 계약**으로 승격하고 `contract_version: "1"`을 부여한다:

```json
{
  "contract_version": "1",
  "event":    { "dbId": "...", "serverName": "...", "hostname": "...", "alarmId": "...",
                "severity": 2, "alarmName": "...", "resourceType": "...", "resourceName": "...",
                "alarmTime": "yyyyMMddHHmmss", "conditions": "...", "conditionLog": "..." },
  "decision": { "tier": "PAGE", "reason": "...", "fingerprint": "...", "signals": { } },
  "meta":     { "recurrence": null, "cluster": null, "root_resource": null, "source": "collectorinfra" }
}
```

- `event`는 폴스타 원 이벤트 스키마(Plan 01 §4)와 동일 키 — collectorinfra `AlarmEvent`가 보유한 값을 그대로 직렬화하면 된다(변환 계층 불필요).
- `decision.signals`·`meta`는 선택 필드 — 없는 값은 생략 가능하고, SREAgent는 결측을 이유로 거부하지 않는다(조사 스코프가 좁아질 뿐). 단 `event.serverName`/`hostname`/`severity`는 필수(식별자 이원화 규칙 때문 — Plan 01 §4).
- 버전 정책: 필드 추가는 하위 호환(마이너), 필수 필드 변경은 `contract_version` 증가 + 구버전 1개 병행 수용.

## 5. 아키텍처 배치

```
sre_agent/                      # collectorinfra 최상위 독립 패키지 (분리 대상 전체 — README)
├── pyproject.toml              # holmesgpt 등 자체 의존성(requires-python >=3.13), 별도 venv
├── sre_agent/                  # 파이썬 패키지 (기존 SREAgent src/sre_agent/ 이관: diagnosis·toolset_profiles·settings)
│   ├── domain/ · infrastructure/ · application/ · prompts/   # Plan 02 계층 배치 유지
│   ├── interface/
│   │   └── mcp_service.py      # FastMCP 앱 — §3 도구 정의, 인증 미들웨어. dispatcher 호출만(로직 없음)
│   ├── application/investigation_jobs.py  # 잡 저장소(submit/poll 상태 관리) — dispatcher(Plan 02 §4) 위에 박막
│   └── run_service.py          # entry — 유일한 기동 단위
├── scripts/arch_check.py       # 계층 게이트(SREAgent에서 동반 이관, MODULE_LAYER_MAP 포함)
└── tests/
```

- `interface` 계층은 arch 규칙상 application/orchestration/infrastructure 참조 가능 — 위반 없음. 신규 모듈 전부 `MODULE_LAYER_MAP` 등록(D-003).
- transport: **SSE**(collectorinfra 클라이언트 실측 경로와 동일). 포트는 폴스타 MCP 서버(9099)와 분리(예: 9098).
- 인증: Plan 04 §6-4와 동일한 정적 Bearer 토큰 검증(협의 후 mTLS 승격). collectorinfra 쪽은 기존 클라이언트에 헤더 한 줄 추가로 대응 가능.

## 6. 폴스타 MCP 서버 (통합 갱신 — 공유 확정)

통합으로 인스턴스 이원화 문제가 소멸했다: `sre_agent` 조사도 본 저장소의 `mcp_server/` 인스턴스를 직접 소비한다(Plan 04 = 그 인스턴스에 대한 고수준 도구 확장). 구 "업스트림 기여 옵션"이 기본 경로가 되었다 — DB 연결 풀·접속 정보 단일화. 향후 `sre_agent` 분리 시에도 `mcp_server`는 collectorinfra에 남고, 분리된 프로젝트는 URL로 원격 소비한다(계약 무변경).

## 7. collectorinfra 쪽 소비 지점 (통합 갱신 — 본 저장소 작업, 착수 시 함께 구현)

- **push**: collectorinfra Plan 60 §14의 자동 조사 트리거 훅(`investigation_trigger_enabled`)이 emit하는 지점에서 `sre_investigate_alarm` 호출 + 후속 poll → 브리핑을 `alarm_notifier` 통보에 첨부. Plan 64의 `investigation_graph` 자체 구현을 SREAgent 위임으로 대체 가능.
- **pull**: deepagents 오케스트레이션의 `fault_diagnosis` 의도에서 `sre_diagnose` 위임.
- 소비 코드는 기존 `DBHubClient` 패턴 복제로 충분(SSE·재연결·타임아웃 동일).

## 8. 테스트·수용 기준

- **단위**: 페이로드 계약 검증(필수 필드 결측 → `rejected`+사유, 선택 필드 생략 → 수용), 잡 상태 전이(accepted→running→done/failed/timeout), dedup(`duplicate` 반환), 재기동 시 running→failed 확정, sweep.
- **통합**: FastMCP 서비스 기동 → `mcp` SDK 클라이언트(collectorinfra 패턴 재현)로 submit→poll end-to-end(HolmesGPT는 mock LLM). `sre_health`가 폴스타 MCP 도달성을 정직하게 보고.
- **e2e(옵트인 `RUN_E2E=1`)**: 실 HolmesGPT로 submit→poll→브리핑 수신 1건 완주.
- **수용 기준**: ① collectorinfra 클라이언트 패턴(SSE+60s 타임아웃)만으로 전 도구 호출 가능(장시간 대기 강제 없음), ② 중복 submit이 조사를 중복 실행하지 않음, ③ `sre_agent/` 패키지에 수신부·게이트 코드가 존재하지 않음(엔트리는 `run_service` 유일) + collectorinfra 내부 모듈 import 0(경계 테스트로 고정), ④ 계약 JSON에 `contract_version` 포함·구버전 수용 정책 문서화, ⑤ `arch_check --ci` 통과.
