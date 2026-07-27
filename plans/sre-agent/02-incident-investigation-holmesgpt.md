# 02. 장애 진단 대응 — HolmesGPT 자동 조사·브리핑·조치 권고 (Incident Investigation with HolmesGPT)

> 작성일: 2026-07-24 · **이관일: 2026-07-24** (SREAgent → collectorinfra `plans/sre-agent/`, 통합 결정: collectorinfra D-118 / SREAgent D-021)
> **원본**: collectorinfra `plans/64-automated-incident-investigation-and-response.md`를 SREAgent로 이식. 원본은 LangGraph 고정 파이프라인(`investigation_graph`)이었으나, 본 계획은 **조사 루프를 HolmesGPT ReAct에 위임하고 판정·브리핑 조립을 결정적 후처리로 유지**하는 구조로 재설계한다(§2).
> **선행 계획**: `plans/01-event-noise-gate.md` — PAGE 트리거 계약(§8) 소비. 게이트 없이도 pull 경로(운영자 질의)로 단독 동작 가능. `plans/04-polestar-mcp-integration.md` — 폴스타 도구를 노출하는 MCP 서버(본 계획이 소비). `plans/05-collectorinfra-interop.md` — 본 계획의 조사 기능을 MCP 서비스로 노출(collectorinfra가 호출). `plans/06-remote-vm-access.md` — 원격 VM 데이터 경로 확정(Prometheus + 폴스타 MCP 2축, 본 계획 §8의 L3 논의를 종결).
> **관련 결정**: D-001(HolmesGPT PyPI SDK — `DiagnosisAgent` 래퍼), D-004(VM 진단 대상·`vm_profile()` 읽기 전용 bash), D-005(계획 3종 이식 방향), D-013(폴스타 연동 MCP 일원화), D-019(원격 VM 접근 = Prometheus + 폴스타 MCP 2축, SSH 미채택)
> **신규 결정(본 계획 예약, 구현 착수 시 등재)**: D-009(조사 루프 HolmesGPT 위임 + 결정적 후처리 경계), D-010(폴스타 MCP 서버 등록 — `Config.mcp_servers`), D-011(조치 권고 human-gated — 실행 경로 미탑재). ※ 이관 후 등재는 **collectorinfra `docs/02_decision.md` 번호 체계**를 grep해 그쪽 최댓값+1로 부여한다(아래 D-번호들은 SREAgent 체계의 예약 인용).
> **상태**: 계획(미구현) — **통합 갱신(2026-07-24)**: 구현 위치는 collectorinfra 최상위 **독립 패키지 `sre_agent/`**(폴더 경계 원칙은 README). 트리거는 **collectorinfra 게이트 단일 소스**(Plan 05 계약 경유) — 내부 게이트 갈래는 Plan 01 대체와 함께 소멸.
> **번호 체계 주의**: 본 문서의 D-번호는 SREAgent(이관 전) 결정 체계의 인용 — collectorinfra D-번호와 무관(폴더 README 참조).

---

## 1. 개요 및 목적

노이즈 게이트(collectorinfra 기존 게이트 — 이관 후 Plan 01은 대체됨)가 **PAGE**로 판정한 사건에 대해, 숙련 운영자의 트리아지 절차(①부하 확인 → ②병목 식별 → ③원인 격리 → ④로그 분석)를 자동 수행하여:

1. **중요도 2차 정밀 판정**(escalate-only — 게이트 판정을 소급 변경하지 않고 상향만)
2. **글래스박스 브리핑**(모든 주장에 소스 인용, 운영자가 30초 내 검증 가능)
3. **조치 후보 권고**(human-gated — 실행은 운영자, 시스템에 실행 코드 경로 없음)

를 운영자에게 전달한다. 범위는 **탐지→조사→브리핑→조치 권고**까지이며, 자동 조치 실행은 거버넌스(읽기 전용 원칙의 예외 결정) 확정 전에는 착수하지 않는다.

## 2. 원본 대비 핵심 재설계 — 고정 파이프라인 → HolmesGPT ReAct + 결정적 경계 (D-009 예약)

원본 Plan 64는 HolmesGPT를 §9.1에서 직접 벤치마킹했으면서도("read-only toolset을 agentic ReAct로 자동 실행, by design read-only, 조사/조치 분리 옵트인") **고정 파이프라인**을 택했다. 그 근거는 "폐쇄망·소형 로컬 LLM은 tool-calling이 불안정해 조사 루프 주도를 맡길 수 없다"는 것이었다.

SREAgent의 전제는 다르다: D-001로 HolmesGPT SDK를 채택했고, 모델은 유능한 tool-calling 모델(`anthropic/claude-sonnet-5`)이다. **갱신(2026-07-27 · collectorinfra D-120)**: 개발·테스트 LLM은 **Gemini API**로 확정(§10.1 — 스모크 하네스·데이터 통제 포함), 운영 LLM은 collectorinfra Plan 66 §7-1에서 별도 확정한다(운영 활성화 전 게이트 — 개발은 차단하지 않음). 따라서 **원본이 HolmesGPT에서 차용하려던 구조를 HolmesGPT 자체로 구현**한다. 단, D-035 경계(결정적 규칙=판단/LLM=보조)는 그대로 계승한다:

| 역할 | 담당 | 근거 |
|---|---|---|
| 증거 수집(어떤 도구를 어떤 순서로) | **HolmesGPT ReAct** (toolset은 전부 read-only) | Roy 등: ReAct 환각률 4~6% vs 순수 검색 49% |
| 원인 가설·서술·브리핑 초안 | **HolmesGPT LLM** | LLM은 서술·해석 전담 |
| 트리거·dedup·동시성·타임아웃 가드 | **결정적 dispatcher** (§4) | 폭주 방지는 코드가 전담 |
| 중요도 2차 판정 | **결정적 severity_judge** (§6) — 도구 원시 출력의 시그니처 매칭 | LLM에 최종 판정 위임 금지 |
| 브리핑 스키마 조립·인용 검증 | **결정적 briefing_builder** (§7) | 인용 없는 주장 차단 |
| 조치 실행 | **없음** (권고만, D-011 예약) | 읽기 전용 원칙 |

## 3. 아키텍처

```
[push] collectorinfra 게이트 PAGE emit(Plan 05 MCP 계약 경유) ─┐
[pull] 운영자 질의(ask·챗 위임 sre_diagnose) ──────────────────┤
                                 ▼
        investigation_dispatcher (결정적)
        - fingerprint dedup TTL·동시 실행 상한·전체 타임아웃 가드
        - incident_scoper: 대상 서버·기준시각·조사 구간·kind 분류(classify_alarm_kind, 결정적)
                                 ▼
        DiagnosisAgent (HolmesGPT ToolCallingLLM)          ← SREAgent에서 이관한 diagnosis.py 확장 (sre_agent/ 패키지)
        - 구조화 사건 프롬프트(§5.3) + system_prompt_additions(조사 지침)
        - toolsets: 폴스타 MCP 서버 도구(§5, L1·L2 — Plan 04)
                  + PromQL 도구(원격 — 같은 mcp_server 노출, D-119·Plan 06 §3)
                  + vm_profile bash(로컬 개발·데모 배치 한정, §8)
                                 ▼ LLMResult(result, tool_calls)
        결정적 후처리
        - severity_judge: tool_calls 원시 출력 시그니처 매칭 → ImportanceVerdict(escalate-only)
        - briefing_builder: 6요소 스키마 조립·인용 검증·한계 서술 강제
        - remediation_recommender: kind별 조치 후보 표(위험도·신뢰도) — 권고만
                                 ▼
        전달(통보 채널) + 감사(decision_store JSONL)
```

- **계층 배치**: dispatcher·후처리는 `application/`, 폴스타 toolset은 `infrastructure/`, 시그니처 표·판정 규칙은 `domain/`(순수), 조사 지침 프롬프트는 `prompts/`. 신규 모듈은 `MODULE_LAYER_MAP` 등록(D-003).
- **DiagnosisResult 확장**: 현행 `tool_calls: list[str]`(description만)로는 severity_judge가 원시 출력을 볼 수 없다. `LLMResult.tool_calls`의 결과 필드를 보존하도록 확장한다 — **필드명·구조는 착수 시 `inspect`로 실측 확정**(실측 우선 원칙, 계획서 의사코드 신뢰 금지).

## 4. 트리거 소비와 폭주 방지 (결정적 dispatcher)

- **push(주 경로)**: Plan 01 §8 계약의 페이로드(AlarmEvent + NotificationDecision + 메타)를 수신. 트리거 소스는 **collectorinfra 게이트 단일 갈래**다 — 그쪽 Plan 60 §14 훅이 Plan 05의 `sre_investigate_alarm` MCP 도구로 submit(통합 전 설계의 "내부 게이트" 갈래는 Plan 01 대체로 소멸). 게이트와 분리된 백그라운드 태스크로 실행하며, per-call 타임아웃만으로는 무력화되므로 **조사 전체 타임아웃**(`investigation_timeout_seconds`, 기본 300s — HolmesGPT 다단계 조사 감안, 원본 45s보다 상향)을 씌운다.
- **pull(보조)**: `DiagnosisAgent.ask()` 경로 유지 — 운영자가 "○○ 서버 원인 분석해줘"로 직접 질의. 이때도 §5 toolset·§7 브리핑 형식을 공유한다.
- **폭주 방지(노이즈 상속 + 자체 가드)**: 게이트가 dedup·클러스터·연쇄 억제를 이미 수행하므로 대표 사건만 도달한다(20대 동시 장애 = 조사 1회). 추가로 `investigation_dedup_ttl_seconds`(동일 fingerprint 최소 간격)·`investigation_max_concurrent`(동시 상한). in-memory 상태는 값 bound + 키 만료 sweep 동시 구현.
- **비용 가드**: HolmesGPT 호출은 토큰 비용이 발생한다. `LLMResult.total_tokens/total_cost`를 감사 레코드에 기록하고, 시간당 조사 횟수 상한(`investigation_hourly_budget`)을 둔다.

## 5. 폴스타 MCP 도구 연동 (D-010 예약) — "폴스타 정보의 조합 활용" 핵심

### 5.1 연동 지점 — `Config.mcp_servers` 등록 (holmesgpt 0.36.0 실측)

폴스타 도구는 SREAgent가 직접 구현하지 않고 **Plan 04의 폴스타 MCP 서버가 노출**한다(D-013). holmesgpt 실측: `Config.mcp_servers: dict[str, dict]` 항목은 `type=mcp`로 스탬프되어 `RemoteMCPToolset`으로 로드되고, 서버의 도구 목록을 `list_tools`로 자동 발견한다. SSE/streamable-http/stdio transport, `health_check_tool`(인자 없는 `list_sources` 지정), `MCP_TOOL_CALL_TIMEOUT_SEC` 타임아웃, 동일 서버 호출 직렬화가 내장돼 있다. 설정 예시는 Plan 04 §7.2.

- 기각한 대안: `additional_toolsets`(Python Toolset 직접 주입) — DB 드라이버·접속 정보가 SREAgent 프로세스에 들어와 MCP 일원화(D-013)와 상충. MCP 서버 경계에 두면 게이트(Plan 01)와 조사(본 계획)가 동일 도구·동일 보안 통제를 공유한다.

### 5.2 조사에 쓰는 도구 (명세는 Plan 04 §4)

조사 파이프라인이 소비하는 고수준 도구: `polestar_alarm_history`(이력·빈도), `polestar_metric_trend`(추이), `polestar_resource_status`(서브리소스 가용 상태), `polestar_topology`(의존 조상/자손), `polestar_process_snapshot`(top-N 프로세스 — 서버 측 args 마스킹, 실시간 단면 명시), `polestar_os_config`(EAV 피벗), `polestar_change_history`(변경 근접 — 인과력 최상위), `polestar_condition_log`(발화 값·매칭 로그). 전부 읽기 전용 고정 SQL/GET이며 값 인자만 받는다 — LLM은 도구 선택·호출·해석만 담당하고 SQL을 작성하지 않는다.

### 5.3 도메인 지식 주입 (`llm_instructions` + `prompts/`)

toolset의 `llm_instructions`와 조사 시스템 프롬프트에 폴스타 도메인 규칙을 명시한다:

- **식별자 이원화**: DB 조회는 `server_name`, 프로세스 API는 `hostname` — 절대 혼용 금지.
- **severity 의미**: 0=해소·1=주의·2=경고·3=심각. `alarmStatus`(ACK)는 해소 여부와 무관.
- **방언**: gp/yd=PostgreSQL(`polestar.` 소문자 한정), b0=DB2(`POLESTAR.` 대문자, FETCH FIRST, 집계 전 CAST) — 단 방언 분기는 MCP 서버 내부에서 결정적으로 처리하고 LLM에는 위임하지 않는다(Plan 04 §5).
- **금지 조인**: `RESOURCE_CONF_ID=CONFIGURATION_ID` 직접 조인 금지(브릿지는 HOSTNAME=STRINGVALUE_SHORT+`NAME='Hostname'`), `cmm_vendor`/`cmm_os`/`cmm_os_param` 조회 금지.
- **조사 지침**: 트리아지 순서(부하→병목→원인 격리→로그), 모든 주장에 도구 출력 인용, 단면 데이터·미수집 신호는 한계로 서술, **조치 실행 시도 금지(권고 서술만)**.

## 6. 중요도 2차 판정 — `severity_judge` (결정적, escalate-only)

HolmesGPT 조사가 끝난 뒤, **도구 원시 출력**에 대해 결정적 시그니처 매칭을 수행한다(LLM 서술이 아니라 raw 출력이 입력 — LLM 환각이 판정에 개입할 수 없음):

| 신호 | 판정 |
|---|---|
| `Out of memory: Killed process` (dmesg/journal) | 상향(강) |
| 파일시스템 read-only 리마운트 / `read-only file system` | 상향(강) |
| 서비스 재시작 루프(`start-limit-hit`, NRestarts 급증) | 상향(강) |
| USE 포화 지속 K구간(run-queue·swap in/out·await) | 상향(중) |
| inode/FD 고갈(`df -i`, `Too many open files`, file-nr) | 상향(중) |
| 단발 스파이크 후 자기 복구 | 상향 없음 |
| L3 부재·데이터 불충분 | 상향 보류 + 브리핑에 "증거 불충분" 명시 |

**원격 배치 주의(Plan 06/D-019)**: dmesg/journal 원문은 원격 2축에서 수집되지 않는다 — OOM 등 로그 시그니처는 Prometheus 카운터(예: `node_vmstat_oom_kill`) 매칭으로 대체하고, 대체 신호도 없으면 마지막 행(증거 불충분) 경로를 따른다. 로그 원문 시그니처 행은 로컬 배치·향후 로그 스택 편입 시 그대로 재사용한다. **통합 델타(2026-07-24)**: collectorinfra Plan 60 §18 E8(D-117 — 폴스타 에이전트 read-only 스냅샷 채널)이 구현되고 Plan 04의 `polestar_host_snapshot` 후보 도구로 노출되면, 원격 배치에서도 dmesg/journal·USE 명령 원문 시그니처가 가용해진다(그때 Prometheus 카운터 대체 규칙은 폴백으로 강등 — E8 착수 시 결정).

출력은 `ImportanceVerdict(level, confidence, escalate, signals)`. **게이트 판정의 소급 변경·하향은 불가**(Plan 01 §8 역방향 계약). OS 장애 시그니처 치트시트(OOM/soft lockup/hung task/FS 오류/conntrack 고갈/segfault)는 `domain/` 순수 모듈로 두고 단위 테스트로 고정한다.

## 7. 글래스박스 브리핑 — `briefing_builder` (결정적 조립)

HolmesGPT의 서술(`result`)과 severity_judge 판정을 6요소 스키마로 조립한다. 인용이 결여된 단정은 "가설"로 강등 표기한다.

```
[중요도] 심각(신뢰도 high) — 게이트 PAGE + 조사 상향(OOM 확정)
[요약]   web-01(gp) 메모리 고갈 → java(pid 12345) OOM 종료 → 서비스 3회 재시작 후 다운
[타임라인]
  14:03  Mem Util 78%→95% 급상승          ← polestar_metric_trend
  14:06  OOM: Killed process 12345 (java)  ← journalctl/dmesg [원문 인용]
[병목]   메모리 (USE: 포화 — swap in/out 지속)
[원인]   힙 상한 미설정 상태의 트래픽 증가(신뢰도 med) — 근거: 프로세스 RSS 추이
[권고]   ① (승인 후) java.service 힙 상향·재기동  ② 메모리 누수 점검
         ※ 실행은 운영자 승인 후 수동 — 시스템은 제안만
[한계]   프로세스는 조사 시점 단면. swap 상세 분해(slab)는 미수집.
```

- **전달**: MVP는 콘솔/파일 + 웹훅 1종(채널은 spec.md 확정 시 결정). 브리핑 전문·근거·토큰 비용을 감사 JSONL에 기록.
- pull 경로(운영자 질의)도 동일 형식으로 응답한다.

## 8. L1/L2/L3 수집 계층과 L3 접근 방식

| 계층 | 내용 | 담당 |
|---|---|---|
| **L1** (즉시) | 알람 이력·메트릭 추이·가용 상태·실시간 프로세스·OS 설정·변경 이력 | §5 폴스타 toolset — **신규 권한 불필요, 1차 구현 대상** |
| **L2** | 관제 로그 본문(LogMonitor)·프로세스 생존(ProcessMonitor) | 폴스타 toolset(위치 매핑은 표본 쿼리로 확정) |
| **L3** | 라이브 OS 상태(USE 전체) — 원격: node_exporter 메트릭으로 대체 / 로컬 배치: vmstat·iostat·journalctl 등 bash | **D-019로 확정** — 아래 참조 |

**L3 접근 방식은 D-019로 확정됐다(Plan 06)** — 원격은 중앙 관측 스택(Prometheus — **전송은 mcp_server PromQL 도구 경유, collectorinfra D-119**)을 쓰고, SSH·대상 VM 에이전트 배포는 채택하지 않는다. 초안에서 검토하던 옵션(①벤더 에이전트 확장 ②중앙 관측 스택 ③SSH 옵트인)은 ②로 종결한다. USE 분해(us/sy/wa/steal)·oom_kill 카운터 등 라이브 OS 신호는 node_exporter 메트릭으로 대체하며, VM 로그(syslog/journal) 원격 수집은 현 2축에 없다 — 로그 스택(Loki 등) 편입은 별도 결정(Plan 06 §9).

로컬 개발·데모(에이전트와 대상이 같은 호스트)에서만 기존 `vm_profile()` bash toolset이 L3다(읽기 전용 allowlist + deny는 구현 완료). **어느 배치든 변경 명령(kill·renice·systemctl restart·dmesg -C)은 toolset에 물리적으로 존재하지 않게 한다.**

## 9. 조치 권고 — human-gated (D-011 예약)

- `remediation_recommender`는 kind별 조치 후보(renice/kill -TERM/힙 상향 재기동/로그 로테이션 등)를 **근거·신뢰도·위험도**(저: renice·로그 정리 / 중: kill / 고: 재기동·설정 변경)와 함께 제시만 한다. 고위험·저신뢰 조합은 "검토 필요"로만 표기.
- **실행 코드 경로는 만들지 않으며, 이를 테스트로 고정한다**(조치 문자열이 실행 API에 닿는 경로가 없음을 단언). 향후 자동 실행은 읽기 전용 원칙의 예외 결정 + 이중 승인 + 롤백·blast radius 설계가 선행돼야 하며 본 계획 범위 밖이다. holmesgpt `Toolset.approval_required_tools` 필드(실측 확인)는 그 단계의 승인 게이트 후보로 기록만 해 둔다.

## 10. 설정 플래그 (전부 기본 off / 보수값)

`investigation_trigger_enabled`, `investigation_trigger_min_tier="PAGE"`, `investigation_timeout_seconds=300`, `investigation_dedup_ttl_seconds`, `investigation_max_concurrent=2`, `investigation_hourly_budget`, `severity_judge_enabled`, `remediation_recommender_enabled`, `l3_bash_enabled`(로컬 배치 한정 — 원격은 D-019에 따라 bash 미확장). SSH 관련 플래그는 두지 않는다(D-019 미채택). pydantic-settings nested는 `Field(default_factory=...)`.

### 10.1 개발·테스트 LLM — Gemini API (collectorinfra D-120)

운영 LLM(Plan 66 §7-1) 확정 전에도 HolmesGPT 위임 설계를 개발·검증할 수 있도록 **Gemini API를 테스트 LLM으로 채택**한다:

- **접속**: holmesgpt의 litellm 경유 규약 — 예상 모델 문자열 `gemini/<model>`·env `GEMINI_API_KEY`. **착수 시 holmesgpt 0.36.0 동봉 litellm 버전으로 실측 확정**(실측 우선 원칙 — 계획서 표기를 신뢰하지 말 것). 기본 모델은 tool-calling 실측 후 결정.
- **설정**: `AgentSettings.investigation_llm_model` + `gemini_api_key`(SecretStr) — pydantic 필드로만 판정(`os.getenv` 금지), `.env` 인라인 주석 금지.
- **스모크 하네스** `sre_agent/scripts/smoke_llm.py` (holmesgpt 반입 직후 즉시 실행 가능한 최소 검증):
  1. litellm 단독 tool-calling 왕복 — 함수 호출 1회를 강제하고 호출명·인자 파싱을 확인(HolmesGPT 성립 조건인 tool-calling 자체의 검증).
  2. `DiagnosisAgent` `ask` 1회 — 로컬 mock MCP 픽스처(Plan 04 로컬 PG 픽스처) 대상, 도구 자동 발견→호출→서술 완주 확인.
- **용도**: ①tool-calling 성립 검증(§7-1 운영 LLM 판단의 실측 근거 축적) ②D-119 품질 게이트(A/B)의 실행 LLM ③W-A~W-C 개발 루프.
- **데이터 통제(절대 제약)**: Gemini API는 외부 SaaS — **개발·테스트 전용, 운영 투입 금지**. 외부 송신 입력은 **목업(Plan 65)·로컬 Docker 픽스처 데이터만**, 실 운영(폴스타) 데이터 송신 금지. 결정적 차단: 테스트 환경 `mcp_server` config에는 픽스처 소스만 등록(운영 connection 미설정 → 빈 값 소스 자동 비활성 규약 재사용 — 물리적으로 실 데이터 접근 불가).
- 비용 가드는 §4와 동일(`LLMResult.total_tokens/total_cost` 감사 기록·`investigation_hourly_budget`).

## 11. 구현 순서

| Wave | 내용 | 선행 조건 |
|---|---|---|
| **W-A** | 폴스타 toolset(§5) + pull 경로(ask에 toolset 주입) + 브리핑 형식 | 없음 — 게이트 없이도 가치 있음 |
| **W-B** | dispatcher + push 트리거 소비 + severity_judge + 감사 | Plan 05 서비스 골격(collectorinfra 게이트 훅 연동) |
| **W-C** | 원격 데이터 경로 합류(Plan 06 R-C·R-D — mcp_server PromQL 도구[D-119] + `remote_vm_profile`) + remediation_recommender | Plan 06 R-A/R-B (L3 접근 방식은 D-019로 확정됨) |

## 12. 테스트·수용 기준

- **단위**: severity_judge 시그니처 표(도구 원시 출력 픽스처 → 판정), dispatcher 가드(dedup TTL·동시 상한·전체 타임아웃), 마스킹 유지 확인(args 마스킹 자체는 MCP 서버 측 강제 — Plan 04 §6 테스트 소관, 여기서는 마스킹된 도구 출력이 브리핑에 원문 복원 없이 유지되는지), 브리핑 조립(인용 결여 → 가설 강등).
- **통합**: toolset이 실 런타임 tool executor에 반영되는지 검증 — `tests/test_vm_profile.py` 전례를 따르되, **prerequisite 캐시 히트 시 config 파싱이 생략되므로 `PrerequisiteCacheMode.DISABLED`로 검증**(기실측 함정).
- **e2e(옵트인 `RUN_E2E=1`, API 키 필요 — 테스트 LLM은 Gemini, §10.1/D-120)**: mock 이벤트(collectorinfra Plan 65 생성기 — 이관 Plan 03은 대체됨) → 게이트 훅 → `sre_investigate_alarm` submit → 실 HolmesGPT 조사 1건 완주. 입력은 목업·픽스처 데이터만(§10.1 데이터 통제).
- **수용 기준**: ① PAGE 1건당 조사 1회(dedup·동시 상한 동작), ② 브리핑 전 항목에 소스 인용 또는 "가설/한계" 표기, ③ 조치 실행 경로 부재가 테스트로 고정, ④ 전체 타임아웃 내 미완주 시 부분 결과 + 사유를 구조화해 전달(침묵 실패 금지), ⑤ `arch_check --ci` 통과.
