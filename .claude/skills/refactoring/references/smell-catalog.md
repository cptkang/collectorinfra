# 스멜 → 처방 카탈로그 (이 저장소 실측 사례 포함)

스멜 이름은 Fowler(2018)·Sharma & Spinellis(2018) 용어를 따른다. 「사례」는 `plans/162`(분석 SHA `7ba5c96`)의 스멜 ID다 — 줄 번호는 그 시점 값이니 작업 전에 다시 찾는다.

| 스멜 | 알아보는 신호 | 처방(기법) | 원칙/패턴 | 하지 말 것 | 사례 |
|---|---|---|---|---|---|
| **Divergent Change · God module** | 한 파일을 서로 다른 계획들이 고친다 · 머리 docstring에 계획 번호 누적 · 책임 구간이 줄 범위로 나뉜다 | Extract Module + 원 모듈 재노출 shim · 순수 함수부터 이동 | SRP · Facade(원 모듈) | 이동과 로직 분해를 한 커밋에 | O1 `apm_query.py` · A2 `query.py` · N2 `query_gen_common.py` · I2 `field_mapper.py` |
| **Large Class · God class** | 메서드 50+ · private 접두 메서드 군이 주제별로 묶임 | Extract Class(합성) — 하위 객체를 속성으로 보유하고 기존 메서드는 위임 | SRP · Composition | 믹스인·상속으로 쪼개기 · 이벤트 버스 | G2 `AlarmWorker` · M2 `ApmTools` · I1 `RedisSchemaCache` |
| **Long Function** | 근사 cc>50 · 200줄+ · 지역 변수 25+ · early return 10+ | Compose Method · Extract Function · Introduce Parameter Object(`@dataclass` 컨텍스트) | SRP | 분기 순서 변경 · 로그 문구 변경 | O2 `run_apm_query` cc184 · A3 스트림 핸들러 |
| **순서 있는 if/early-return 사슬** | 단계 번호(6.2·7.5…) 중간 삽입 반복 · 같은 모양의 가드가 나열 | `RULES: tuple[tuple[str, Callable], ...]` + for 루프 | Chain of Responsibility(함수 튜플형) · OCP | 규칙별 클래스 계층 | G1 `decide_notification` · O7 `replanner` · N7 노트 체인 |
| **Switch on closed vocabulary** | `if kind == "a" … elif kind == "b"` — 어휘가 닫혀 있다 | `{kind: handler}` dict dispatch | 경량 Registry · OCP | Strategy 클래스(공유 상태가 크면 인자만 늘어남) | O2 `ViewSpec.target` · O9 `_coerce_arg` · A2 진행 요약 |
| **Duplicated Code (쌍 경로)** | 단일/멀티·스트림/비스트림·텍스트/파일 경로가 2/3 이상 같음 · 한쪽만 고친 사고 | Extract Function — 차이는 **인자**로 · 공통 순수 함수가 dataclass를 반환하고 경로별로 렌더만 | DRY | Template Method(훅 3개 이하) · 서로 다른 개념을 이름이 같다는 이유로 합치기 | A1 `event_generator` 2벌 · N1 폼필 피벗 |
| **SSOT 위반 — 상수 사본** | 같은 리터럴 상수가 여러 모듈에 정의 | 상수 모듈 1곳(가장 안쪽 계층) + import | SSOT | 패키지 경계를 넘는 공유 모듈 | X3 `DEGRADED_KEY` 4벌 · G5 `alarm:` 키 |
| **SSOT 위반 — 판정 사본** | 본문이 같은 판정 함수가 2벌 이상 | 정본 1곳 + import. 출력이 다를 수 있으면 **먼저 특성화**, 다르면 합치지 않는다 | SSOT | 출력이 다른 사본을 「통일」(= 행동 변경) | X4 `_capability_ownership_on` · `_zone_label` 3벌 |
| **SSOT 위반 — 설정 기본값 이중 정의** | `getattr(cfg, "필드", 기본)`의 기본이 필드 기본과 다름 | 존재하는 필드는 `cfg.<그룹>.<필드>` 직접 접근 · 재유입 린트(D-331 ②) | SSOT · fail-fast | 폴백값을 필드 기본에 「맞춰」 남기기 | X1 약 80곳(`zone_group_exclusive` 폴백 True ↔ 필드 False) |
| **Hidden global dependency** | 하위 계층이 `load_config()`·`os.getenv`를 직접 호출 · 테스트가 `cache_clear`·monkeypatch 의존 | 인자·생성자 주입 | DIP | 구현 하나뿐인 Protocol 신설 | X2 `pii_filter` · X6(동작 변경 — `plans/163`) |
| **Facade bypass (Inappropriate Intimacy)** | 외부가 `obj._private` 직접 접근 | Facade에 공개 메서드 추가 → 호출부 교체 → 우회 0 확인 | Facade 완결 · 캡슐화 | 새 Facade 층 신설 | I1 `_redis_cache` 10곳 |
| **`_` 접두 공유 함수** | `from x import _name`이 모듈 밖에 | 공개명 승격 + 옛 이름 별칭 1사이클(D-330 ③) | 명시적 인터페이스 | 별칭 없이 바로 개명 | O4 `_make_isolated_input` · M2 `_Limits` |
| **Cyclic dependency** | 함수 내부 지연 import로 순환 회피 | 의존 없는 leaf 모듈(타입·스펙)을 분리 → 상단 import | ADP(비순환 의존) | `TYPE_CHECKING` 남발로 감추기 | O4/O5 `SubAgentSpec` |
| **Misplaced helper** | 그래프 노드 패키지에 노드가 아닌 순수 헬퍼 → 노드 간 import 경고 | Move Module(비노드 application 하위로) | SRP · 계층 정합 | 「그래프 간선으로 해결」 | N3 `column_deriver` 등 |
| **Primitive Obsession (dict 상태)** | 키 오타·누락 사고 반복 · 출력 노드가 선언 안 된 키를 읽음 | TypedDict 선언 · 키 정합 테스트 | 타입으로 SSOT | 전면 dataclass/pydantic 전환(체크포인터·`**extra`와 충돌) | N6 `AgentState` 136필드 · O7 핸들러 결과 |
| **Silent fallback** | `except Exception: pass/return 상수` | `logger.warning(..., exc_info=True)` 추가 또는 구체 예외로 축소 · 반환값 불변 | 침묵 폴백 금지(CLAUDE.md) | 651곳 일괄 치환 | X5 `redis_cache.py` 7 |
| **Boilerplate repetition** | 같은 9줄 블록이 14회 · 같은 except 관용구 32회 | 작은 헬퍼 함수 1개 | DRY | 데코레이터·동적 시그니처 생성(외부 계약 흔들림) | M1 `register_tools` · M3 `ApmError` 관용구 |

## 「합치지 말 것」 체크 (Kapser & Godfrey 2008)

중복처럼 보여도 다음이면 그대로 둔다. ① 본문은 같아도 **바뀌는 이유가 다르다**(다른 단·다른 패키지가 독립 진화) ② 출력이 입력 일부에서 다르다(특성화로 확인) ③ 패키지 경계(D-139·D-274)를 넘는다 → 계약 스냅샷 테스트로 대신 ④ 「이름만 같다」(`_norm`·`_normalize`·`_parse_ts`).
