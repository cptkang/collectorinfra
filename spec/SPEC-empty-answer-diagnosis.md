# Spec: empty-answer-diagnosis (Wave 8 · 요구 4)

> 지도: `CAPABILITY-MAP-execution-groups.md` v3 · 계획: `plans/82` v6 §6.1~§6.8 · Wave 8
> 결정: **D-176 후속1**(착수 시 부기) · 선행: 1차 4개 모듈(구현 완료)

## ASSUMPTIONS (실측 확인분 — 추정 아님)

1. 0건 응답은 `src/nodes/output_generator.py:266 _generate_empty_result_response(parsed)` 한 곳에서
   조건 무관 정형문으로 생성된다. 호출부는 `:241`(`_generate_text_response`, `state` 보유).
2. 0건 + `aggregation`이면 `result_organizer:_check_data_sufficiency` Case 1이 `False`를 반환해
   **재생성 루프가 돈다**(`:312-316`).
3. `group_results[*].row_count`는 **1차에서 이미 구현**됐다(`src/nodes/multi_db_executor.py`).
4. 복합 경로 분해 규칙은 `src/prompts/intent_planner.py:138-168`에 **이미 있고** t1 행 수가
   `task_results`에 남는다.
5. SQL 실행 경로는 `get_db_client(app_config, db_id=...)` + `client.execute_sql(sql)`
   (`src/nodes/query_executor.py:96-99`).
6. domain 계층에서 **선언 YAML 직접 로드는 선례가 있다** — `src/domain/middleware.py`가
   `config/middleware_signatures.yaml`을 `yaml.safe_load`로 읽는다(외부 패키지만 의존).
7. `Text2SQLConfig`(`src/config.py:242-338`, `env_prefix="TEXT2SQL_"`)에 **동시 작업 hunk가 없다**
   (동시 hunk는 777·796·975·1114 — `NoiseGateConfig`·`CompositeConfig`·`AppConfig`).

## Objective

조건이 여럿인 질의가 0건일 때 *"조건에 해당하는 데이터가 없습니다"* 만 내보내던 것을
**어느 조건에서 끊겼는지**로 바꾼다. 사용자가 확인해야 하는 것은 세 가지다(요구 4 원문):

- CPU 80% 넘는 게 **없는지**
- 넘는 것 중 파일시스템 80% 넘는 게 **없는지**
- **갑자기** 80이 된 게 없는지 (→ 표현 자체가 안 됐다면 그 사실 · G-4)

성공은 **끊긴 지점이 응답에 특정되는 것**이다. 문헌 용어로 XSS(마지막 성공 단계)와
MFS(최초 실패 단계)를 사용자에게 보여준다(`EMPTY-MFS-01` Godfrey IJCIS 1997).

## ★ 계획서 §6.4에서 벗어난 지점 1건 (근거 첨부)

**계획서는 "`filter_conditions` 누적 프로브"라고 썼다. 그대로는 구현할 수 없다** —
`filter_conditions`는 자연어 서술이고 SQL 조건과 1:1이 아니다. 실 SQL의 최상위 conjunct에는
**사용자 조건과 스키마 배관이 섞여 있다**:

```sql
WHERE r.resource_type = 'server.FileSystems'   -- 배관
  AND s.definition_name = 'Utilization'        -- 배관
  AND s.max_val BETWEEN 0 AND 1000             -- 배관(쓰레기 값 가드)
  AND r.dtime IS NULL                          -- 배관
HAVING MAX(...) >= 80                          -- ★ 사용자 조건
   AND MAX(...) >= 80                          -- ★ 사용자 조건
```

배관을 벗기면 **무의미한 수**가 나온다. 따라서 프로브 대상은 **화이트리스트로 좁힌다**:

> **사용자 조건 = 지표 값 컬럼(`avg_val`/`max_val`/`min_val`) 또는 SELECT 별칭에 대한
> 수치 비교(`>=` `>` `<=` `<`)** 이고, `BETWEEN`은 제외한다(가드 패턴).

식별된 사용자 조건이 **0개면 프로브를 돌리지 않고** 신호 기반 퍼널 + 미반영 경고로 강등하며
**사유를 응답에 남긴다**(침묵 금지). 이 이탈은 계획서 §6.4의 의도(단계별 잔존 건수)를 유지하되
**"어떤 조건이 사용자 조건인가"를 결정적으로 판정 가능한 범위로 좁힌 것**이다.

## Tech Stack

Python 3.12 · pydantic v2 · PyYAML · pytest / pytest-asyncio. **신규 의존 0.**

## Commands

```bash
pytest tests/test_domain/test_empty_answer.py -q
pytest tests/test_nodes/test_condition_probe.py -q
pytest tests/test_nodes/test_empty_diagnosis_wiring.py -q
python3 scripts/arch_check.py --ci
python3 scripts/overfit_check.py
```

## Project Structure

```
config/change_terms.yaml            신규 — 변화·급증 어휘 + 기본 임계 선언 (Wave 9와 공유)
src/domain/change_terms.py          신규 — 선언 파일 로더 (domain · middleware.py 선례)
src/domain/empty_answer.py          신규 — 퍼널 조립 · 미반영 판정 · G-5 판정 · 렌더 (순수)
src/nodes/condition_probe.py        신규 — SQL conjunct 수술 + COUNT 프로브 (실행은 주입)
src/nodes/output_generator.py       수정 — 0건 응답에 진단 주입
src/nodes/result_organizer.py       수정 — 0건일 때 프로브 실행 + G-5 재생성 판정
src/state.py                        수정 — empty_diagnosis 필드(요청 스코프)
src/config.py                       수정 — Text2SQLConfig에 플래그 2개
tests/test_domain/test_empty_answer.py        신규
tests/test_nodes/test_condition_probe.py      신규
tests/test_nodes/test_empty_diagnosis_wiring.py 신규
```

## Code Style

domain은 **순수**다 — I/O·LLM·전역 상태 없음. 선언 파일 로더만 예외(`middleware.py` 선례).

```python
@dataclass(frozen=True)
class FunnelStage:
    """퍼널 한 단계. counts의 키는 그룹 키(단일 그룹이면 "")."""

    label: str
    counts: dict[str, Optional[int]]   # None = 미측정(프로브 실패·상한 절단)
    source: str                        # "probe" | "group_results" | "task_results"


def build_diagnosis(
    *,
    parsed: dict,
    stage_counts: Sequence[FunnelStage],
    unexpressed: Sequence[str],
    notes: Sequence[str],
) -> EmptyDiagnosis:
    """퍼널 단계에서 XSS/MFS를 판정한다. 입력만으로 결정되며 부작용이 없다."""
```

## Testing Strategy

**전부 mock — LLM·네트워크·DB 미사용**(D-127). 프로브 실행은 콜백 주입으로 대체한다.

| 대상 | 검증 |
|---|---|
| `change_terms` 로더 | 선언 파일 파싱 · 누락 키 기본값 · 파일 부재 시 빈 규칙(예외 아님) |
| 미반영 판정(G-4) | 변화 어휘 있고 대응 조건 없음 → 경고 / 어휘 없음 → 무경고 / 어휘 있고 조건도 있음 → 무경고 |
| 퍼널 XSS/MFS | 1204→12→0이면 MFS=3단계·XSS=2단계 / 전 단계 0이면 P0 신호 / 그룹 2개 열 분리 |
| G-5 판정 | P0>0 → 재생성 **중단** / P0=0 → 재생성 **허용** + 스코프 경고 |
| conjunct 수술 | HAVING·WHERE 최상위만 · 서브쿼리 내부 불변 · 문자열 리터럴 내 키워드 무시 · `BETWEEN` 제외 · 사용자 조건 0개면 프로브 미생성 |
| COUNT 래핑 | `ORDER BY`·`LIMIT`·`FETCH FIRST` 제거 · 파생 테이블 별칭 부여(DB2 필수) |
| 배선 | 플래그 off면 **응답 바이트 동일** · 결과 있으면 **프로브 0회**(호출 수 단언) · 프로브 예외 시 현행 문구 + 사유 |

## Boundaries

**Always**
- 플래그 기본 **off** → 회귀 0. off 경로는 바이트 동일.
- **0건일 때만** 프로브 발동. 결과가 있으면 호출 0회.
- 프로브 상한 K(기본 5) 초과 시 **절단 사실을 응답에 노출**.
- 실패·강등은 **사유를 구조화해 응답에 노출**(침묵적 폴백 금지).
- 매 태스크 `arch_check --ci` 통과.

**Ask first**
- 그래프 노드 추가·순서 변경 (이번 범위 밖 — `result_organizer` 안에서 호출한다)
- `config.py`의 `CompositeConfig`·`NoiseGateConfig`·`AppConfig` 수정 (동시 작업 hunk 존재)
- 자동 조건 완화 후 **재조회** (U16 = 제안까지만)

**Never**
- 프로브에서 SELECT 외 SQL 생성 (읽기 전용 · `COUNT(*)`만)
- 배관 conjunct 제거 (무의미한 수 산출)
- LLM 호출 추가 (프로브는 **LLM 0회**)
- 급증 판정 구현 (→ `spike-condition` 소관)

## Success Criteria

1. 조건 3개 질의가 2단계에서 끊기면 응답에 **단계별 잔존 표 + "여기서 끊겼습니다"** 가 나온다.
2. 변화 어휘가 있는데 대응 조건이 없으면 **미반영 경고**가 나온다(G-4).
3. 그룹 2개면 퍼널이 **존별 열**로 나뉜다(`group_results` 재사용 — 재구현 0).
4. P0>0이면 재생성이 **중단**되고, P0=0이면 **허용**되며 스코프 경고가 붙는다(G-5).
5. 플래그 off면 기존 테스트 전량 통과 + 0건 응답 **바이트 동일**.
6. `arch_check --ci` exit 0 · `overfit_check` 위반 0.

## Open Questions

없음 — U14(즉시 착수)·U16(제안까지만) 확정. 프로브 화이트리스트 이탈은 위 §에 근거 첨부.
