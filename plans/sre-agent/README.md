# SRE-Agent 계획 (plans/sre-agent/) — SREAgent 통합 이관분

> 이관일: 2026-07-24 · 통합 결정: **collectorinfra D-118** / SREAgent D-021
> 원 저장소: `/Users/cptkang/AIOps/SREAgent` (계획·초기 구현 `diagnosis.py`·`toolset_profiles.py`·`settings.py`·arch_check 보유 — 코드 이관은 착수 시)

SREAgent 프로젝트(HolmesGPT 기반 장애 진단 에이전트)를 별도 프로젝트로 유지하지 않고 collectorinfra로 통합하기로 한 사용자 결정(2026-07-24)에 따라, 그쪽 계획 01~06을 본 폴더로 이관했다. **HolmesGPT 관련 기능은 최상위 독립 패키지 `sre_agent/`로 구현**하여 향후 별도 프로젝트로 분리할 수 있는 구성을 유지한다.

## 폴더 경계 원칙 (분리 가능 구성의 불변 조건)

`mcp_server/`(자체 pyproject·venv·프로세스의 독립 최상위 패키지) 전례를 그대로 따른다:

1. **import 격리(양방향)**: `sre_agent/`는 collectorinfra `src/`의 어떤 모듈도 import하지 않는다. collectorinfra도 `sre_agent`를 import하지 않는다 — 호출은 MCP 클라이언트(기존 `DBHubClient` 패턴)로만. 위반 여부는 경계 테스트로 고정한다.
2. **의존성·런타임 격리**: `sre_agent/pyproject.toml`에 자체 의존성(holmesgpt>=0.36.0 등, requires-python >=3.13)을 선언하고 **별도 venv·별도 프로세스**(`run_service` 단일 엔트리)로 실행한다. collectorinfra 본체(>=3.11, LangGraph 스택)와 패키지 충돌이 원천 차단된다.
3. **통신은 계약뿐**: 상향(호출받기)은 Plan 05의 MCP submit/poll 계약(`contract_version` 페이로드), 하향(데이터 접근)은 **`mcp_server` 하나**(관측 데이터 읽기 접근 경계 — 폴스타 + Prometheus PromQL, Plan 04 §4.4·Plan 06 §3, D-119)만.
4. **내부 품질 게이트 동반**: SREAgent의 계층 규칙(arch_check, `MODULE_LAYER_MAP`)은 `sre_agent/scripts/`로 함께 이관되어 패키지 내부에서 계속 적용된다.
5. **분리 절차**: `sre_agent/` 폴더를 새 저장소로 복사 → 호출측(`mcp_server`·조사 서비스) URL 설정 변경. 계약·코드 무변경이 목표이며, 이 단순성이 유지되는지가 경계 설계의 회귀 기준이다.

## 계획 상태 (통합 갱신 후)

| 계획 | 상태 | 내용 |
|---|---|---|
| [01-event-noise-gate.md](01-event-noise-gate.md) | **대체됨** | 게이트는 collectorinfra 기존 게이트(Plan 52/60)가 담당. §8 트리거 계약(→ Plan 05 §4 승격)·§4/§6 스키마·SQL 명세(→ Plan 04 흡수)만 유효 |
| [02-incident-investigation-holmesgpt.md](02-incident-investigation-holmesgpt.md) | **유효(중심)** | HolmesGPT ReAct 조사 + 결정적 후처리(severity_judge·브리핑·권고). `sre_agent/` 패키지의 본체 |
| [03-mock-event-generator.md](03-mock-event-generator.md) | **대체됨** | collectorinfra 원본 Plan 65가 담당. `invest-trigger` 시나리오 델타만 Plan 65에 반영 |
| [04-polestar-mcp-integration.md](04-polestar-mcp-integration.md) | **유효(재편)** | "이식" → 기존 `mcp_server`에 조사용 고수준 도구 8종·마스킹 프록시·도메인 deny·전송 인증 직접 확장. `polestar_noise_signals`는 폐기 |
| [05-collectorinfra-interop.md](05-collectorinfra-interop.md) | **유효(기준 문서)** | `sre_agent` 조사 서비스의 MCP 노출(submit/poll)·패키지 경계·분리 준비. 게이트 훅·챗 위임의 소비 지점 포함 |
| [06-remote-vm-access.md](06-remote-vm-access.md) | **유효** | 원격 VM 데이터 경로 = Prometheus + 폴스타 2축(소스)·SSH 미채택 — **전송은 `mcp_server` 일원화(D-119)**, `remote_vm_profile()`·hostname 정합 규약(서버측 nodename 조립) |

**권장 착수 순서**: Plan 04 M-B(mcp_server 고수준 도구) → Plan 02 W-A(조사 코어·pull) + Plan 06 R-A/R-B → Plan 05(서비스 노출·게이트 훅 연동) → Plan 02 W-B/W-C. ※ Plan 60~65 잔여분까지 포함한 **통합 실행 시퀀스는 collectorinfra `plans/66-sre-agent-integrated-implementation-plan.md`**(Phase 0~5·착수 게이트 §7)가 단일 장부다.

**collectorinfra 계획 정합화(2026-07-24 완료)**: Plan 60(§14.2 훅 위임처=`sre_investigate_alarm` submit/poll·§18.4/§18.6 공용 자산 소재 재정의), Plan 62(§2 C6·§4 P4·§5.2), Plan 64(**§0 통합 재편** — `investigation_graph` 자체 구현 대체·섹션별 상태 매핑·CW-A~C 소비 배선·블로커/번호 재편), Plan 65(§4.3 `invest-trigger` 델타 편입)가 본 폴더 계획 기준으로 갱신됐다. 역방향 델타: Plan 60 E8(D-117) 폴스타 에이전트 스냅샷 채널의 `polestar_host_snapshot` 노출 후보(Plan 04 §4.2·Plan 02 §6·Plan 06 §9에 기록 — E8 착수 시 결정).

**갱신(2026-07-27 · D-119)**: PromQL 접근을 `mcp_server`로 통합(holmesgpt 내장 Prometheus toolset 직결 미채택) — `mcp_server` 성격을 "관측 데이터 읽기 접근 경계"로 재정의. hostname 앵커 고수준 도구 기본 + 원시 옵트인, 서버측 `{nodename=…}` 결정적 조립, 감사·자격증명·지침 주입 일원화. 검증 게이트(내장 toolset 대비 품질 열화 없음·열화 시 복귀)는 Plan 06 §8 수용 기준 7. 반영: Plan 04 §4.4·Plan 06 §1~§8·Plan 02·collectorinfra Plan 66 R5.

**갱신(2026-07-27 · D-120)**: HolmesGPT **개발·테스트 LLM = Gemini API**(litellm 경유·착수 시 실측) — 스모크 하네스 `sre_agent/scripts/smoke_llm.py`·`AgentSettings` LLM 필드·데이터 통제(외부 송신은 목업·픽스처만, 운영 투입 금지)는 Plan 02 §10.1. 운영 LLM은 Plan 66 §7-1에서 별도 확정(운영 활성화 게이트로 완화 — Phase 2 개발은 선행 가능).

## 번호 체계 주의

- 이관 문서 본문의 **D-번호는 SREAgent(이관 전) 결정 체계의 인용**이다 — collectorinfra의 D-번호와 무관하다. SREAgent 등재분: D-001(HolmesGPT SDK)·D-004(VM 진단 대상)·D-005(계획 이식)·D-013(폴스타 MCP 일원화)·D-016(연동 요건)·D-019(원격 2축)·D-021(본 통합). 원문: SREAgent `docs/02_decision.md`.
- 이관 계획의 "예약 D-번호"를 등재할 때는 **collectorinfra `docs/02_decision.md`의 채번 규칙**(`## D-` 헤더 + 「변경 이력」 표 전체 grep 후 최댓값+1)을 따른다 — SREAgent 예약 번호를 그대로 쓰지 않는다.
- collectorinfra 문서에서 이 계획들을 참조할 때는 기존 Plan 60 등과의 혼동을 피하기 위해 `sre-agent/01` 또는 "SRE-Plan 01" 표기를 권장한다.
