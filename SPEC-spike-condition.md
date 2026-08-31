# Spec: spike-condition (Wave 9 · 요구 5)

> 지도: `CAPABILITY-MAP-execution-groups.md` v3 · 계획: `plans/82` v6 §6.9~§6.13 · Wave 9
> 결정: **D-176 후속2**(착수 시 부기) · 선행: **Wave 8**(`config/change_terms.yaml` 공유)
> 사용자 확정: **U15=(b) 월 단위 한정** · **U17=승인(실행 불가 — 아래 ①)** · **U18=+20%p** · **U19=파일시스템 단위**

## ASSUMPTIONS (실측 확인분)

1. 시계열 통계 3단이 존재하고 세 프로파일 전부 `allowed_tables`에 등재돼 있다 —
   `cmm_metric_stat_h`(YYYYMMDDHH) · `_d`(YYYYMMDD) · `_m`(YYYYMM), 각 행 `min_val`·`avg_val`·`max_val`.
2. **"용량 대비 사용률"은 이미 계산되어 저장돼 있다** — `resource_type='server.FileSystems'` +
   `definition_name='Utilization'` 이 퍼센트다(`used/total` 불필요).
3. 결정적 조립 주입 지점은 `src/nodes/query_generator.py:494 _try_deterministic(state, ctx)` —
   `:898`에서 `_try_semantic`·`_llm_fallback`보다 **먼저** 호출되고 `{"sql": ...}`를 반환하면 채택된다.
4. 엔진 방언 3지점: 소수 보존(`::numeric` vs 집계 내부 `CAST(… AS DOUBLE)`) · 행 제한
   (`LIMIT` vs `FETCH FIRST n ROWS ONLY`) · 스키마 한정(`polestar` vs 대문자 `POLESTAR`).
   조립기에 `decimal_cast_example(db_engine)`(`assembler.py:29`)가 이미 있다.
5. 컴파일러가 *"전월 대비 증감"* 을 **커버리지 밖**으로 명시 선언한다(`src/prompts/semantic_compiler.py:52`).
6. `build_stat_month_block`(`src/utils/query_gen_common.py:179`)은 **단일 기간 등호/BETWEEN을 강제**하고
   *"`_h`/`_d`로 대체 금지"* 까지 지시한다 → **비교 모드와 배타**.
7. `metric_tables`에 **`week`가 없다**(`catalog.yaml:76-79` — hour/day/month만).

### ① U17은 승인받았으나 지금 실행할 수 없다

DBHub(`localhost:9099`) **연결 거부** · `ACTIVE_DB_IDS=polestar`(로컬 샌드박스, 실 운영 3존 아님).
계획서 §10.3이 예고한 상황이다. 따라서:

- **월 단위(1달 전 대비)만 구현한다**(U15=(b) 그대로).
- **주 단위 요청은 차단 경로로 구현**한다 — *"일별 통계 보존기간 미확인"* 사유 + 월 단위 대체 제안.
- DBHub 가동 시 실행할 프로브를 **1줄로 남긴다**:
  `SELECT MIN(stat_date), MAX(stat_date), COUNT(DISTINCT stat_date) FROM cmm_metric_stat_d`

## Objective

*"파일시스템 사용률이 갑자기 80% 이상으로 상승한"* 처럼 **기간 대비 급증 + 절대 임계**를 요구하는
질의를 **표현 가능하게** 만든다. G-4(급증 판정 코드 0건)의 원인은 데이터 부재가 아니라
**표현 경로의 부재**였다 — 파이프라인이 3겹으로 막고 있다(ASSUMPTIONS 5·6·7).

성공은 급증이 §6.13에서 **퍼널의 정식 단계**로 나타나는 것이다(더 이상 "미반영 경고"가 아니다).

## 조작적 정의 (사용자 확정 — 코드로 못 박는다)

| 축 | 확정 | 왜 |
|---|---|---|
| **판정식** | **차분(%p) AND 절대 임계** 병행 | 비율이면 5→10%(2배)가 75→85%를 이겨 저사용 파일시스템이 상위 점령 |
| **기준 시점** | **단일 직전 기간**(전월) | 검산 가능성이 핵심 가치. 분포 기준선은 비결정 지점 2개(창 크기·편차 배수) 추가 |
| **기본 임계** | **+20%p**(U18) · 응답에 **항상 노출** | 절대 임계(80%)가 병행 게이트라 저사용 노이즈는 이미 배제 |
| **집계 축** | **파일시스템 단위 행 유지**(U19) | 서버 AVG면 `/var` 30→90%가 서버 51%로 눌려 임계 80% 미달로 **놓친다** |

## Tech Stack

Python 3.12 · pytest. **신규 의존 0.** LLM 호출 **감소**(결정적 조립이 생성 단계를 우회).

## Commands

```bash
pytest tests/test_utils/test_comparison_periods.py -q
pytest tests/test_db_adapters/test_spike_sql.py -q
pytest tests/test_nodes/test_spike_wiring.py -q
python3 scripts/arch_check.py --ci
python3 scripts/overfit_check.py
```

## Project Structure

```
config/change_terms.yaml                신규 확장 — 급증 어휘 + default_delta_pp + default_baseline
src/domain/change_terms.py              확장 — 급증 의도·임계 해석 (Wave 8이 만든 로더)
src/utils/query_gen_common.py           수정 — resolve_comparison_periods 신설
src/db_adapters/polestar/spike_sql.py   신규 — 비교 SQL 조립 (엔진 분기 · 순수 문자열)
src/nodes/query_generator.py            수정 — _try_deterministic에 급증 조립 배선
src/config.py                           수정 — Text2SQLConfig 플래그 2개
tests/test_utils/test_comparison_periods.py    신규
tests/test_db_adapters/test_spike_sql.py       신규
tests/test_nodes/test_spike_wiring.py          신규
```

**`spike_sql.py`를 `assembler.py`에 넣지 않는 이유**: `assembler.py`는 폼필 피벗 전용(1,000행 규모)이고
동시 작업과 무관하게 이미 크다. 급증은 별 관심사이므로 형제 모듈로 둔다(단일 책임).

## Code Style

```python
def build_spike_sql(
    *,
    db_engine: str | None,
    db_schema: str | None,
    base_month: str,          # YYYYMM — 기준(이전) 기간
    cur_month: str,           # YYYYMM — 비교(현재) 기간
    threshold_pct: float,     # 절대 임계 (80)
    delta_pp: float,          # 차분 임계 (20)
    limit: int,
) -> str:
    """기간 대비 급증 SQL을 조립한다. 엔진 방언 3지점을 분기한다.

    GROUP BY에 파일시스템 행(r.name)을 유지한다 — 서버 단위로 접으면 급증이 희석된다(U19).
    HAVING 두 항이 차분·절대 임계 병행 판정이다(U18).
    """
```

## Testing Strategy

**전부 mock — DB·LLM 미사용**(D-127 · DBHub 미가동). SQL은 **문자열로 단언**한다.

| 대상 | 검증 |
|---|---|
| 기간 쌍 해석 | "1달 전 대비"/"전월 대비" → (직전월−1, 직전월) · 절대월 지정 · 미매칭 None · **주 단위 요청은 차단 신호** |
| 급증 의도 판정 | 선언 어휘 매칭 · 임계 명시("30% 이상 상승") 우선 · 미명시 시 기본값 + **노출 플래그** |
| SQL 조립(PG) | `::numeric` · `LIMIT` · `GROUP BY … r.name` 유지 · `HAVING` 2항 · 가드 `BETWEEN 0 AND 1000` |
| SQL 조립(DB2) | 집계 **내부** `CAST(… AS DOUBLE)` · `FETCH FIRST n ROWS ONLY` · `POLESTAR.` 대문자 한정 · `::numeric` **부재** 단언 |
| 판정 경계 | 5→10% 배제(차분 미달) · 75→85% 포함 · 79→99% 포함 · 60→85% 포함 · 85→90% 배제(차분 미달) |
| 한계 표기 | 기본 임계 사용 시 값 노출 · 용량 변경 미대조 각주 · 주 단위 요청 시 대체 제안 |
| 배선 | 플래그 off면 `_try_deterministic` **반환 None**(바이트 동일) · 재시도 턴 미진입 · 폼필과 배타 · `build_stat_month_block` **미주입** 단언 |

## Boundaries

**Always**
- 플래그 기본 **off** → 회귀 0.
- **읽기 전용** — SELECT만.
- 엔진 방언 3지점 분기(Wave 1 방언 그물이 잡는 대상이므로 애초에 맞게 낸다).
- 기본 임계값 사용 사실·값을 **응답에 노출**.
- 한계 3건(용량 변경 · 기본 임계 · 주 단위 미확인)을 **응답에 표기**.

**Ask first**
- 분포 기준선(평균±편차) 도입 — U15에서 범위 밖 확정
- 주 단위(`_d`) 실제 개방 — U17 프로브 실측 후
- CPU·메모리로 급증 확장 — 이번 범위는 파일시스템

**Never**
- 과거 용량을 EAV 현재값으로 대체 추정 (없는 것을 있는 척 하지 않는다)
- 서버 단위 AVG로 접기 (U19 — 급증 희석)
- 비율(배수) 판정 단독 사용 (저사용 노이즈)
- `build_stat_month_block`을 비교 모드에 함께 주입 (기간 규칙 충돌)
- 주 단위를 보존기간 확인 없이 "지원"으로 응답 (약속하고 침묵 누락 = 최악)

## Success Criteria

1. `"파일시스템 사용률이 갑자기 80% 이상 상승한 서버"` 가 **runnable 비교 SQL**로 조립된다.
2. 조립 SQL이 **엔진별로 세 지점 다르다**(캐스트·행제한·스키마).
3. **파일시스템 단위 행이 유지**된다(서버 AVG로 접히면 실패).
4. 차분·절대 임계를 **둘 다** 만족해야 포함된다(경계 5건 통과).
5. 기본 임계값이 응답에 **노출**되고, 주 단위 요청은 **사유 + 월 단위 대체 제안**이 나온다.
6. 플래그 off면 기존 테스트 전량 통과 · `_try_deterministic` 반환 불변.
7. `arch_check --ci` exit 0 · `overfit_check` 위반 0.

## Open Questions

- **U17 프로브 미실행**(DBHub 미가동) → 주 단위는 차단 경로로 구현. 가동 시 1줄 실행으로 해소.
