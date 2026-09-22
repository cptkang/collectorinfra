# Capability Map: 복합 질의 호스트 조사 오케스트레이션 (차수 3-A)

> **범위**: `plans/80` §5.2 **차수 3** 중 **WU-11·12·13·14·15·16·17·19**
> (= `plans/78` W1 · W2-1~6 · W3-1·4·5 · W4 · W5 · W6 · W2-7·8).
> **제외**: **WU-18**(W3-2·3 경로 선택) — `WU-06`(S-2 `relevance_score` 분포 실측)이 **실선행**이며
> G-BILL 뒤에 있다. 3-B로 이월.
>
> **작성 근거**: `plans/80` §5.5 순서 계약 ①(WU-05 선행)은 **e2e 수용 검증**을 막는 것이지
> **구현·단위 검증**을 막지 않는다. 78 §6.1 파일 목록 실측 결과 **78이 수정하는 라우팅 파일은 0건**이다
> (`src/routing/**` 미포함). 접점 D가 종속시키는 것은 *`fault_diagnosis` 도달 빈도*이지
> *대상 해소 로직*이 아니다. 따라서 3-A는 **라우팅에 단언하지 않는 조건**으로 착수한다.

## 모듈

| Module id | 책임 | 의존 | WU | 78 Wave |
|---|---|---|---|---|
| `prior-targets` | 선행 결과 → 조사 대상 해소(해석 3단 + 결정적 확정) · `TargetRef` 타입 계약 · **3경로 공통화**(G2+G5) | — | WU-11 | W1 |
| `investigation-audit` | 조사 감사 레코드 **스키마 생성**(계약 C-B v2) · 실패 트레이스 · 기동 로그 · **Tier 2 지표 4종** | `prior-targets` | WU-14 | W6 |
| `target-fanout` | N-대상 fan-out · 부분 실패 격리 · 전체 타임아웃 · 결정적 reduce · **부하 가드 요구 전달** | `prior-targets` | WU-12 | W2-1~6 |
| `mcp-highlevel-tools` | `DBHubClient._call_tool` 고수준 도구 4종 배선 · 도구 명명·스키마 규약 | `prior-targets` | WU-13 | W3-1·4 |
| `host-authz` | 호스트 인가 게이트(`admin_only` · **fail-closed**) · **채팅·이벤트 대칭** | `mcp-highlevel-tools`, `investigation-audit`(레코드 필드) | WU-15 | W3-5 |
| `sufficiency-replan` | 결정적 충족도 체크 · **1회만** 재계획 · 실행 전 준비 검증 · 대상 정합 사후 대조 | `target-fanout` | WU-16 | W5 |
| `fanout-compaction` | 결정적 2단 축약(압축 손실 기록) · 단기 조사 캐시(TTL·sweep·나이 표기) | `investigation-audit` | WU-17 | W2-7·8 |
| `diagnosis-consumption` | 브리핑 6요소·`Remediation` **소비만** · 위험도/신뢰도 무손실 표기 · 실행 경로 부재 고정 | `host-authz`, `sufficiency-replan` | WU-19 | W4 |

## 의존 방향 (순환 없음)

```
prior-targets ─┬─▶ investigation-audit ─┬─▶ fanout-compaction
               │                        └─▶ host-authz ──┐
               ├─▶ target-fanout ─▶ sufficiency-replan ───┼─▶ diagnosis-consumption
               └─▶ mcp-highlevel-tools ──▶ host-authz ────┘
```

**빌드 순서**: `prior-targets` → `investigation-audit` → {`target-fanout`, `mcp-highlevel-tools`}
→ {`host-authz`, `sufficiency-replan`, `fanout-compaction`} → `diagnosis-consumption`

> **`investigation-audit`를 2번째에 두는 것은 `plans/78` §4.6.2 Tier 규율이다** — *측정 없이 최적화를
> 쌓지 않는다.* 번호(W6)는 마지막이지만 순서는 Tier 2(`fanout-compaction`)보다 **앞**이다.
>
> **`host-authz`가 `investigation-audit`에 의존하는 이유**(78 W6-5): 인가 판정 결과가 감사 레코드에
> 실려야 G 계층의 증거가 된다. **스키마는 W6가 소유**(계약 C-B v2)하고 **W3-5가 채운다** — 역방향 아님.

## 경계 (전 모듈 공통)

- **읽기 전용**(D-003) — 변경 명령 실행 경로를 만들지 않는다. `diagnosis-consumption`이 테스트로 고정한다.
- **`sre_agent` import 0**(D-118) — MCP JSON 계약만. 부하 가드·미들웨어 프로파일은 **요구로 전달**.
- **R-13** — `src/orchestration/intent_planner.py` · `AgentState.task_plan` · `TaskSpec` 미수정.
- **79 단독 소유 자산 무수정** — `src/prompts/semantic_router.py` · `src/routing/semantic_router.py` ·
  `src/nodes/input_parser.py` · `src/clients/instructor_adapter.py`(80 §6).
- **라우팅 미단언** — 어떤 테스트도 라우팅 결과·`relevance_score`·의도 분류를 단언하지 않는다(3-A 조건).

## 산출 규약

명세는 **`SPEC-composite-orchestration.md` 한 벌**이며, 모듈별로 `## M<n>. <module-id>` 절을 갖는다.
모듈별 파일로 쪼개지 않는 이유: 6대 영역 중 **Commands·Structure·Style·Testing·Boundaries가 8개 모듈에
동일**하다. 파일을 쪼개면 그 다섯이 8벌 복제되어 **D-053(사본 금지)** 위반이 된다. 모듈 절은
**Objective·Success Criteria delta만** 담고, 수용 기준은 `plans/78` 해당 Wave를 **참조**한다(중복 기술 금지 —
`plans/80` §5.3-④ 정합). 이 지도가 색인이며, 파일명 추측으로 스펙을 찾지 않는다.
