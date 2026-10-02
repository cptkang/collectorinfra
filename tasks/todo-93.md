# TODO 93 — 벤치마크 전수 설정 검증

- [x] T-01 `scripts/bench/catalog.py` — 카탈로그 어댑터 + F1 축 후보 필터
  - Acceptance: `field_index()`를 감싸 `KnobSpec` 목록을 내고, F1 제외 규칙(미소비·시크릿·URL/경로·보존정책·알람그룹)별로 사유를 남긴다
  - Verify: `pytest tests/test_scripts/test_bench_catalog.py -q`
  - Files: `scripts/bench/__init__.py`, `scripts/bench/catalog.py`, 테스트 1
- [x] T-02 `catalog.py` — L1 4자 정합
  - Acceptance: `.env` / `.env.example` / 카탈로그를 대조해 고아·누락·설명부재를 분류
  - Verify: 합성 파일 픽스처로 3분류 검출
  - Files: `scripts/bench/catalog.py`, 테스트
- [x] T-03 `scripts/bench/probe.py` — 자식 프로세스 실효값 에코
  - Acceptance: env를 주입해 자식에서 `load_config()` 결과를 JSON으로 받아온다. 실패는 사유를 담아 반환(예외 전파 금지)
  - Verify: 자기 프로세스 대상 왕복 1건 + 주입 러너 테스트
  - Files: `scripts/bench/probe.py`, 테스트
- [x] T-04 `scripts/bench/validate.py` — L3 주입 실효성
  - Acceptance: 주입값 ≠ 에코값이면 `ShadowedKey`로 분류하고 출처(os_env/encenv)를 판정
  - Verify: `monkeypatch.setenv`로 가림 재현
  - Files: `scripts/bench/validate.py`, 테스트
- [x] T-05 `validate.py` — L2 기동 안전성
  - Acceptance: 타입별 경계값 생성 → 격리 로드 → 성공/거부 분류, `ValidationError` 원문 보존
  - Verify: bool·int·enum·secret 각 1건
  - Files: `scripts/bench/validate.py`, 테스트
- [x] T-06 `validate.py` — L4 소비 실증
  - Acceptance: 값 변경 전후 실효 config 해시 비교로 `변함/불변/미검사` 판정 + `UNCONSUMED_KEYS` 3분류 대조
  - Verify: 대조 3분류 각 1건
  - Files: `scripts/bench/validate.py`, 테스트
- [x] T-07 `scripts/bench/report.py` — 건강 보고서 + 커버리지 장부
  - Acceptance: 전건 장부 · 미측정 사유 강제(없으면 예외) · 판정 5어휘 · `SUMMARY.md` 한눈에 블록
  - Verify: 전건 수 일치 · 사유 누락 시 예외
  - Files: `scripts/bench/report.py`, 테스트
- [x] T-08 `scripts/bench/axes.py` — 축 자동 선별·arm 전개
  - Acceptance: F1 통과분에서 영향경로 규칙으로 축을 뽑고 OFAT arm을 전개. `--explain` 근거 출력
  - Verify: 제외 규칙별 1건 + OFAT arm 수 단언
  - Files: `scripts/bench/axes.py`, 테스트
- [x] T-09 `scripts/bench/__main__.py` — 단일 진입·preflight·프로바이더 판정
  - Acceptance: `--preflight` / `--quick` / 기본 / `--show-env` · 내부망이면 승인 프롬프트 없음
  - Verify: 프로바이더별 판정 테스트 + 실제 `--quick` 실행
  - Files: `scripts/bench/__main__.py`, 테스트
- [x] T-10 게이트·문서 — `arch_check` · `overfit_check` · 전체 pytest · D-211 본문 등재 · 계획서 상태 갱신
  - Acceptance: 신규 실패 0 · D-211 등재 · `plans/93` `-TODO` 해제 판정
  - Verify: 세 게이트 그린
  - Files: `docs/02_decision.md`, `plans/INDEX.md`, 계획서

---

## 구현 기록 (2026-09-11)

| 항목 | 결과 |
|---|---|
| 신규 코드 | `scripts/bench/` 6모듈 (`catalog`·`probe`·`validate`·`axes`·`report`·`__main__`) |
| 신규 테스트 | `tests/test_scripts/test_bench_{catalog,probe,validate,report,axes}.py` **134건 통과** |
| `src/` 수정 | **0** — 경계 규칙 준수 |
| 신규 config 필드·플래그 | **0** — D-162 준수 |
| 실 LLM 호출 | **0** |
| arch_check / overfit_check | 둘 다 exit 0 · 신규 유입 없음 |

### 실측이 구현을 고친 것 5건

1. **F1 후 후보 148** — 계획서 추정 40~60은 과소. 40~60으로 좁히는 것은 F2의 몫이다.
2. **`auth.jwt_secret`이 기동마다 재생성** — 설정 지문이 매번 달라져 L4가 전 항목을 "변함"으로 오판한다.
   비결정 필드 **자동 탐지**로 막았다(하드코딩 제외 목록은 낡는다).
3. **env 접두 ≠ 그룹명** — `API_PORT`는 `server.port`다. 문자열을 깎지 않고 카탈로그 `field_name`을 쓴다.
   교정 후 **335필드 전건 경로 적중**.
4. **마스킹 이름 규칙 오탐** — `prompt_token_budget`이 "token" 부분일치로 시크릿 취급됐다.
   카탈로그 판정 + **꼬리 일치**로 교정.
5. **축은 1차·2차로 갈라야 한다** — 타임아웃류를 1차에 두면 47개가 된다. 타임아웃을 줄이는 것은
   빨라지는 것이 아니라 실패가 느는 것이다. 1차 28 · 2차 22.

### 첫 실행 결과 (`--quick` · 80초)

```
고아 키 0 · 가림 0 · 기동 실패 0        → 즉시 조치 0건
.env.example 누락 103 · 설명 부재 46     → 문서 재편(R1) 대상
등급 초안 A 31 · B 144 · C 160
1차 축 28 · 2차 축 22
```

### 2차 구현 기록 (2026-09-11 · 트랙 A~C)

| 항목 | 결과 |
|---|---|
| 신규 모듈 | `sweep.py`(94 러너 위 arm 조율) · `compare.py`(쌍체 통계·판정 5어휘) · `optimize.py`(10순위 처분·D-161 4항) |
| 신규 테스트 | `test_bench_sweep.py` **43건** · 전체 **179건 통과** |
| CLI | `--sweep`(dry/mock/run) · `--propose` 추가 |
| 재사용 | 서버 기동·주입 에코 검증·시나리오 실행·원시 적재는 **전부 94 소유**. 93은 조율·비교만 |

#### 실측이 설계를 고친 것 3건

1. **시나리오가 자기 프로파일을 지정한다** — 94의 `Scenario.profile`(기본 `baseline`) 때문에
   arm을 프로파일로 넣어도 **baseline만 돌았다**(실측: 32행·arm 1개). 94의 질문(*"이 기능이 이 설정에서 동작하나"*)에는
   맞는 설계지만 93의 질문(*"같은 질의가 설정 A와 B에서 어떻게 다른가"*)에는 맞지 않는다.
   → **시나리오를 arm 수만큼 복제**하며 `profile`만 갈아끼운다(id는 유지해야 쌍이 맺어진다). 교정 후 288행·9 arm.
2. **mock의 `manual` 판정을 통과로 세면 안 된다** — 합격도 불합격도 아니다. 통과로 세면 전 arm이 100%가 되어
   비교가 무의미해진다. 정확도 비교에서 제외하되 **지연·호출 수는 그대로 비교**한다(mock 실행의 가치가 거기 있다).
3. **불일치 쌍이 적으면 효과가 커 보여도 판정하지 않는다** — 20건 표본에서 1건 차이는 5%p라 임계를 넘지만
   정보는 1건뿐이다. 가드가 없으면 작은 표본에서 큰 효과가 만들어진다(§2-② 검정력 문제의 정확한 실패 유형).

#### 실행 확인

```
--sweep --mode dry    arm 9개 전개 (기준선 + 축 4개 × 2수준)
--sweep --mode mock   288행 수집 · 부트스트랩 신뢰구간 산출 · 판정 문장 생성
--propose --limit 30  즉시 수정 15 · 등급 강등 15 · 제안서 3종 생성
```

### 잔여

- [x] T-10 D-211 본문 등재 · 계획서 상태 갱신 (전체 스위트 확인 후)
- [x] `bench-sweep`·`bench-compare`·`bench-optimize` — **완료(2026-09-11)** · 94 실행 원자 위에 배선
- [ ] **L5 실 스위프** — 내부망에서 `--sweep --mode run --scale full` (승인 불요 · 코드는 준비됨)
- [ ] 워크로드 선별 정교화 — 94 카탈로그에서 축 비교 적격 케이스만 고르는 규칙(현재는 정상 군 전체)
