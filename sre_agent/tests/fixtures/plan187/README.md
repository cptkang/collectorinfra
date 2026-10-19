# plans/187 골든 픽스처 (W0 · 2026-10-19 · 99ec1ea4 실측)

플레이북 실행기(W1~W2)·판독 규칙 R-A~R-H(W3)·브리핑·표시(W4~W6)의 공용 입력과 기대값이다.
모양 검사는 `sre_agent/tests/test_plan187_w0_fixtures.py`가 한다.

## 파일

| 파일 | 시나리오 | 플레이북 |
|---|---|---|
| `normal.json` | 정상(알람만 울림 · S3 skipped) | was.latency |
| `latency_sql.json` | 지연 — SQL 실행 지배 · 같은 업무 다른 호스트까지(공통 의존) | was.latency |
| `latency_external.json` | 지연 — 외부 호출 지배 · 단일 서비스 · 인스턴스 한정 | was.latency |
| `gc.json` | 힙 바닥 상승 + GC 비중 증가 | was.latency |
| `partial_missing.json` | 결손 — 런타임 오류·느린 거래 0건·폴스타 지표 2축 실패·프로세스 API 미설정 | was.latency |
| `case1_jdbc_acquire.json` | 제니퍼 사례 ① JDBC GET-CONNECTION 15.6s vs 실행 7ms | was.latency |
| `case2_ldap_external.json` | 제니퍼 사례 ② LDAP 외부 호출 · 시스템 CPU 50% vs Java 15~20% | was.latency |
| `case3_cpu_loop.json` | 제니퍼 사례 ③ TPS 무관 CPU 급등(배포 50분 뒤) | was.latency |
| `case4_oom_periodic.json` | 제니퍼 사례 ④ OOM 3시간 주기 | was.heap |
| `polestar_unresolved.json` | (W2 추가) 폴스타 서버 미확정 — 페이로드에 폴스타 소스 없음 → 폴스타 축 호출 0 · S4·S6 missing · S5·S7 폴스타 축 결손(「폴스타 서버 미확정」) | was.latency |
| `sme_review_catalog.csv` · `sme_review_rules.csv` | §4.1 카탈로그 · §4.2 판독 규칙 SME 검토용(「SME 의견」 칸 공란 · UTF-8 BOM — 엑셀용) | — |

## 형식

```json
{
  "fixture_version": 1, "scenario": "...", "title": "...",
  "alarm_time": "2026-10-19T10:00:00", "tool_reference_time": "2026-10-19T10:10:00",
  "incident_window": {"start": "...", "end": "...", "minutes": 40},
  "baseline_windows": {"prev_day": "...", "prev_week": "..."},
  "notes": ["시나리오 가정·주의"],
  "alarm": { plans/153 apm_trigger_payloads.json 과 같은 트리거 payload },
  "tool_responses": {"<도구>": [{"args": {...}, "response": {...}}]},
  "expected": {
    "playbook_id": "was.latency",
    "s0": {"alarm_instance": "A1", "host": "...", "polestar_source": "...", "polestar_server": "...",
           "targets": [{"instance": "A1", "verdict": "normal|degraded"}]},
    "readings": {"scope", "service_spread", "load_mode", "little_check", "dominant", "db_split",
                 "cpu_owner", "heap_shape", "host_saturation", "fs_eta", "change_candidates"},
    "case_conclusion": {"db_split": "acquire|execute"},
    "section_order": ["S0", ...], "section_status": {"S0": "ok|missing|skipped"},
    "missing": {"S3": ["apm_slow_transactions"]},
    "top_hypothesis": "...", "deep_dive_expected": false, "realtime_steps": ["S2", "S6"]
  }
}
```

- **녹화 조회 규칙**: 호출 `(도구, args)`에 대해 그 도구의 녹화 중 **녹화 args가 호출 args의 부분집합인 첫 항목**을 돌려준다.
  맞는 녹화가 없으면 「미녹화」 = 그 호출은 계획 밖이다(실행기 테스트는 실패로 본다).
- **응답은 도구가 실제로 내는 JSON 그대로**다 — 게이트웨이 봉투(`rows`·`row_count`·`source_kind="apm_api"`·`limits`·`sources`·
  `instance_resolution`·`was_signals` …)와 폴스타 `_ok` 봉투(`source_kind="polestar_db"`·`engine`·`window` …).
  오류는 게이트웨이 `{"error": 코드, "reason", "source_kind", "source", "tool"}` · 폴스타 `{"error": 문장}`.
- `readings` 값 `null` = 판정 불가(입력 결손). enum 어휘는 계획서 §4.2.
- `readings.db_split`은 **전 건 `n/a`**다 — 게이트웨이 `apm_transaction_profile`에 커넥션 획득·SQL 실행을 가르는 구조화 필드가 없고
  `profile_excerpt` 텍스트 형식도 미검증이다(W0 실측). 문헌 사례의 결론은 `case_conclusion.db_split`에 따로 둔다.
  `profile_excerpt` 안의 `GET-CONNECTION …` 같은 줄 형식은 **가정**이다.
- `section_order`는 §4.1 스텝 순서(was.latency S0~S9 · was.heap S0→S2→S1→S7→S4→S9). S8(DPM)은 미연동이라 `skipped`.
- 리터럴은 전부 가짜다 — 호스트 `wasdemo01`·`wasdemo02` · 인스턴스 `A1`(2001)·`A2`(2002)·`B1`(2101) · 업무 `계좌조회` ·
  IP `10.10.0.x`. 폴스타 소스 `polestar`·서버명은 호스트명과 같다고 **가정**한다(S0 → 폴스타 연결 브리지는 현재 없다 — 계획서 §2.3).
- R-G 코어 수는 4코어로 **가정**한다 — 현 도구 중 코어 수를 주는 것이 없다.
- 호출 수는 사건창 40분 기준이며 리틀 법칙(액티브 ≈ TPS×응답시간)과 맞게 잡았다.

## 다시 만들기

W0 생성기는 일회성이라 저장소에 두지 않았다. 고칠 때는 응답 모양(실측 키 집합)을 유지하고 값만 바꾼다. 게이트웨이·폴스타 도구의 응답 키가 바뀌면 테스트 상수(실측 사본)와 픽스처를 함께 바꾼다.
