# Plan 50 실행 계획 — 장애진단·원인분석 잔여(v2.1 · G1~G6)

> `plans/50` §0(v2.1) · `CAPABILITY-MAP-50.md` · D-197 예정 · 사용자 확정(2026-09-02):
> **6모듈 전부 순차** · 브리핑 계약 수정은 **챗·알림 둘 다 즉시 노출**(플래그 없음)

## 구현 순서와 그 이유

```
M1 briefing-contract        ← 출력 계약이 깨진 채로는 뒤 모듈의 산출물 도달을 검증할 수 없다
M2 incident-window-tools    ← 읽기 경계(mcp_server)의 구간 앵커 SQL — 미지정 시 SQL 문자열 동일
M3 investigation-guidance   ← system_prompt_additions 배선 1곳 — Plan 51 §6 플레이북의 진입점
      ↓ (M1·M2·M3는 서로 독립 — 순차로 두되 파일이 겹치지 않는다)
M4 incident-scope           ← M2 인자를 채우는 쪽: sre_diagnose 시각 인자 · pull 파싱 · push alarmTime
      ↓
M5 evidence-correlation     ← M2 도구를 결정적으로 사전 호출 + 상관 계산(sre_agent)
      ↓
M6 diagnosis-briefing       ← M5 결과를 M1 계약으로 표현(rank·confidence·상대시각)
      ↓
M7 문서(D-197 · plans/50 §0.3 해소 표기 · INDEX)
```

## 검증 체크포인트

| 이후 | 확인 |
|---|---|
| M1 | 두 소비자 출력에 `[한계]`·`[가설]`·`[중요도]` 도달 · repr 0건 · 스텁 비트 동일 · `sre_agent/tests` 무회귀 |
| M2 | 인자 미지정 시 생성 SQL **문자열 동일**(회귀 0) · 앵커 지정 시 `NOW()` 부재 · `mcp_server/tests` |
| M3 | `_default_diagnose_fn`이 `system_prompt_additions`를 전달 · 지침 off면 종전과 동일 |
| M4 | `sre_diagnose(reference_time=…)` 수용 · 질의 "어제 14시" 파싱 · push가 `alarmTime`을 전달 |
| M5 | 상관 순수 함수 골드 케이스(선행성·z-score·시차) · MCP 미가용 시 `notes`에 결손 기록 |
| M6 | 브리핑에 `root_cause_hypotheses[]`·`T-15m` 타임라인 · M1 렌더러로 사용자 도달 |
| 전체 | `arch_check --ci`(본체+sre_agent) · `overfit_check --ci` · 실 LLM 호출 0(D-127) |

## 리스크와 대응

| 리스크 | 대응 |
|---|---|
| 기존 테스트가 잘못된 키(`evidence`·`limitation`)를 정답으로 굳힘 | 삭제하지 않고 정본 키로 **갱신**(사유를 docstring에 남긴다) |
| `sre_agent`는 루트 venv에서 import 불가 | 계약은 **양쪽에 같은 리터럴 키 집합**을 두는 대칭 테스트로 고정한다(양방향 import 0 유지) |
| 앵커 인자가 종전 SQL을 바꾼다 | 기본값 None 경로의 SQL을 **문자열로 스냅샷** 단언 |
| DB2 방언(FETCH FIRST·TIMESTAMP 연산) | 앵커는 `_time_floor` 자리만 바꾼다 — 방언 분기는 기존 코드가 담당 |
| `evidence-correlation`이 LLM을 부른다 | 순수 함수 + 도구 호출만. LLM 호출 경로 없음(D-035) |
| `overfit_check`가 `noise_gate/domain` 신규 파일을 스캔 | 도메인 렌더러에 스키마·제품명 리터럴을 두지 않는다 |

## 범위 밖

다중서버 연쇄 RCA · 진단 이력/피드백(Phase C′) · `POST /diagnosis/analyze` · 실 LLM 조사 실행.
