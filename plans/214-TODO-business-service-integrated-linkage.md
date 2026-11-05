# 214. 업무 서비스 통합 연계 — 업무 서비스별 APM·SMS 연계를 자동 분석·저장·관리하고, 알람·실시간 지표로 모니터링하며, 서비스 단위로 복합 진단한다

> **작성일**: 2026-11-05
> **상태**: **TODO** — 계획(코드 0건) · 열린 질문 Q1~Q37(§15)은 권고안을 「이의 없으면 확정」으로 제시 · D-367 예약
> **분석 기준 HEAD**: `a86aa4e5` (main · plans/210 W1~W10 · plans/211 W1~W8 · plans/212 포함)
> **계획 시작 SHA**: (착수 시 기입)
> **회귀 대상 파일**: (착수 시 Wave별 기입 — §12)
> **개정 이력**
> - 초안(2026-11-05): 사용자 지시 *"Apm과 sms을 연결하는 기능과 유사하게 엄무명이나 호스트네임의 유사성을 이용하여 같은 업무의 서버를 그룹핑을 자동화 하는 페이지를 만들 계획파일을 작성하라"* → SMS 서버 업무 그룹(신호 S1~S5)
> - 개정 1(2026-11-05): *"호스트면 앞 3글자는 업무코드로 만들기도 한다. 이 정책도 반영하라"* → 신호 S6 호스트 접두 업무코드(§3.2.1)
> - **개정 2(2026-11-05)**: *"통합 관점에서 apm, sms 및 업무서비스별 통합관점에서 연계성을 분석하여 저장하고 관리, 모니터링할 수 있어야 한다"* + 확인 답변 2건 — ① 모니터링 범위 = **연계 상태 + 알람 현황 + 실시간 지표까지** ② **214을 통합 계획으로 확장**(새 계획 분리 안 함). → 중심 엔티티를 「서버 그룹」에서 **「업무 서비스」**로 바꿈(구성원 = SMS 서버 + APM 도메인·인스턴스) · 연계 분석(§3.4) · 모니터링 3층(§4) · 모니터링 보드(§5.2) · 파일명 `213-TODO-business-server-grouping.md` → 현 이름. 코드 0건이라 표·칼럼 이름은 새로 정했다(옛 `server_group*` → `biz_service*`).
> - **개정 3(2026-11-05)**: *"전체적인 코드와 데이터를 확인하여 업무 서비스별 서버, apm의 모니터링 및 장애 진단이 복합적으로 될 수 있도록 계획이 되어 있는지 확인하여 업데이트하라."* → 전수 실측(§2) 결과 ① **장애 진단이 빠져 있었다** — 보드의 「장애 진단 열기」 링크뿐이고 Q1이 조사 범위를 후속으로 미뤘으며, 현행 진단 경로는 전부 **호스트 1대·알람 1건·같은 호스트 사건** 단위다(`sre_diagnose` 대상 스칼라 1개 · 채팅 `fault_diagnosis` 첫 대상 1개 · 사건 키 `zone|host_key`) ② **기존 업무 자산 5갈래를 놓쳤다** — 게이트웨이 수동 `business_map` · `apm_instance_map(business=)` 해석 · 제니퍼 업무 도구(`apm_business`·`apm_metrics(scope=business)`) · 본체 간선 E6/E7 · 채팅 `targets.kind=business`(§2 「확인한 공백」 정정) ③ **APM 판정 서술이 틀렸다** — `apm_app_health` 판정은 위험 2규칙(큐잉·오류 급증)뿐이고 응답시간 임계·「주의」 단계가 없으며, D-335 ②는 「정상」 어휘를 금한다. → **복합 진단 3층 D1~D3**(§4.6~4.9) · 업무 정본 관계(§3.7) · 구성원 종류 `apm_business`(제니퍼 업무 · 수동 전용) · §4.4 판정 정정 · Wave W9~W11 추가(매뉴얼 W9 → W12) · Q1 개정 · Q17~Q22 신설.
> - **개정 4(2026-11-05)**: *"업무별 서버 묶음을 조회하고 정의하는 웹페이지 1개와 업무별로 서버들의 상황이나 이벤트를 보는 페이지가 각각 있어야 할 것으로 보인다. 계획 문서와 AIOps 측면에서 필요한 UI가 뭔지 조사하여 계획에 반영하라."* → 기존 화면 구조 실측(§5.0 — 포털 뷰 탭 4종 · 관리자 탭 15종 · 노이즈 관제 포털 탭 이식 `plans/193` · 화면 결정 D-245·D-248) + AIOps 서비스 중심 화면의 필수 요소(서비스 카탈로그 · 구성원 작업대 · 서비스 상태 개요 · 구성 맵 · 통합 이벤트 타임라인 · 변경 상관 · 진단 진입)를 정리해 **§5를 화면 2개(P1 정의 · P2 현황)로 다시 썼다**. 옛 관리자 탭 안(§5.1)은 P1이 대신한다. 데이터 칸 추가(서비스 `tier` 중요도 · `description` · `owner_org` 담당 부서 · 구성원 `layer` 계층) · API·플래그(변경 이력 `NOISE_BIZ_MONITOR_CHANGES_ENABLED`) · Wave W5·W5b·W8·W8b · 수용 기준 19~26 · Q23~Q28.
> - **개정 5(2026-11-05)**: *"업무끼리 묶을때 참고할만한 것들을 좀더 조사해 봐라. 예를 들어 유사한 ip대역이라던가 생성일시나 등록일시라던가... 조사하여 자동 묶기를 기능에 대해 보완하라."* → 폴스타 `cmm_resource` 전 칼럼·ITAM 서버 통합 현황 전사본·알람 페이로드·로컬 샌드박스(읽기 전용 SELECT)를 실측했다(§3.2.4). 그 결과 ① **자원 트리 그룹 S7**(중) — 운영 트리 폴더 이름이 업무 시스템명(코드 포함)인 실례가 있다 ② **보조 신호 S8~S12**(등록 시각 묶음 · IP 대역 · ITAM 계약 · 알람 동시 발생 · 가용성 의존) — **전파형 후보 전용**(스스로 서비스를 만들지 않음) + **신호 순도 보정**(확정 구성원과 대조해 순도가 낮은 신호는 존별로 자동으로 끈다 — 샌드박스의 /24 대역은 업무가 아니라 계층으로 갈렸다) ③ **S6 보완** — `cob0-kahapp01`처럼 존 머리가 붙으면 앞 3글자가 업무코드가 아니다(`strip_prefix_patterns`) ④ 쓰지 않는 단서와 이유를 적었다. 보강 읽기는 미리보기 때 214 소유 고정 SELECT(210 스캐너 불변 · Q29). §3.2.2~3.2.4 신설 · G14 · F16~F21 · W0-19~W0-24 · 수용 기준 27~32 · Q29~Q33.
> - **개정 6(2026-11-05)**: *"각 서비스의 상세 모니터링 정보를 보여주는 화면을 추가하는 것에 대해 검토해봐."* → 검토(독립 페이지 A · P2 상세 보기 탭 B · 추이 칸 확대 C) 뒤 사용자 지시 *"B안으로 계획에 반영하라"*. P2 서비스 상세를 **보기 탭 4개(개요 · 지표 · 이벤트 · 진단)**로 나누고 **「지표」 보기**(§5.2.1)를 더했다 — 구성원별 지표 추이(계층 레인별 그래프 · 기간 1h/6h/24h/7d) · 이벤트 표지 겹치기 · 구성원 드릴다운 서랍 · 어제 같은 시각 비교. 지표는 M3 저장을 늘리지 않고 **탭을 열 때 원천에서 1회 읽는다**(SMS 시간·일 통계 고정 SELECT · 제니퍼 `apm_metrics(mode=series)` · 60초 메모 · 구성원 ≤20 · 동시 상한) — PG 저장 0(Q11 유지). 플래그 `NOISE_BIZ_MONITOR_HISTORY_ENABLED`(기본 off — 이름 `…_METRICS_ENABLED`는 M3이 이미 쓴다) · G15 · F22~F24 · W0-25 · W8c · 수용 기준 33~37 · Q34~Q37. 독립 페이지·히트맵·APM 7일은 범위 밖(§16).
> - **번호 재부여(2026-11-05)**: 원 `plans/213` → **214** · 원 D-366 예약 → **D-367** — 원격 `main`이 `plans/213`(심층 조사 회복력)·D-366을 먼저 푸시했다(병합 시 재부여 · 내용 불변).
> **근거**: 저장소 코드 실측(2026-11-05 · §2 · §5.0) · 데이터 실측(2026-11-05 · §3.2.4 — 로컬 샌드박스 `polestar_pg` 읽기 전용 SELECT · 실 DB 익스포트 행 포함)
> **관련 계획·결정**: `plans/210`·D-362(APM·SMS 연계 DB — APM 축의 원천 · 사전 스캔 스냅샷) · `plans/211`·D-363(호스트 정규형 · 업무명 꼬리 · LIKE는 후보 전용) · D-348(유사 후보는 사람이 확정) · D-290(업무명 근거) · D-224 ④(2건 이상이면 고르지 않음) · D-347·D-356 ⑤(MCP·제니퍼 동시 상한 · 호출자 공정 분배) · D-049(워커 PG 쓰기 금지) · D-139(패키지 경계) · D-003(읽기 전용) · D-089(DB별 SQL 격리) · D-255(매뉴얼 동반) · D-303(회귀) · D-354(「담당 업무」 `duty_areas`는 직무 영역 — 이 계획의 「업무」와 다르다) · D-290·D-310(업무명 해석 · 업무 도구 — 제니퍼 「업무」는 트랜잭션 규칙 묶음이라 이 계획의 「업무 서비스」와 다르다) · D-274 ⑤(정합은 게이트웨이 한 곳) · **복합 진단(개정 3)**: D-118(sre_agent 경계 — MCP 계약만) · D-317 ④(사건 = 존·같은 호스트) · D-325 ⑨(크로스소스 종합 조사) · D-335 ②④⑤⑥(판정 어휘 · 원인 조사 위임 · 조사 권한 · 비용 상한) · D-341 ①(플레이북 결정적 대상 확정) · D-347 ①②(수동 전용 · 같은 호스트 키 · 조사 서비스 상한) · D-350 ④(이웃은 확정 연결만) · D-352 ②(원인 영역·확실성 배지) · D-359(조사 동시 5건) · **D-367**(이 계획 — 예약)

---

## 0. 요약

- **중심 엔티티 = 업무 서비스**(인터넷뱅킹 등). 구성원은 두 종류다 — **SMS 서버**(폴스타)와 **APM 구성요소**(제니퍼 도메인·인스턴스).
- **연계 = 세 축의 합성**: ① 업무 서비스 ↔ SMS 서버(업무명·호스트 이름·자원 트리 규칙 S1~S7 + 보조 후보 S8~S12 · §3.2) ② APM 인스턴스 ↔ SMS 서버(`plans/210` 연계 매핑 그대로 · §3.3) ③ 둘을 합쳐 업무 서비스 ↔ APM(도출 · §3.3). 계산은 결정적 순수 함수(LLM 0) · 기본 모집단은 210 사전 스캔 스냅샷 재사용.
- **묶기 단서**(개정 5 · §3.2): 강 S1(등록명 꼬리) · 중 S2 APM 도메인 · S3 ITAM · S6 호스트 접두 코드 · **S7 폴스타 자원 트리 그룹** · 약 S4·S5 · **보조 S8~S12**(등록 시각 묶음 · IP 대역 · ITAM 계약 · 알람 동시 발생 · 가용성 의존 — 이미 배정된 이웃의 서비스로 끌어오는 **후보 전용**). 단서마다 확정 구성원과 대조한 **순도**를 계산해 낮으면 그 존에서 끈다(대역이 계층으로 나뉜 망 등). 트리·등록 시각·의존은 미리보기 때 존별 고정 SELECT로 보강해 읽는다(§3.2.4 · Q29).
- **연계 분석**: 서비스마다 「APM 있는 서버 / APM 없는 서버 / SMS에 못 이은 APM 인스턴스 / 추정 비율 / 끊김·충돌」을 계산해 실행마다 저장하고 추이를 본다(§3.4).
- **화면 2개**(개정 4 · §5): **P1 「업무 서비스 정의」**(독립 페이지 `/biz-services` · 새 탭) — 서비스 카탈로그 조회 · 구성원 정의(서버·인스턴스 찾아 추가 · 계층 지정 · 확인/제외/옮기기) · 검토함(후보·충돌·미소속 서버 일괄 지정) · 자동 계산 미리보기 → 적용 · CSV 가져오기/내보내기 · 변경 이력 · 연계 분석. 조회는 로그인 사용자, 편집은 관리자. **P2 「업무 현황」**(포털 뷰 탭 `/?view=biz`) — 서비스 상태 타일 · 서비스 상세(보기 탭 4개 — 개요(구성 맵 · 구성원 상태 · 추이) · **지표**(개정 6 · 구성원별 추이 · 열 때 원천 1회) · **통합 이벤트 타임라인** · 진단 패널). 알람·조사는 같은 창의 노이즈 관제·장애 진단 탭으로 넘긴다(새 탭 0). 옛 「관리자 탭」(§5.1 초안)은 P1으로 대체한다.
- **모니터링 3층**(§4): M1 연계 상태 · M2 알람 현황(노이즈 게이트가 이미 가진 사건·판정 집계 — 조회 추가 0) · M3 실시간 지표(제니퍼 골든 시그널 1분 주기 · 폴스타 시간 통계 10분 주기 확인). M3은 **감시 대상으로 지정한 서비스만 · 보드를 보는 사람이 있을 때만** 돌아 관측 시스템 부하를 묶는다.
- **복합 진단 3층**(개정 3 · §4.6~4.9): **D1 서비스 진단 스냅샷**(LLM 0 — 구성원 전체의 알람·APM 신호·SMS 지표를 210 연계로 겹쳐 「이상 구성원 · 최초 이상 순 · 교차 계층 판독(APM 단독 / 이어진 서버 OS 동반 / 여러 서버 동시)」을 결정적으로 보인다) · **D2 알람 조사에 서비스 맥락 부착**(구성원 알람의 조사 페이로드에 `meta.biz_service` 가산 → sre_agent 업무 동료 비교·SMS 다중 지표가 서비스 구성원으로 돈다) · **D3 서비스 심층 조사**(보드에서 수동 · D1이 고른 대표 1대 + 이상 구성원 상한 안 · `sre_diagnose` 선택 인자 가산 · 서비스 단위 중복 차단). 노이즈 게이트 억제·상관 판정은 바꾸지 않는다.
- **업무 정본**(§3.7): 「업무 서비스」 구성원의 정본은 `biz_service`(PG) 하나다. 게이트웨이 `business_map`·제니퍼 업무·E6 LIKE는 각자 채팅 해석용으로 남고, 이 계획은 그것들과 **대조해 불일치를 보일 뿐** 덮어쓰지 않는다.
- **플래그 7개 전부 기본 off**(§7 · 개정 6 `NOISE_BIZ_MONITOR_HISTORY_ENABLED` 포함) = 현행 비트 동일.

## 1. 발단과 목적

### 1.1 발단
개정 이력 3건. `plans/210`은 「제니퍼 인스턴스 ↔ 폴스타 서버」를 잇지만, 그 위의 「어떤 업무의 서버·WAS인가」는 어디에도 없다. 장애 때 운영자는 「인터넷뱅킹이 지금 괜찮은가」를 묻는데, 지금은 서버·인스턴스 단위로만 볼 수 있다.

### 1.2 목표
| # | 목표 | 확인 |
|---|---|---|
| G1 | 스냅샷만으로 업무 서비스·구성원 제안을 결정적으로 계산한다 | 순수 함수 테스트 벡터 · 입력 순서 무관 |
| G2 | 강 신호만 자동 확정 · 중 신호는 「자동 · 추정」 · 약 신호는 후보 | 등급별 단언 |
| G3 | 관리자가 고친 것(수동·확인·제외·거절)을 다음 계산이 덮지 않는다 | 쓰기 규칙 테스트 |
| G4 | 서비스별 연계 분석 지표가 실행마다 저장되고 추이가 보인다 | 실행 기록·화면 |
| G5 | 서비스별 알람 현황이 추가 관측 조회 없이 보인다 | 가짜 사건·판정 종단 |
| G6 | 감시 대상 서비스의 실시간 지표가 주기 안에 갱신되고, 조회 실패는 사유로 보인다(침묵 금지) | 가짜 게이트웨이·가짜 실행기 |
| G7 | M3 부하가 상한(서비스 수·호출 수·동시성·시간 예산) 안에 묶인다 | 상한 단언 · 호출 횟수 계측 |
| G8 | 플래그 off = 현행 비트 동일 | 기동·라우트·탭·태스크 부재 단언 · 조사 페이로드 바이트 동일 |
| G9 | 서비스 하나의 SMS·APM 구성원 상태를 **한 화면에서 교차 판독**한다(어느 구성원이 먼저 · 어느 계층이 · APM 이상이 그 서버 OS 이상을 동반하는지) — LLM 0 | D1 순수 함수 벡터 · 「가설 · 참고용」 표지 |
| G10 | 구성원 서버·인스턴스의 알람 조사가 **같은 서비스의 다른 구성원**을 비교 대상으로 쓴다 | D2 페이로드 단언 · sre_agent S0 대상 단언 |
| G11 | 서비스 단위 심층 조사를 사람이 시작할 수 있고, 조사 부하 상한(D-347)·권한(D-335 ⑤)·중복 차단 안에 묶인다 | D3 접수 거부 사유 · 서비스 키 in-flight · 상한 단언 |
| G12 | (개정 4) 업무별 서버 묶음을 **한 페이지에서 조회·정의**한다 — 자동 제안이 약해도(F1~F6 미확인) 사람이 찾아 넣고 일괄 지정·가져오기로 채울 수 있다 | P1 브라우저 1회 · 편집 동작 감사 · 일반 사용자 편집 403 |
| G13 | (개정 4) 업무 하나의 **서버·APM 상황과 이벤트를 한 화면 시간축**으로 본다 — 알람·제니퍼 이벤트·노이즈 판정·상태 전이·조사·변경 | P2 타임라인 소스별 단언 · 행 → 기존 화면 같은 창 전환 |
| G14 | (개정 5) 이름 규칙이 약한 서버도 **운영 트리·등록 시각·대역·계약·동시 알람·의존** 단서로 후보에 오른다 — 단서가 업무가 아니라 계층·센터·일괄 이관을 따라 갈리면 **순도 보정이 스스로 끈다**(오묶음보다 미제안) | S7·전파 벡터 · 순도 미달 → 신호 off + 사유 · 보조 신호 자동 구성원 0 |
| G15 | (개정 6) 서비스 하나의 **구성원별 지표 추이**(어느 서버·인스턴스가 언제부터 나빠졌나)를 이벤트와 같은 시간축에서 본다 — 독립 화면 없이 P2 상세 안에서 · 상시 수집·저장 없이 열 때 1회 조회로 | 조회 상한 단언(구성원·행·동시) · 탭을 떠나면 요청 0 · 플래그 off면 관측 호출 0 |

## 2. 현행 실측 — 재사용할 것 (2026-11-05 · `a86aa4e5`)

| 자산 | 위치 | 쓰임 |
|---|---|---|
| SMS 스냅샷 `sms_inventory`(`db_id`·`resource_id`·`server_name`·`hostname`·`ip`·`nic_ips`) | `ddl/apm_host_map.sql:73` · `noise_gate/infrastructure/apm_host_map_sql_scanners.py:128` | SMS 구성원 모집단 · 비고(`description`)는 읽지 않는다(Q6) |
| APM 스냅샷 `apm_inventory`(`source_id`·`domain_id`·`domain_name`·`domain_description`·`instance_id`·`instance_name`·`workload_key`) | `ddl/apm_host_map.sql:56` | APM 구성원 모집단 |
| ITAM 스냅샷 `itam_snapshot_app`·`itam_snapshot_server` | `ddl/apm_host_map.sql:85·93` | ITAM 업무 신호(추정 · D-362 ⑥) · 업무코드 대조 |
| APM·SMS 연계 `apm_host_map`(`scope` instance/workload/domain · `state=active` · `targets[]` · origin·verified) | `noise_gate/infrastructure/apm_host_map_repository.py` | 축 ②의 정본 — 그대로 읽는다(쓰지 않는다) |
| `split_business_suffix` · `canonical_host_key` · `name_tokens` · `k8s_workload_name` | `src/domain/host_names.py` | S1·S3·S6 |
| `host_group`(끝 숫자·구분자 제거) | `noise_gate/domain/incident_fingerprint.py:75` | S4 stem 규칙 선례 |
| 사전 스캔 실행기·락 | `noise_gate/application/apm_host_map_scan.py` · `apm_host_map_scan_lock.py` | 계산은 현재 스냅샷 포인터를 읽는다 |
| 열린 사건 `IncidentRepository.list_open(limit)` · 결정 이력 `decision_store` | `noise_gate/infrastructure/incident_repository.py:187` · `decision_store.py` | M2 알람 현황(조회 추가 0) |
| 알람 존 권한 `alarm_zones_for_user(user, auth_enabled)` | `src/api/routes/alarm.py:587` | 모니터링 보드 존 필터 |
| 제니퍼 `apm_app_health(targets=…)` — 응답시간·TPS·액티브 서비스·에러율 + 신호(`was_signals`) | MCP `apm_gateway/apm_gateway/interface/server.py:504` · 코어 `application/tools.py:243` | M3 APM 지표. **정정(개정 3)**: ① `targets` 배치는 **작업 1개 안에서 항목마다 코어를 1회씩** 부른다(`application/batch.py` — 호출 수는 줄지 않고 왕복만 준다) ② 판정은 `domain/signals.py:232` `judge_app`의 **위험(CRITICAL) 2규칙뿐** — `reject_rate>0` → `was_service_queuing` · 창이 있고 `calls≥20 ∧ error_rate≥0.2` → `was_error_burst`(임계 `apm_gateway/config/was_signatures.yaml`). **응답시간 임계·「주의」 단계는 없다**(§4.4) |
| 제니퍼 범위 도구 `apm_fleet(mode=ranking)` — (소스, 도메인)마다 실시간 1호출로 전 인스턴스 | `apm_gateway/apm_gateway/interface/fleet_server.py:36` · `application/fleet_tools.py` | M3 대안 후보(인스턴스가 많은 도메인은 도메인당 1호출이 싸다 — 신호 동반 여부는 W0-10에서 확인) |
| **제니퍼 「업무」 도구** `apm_business(mode=current, business=str·list)`(도메인마다 1호출) · `apm_metrics(series, scope=business)` | `apm_gateway/apm_gateway/interface/scope_server.py:62` · `server.py:935` · 허용목록 `adapters/jennifer/allowlist.py:101·110·112` | 구성원 종류 `apm_business`(수동 전용 · §3.1)의 M3 지표. 제니퍼 업무는 **트랜잭션 규칙 묶음**이라 인스턴스 목록이 없다(`plans/130` §3) — 업무 서비스와 같은 것이 아니다 |
| **게이트웨이 업무 수동 매핑** `business_map`(업무명·별칭 → 인스턴스 이름 · 선택 `source_id`) | 정합 파일 `apm_gateway/config/instance_map.yaml:25`(현재 `[]`) · 파서 `application/business_map.py:56` | 채팅 업무명 해석의 B0(D-290 ②). 본체가 직접 읽을 수 없고(D-274 ⑤ · 양방향 import 0) 목록을 내주는 도구도 없다 — §3.7 |
| **업무명 → 인스턴스 해석** `apm_instance_map(business=…)` — B0 수동 → 없으면 B1 도메인명 ∪ B2 제니퍼 업무 역추적 ∪ B3 인스턴스 이름·설명 · 근거 `match_kind` | `apm_gateway/apm_gateway/application/instance_tools.py:336` | §3.7 대조 · sre_agent 업무 동료 비교가 지금 쓰는 경로 |
| **본체 간선 E6/E7** `business_name → hostname`(폴스타 등록명·비고 **LIKE 부분 일치** · 상한 1000) · `business_name → apm_instance` | `config/db_registry.yaml:619-622` · `src/db_adapters/polestar/entity_probe.py:75` · `src/orchestration/entity_link.py:213` | 채팅 「인터넷뱅킹 서버들 …」 해석 — 214 S1(정확 일치)과 결과가 다를 수 있다(§3.7) |
| 조사 페이로드 `{contract_version:"1", event, decision, meta}` — `meta`는 값이 있을 때만 붙는 가산형 · APM 힌트·`polestar_link`(210 연계 · `targets` ≤10) | `noise_gate/domain/investigation_payload.py:61·106-135` · `noise_gate/domain/apm_host_map.py:800` | D2 `meta.biz_service` 가산 지점(계약 "1" 유지) |
| 알람 조사 노드(대상 `targets[0]` 1대 · 호스트 인가 1대) · 사건 종합 조사 · 수동 조사 페이로드 | `noise_gate/application/nodes/investigation_trigger.py:60·517·632` | D2 부착 위치 |
| 사건 키 `zone|host_key` — 서로 다른 호스트를 묶지 않는다(D-317 ④) · E2 상관·E4 토폴로지는 **억제·대표 선출용** | `noise_gate/domain/cross_source.py:189` · `correlation.py:105` · `topology.py:31` | 서비스 단위 묶음은 D1이 **표시용으로만** 만든다(억제 판정 불변) |
| sre_agent `sre_diagnose(question, server_name, hostname, db_id, target_state, reference_time, lookback_minutes, prior_findings)` — **대상 스칼라 1개** | `sre_agent/sre_agent/interface/mcp_service.py:605` · `application/investigation_jobs.py` `submit_diagnosis` | D3 선택 인자 가산 지점 |
| sre_agent 업무 동료 비교 — S0 `resolve_was_targets`가 `_business_term`(`hints.business_name`→인스턴스 행 업무명→설명 첫 낱말)으로 `apm_instance_map(business=)`을 불러 **같은 업무 인스턴스**를 비교(상한 `max_compare` 8 · `max_instances` 3) · **`hints.business_name` 생산자는 noise_gate·src에 0건** | `sre_agent/sre_agent/application/playbook_steps.py:1112·1194` · `config/playbooks/was.latency.yaml:13-14` | D2가 서비스 구성원을 **명시 목록**으로 넘긴다(업무명 문자열만 넘기면 게이트웨이 해석이 214 구성원과 다를 수 있다 — §4.8) |
| sre_agent SMS 다중 대상 — `polestar_link_targets`(상한 `POLESTAR_LINK_TARGETS_MAX`=10)가 있으면 폴스타 지표를 대상별 수집 | `sre_agent/sre_agent/application/playbook_runner.py:341·466` | D2·D3 SMS 구성원 수집의 선례(키는 분리 — 210 연계 의미를 바꾸지 않는다) |
| sre_agent 같은 호스트 in-flight 가드 `_host_key` = `(db_id, host[, "episode"])` · 대상을 식별 못 하면 `None` = 가드 통과 | `sre_agent/sre_agent/application/investigation_dispatcher.py:371` | D3 서비스 키 축 `(…, "biz")` + 서비스 키 가드 |
| 본체 진단 화면 — 조사 **id 단위 뷰어** · URL `?diagnosis=` / `?investigation=` · 새 조사는 알람 1건(`POST /api/v1/alarm/{alarm_id}/investigations` — 존 검사·사용자 상한·감사) | `src/static/js/diagnosis-view.js` · `src/static/js/app.js:5706` · `src/api/routes/alarm.py:2787` | D3 접수 라우트 골격 · 결과는 기존 뷰어로 연다 |
| 채팅 진단 `fault_diagnosis._extract_targets` — **첫 대상 1개만**(「N-대상 진단은 범위 밖」 독스트링) | `src/nodes/fault_diagnosis.py:372` | 이 계획은 바꾸지 않는다(Q21) |
| 제니퍼 동시 상한 `JENNIFER_MAX_INFLIGHT_PER_SOURCE`(8) · 호출자 공정 분배 `…_PER_CALLER`(D-356 ⑤) | `apm_gateway/apm_gateway/config.py:102` | M3 호출자 `owner=biz_monitor` 분리 |
| 폴스타 성능 통계 — **가장 촘촘한 것이 시간 통계 `cmm_metric_stat_h`**(stat_date `YYYYMMDDHH`) | `config/db_profiles/polestar_cm_gp.yaml:318` | M3 SMS 지표 = 최신 정각 통계(실시간 아님 — 화면에 명시) |
| `polestar_metric_trend(source, server_name, kind, granularity)` — 서버당 1호출 | `mcp_server/mcp_server/polestar_tools.py:940` | **쓰지 않는다** — 서버 N대면 N호출. 대신 존별 고정 SELECT 1회(§4.3) |
| MCP DB2 동시 상한 4(D-347) · `max_rows` 10000 · `query_timeout` 30 | `mcp_server/mcp_server/config.py:198·216·217` | M3 SMS 조회 동시 1 · 페이지 ≤1000 |
| 단일 인스턴스 잠금(`SET NX EX` + Lua 연장 · 자기 값만 해제) | `noise_gate/infrastructure/worker_lock.py` | M3 수집기 리더 1개 |
| (개정 5) 묶기 보조 단서 원천 — 폴스타 `cmm_resource` `id_ancestry`(자원 트리 조상 id 경로)·`ctime`(등록 ms)·`avail_depend_resource_id(_2)`(가용성 의존) · 알람 페이로드 `resource_ancestry`(트리 이름 경로) · ITAM 서버 통합 현황 계약 칸 · 결정 이력 `recent_decisions(window_seconds)` | `testdata/pg/init/01_create_tables.sql`(칼럼 정의) · `noise_gate/domain/alarm.py:94` · `testdata/itam/schema.yaml` · `noise_gate/infrastructure/decision_store.py:1322` | S7~S12(§3.2.2~3.2.4) — 210 스냅샷에는 없다(210 서버 페이지 SQL은 `id·name·hostname·ipaddress`만 · `apm_host_map_sql_scanners.py:128`) |
| 관리자 탭 「APM·SMS 연계」 골격(목록·편집·확인/취소·감사·`textContent`) | `src/api/routes/admin_apm_host_map.py` · `src/static/js/admin-apm-host-map.js` | 관리 API·화면 골격 |

**확인한 공백(개정 3 정정)**: 「업무명 → 인스턴스/서버」 **해석** 자산은 이미 있다(위 표의 `business_map`·`apm_instance_map(business=)`·제니퍼 업무 도구·E6/E7 — 모두 채팅 질의 시점의 즉석 해석). 없는 것은 ① 업무 서비스를 **저장·관리하는 정본**(SMS 서버 + APM 구성요소의 확정 구성원) ② 서비스 단위 **모니터링** ③ 서비스 단위 **진단 입력**(현행 진단은 전부 호스트 1대·알람 1건·같은 호스트 사건 단위)이다. `service_group`·`business_group`·`server_group` grep 0건은 그대로다(`_ALARM_SERVER_GROUP_RE`는 SQL `GROUP BY` 판정, `host_group`은 조사 지문 토큰).

## 3. 연계 모델

### 3.1 엔티티

```
업무 서비스 (biz_service)
 ├─ SMS 서버 구성원      ← 축 ① 업무명·호스트 이름 규칙(§3.2)
 └─ APM 구성원(도메인·인스턴스) ← 축 ③ = 축 ②(210 연계)로 SMS 구성원에서 도출(§3.3)
```
- 구성원 종류 `kind`: `sms_server`(키 `db_id`+`resource_id`) · `apm_instance`(`source_id`+`domain_id`+정규화 인스턴스명 또는 워크로드 키) · `apm_domain`(`source_id`+`domain_id`) · **`apm_business`**(개정 3 · `source_id`+`domain_id`+제니퍼 업무 id — **관리자 수동 추가 전용**. 자동 계산은 만들지 않는다. 제니퍼 업무는 규칙 묶음이라 인스턴스로 도출할 수 없어서다. 쓰임은 M3 업무 단위 지표뿐이다).
- **한 SMS 서버는 한 서비스**(Q4). APM 구성원은 이어진 SMS 서버를 따라간다(§3.3).

### 3.2 축 ① — SMS 서버를 업무 서비스로 묶기 (`noise_gate/domain/biz_service.py` 순수 함수 · LLM 0)

| 신호 | 등급 | 규칙 | 결과 |
|---|---|---|---|
| **S1 등록명 업무 꼬리** | 강 | `split_business_suffix(server_name)`의 업무명을 정규화(NFKC · 공백 제거 · 소문자)해 **정확 일치**하는 서버끼리 | 자동 확정(`confirmed`) |
| **S2 APM 도메인** | 중 | `apm_host_map` `state=active` 행의 `targets[]`를 `(source_id, domain_id)`별 합집합 | 「자동 · 추정」(`inferred`) — Q2 |
| **S3 ITAM 업무** | 중 | ITAM 서버 단서 칸에 업무명이 든 서버(D-362 ⑥ 규칙) → `canonical_host_key`로 폴스타 **1대 일치**만(D-224 ④) | 「자동 · 추정」 — Q2 |
| **S6 호스트 접두 업무코드** | 중 | 호스트 이름 앞 3글자 = 업무코드(개정 1) — §3.2.1 | 「자동 · 추정」(`basis=host_prefix`) |
| **S7 자원 트리 그룹**(개정 5) | 중 | 폴스타 자원 트리에서 서버에 가장 가까운 비일반어 폴더(`…>AI 금융상담시스템(KAH)>운영>서버` → 「AI 금융상담시스템(KAH)」) — §3.2.2 | 「자동 · 추정」(`basis=tree_group`) |
| **S4 호스트 이름 계열** | 약 | `canonical_host_key` 첫 레이블 → `host_group` 규칙 → stem ≥4자 · 같은 stem ≥2대 | **후보 전용** |
| **S5 이름 토큰 유사** | 약 | 업무명끼리 `name_tokens` 겹침(약어 「인뱅」은 못 잡음 — 고지) | **병합 후보 전용** |
| **S8~S12 보조**(개정 5) | 보조 | 등록 시각 묶음 · IP 대역 · ITAM 계약 · 알람 동시 발생 · 가용성 의존 — 이미 배정된 이웃 서버의 서비스로 끌어온다 · §3.2.3 | **후보 전용**(서비스 생성·자동 구성원 0) |

- **합치기**: 강·중 신호로 잇는 그래프의 연결 성분 = 서비스. **중 신호는 강 신호 서비스 1개에만 붙는다** — 한 서버가 서로 다른 S1 업무명 둘에 이어지면 잇지 않고 「충돌 · 검토 필요」(중 신호가 강 신호 서비스끼리를 다리 놓는 것 차단). 보조 신호(S8~S12)는 그래프에 들어가지 않고 합치기가 끝난 뒤 후보만 낸다.
- **순도 보정**(개정 5): S6·S7과 보조 신호는 계산마다 확정 구성원(S1·수동·확인)과 대조한 순도를 재고, 기준 미달이면 그 존에서 그 신호를 끈다(§3.2.3 4 · 실행 기록에 사유).
- **서비스 키**: S1 업무명 정규형 → ITAM 업무 코드 → `prefix:{업무코드}`(S6) → `tree:{정규화 그룹명}`(S7) → `apm:{source_id}:{domain_id}`. 재계산 사이 안정적이라 관리자 편집이 키에 붙는다.
- **상한**: 서비스당 SMS 구성원 `NOISE_BIZ_SERVICE_MAX_MEMBERS`(기본 200) 초과면 `ambiguous`(일반어 업무명 의심) · 자동 확정 0.
- **일반어**: S1·S3 업무명이 `service_host_lookup.yaml` `generic_terms`뿐이면 신호 아님. S6 제외 접두는 `config/knowledge/biz_service.yaml`(§3.2.1 6).

#### 3.2.1 S6 — 호스트 접두 업무코드 (개정 1)

명명 정책 「호스트 이름 앞 3글자 = 업무코드」를 결정적 키로 쓴다. 유사도가 아니라 **정의된 키의 정확 일치**라서 S4·S5와 달리 자동 구성원이 될 수 있다. 「만들기도 한다」 — 모든 서버가 따르지는 않으므로 확신도는 늘 `inferred`다.

1. **코드 추출**: 대상 = 폴스타 `hostname` → 비면 등록명 호스트 부분. 둘이 다르면 `hostname`을 쓰고 근거에 불일치를 남긴다(Q10). 소문자 첫 레이블에서 `strip_prefix_patterns`(개정 5 — 존·센터 머리 정규식 · 맞는 것 중 첫 번째만 1회 제거)를 떼고, 남은 앞 `length`(3)글자를 **구분자 제거 전** 원문에서 자른다 · `[a-z]{3}`가 아니거나 IP·`exclude_prefixes`면 코드 없음.
   - **개정 5 근거**: 운영 경로 `…>AI 금융상담시스템(KAH)>운영>cob0-kahapp01`(plans/72 Q4 · 사용자 제공)에서 업무코드 `kah`는 머리 `cob0-` **뒤**에 있다. 샌드박스 픽스처 `cocm-hdkapp01`·`cob0-bnndbp01`·`cocm-xgzapp09`도 같은 모양이다(`co` + 존 db_id 꼬리 `b0`·`cm`과 같은 글자 — **추정**). 떼지 않으면 앞 3글자가 존마다 같은 `cob`·`coc`가 되고, 3의 「다업무 코드 배제」가 그 존 S6을 통째로 무효로 만든다(오묶음은 없지만 신호 0). 패턴 값은 W0-6에서 확인한 뒤 채운다(기본 `[]` — `^co(b0|cm)-`는 **확인 대상**이지 기본값이 아니다).
2. **ITAM 대조**: 코드가 `itam_snapshot_app.app_code`와 대소문자 무시 정확 일치하면 표시명을 그 업무명으로 하고 근거에 「ITAM 업무코드 일치」(코드 체계 확인은 W0-7 — 다르면 무효).
3. **S1과의 대응 학습**(계산마다 다시 구함): S1 서비스 구성원의 최빈 코드 비율 ≥ `min_share`(0.8) · 구성원 ≥2대면 「코드 → 그 업무」. **한 코드가 S1 업무 2개 이상에 나타나면 그 코드는 업무코드가 아니다**(전사 공통 접두) — 묶지 않고 해당 서버는 「업무 후보 N개」로만.
4. **붙이기**: 대응 코드의 꼬리 없는 서버 → 그 S1 서비스에 `inferred` · 대응 없는 코드는 같은 코드 ≥2대면 새 서비스 `prefix:{코드}`(Q9) · S1 업무명과 코드 대응이 다르면 **S1이 이긴다**(근거에 「접두 코드 불일치」).
5. **합치기 제약**: 중 신호 규칙 그대로 — 3의 둘째 항이 접두 코드가 두 S1 서비스를 잇는 것을 막는다.
6. **설정** `config/knowledge/biz_service.yaml`(application 계층 로더가 읽어 순수 함수에 인자로 · 값 오류 시 S6 비활성 + 경고 — 침묵 금지):
   ```yaml
   host_prefix_code:
     enabled: true
     length: 3
     min_share: 0.8
     prefix_only_services: inferred   # inferred | candidate — Q9
     exclude_prefixes: []              # W0-6 결과로 채운다(값을 지어내 넣지 않는다)
     strip_prefix_patterns: []         # (개정 5) 존·센터 머리 정규식 — W0-6 · 각 패턴은 ^로 시작해야 한다(아니면 S6 비활성 + 경고)
   ```
7. S4 stem은 S6보다 잘다(`ibkweb` ⊂ `ibk`) — 같은 S6 서비스 안의 S4 후보는 내지 않는다.

#### 3.2.2 S7 — 폴스타 자원 트리 그룹 (개정 5 · 중)

폴스타 운영자는 서버를 자원 트리 폴더 아래 등록한다. 폴더 이름이 업무 시스템명인 실례가 있다(§3.2.4 ①). 이름 규칙(S1·S6)과 **독립된 사람의 분류**라서 이름이 약한 서버를 메울 수 있다. 다만 트리를 센터·OS·팀 기준으로 짠 존도 있을 수 있으므로 S6과 같은 「S1 대응 학습 + 다업무 배제 + 순도 보정」을 거친다.

1. **경로**: 서버 `id_ancestry`(`1>…>부모>자기>`)의 조상 id → 보강 읽기(§3.2.4)로 얻은 폴더 이름. 루트와 자기 자신은 뺀다. 폴더 자원 유형은 W0-19로 확정한다 — 후보는 `management.Group`(b0 834건)이다. `management.MonitorGroup`은 샌드박스에서 **서버의 자식**이라 폴더가 아니다.
2. **그룹 수준**: 서버에서 가까운 폴더부터 올라간다. 이름이 `generic_terms`(운영·개발·테스트·스테이징 등 — `service_host_lookup.yaml` 기존 목록 재사용 · 새 값 0)이거나 서버 이름과 같은 폴더는 건너뛴다. 처음 남는 폴더가 그 서버의 트리 그룹이다. 이름 끝 괄호 안 영숫자 2~10자 `(KAH)`는 `tree_code`로 뗀다.
3. **S1 대응**(S6 3과 같은 방식 · 계산마다): 트리 그룹 안 S1 구성원의 최빈 서비스 비율이 `min_share`(0.8) 이상이고 2대 이상이면, 그 그룹의 꼬리 없는 서버를 그 서비스에 붙인다(`inferred`). **S1 서비스 2개 이상이 섞인 그룹은 업무 폴더가 아니다**(센터·인프라 폴더) — 그 그룹 수준은 신호가 아니고, 아래 폴더가 있으면 아래가 쓰인다.
4. **새 서비스**: S1 구성원이 없는 그룹에 서버가 2대 이상이면 새 서비스 `tree:{정규화 그룹명}`을 만든다(`inferred`). 표시명은 그룹명이다. `tree_code`가 ITAM `app_code`와 정확히 일치하면 표시명은 ITAM 업무명, 키는 ITAM 코드다. 존이 달라도 정규화 그룹명이 같으면 한 서비스다(Q3). 그룹명이 `generic_terms`뿐이면 만들지 않는다.
5. **교차 확인**: `tree_code`가 S6 접두 코드와 같으면 근거에 「트리 코드 = 접두 코드」를 남긴다. 확신도는 올리지 않는다(Q30 — 배지만). 다르면 「불일치」만 기록하고, S1이 있으면 S1이 이긴다.
6. **순도 보정**(§3.2.3 4)에 미달하면 그 존의 S7을 끈다. 보강 읽기가 실패하거나 W0-19에서 그 존에 `id_ancestry`가 없다고 나오면 S7을 끄고 사유를 남긴다(침묵 금지).

#### 3.2.3 보조 신호 S8~S12 — 전파형 후보 (개정 5 · 후보 전용)

유사성·동시성 단서는 업무가 아닌 이유로도 겹친다 — 일괄 이관, 망 분리, 전사 공용 계약, 공용 장애. 그래서 보조 신호는 **서비스를 스스로 만들지 않는다.** 이미 배정된 서버(닻)의 서비스로 미배정 서버를 **후보**로 끌어올 뿐이다(D-348 — 확정은 사람).

| 신호 | 묶음 키(또는 이웃) | 원천 | yaml 기본 |
|---|---|---|---|
| **S8 등록 시각 묶음** | 존별로 `ctime` 오름차순 정렬. 앞 서버와 간격이 `gap_seconds` 이하면 같은 묶음이다. 묶음 크기는 2~`max_batch`여야 하고, 넘는 묶음은 「대량 등록(이관 의심)」이라 신호가 아니다 | 폴스타 `ctime`(ms epoch로 보임 — W0-20) · 보강 읽기 | on |
| **S9 IP 대역** | 대표 `ip`의 IPv4 /`prefix_len`. `exclude_cidrs`는 뺀다. 보조 NIC IP(`nic_ips`)는 쓰지 않는다(백업·관리망은 전 서버 공통 대역) · IPv6는 뺀다 | `sms_inventory.ip`(새 조회 0) | on |
| **S10 ITAM 계약** | 같은 구매 계약번호, 같은 유지보수 계약번호(각각 따로 묶음). ITAM 서버와 폴스타는 `canonical_host_key` 1대 일치만 잇는다(D-224 ④) | ITAM 서버 통합 현황 계약 칸(W0-22) · 보강 읽기 | **off**(ITAM 구조 정본 미작성 — plans/95 G-4) |
| **S11 알람 동시 발생** | 같은 분에 알람이 `min_co`회 이상 겹친 서버 쌍이 이웃이다(최근 `lookback_days`). 그 분에 알람 서버가 `burst_hosts`대를 넘으면 그 분은 세지 않는다(망·공용 장애) | 노이즈 게이트 결정 이력 `recent_decisions`(로컬 파일 · 관측 조회 0) | **off**(W0-23) |
| **S12 가용성 의존** | `avail_depend_resource_id(_2)`로 직접 이어진 서버 ↔ 서버가 이웃이다(대상이 서버가 아니면 뺀다) | 폴스타 · 보강 읽기 · E4 토폴로지가 쓰는 칼럼 | on |

**규칙**(공통 · 결정적 · 순수 함수 `propagate_candidates`):
1. **닻** = 합치기 결과(S1~S7) + 수동·확인 구성원이다. `excluded` 구성원은 닻이 아니다.
2. 미배정 서버 x의 묶음(또는 이웃)에서 닻이 `min_anchors`(2) 이상이고 **전부 한 서비스 S**에 속하면 후보를 낸다(x → S · `basis=aux:<신호>`). 닻이 서비스 2개 이상에 걸치면 그 묶음은 「공용」이라 아무것도 내지 않는다(실행 기록에 건수만). 닻이 없으면 아무것도 내지 않는다.
3. 같은 x → S를 여러 신호가 내면 후보 1건에 근거를 여럿 단다. 검토함 정렬은 근거 수 내림차순, 다음 순도 내림차순이다. x가 두 서비스의 후보면 둘 다 보이고 「후보 2개」로 표시한다.
4. **순도 보정**(계산마다 · 존별 · 신호별 — S6·S7 포함): **확정 닻**(S1·수동·확인 — `inferred`는 순환을 막으려고 뺀다)이 2대 이상인 묶음 가운데 서비스가 1개뿐인 묶음의 비율을 순도로 잰다. 순도가 `min_purity`(0.8) 미만이거나 평가할 묶음이 `min_groups`(5) 미만이면 그 존에서 그 신호를 끈다. 사유는 실행 기록과 P1 ④에 보인다(예: 「S9 순도 0.35 — 대역이 업무가 아니라 계층으로 나뉜 것으로 보임」). 평가 묶음이 적어 끈 경우와 순도 미달로 끈 경우는 사유를 구분한다.
5. 보조 신호는 서비스를 만들지 않고, 자동 구성원을 만들지 않고, 구성원을 옮기지 않는다. 수락은 관리자가 한다(검토함 일괄 수락 가능 · 수락 = `origin=manual`·`confirmed` · 근거 보존).

**설정**(`config/knowledge/biz_service.yaml` — 값 오류 시 그 신호만 끄고 경고). 숫자는 **초기값**이며 W0-20~W0-23 분포를 보고 고친다:
```yaml
tree_group: {enabled: true, min_share: 0.8}
aux_signals:
  min_anchors: 2
  min_purity: 0.8
  min_groups: 5
  registered_batch:   {enabled: true,  gap_seconds: 120, max_batch: 20}
  ip_subnet:          {enabled: true,  prefix_len: 24, exclude_cidrs: []}   # W0-21
  itam_contract:      {enabled: false}
  alarm_cooccurrence: {enabled: false, min_co: 3, lookback_days: 14, burst_hosts: 10}
  avail_dependency:   {enabled: true}
```

#### 3.2.4 보강 읽기 · 실측 근거 · 쓰지 않는 단서 (개정 5)

**보강 읽기**(S7·S8·S12 · S10이 on이면 ITAM) — 210 스캐너와 스냅샷은 바꾸지 않는다. 214이 소유하는 고정 SELECT를 미리보기 때 존별로 돌린다(`noise_gate/infrastructure/biz_service_sql.py` · Q29).
- ① 서버 행 키셋 페이지(≤1000/페이지 · 210 서버 페이지와 같은 조건 `server.Server` · 삭제 제외): 자원 id · `id_ancestry` · `ctime` · `avail_depend_resource_id`·`_2`.
- ② 트리 폴더 이름: ①에서 모은 조상 id 집합을 IN 묶음(≤500)으로 나눠 id · 이름 · 자원 유형을 읽는다.
- ③ (S10 on일 때만) ITAM 서버 통합 현황의 호스트명 · IP · 구매 계약번호·이름 · 유지보수 계약번호·이름 · 담당 부점명. **담당자 성명·사번 칸은 SELECT에 넣지 않는다**(PII).
- 결과는 `biz_service_attr`(run별 · §3.5)에 저장하고, 적용은 같은 run의 값을 쓴다(미리보기와 적용이 같은 입력). 최근 run 3개분만 보존한다.
- 실패는 신호 단위로 처리한다. 그 신호만 「보강 읽기 실패 · 사유」로 끄고 계산은 계속한다(개별 try · 침묵 금지).
- 부하는 존당 (서버 수 / 1000) + 폴더 IN 묶음 수 만큼의 SELECT다. 수동 미리보기 때만 돈다(Q8) · MCP 동시 상한 안 · 동시 1.
- 외부 입력은 정수 id뿐이다. 표·칼럼 리터럴은 infrastructure에만 둔다(overfit_check — domain 독스트링에도 쓰지 않는다).

**실측 근거**(2026-11-05):

| # | 단서 | 실측 | 판단 |
|---|---|---|---|
| ① | 자원 트리 | 실 DB 익스포트 행(`testdata/pg/05_insert_excel_data.sql` — `docs/plan61_bugfix_plan.md:38` 「실캡처」) `hostapo01·02`의 `id_ancestry` = `1>10812>150578>288594>72616>{자기}>`: 같은 부모 72616, 등록명 꼬리도 같은 업무(「빅데이터 까페 WAS#1·#2」) · 사용자 제공 운영 경로 `…>AI 금융상담시스템(KAH)>운영>cob0-kahapp01`(plans/72 Q4) · b0 비 `server.*` 자원 실측 `management.Group` 834 · `management.MonitorGroup` 4,276(plans/201 §①″) · 알람 `resource_ancestry` = 「폴스타 트리 전체 경로」(`noise_gate/domain/alarm.py:94`) | **S7 중** — 폴더 이름(72616 등)은 샌드박스에 없어 W0-19 |
| ② | 등록 시각 | 같은 두 행 `ctime` 1574822258074 · 1574822259602(**1.5초 차**) · `resource_key` `MA_hostapo0N_20191002170929`(설치 시각 동일) / 합성 픽스처 8대는 업무가 서로 다른데 `ctime`이 모두 1767200000000(일괄 적재) | **S8 후보** · 대량 묶음은 배제(`max_batch`) |
| ③ | IP 대역 | 샌드박스 /24 분포: `10.0.1` 웹 10대 · `10.0.2` WAS 10대 · `10.0.3` DB 10대 — **업무가 아니라 계층별**(합성 데이터라 운영 근거는 아니다). 웹 DMZ · 내부 WAS · DB망으로 대역을 나누는 망 분리 설계라면 같은 대역 = 같은 계층 · 다른 업무다 | **S9 후보 + 순도 보정 필수** · 계층 단서 후보(W0-12) |
| ④ | ITAM 계약 | 서버 통합 현황 전사본: 구매 계약번호·이름 · 유지보수 계약번호·이름 · 취득일 · 담당 부점 · 보유 부점 · 가상화 군집 참조 id(`testdata/itam/schema.yaml` · 벤더 시트 판독본 · 실 DB 대조 미실시) | **S10 후보**(off) · 담당 부점은 `owner_org` 제안(Q33) |
| ⑤ | 가용성 의존 | `avail_depend_resource_id(_2)` — `polestar_topology`(`mcp_server/mcp_server/polestar_tools.py:344`)·E4가 쓰는 칼럼 · 샌드박스 0건 | **S12 후보** · 채움률 W0-24 |
| ⑥ | 알람 동시 | 결정 이력은 보존 상한 없이 쌓인다(`decision_store.py:1425`) · 시간 창 조회 `recent_decisions` | **S11 후보**(off · W0-23) |

**쓰지 않는 단서**:

| 단서 | 이유 |
|---|---|
| EAV OS·버전·패치·벤더·모델·일련번호 | 표준화·일괄 구매 단위다 — 같은 모델이 전 업무에 깔려 업무를 가르지 못한다 |
| `mtime`(수정 시각) | 어떤 편집에도 바뀐다 |
| ITAM 가상화 군집·가상화 관리 서버 id | 한 군집에 여러 업무가 올라간다 — 업무를 오히려 섞는다 |
| `location` · ITAM 설치 지역 | 센터 단위라 존(김포·여의도 · D-271)과 겹친다 · 샌드박스 0건 |
| `nic_ips`(보조 NIC) | 백업·관리망은 전 서버가 같은 대역이다 |
| ITAM 취득일 | 일 단위라 거칠다 — 같은 구매라면 S10 계약번호가 더 정확하다 |
| ITAM 담당자 성명·사번 | PII — 읽지 않는다 |
| `acl_manager_group_id` · `importance_id` | 의미 미확인(권한 그룹 · 중요도) — W0-24로 의미를 확인한 뒤 판단한다(중요도는 묶기가 아니라 `tier` 제안 후보) |
| 통신 흐름(접속 상대) 기반 서비스 발견(업계 도구의 주 방식) | 이 저장소 원천에 접속 상대 목록이 없다(`server.Netstat`은 통계 자원) — §16 |

### 3.3 축 ② · ③ — APM 구성요소를 업무 서비스에 잇기

- **축 ②는 `plans/210`이 정본**이다 — 이 계획은 `apm_host_map`을 **읽기만** 한다(쓰기·판정 순서 불변).
- **도출 규칙**(`derive_apm_members`): `apm_host_map` `state=active` · `target_gone` 아닌 대상만 본다.
  - instance·workload 행: 대상 SMS 서버가 **모두 같은 서비스 S**에 속하면 그 인스턴스(또는 워크로드)를 S의 `apm_instance` 구성원으로. 확신도는 매핑을 따른다 — `manual`·`verified` → `confirmed`, `auto` → `inferred`. 대상이 **서로 다른 서비스로 갈리면** 잇지 않고 「APM 충돌」(공용 WAS 등 · 검토 필요).
  - domain 행 · 또는 한 도메인의 인스턴스가 전부 S로 이어짐: `apm_domain` 구성원(도메인 단위 지표 조회 키).
  - 매핑이 없는 인스턴스: 같은 도메인의 다른 인스턴스가 S로 이어졌으면 **후보**(「같은 도메인 · 미연계」) · 연계 추가는 210 관리 탭으로 안내(이 계획은 210 매핑을 만들지 않는다 — Q12).
- **역방향 신호**: S2(APM 도메인 → SMS 서버 묶기)는 축 ①의 입력이기도 하다. 순환은 없다 — 축 ①이 SMS 서비스를 먼저 확정하고, 축 ③은 그 결과 위에서만 도출한다(같은 계산 1회 안의 2단계).

### 3.4 연계 분석 — 서비스별 · 전체

계산(미리보기·적용)마다 결정적으로 구해 실행 기록에 저장한다.

| 지표 | 서비스별 | 전체 |
|---|---|---|
| SMS 서버 수(존·DB별) · 확정/추정 | ● | 서비스 미소속 서버 수 |
| APM 연계 서버 수 / **APM 없는 서버** 수 | ● | — |
| APM 인스턴스 수 · **SMS에 못 이은 인스턴스**(같은 도메인 기준) | ● | 서비스 미소속 인스턴스 수 |
| 추정 비율(구성원 중 `inferred`) | ● | ● |
| 끊김(`target_gone`) · 충돌(SMS·APM) · 열린 후보 | ● | ● |
| 직전 적용 대비 변화(구성원 증감 · 연계 증감) | ● | ● |
| (개정 5) 신호별 기여(구성원·후보 수) · **신호 순도·켜짐 여부·끈 사유**(존별) · 「공용」 묶음 수 · 보강 읽기 실패 | 기여만 | ● |

- **연계 그래프 보기**: 서비스 → APM 도메인 → 인스턴스 → SMS 서버의 표(트리). 끊긴 선·추정 선을 구분 표시.
- **추이**: `biz_service_run.summary`(서비스별 지표 JSON)를 실행 시각 순으로 그린다 — 「APM 연계 서버 비율」 등 커버리지가 좋아지는지 본다.

### 3.5 저장 — PG 정본(API 프로세스만 쓴다 · D-049)

`ddl/biz_service.sql` + `src/api/server.py` 기동 시 보장(플래그 on일 때만)

| 표 | 핵심 칼럼 |
|---|---|
| `biz_service` | `id` · UNIQUE `service_key` · `display_name` · (개정 4) `tier`(중요도 등급 1~3 · NULL = 미지정 — 현황 정렬·필터) · `description`(≤500자) · `owner_org`(담당 부서 텍스트 ≤60자 — **개인 이름·연락처 금지** 안내 · PII 패턴 검사로 거부) · `origin`(auto·manual) · `state`(active·ambiguous·disabled) · `verified` · `monitored`(M3 감시 대상 · 기본 false) · `evidence` JSONB(신호별 건수·업무명 원문 토큰·ITAM 코드·`scan_suggests`) · `note` · `created/updated_by/at` · `version` · `last_run_id` |
| `biz_service_member` | `service_id` · `kind`(sms_server·apm_instance·apm_domain·apm_business) · `member_key`(종류별 정규 키) UNIQUE(`kind`,`member_key`) — **SMS는 한 서비스**(UNIQUE가 보장) · APM은 도출이라 같은 UNIQUE · 표시 칼럼(서버명·hostname·IP·db_id·존 / source·도메인명·인스턴스명) · `prefix_code` · `origin` · `basis`(biz_suffix·apm_domain·itam·host_prefix·tree_group·apm_host_map·manual — 보조 신호 후보를 수락하면 `manual` + `evidence.aux`에 근거 보존) · `confidence`(confirmed·inferred) · `state`(active·excluded·target_gone) · `apm_map_id`(축 ③ 출처) · (개정 4) `layer`(web·was·db·batch·etc · NULL = 미지정) · `layer_origin`(auto·manual — 자동 제안은 호스트 이름 토큰 규칙 `config/knowledge/biz_service.yaml` `layer_tokens`(W0-12로 채움 · 값을 지어내지 않는다) · 관리자 지정은 재계산이 덮지 않는다) — D1 R2·P2 구성 맵이 쓴다 |
| `biz_service_candidate` | `kind`(member·merge·apm_unlinked) · 대상 · 근거(개정 5 — `signals[]`: `aux:registered_batch`·`aux:ip_subnet`·`aux:itam_contract`·`aux:alarm_cooccurrence`·`aux:avail_dependency`마다 묶음 키·닻 수·순도) · `status`(open·accepted·rejected) — 거절은 기억해 다시 내지 않는다 |
| `biz_service_run` | `run_id` · 요청자 · 기준 `scan_run_id` · `mode`(preview·apply) · `summary`(전체 + 서비스별 §3.4 지표 · 개정 5 신호 순도) · 보존 90일 |
| `biz_service_attr`(개정 5) | `run_id` · `db_id` · `resource_id` · `tree_group_id`·`tree_group_name`(≤255)·`tree_code` · `registered_at` · `depends[]`(자원 id) · (S10 on) `contract_no`·`contract_name`·`maint_contract_no`·`owner_branch` — 보강 읽기 결과(§3.2.4) · 최근 run 3개분만 보존 · 담당자 개인 칸 없음 |

**Redis 사본(API 프로세스가 유일한 기록자)** — M2·M3이 매 주기 PG를 읽지 않도록 적용 시마다 재구성:
- `bizsvc:idx:sms` 해시 `{db_id}:{resource_id}` → service_id · `bizsvc:idx:host` `canonical_host_key` → service_id(1대일 때만) · `bizsvc:idx:apm` `{source}:{domain}:{instance_key}` → service_id · `bizsvc:idx:apmdom` `{source}:{domain}` → service_id.
- 기동 시 전체 재구성 · 사본이 비면 M2 집계는 「서비스 미상」으로 셀 뿐 오귀속 없음.

### 3.6 계산 실행 — 미리보기 → 적용

1. `POST /preview` — 현재 스냅샷 + `apm_host_map` 현재 행으로 §3.2 → §3.3 → §3.4 계산 · 저장 상태와의 차이 반환 · 저장 0(실행 기록만 `preview`).
2. `POST /apply {preview_run_id}` — 미리보기 뒤 스캔·연계 매핑이 바뀌었으면 409 `snapshot_changed`. 쓰기 규칙:

| 경우 | 처리 |
|---|---|
| 키에 서비스 없음 | auto 신규(`monitored=false`) |
| auto·미확인 · 결과 동일 / 변경 | `updated_at`만 / 갱신 + `evidence.previous` |
| manual·verified | 불변 · 다르면 `scan_suggests` |
| disabled | 불변 |
| 구성원 `excluded` · 관리자가 옮긴 구성원(`origin=manual`) | 자동이 바꾸지 않음 |
| 스냅샷에서 사라짐 | `target_gone` · 180일 뒤 auto 구성원 삭제 |
| APM 구성원 — 출처 매핑이 사라지거나 비활성 | 구성원 `target_gone`(관리자 수동 APM 구성원은 유지) |

- 단독 실행 Redis 락 `bizsvc:lock`(`SET NX EX 600`) · 210 스캔 실행 중이면 409 `scan_running`.
- 스캔·210 매핑 변경 뒤 자동 연쇄 없음 — 화면에 「스냅샷이 바뀌었습니다 · 다시 계산」 표지만(Q8).

### 3.7 업무 정본 관계 — 기존 업무 해석 자산과의 경계 (개정 3)

| 자산 | 무엇의 정본 | 이 계획과의 관계 |
|---|---|---|
| `biz_service`(이 계획 · PG) | **업무 서비스 구성원**(SMS 서버 + APM 인스턴스·도메인·제니퍼 업무) — 모니터링·진단 입력 | 정본 |
| 게이트웨이 `business_map`(D-290 ② B0) | 채팅 업무명 → 인스턴스 **수동 해석** | 건드리지 않는다. 본체가 못 읽는다(D-274 ⑤). P1 서비스 상세에 「게이트웨이 업무 매핑 사본(YAML)」 미리보기·복사만 둔다(확정 APM 구성원 → `{business, aliases, instances, source_id}` — 관리자가 정합 파일에 붙여 넣을지 정한다 · Q17) |
| `apm_instance_map(business=)` B1~B3 · 제니퍼 업무 | 채팅 즉석 해석 | 서비스 상세 「APM 대조」: 서비스 표시명으로 1회 해석(관리자 버튼 · 게이트웨이 1호출 · `owner=biz_admin`)해 확정 APM 구성원과의 **차이**(해석에만 있음 / 구성원에만 있음)를 보인다. 해석 결과는 구성원으로 자동 편입하지 않는다(후보 `kind=member` · 근거 `gateway_business`) |
| 본체 E6(폴스타 등록명·비고 LIKE) | 채팅 업무명 → 서버 | 바꾸지 않는다. 채팅이 `biz_service`를 먼저 쓰게 하는 간선(E8)은 후속(Q18) — 채팅·보드의 구성원이 다를 수 있음을 매뉴얼에 적는다 |
| ITAM 업무 사슬(D-362 ⑥ · `apm_host_map_itam.py`) | APM 도메인 → ITAM 업무 → SMS 서버(추정) | S3(서버 → 업무)는 같은 규칙을 반대 방향으로 쓴다 — 같은 ITAM 근거가 두 방향에서 결론이 다르면 「충돌 · 검토 필요」(§3.2 합치기 규칙) |

- **용어 분리**: 화면·API·독스트링은 「업무 서비스」(`biz_service`)만 쓴다. 「업무(business)」는 제니퍼 업무를 뜻하는 기존 인자(`apm_business`·`business=`)라 섞지 않는다(D-354 `duty_areas`와도 다르다).

## 4. 모니터링 (개정 2) · 복합 진단 (개정 3)

### 4.1 3층

| 층 | 무엇 | 데이터 출처 | 관측 시스템 추가 부하 |
|---|---|---|---|
| **M1 연계 상태** | §3.4 지표 · 끊김·충돌·후보 · 스냅샷 경과 시간 | PG 실행 기록 | 0 |
| **M2 알람 현황** | 서비스별 열린 사건 · 최근 N분 알람(심각도별 · 전달/억제/강등) · 마지막 알람 시각 | 노이즈 게이트 열린 사건·결정 이력 | 0 |
| **M3 실시간 지표** | APM 골든 시그널 · SMS 시간 통계 · 서비스 상태 판정 | 제니퍼 게이트웨이 · 폴스타 DB(readonly MCP) | **있음 — §4.3 상한** |

### 4.2 M2 — 알람 현황 (조회 추가 0)

- `IncidentRepository.list_open` + `decision_store` 최근 창(`NOISE_BIZ_MONITOR_ALARM_WINDOW_MINUTES` 기본 60)의 알람을 서비스로 귀속한다.
- **귀속 순서**(첫 성립에서 멈춤 · 결정적): 알람 `meta.hints.polestar_link`의 `db_id`+`resource_id`(D-362 연계 결과) → `bizsvc:idx:sms` · 제니퍼 알람의 `(source, domain, instance)` → `bizsvc:idx:apm` → 도메인 `bizsvc:idx:apmdom` · 호스트 이름 → `canonical_host_key` → `bizsvc:idx:host`(1대일 때만) · 그 밖 「서비스 미상」(건수로 노출).
- (개정 5) 「서비스 미상」 알람은 `resource_ancestry`(트리 이름 경로)에서 §3.2.2 2의 트리 그룹을 뽑는다. 그 그룹명이 S7 서비스 1개에 대응하면 「서비스 미상 · 트리 그룹 추정: X」로 **표시만** 한다(집계에 넣지 않는다 · 조회 추가 0).
- 집계는 API 요청 시 계산 + 10초 메모 캐시(보드가 여럿이어도 1회). 워커 경로·판정은 바꾸지 않는다.

### 4.3 M3 — 실시간 지표 수집기

**실행 위치·주기**
- API 프로세스 백그라운드 태스크 1개 · 리더 잠금 `bizmon:leader`(`worker_lock` 패턴 — API 인스턴스가 여럿이어도 수집은 한 곳).
- **수요 기반**(Q13): 보드가 열려 있으면 화면이 30초마다 `POST /monitor/heartbeat` → `bizmon:viewers` `EX 120`. 키가 없으면 수집 0(보는 사람 없으면 부하 0).
- **감시 대상만**: `biz_service.monitored=true`(관리자가 켬) 서비스 · 최대 `NOISE_BIZ_MONITOR_MAX_SERVICES`(기본 20).

**APM (제니퍼 · 주기 `NOISE_BIZ_MONITOR_APM_INTERVAL_SECONDS` 기본 60)**
- 감시 서비스의 `apm_instance` 구성원을 소스별로 묶어 `apm_app_health(targets=[…], owner="biz_monitor", lookback_minutes=5)` **소스당 1호출**(최대 인스턴스 `NOISE_BIZ_MONITOR_MAX_APM_TARGETS` 기본 100 · 넘으면 서비스 순서대로 자르고 「일부만 감시」 고지).
- 호출자 공정 분배(D-356 ⑤)로 사용자 질의를 밀어내지 않는다 · 게이트웨이 소스당 동시 상한(D-347)·차단기 그대로.
- 결과: 인스턴스별 응답시간·TPS·에러율·액티브 서비스 + **게이트웨이 신호**(`was_signals` — §2 정정: 위험 2규칙)를 그대로 쓴다(판정 로직 새로 만들지 않음 — Q14).
- **X-View 무거운 조회 금지**: `xview_confirm_over` 기본 그대로 · 긴 창 조회 없음(5분 창 고정). 창을 주면 `was_error_burst` 판정이 살지만 X-View 사전 확인이 붙을 수 있다 — W0-10에서 「창 5분」과 「창 없음(실시간만 · 오류 급증 판정 없음)」의 소요를 둘 다 재고 정한다.
- **도메인당 1호출 대안**: 감시 인스턴스가 한 도메인에 많으면 `apm_fleet(mode=ranking, domain_id, full=true)` 도메인당 1호출이 더 싸다 — W0-10 결과로 소스·도메인별로 고른다(코드는 두 경로를 다 두지 않고 하나로 정한다).
- **제니퍼 업무 지표**(구성원 `apm_business`가 있을 때만): `apm_business(mode=current, business=[…], domain_id)` 도메인당 1호출 · 같은 APM 주기 · 같은 예산 안.

**SMS (폴스타 · 확인 주기 `NOISE_BIZ_MONITOR_SMS_INTERVAL_SECONDS` 기본 600)**
- 폴스타 DB의 가장 촘촘한 지표는 **시간 통계**다 — 「실시간」이 아니라 「최신 정각 통계」로 표기한다. Prometheus 노드 지표 결합은 v1 밖(Q15).
- 존(db_id)별로 감시 서버 `resource_id`를 ≤500개씩 묶어 **코드가 조립한 고정 SELECT 1회**(`cmm_metric_stat_h` 최신 `stat_date` · CPU·메모리·파일시스템 사용률 · DB2 `POLESTAR`·`FETCH FIRST` / PG `LIMIT` · `sql_guard` · readonly MCP · 동시 1). 최신 `stat_date`가 직전과 같으면 지표 행 조회를 건너뛴다(확인 질의 1건만).
- SQL 조립은 `noise_gate/infrastructure/biz_service_sql.py`(210 스캐너의 `_table`·`_row_limit`·고정 SELECT 선례 · 외부 입력은 정수 id뿐).
- 지표 이름·자원 유형 칼럼값은 W0-8로 확정(추정 금지).

**시간 예산·실패**
- 주기마다 전체 예산(`APM` 20초 · `SMS` 60초) · 초과분은 이번 주기 건너뜀 + 사유.
- 연속 실패 소스는 주기 ×2 백오프(최대 10분) · 화면에 「조회 실패 · 마지막 성공 HH:MM」(침묵 금지).

### 4.4 서비스 상태 판정

| 상태 | 조건(어느 하나 · 위가 우선) |
|---|---|
| **위험** | 열린 사건 중 심각도 critical 이상 · 구성원 APM 신호 CRITICAL(`was_service_queuing`·`was_error_burst`) · SMS 사용률 ≥ `crit` |
| **주의** | 열린 사건(그 밖) · SMS 사용률 ≥ `warn` (APM에는 주의 단계가 없다 — 게이트웨이 신호가 위험뿐) |
| **이상 신호 없음** | 위에 해당 없음 · 지표가 주기 안에 들어옴 (D-335 ② — 「정상」 어휘를 쓰지 않는다) |
| **판정 불가** | 감시 꺼짐 · 지표 없음 · 조회 실패 — 사유를 함께 보인다 |

- **정정(개정 3)**: APM 응답시간·TPS·에러율 값은 **표시만** 하고 본체가 임계로 판정하지 않는다 — 본체에 APM 지표 임계 리터럴을 두지 않는 D-335 ①을 따른다. 응답시간 판정이 필요하면 게이트웨이 결정적 판정(D-335 ① `apm_judge_values` — 미구현 · `plans/155`)이 생긴 뒤 그 결과를 쓴다(Q14).

- SMS 임계는 `config/knowledge/biz_service.yaml` `sms_thresholds`(cpu·memory·filesystem의 `warn`·`crit`) — 초기값은 W0-9에서 운영 관행을 확인해 채운다(그 전엔 SMS 판정 「판정 불가 · 임계 미설정」).
- 서비스 상태는 구성원 최악값 · 근거(어느 구성원·어떤 값·어떤 규칙)를 같이 보인다.
- **알림(통보)은 하지 않는다**(Q16) — 알람 통보 경로는 노이즈 게이트 하나(중복 통보 방지).

### 4.5 지표 저장
- Redis 최신값 `bizmon:svc:{id}`(TTL 3×주기) + 24시간 시계열 `bizmon:ts:{id}`(서비스 상태·핵심 지표 · 최대 1,440점 · 오래된 것 자름).
- PG에는 저장하지 않는다(Q11) — 장기 이력은 원천(제니퍼·폴스타)에 있다. 상태 전이(이상 신호 없음→위험 등)만 `biz_service_state_log`(PG · 90일)로 남겨 「언제부터 나빴나」를 본다.
- (개정 6) P2 「지표」 보기(§5.2.1)의 구성원별 추이는 **여기에 저장하지 않는다** — 탭을 열 때 원천(폴스타 시간·일 통계 · 제니퍼 시계열)에서 1회 읽고 60초 메모만 둔다. Redis 24시간 시계열은 개요의 서비스 단위 스파크라인 전용으로 남는다.

### 4.6 복합 진단 — 3층 (개정 3)

| 층 | 무엇 | 실행 | LLM | 관측 추가 부하 |
|---|---|---|---|---|
| **D1 서비스 진단 스냅샷** | 서비스 구성원 전체(SMS·APM)의 알람·사건·APM 신호·SMS 지표를 210 연계로 겹쳐 「이상 구성원 · 최초 이상 시각 순 · 교차 계층 판독」 | 보드 상세를 열 때(API 요청 시 계산) | 0 | M3 캐시가 있으면 0 · 없으면 §4.7 상한 안 즉석 1회 |
| **D2 알람 조사 서비스 맥락** | 구성원 알람의 조사(자동·수동·사건 종합)에 `meta.biz_service` 가산 → sre_agent가 같은 서비스 구성원을 비교 대상으로 | 기존 조사가 나갈 때 | 기존 그대로 | 조사 안의 비교 호출(sre_agent 상한 안) |
| **D3 서비스 심층 조사** | 서비스 하나를 sre_agent에 맡긴다 — D1이 고른 대표 1대 + 이상 구성원 상한 안 | 보드에서 사람이 [서비스 심층 조사] | sre_agent 조사 1건 | 조사 1건(D-347 상한 안) |

- **억제·통보 판정은 바꾸지 않는다** — 서비스 단위 묶음은 D1이 표시용으로만 만든다(사건 키 `zone|host_key` · D-317 ④ 불변 · Q1).
- **결정적 층 위에 LLM**(D-350): D1은 사실 정리, D3 LLM 조사는 D1 결과를 `prior_findings`(기존 인자 — LLM 서술 제외 판정 요약)로 받아 그 위에서만 돈다.

### 4.7 D1 — 서비스 진단 스냅샷 (`noise_gate/domain/biz_diagnosis.py` 순수 함수 · `noise_gate/application/biz_diagnosis.py` 조립)

**입력**(모두 이미 있는 것): 서비스 구성원(§3.5 · Redis 사본) · M2 귀속 결과(열린 사건·최근 알람 · 구성원별) · M3 최신값(`bizmon:svc:{id}`) · 210 연계(APM 구성원 → 이어진 SMS 구성원) · 구성원별 최근 조사(`InvestigationStore` — 원인 영역·확실성 배지 · D-352 ②).
- M3이 꺼졌거나 그 서비스가 감시 대상이 아니면: **즉석 1회** — APM `apm_app_health(targets=APM 구성원 ≤ NOISE_BIZ_DIAGNOSIS_MAX_APM_TARGETS(기본 50), owner="biz_diagnosis")` 소스당 1호출 + SMS 존별 고정 SELECT 1회(§4.3과 같은 빌더). 서비스당 결과 메모 60초(같은 서비스를 여럿이 열어도 1회) · 전역 동시 1 · 예산 20초 · 넘으면 「일부만」 + 사유.

**판독 규칙**(결정적 · 위가 우선 · 결과는 「가설 · 참고용」 표지 — D-335 ② 어휘):
| # | 조건 | 판독 |
|---|---|---|
| R1 | 이상 구성원 = 0 | 「이상 구성원 없음」 |
| R2 | 이상 SMS 서버가 서비스 SMS 구성원의 ≥50% · 또는 서로 다른 계층(구성원 `layer` — 지정된 구성원만 · 미지정이면 이 조건은 건너뛴다) 둘 이상에 동시(창 `NOISE_BIZ_DIAGNOSIS_WINDOW_MINUTES` 기본 15) | 「여러 서버 동시 — 공통 원인 의심(공용 자원·네트워크)」 · 토폴로지 확인 안내(확정 연결만 · D-350 ④) |
| R3 | APM 이상 인스턴스의 이어진 SMS 서버(210 `state=active`)가 같은 창에 OS 이상(알람 또는 SMS 임계) | 「APM 이상 + 그 서버 OS 동반」 — 서버 자원 쪽 먼저 확인 |
| R4 | APM 이상인데 이어진 SMS 서버가 이상 신호 없음 | 「APM 단독 — 애플리케이션 쪽 추정」 |
| R5 | APM 이상인데 이어진 SMS 서버가 없음(미연계) · 또는 SMS 지표 판정 불가 | 「APM 이상 · 서버 확인 불가(연계 없음/지표 없음)」 — 근거 결손을 숨기지 않는다 |
| R6 | SMS 이상만 · APM 구성원은 이상 신호 없음 | 「서버 단독 — 서비스 영향(APM) 미관측」 |

- **최초 이상 순 타임라인**: 구성원마다 이번 창의 첫 이상 시각(알람 발생 시각 · M3 상태 전이 시각)을 정렬 — 「누가 먼저」만 보이고 인과를 단정하지 않는다(상관 ≠ 인과 병기 · D-325 ⑨와 같은 표현).
- **대표 선정**(D3 입력): ① 최초 이상 SMS 서버 ② 없으면 최초 이상 APM 인스턴스의 이어진 SMS 서버(1대일 때만 · D-224 ④) ③ 없으면 대표 없음 → D3 버튼 비활성 + 사유. 동률은 `(db_id, resource_id)` 오름차순(결정성).
- 출력: `{service_id, verdict(R#), anomalous_members[], timeline[], representative, gaps[](연계 없음·지표 없음·권한 밖 N), computed_at, sources{m2, m3|adhoc}}`.

### 4.8 D2 — 알람 조사에 서비스 맥락 부착 (`NOISE_BIZ_INVESTIGATION_CONTEXT_ENABLED`)

- **위치**: 조사 페이로드 조립(`investigation_payload.py` `build_trigger_payload` · 수동 `build_manual_payload` · 사건 종합 `submit_episode_investigation`)에서 대상 서버(또는 APM 알람의 인스턴스)를 `bizsvc:idx:*`로 서비스에 귀속(§4.2 귀속 순서와 같은 함수) → **가산 키 `meta.biz_service`**(값이 있을 때만 · 계약 `contract_version` "1" 유지 · 없으면 페이로드 바이트 동일):
  ```
  meta.biz_service = {
    service_id, display_name, confidence(대상 구성원의 confirmed|inferred),
    sms_members:  [{db_id, server_name, hostname}]            ≤ NOISE_BIZ_INVESTIGATION_MAX_SMS(기본 10 · 대상 자신 제외 · 존 권한 안)
    apm_members:  [{source_id, domain_id, instance_name}]     ≤ NOISE_BIZ_INVESTIGATION_MAX_APM(기본 8)
    truncated: bool, d1_verdict?: R#                           (D1 메모가 있으면만)
  }
  ```
- **고르는 순서**(상한 안 · 결정적): D1 이상 구성원 → 대상과 같은 접두 업무코드·같은 APM 도메인 → 나머지 `(db_id, resource_id)` 순. `inferred` 구성원은 포함하되 `confidence`를 실어 sre_agent가 「추정 구성원」으로 표기.
- **`polestar_link`는 건드리지 않는다** — 그 키는 210 「이 APM 알람의 서버」 의미다(비교 대상과 섞으면 조사가 형제 서버를 알람 서버로 오인).
- **sre_agent 쪽(계약 가산 · D-118 경계 — 받은 사실만)**:
  - S0 `resolve_was_targets`: `meta.biz_service.apm_members`가 있으면 **그 목록을 같은 업무 인스턴스 후보로 먼저** 쓴다(`_business_term` 게이트웨이 해석은 목록이 없을 때만 — 214 구성원과 게이트웨이 해석이 다를 수 있어서다). 상한 `max_compare`·`max_instances` 그대로.
  - 폴스타 비교: `sms_members` 중 D1 이상 구성원(또는 앞에서부터) ≤3대를 비교 지표 수집 대상으로(`polestar_link_targets` 수집 함수를 재사용하되 별도 입력 · 상한 `POLESTAR_LINK_TARGETS_MAX` 공유).
  - 브리핑 선택 키 `biz_service`(서비스명 · 비교한 구성원 · 추정 여부) — 6요소 불변(D-325 ⑨와 같은 방식).
- **D-341 ①과의 관계**: 플레이북 대상 확정은 여전히 결정적이다 — 입력 목록이 하나 늘 뿐 LLM이 고르지 않는다.

### 4.9 D3 — 서비스 심층 조사 (`NOISE_BIZ_DIAGNOSIS_ENABLED`)

- **진입**: 보드 서비스 상세 [서비스 심층 조사](D1 대표가 있을 때만 활성 · 아니면 사유) → `POST /api/v1/biz-services/{id}/investigations`.
- **인가**: 로그인 사용자 + `authorize_host_investigation`(D-335 ⑤ · 기본 `HOST_AUTHZ_MODE=admin_only`)을 **대표 서버에** 적용 · 비교 구성원은 존 권한(`alarm_zones_for_user`) 밖이면 페이로드에서 빼고 건수만 남긴다 · 거부는 403 + 감사(`log_admin_event`가 아니라 기존 조사 감사 경로).
- **상한**(D-335 ⑥ · D-347 ②): 사용자별 진행 1·시간당 3 공유(실제 접수만 셈) · 시간당 전역 예산 1건으로 셈 · **같은 서비스 진행 중이면 기존 조사 id 반환**(키 `biz:{service_id}`) · 대표 서버 단독 조사가 도는 중이어도 거부하지 않도록 sre_agent `_host_key`에 `"biz"` 축(`(db_id, host, "biz")` — 종합 조사 `"episode"` 축과 같은 방식).
- **sre_agent 호출**: `sre_diagnose(question=코드 템플릿, server_name/hostname/db_id=대표, reference_time=D1 최초 이상 시각, lookback_minutes=창, prior_findings=D1 요약)` + **새 선택 인자 `context`**(`{biz_service: §4.8과 같은 모양}`) — 넘기지 않으면 종전과 같다(D-335 ④ 「계약 불변」을 가산 인자로 해석 — Q19). 질문 템플릿에 사용자 자유 텍스트 없음(D-335 ④).
  - 진단 잡이 플레이북을 타는지·HolmesGPT 자유 조사인지에 따라 `context`가 닿는 곳이 다르다 — **W0-14 코드 확인**: 진단 잡 payload에 `meta.biz_service`를 실으면 §4.8 S0·폴스타 비교가 그대로 돌게 하는 것이 목표 · 안 되면 HolmesGPT 지침의 「이미 수집된 판정」 블록에 구성원 목록을 렌더(지침 렌더 변경 = 프롬프트 회귀 대상).
- **저장·표시**: `InvestigationStore` `kind=diagnosis` + 선택 필드 `subject={type: "biz_service", service_id, display_name}`(D-335 ⑦ `subject` 재사용) · 조회는 소유자 또는 관리자(D-335 ⑦) · 결과는 기존 진단 뷰어 `?investigation=<id>`로 연다(D-352 3단 표시 그대로) · 보드 상세 「진단 이력」에 그 서비스 조사 목록(최근 10).
- **자동 서비스 조사는 하지 않는다**(D-347 ① 수동 전용 기본 · Q20).

## 5. 화면 — P1 「업무 서비스 정의」 · P2 「업무 현황」 (개정 4)

### 5.0 현행 화면 구조 실측과 배치 원칙 (2026-11-05)

| 현행 | 위치 | 이 계획에 주는 제약 |
|---|---|---|
| 포털 머리글 링크 — 노이즈 관제(새 탭 · 포털 탭 플래그 on이면 빠짐) · 매뉴얼 메뉴 · Admin(새 탭) | `src/static/index.html:72-88` | 머리글 버튼을 늘리지 않는다(1440px 줄바꿈 — plans/116 G-5) → P1 진입은 **매뉴얼 옆 메뉴 1줄**로 |
| 포털 뷰 탭 — 질의응답 · 이벤트 알람 · 노이즈 관제(`plans/193` · 기능 조회 `noise_console_portal_tab`) · 장애 진단(사전 게이트 `/active`) · 조사 대화 | `src/static/index.html:99-121` · `app.js` `setActiveView`·`openInvestigationEntry`(`:5787`) | P2는 **같은 방식의 뷰 탭**(기능 조회 키 · 탭 전환 · 비활성이면 멈춤) |
| 노이즈 관제 이식 방식 — 마크업 한 벌(`noise.html`)을 받아 붙이고 `NoiseConsole.mount(root, opts)` → `{pause, resume, unmount}` · 주소 `?view=noise` 동기화 | `app.js:5793-5845` · D-245 부기 · `plans/193` K-1 | P2도 `BizMonitor.mount` 모듈 한 벌 · `?view=biz&service=<id>` |
| 새 탭 교훈 — 관제 → 조사 진입마다 새 탭이 쌓였고(192 P6), 진입로가 셋이면 「어디서 열었느냐」로 동작이 갈렸다(193 K-1 B) | `plans/192`·`plans/193` · `docs/18` 2026-10-22 | P2의 알람·조사·질의 진입은 **같은 창 탭 전환만** · 독립 `/biz-monitor` 페이지를 만들지 않는다 |
| 포털을 떠나면 채팅이 사라진다 → Admin·관제는 새 탭(D-248 ①) | D-248 | P1(넓은 편집 작업 화면)은 **새 탭 독립 페이지** |
| 관리자 대시보드 탭 15종(「APM·SMS 연계」·「호스트 연결」 등) | `src/static/admin/dashboard.html:128-144` | 관리자 탭을 새로 만들지 않는다 — P1 링크 1줄만(같은 화면 두 벌 금지 · D-245 「두 벌이면 한쪽만 고쳐진다」) |

**AIOps 서비스 중심 화면의 필수 요소**(업계 공통 구성 — 서비스 카탈로그·서비스 상태 개요·서비스 상세 드릴다운)를 이 저장소 데이터로 옮긴 결과:

| 요소 | 목적 | 이 계획의 데이터 | 화면 |
|---|---|---|---|
| 서비스 카탈로그 | 무엇이 어떤 업무인가 · 누가 담당 · 얼마나 중요 | `biz_service` + `tier`·`owner_org`·`description` | P1 목록 |
| 구성원 작업대 | 자동 제안이 약해도 사람이 빠르게 채운다(찾아 넣기 · 일괄 지정 · 가져오기) | `sms_inventory`·`apm_inventory` 스냅샷 · 후보 | P1 상세·검토함·가져오기 |
| 구성 맵(계층 레인) | 웹 → WAS → DB 어디가 나쁜가를 한눈에 | 구성원 `layer` · 210 연계 선 | P1 미리보기 · P2 상세 |
| 서비스 상태 개요 | 지금 어떤 업무가 나쁜가(중요도 순) | §4.4 상태 · `tier` | P2 타일 |
| 통합 이벤트 타임라인 | 업무 하나의 알람·APM 이벤트·판정·상태 전이·조사·**변경**을 한 시간축에 | 판정 이력 · `biz_service_state_log` · 조사 저장소 · 폴스타 변경 이력(`change_feed`) | P2 상세 |
| 변경 상관 | 「장애 직전 무엇이 바뀌었나」(변경이 최대 원인 — `change_correlation.py` 머리말) | `change_feed`(PG 존만 · DB2 빈 결과 · `event_time` 단위 미확정) | P2 타임라인(옵트인 · Q27) |
| 진단 진입 | 보던 화면에서 바로 조사·질의 | D1·D3 · 기존 장애 진단·질의응답 탭 | P2 진단 패널 |
| 구성원 지표 추이(개정 6) | 「어느 구성원이 언제부터」 — 서비스 상태 뒤의 구성원 단위 근거 | 폴스타 `cmm_metric_stat_h`·`_d` · 제니퍼 `apm_metrics(mode=series)` · 열 때 1회 | P2 상세 「지표」 보기 |
| 관제실 대형 화면 | 벽면 표시 | P2 타일 | 「전체 화면」 버튼(Fullscreen API · 자동 순환 없음) |

### 5.1 P1 「업무 서비스 정의」 — 조회·정의 (독립 페이지 · 새 탭)

`src/static/biz-services.html` + `src/static/js/biz-services.js` · 서버 라우트 `/biz-services`(`NOISE_BIZ_SERVICE_ENABLED` off면 404) · 진입: 포털 매뉴얼 메뉴 옆 「업무 서비스 정의」(기능 조회 `biz_service_enabled`) · 관리자 대시보드 링크 1줄 · P2 서비스 상세의 [정의에서 편집](관리자). **조회는 로그인 사용자(존 필터) · 편집은 관리자**(Q24) — 편집 버튼은 기능 조회 `is_admin`일 때만 그리고, 서버가 `require_admin_user`로 다시 막는다.

| 영역 | 내용 |
|---|---|
| **① 서비스 목록**(카탈로그) | 검색(표시명·서비스 키·구성원 서버명) · 필터(존 · 등급 · 상태 확정/추정/검토 필요/비활성 · 감시 대상 · APM 커버리지 구간) · 열: 표시명 · 등급 · 담당 부서 · SMS 서버 수(존별 칩) · APM 인스턴스 수 · APM 커버리지 · 추정 비율 · 「검토 필요」 배지 · 감시 · 마지막 변경 · 정렬 · CSV 내보내기 · [새 서비스](관리자) |
| **② 서비스 상세·편집** | 기본 정보(표시명 · 설명 · 등급 · 담당 부서 · 감시 대상 · 비활성) · 구성원 탭 3개 — **SMS 서버**(서버명 · hostname · IP · 존/DB · 업무코드 · **계층** · 근거 · 확정/추정 · [확인]·[제외]·[다른 서비스로]) / **APM**(소스 · 도메인 · 인스턴스 · 출처 매핑 · 확정/추정 · 「APM·SMS 연계에서 수정」 링크 — 210 매핑은 여기서 만들지 않는다 · Q12) / **제니퍼 업무**(수동 추가 — `apm_business(mode=list)` 1호출 목록) · **[서버 찾아 추가]**(스냅샷 `sms_inventory` 검색 — 서버명·hostname·IP 부분 일치 · 결과 ≤50 · 관측 DB 조회 0 · 이미 다른 서비스면 「옮기기」로) · **[인스턴스 찾아 추가]**(`apm_inventory` 검색 — 수동 APM 구성원) · 계층 일괄 지정(선택 행) · **구성 맵 미리보기**(계층 레인 · §5.3) · 연계 그래프(§3.4) · 병합·분할 · APM 대조 · 게이트웨이 업무 매핑 사본(§3.7) · **변경 이력**(이 서비스의 감사 기록 before/after · 최근 50 · 관리자) |
| **③ 검토함**(작업 큐 · 배지 = 열린 건수) | 구성원 후보(S4·S5 · `gateway_business` · 개정 5 보조 S8~S12 — **근거 칩**: 「같은 시각 등록 3대」·「같은 대역 10.x.y.0/24」·「같은 구매 계약」·「동시 알람 5회」·「가용성 의존」 · 칩마다 닻 서버 목록 펼침 · 근거 수 순 정렬) · 병합 후보 · 충돌(SMS·APM) · APM 미연계(같은 도메인) · **미소속 서버**(스냅샷에 있는데 어느 서비스에도 없음 · 존·접두 코드로 묶어 보기) — 다중 선택 → [서비스로 지정]·[수락]·[거절] 일괄(한 번에 ≤200 · 동작마다 감사 1행) |
| **④ 자동 계산** | 기준 스캔 시각 · 「스냅샷이 바뀌었습니다」 표지 · [다시 계산] → 차이(신규·변경·삭제 서비스 · 구성원 증감 · `scan_suggests`) → [적용] · 실행 이력(§3.6) · 210 플래그·스냅샷이 없으면 사유 문구 · (개정 5) **신호 표**(존 × 신호: 켜짐/꺼짐 · 순도 · 기여 · 끈 사유 · 보강 읽기 실패) — 관리자가 yaml을 고칠 근거 |
| **⑤ 가져오기·내보내기**(관리자 · Q26) | CSV 템플릿 `service_key,display_name,tier,owner_org,db_id,server_name,hostname,layer` → [미리보기](행마다 일치 결과: 1대 일치 = 추가 / 0대 = 「스냅샷에 없음」 / 2대 이상 = 「고르지 않음」(D-224 ④) / 이미 다른 서비스 = 「옮김」 표시) → [적용](`origin=manual` · `confidence=confirmed`) · 내보내기는 같은 형식 · 파일 ≤1MB · 행 ≤5,000 · UTF-8(BOM 허용) |
| **⑥ 연계 분석** | 전체 지표 · 추이(실행 순) · 미소속 서버·인스턴스 수 · 서비스별 커버리지 순위(낮은 순 — 연계 보강 대상) |

- 상세는 같은 페이지 안 오른쪽 패널(목록 유지) · 주소 `/biz-services?service=<id>&tab=members` 동기화(새로고침·링크 공유 복원).
- 편집 충돌: `version` 불일치 409 → 「다른 사람이 먼저 고쳤습니다 · 다시 불러오기」(덮어쓰기 버튼 없음).
- 저장 전 확인이 필요한 동작(병합·분할·옮기기·일괄 지정·가져오기 적용)은 **미리보기 패널**로 영향(구성원 몇 개가 어디서 어디로)을 보이고 확인 버튼으로 실행 — 브라우저 `confirm()` 대화상자를 쓰지 않는다.

### 5.2 P2 「업무 현황」 — 상황·이벤트 (포털 뷰 탭)

마크업 한 벌 `src/static/biz-monitor.html`(조각) + 모듈 `src/static/js/biz-monitor.js` `BizMonitor.mount(root, opts)` → `{pause, resume, unmount}` · 포털 뷰 탭 「업무 현황」(기능 조회 `biz_monitor_enabled` · 꺼지면 탭·라우트 없음) · 주소 `/?view=biz[&service=<id>]` · 독립 페이지 없음(Q25). **로그인 사용자 · 존 필터**(권한 밖 구성원은 건수만).

(개정 6) **서비스 상세 = 머리(③) + 보기 탭 4개** — 주소 `&tab=overview·metrics·events·diagnosis`(새로고침·링크 공유 복원 · 기본 `overview`) · [넓게 보기]로 상세를 전체 폭으로 펼친다(② 목록 접힘 · 같은 탭 안 · 새 창 0).

| 보기 탭 | 담는 영역 | 조건 |
|---|---|---|
| 개요 | ④ 구성 맵 · ⑤ 구성원 상태 표 · ⑦ 추이 | 늘 |
| **지표**(개정 6) | ⑨ 구성원별 지표 추이(§5.2.1) | `NOISE_BIZ_MONITOR_HISTORY_ENABLED` — off면 탭 없음 |
| 이벤트 | ⑥ 통합 이벤트 타임라인 | 늘 |
| 진단 | ⑧ 진단 패널 | `NOISE_BIZ_DIAGNOSIS_ENABLED` — off면 탭 없음 |

| 영역 | 내용 |
|---|---|
| **① 상단 요약 띠** | 상태별 서비스 수(위험·주의·이상 신호 없음·판정 불가 — 누르면 필터) · 열린 사건 수 · 지표 수집 상태(꺼짐 / 정상 수집 / 일부만 / 조회 실패 · 마지막 성공 HH:MM) · 마지막 갱신 · [전체 화면] |
| **② 서비스 목록** | 타일 ↔ 표 전환(브라우저 기억) · 정렬 = 상태 나쁜 순 → 등급 높은 순 → 이름 · 필터(존 · 등급 · 상태 · 감시 대상만 · 검색) · 타일: 상태 색 + **상태 글자**(색만으로 구분하지 않는다) · 등급 · 열린 사건 수 · 최근 1시간 알람(전달/억제) · APM 응답시간·에러율 · SMS CPU·메모리(「HH시 통계」) · 갱신 시각 · 감시 꺼진 서비스는 알람 칸만 |
| **③ 서비스 상세 — 머리** | 상태 + 판정 근거 1줄(어느 구성원·어떤 값·어떤 규칙) · 등급 · 담당 부서 · 구성원 수 · APM 커버리지 · 지표 시각 · 동선 버튼: [질의응답에서 묻기](같은 창 질의응답 탭 입력창에 서버 목록을 넣은 질문을 **채우기만** — 전송하지 않는다 · 질의 이력 원칙과 같다) · [정의에서 편집](관리자 · P1 새 탭) |
| **④ 구성 맵** | 계층 레인(웹 → WAS → DB → 배치 → 기타/미지정) · 구성원 칩(상태 색+글자) · APM 인스턴스 ↔ 서버 선(210 연계 · 추정 선은 점선 · 끊김 표시) · 칩 클릭 → ⑤의 그 행으로 이동·강조 · 구성원 >60이면 레인별 「외 N」 접힘 · 결정적 배치(정렬 키 고정 · SVG를 코드가 그림 · 외부 라이브러리 없음) |
| **⑤ 구성원 상태 표** | SMS(서버 · 존 · 계층 · CPU/메모리/파일시스템 · 열린 알람 · 마지막 알람 시각) / APM(인스턴스 · 이어진 서버 · 응답시간 · TPS · 에러율 · 액티브 · 신호) · 정렬 상태 나쁜 순 · 행 [이 구성원 알람 조사](기존 알람 조사 동선) |
| **⑥ 통합 이벤트 타임라인** | 기간 1h/6h/24h(기본 1h) · 소스 칩(폴스타 알람 · 제니퍼 이벤트 · 노이즈 판정 전달/억제/강등 · 서비스 상태 전이 · 조사 시작/완료 · 변경 이력) · 필터(소스 · 심각도 · 구성원 · 「억제 포함」은 기본 끔) · 같은 분의 같은 구성원·같은 알람은 1행 + 「×N」 · 행: 시각 · 소스 · 구성원 · 요약 · 심각도·판정 칩 · 행 동작: 알람 → **같은 창 「노이즈 관제」 탭 서랍**(`?view=noise&alarm=<id>` — 관제 모듈 mount 옵션 `openAlarm` 1개 가산) · 조사 → 장애 진단 탭(`openInvestigationEntry`) · 변경 → 행 펼침(설명) · 행 ≤500(넘으면 「최근 500건만」) · 「장애 직전 변경」 표지: 이상 시작 전 `NOISE_BIZ_MONITOR_CHANGE_LOOKBACK_MINUTES`(기본 60) 안 변경은 머리에 고정(상관 ≠ 인과 문구) |
| **⑦ 추이** | 24시간 상태 띠(상태 전이 기록) · 핵심 지표 스파크라인(Redis 시계열 · APM 응답시간·에러율 / SMS CPU) · 감시 꺼짐이면 상태 띠만 |
| **⑧ 진단 패널**(`NOISE_BIZ_DIAGNOSIS_ENABLED`) | D1 판독 1줄(R# 문구 + 「가설 · 참고용」) · 이상 구성원(최초 이상 순) · 결손(연계 없음·지표 없음·권한 밖 N) · [서비스 심층 조사](D3 · 대표 서버 표시 · 비활성이면 사유) · 「진단 이력」(최근 10 → 장애 진단 탭 같은 창) |

- **갱신**: 30초 폴링(SSE 신설 없음) · 탭이 비활성·브라우저 탭 숨김이면 `pause()` — 폴링·heartbeat를 멈춰 M3 수요가 사라진다(§4.3 「보는 사람 없으면 수집 0」과 맞물림) · 상세가 열려 있으면 상세만 30초 · 타임라인은 수동 [새로고침] + 60초.
- **알람 탭 배지와의 관계**: 업무 현황 탭에는 배지를 두지 않는다(알람 배지는 「이벤트 알람」 탭 하나 — 같은 알람을 두 배지가 세지 않게).

### 5.2.1 ⑨ 「지표」 보기 — 구성원별 지표 추이 (개정 6 · `NOISE_BIZ_MONITOR_HISTORY_ENABLED`)

**목적**: 서비스 상태(최악값 한 줄) 뒤에서 **어느 구성원이 언제부터** 나빠졌는지를 이벤트와 같은 시간축에서 본다. 그때그때 묻는 분석은 [질의응답에서 묻기]가 맡고, 이 보기는 정형 보기만 둔다(같은 기능 두 벌 금지).

| 기간 | SMS(폴스타) | APM(제니퍼) |
|---|---|---|
| 1h | 시간 통계 최근 2점(「정각 통계 — 1시간 창은 점이 적다」 표기) | `interval_minute` 1 · 60점 |
| 6h | 시간 통계 6점 | 5 · 72점 |
| **24h**(기본) | 시간 통계 24점 | 서버 허용값 중 15에 가장 가까운 값(W0-25) |
| 7d | **일 통계** `cmm_metric_stat_d` 7점(시간 통계 7일은 행 상한을 넘는다 — 아래 상한) | **미지원(v1)** — 「APM은 24시간까지」 표기(Q36) |

**화면**
- **계층 레인별 그래프**: 웹 → WAS → DB → 배치 → 기타/미지정 순 섹션 · 섹션마다 그 레인에 있는 종류의 지표만(SMS CPU·메모리·파일시스템 / APM 응답시간·TPS·에러율 · 제니퍼 업무 구성원은 업무 시계열) · **구성원마다 선 1개**(그래프당 ≤8 · 넘으면 「외 N — [구성원 고르기]」).
- **조회 구성원 선택**(결정적): 상태 나쁜 순 → 최근 1시간 알람 수 → 이름 · 서비스 전체에서 ≤`NOISE_BIZ_MONITOR_HISTORY_MAX_MEMBERS`(20) · [구성원 고르기]로 바꿀 수 있으나 상한은 같다 · 「조회 N대 / 구성원 M대」 표기.
- **이벤트 표지**: 알람(심각도 글자) · 상태 전이 · 조사 시작 · (옵트인) 변경 이력을 그래프 시간축에 세로 표지로 — ⑥과 **같은 이벤트 API 결과**를 쓴다(추가 조회 0 · 표지 ≤200 · 같은 칸은 「×N」). 표지를 누르면 이벤트 탭이 그 시각으로 열린다(`&tab=events&at=`).
- **시간 커서 공유**: 한 그래프에 마우스를 올리면 탭 안 모든 그래프에 같은 시각 세로선 + 값 말풍선.
- **구성원 드릴다운 서랍**: 선·범례를 누르면 오른쪽 서랍 — 그 서버의 SMS 3지표 + 210 연계로 이어진 APM 인스턴스 지표(추정 연계는 점선) · 열린 알람 · [이 구성원 알람 조사](기존 동선) · [질의응답에서 묻기](채우기만). 조회 20대 밖 구성원을 열면 그 1대만 추가 조회(같은 상한·메모).
- **어제 같은 시각 비교**: 토글(기본 끔 · 1h/6h/24h만) — 켜면 같은 조회를 하루 앞 창으로 1회 더(옅은 선 · 「어제」 범례).
- **임계선**: APM은 **긋지 않는다**(본체 APM 임계 0 — D-335 ①) · SMS는 `sms_thresholds`가 있을 때만 `warn`·`crit` 점선(「설정 임계」 범례).
- **신선도·결손**: 그래프마다 「SMS HH시 통계까지 · APM HH:MM까지」 · 값이 없는 칸은 **선을 끊는다**(보간 0) · 소스 실패는 그 그래프 자리에 사유(「제니퍼 조회 실패 — 다시 시도」) · 권한 밖 구성원은 선 없이 「권한 밖 N대」.
- **렌더**: 포털이 이미 싣는 Chart.js(`src/static/vendor/chartjs/chart.umd.min.js` · `index.html:459`)와 `result-viz.js`의 테마 팔레트 방식을 따른다 · 라이브러리가 없거나 그리기 실패면 **표로 보기**로 대체(사유 문구) · [표로 보기] 전환은 늘 있다(접근성) · 값·이름은 `textContent`.
- **갱신**: 1h는 60초 자동 · 그 밖은 수동 [새로고침] · 탭 전환·`pause()`면 요청 0.

**조회**(`noise_gate/application/biz_monitor_history.py` · M3 수집기와 별개 · 저장 0)
- 진입: `GET /api/v1/biz-services/monitor/{id}/metrics?window=&members=&compare=` — `members`는 **그 서비스의 활성 구성원 id만**(아니면 400 · 임의 `resource_id`로 다른 서버를 읽지 못하게).
- SMS: 존(db_id)마다 **고정 SELECT 1회**(`biz_service_sql.py` 이력 빌더 · 24h 이하는 `cmm_metric_stat_h` · 7d는 `cmm_metric_stat_d` · `stat_date` 범위 + `resource_id IN`(≤20 정수) · 지표 3종 · DB2 `POLESTAR`·`FETCH FIRST` / PG `LIMIT` · `sql_guard` · readonly MCP · SMS 동시 1은 M3과 같은 제한기를 나눠 쓴다). 행 상한: 24h = 24×20×3 = **1,440** · 7d = 7×20×3 = **420**(시간 통계 7일이면 168×20×3 = 10,080 — MCP `max_rows` 10,000 초과라 일 통계로 읽는다).
- APM: 소스마다 `apm_metrics(mode="series", scope="instance", targets=[…≤20], lookback_minutes, interval_minute, owner="biz_monitor")` 1호출(배치는 항목마다 코어 1회 — §2 정정 · D-347 소스당 동시·D-356 ⑤ 공정 분배 그대로) · 제니퍼 업무 구성원은 `scope="business"` 도메인당 1호출.
- 메모: Redis `bizmon:hist:{서비스·기간·구성원 해시·비교}` `EX 60` — 여러 사람이 같은 화면을 봐도 원천 조회 1회.
- 상한: 전체 동시 `NOISE_BIZ_MONITOR_HISTORY_MAX_CONCURRENCY`(2) · 사용자당 진행 중 1 — 넘으면 429 + 「다른 지표 조회가 진행 중입니다」 · 요청 시간 예산 20초 — 넘은 소스는 빼고 부분 결과 + 사유(침묵 금지).
- 감시 대상(`monitored`)이 아닌 서비스도 볼 수 있다 — 열 때 1회라 상시 부하가 없다(Q37). M3 수요 키(`bizmon:viewers`)는 건드리지 않는다.

### 5.3 두 화면 공통 규칙

- 렌더 `textContent`만(`innerHTML` 0) · 상태는 색 + 글자 · 테마 토큰(`style.css` 라이트/다크) · 1280px에서 줄바꿈 없음 · 표는 가로 스크롤.
- 상태 어휘는 §4.4 하나(위험·주의·이상 신호 없음·판정 불가) · 확신도 배지는 확정/추정 둘 · 「가설 · 참고용」은 D1·D3 결과에만.
- 빈 상태 문구: 서비스 0개(「자동 계산 또는 가져오기로 서비스를 만드세요」 · 관리자에게만 버튼) · 210 스냅샷 없음(사유) · 지표 수집 꺼짐(「지표 수집 꺼짐 — 알람 현황만」).
- 존 권한 밖은 이름을 보이지 않고 건수만 · CSV 내보내기도 같은 필터.
- 화면 이름은 매뉴얼과 같게(D-255) — 「업무 서비스 정의」·「업무 현황」.

## 6. API

| 영역 | prefix | 인가 |
|---|---|---|
| 관리 | `/api/v1/admin/biz-services` — 목록·상세·수동 생성·수정·구성원 추가/제외/옮기기·병합·분할·확인/취소·후보 수락/거절·`monitored` 토글·`preview`·`apply`·`runs`·`analysis` | `require_admin_user` + role 재검증 · 전 동작 `log_admin_event` before/after(거부 포함 · 예외 문자열 미포함) |
| 보기 | `/api/v1/biz-services/monitor` — 요약(타일) · `/{id}` 상세 · `/{id}/timeline` · `POST /heartbeat` | 로그인 사용자(JWT `type`·role 명시 검증) · 존 필터 · 열람 감사는 기존 감사 미들웨어 |
| 진단(개정 3) | `/api/v1/biz-services/{id}/diagnosis`(D1 스냅샷 · GET) · `POST /{id}/investigations`(D3 접수) · `GET /{id}/investigations`(진단 이력) | D1 = 로그인 사용자 + 존 필터 · D3 = 위 + `authorize_host_investigation`(대표) · 사용자 상한 · 접수 거부는 `reason_text`(D-347 ⑤) |
| 관리 추가(개정 3) | `POST /api/v1/admin/biz-services/{id}/apm-compare`(게이트웨이 업무명 해석 1회) · `GET /{id}/gateway-business-map`(YAML 사본) | 관리자 · 감사 |
| P1 조회(개정 4) | `GET /api/v1/biz-services`(목록 · 검색·필터·페이지 ≤100) · `GET /{id}`(상세 · 구성원) · `GET /export.csv` | 로그인 사용자 · 존 필터(권한 밖 구성원 = 건수) |
| P1 편집(개정 4) | `GET /api/v1/admin/biz-services/inventory/sms?q=` · `…/inventory/apm?q=`(스냅샷 검색 · `q` 2~64자 · 결과 ≤50 · LIKE 이스케이프 · 관측 DB 0) · `POST /bulk-assign`(서버 ≤200 → 서비스) · `POST /candidates/bulk`(수락·거절 ≤200) · `PATCH /{id}/members/layer`(일괄 계층) · `POST /import/preview`·`/import/apply {preview_id}`(미리보기 뒤 스냅샷·대상 서비스 `version`이 바뀌면 409) · `GET /{id}/history`(감사 기록 최근 50) | 관리자 · 전 동작 감사(일괄은 행마다 1줄 + 묶음 id) |
| P2 현황(개정 4) | `GET /api/v1/biz-services/monitor/{id}/events?window=1h·6h·24h&sources=…`(통합 타임라인 · 행 ≤500 · 10초 메모) · `GET /monitor/{id}/topology`(구성 맵 — 구성원·계층·210 선) | 로그인 사용자 · 존 필터 · 「억제 포함」은 관제와 같은 규칙(사용자 표시 레벨 — 관리자만 억제 포함 · D-245 사용자 모드 범위) |
| P2 지표(개정 6) | `GET /api/v1/biz-services/monitor/{id}/metrics?window=1h·6h·24h·7d&members=<구성원 id ≤20>&compare=yesterday`(§5.2.1 · 60초 메모 · 동시 상한 429 · 부분 결과 사유) | 로그인 사용자 · 존 필터(권한 밖 구성원 = 건수 · 선 없음) · `members`는 그 서비스 활성 구성원만(400) · 플래그 off면 404 |
| 기능 조회(개정 4) | 기존 `GET /api/v1/alarm/capabilities`에 `biz_service_enabled`·`biz_monitor_enabled`·`biz_diagnosis_enabled`·(개정 6) `biz_history_enabled` 불리언 가산 | 종전 키 불변 · 가산만 |

입력 검증: `db_id`는 레지스트리 등록값 · `resource_id ^\d{1,19}$` · `source_id ^[a-z][a-z0-9_]{0,15}$` · `domain_id ^\d{1,19}$`(D-362 부기 1 ⑮와 같은 형식).

## 7. 설정 (`src/config/alarm.py` · env `NOISE_` · 전부 기본 off/현행)

| 키 | 기본 | 설명 |
|---|---|---|
| `NOISE_BIZ_SERVICE_ENABLED` | `false` | 연계 계산·저장 · P1 「업무 서비스 정의」 페이지(M1 포함) — off면 DDL·라우트·메뉴 0 |
| `NOISE_BIZ_SERVICE_MAX_MEMBERS` | `200` | 서비스당 SMS 구성원 상한 |
| `NOISE_BIZ_MONITOR_ENABLED` | `false` | P2 「업무 현황」 탭·M2(위 플래그도 on이어야 함) |
| `NOISE_BIZ_MONITOR_METRICS_ENABLED` | `false` | M3 수집기 — off면 지표 칸 「지표 수집 꺼짐」 · 관측 호출 0 |
| `NOISE_BIZ_MONITOR_MAX_SERVICES` | `20` | 감시 서비스 상한 |
| `NOISE_BIZ_MONITOR_MAX_APM_TARGETS` | `100` | 주기당 APM 인스턴스 상한 |
| `NOISE_BIZ_MONITOR_APM_INTERVAL_SECONDS` | `60` | ≥30 검증 |
| `NOISE_BIZ_MONITOR_SMS_INTERVAL_SECONDS` | `600` | ≥300 검증 |
| `NOISE_BIZ_MONITOR_ALARM_WINDOW_MINUTES` | `60` | M2 창 |
| `NOISE_BIZ_DIAGNOSIS_ENABLED` | `false` | D1 진단 패널·D3 서비스 심층 조사(`NOISE_BIZ_MONITOR_ENABLED`도 on이어야 함 · D3는 조사 가능 신호 `investigation_enabled`도 필요) |
| `NOISE_BIZ_DIAGNOSIS_MAX_APM_TARGETS` | `50` | D1 즉석 조회 APM 인스턴스 상한 |
| `NOISE_BIZ_DIAGNOSIS_WINDOW_MINUTES` | `15` | D1 동시성·최초 이상 창 |
| `NOISE_BIZ_INVESTIGATION_CONTEXT_ENABLED` | `false` | D2 조사 페이로드 `meta.biz_service` 가산(off = 페이로드 바이트 동일 · `NOISE_BIZ_SERVICE_ENABLED` 필요) |
| `NOISE_BIZ_INVESTIGATION_MAX_SMS` · `…_MAX_APM` | `10` · `8` | D2·D3 비교 구성원 상한(sre_agent 상한 `POLESTAR_LINK_TARGETS_MAX` 10 · `max_compare` 8과 맞춤) |
| `NOISE_BIZ_MONITOR_CHANGES_ENABLED` | `false` | (개정 4) P2 타임라인 변경 이력 — 상세를 열 때 존별 고정 SELECT 1회(`change_feed` · 구성원 `resource_id` ≤500 · 60초 메모 · PG 존만 · DB2 존은 「변경 이력 미지원」 표기) · W0-16(`event_time` 단위) 확인 전에는 켜지 않는다 |
| `NOISE_BIZ_MONITOR_CHANGE_LOOKBACK_MINUTES` | `60` | 「장애 직전 변경」 창 |
| `NOISE_BIZ_MONITOR_HISTORY_ENABLED` | `false` | (개정 6) P2 상세 「지표」 보기(§5.2.1) — off면 탭·`/metrics` 라우트·원천 조회 0(`NOISE_BIZ_MONITOR_ENABLED`도 on이어야 함 · M3 플래그 `…_METRICS_ENABLED`와 독립) |
| `NOISE_BIZ_MONITOR_HISTORY_MAX_MEMBERS` | `20` | 지표 보기 조회 구성원 상한(1~20 검증 — 20을 넘기면 SMS 행 상한 계산이 깨진다) |
| `NOISE_BIZ_MONITOR_HISTORY_MAX_CONCURRENCY` | `2` | 지표 보기 전체 동시 조회 상한(사용자당은 1 고정) |

- sre_agent 쪽은 새 플래그 없이 **키가 있을 때만** 동작한다(`meta.biz_service`·`context`가 없으면 종전 경로 — 생산자 플래그 off면 비트 동일).

- 명명 규칙(S6)·SMS 임계는 env가 아니라 `config/knowledge/biz_service.yaml` — 폐쇄망 실측으로 바꾸는 지식 값. (개정 5) S7·보조 신호 S8~S12 설정도 같은 파일(§3.2.3) — **새 env 플래그 없음**(모두 `NOISE_BIZ_SERVICE_ENABLED` 아래 · off면 보강 읽기 0).
- 의존: 스냅샷 출처 `NOISE_APM_HOST_MAP_ENABLED`(210) off면 탭은 「사전 스캔 비활성」 사유만(Q5). 축 ③은 210 매핑이 있을 때만.
- 플래그는 기동 시 1회 해석.

## 8. 정상 동작 가능성 검토

| # | 가정 | 판정 | 근거·대책 |
|---|---|---|---|
| F1 | 등록명 `호스트(업무명)` 꼬리가 충분하다 | 미확인 | D-363 부기 1 「괄호를 쓴다」 · 비율 W0-1 |
| F2 | 업무명 표기가 일치한다 | 미확인 | 공백 정규화 · 약어는 S5 병합 후보 · W0-2 |
| F3 | 호스트 이름 계열 규칙 | 미확인 | S4 후보 전용이라 오연결 0 · W0-3 |
| F4 | ITAM 추정이 쓸 만하다 | 조건부 | D-362 주의 ② 승계(정답 대조 <70%면 ITAM 규칙 끔) |
| F5 | 연계 계산은 스냅샷만으로 가능 | **확실** | §2 표 |
| F6 | 앞 3글자 업무코드 정책 일관성 | 미확인 | W0-6 · 낮으면 `enabled: false` |
| F7 | 업무 아닌 흔한 접두 배제 | 조건부 | 제외 목록 + 다업무 코드 배제 이중 |
| F8 | 210 연계 매핑이 서비스 APM 구성원을 낼 만큼 쌓였다 | **미확인** | 210 W0(폐쇄망 실측) 미완 · 매핑이 적으면 APM 커버리지가 낮게 나온다 — 그것 자체가 M1 지표로 보인다(숨기지 않음) |
| F9 | `apm_app_health` 배치 호출이 1분 주기 부하를 견딘다 | 조건부 | 인스턴스 100 · 소스당 1호출 · 5분 창 · 호출자 분리 · W0-10에서 1회 소요를 재고 주기·상한을 정한다 |
| F10 | `cmm_metric_stat_h`가 정각 뒤 언제 채워지는지 | 미확인 | W0-8 · 10분 확인 주기는 지연을 최대 10분 더한다(화면에 통계 시각 표기) |
| F11 | D1 교차 계층 판독(R3·R4)이 의미 있다 | 조건부 | 210 연계(축 ②)가 있어야 APM ↔ 서버 대응이 선다 — 연계 없는 인스턴스는 R5로 결손을 드러낸다(F8과 같은 원인) |
| F12 | SMS 지표가 시간 통계라 D1 「같은 창」 비교가 성립한다 | 조건부 | APM은 분 단위·SMS는 정각 단위 — R3의 SMS 쪽은 **알람(분 단위)을 먼저** 보고 시간 통계는 보조(「HH시 통계」 표기) · 창 15분 안에 정각 통계가 없으면 R5 |
| F13 | 진단 잡에 실은 서비스 맥락이 sre_agent 조사에 닿는다 | **미확인** | 진단 잡이 플레이북 경로를 타는지 W0-14 코드 확인 · 안 닿으면 지침 렌더 경로(§4.9) |
| F14 | 업무 계층(웹·WAS·DB) 구분값이 있다 | 미확인 | R2 「다른 계층 동시」는 계층 칸이 있을 때만 — 없으면 비율 조건만(W0-12) |
| F15 | 서비스 구성원이 존을 넘는다 | 조건부 | 서비스는 존으로 나누지 않는다(Q3) — 조사 대표는 사용자 존 안에서만 고르고, 존 밖 구성원은 건수만(W0-13 비율) |
| F16 | (개정 5) 운영 자원 트리 폴더가 업무 단위다 | 조건부 | 실례 1건(plans/72 Q4)·실캡처 같은 부모(§3.2.4 ①) · 존마다 다를 수 있다 → S1 대응·다업무 배제·순도 보정 · W0-19 |
| F17 | 은행존(DB2)에 `id_ancestry`·`ctime`·가용성 의존 칼럼이 있다 | 미확인 | 샌드박스 DDL은 PG · b0 프로필은 `parent_resource_id`만 언급 → W0-19 · 없으면 그 존 S7·S8·S12 끔(사유) |
| F18 | `ctime` 단위·의미(등록 시각) | 조건부 | 실캡처 13자리 ms epoch(2019-11-27)로 해석됨 · W0-20 |
| F19 | IP 대역이 업무를 따른다 | **의심** | 샌드박스는 계층을 따랐다(§3.2.4 ③) → 순도 보정이 끄는 것을 기본 시나리오로 본다 |
| F20 | ITAM 계약 칸 물리 이름·채움률 | 미확인 | 전사본만 있음(실 DB 대조 미실시 · G-4) → yaml off · W0-22 |
| F21 | 보조 신호 후보가 검토함을 넘치게 하지 않는다 | 조건부 | 닻 ≥2 · 한 서비스만 · 순도 · 공용 묶음 배제 · 검토함 근거 수 순 정렬 · 후보 수는 실행 기록에서 신호별로 보인다 |
| F22 | (개정 6) 1h·6h 창에서 SMS 추이가 쓸 만하다 | 조건부 | 시간 통계라 점이 2~6개뿐 — 화면에 「정각 통계」 표기 · 짧은 창의 근거는 APM과 알람 표지가 맡는다 · 실시간 SMS는 Q15(Prometheus) 뒤 |
| F23 | (개정 6) `apm_metrics(series)` 20개 · 24시간 1호출이 쓸 만한 시간에 온다 | 미확인 | 창 길이 상한이 게이트웨이 코드에 없다(`apm_core.window`) · 허용 간격은 서버가 정한다 → W0-25 · 느리면 24h APM을 15분 → 60분 간격으로 낮추거나 6h까지로 줄인다 |
| F24 | (개정 6) `cmm_metric_stat_d`의 어제 통계가 언제 채워지는지 · 지표 칼럼이 `_h`와 같은지 | 미확인 | W0-8 확장 · 다르면 7d 그래프는 「일 통계 미확인」으로 끈다 |

## 9. 보안
- 관리 = 관리자만 · 보기 = 로그인 사용자 + 존 필터(권한 밖 구성원은 건수만).
- 관측 DB: 연계 계산의 모집단은 스냅샷 · (개정 5) 보강 읽기(§3.2.4)와 M3 SMS만 고정 SELECT(외부 입력은 정수 id · `sql_guard` · readonly MCP · D-003). ITAM 보강 읽기는 담당자 성명·사번 칸을 SELECT하지 않는다 · 트리 폴더 이름은 `biz_service_attr`에만 두고 화면은 `textContent`.
- 업무명·서버명은 `textContent`만 · SQL에 들어가지 않는다.
- PII: `evidence`에 신호가 맞은 업무명 토큰만 저장 · ITAM 단서 칸·비고 원문 저장 안 함.
- 제니퍼 토큰은 게이트웨이에만(D-274) — 본체는 MCP 계약으로만 부른다.
- **화면(개정 4)**: P1 편집은 화면 숨김이 아니라 서버 `require_admin_user` + role 재검증이 정본 · CSV 내보내기는 **수식 주입 방지**(`=`·`+`·`-`·`@`·탭·CR로 시작하는 칸 앞에 `'`) · 가져오기는 크기·행 상한 · 칸 길이 상한 · `db_id` 레지스트리 값만 · 서버 이름은 SQL에 넣지 않는다(스냅샷 PG 조회는 바인드 인자) · 스냅샷 검색 `q`는 LIKE 이스케이프 · `owner_org`·`description`은 PII 패턴(`pii_filter`·`data_masker` 규칙)에 걸리면 저장 거부 · 타임라인 사용자 경로는 관제 사용자 경로와 같은 마스킹(D-245 부기).
- **지표 보기(개정 6)**: `members`는 그 서비스의 활성 구성원 id 정수만(임의 서버 조회 차단 · 400) · `window`·`compare`는 열거값만 · SQL에는 정수 id와 코드가 만든 `stat_date` 범위만 · 사용자 경로 응답은 관제 사용자 경로와 같은 마스킹 · 권한 밖 구성원은 값·이름 없이 건수만.
- **진단(개정 3)**: 「조회 권한 ≠ 조사 권한」(D-170 R-9) — D1 보기는 존 필터, D3 접수는 대표 서버에 `authorize_host_investigation`. 조사 페이로드의 비교 구성원도 요청자 존 안만(권한 밖 서버 이름이 조사 결과로 새지 않게) · 자동 경로(D2)는 알람 존 안의 구성원만. `meta.biz_service`·`context`에는 식별자만 싣고 업무명은 표시명 1개(텍스트 마스킹 대상 경로 그대로).

## 10. 결정 충돌 검토 (CLAUDE.md 작업 전 필수)

| 결정 | 관계 | 판정 |
|---|---|---|
| D-348 ② · D-363 ① (유사·LIKE는 후보 전용) | S4·S5 후보 전용 | 충돌 없음 |
| D-363 ① ↔ S6 | 명명 정책 키의 정확 일치 · SQL 미사용 · 늘 `inferred` | 충돌 없음으로 판단 — D-367에 「S6은 D-363 ① 대상 아님」 명시 · 이견이면 Q9 `candidate` |
| D-362 (210 연계) | 읽기만 · 판정 순서·쓰기 규칙 불변 · 210 매핑은 만들지 않는다 | 정합 |
| D-362 ⑦ (스캔은 수동만) | 계산도 수동 · 연쇄 없음 | 정합 |
| D-224 ④ | ITAM·호스트 색인 1대 일치만 · 충돌은 잇지 않음 | 정합 |
| D-347 · D-356 ⑤ (동시 상한·호출자 공정) | M3은 상한 안 · `owner=biz_monitor` · 동시 1 | 정합 |
| D-049 (워커 PG 쓰기 금지) | 쓰기·수집기 모두 API 프로세스 · 워커 변경 0 | 정합 |
| D-089 (DB별 SQL은 `src/db_adapters/`) | M3 SMS SQL을 `noise_gate/infrastructure/`에 둔다 — 210 스캐너(`apm_host_map_sql_scanners.py`)와 같은 선례 | **선례 따름 · 이견이면 Q로** — 착수 전 확인 |
| D-139 (패키지 경계) | 도메인·저장소·계산·수집기 `noise_gate/` · 본체는 라우트·화면·기동 배선 | 정합 |
| 노이즈 게이트 통보 단일 경로 | M3은 통보하지 않음(Q16) | 정합 |
| D-354 (`duty_areas`) | 용어 분리 — 「업무 서비스」 | 충돌 없음 |
| D-290 ② · D-274 ⑤ (업무명 해석은 게이트웨이 수동 매핑 우선 · 정합은 게이트웨이 한 곳) | `business_map`을 읽지도 쓰지도 않음 · 사본 YAML은 관리자가 손으로 옮김 · 채팅 해석 순서 불변 | 정합 — 단 **두 수동 정본이 생길 수 있다**(Q17) |
| D-310 (업무 도구) | `apm_business`는 수동 구성원의 지표 조회로만 | 정합 |
| D-317 ④ (사건 = 존·같은 호스트) | 서비스 묶음은 D1 표시용 · 사건 키·억제 불변 | 정합 |
| D-335 ① ② (판정은 게이트웨이 · 「정상」 금지) | §4.4 정정 — 본체 APM 임계 0 · 「이상 신호 없음」 | 정합(개정 3에서 고침) |
| D-335 ④ (원인 조사 = `sre_diagnose` 위임 · 계약 불변) | D3가 선택 인자 `context`를 더한다 | **해석 필요 — Q19**(가산 인자는 기존 호출자에게 불변이라 「계약 불변」 안으로 본다) |
| D-335 ⑤ ⑥ · D-347 ② (조사 권한 · 사용자·전역 상한 · 같은 호스트 키) | 대표 서버 인가 · 상한 공유 · `"biz"` 축 + 서비스 키 | 정합 |
| D-347 ① (수동 전용 기본) | D3 수동만 · D2는 기존 조사가 나갈 때만 가산 | 정합 |
| D-341 ① (플레이북 결정적 대상) | D2 입력 목록 추가 · 고르는 것은 코드 | 정합 |
| D-350 ④ (이웃은 확정 연결만) | R2 토폴로지 안내는 확정 연결만 · 서비스 구성원은 「이웃」이 아니라 별도 키 | 정합 |
| D-352 ② (원인 영역 enum 정본은 sre_agent) | D1은 원인 영역을 새로 판정하지 않고 R# 판독 문구만 · 구성원 조사의 원인 영역은 저장된 값을 보인다 | 정합 |
| D-118 (sre_agent 경계) | 받은 사실만 · 식별자 재해소 없음 | 정합 |
| D-245 (관제 마크업·JS 두 벌 복제 기각 · 사용자 모드 읽기 범위) | P2는 마크업 한 벌 + mount 모듈 · 알람 상세는 관제 서랍을 다시 만들지 않고 관제 탭으로 넘긴다(mount 옵션 `openAlarm` 가산 — 관제 API·인가 불변) · 관리자 탭 안 만듦(P1 한 벌) | 정합 — 관제 모듈 옵션 1개 가산은 `plans/193`의 `onOpenInvestigation`과 같은 방식 |
| D-248 ① (포털을 떠나는 화면은 새 탭) · `plans/193` K-1 교훈(진입로가 셋이면 동작이 갈림) | P1 = 새 탭 독립 페이지 · P2 = 포털 뷰 탭 하나(독립 페이지 없음) · P2 안 진입은 같은 창 탭 전환 | 정합 |
| D-081 (변경 피드 초안 · `change_feed` graceful) | P2 타임라인이 소비처를 하나 늘림(읽기 전용 고정 SELECT · PG만) · 단위 미확정은 옵트인 플래그로 막음 | 정합 — W0-16 뒤 켬 |
| D-255 (매뉴얼 동반) | 사용자 「업무 현황」 · 관리자 「업무 서비스 정의」 둘 다 W12 | 정합 |
| (개정 5) D-348 ② · D-363 ① (유사는 후보 전용 · 사람이 확정) | 보조 S8~S12는 후보만 · 서비스 생성·자동 구성원 0 · S7은 「정의된 폴더의 소속」이라 유사도가 아니다(S6과 같은 논리 · 늘 `inferred`) | 충돌 없음 — D-367에 「S7은 D-363 ① 대상 아님」 명시 · 이견이면 Q30 대안(S7도 후보 전용) |
| (개정 5) D-362 (210 스캐너·스냅샷 정본) | 보강 읽기는 214 소유 SQL로 따로 돈다 · 210 서버 페이지 SQL·`sms_inventory` 칼럼 불변 | 정합 |
| (개정 5) D-022 (`RESOURCE_CONF_ID` JOIN 금지 — 토폴로지 조회) | 보강 읽기는 `cmm_resource` 단일 표(자기 칼럼 + 폴더 id IN)만 — EAV JOIN 0 | 정합 |
| (개정 5) plans/72 Q4 (자원 트리 구조 지식을 양식 매핑에 쓰면 과적합 — 사용자 동의) | S7은 특정 트리 구조·폴더 이름을 코드에 두지 않는다 — 「가까운 비일반어 폴더」 일반 규칙 + 기존 `generic_terms` + S1 대응·순도로 존마다 스스로 검증 | 정합으로 판단 — 착수 전 확인 권고(Q30) |
| (개정 5) PII 규칙(`pii_filter`·D-170 계열) | ITAM 담당자 개인 칸 미독 · 부점명만 `owner_org` 제안 | 정합 |
| (개정 6) Q25 · D-248 ① · `plans/193` K-1 (현황은 포털 탭 하나 · 진입로가 여럿이면 동작이 갈림) | 상세 모니터링을 독립 화면이 아니라 P2 상세 보기 탭으로 둔다 · [넓게 보기]도 같은 탭 안 | 정합(사용자 B안 지시) |
| (개정 6) Q11 · §16 「지표 장기 보관」 (PG 지표 저장 안 함) | 지표 보기는 원천에서 열 때 읽고 60초 메모만 · 저장 0 | 정합 |
| (개정 6) Q13 (보는 사람이 있을 때만 · 감시 대상만) | 지표 보기는 M3이 아니라 사람이 탭을 연 순간 1회 · 감시 대상이 아니어도 볼 수 있다(상시 부하 0 · 동시 상한) | 정합 — 「감시 대상만」은 상시 수집(M3)의 규칙으로 읽는다(Q37) |
| (개정 6) D-335 ① (본체 APM 임계 0) | APM 그래프에 임계선을 긋지 않는다 · SMS는 설정 임계만 | 정합 |
| (개정 6) D-347 · D-356 ⑤ · MCP `max_rows` 10,000 | APM `owner=biz_monitor` · SMS 동시 1을 M3과 공유 · 행 상한 계산(7d는 일 통계) | 정합 |
| (개정 6) D-245 (한 벌) | 그래프 렌더는 포털의 Chart.js·테마 팔레트 방식 재사용 · 이벤트 표지는 ⑥ API 결과 재사용(조회 두 벌 0) | 정합 |

## 11. W0 폐쇄망 실측 (사용자 · 착수 전 권고)

| # | 확인 | 쓰임 |
|---|---|---|
| W0-1 | 존별 등록명 `(…)` 꼬리 비율 · 꼬리 값 상위 30 | S1 커버리지 · 일반어 |
| W0-2 | 같은 업무 꼬리의 표기 변이 예 10 | S5 |
| W0-3 | hostname 명명 규칙 예 20 | S4 |
| W0-4 | `cmm_resource.description` 업무명 유무 · PII 여부 | Q6 |
| W0-5 | 존별 서버 수 | 계산 시간 |
| W0-6 | hostname 앞 3글자 분포 상위 50 · S1 서비스별 최빈 코드 비율 · 다업무 코드 · (개정 5) 존·센터 머리(`cob0-`·`cocm-` 꼴) 유무·형태와 머리 뗀 뒤 3글자 분포 | S6 설정 · `strip_prefix_patterns` |
| W0-7 | ITAM `어플리케이션코드` 형식 | S6 ITAM 대조 |
| W0-8 | `cmm_metric_stat_h`의 CPU·메모리·파일시스템 자원 유형·칼럼값 · 정각 뒤 적재 지연 · 존별 행 수 · (개정 6) `cmm_metric_stat_d` 같은 칼럼 여부·어제 통계 적재 시각 · 20대×24시간 `_h` 조회 1회 소요 | M3 SMS SQL·주기 · 지표 보기 SMS(F24) |
| W0-9 | 운영 CPU·메모리·파일시스템 주의/위험 임계 관행 | `sms_thresholds` |
| W0-10 | `apm_app_health(targets=100)` 1회 소요 · 뷰서버 부하 체감 | APM 주기·상한 |
| W0-11 | 210 W0 진행 상태(연계 매핑 행 수) | F8 |
| W0-12 | 서버 계층(웹·WAS·DB) 구분값 유무 — 폴스타 자원 유형·등록명 규칙·ITAM 칸 · (개정 5) 업무코드 뒤 토큰(`kah`**`app`**`01`·`bnn`**`dbp`**`01` 꼴)과 IP 대역(S9 순도가 낮게 나오면 대역이 계층을 따르는지) | D1 R2(F14) · `layer_tokens` |
| W0-13 | 업무 꼬리 기준 서비스 중 존을 넘는 비율 | F15 |
| W0-14 | (코드 확인 · 외부망 가능) sre_agent 진단 잡(`submit_diagnosis`)이 플레이북 S0·폴스타 비교를 타는지 · 진단 payload에 `meta`를 실을 수 있는지 | D3 `context` 전달 방식(F13) |
| W0-15 | 게이트웨이 `business_map` 운영 항목 수 · 제니퍼 업무 정의 수(`apm_business(mode=list)`) | Q17 · `apm_business` 구성원 쓸모 |
| W0-16 | 폴스타 `cmm_resource_lifecycle_history` 운영 데이터 — `event_time` 단위(초/ms) · 하루 행 수 · 은행존(DB2)에 같은 표가 있는지 | `NOISE_BIZ_MONITOR_CHANGES_ENABLED` 켜기 판단 |
| W0-17 | 운영팀이 가진 업무별 서버 목록(엑셀 등)의 유무·칸 구성(익명 칸 이름만) | P1 가져오기 템플릿 칸(Q26) |
| W0-18 | 업무 중요도 등급 체계(있으면 등급 수·이름) · 담당 부서 표기 관행 | `tier` 단계 수 · `owner_org` 형식 |
| W0-19 | (개정 5) 존별 자원 트리 — 서버 `id_ancestry` 채움률 · 폴더 자원 유형(`management.Group` 여부) · 서버에서 가까운 폴더 이름 상위 30(익명화 불요한 업무 시스템명만 · 사람 이름 폴더가 있으면 건수만) · 폴더당 S1 업무 수 분포 · 은행존(DB2)에 같은 칼럼이 있는지 | S7 켜기 · F16·F17 |
| W0-20 | `ctime` 단위(13자리 ms?)·채움률 · 존별 등록 간격 분포(같은 초·같은 분 묶음 크기) · 대량 이관일 묶음 크기 | S8 `gap_seconds`·`max_batch` · F18 |
| W0-21 | 대표 IP /24·/25 분포 · S1 서비스당 대역 수 · 대역당 S1 서비스 수 · 공용 대역(관리·백업·DMZ) 목록 | S9 `prefix_len`·`exclude_cidrs` · F19 |
| W0-22 | ITAM 서버 통합 현황 계약 칸 물리 이름·채움률 · 계약 1건당 서버 수 분포 · 계약당 S1 업무 수 | S10 켜기 · F20 |
| W0-23 | 결정 이력 14일 — 분당 알람 서버 수 분포 · 동시 발생 쌍 상위 20의 S1 업무 일치율 | S11 켜기 · `burst_hosts` |
| W0-24 | `avail_depend_resource_id(_2)` 채움률(서버 → 서버 비율) · `acl_manager_group_id`·`importance_id` 값 분포와 화면상 의미 | S12 · 쓰지 않는 단서 재판단 |
| W0-25 | (개정 6) `apm_metrics(mode=series, targets=20개, lookback 60·360·1440분)` 1회 소요·응답 크기 · 서버가 받는 `interval_minute` 허용값 · 뷰서버 부하 체감 | 지표 보기 APM 간격·기간(F23 · Q36) |

점검 SQL·스크립트는 `noise_gate/scripts/probe_biz_service.py`(고정 SELECT · 건수·익명 예시만 반출).

## 12. Wave

| Wave | 범위 | 파일(예정) | 검증 |
|---|---|---|---|
| W0 | 폐쇄망 실측(§11) | `noise_gate/scripts/probe_biz_service.py` | 사용자 실행 · 결과로 설정 값 채움 |
| W1 | 도메인 순수 함수 — S1~S7(개정 5 S7 · S6 머리 떼기) · 합치기 · 보조 신호 전파 `propagate_candidates`(S8~S12 묶음 키 추출 포함) · 신호 순도 보정 · 충돌·상한 · 서비스 키 · 축 ③ 도출 · §3.4 분석 · 차이 계산 · 상태 판정(§4.4) | `noise_gate/domain/biz_service.py` · `noise_gate/application/biz_service_config.py` · `config/knowledge/biz_service.yaml` · `noise_gate/tests/test_plan214_w1_*.py` | 테스트 벡터(§14) · 결정성 · 설정 오류 시 S6 비활성 + 경고 |
| W2 | DDL · 저장소(쓰기 규칙 · version 409) · Redis 색인 · 플래그 · 기동 보장 | `ddl/biz_service.sql` · `noise_gate/infrastructure/biz_service_repository.py` · `biz_service_cache.py` · `src/config/alarm.py` · `src/api/server.py` | 쓰기 규칙 표 전 행 · 플래그 off DDL 0 · **[웨이브 회귀 필요]**(`src/config/`) |
| W3 | 계산 서비스 — 스냅샷·210 매핑 로드 → (개정 5) 보강 읽기(§3.2.4 · 신호별 개별 실패) · 결정 이력 동시 발생 집계(S11 on일 때) → 계산 → 미리보기 → 적용 · 락 · 실행 기록 · `biz_service_attr` | `noise_gate/application/biz_service.py` · `noise_gate/infrastructure/biz_service_sql.py`(보강 SELECT 빌더 — W7이 M3 빌더를 더한다) · `ddl/biz_service.sql`(`biz_service_attr`) | 가짜 스냅샷 종단 · `snapshot_changed`·`scan_running` 409 · 보강 SQL `sql_guard` 통과·PG/DB2 방언·IN ≤500 · 보강 읽기 실패 → 그 신호만 꺼짐 + 사유 · 플래그 off면 보강 SELECT 0 · ITAM SELECT에 개인 칸 0(문자열 단언) |
| W4 | 관리 API · 감사 | `src/api/routes/admin_biz_service.py` · `tests/test_api/test_plan214_w4_*.py` | 인가 · 감사 · 입력 검증 |
| W5 | (개정 4) **P1 「업무 서비스 정의」** — 페이지·라우트 · 목록(검색·필터·CSV 내보내기) · 상세·편집(기본 정보·구성원 3탭·찾아 추가·계층·구성 맵 미리보기·연계 그래프·병합/분할·변경 이력) · 검토함(일괄) · 자동 계산 · 연계 분석 · 진입 링크(포털 메뉴·관리자 대시보드) · P1 조회·편집 API(스냅샷 검색·일괄·계층·이력) | `src/static/biz-services.html` · `src/static/js/biz-services.js` · `src/api/server.py`(라우트 1줄) · `src/api/routes/biz_service.py`(조회) · `src/api/routes/admin_biz_service.py`(편집 가산) · `src/static/index.html`·`app.js`(메뉴 1줄) · `src/static/admin/dashboard.html`(링크 1줄) | 플래그 off 404·메뉴 없음 · 일반 사용자 편집 버튼 없음 + API 403 · 존 필터 · `innerHTML` 0 · `confirm()` 0 · CSV 수식 주입 방지 · 브라우저 1회(가짜 데이터) |
| W5b | (개정 4) P1 가져오기 — CSV 미리보기(일치 판정 4종) → 적용 · 409 · 상한 | `noise_gate/application/biz_service_import.py` · `src/api/routes/admin_biz_service.py` · `src/static/js/biz-services.js` | 일치 판정 벡터(§14) · 2대 이상 = 고르지 않음 · 크기·행 상한 · 적용 감사 |
| W6 | M2 알람 집계 · 보기 API(요약·상세·존 필터) | `noise_gate/application/biz_monitor_alarms.py` · `src/api/routes/biz_monitor.py` | 귀속 순서 단언 · 존 권한 밖 건수만 · 조회 추가 0(가짜 저장소 호출 계측) |
| W7 | M3 수집기 — 리더 잠금 · 수요 기반 · APM 배치 · SMS 고정 SELECT · 예산·백오프 · Redis 시계열 · 상태 전이 기록 | `noise_gate/application/biz_monitor_collector.py` · `noise_gate/infrastructure/biz_service_sql.py` · `ddl/biz_service.sql`(state_log) | 가짜 게이트웨이·가짜 실행기 · 주기당 호출 수 = 소스 수 / 존 수 · 보는 사람 없으면 0 · 같은 `stat_date`면 지표 조회 0 · 실패 사유 노출 · SQL `sql_guard` 통과 |
| W8 | (개정 4) **P2 「업무 현황」 포털 뷰 탭** — 마크업 조각 + `BizMonitor.mount` · 기능 조회 키 · `?view=biz&service=` · 요약 띠 · 타일/표 · 상세 머리·구성 맵·구성원 표·추이 · 질의응답 채우기 · 전체 화면 · pause/resume | `src/static/biz-monitor.html` · `src/static/js/biz-monitor.js` · `src/static/index.html`(탭 1개) · `src/static/js/app.js`(뷰 등록) · `src/api/routes/alarm.py`(기능 조회 가산) · `src/api/routes/biz_monitor.py`(topology) | 30초 폴링·heartbeat · 탭 비활성 = 요청 0 · 「HH시 통계」 · 판정 불가 사유 · 상태 색+글자 · 새 탭 0 · 기능 조회 종전 키 불변 · 브라우저 1회 |
| W8b | (개정 4) P2 통합 이벤트 타임라인 — 판정 이력·상태 전이·조사·(옵트인) 변경 이력 합치기 · 묶음 「×N」 · 「장애 직전 변경」 · 알람 행 → 관제 탭 서랍(`noise.js` mount 옵션 `openAlarm` 가산) | `noise_gate/application/biz_monitor_events.py` · `noise_gate/infrastructure/change_feed.py`(재사용 · 고정 SELECT 조립 추가 시 `biz_service_sql.py`) · `src/api/routes/biz_monitor.py` · `src/static/js/biz-monitor.js` · `src/static/js/noise.js`(옵션 1개) | 소스별 행 단언 · 행 ≤500 · 사용자 경로 마스킹 · 억제 포함 규칙 = 관제 · 변경 플래그 off면 `change_feed` 호출 0 · DB2 존 「미지원」 · 관제 모듈 옵션 없으면 종전 동작 |
| W8c | (개정 6) **P2 상세 보기 탭 4개 + 「지표」 보기**(§5.2.1) — 탭·주소 `&tab=` · [넓게 보기] · 계층 레인 그래프 · 구성원 선택·고르기 · 이벤트 표지(W8b 결과 재사용) · 시간 커서 · 드릴다운 서랍 · 어제 비교 · 표로 보기 대체 · 이력 조회(SMS 고정 SELECT · APM series · 메모 · 동시 상한 · 부분 결과) · 플래그·기능 조회 키 | `noise_gate/application/biz_monitor_history.py` · `noise_gate/infrastructure/biz_service_sql.py`(이력 빌더) · `src/api/routes/biz_monitor.py` · `src/config/alarm.py`(플래그 3) · `src/api/routes/alarm.py`(기능 조회 키) · `src/static/biz-monitor.html` · `src/static/js/biz-monitor.js` · `noise_gate/tests/test_plan214_w8c_*.py` | 플래그 off 탭·라우트 0·관측 호출 0 · 행 상한 단언(24h 1,440 · 7d 일 통계) · `members` 외부 id 400 · 동시 429 · 60초 메모 재요청 호출 0 · APM 임계선 0 · 보간 0 · 탭 이탈 요청 0 · `innerHTML` 0 · 브라우저 1회(가짜 데이터) · **[웨이브 회귀 필요]**(`src/config/`) |
| W9 | D1 서비스 진단 스냅샷 — 판독 R1~R6 · 최초 이상 순 · 대표 선정 · 즉석 조회 상한·메모 · 진단 API · 보드 진단 패널(D3 버튼 제외) | `noise_gate/domain/biz_diagnosis.py` · `noise_gate/application/biz_diagnosis.py` · `src/api/routes/biz_monitor.py` · `src/static/js/biz-monitor.js` · `noise_gate/tests/test_plan214_w9_*.py` | 판독 벡터(§14) · 결정성 · M3 캐시 있으면 관측 호출 0 · 즉석 조회 소스당 1·존당 1 · 「가설 · 참고용」·「정상」 어휘 0 |
| W10 | D2 알람 조사 서비스 맥락 — `meta.biz_service` 가산(트리거·수동·사건 종합) · sre_agent S0 명시 구성원 우선 · 폴스타 비교 · 브리핑 선택 키 | `noise_gate/domain/investigation_payload.py` · `noise_gate/application/nodes/investigation_trigger.py` · `sre_agent/sre_agent/application/playbook_steps.py`·`playbook_runner.py` · `sre_agent/tests/test_plan214_w10_*.py` | 플래그 off 페이로드 바이트 동일 · 상한·존 필터 · `polestar_link` 불변 · 키 없으면 S0 종전 경로 · **[웨이브 회귀 필요]**(조사 페이로드 계약) · sre_agent는 자체 venv |
| W11 | D3 서비스 심층 조사 — 접수 라우트(인가·상한·서비스 키) · `sre_diagnose` `context` 가산 · `_host_key` `"biz"` 축 · 저장 `subject` · 보드 버튼·진단 이력 | `src/api/routes/biz_monitor.py` · `noise_gate/infrastructure/sre_agent_client.py` · `sre_agent/sre_agent/interface/mcp_service.py`·`application/investigation_jobs.py`·`investigation_dispatcher.py` · `src/static/js/biz-monitor.js` | 거부 사유(권한·상한·대표 없음) · 같은 서비스 재접수 = 기존 id · 대표 단독 조사 중에도 접수 · `context` 없으면 sre_agent 비트 동일 · 프롬프트 렌더 회귀(`prompt_render_diff.py` — 지침 경로를 쓰면) |
| W12 | 매뉴얼(D-255) — 관리자 「업무 서비스 정의」(편집·검토함·가져오기·계산) · 사용자 「업무 서비스 정의」(조회) · 「업무 현황」(타일·보기 탭·구성 맵·지표·타임라인·서비스 진단) · 캡처 | `scripts/manual/features.yaml` · `content/admin.md`·`user.md` · `src/static/manual/*` | `pytest tests/test_manual` |

- 회귀: Wave 중간은 자기 테스트 + `regress.py --no-tests --files …` · 계획 끝 1회 `regress.py --base <계획 시작 SHA> --files …`(D-303). sre_agent 변경(W10·W11)은 `sre_agent/.venv`로 그 패키지 테스트를 따로 돌린다.
- 품질 게이트: `arch_check.py --ci`(noise_gate domain은 stdlib + `src.domain.host_names`만) · `overfit_check.py --ci`(`noise_gate/domain` 독스트링에 폴스타 표·칼럼 리터럴 금지 — SQL은 infrastructure에만).
- 실 LLM 0 — MLX·과금 게이트 해당 없음.
- W1~W5(연계·관리) · W6~W8(모니터링) · W9~W11(진단)은 플래그가 나뉘어 앞 묶음만 먼저 배포할 수 있다. W10(D2)은 W2 색인만 있으면 W6~W9 없이도 된다(D1 판독 칸은 비어 나간다).

## 13. 수용 기준
1. 같은 스냅샷 계산 2회 → 차이 0 · 행 순서를 섞어도 동일.
2. S1 업무명이 같은 서버 3대 → 한 서비스 `confirmed` · 업무코드도 꼬리도 없는 서버는 후보.
3. 한 서버가 S2로 업무 A, S1로 업무 B → 잇지 않고 충돌.
4. 관리자 제외·확인·「서비스 아님」·거절 후보 → 재계산이 바꾸지 않음(`scan_suggests`만).
5. 구성원 상한 초과 → `ambiguous` · 자동 확정 0.
6. S6: 꼬리 없는 `ibkweb05`가 코드 대응으로 `inferred` 구성원 · 다업무 코드는 묶지 않음 · `enabled: false`면 S6 기여 0.
7. 축 ③: 210 매핑 `verified` 인스턴스의 대상이 서비스 S 서버면 S의 APM 구성원 `confirmed` · auto면 `inferred` · 대상이 두 서비스로 갈리면 「APM 충돌」.
8. §3.4 분석 지표가 실행 기록에 저장되고 추이 API가 실행 순으로 돌려준다.
9. M2: 알람 `polestar_link` 서버가 서비스 S 소속이면 S에 집계 · 미상은 「서비스 미상」 건수 · 관측 조회 0.
10. M3: 보는 사람 없음 → 호출 0 · 감시 서비스 APM 인스턴스 120개(상한 100) → 소스당 1호출 · 100개만 + 「일부만 감시」 · 같은 `stat_date` → 지표 행 조회 0 · 게이트웨이 실패 → 「조회 실패 · 마지막 성공」.
11. 상태 판정 표(§4.4) 전 행 · 임계 미설정이면 SMS 「판정 불가 · 임계 미설정」.
12. 보드: 존 권한 밖 구성원은 건수만 · 일반 사용자 관리 API 403.
13. 플래그 7개 off → DDL 0 · 라우트 404 · P1 메뉴·P2 탭·지표 보기·진단 패널 숨김 · 수집 태스크 0 · 조사 페이로드·`sre_diagnose` 인자 바이트 동일.
14. D1: 서비스 S의 APM 인스턴스 이상 + 이어진 서버 CPU 알람(같은 창) → R3 · 이어진 서버 이상 없음 → R4 · 연계 없음 → R5 · 판독에 「정상」 어휘 0 · M3 캐시가 있으면 관측 호출 0.
15. D2: 구성원 서버 알람 조사 페이로드에 `meta.biz_service`(대상 제외 · 상한 · 존 안) · `polestar_link` 바이트 불변 · sre_agent S0가 `apm_members`를 같은 업무 후보로 쓰고 `apm_instance_map(business=)`는 부르지 않는다.
16. D3: 대표 서버 조사 권한 없음 → 403 + 사유 · 같은 서비스 진행 중 재요청 → 기존 id · 대표 서버 단독 조사 진행 중이어도 서비스 조사 접수 · 사용자 상한은 실제 접수만 셈.
17. D3 결과가 기존 진단 뷰어에서 3단 표시(D-352)로 열리고 보드 「진단 이력」에 나온다.
18. 관리 「APM 대조」: 게이트웨이 해석에만 있는 인스턴스가 후보(`gateway_business`)로만 뜨고 구성원은 바뀌지 않는다.
19. P1: 일반 사용자 — 목록·상세 조회 가능(존 밖 구성원은 건수) · 편집 버튼 없음 · 편집 API 403. 관리자 — 서버 찾아 추가 → 저장 → 재계산이 그 구성원을 바꾸지 않음(`origin=manual`).
20. P1 검토함: 미소속 서버 3대 선택 → [서비스로 지정] → 3대 구성원 · 감사 3행(같은 묶음 id).
21. P1 가져오기: 1대 일치 = 추가 · 0대 = 「스냅샷에 없음」 · 2대 이상 = 「고르지 않음」 · 다른 서비스 소속 = 「옮김」 표시 · 미리보기 뒤 대상 서비스가 바뀌면 적용 409.
22. P1 CSV 내보내기: `=SUM(1)` 표시명 → `'=SUM(1)`로 나간다.
23. P2: 포털 탭에서 서비스 상세 → 알람 행 → 같은 창 노이즈 관제 탭 서랍이 그 알람으로 열림 · 조사 행 → 같은 창 장애 진단 탭 · 새 브라우저 탭 0.
24. P2: 탭을 떠나면 30초 안에 폴링·heartbeat 0 → `bizmon:viewers` 만료 뒤 M3 수집 0.
25. P2 타임라인: 같은 분 같은 구성원 같은 알람 4건 → 1행 「×4」 · 사용자 기본 화면에 억제 판정 없음 · 변경 플래그 off면 변경 칩 없음·`change_feed` 호출 0.
26. P2 구성 맵: 계층 미지정 구성원은 「미지정」 레인 · 같은 입력이면 같은 배치(정렬 고정).
27. (개정 5) S7: 같은 비일반어 폴더 아래 S1 「카드」 3대 + 꼬리 없는 2대 → 2대가 카드 `inferred`(`tree_group`) · S1 업무 2개가 섞인 폴더 → 그 수준 신호 아님 · `운영` 폴더는 건너뛰고 위 폴더가 쓰인다.
28. 보조 신호: 미배정 서버의 같은 등록 묶음 닻 2대가 모두 서비스 S → 후보 1건(구성원 아님) · 닻이 S·T로 갈림 → 후보 0 · 「공용」 1 · 닻 1대 → 후보 0.
29. 순도 보정: 확정 닻 묶음 10개 중 서비스 1개뿐인 묶음 3개(0.3) → 그 존 S9 꺼짐 + 사유 · 평가 묶음 4개(<5) → 꺼짐 + 「평가 부족」(사유 구분).
30. 보조 신호 후보를 일괄 수락 → 구성원 `origin=manual`·`confirmed` · `evidence.aux`에 근거 보존 · 재계산이 바꾸지 않음.
31. 보강 읽기: 플래그 off → SELECT 0 · 트리 폴더 SELECT 실패 → S7만 꺼짐(나머지 계산 정상 · 사유 노출) · ITAM SELECT 문자열에 담당자 성명·사번 칸 0.
32. S6 머리 떼기: `strip_prefix_patterns: ["^co(b0|cm)-"]`(테스트 설정)일 때 `cob0-kahapp01` → 코드 `kah` · 패턴이 `^` 없이 시작 → S6 비활성 + 경고.
33. (개정 6) P2 상세 보기 탭: 개요·이벤트는 늘 · 지표 탭은 `NOISE_BIZ_MONITOR_HISTORY_ENABLED` off면 없음 + `/metrics` 404 + 원천 조회 0 · 진단 탭은 진단 플래그 off면 없음 · `&tab=metrics` 새로고침 복원 · [넓게 보기]가 새 창을 열지 않음.
34. 지표 24h · 구성원 35대 → 조회 20대(상태 → 알람 수 → 이름 순) · SMS 존별 SELECT 1회 · 행 ≤1,440 · APM 소스별 1호출 · 7d → `cmm_metric_stat_d` · APM 7d → 「APM은 24시간까지」 · 60초 안 같은 요청 → 원천 호출 0.
35. `members`에 그 서비스 구성원이 아닌 id → 400 · 존 권한 밖 구성원 → 선 없음 + 「권한 밖 N대」.
36. 동시 상한 초과 → 429 사유 · 사용자당 진행 중 1 · 시간 예산 초과 소스 → 부분 결과 + 사유 · 지표 탭을 떠나거나 `pause()` → 요청 0.
37. APM 그래프 임계선 0 · SMS 임계선은 `sms_thresholds`가 있을 때만 · 값 없는 칸은 선이 끊김(보간 0) · Chart.js 없음 → 표로 보기 + 사유 · 이벤트 표지는 이벤트 API 결과와 같은 건수(추가 조회 0).

## 14. 테스트 벡터 (W1 정본 — W0 결과로 보강)

| 입력 | 기대 |
|---|---|
| `ibkweb01(인터넷뱅킹)` · `ibkwas01(인터넷뱅킹)` · `ibkdb01 (인터넷 뱅킹)` | 한 서비스(공백 정규화) · S1 |
| `ibkweb02`(꼬리 없음 · S6 끔) | 구성원 아님 · S4 후보 — S6 켜짐이면 아래 S6 첫 행 |
| `srv01(운영)` | 일반어 → 신호 아님 |
| `pay01(결제)`가 APM 도메인으로 인터넷뱅킹 서버와 이어짐 | 잇지 않음 · 충돌 |
| ITAM 단서 `인터넷뱅킹` 서버 `IBK-WEB-03` → 폴스타 `ibkweb03` 1대 | 구성원 · `itam` · `inferred` |
| ITAM 서버 정규형이 폴스타 2대와 일치 | 잇지 않음(D-224 ④) |
| `인터넷뱅킹` vs `인터넷뱅킹시스템` | 병합 후보(S5) |
| S6 — `ibkweb01(인터넷뱅킹)`·`ibkwas01(인터넷뱅킹)` + `ibkdb02` | `ibk` → 인터넷뱅킹(2/2) · `ibkdb02` `host_prefix`·`inferred` |
| S6 — `crdap01`·`crdap02`·`crddb01`(꼬리 없음) | 새 서비스 `prefix:crd` 표시명 `CRD`(ITAM 코드 일치면 업무명) · `inferred` |
| S6 — `crdap09` 1대만 | 서비스 안 만듦 |
| S6 — `kbk`가 인터넷뱅킹·기업뱅킹 두 업무에 | 업무코드 아님 · 꼬리 없는 `kbk*`는 「업무 후보 2개」 |
| S6 — `ibkpay01(결제)` | S1 이김 → 결제 · 근거 「접두 코드 불일치」 |
| S6 — `web-01` · `srv001` · `10.1.2.3` · `ib-web01` · `a1bweb` | 코드 없음 |
| S6 — hostname `ibkweb07` · 등록명 `crdweb07(카드)` | 코드 `ibk`(hostname) · 불일치 기록 · 서비스는 S1 카드 |
| 축 ③ — 210 instance 행 `ibk-was-7f9c-x2k1p` 대상 `ibkwas01`(인터넷뱅킹) · verified | 인터넷뱅킹 APM 구성원 `confirmed` |
| 축 ③ — workload 행 대상 `ibkwas01`(인터넷뱅킹)·`crdwas01`(카드) | 「APM 충돌」 · 구성원 아님 |
| 축 ③ — 같은 도메인 인스턴스 3개 중 2개만 매핑 | 매핑 2개 구성원 · 1개 「같은 도메인 · 미연계」 후보 |
| M2 — 알람 `polestar_link` 없음 · 호스트 `IBKWEB01` | `canonical_host_key` 색인 1대 → 인터넷뱅킹 |
| §4.4 — APM 신호 없음 · 열린 사건 major 1 · SMS CPU 95(crit 90) | 위험(근거: SMS CPU) |
| §4.4 — APM 응답시간 8초 · 신호 없음 · 사건 없음 · SMS 임계 미만 | 이상 신호 없음(응답시간은 표시만 — 본체 임계 0) |
| D1 — 인스턴스 `ibk-was-01` `was_error_burst` · 이어진 `ibkwas01` 같은 창 CPU 알람 | R3 · 대표 `ibkwas01` |
| D1 — 같은 인스턴스 이상 · `ibkwas01` 이상 없음 | R4 · 대표 = `ibkwas01`(이어진 서버 1대) |
| D1 — 인스턴스 이상 · 210 연계 없음 | R5 · 결손 「연계 없음」 · 대표 없음 → D3 비활성 |
| D1 — SMS 구성원 4대 중 3대 같은 창 알람 | R2 · 대표 = 최초 이상 서버 |
| D1 — 이상 서버 2대 동시각 | 대표 = `(db_id, resource_id)` 작은 쪽 |
| D2 — `ibkweb01` 알람 · 서비스 SMS 구성원 15대 · APM 12개 | `sms_members` 10(대상 제외) · `apm_members` 8 · `truncated=true` |
| D2 — 존 A 사용자 수동 조사 · 서비스 구성원 일부 존 B | 존 B 구성원 제외 |
| D2 — 서비스 미소속 서버 알람 | `meta.biz_service` 키 없음(페이로드 종전과 동일) |
| P1 가져오기 — `polestar_cm_gp,IBKWEB01,,web` · 스냅샷에 `ibkweb01` 1대 | 추가(정규형 일치 · `canonical_host_key`) · 계층 web |
| P1 가져오기 — `db_id`가 레지스트리에 없음 | 행 거부 「등록되지 않은 DB」 |
| P1 가져오기 — 같은 서버가 파일 안에서 두 서비스에 | 두 행 모두 「파일 안 충돌」 · 적용 안 함 |
| 계층 자동 제안 — `layer_tokens`가 비어 있음 | 전 구성원 「미지정」(지어내지 않는다) |
| (개정 5) S6 — `cob0-kahapp01`·`cob0-bnndbp01` · `strip_prefix_patterns` 비어 있음 | 코드 `cob`가 여러 업무에 → 업무코드 아님(오묶음 0 · 신호 0) |
| S6 — 같은 입력 · 패턴 `^co(b0|cm)-` | 코드 `kah`·`bnn` |
| S7 — 경로 `루트>센터A>AI 금융상담시스템(KAH)>운영>서버` · S1 없음 · 같은 폴더 3대 | 새 서비스 `tree:ai금융상담시스템(kah)` · `tree_code` KAH · ITAM `KAH` 있으면 키·표시명 ITAM |
| S7 — 폴더 「센터A」 아래 S1 「카드」·「대출」 섞임 · 하위 폴더 없음 | 신호 아님 |
| S7 — `tree_code` KAH · S6 코드 `kah` | 근거 「트리 코드 = 접두 코드」 · 확신도 `inferred` 그대로 |
| S7 — 존 A·B에 같은 폴더명 「인터넷뱅킹」 | 한 서비스 |
| S8 — `ctime` 간격 1.5초 묶음 2대 중 1대 카드 확정 · 1대 미배정 | 닻 1대 → 후보 0(`min_anchors` 2) |
| S8 — 같은 분 등록 30대(`max_batch` 20 초과) | 「대량 등록」 신호 아님 |
| S9 — `10.0.1.0/24`에 웹 서버 10대가 업무 5개 | 공용 → 후보 0 · 이런 대역이 대부분이면 순도 미달 → S9 꺼짐 |
| S9 — 대표 IP 없음 · IPv6 · `exclude_cidrs` 안 | 묶음 키 없음 |
| S11 — 같은 분 알람 서버 40대(`burst_hosts` 10 초과) | 그 분은 세지 않음 |
| S12 — 의존 대상이 ICMP 노드(서버 아님) | 이웃 아님 |
| 여러 보조 신호가 같은 x → S | 후보 1건 · 근거 2개 · 검토함 위쪽 |
| (개정 6) 지표 보기 — 구성원 35대 · 위험 3·주의 5·나머지 · 알람 수 같으면 이름 순 | 조회 20대 = 위험 3 → 주의 5 → 알람 수 → 이름 · 같은 입력이면 같은 선택 |
| (개정 6) 지표 보기 — `window=7d` | SMS SQL이 `cmm_metric_stat_d` · `stat_date` 7일 범위 · APM 호출 0 + 「24시간까지」 |
| (개정 6) 지표 보기 — `window=24h` · 구성원 20대 · 존 2개 | SMS SELECT 2회(존별) · 각 행 상한 ≤1,440 |
| (개정 6) 지표 보기 — `members`에 다른 서비스 구성원 id | 400 · 원천 호출 0 |
| (개정 6) 지표 보기 — SMS 응답에 13시 칸 없음 | 13시에서 선이 끊김 · 12·14시 값으로 채우지 않음 |

## 15. 열린 질문 — 권고안 (이의 없으면 확정)

| # | 질문 | 권고 |
|---|---|---|
| **Q1** (개정 3) | 서비스 정보를 모니터링 밖 어디에 더 쓰나(노이즈 상관 억제 · 장애 조사 범위 · 채팅 「인터넷뱅킹 서버들」 질의) | **이 계획은 연계·관리·모니터링 + 장애 진단(D1~D3)까지.** 장애 진단은 표시·조사 입력 보강이라 잘못 묶여도 알람이 숨지 않는다(추정 구성원은 표기). **노이즈 상관 억제**(서비스 단위 억제·대표 선출)와 **채팅 질의 해석**(Q18)은 후속 — 억제는 잘못 묶이면 알람이 숨고, 채팅은 D-290 해석 순서를 바꾼다 |
| **Q2** | 중 신호(APM 도메인·ITAM·S6·S7)를 자동 구성원으로 넣나 | **자동 + 「추정」 표기** · 강 신호 서비스 1개에만 · 엄격한 소비처는 `confirmed`만 읽는다 |
| **Q3** | 존별로 서비스를 나누나 | **나누지 않는다** — 구성원별 존 표시·필터 |
| **Q4** | 한 SMS 서버가 여러 서비스에 속할 수 있나 | **v1은 1서버 1서비스** · 공용 서버는 충돌로 두고 관리자가 정함 |
| **Q5** | 스냅샷을 210 사전 스캔에서 가져오나 | **재사용** — 모집단은 스냅샷 · 210 플래그 off면 쓸 수 없음(사유 표시) · (개정 5) 트리·등록 시각·의존 보강 읽기는 Q29 |
| **Q6** | 폴스타 비고를 신호로 읽나 | **v1 제외 · W0-4 뒤 결정** |
| **Q7** | 모니터링 보드는 누가 보나 · 어디서 들어가나 | **로그인 사용자 전원(존 필터)** · 진입은 포털 뷰 탭(개정 4 — Q25) · 관리는 관리자만 |
| **Q8** | 스캔 뒤 자동 재계산 | **수동만** · 「다시 계산」 표지 |
| **Q9** | 대응 없는 업무코드만으로 서비스 자동 생성 | **`inferred` 자동 생성** · W0-6 일관성이 낮으면 `candidate` |
| **Q10** | 업무코드를 hostname·등록명 중 어디서 | **hostname 우선** · 다르면 근거에 남김 |
| **Q11** | 지표 이력을 PG에 저장하나 | **아니오** — Redis 24시간 + PG 상태 전이 기록만 · 장기는 원천에 있다 |
| **Q12** | 「같은 도메인 · 미연계」 APM 인스턴스를 이 화면에서 210 매핑으로 만들까 | **만들지 않는다** — 210 관리 탭 링크만(연계 기록자 하나 유지) |
| **Q13** | M3을 항상 돌리나, 보는 사람이 있을 때만 돌리나 | **보는 사람 있을 때만 + 감시 대상만** — 관측 시스템 부하 최소. 상시 감시가 필요하면 그때 상태 전이 기록용 저주기(예: 10분) 상시 모드를 별도 결정 |
| **Q14** (개정 3) | APM 판정 기준 | **게이트웨이 `apm_app_health` 신호 그대로**(위험 2규칙 — 큐잉·오류 급증) · 응답시간·TPS는 표시만 · 본체 APM 임계 0(D-335 ①) — 응답시간 판정은 게이트웨이 `apm_judge_values`(`plans/155`)가 생기면 그때 붙인다 |
| **Q15** | Prometheus 노드 지표(진짜 실시간 SMS 대체)를 붙이나 | **v1 제외** — 폴스타 시간 통계만 · 노드 지표 결합은 앵커·커버리지(`metric_source`) 정합이 필요해 별도 |
| **Q16** | 서비스 상태가 위험이 되면 통보하나 | **안 한다** — 통보는 노이즈 게이트 단일 경로 · 보드 표시만 |
| **Q17** (개정 3) | 게이트웨이 `business_map`(채팅 업무명 수동 해석)과 `biz_service`(구성원 정본) — 수동 정본이 둘이 된다. 어떻게 맞추나 | **`biz_service`가 정본 · `business_map`은 관리자가 옮기는 파생 사본** — P1이 확정 APM 구성원으로 YAML 사본을 만들어 보여 주고(복사), 본체는 게이트웨이 파일을 쓰지 않는다(D-274 ⑤). 자동 동기화·`business_map` 퇴역은 W0-15(운영 항목 수) 뒤 별도 결정 |
| **Q18** (개정 3) | 채팅 업무명 질의(E6 LIKE · 게이트웨이 해석)가 `biz_service`를 먼저 쓰게 하나(새 간선 E8) | **후속 계획** — D-290 ② 해석 순서 개정이라 채팅 회귀(골드셋)가 필요하다. v1은 보드 「질의응답에서 묻기」가 서버 이름 목록을 질문에 넣는 것으로 대신 · 채팅·보드 구성원이 다를 수 있음을 매뉴얼에 적는다 |
| **Q19** (개정 3) | D3에서 `sre_diagnose`에 선택 인자 `context`를 더하는 것이 D-335 ④ 「sre_agent 계약은 바꾸지 않는다」와 충돌하나 | **충돌 아님으로 해석** — 선택 인자 가산이라 기존 호출자·응답은 불변(같은 방식의 `prior_findings` 선례 · plans/187 W10). D-367에 「D-335 ④ 해석 — 가산 인자 허용」을 적는다 · 이견이면 `prior_findings` 안에 구성원 목록을 싣는 대안(계약 변경 0 · 단 판정 요약 칸의 의미가 흐려짐) |
| **Q20** (개정 3) | 서비스 상태가 위험이 되면 서비스 심층 조사를 자동으로 내나 | **안 한다** — D-347 ① 수동 전용 기본 · 서비스 조사는 대상이 넓어 비용이 크다. 구성원 알람의 기존 자동 조사(옵트인)에는 D2 맥락만 붙는다 |
| **Q21** (개정 3) | 채팅 장애 진단(`fault_diagnosis` — 첫 대상 1개)을 여러 대상·서비스 단위로 넓히나 | **이 계획 밖** — 채팅 경로 변경은 질의 회귀 대상이다. 서비스 단위 진단은 보드 D3로 제공하고, 채팅은 Q18과 함께 후속 |
| **Q22** (개정 3) | D2에 `inferred`(추정) 구성원도 비교 대상으로 싣나 | **싣고 표기한다** — 비교 대상은 판정을 바꾸지 않고 근거를 늘릴 뿐이라 놓치는 쪽이 더 나쁘다 · 브리핑에 「추정 구성원」 표지 · 엄격하게 하려면 `confirmed`만(설정 1개로 바꿀 수 있게 하지 않는다 — 필요해지면 그때) |
| **Q23** (개정 4) | 정의 화면을 어디에 두나 — 독립 페이지 · 관리자 대시보드 탭 · 포털 뷰 탭 | **독립 페이지 `/biz-services`(새 탭)** — 넓은 표·일괄 작업 화면이라 채팅 옆 탭에 맞지 않고, 일반 사용자도 조회하므로 관리자 대시보드에 두면 못 본다. 관리자 대시보드에는 링크 1줄(같은 화면 두 벌 금지) |
| **Q24** (개정 4) | 정의 페이지 권한 | **조회 = 로그인 사용자(존 필터) · 편집 = 관리자만** — 210 연계·호스트 연결도 관리자 확정이다(D-348 「사람이 확정」의 사람 = 관리자). 운영 담당자 편집 역할이 필요하면 역할 추가를 별도 결정 |
| **Q25** (개정 4) | 현황 화면을 포털 뷰 탭으로만 두나, 독립 페이지도 두나 | **포털 뷰 탭 하나** — 알람·조사·질의로 넘어가는 동선이 전부 포털 탭이라 같은 창 전환이 되고, 진입로가 둘 이상이면 동작이 갈린 전례(`plans/193` K-1)가 있다. 관제실 벽면은 탭 안 [전체 화면]으로 · 독립 페이지가 필요해지면 `/?view=biz` 바로가기로 |
| **Q26** (개정 4) | CSV 가져오기를 v1에 넣나 | **넣는다(관리자 · 미리보기 필수 · 1대 일치만)** — 자동 신호 S1~S6이 운영에서 얼마나 맞을지 모르고(F1~F6 미확인), 운영팀의 업무별 서버 목록이 가장 빠른 정답 원천이다(W0-17) |
| **Q27** (개정 4) | P2 타임라인에 폴스타 변경 이력을 넣나 | **넣되 옵트인**(`NOISE_BIZ_MONITOR_CHANGES_ENABLED` 기본 off) — 「장애 직전 변경」은 AIOps 원인 확인의 첫 단서지만 `event_time` 단위 미확정·DB2 미지원(`change_feed.py` 머리말)이라 W0-16 뒤 켠다 |
| **Q28** (개정 4) | 서비스 속성 — 중요도 등급·담당 부서·구성원 계층을 추가하나 | **추가** — 등급은 현황 정렬(위험이 여럿일 때 무엇부터), 계층은 구성 맵·D1 R2에 쓰인다. 담당 부서는 부서 이름 텍스트만(개인 이름·연락처 금지 — PII 패턴 거부). 담당자 호출·연락처·당번 연동은 범위 밖 |
| **Q29** (개정 5) | 트리·등록 시각·의존 단서를 위해 관측 DB 보강 읽기를 더하나(Q5 「새 관측 조회 0」을 넘는다) · 210 스캐너를 넓히나 | **214 소유 보강 읽기를 더한다 · 210 스캐너는 넓히지 않는다** — 수동 미리보기 때만 · 존별 고정 SELECT · 정수 id만 · 실패는 신호 단위. 210 스냅샷에 칼럼을 더하면 210 계약·회귀가 바뀌고 214 플래그 off 비트 동일을 지키기 어렵다 |
| **Q30** (개정 5) | S7 트리 그룹의 등급 — 자동 구성원(중)인가, 후보(약)인가 · 트리 코드·접두 코드·ITAM 코드가 셋 다 맞으면 확정으로 올리나 | **중(`inferred` 자동) · 올리지 않는다(배지만)** — 사람이 만든 폴더 소속이라 유사도가 아니고, S1 대응·다업무 배제·순도 보정이 트리를 업무 외 기준으로 짠 존을 막는다. 자동 확정은 S1 하나로 둔다(G2). plans/72 Q4의 과적합 우려가 남으면 S7을 후보 전용으로 낮추는 대안 |
| **Q31** (개정 5) | 보조 신호(S8~S12)를 자동 구성원으로 넣나 · 근거가 여럿 겹치면 올리나 | **넣지 않는다 · 후보만**(D-348) — 일괄 이관·망 분리·공용 계약·공용 장애가 모두 업무 밖 이유로 겹친다. 근거 수는 검토함 정렬에만 쓴다 |
| **Q32** (개정 5) | 알람 동시 발생(S11)·ITAM 계약(S10)을 v1에 넣나 | **코드는 넣고 yaml 기본 off** — S11은 W0-23(공용 장애 비율), S10은 W0-22(ITAM 칸 확정 · G-4) 뒤 켠다. 순도 보정이 있어 켠 뒤에도 존별로 스스로 꺼진다 |
| **Q33** (개정 5) | ITAM 담당 부점을 `owner_org`에 쓰나 | **제안만** — `owner_org`가 비었을 때 P1 상세에 「ITAM 담당 부점: X(구성원 N대 중 M대)」를 보이고 [채우기]로 넣는다(관리자 · 감사). 자동 기입·담당자 개인 칸 읽기는 하지 않는다 · S10이 off면 표시 없음 |
| **Q34** (개정 6) | 서비스 상세 모니터링 화면을 따로 만드나 | **확정(사용자 2026-11-05 · B안)** — 독립 페이지가 아니라 P2 서비스 상세의 보기 탭(개요·지표·이벤트·진단) + [넓게 보기]. Q25(포털 탭 하나)·`plans/193` K-1과 같은 이유 |
| **Q35** (개정 6) | 구성원별 지표 추이의 원천 — M3 저장을 늘리나, 열 때 원천을 읽나 | **열 때 원천 1회 + 60초 메모** — M3은 감시 대상·보는 사람이 있을 때만 돌아 기록에 빈칸이 생기고, 저장을 늘리면 Q11(PG 지표 저장 안 함)을 바꿔야 한다. 장기 이력은 원천에 이미 있다 |
| **Q36** (개정 6) | 기간 상한 — SMS 7일·APM 몇 시간까지 | **SMS 7일(7d는 일 통계) · APM 24시간** — 시간 통계 7일은 20대 기준 10,080행으로 MCP `max_rows`를 넘고, APM series 비용은 W0-25 전에는 모른다. W0-25 결과가 가벼우면 APM 7d(60분 간격)를 별도로 연다 |
| **Q37** (개정 6) | 감시 대상(`monitored`)이 아닌 서비스도 지표 보기를 허용하나 | **허용** — 상시 수집이 아니라 사람이 연 순간 1회이고 동시 상한·메모로 묶인다. 「감시 대상만」은 M3 상시 수집 규칙으로 남긴다 |

## 16. 범위 밖
- 서비스 정보의 다른 소비(노이즈 상관 억제 · 채팅 질의 해석 E8 · 채팅 장애 진단 다중 대상) — Q1·Q18·Q21.
- (개정 4) 업무 담당자 호출·연락처·당번 연동 · 운영 담당자 편집 역할(Q24) · 현황 독립 페이지·자동 순환 벽면 모드(Q25) · 서비스 단위 SLO·가용률 계산(지표 장기 보관이 없어서 — Q11) · 업무 영향도(사용자 수·거래량) 산정.
- 서비스 심층 조사 자동 발주(Q20) · `business_map` 자동 동기화·퇴역(Q17) · 게이트웨이 응답시간 판정(`plans/155`).
- LLM 업무명 동의어 판정 — 결정적 후보 + 관리자 병합으로 대신.
- (개정 5) 통신 흐름(접속 상대·트래픽) 기반 서비스 발견 — 원천 없음(§3.2.4) · EAV 사양 유사 묶기 · 가상화 군집 묶기 · 보조 신호의 자동 확정 · `importance_id` → `tier` 자동 제안(W0-24 뒤 별도).
- DPM 구성원 — 210 §3.9 슬롯이 생기면 `kind` 추가로 확장.
- 서비스 상태 통보 · 주기·예약 재계산 · Prometheus 지표 결합 · 지표 장기 보관.
- (개정 6) 서비스 상세 모니터링 독립 페이지(Q34) · 구성원 전수 히트맵(전 구성원 조회가 필요해 조회 상한과 맞지 않음 — 「구성원 고르기」로 대신) · APM 7일 추이(Q36 · W0-25 뒤) · 그래프 내보내기(PNG·CSV) · 지표 보기 자동 이상 탐지·기준선 학습(「어제 같은 시각」 겹쳐 보기만).
