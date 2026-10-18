# 170. 스키마 캐시 Facade 완결(I1) · `pii_filter` 설정 읽기 통합과 침묵 예외 정리(X2·X5)

> **작성일**: 2026-10-16 · **v1.1**
> **상태**: **완료**(2026-10-16 · 커밋 전 — 메인이 커밋) — Facade 우회 10곳 중 7곳 교체·3곳 seam 잔류 · `_prefer_redis` 38곳 · 유사어 27메서드 `RedisSynonymStore` 합성 · 키 빌더 `schema_key` · `pii_filter` `_flag` · 계획 끝 회귀 실패 0(모듈 단위) · verifier Critical·Major 0 · §4
> **상위**: `plans/166` §0.1 단계 5 · **계획 ID 5c**(W3 쌍 경로 공통화 · 5a ∥ 5b → 5c)
> **성격**: 동작 불변 구조 리팩터(D-337) — 캐시 키·TTL·저장 경로·응답·문구·기본값·플래그 불변 · PII 차단·마스킹·FabriX 필터 대응 불변 · 단계 1 스냅샷 갱신 금지 · 테스트 단언 변경 금지
> **계획 시작 SHA**: `3a0a1101`(RCA) — **실제 기준은 그 위의 미커밋 5b 상태**(`plans/169` — `src/nodes/multi_db_executor.py` 등. 5b가 커밋 전이라 worktree 격리를 쓰지 않고 같은 작업 트리에서 파일이 겹치지 않게 트랙을 나눈다). 5a(`plans/168` · `src/api/routes/query.py`)도 같은 트리에서 미커밋 진행 중 — 이 계획은 그 파일을 건드리지 않는다.
> **회귀 대상 파일(누적)**: 14 — `src/schema_cache/{cache_manager,redis_cache,structure_store}.py` · 신규 `src/schema_cache/redis_synonyms.py` · `src/nodes/{schema_analyzer,field_mapper,multi_db_executor}.py` · `src/document/excel_csv_converter.py` · `src/security/pii_filter.py` · `scripts/overfit_baseline.json` · 신규 테스트 `tests/test_security/test_plan170_{pii_flag,verify_pii}.py` · `tests/test_schema_cache/test_plan170_verify_{synonym_store,facade}.py` *(누적 끝 — 계획 끝 회귀 1회: §4. verifier 테스트 3파일은 회귀 뒤 추가 — verifier가 직접 실행)*
> **관련 결정**: D-337(리팩터 규약 ①~⑦) · D-331(게이트 — arch 경고 기준선 · 폴백 린트) · D-316 부기(163 D4 — `invalidate(db_id)`가 `knowledge_seeded`도 지움) · D-294 부기(163 D3 — `ADMIN_ASSET_SUFFIXES`에 `asset_drafts`·`ddl_comments`) · D-075(유사어 거버넌스 OFF = 저장 구조 바이트 불변) · D-155(PII 차단 덤프) · D-003 · D-303(회귀)
> **적용 스킬**: `.claude/skills/refactoring/` · `.claude/skills/secure-coding/`(E 로그·PII — 보안 동작을 바꾸지 않는다 · D-337 ⑥)

---

## 1. 범위 (166 §3 I1·X2·X5 행 · §5.5 · §5.3 W1 X5 행 — 줄 번호는 2026-10-16 작업 트리 재실측)

| 스멜 | 위치(재실측) | 처방 | 성공 기준 |
|---|---|---|---|
| **I1-a** Facade 우회 | `SchemaCacheManager`(`src/schema_cache/cache_manager.py` · 2,021줄) 밖에서 private `_redis_cache` 직접 접근 10곳: `src/nodes/schema_analyzer.py:939,940,947,954,973` · `src/nodes/multi_db_executor.py:3182,3188`(5b 반영 뒤 위치) · `src/document/excel_csv_converter.py:250,269` · `src/nodes/field_mapper.py:446` | **Facade 완결** — 우회 지점마다 Manager 공개 메서드(순수 위임 · 연결 확인 추가 없음)를 두고 교체. **단, 그 지점을 지나는 테스트가 가짜 매니저(MagicMock·자체 대역)에 `_redis_cache`만 심어 두는 경우는 교체하면 테스트가 깨지므로 seam 소유 지점으로 남기고 기록한다**(D-337 ② · 테스트 수정 0) | 호출부 테스트 무수정 통과 · W0 fakeredis 스냅샷(`tests/test_schema_cache/test_plan166_w0_redis_keyspace.py`) 동일 |
| **I1-b** 분기 중복 | `cache_manager.py`의 `self._backend == "redis" and await self.ensure_redis_connected()` 39회 | `_prefer_redis()` 헬퍼 1개 — 평가 순서·단락 평가 그대로 | 같은 테스트 · 스냅샷 동일 |
| **I1-c** God class 키 공간 | `RedisSchemaCache`(`src/schema_cache/redis_cache.py` · 2,192줄 · 키 공간 7종) | **Composition(synonyms 먼저)** — 유사어 키 공간 메서드 본체를 하위 저장소 객체로 옮기고 `RedisSchemaCache`의 같은 이름 메서드는 위임만(호출부 무변경). 나머지 키 공간(descriptions·csv·form_memory 등)은 이 계획 밖 | 유사어 테스트 무수정 통과 · **governance OFF 저장 바이트 불변**(D-075) · 스냅샷 동일 |
| **I1-d** 키 규칙 중복 | `StructureStore._key`(`src/schema_cache/structure_store.py:178`)와 `RedisSchemaCache._key`(`redis_cache.py:193`)가 `schema:{db_id}:{suffix}`를 각각 정의 | 키 빌더 1곳(모듈 함수) — 두 `_key`는 그것을 부른다(이름·시그니처 유지) | 스냅샷 동일 |
| **X2·X5**(`pii_filter` 분) | `src/security/pii_filter.py:225,285,298,311,324` — 플래그 5개마다 함수 하나 · 매번 `load_config()` · `except Exception: return 고정값`(침묵) | `_flag(name, default)` 헬퍼 1개로 5함수 본체 통합(공개 함수 5개·이름·독스트링 유지) · 설정 로드 실패 시 **같은 고정값**을 돌려주되 `warning` 한 줄(예외 종류만 — 스택·원값 없이 · W1 verifier 기준) | 플래그 on/off·로드 실패 파라미터 테스트 · 반환값(방향) 불변 · caplog 단언 |
| **X2**(AppConfig 받는 클래스) | 166 처방 「이미 AppConfig를 받는 클래스의 `load_config()` 직호출 제거」 | 팀 리드 AST 실측(src·noise_gate · `__init__`에 config 인자 ∧ 메서드에서 `load_config()`) → **1건**: `RedisSchemaCache._governance_enabled`(`redis_cache.py:213`). 이 클래스는 `redis_config`·`schema_cache_config`만 받고 `AppConfig`·유사어 설정은 받지 않으며, 테스트가 `patch.object(RedisSchemaCache, "_governance_enabled")`(5파일)와 `load_config` 실패 주입(`test_plan166_w1_verify_guards.py:127`)으로 호출 시점 읽기에 기대고 있다 → **해당 없음으로 닫는다**(생성자 주입은 읽는 시점이 바뀌는 행동 변경 · §4 이탈) | — |

**범위 밖(기록만)**: `schema_cache` 패키지 안의 `_redis_cache` 접근(`form_memory.py:61`·`source_memory.py:257` — 같은 패키지 내부 · `noqa: SLF001` 명시) · `SynonymLoader`·`AdminJobRunner`·`StructureStore`가 `RedisSchemaCache`를 주입받아 쓰는 것(정상 의존) · 유사어 외 키 공간 합성 · 백엔드 Strategy(166 §3 I1 — 미도입) · W0 부록 이상 5번 중 163이 고친 2건 외 `structure_meta` 키 공유(I1-d가 규칙만 한 곳으로 모은다 — 키 자체는 그대로).

## 2. 설계 제약(팀 리드 실측 · 2026-10-16)

- **직접 대입 seam**: 테스트 34파일·213지점이 `_redis_cache`를 언급한다. 가짜 매니저에 `_redis_cache`만 심는 형태 — `tests/test_excel_fill_pipeline.py:103-105`(`MagicMock()` 매니저 + `_redis_cache.load_csv_cache/save_csv_cache`) · `tests/test_nodes/test_plan120_field_mapper_priority.py:53-61`(`_FakeCacheManager` — 공개 메서드 일부만 정의) · `tests/test_nodes/test_synonym_set.py:35` · `tests/test_semantic_routing/test_synonym_set_routing.py:71,170`(MagicMock 매니저) · `tests/test_nodes/test_cache_management_synonyms.py:205`. 진짜 `SchemaCacheManager`에 `_redis_cache`를 대입하는 형태(`test_plan31_field_mapping_fix.py:43` 등)는 공개 메서드가 `self._redis_cache`를 호출 시점에 읽으면 안전하다.
- **RedisSchemaCache seam**: `patch.object(RedisSchemaCache, "_governance_enabled", …)` 5파일 · 인스턴스 `_redis` 대입(fakeredis 부착 `attach_fake_redis`) · 테스트 4지점·2파일 `patch("…redis_cache.…")`. → 하위 저장소 객체는 소유자 참조를 들고 **호출 시점에** `owner._redis`·`owner._key`·`owner._governance_enabled()`를 읽고, 유사어 메서드끼리의 내부 호출도 소유자를 거쳐(`owner.<메서드>`) 인스턴스·클래스 patch가 그대로 걸리게 한다.
- **pii_filter seam**: 5함수는 함수 안에서 `from src.config import load_config`를 해 테스트의 `src.config.load_config` patch를 호출 시점에 받는다 — `_flag`도 같은 지연 import를 유지한다. 호출부 `multi_db_executor.py`(5)·`query_generator.py`(4)는 바꾸지 않는다.
- **로거 이름**: 옮긴 코드의 로거는 원 모듈 이름(`src.schema_cache.redis_cache`)으로 고정(166 W2 선례).
- **overfit 기준선**: 리터럴이 든 코드를 새 파일로 옮기면 새 경로가 신규 유입으로 걸린다 — 기준선(`scripts/overfit_baseline.json` · 5b 미커밋 변경 포함)은 팀 리드가 자기 델타만 옮긴다.

## 3. 실행 (3트랙 · 같은 작업 트리 · 파일 겹침 0)

| 트랙 | 스멜 | 담당 파일 | 비고 |
|---|---|---|---|
| A | I1-a·I1-b | `src/schema_cache/cache_manager.py` · `src/nodes/{schema_analyzer,field_mapper,multi_db_executor}.py` · `src/document/excel_csv_converter.py` | `multi_db_executor.py`는 5b 반영본 위에서 해당 2줄만 |
| B | I1-c·I1-d | `src/schema_cache/redis_cache.py` · 새 하위 저장소 모듈 1 · `src/schema_cache/structure_store.py` | |
| C | X2·X5 | `src/security/pii_filter.py` | |

Wave 중간 회귀: 트랙마다 자기 테스트 + `python scripts/regress.py --base 3a0a1101 --no-tests --files <바꾼 파일…>`. 계획 끝 1회 `python scripts/regress.py --base 3a0a1101 --files <누적…>`(팀 리드).

## 4. 결과 (2026-10-16)

### 4.1 스멜별

| 스멜 | 결과 | 비고 |
|---|---|---|
| I1-a | **교체 7 · 잔류 3**. 교체: `schema_analyzer.py` 5(`load_resource_type_synonyms`·`load_eav_name_synonyms`·`load_global_synonyms` · `SynonymLoader`·값 인덱스에 `redis_cache` 프로퍼티) · `multi_db_executor.py` 2. 잔류(seam 소유 · 주석 1줄): `field_mapper.py:446`(`test_plan120_field_mapper_priority.py:53-61` `_FakeCacheManager` — 교체 시 AttributeError가 `except`에 삼켜져 결과가 조용히 바뀜 · `test_plan31_field_mapping_fix.py` MagicMock 매니저) · `excel_csv_converter.py:250,269`(`test_excel_fill_pipeline.py:103-105,136-138,169-171` MagicMock 매니저) | 새 Manager 공개 경로 4: `redis_cache` 프로퍼티 · `load_resource_type_synonyms` · `load_eav_name_synonyms` · `load_global_synonyms`(순수 위임 — 연결 확인·폴백 없음 · 기존 `get_global_synonyms`와 다름을 독스트링에 명시). 판정은 10지점 임시 계측 + 관련 테스트 약 12.5K건 실행으로 지나는 대역 타입을 확인(계측 제거 · 바이트 원복 확인) |
| I1-b | `_prefer_redis()` · **38곳 치환**(한 줄 36 · `seed_words and await self._prefer_redis()` 합침 1 · 부정형 `if not await self._prefer_redis():` 1) · 미치환 1(`get_db_description_origin` — 중간 조건 `redis_cache is not None`이 있어 평가 순서 보존) | verifier가 진리표 43건을 기준 worktree에서도 실행해 ensure 호출 횟수·저장 호출·반환 동일 확인 |
| I1-c | `RedisSchemaCache` 2,192→1,249줄 · 유사어 메서드 **27개** 본문을 새 `src/schema_cache/redis_synonyms.py` `RedisSynonymStore`(1,205줄)로 이동 · 원 메서드는 위임 한 줄 · 하위 객체는 소유자 참조로 `_redis`·`_connected`·키 상수·`_key`·`_governance_enabled`·`_ensure_meta`·`_merge_llm_words`와 유사어 메서드끼리의 호출을 **호출 시점에 소유자를 거쳐** 읽음 · 로거 이름 `src.schema_cache.redis_cache` 고정 · 순환 import 0 | AST 대조 로직 차이 0(타입 주석 정밀화 로컬 9·인자 5 — 런타임 무영향 · 이탈 §4.3) · 기준 worktree vs 현 트리 fakeredis 하네스(유사어 27메서드 전 분기 73스텝 × governance OFF/ON · 반환값·로그 레코드·전 키 덤프) `cmp` 동일 130,057B — verifier가 원본·신규 실제 로드를 확인하고 재실행 |
| I1-d | 모듈 함수 `schema_key(db_id, suffix)`(`redis_cache.py`) — `RedisSchemaCache._key`·`StructureStore._key`가 호출(이름·시그니처 불변) | |
| X2·X5(`pii_filter`) | 917→909줄 · `_flag(name, default)` 1개 · 5함수 한 줄 위임(이름·독스트링 불변) · 지연 import 유지 · 로드 실패 시 같은 고정값 + `warning` 「PII 설정 조회 실패(필드) — 간주값로 간주: 예외 종류」(원문·스택 없음) | verifier 차분 80입력 반환 동일 · 스크럽·마스킹·차단·덤프 diff 0 · 보안 동작 변경 없음 |
| X2(AppConfig 받는 클래스) | **해당 없음** — §1 표 실측 | |

### 4.2 검증

- **계획 끝 회귀(팀 리드 1회)**: `python scripts/regress.py --base 3a0a1101 --files <11파일>` 종료 0 — 본체 통과 3,607 · 실패 0 · 에러 0 · 건너뜀 20 · 실패 귀속 「실패 없음」 · arch·overfit·config_fallback·ruff·mypy 통과(이번 diff 줄 0) · 권고 블록 없음 · `범위: 모듈 단위 — 전체 미실행`. 5b 미커밋 변경이 섞였지만 실패가 0이라 귀속 worktree는 쓰지 않았다.
- 게이트: arch 경고 17(기준 유지) · overfit 기준선 자기 델타 1(`redis_cache.py` → `redis_synonyms.py` `server.Cpu` 키 이동) · `catalog_diff --ci` 0 · `prompt_render_diff --ci` 바이트 동일 · import 스모크(`src.graph`·`src.api.server`·`structure_store`·`pii_filter`).
- W0 스냅샷 `test_plan166_w0_redis_keyspace.py` 무갱신 통과 · 테스트 파일 수정 0.
- implementer 자기 테스트: A 89파일 1,530 통과 · B 74파일 1,421 통과 · C 37파일 1,542 통과 + 신규 15 (병행 편집 중 일시 실패 2건은 상대 트랙 편집 도중 소스 읽기 — 최종 재실행 통과).
- verifier: Critical 0 · Major 0 · Minor 4 · 정보 2 · 변이 16/16 검출 · 새 검증 테스트 3파일 142건(+ 관련 2파일 합 168 통과) · `regress --no-tests` 종료 0.

### 4.3 이탈·잔여 (기록 — 다음 계획 대상)

1. **seam 잔류 3곳**(I1-a): 없애려면 테스트 대역을 고쳐야 한다(D-337 주의 ② — 다음 계획에서 테스트 수정과 함께).
2. **공허해진 소스 가드**(verifier Minor): `tests/test_semantic_routing/test_synonym_set_routing.py:146-150`의 `inspect.getsource(RedisSchemaCache.add_global_synonym / load_global_synonyms)`에 `GLOBAL_SYNONYMS_KEY` 포함 단언이 위임 독스트링으로 통과한다(쓰기 키만 바꾼 변이를 못 잡음). 이 계획은 테스트 무수정이라 새 검증 테스트(`test_plan170_verify_synonym_store.py` — 하위 저장소 소스의 `owner.GLOBAL_SYNONYMS_KEY` 참조 · fakeredis 왕복)로 보강했다. 원 테스트 대상 이동은 다음 계획.
3. **집계 누락 Facade 우회 5곳**(verifier Minor · 범위 밖): `getattr(<매니저>, "_redis_cache", None)` 형태 — `src/nodes/cache_management.py:1042` · `src/document/mapping_feedback.py:168` · `src/document/redis_registration.py:214,341,361`. 166 「10곳」은 `._redis_cache.` grep 기준이었다. 후속에서 `redis_cache` 프로퍼티로 교체(대역 seam 확인 선행). 같은 패키지 내부 `form_memory.py:61`·`source_memory.py:257`은 범위 밖 유지.
4. **이름 혼동 위험**(verifier Minor): Manager `load_global_synonyms`(순수 위임)와 `get_global_synonyms`(연결 확인 + YAML 폴백). 후속에서 이름 구분 검토.
5. **schema_analyzer 교체 지점 기존 테스트 공백**(verifier Minor · 이번 diff가 만든 것 아님): 단일 경로 노드의 5지점 결과를 단언하는 기존 테스트가 없다(`except`가 삼킴). 후속에서 단일·멀티 대칭 테스트 권고.
6. **타입 주석 정밀화**(이탈): 이동 본문의 `dict`→`dict[str, Any]` 14곳·`Optional[str]`→`str | None` 1곳 — 새 파일 전 줄이 diff 줄로 잡혀 mypy·ruff 신규 위반이 되기 때문. 런타임 무영향(`from __future__ import annotations`). 되돌리는 비용: 해당 줄 원복 + 게이트 예외 처리.
7. **경고 반복**(정보): `pii_filter` 경고는 설정 로드가 실패하는 동안 호출마다 나간다(질의당 여러 번). 1회 경고로 바꾸는 것은 행동 변경 — 필요하면 별도 계획.
   → plans/182 #M4
8. shim·별칭: 없음(원 메서드가 위임으로 남아 공개 API 동일). 매뉴얼 미반영: 사용자 접점 변경 없음(D-255).

## 5. 변경 이력

- 2026-10-16 v1.1 — 3트랙 구현(A Facade·`_prefer_redis` / B 유사어 합성·키 빌더 / C `pii_filter`) · 팀 리드 overfit 기준선 델타 1 · 계획 끝 회귀 실패 0 · verifier Critical·Major 0 · 파일명 `-WIP-` 태그 제거.
- 2026-10-16 v1.0 — 작성. 166 §5.5 I1·X2·X5 행을 옮기고 줄 번호 재실측 · X2 「AppConfig 받는 클래스」 AST 실측 1건(해당 없음 판정) · 3트랙 분할.
