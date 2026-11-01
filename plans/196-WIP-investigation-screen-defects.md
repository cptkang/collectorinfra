# 196. 장애 진단 조사 화면 결함 정리 — 폴스타 브리지 미구현 외 (내부망 실화면 판독)

> **작성일**: 2026-10-27 · **v0.2**(같은 날 — D1을 유사 호스트 후보·사용자 선택·연결 학습 설계로 교체 · §1.1)
> **상태**: **WIP** — 2026-10-27 W1·W2a·W2b·W2c·W3·W4(D3 공통 원칙·D4·D7) 구현 · 검증·보안 감사 교정 완료 · 같은 날 잔여 라운드(사건 종합 조사 연결 부착 · `was.sql` 증상 진입 · D7 축별 집계 · 저장 원자화·상한 Redis 공유 · 재조회 재호출 유지 확정 · D-348 부기 1) · 커밋 없음 · **잔여 = W0(내부망 실측 · §5.7) + 매뉴얼 캡처 2장(로컬 PG·Redis 컨테이너 정지로 미촬영)** · Q1 운영 `.env` · Q2~Q7은 **권고 적용 · 사용자 확인 전**
> **분석 기준 HEAD**: `1f61461f`
> **계획 시작 SHA**: `59e53749`
> **회귀 대상 파일**: 47개 — 목록은 `git diff 59e53749 --name-only`(plans·docs 제외) · 계획 끝 회귀 `regress.py --base 59e53749 --files <47>` rc=0(본체 통과 3,591 · 원래 실패 3 · sre_agent 통과 2,518) + 교정 라운드 선택분 재실행(본체 1,805 · sre_agent 2,524 · 실패 0) · 범위: 모듈 단위 — 전체 미실행 · **잔여 라운드(2026-10-27)** `regress.py --base 59e53749 --files <19>`(이번 라운드 바꾼 파일) rc=1 → 원인 = 이번 diff 주석 1줄 E501 → 고친 뒤 `--no-tests` rc=0 · 테스트 본체 통과 3,171·실패 0 · sre_agent 통과 2,532·실패 0 · 범위: 모듈 단위 — 전체 미실행
> **165와의 범위(2026-10-27)**: 이 계획은 조사 쪽(sre_agent·noise_gate·본체 조사 API·장애 진단 화면) 브리지와 **공용 hostname 정규화 함수 정의**(`src/domain/host_names.py`)를 소유한다. `plans/165` F3(text2sql `assembler.py` HAVING · `key_bridge.py` 승계)는 **범위 밖**이며 165에 남는다 — 165 F3가 이 계획의 정규화 함수를 쓴다(의존 방향 196 → 165).
> **사용자 제기(2026-10-27)**: *"폴스타 서버 브리지 미구현 등 수정해야 되는 것들을 이미지 분석을 통해 정리하고 파일로 작성하라."*
> **근거 화면**: 내부망 KB AIOps 포털 「장애 진단」 탭 → 「조사 흐름」(모니터 촬영 사진 1장)
> **관련 계획·결정**: `plans/165` F3(제니퍼 → 폴스타 hostname 정확 일치) · `plans/187` W21 P6(폴스타 소스 대체 매핑)·§8 위험 1·미결 20 · `plans/195`(조사 화면 조사 내용·다시 조회 질의) · D-341(조사 플레이북) · D-325 ③(사용자 뷰) · D-255(매뉴얼 동반)

---

## 0. 화면 판독 (사진 기준 사실)

| 항목 | 화면 문구 |
|---|---|
| 알람 | 제니퍼 이벤트 `SQL_TOOMANY_FETCH` · 대상 `bfm-kbland-admin-price-deploy-6dd49d5df6`(K8s 디플로이먼트/파드 이름 형태) |
| 조사 이유 | 「플레이북 **fallback** 선택 — 맞는 유형 플레이북이 없어 공통 점검(사용량 추이·관련 알람·재발·최근 변경)을 돈다」 · 「심층 조사(HolmesGPT) 시작 — **parallel** — …」 |
| 진행 줄 | 「자동 조사 · 플레이북 + HolmesGPT · 진행 S5/5 · 완료 · **2시간 39분**」 |
| 지금까지 | 「판정 없음」 |
| S4 호스트 자원 시간대별 추이 | 결과: 수집 결손 — 폴스타 서버 미확정 — APM 알람 페이로드에 폴스타 소스(`meta.episode.infra_db_ids`)가 없고 설정된 폴스타 소스도 없다 — APM 호스트 → 폴스타 서버 브리지 미구현(plans/165 F3) · 해석: AI 해석 없음 — 단계 상태 **missing** — 소견 없음 |
| S5 전후 연관 알람 | 조회: 제니퍼 이벤트(09:03~09:43) ✓ 3.3s **7행** · 결과: **판정 없음 · 결과 0건 · 일부 결손** · AI 해석: 「폴스타 서버 미확정 및 브리지 미구현으로 인해 전후 연관 알람 데이터를 수집하지 못했습니다」 |
| S5a 같은 알람 재발 이력 | S4와 같은 수집 결손 문구 · AI 해석 없음 — 단계 상태 missing |
| S7 최근 변경 대조 | 조회: 제니퍼 소스 변경 이력 ✓ 2.8s 0행 · 제니퍼 변경 영향 ✓ 2.8s 0행 · 결과: 판정 없음 · 결과 0건 · 일부 결손 · AI 해석: 「폴스타 서버 미확정 및 ITSM 미연결로 인해 변경 이력을 수집하지 못했습니다」 |
| 우측 | 「✓ 3.3s · 상관 ≠ 인과 — …」 배지가 화면 밖으로 잘림(사진 잘림일 수도 있음) |

**결론**: 플레이북 4단계 중 폴스타 축 2단계(S4·S5a)는 통째 결손, 나머지 2단계는 제니퍼 축만 돌았다. 화면에 확정 소견이 하나도 없다. 원인 대부분이 **폴스타 소스·서버 미확정**(D1) 하나에서 갈라져 나온다.

---

## 1. 결함 목록

심각도: **A** = 조사 결과가 비거나 틀림 · **B** = 사용자를 오도하는 표시 · **C** = 문구·표시 품질.
「코드 확인」은 이 문서 작성 때 소스에서 원인 지점을 확인했다는 뜻이고, 「추정」은 내부망 재현이 필요하다.

### D1 (A) APM 알람 → 폴스타 소스·서버 브리지 없음 — 코드 확인

- **증상**: S4·S5a 전체 결손, S5·S7 폴스타 축 결손. 제니퍼 알람으로 시작한 조사는 호스트 자원·알람 이력을 하나도 못 본다.
- **원인**:
  - `sre_agent/sre_agent/application/playbook_runner.py:260-283` `resolve_polestar_source` — APM 트리거는 ① `meta.episode.infra_db_ids[0]` ② 호출자 `configured`(= W21 P6 설정 매핑 `SRE_PLAYBOOK_POLESTAR_SOURCE_MAP`) 둘 다 없으면 `POLESTAR_BRIDGE_MISSING`.
  - `meta.episode.infra_db_ids`는 noise_gate 크로스소스 사건(`noise_gate/domain/cross_source.py:300`)에 인프라 멤버가 묶였을 때만 생긴다 — 폴스타 알람이 같이 안 뜬 단독 APM 알람에는 없다.
  - 내부망 sre_agent `.env`에 `SRE_PLAYBOOK_POLESTAR_SOURCE_MAP`이 비어 있다(기본 `{}` · `sre_agent/sre_agent/settings.py:330` · 187 미결 20 미결정).
  - 소스가 정해져도 서버명은 `initial_scope`(`playbook_runner.py:330-339`)가 제니퍼 `hostname`을 **그대로** `polestar_server`로 쓴다 → 165 F3(대소문자·FQDN 정확 일치) 결함을 그대로 물려받는다.
- **추가 위험(추정 · 확인 필요)**: 이번 대상은 `…-deploy-6dd49d5df6` — **K8s 파드**로 보인다. 제니퍼 hostname이 파드 이름이면 폴스타에 등록된 서버(워커 노드·VM)와 이름이 애초에 다르다. hostname 정규화(F3)만으로는 안 붙고 **파드 → 노드** 연결이 따로 필요하다.
- **수정 방향(v0.2 · 사용자 지시 2026-10-27)**: *"폴스타의 유사 정보를 찾는 방식으로 처리 — 호스트 이름은 유사 호스트를 찾아 사용자가 선택하게 하고, 유사 패턴을 정리해 향후 연관성을 찾는 데 활용."*
  → **유사 후보 검색 → 사용자 선택 → 확정 연결 저장 → 패턴 정리·재사용**의 4단으로 푼다. 아래 §1.1이 설계다.
- **즉시 대책(설정만 · 코드 0 · 병행 가능)**: 폐쇄망 sre_agent `.env`에 `SRE_PLAYBOOK_POLESTAR_SOURCE_MAP={"cm_prd":"polestar_cm_gp", …}`. 소스(DB)만 정해지고 서버명은 여전히 제니퍼 hostname 원문이다 — §1.1이 들어오면 후보 검색의 「어느 DB부터 볼지」 우선순위로만 쓰인다.
- **165 F3와의 관계**: F3는 text2sql 쪽(`src/db_adapters/polestar/assembler.py` HAVING 정확 일치) 결함, D1은 조사 쪽 브리지 **부재**다. §1.1의 정규화 규칙(대소문자·FQDN 짧은 이름)은 F3 수정안과 같은 규칙이므로 한 정의(`src/domain` stdlib 순수 함수 — noise_gate 역방향 import 허용 범위 · D-139 부기)를 같이 쓴다.

### 1.1 D1 설계 — 유사 호스트 후보 · 사용자 선택 · 연결 학습

**원칙**
- **자동 결합은 확정 연결만.** 유사 후보는 아무리 점수가 높아도 사람이 고르기 전에는 폴스타 축을 부르지 않는다 — D-188 「모호하면 승격하지 않는다」·D-224 ⑤ 「일치·가능·모호를 코드가 판정」과 같은 보수 원칙(다른 서버 이력을 근거로 붙이는 오진이 미표시보다 나쁘다).
- **LLM 0.** 후보 검색·점수·패턴 추출은 전부 결정적 코드다. 연결 저장은 **사람의 선택에서만** 일어난다(Known Mistakes 「LLM 자동 등록은 쓰기 지점에서 결정적으로 차단」 — 오염 자기강화 방지).
- **결손과 대기를 나눈다.** 후보는 있는데 아직 안 고른 상태는 「수집 결손」이 아니라 「서버 선택 대기」(skipped)로 표시한다.

**① 후보 검색 (결정적 · 존 순회)**

| 단계 | 넓힘 | 근거 · 재사용 |
|---|---|---|
| a | 대소문자 무시 정확 일치(hostname·name) | `build_host_probe_sql` `names` (`noise_gate/infrastructure/polestar_hostname_resolver.py:133`) |
| b | FQDN ↔ 짧은 이름 | 같은 함수 `prefixes`(`LIKE 'x.%'`) + 입력이 FQDN이면 첫 레이블 |
| c | IP 일치 | 같은 함수 `ips` — 제니퍼 인스턴스 IP가 응답에 있을 때(W0 실측) |
| d | **K8s 이름 정규화** — 파드 해시(`-[0-9a-f]{8,10}` ReplicaSet · `-[a-z0-9]{5}` 파드) 제거 → 디플로이먼트 이름(`bfm-kbland-admin-price`) · `-deploy` 같은 꼬리 제거 | 신규 순수 함수. 정규화된 이름의 **토큰**(`kbland`·`price` 등 길이 ≥ 3)으로 `LOWER(name) LIKE '%tok%'` 넓힘 — 상한 행(소스당 50)으로 자른다 |
| e | **학습 패턴** 적용 결과(③) | 이전 확정에서 뽑은 변환을 입력에 적용해 나온 이름을 a로 다시 조회 |

- SQL은 넓게 좁히기만 하고, 점수·등급은 코드가 매긴다: **확정**(③ 확정 연결 일치) > **일치**(a) > **유력**(b·c·e) > **유사**(d — 토큰 겹침 비율 + `difflib.SequenceMatcher` 비율 · 표준 라이브러리). 화면에는 상위 5건과 **왜 후보인지**(「대소문자만 다름」 「FQDN 짧은 이름」 「IP 같음」 「디플로이먼트 이름 토큰 kbland·price 일치」 「이전 확정 패턴 P3 적용」)를 함께 보인다.
- 검색 범위는 인가된 존의 폴스타 소스 전부(`host_sweep.authorized_zones` 규약 재사용). 소스별 개별 try — 실패는 「확인하지 못함」으로 남기고 「후보 없음」과 합치지 않는다.
- 캐시하지 않는다(D-224 entity_locator 규약 4와 같음 — 방금 등록된 서버를 놓치지 않게). 확정 연결(③)은 캐시가 아니라 저장된 사실이다.

**② 사용자 선택 (장애 진단 화면)**
- S0에서 확정 연결·일치가 없으면 조사 브리핑에 `host_candidates`(후보 목록 · 등급 · 사유 · 소스)를 싣고, 폴스타 축 단계(S4·S5a 등)는 「서버 선택 대기 — 후보 n건」(skipped)으로 둔다. 제니퍼 축 단계는 그대로 진행한다.
- 「조사 흐름」의 해당 단계 카드에 후보 목록(라디오) + **[이 서버로 다시 조회]** + **[해당 서버 없음]** 을 둔다. 후보 0건이면 서버명 직접 입력(입력값도 a~c로 검증 후 존재할 때만 받음)을 연다.
- [이 서버로 다시 조회]는 같은 조사의 **폴스타 축 단계만** 다시 돈다(제니퍼 축 재호출 0). 페이로드 힌트 `meta.hints.polestar_link = {db_id, server_name, hostname, basis:"사용자 선택", by, at}`로 넘기면 sre_agent `resolve_polestar_source`가 이를 `infra_db_ids` 다음 순위의 사실로 받고, 근거 문장(「사용자 선택으로 연결 — 누가·언제」)을 남긴다(침묵 대체 금지 · W21 P6과 같은 형식). 재조회 뒤 종합(S9)만 다시 돈다.
- [해당 서버 없음]은 부정 연결로 저장한다 — 같은 hostname이 다시 오면 후보를 내지 않고 「폴스타 미등록(사용자 확인 · 누가·언제)」으로 바로 생략한다. 파드처럼 원래 폴스타에 없는 대상이 반복해서 사람을 부르지 않게 한다.

**③ 확정 연결 저장 · 유사 패턴 정리**
- **확정 연결**: 키 `(제니퍼 source_id, 정규화 hostname)` → `{polestar db_id, server_name, hostname, 등급(선택 당시), 선택자, 시각, 조사 id}` 또는 부정 연결. 다음 알람부터 **확정** 등급으로 자동 사용하되 화면에 근거(「이전 확정 — 누가·언제」)와 [연결 변경]을 항상 보인다.
- **유사 패턴**: 확정 연결이 쌓일 때 제니퍼 이름 → 폴스타 이름 **변환 규칙**을 결정적으로 뽑아 건수와 함께 누적한다.

  | 패턴 종류 | 예 |
  |---|---|
  | 대소문자 · FQDN 접미 제거 | `WEBAP01.kb.local` → `webap01` |
  | K8s 해시·꼬리 제거 | `bfm-kbland-admin-price-deploy-6dd49d5df6` → `bfm-kbland-admin-price` |
  | 접두·접미 치환 | `bfm-` ↔ `kbl-` · `-ap` ↔ `ap` |
  | 구분자 정규화 | `-`·`_`·`.` 차이 |
  | 노드 연결(컨테이너) | 디플로이먼트 `bfm-kbland-*` → 워커 노드 `kbl-k8s-w0[1-3]` (W5 · 노드 출처 확보 시) |

- 패턴의 쓰임은 **후보 순위 올리기와 사유 표시뿐**이다(① e). 패턴만으로 자동 연결하지 않는다. 운영자가 패턴을 「규칙 승격」하면 그 패턴에 맞는 새 hostname은 **유력** 등급 1건일 때 자동 연결하는 안은 §4 Q7로 남긴다.
- 관리자 화면에 「호스트 연결」 목록(확정·부정 연결 · 패턴과 적용 건수 · 선택자·시각)과 삭제·변경을 둔다. 변경·삭제는 감사 로그 대상.
- **같은 자산을 다른 곳에서도 쓴다**: 165 F3(text2sql 제니퍼 → 폴스타 승계)·plans/189 Prometheus 앵커도 확정 연결을 「일치」로 쓸 수 있다 — 이 계획 범위 밖, 후속 연결 후보로만 적는다.

**④ 배치 (패키지 경계)**
- 후보 검색·확정 연결 저장·패턴 추출은 **noise_gate**(본체와 같은 프로세스)에 둔다. 폴스타 접근(`probe_hosts`)·D-188 서버 식별이 이미 여기 있고, 선택 UI·API가 본체에 있다. 저장소는 Redis(TTL 없음 · 키 `hostlink:{source_id}:{hostname}` · 패턴은 별도 해시) — 운영 Redis 영속 설정은 W0에서 확인.
- APM 알람 수신·수동 조사 제출 때 noise_gate가 확정 연결을 찾아 페이로드(`meta.hints.polestar_link`)에 싣는다 — D-188 `attach_server_identity`와 같은 지점. sre_agent는 받은 사실만 쓰고 후보 검색을 하지 않는다(import 0 · 계약은 페이로드 · D-139).
- 후보 목록은 조사 브리핑에 싣지 않고 화면이 열 때 본체 API(`GET …/host-candidates`)로 받는 안도 있다 — 브리핑 크기와 최신성(그 사이 등록된 서버)을 보면 API 쪽이 낫다(§4 Q3).
- 새 기능 플래그 기본 off = 현행(「폴스타 서버 미확정」)과 비트 동일. 구현 끝에 새 D-번호(호스트 연결 학습 계약) 등재 · D-188·D-224·D-341 부기.

**위험 · 한계**
- **파드 대상**: 이번 화면처럼 대상이 파드면 폴스타에 같은 이름의 서버가 없다. d의 토큰 검색이 업무 서버(같은 업무명의 VM)를 후보로 낼 수 있는데, 그 서버는 파드가 실제로 돈 노드가 아니다 — 후보 사유에 「이름 토큰 유사 — 실행 노드 아님 가능」을 붙이고, 노드 출처가 확보되면(W5) 노드 연결을 우선한다.
- **비슷한 이름의 다른 서버**(`price-ap01`↔`price-ap02`): 자동 연결 금지와 사유 표시로만 막는다 — 선택 오류는 [연결 변경]과 관리자 목록으로 바로잡는다.
- **선택 권한**: 한 사람의 선택이 이후 모든 조사에 쓰인다 — 누가 확정할 수 있는지는 §4 Q5.

### D2 (A) SQL_TOOMANY_FETCH 같은 APM 알람이 호스트용 fallback 플레이북으로 감 — 코드 확인

- **증상**: 제니퍼 SQL 이벤트인데 「공통 점검」(호스트 CPU·메모리·FS·디스크 추이 · 폴스타 알람 재발)만 돈다. 4단계 중 2단계가 폴스타 전용이라 D1이 겹치면 절반이 무조건 결손.
- **원인**: `sre_agent/config/playbooks/was.latency.yaml:7` `any_kind`가 `latency, response_time, was_external_call_delay, was_slow_sql`만 받는다. `SQL_TOOMANY_FETCH`(과다 fetch)는 어느 WAS 플레이북에도 안 걸려 `fallback.yaml`로 떨어진다. `fallback.yaml`은 호스트 알람을 전제로 짜여 있다.
- **수정안**: ① 제니퍼 SQL 계열 이벤트(`SQL_TOOMANY_FETCH` 등 — 이벤트 유형 정규화 값 실측 후)를 받는 매칭 추가 — 기존 `was.latency`에 넣을지 SQL 전용 플레이북(활성 SQL · 느린 트랜잭션 분해 · 소스 변경)을 새로 둘지 결정(§4 Q2) ② APM 알람용 fallback(제니퍼 이벤트·골든 시그널·소스 변경 위주)을 따로 두어 호스트용 fallback과 나눈다.

### D3 (B) 「조회 7행」인데 결과는 「결과 0건」 — 원인 추정

- **증상**: S5 조회 줄은 제니퍼 이벤트 7행, 결과 줄은 「판정 없음 · 결과 0건」. 사용자는 조회가 헛돈 것으로 읽는다.
- **확인한 것**: 결과 줄은 `sre_agent/sre_agent/domain/step_findings.py:438-444` `_generic`이 섹션 `table.rows`(= `related_alarms`의 타임라인 · `playbook_steps.py:1722-1731`)를 센다. 타임라인은 제니퍼 이벤트 중 `time_ms`가 ISO로 바뀐 것만 `EpisodePoint`로 들어간다(`playbook_steps.py:1685-1705` · `_epoch_ms_iso` `:865`). 게이트웨이 행에는 `time_ms`가 있다(`apm_core.py:625`).
- **추정 원인(재현으로 확정)**: ⓐ 브리핑 저장·사용자 뷰 변환 중 `table.rows`가 비워짐 ⓑ 조회 줄의 「7행」이 `row_count`(게이트웨이 봉투)이고 실제 `rows`는 다른 키/0건 ⓒ `_epoch_ms_iso`가 서버 로컬 TZ로 바꿔 타임라인 축이 어긋남(이 경우 행은 남으므로 가능성 낮음).
- **수정안**: 내부망 해당 조사의 브리핑 JSON(S5 섹션 `table`·`data.apm_events`)을 떠서 원인을 확정한 뒤 고친다. 공통 원칙: 조회 행 수와 결과 행 수가 다르면 그 차이(「이벤트 7건 중 타임라인 반영 0건 — 사유」)를 결과 줄에 적는다.

### D4 (B) AI 해석이 결손 사유를 실제보다 넓게 씀 — 코드 확인(프롬프트 입력)

- **증상**: S5 해석은 「폴스타 미확정으로 데이터를 수집하지 못했다」고 쓰지만 제니퍼 이벤트 7행은 받았다. S7 해석은 「변경 이력을 수집하지 못했다」고 쓰지만 제니퍼 소스 변경·변경 영향은 **조회 성공 · 0행**(= 변경 없음)이다.
- **원인**: 소견 LLM 입력에 섹션의 `reason`(폴스타 축 사유 · ITSM 미연동 사유)이 축 구분 없이 한 문자열로 들어간다(`playbook_steps.py:1891-1895` `reason=" · ".join(...)`). 축별 `axes`는 `data`에 있지만 해석 지시가 「조회 성공 0건」과 「조회 못 함」을 구별하라고 하지 않는다(`sre_agent/sre_agent/application/playbook_prompts.py`).
- **수정안**: ① 해석 전에 **축별 결정적 요약**(「제니퍼: 조회 성공 0건 = 24시간 내 변경 없음 · 폴스타: 서버 미확정 · ITSM: 미연동」)을 만들어 결과 줄과 프롬프트에 함께 싣는다 ② 프롬프트에 「조회 성공 0건은 '없음'이라는 근거다 — '수집 못 함'으로 쓰지 마라」를 좁게 추가 ③ ITSM 미연동은 상시 상태이므로 해석 근거에서 빼고 결과 줄 꼬리(「ITSM 미연동」)로만 둔다.

### D5 (B) 완료된 조사의 경과 시간이 계속 늘어남 — 코드 확인

- **증상**: 「진행 S5/5 · 완료 · **2시간 39분**」. 조사는 09:54~09:56대에 시작했고 예산이 600s인데 경과가 2시간을 넘는다.
- **원인**: `src/static/js/diagnosis-view.js:1701` `renderTabHead`가 `tab.done`이어도 `liveElapsed(tab.elapsed, tab.elapsedAt)`(`:1286` — 받은 시각 이후 흐른 시간을 더함)를 쓴다. 바로 위 `renderTabButton`(`:1684`)은 `tab.done`이면 생략한다 — 비대칭.
- **수정안**: 종결이면 서버가 준 `elapsed_s`(최종값) 그대로 표시. 회귀 테스트(종결 탭 머리 경과 고정) 추가.

### D6 (C) 사용자 화면에 내부 용어·필드명·계획서 번호 노출 — 코드 확인

- **증상**: `meta.episode.infra_db_ids` · `(plans/165 F3)` · `fallback` · `parallel` · `missing`이 사용자 화면에 그대로 나온다.
- **원인**:
  - `playbook_runner.py:260-263` `POLESTAR_BRIDGE_MISSING` 문장이 개발자용 사유를 그대로 결과 줄에 싣는다.
  - `playbook_runner.py:1123` `STEP_STATUS_NO_OPINION = "단계 상태 {status} — 소견 없음"`에 원시 상태값(`missing`)이 들어간다.
  - 조사 이유 줄의 「플레이북 fallback 선택」·「parallel」은 실행 기록 `purpose` 앞에 원시 키가 붙는다(`investigation_dispatcher.py:455-461` 문구 자체는 한국어 — 화면 조립부에서 키를 같이 찍는 것으로 추정).
- **수정안**: 사유를 **사용자 문장**(「제니퍼 대상 서버를 폴스타에서 찾지 못해 이 단계를 건너뜀」)과 **운영자 상세**(필드명·계획 번호 — 관리자 화면·「ⓘ주의」 펼침·서버 로그)로 나눈다(D-325 ③ 사용자 뷰 방침과 같은 방향). 상태값은 한국어 라벨 맵(`missing`→「수집 결손」 · `skipped`→「생략」).

### D7 (C) 조사 종결인데 「지금까지 판정 없음」만 표시 — 확인 필요

- **증상**: 완료 상태인데 상단 요약이 「판정 없음」뿐이다.
- **확인한 것**: `diagnosis-view.js:903-918` `sofarLine`은 종결이면 종합 1위 가설을 쓰고, 없으면 「판정 있는 단계 · 행 있는 단계」 고리를 쓴다. 이번 조사는 모든 단계가 결손·0행이라 고리가 비고 종합 가설도 없다 → 정상 동작이나 **왜 판정이 없는지**(폴스타 축 결손 2/4 · 제니퍼 축 0건)를 말하지 않는다.
- **수정안**: 종결 + 가설 없음이면 「판정 근거 부족 — 폴스타 축 결손 2단계 · 제니퍼 축 변경·이벤트 없음」처럼 결손 집계를 한 줄로. HolmesGPT 결과(사진 하단 「HolmesGPT 1회차」)가 있으면 그 요약을 우선 노출할지 결정(§4 Q4).

### D8 (C) 우측 배지 잘림 — 확인 필요(사진 잘림 가능)

- 「✓ 3.3s · 상관 ≠ 인과 — …」·「✓ 2.8…」이 화면 오른쪽 끝에서 잘렸다. 탭 막대도 잘려 있어 사진·창 크기 문제일 수 있다. 같은 해상도(1920 폭 · 배율 100%)에서 재확인 후 줄바꿈/말줄임 처리.

---

## 2. 원인 관계

```
D1 폴스타 소스·서버 미확정 ──┬─> S4·S5a 전체 결손 ─> D7 「판정 없음」
                            ├─> S5·S7 「일부 결손」 ─> D4 AI 해석 과장
D2 APM 알람 → 호스트 fallback ┘  (폴스타 전용 단계가 절반)
D3 조회 7행 / 결과 0건 (독립)
D5 경과 시간 (독립 · 프론트)
D6 내부 용어 노출 (독립 · 문구)
```

D1·D2를 먼저 고치면 D4·D7의 발생 빈도가 크게 준다. D5·D6은 작고 독립이라 바로 고칠 수 있다.

---

## 3. 권장 순서 (Wave 안)

| Wave | 범위 | 패키지 | 크기 |
|---|---|---|---|
| W0 | 실측 — 내부망 해당 조사 브리핑 JSON(S5 `table`·`data`)으로 D3 원인 확정 · 제니퍼 hostname이 파드인지·노드명 출처·인스턴스 IP 유무 확인(§1.1 ① c·d) · 폴스타 서버 이름 표본으로 정규화 규칙 d 검증 · 운영 Redis 영속 설정 · `SQL_TOOMANY_FETCH` 정규화 유형 값 확인(D2) | 내부망 | 실측만 |
| W1 | D5(종결 경과 고정) · D6(사용자 문장/운영자 상세 분리 · 상태 한국어 라벨) | `src/static` · sre_agent | 소 |
| W2a | D1 설정 매핑 운영 값(운영자 결정 · 코드 0) · §1.1 ① 후보 검색(a~d · 정규화 순수 함수 · 점수·사유) + 확정 연결 저장소 + 페이로드 `meta.hints.polestar_link` 계약 · sre_agent 수용(「서버 선택 대기」 상태) | noise_gate · sre_agent · `src/domain` | 중 |
| W2b | §1.1 ② 선택 UI(단계 카드 후보·[이 서버로 다시 조회]·[해당 서버 없음]) · 폴스타 축 단계만 재조회 + 종합 재실행 · 본체 API | `src/api` · `src/static` · sre_agent | 중 |
| W2c | §1.1 ③ 패턴 추출·누적 · 후보 순위 반영(① e) · 관리자 「호스트 연결」 목록·변경·삭제 · 감사 로그 · 매뉴얼(D-255 — 사용자·관리자 둘 다) | noise_gate · `src/static` | 중 |
| W3 | D2 APM SQL 계열 플레이북 매칭 · APM용 fallback | sre_agent `config/playbooks` | 소~중 |
| W4 | D3 수정(W0 결과대로) · D4 축별 결정적 요약 + 프롬프트 좁은 규칙 · D7 결손 집계 줄 | sre_agent · `src/static` | 중 |
| W5 | D1 ③ 컨테이너 대상(노드명 출처가 있을 때만) · D8 | sre_agent · `src/static` | W0 결과에 따름 |

매뉴얼(D-255): W1·W4는 사용자 화면 문구가 바뀌므로 `src/static/manual/user.html` 「장애 진단」 절 갱신 대상.

---

## 4. 사용자 결정 필요

| # | 질문 | 권고 (Q2~Q7 = 권고 적용 · 사용자 확인 전 · 2026-10-27) |
|---|---|---|
| Q1 | D1 즉시 대책으로 폐쇄망 `SRE_PLAYBOOK_POLESTAR_SOURCE_MAP`을 채울지(187 미결 20) | 채운다 — 존이 다른 알람이면 다른 서버를 조회할 위험은 화면 근거 문장으로 이미 고지된다 |
| Q2 | D2 — `SQL_TOOMANY_FETCH`를 `was.latency`에 넣을지, SQL 전용 플레이북을 새로 둘지 | SQL 전용(`was.sql`) — 과다 fetch는 지연이 아니라 SQL 패턴 문제라 판독 규칙이 다르다 |
| Q3 | §1.1 후보 목록을 조사 브리핑에 실을지, 화면이 열 때 본체 API로 받을지 (v0.2 — 검색·저장 위치는 noise_gate로 정함: 선택 UI·저장소·폴스타 접근이 모두 본체 쪽) | API — 최신 등록 서버를 반영하고 브리핑이 커지지 않는다 |
| Q4 | D7 — 플레이북 판정이 없을 때 HolmesGPT 요약을 「지금까지」 자리에 올릴지 | 올리되 「AI 판단 — 검증 전」 표지 유지(G-48) |
| Q5 | §1.1 확정 권한 — 누가 고른 연결이 이후 조사에 자동으로 쓰이나 | 사용자 선택은 **그 조사에만** 즉시 적용 + 「확정 대기」로 저장, 운영자가 관리자 목록에서 승인하면 전역 확정. 운영자가 고르면 바로 전역 |
| Q6 | §1.1 [해당 서버 없음](부정 연결)도 전역으로 저장할지 | 저장 — 파드처럼 반복 질문을 막는다. 단 30일 뒤 다시 후보를 묻도록 만료 |
| Q7 | §1.1 ③ 패턴 「규칙 승격」(운영자 승격 패턴에 맞고 후보가 유력 1건이면 자동 연결)을 둘지 | 이번 계획에서는 두지 않는다 — 패턴은 순위·사유에만. 확정 연결이 충분히 쌓인 뒤 실측으로 다시 판단 |

**결정 상태(2026-10-27)**: 사용자는 Q1~Q7에 답하지 않고 구현을 지시했다. Q2~Q7은 위 권고안으로 구현했다 — **권고 적용 · 사용자 확인 전**. Q1은 폐쇄망 sre_agent `.env` 운영 값이라 코드 작업이 아니므로 손대지 않았다(운영자 결정 대기).

---

## 5. 구현 계약 (v0.3 · 2026-10-27 착수 — 패키지 사이 계약의 단일 출처)

### 5.1 공용 정규화 — `src/domain/host_names.py` (stdlib 순수 · noise_gate 역방향 import 허용 · D-139 부기)
- `normalize_hostname(value) -> str` — 앞뒤 공백 제거·소문자. IP(v4)는 그대로. FQDN이면 첫 레이블(짧은 이름).
- `k8s_workload_name(value) -> str | None` — 파드 해시(`-[0-9a-f]{8,10}` ReplicaSet · 뒤이은 `-[a-z0-9]{5}` 파드) 제거 → 그다음 `-deploy`·`-deployment` 꼬리 제거. 바뀐 게 없으면 None.
- `name_tokens(value, min_len=3) -> list[str]` — 정규화 이름을 `-`·`_`·`.`로 쪼갠 길이 ≥3 토큰(순서 유지·중복 제거 · 해시 모양 토큰 제외).
- `name_similarity(a, b) -> float` — `difflib.SequenceMatcher` 비율(정규화 후).
- 165 F3가 같은 함수를 쓴다(이 계획에서 F3 코드는 고치지 않는다).

### 5.2 페이로드 계약 — `meta.hints` 가산 키 (계약 `contract_version "1"` 불변 · 값이 있을 때만 키)
- `polestar_link` (APM 알람 · 플래그 on일 때만 noise_gate가 싣는다):
  - `{"status": "linked", "db_id", "server_name", "hostname", "basis": "previous_confirmed" | "user_selected", "by", "at"}` — sre_agent는 `infra_db_ids` 다음·설정 매핑 `configured` 앞 순위로 소스를 받고, `polestar_server`를 `server_name`(폴스타 등록명 · D-046)으로 쓴다. 근거 문장: 「이전 확정 연결 — {by}·{at}」/「사용자 선택으로 연결 — {by}·{at}」.
  - `{"status": "absent", "by", "at"}` — 폴스타 축 단계는 **skipped**, 사유 「폴스타 미등록(사용자 확인 · {by}·{at})」.
  - `{"status": "unresolved"}` — 확정·부정 연결이 없다. 폴스타 축 단계는 **skipped**, 사유 「폴스타 서버 선택 대기 — 조사 화면에서 후보를 골라 다시 조회」(수집 결손 missing 아님).
  - 키 없음 = 현행(플래그 off · 비트 동일 — 단 D6 문구 개선은 결함 수정으로 적용).
  - **순위(팀 리드 확정 2026-10-27 — 플래그 on이 현행보다 나빠지지 않게)**: `infra_db_ids` → `linked` → `absent`(생략) → 설정 매핑 `configured`(현행 — 서버명은 제니퍼 hostname 원문) → `unresolved`(생략 · 설정 매핑이 없을 때만) → 키 없음(결손 · 현행).
- `rerun` (본체 「이 서버로 다시 조회」만): `{"parent_investigation_id", "axes": ["polestar"], "prior_sections": [...]}` — `prior_sections`는 부모 브리핑 `briefing.playbook.sections` 원본. `prior_readings` = 부모 `briefing.playbook.readings`(있을 때만 · 재사용 단계 판독 키는 부모 값 — 검증 Major 교정 2026-10-27). sre_agent는 **폴스타 축 단계**(액션 `host.*` + 폴스타 축이 있는 혼합 단계)만 다시 돌리고, 나머지 단계는 같은 `step` id의 prior 섹션을 그대로 재사용(도구 호출 0 · `reused_from` 표지)한 뒤 종합(S9)을 다시 돈다.

### 5.3 섹션 사유 분리 (D6 · D-325 ③)
- 섹션 `reason`은 **사용자 문장**, 개발자 사유(필드명·계획 번호)는 섹션 `reason_detail`(새 가산 키)로 둔다. 본체 `investigation_view`는 운영자 뷰가 아니면 `reason_detail`을 뺀다. 상태 원시값은 화면이 한국어 라벨(`missing`→수집 결손 · `skipped`→생략 · `ok`→완료 · `failed`→실패 · `timeout`→시간 초과)로 보인다.

### 5.4 연결 저장소 (noise_gate · Redis · TTL 없음 — 부정 연결만 30일 만료 · Q6)
- 키 `hostlink:{source_id}:{normalize_hostname(hostname)}` → JSON `{state: "confirmed"|"pending", kind: "link"|"absent", db_id, server_name, polestar_hostname, grade, by, by_role, at, investigation_id, approved_by?, approved_at?, expires_at?(absent만)}`.
- 패턴 해시 `hostlink:patterns` — 필드 = 패턴 키(`kind|from|to`), 값 = 적용 건수. **확정(confirmed) 연결에서만** 뽑는다(LLM 0).
- Q5: 운영자(`_is_alarm_operator`)가 고르면 바로 confirmed, 일반 사용자는 pending(그 조사의 재조회에만 즉시 적용). 자동 사용(`previous_confirmed`)은 confirmed만. Q7: 패턴으로 자동 연결하지 않는다(순위·사유에만).

### 5.5 본체 API (플래그 off면 404)
- `GET /alarm/investigations/{investigation_id}/host-candidates` → `{hostname, source_id, link, candidates[≤5]: {db_id, server_name, hostname, ipaddress, grade("확정"|"일치"|"유력"|"유사"), grade_key("confirmed"|"exact"|"likely"|"similar"), score, reasons[]}, unchecked[]: {db_id, reason}}` — 조사 열람 인가와 같은 존 검사. 캐시 없음.
- `POST /alarm/investigations/{investigation_id}/host-link` body `{action: "select"|"none", db_id?, server_name?, hostname?}` → 선택 서버는 a~c 프로브로 존재를 다시 확인한 뒤 저장 → `select`면 재조회 조사(5.2 `rerun`)를 접수해 `{link, investigation_id, status}`. 부하 상한(D-347)은 수동 조사와 같이 센다. 감사 로그 대상.
- 관리자: `GET /admin/host-links` → `{links[], patterns[{key,kind,from,to,label,count}]}` · `POST /admin/host-links/approve`(body `{source_id, hostname, db_id, server_name, at}` — 현재 값과 다르면 409 `link_changed`) · `DELETE /admin/host-links`(body `{source_id, hostname}`) — 운영자만 · 감사 로그.
- 직접 입력(후보 0건): body `{action:"select", server_name}`(db_id 없음) → 본체가 인가 소스 전부에서 a~c 정확 확인 · 1곳만 일치하면 수용 · 여러 곳 409 `server_ambiguous` · 없음 400 `server_not_found`. 일반 사용자 응답의 `link`에는 `by`·`by_role`·`approved_by`·`investigation_id`·`at`을 싣지 않는다(운영자만). 두 라우트는 사용자별 단기 호출 상한(D-347 취지).
- 플래그: `NOISE_HOST_LINK_ENABLED`(기본 false · `src/config/` noise_gate 그룹).

### 5.6 구현 결과 · 이탈 (2026-10-27)
- **W1**: D5 종결 탭 경과 고정 · D6 사유 `reason`/`reason_detail` 분리(사용자 뷰에서 섹션·`scope` 상세 제거) · 상태 한국어 라벨 · 조사 이유 줄 「플레이북 공통 점검 선택」「— 병렬」.
- **W2a~c**: §5 계약대로. 소스 순위는 §5.2 「순위」 줄. 일반 사용자의 [해당 서버 없음]도 pending(Q5를 Q6보다 우선). 사용자별 60초 호출 상한(후보 10 · 연결 5 · 프로세스 단위). 승인은 비교 후 저장(원자 아님 — 주석).
- **W3**: `was.sql`(priority 15 · SQL 이벤트 5종 `SQL_TOOMANY_FETCH`·`DB_TOOMANY_FETCH`·`DB_BAD_RESPONSE`·`JDBC_BAD_RESPONSE`·`JDBC_STMT_EXCEPTION` — `SQL_TOOMANY_FETCH`는 화면에서만 관측된 이름이라 내부망 errorType 확인 필요) · `was.fallback`(apm.Instance 전용 · priority 900).
- **W4**: D3는 로컬 재현 안 됨 — 「이벤트 n건 중 타임라인 반영 m건 — 사유」 차이 문장만 구현 · D4 축별 결정적 요약 + 프롬프트 1줄 · D7 「판정 근거 부족 — 수집 결손 n · 생략 m · 0건 k단계」(축 구분은 안 함 — 프론트가 단계 액션을 모름) + HolmesGPT 요약 「AI 판단 — 검증 전」.
- **이탈**: 재조회는 HolmesGPT 생략 · 쓰기 요청은 `app.js` 주입(조사 화면 GET 전용 가드 유지).
- **잔여 라운드(2026-10-27 · D-348 부기 1)**:
  - ① 사건 종합 조사(`submit_episode_investigation`)도 대표 알람 기준 `meta.hints.polestar_link`를 싣는다(`redis` 키워드 · `EpisodeHost.redis` 배선 · 플래그 off 키 없음). 사건에 `infra_db_ids`가 있으면 그 값이 연결보다 앞선다(§5.2 순위 그대로).
  - ② `src/domain/symptom_router.py`에 `was.sql`(표면어 「과다 페치」·「SQL 페치」·「SQL 패턴」 · 가상 이벤트 `apm.Instance`/`SQL_TOOMANY_FETCH`) 편입 — 채팅 증상 진입. sre_agent w12 `PENDING_IN_ROUTER` 제거 · 본체 사본 동등성 테스트가 `was.fallback`을 증상 대상에서 뺀다(앞 라운드에서 두 단언이 실패 상태였다).
  - ④ D7: 섹션 가산 키 `axis_status`(`{축: 상태}` — 단일 축은 액션 표 `ACTION_AXIS`, 혼합 S5·S7은 `data.axes` · 미연동 축 제외 · 재사용 섹션은 부모 값) → 화면 「판정 근거 부족 — 폴스타: 생략 2단계 / 제니퍼: 0건 1단계」(순서 폴스타·제니퍼·ITSM·기타 · 축 없는 건너뜀은 세지 않음 · 축 상태 없는 옛 브리핑은 종전 문장).
  - ⑤ 저장소 `save`·`approve`·`delete` = 연결 키 WATCH/MULTI(Lua·`SET IFEQ` 미사용 — 운영 Redis 버전 무관 · 로직 Python 한 곳) · 호출 상한 = Redis 공유 고정 창 `hostlinkrate:*`(오류 시 프로세스 단위 폴백 · 경계 최대 2배). 검증 교정: 관리자 목록·승인·삭제도 저장소 실패를 503 `link_store_failed`(쓰기는 실패 감사)로 · Redis 소켓 타임아웃 2초. 검증 테스트 `noise_gate/tests/test_plan196_verify_redis_wire.py`(실 redis-py ↔ RESP 최소 서버 WATCH 실측)·`tests/test_api/test_plan196_verify_admin_conflict.py` · 196 테스트 92 통과.
  - ③ **결정(이탈 → 결정)**: 재조회의 혼합 단계 S5·S7은 제니퍼를 다시 부른다(유지). 부모 섹션은 브리핑용(`include_data=False`)이라 원자료가 없고, 원자료(이벤트 메시지·변경 목록)를 남기면 브리핑이 커지고 사용자 뷰 마스킹 대상(D-325 ③)이 늘어난다. 비용은 S5 이벤트 1회 + S7 변경 축 소수 · 사용자별 상한과 D-347 부하 상한을 탄다 · 같은 사건창 재조회라 늦게 도착한 이벤트도 반영된다.
  - ⑥ 매뉴얼: 원천 갱신(U-70 축별 「판정 근거 부족」 · U-69 증상 낱말 「SQL 과다 페치」) · build · `tests/test_manual` 695 통과. 캡처 장면 2개(`diagnosis-host-pick` · `admin-host-links` — 응답 가로채기 · 플래그 불필요)와 예시 조사 `inv-manual-hostwait`는 정의만 했고 **촬영 못 함** — 로컬 `polestar_pg`·`collectorinfra-redis` 컨테이너가 정지(캡처 프로필이 인증 PG 5434·Redis 6380을 요구). 컨테이너 기동 뒤 `run_capture --only diagnosis-host-pick admin-host-links` → features.yaml·content 캡처 참조 넣기 → build → test_manual.
- **잔여(코드)**: 원래 실패 3건(`test_plan147_w4_*`)은 이 계획과 무관 · DPM 미연동 단계 사유가 `not_connected` 원문으로 카드 제목에 보일 수 있음(확인 필요 — 미실측).

### 5.7 W0 내부망 실측 체크리스트 (로컬 불가)
1. **D3** — 해당 조사 브리핑 JSON(S5 `table`·`data`)으로 「조회 7행 / 결과 0건」 원인 확정(로컬은 차이 문장만 구현).
2. **D2** — `SQL_TOOMANY_FETCH`의 실제 제니퍼 errorType 값(화면에서만 관측 · `was.sql` 매칭·가상 이벤트가 이 이름을 가정).
3. **W5** — 제니퍼 hostname이 파드인지 · 파드→노드명 출처 · 인스턴스 IP 유무(§1.1 ① c·d) · 폴스타 서버 이름 표본으로 정규화 규칙 d 검증. **→ 2026-10-29 실측으로 파드 확인 · 노드 출처 = 웹 세션 `hostAddress` — `plans/202`로 이관.**
4. **D8** — 우측 배지 잘림: 1920 폭 · 배율 100%에서 재확인.
5. **운영 Redis 영속 설정**(AOF/RDB) — 연결 저장소는 TTL 없는 사실 기록이다.
6. Q1 — 폐쇄망 `SRE_PLAYBOOK_POLESTAR_SOURCE_MAP` 운영 값(운영자 결정).
