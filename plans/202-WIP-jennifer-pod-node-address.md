# 202. 제니퍼 K8s 파드 알람의 노드 서버 연결 — 웹 세션으로 `hostAddress` 수집 · 노드 IP 정확 일치 · 틀린 존 대체 금지

> **작성일**: 2026-10-29 · **번호 재부여**(2026-10-30): 원 `plans/199`·D-352 → `plans/202`·D-355(origin/main `36ce5f1` 선점)
> **상태**: **WIP** — W1~W4 구현·검증 완료(2026-10-29 · 커밋 없음 · 플래그 `JENNIFER_NODE_ADDRESS_ENABLED` 기본 off) · 계획 끝 모듈 회귀 1회(새 실패 0 — 실패는 전부 기준선 동일·환경성) · verifier Critical 0 · 보안 감사 Critical/High 0 · 지적 교정(실패 백오프 · 로그인 판정 축소 · 스트리밍 상한 · TLS 엄격 파싱 · IPv4 재검증) · **잔여**: ① 수동 재분석 경로(`src/api/routes/alarm.py` `_manual_event`가 판정 기록에서 이벤트를 다시 만들며 `apm` 블록이 없음 → 노드 연결 미작동 · 현행과 같음) ② 로그인 차단기 재기동 초기화(메모리) ③ 폐쇄망 실검증(3소스 웹 로그인·노드 연결) · D-355 부기 1
> **분석 기준 HEAD**: `60b4992b`
> **계획 시작 SHA**: `60b4992b`
> **브랜치**: `improve_noise_canceling`
> **사용자 제기(2026-10-29)**: *"제니퍼 알람에 대해 호스트네임으로 서버를 검색하는데, 제니퍼상에서는 인스턴스 이름==호스트이름(POD 이름)으로 조회되는 자원이 있는데, IP 주소는 10.139.x.x (POD대역)과 호스트 주소(10.136.x.x; 서비스 대역 — 이걸로 조회해야함)인 경우가 있음. 참고로 B0 제니퍼이지만 실제 자원은 폴스타 CM-GP에서 조회할 수 있음"* → 출처 탐색 실측 뒤 *"계획서 작성은 새로운 브랜치 improve_noise_canceling 같은 이름으로 만들어서 진행하시오"*
> **근거**: 내부망 실측(사용자 수행 · 2026-10-29 · §0) · 저장소 코드
> **관련 계획·결정**: `plans/196` §1.1·W5·§5.7-3(파드 → 노드 출처 — **이 계획이 이어받는다**) · `plans/197`(제니퍼 알람 폴스타 경로) · `plans/144`(크로스소스 사건) · D-274(게이트웨이 자격증명 경계) · D-348(호스트 연결 학습) · D-188(서버 식별 역조회) · D-329(소스 id) · D-300 ③④(자격증명·식별자 가림) · D-296(제니퍼 조회 범위) · D-255(매뉴얼 동반) · **D-355**(이 계획의 결정)

---

## 0. 실측 요약 (2026-10-29 · 내부망 · 사용자 수행)

| # | 출처 | 결과 |
|---|---|---|
| 1 | Open API `GET /api/instance?domain_id=` | `name`·`hostName` = 파드명 · `ipAddress` = **파드 IP(10.139)** · 노드 주소 칸 없음 |
| 2 | Open API 환경변수 · 알람 원문(`alarm:raw`) | 노드 주소 없음 |
| 3 | 폴스타 CM-GP | 노드가 **10.136 주소로 서버 등록**돼 있음 · 인터페이스 중 `tunl0`(Calico)이 10.139 대역 |
| 4 | 폴스타 `tunl0` 블록 매칭 | **기각** — 클러스터마다 같은 파드 대역을 다시 쓸 수 있어 프리픽스로 서버를 특정할 수 없다(사용자 판단) |
| 5 | K8s API · 에이전트 `description` 주입 | **불가** — K8s 계정 협의 거의 불가 · `description`은 재기동·재설치 때 덮어써지고 업무 담당자별 요청이 어렵다 |
| 6 | 뷰서버 extension `com.aries.extension.data.InstanceData`(`javap`) | `instanceId`·`version`·`description`·`ipAddress`·`platform`·`hostName`·`configFilePath`뿐 · K8s 클래스 없음 → **어댑터로 불가**(저장소 어댑터의 `k8s.nodeName` 읽기는 이 jar에서 항상 null) |
| 7 | **웹 내부 API `GET /agent/upgrade/list?sid=<도메인 ID>`** | 도메인의 인스턴스 배열 · **`hostAddress` = 노드 IP(10.136)** · `ip` = 파드 IP · `hostName` = 파드명 · `instId` = Open API `instanceId`(대조 확인) |
| 8 | 같은 경로를 토큰만·무인증으로 호출 | 둘 다 `/login`으로 리다이렉트 → **웹 세션 전용** |
| 9 | 권한 | 관리자가 아니어도 **해당 자원의 모니터링 권한**만 있으면 조회된다 |
| 10 | 로그인 | `POST /login/page` 폼(`id`·`password`·`autoLogin=false`·`redirect=`) · 사전 `POST /login/duplicate`(JSON `{id}` → `false`) · 세션 쿠키 `JSESSIONID<포트>` · 응답이 `ID<포트>`·`PASSWORD<포트>` 쿠키를 내림 · CSRF 미관측 · 만료 미상 |
| 11 | 응답 칸 | `hostName`·`ip`·`hostAddress`·`instId`·`sid`·`oid`·`status`·`statusMsg`·`lastLiveTime`·`os`·`shortName`·`longName`·`connectedVersion`… · ⚠ `jmx.idOrEmpty`·`jmx.passwordOrEmpty`(JMX 자격증명) 포함 |
| 12 | VM 도메인 7개 | 전 인스턴스 `hostAddress = ip` |
| 13 | 파드 도메인 10개 | 전 인스턴스 `hostAddress ≠ ip` |
| 14 | 비용 | 도메인 1건 2~83KB · 52~664ms · 도메인 약 350개 |
| 15 | 뷰서버 | 소스(`b0`·`cm_prd`·`cm_stg`)마다 따로 → **소스별 웹 계정** |
| 16 | 스크립트 실측(cm_prd·cm_stg · `probe_web_session.py`) | `/login/duplicate` = **404**(없는 경로) · `/login/page`만으로 세션 성립 · `JSESSIONID7900` 단독으로 조회 성공 · ID·PASSWORD 쿠키는 클라이언트에 남지 않음(autoLogin=false) · 같은 계정 재로그인 뒤 첫 세션 유지 · 무효·무쿠키 → `/login` 리다이렉트 · `status` `3`(statusMsg 빈 값)·`0`(`Stopped`) · `instId` 중복 0 · `hostAddress` 빈 값 0 · 파드 도메인 100/100·8/8 `hostAddress≠ip`, VM 0/6·0/4 · 최대 127KB·646ms(100건) |
| 17 | b0 | 웹 = `https`·포트 7901(`.env` API_URL과 다름 → `JENNIFER_B0_WEB_URL` 필요) · 사설 인증서 — 검증 끄면(`--insecure`) 접속 · 세션 쿠키 `JSESSIONID7901`(삭제 쿠키는 `ID7900`·`PASSWORD7900`) · `/login/duplicate` 404 · 단순 POST 로그인은 `/login`으로 되돌림 → **브라우저 방식(사전 `GET /login`으로 `JSESSIONID7901` 받기 + `Origin`·`Referer`)이면 성공**(로그인 → `/`) |
| 18 | 목록 상한 | cm_prd sid 10020 = 웹 100 · Open API 100 · 공통 100 — **잘림 없음**. 100은 제니퍼 도메인당 에이전트 등록 상한(사용자 확인) |
| 19 | b0 스크립트 실측(브라우저 방식) | cm과 같은 모양 — `JSESSIONID7901` 단독 조회 성공 · 재로그인 뒤 첫 세션 유지 · ID·PASSWORD 쿠키 삭제 지시 · 무효·무쿠키 → `/login` · `status` `3` · `instId` 중복 0 · 파드 도메인 51/51, VM 0/49 `hostAddress≠ip` · Open API와 51·49건 일치 · 최대 64KB·744ms |

**결론**: 노드 IP의 유일한 출처는 웹 세션 API다. 전체 주기 조회(350건)는 하지 않고 **알람이 난 도메인만 조회해 캐시**한다.

---

## 1. 문제

### 1.1 현재 흐름이 깨지는 지점 (코드 확인)

| 단계 | 위치 | 파드 알람에서 |
|---|---|---|
| 인스턴스 파싱 | `apm_gateway/apm_gateway/adapters/jennifer/fields.py:248` `parse_instance` | `hostName`·`ipAddress` = 파드명·파드 IP만 |
| 알람 페이로드 | `apm_gateway/apm_gateway/domain/events.py:88` `build_alarm_payload` | `hostname`·`ipAddress` = 파드 값 |
| 후보 검색 | `noise_gate/domain/host_link.py:238` `build_probe_plan` · `noise_gate/application/host_candidates.py` | 이름·IP 모두 폴스타 행과 불일치 · 토큰 유사(업무 VM 오인 위험 — 196 §1.1 위험) |
| 확정 연결 | 저장 키 `(source_id, hostname)` | 파드명은 재기동마다 바뀌어 확정이 누적되지 않음 · 파드가 노드를 옮기면 틀린 값 |
| 폴스타 소스 결정 | `sre_agent/sre_agent/application/playbook_runner.py:336` `resolve_polestar_source` | 연결 없음 → **설정 매핑(`SRE_PLAYBOOK_POLESTAR_SOURCE_MAP` b0 → polestar_b0)으로 대체** → 실제 자원은 CM-GP라 폴스타 단계 전부 0건(침묵 오답) |

### 1.2 목표

1. 파드 알람이면 그 시점의 **노드 IP**로 폴스타 서버를 **모든 존**에서 정확히 일치시켜 연결한다(B0 제니퍼 알람이라도 CM-GP 서버).
2. 노드를 정하지 못하면 **틀린 존으로 대체하지 않고** 사유를 밝힌다.
3. VM 기반 WAS는 현행과 비트 동일.

---

## 2. 설계

```
[apm_gateway 폴러] 이벤트(source_id·domain_id·instance_id)
   └─ 노드 주소 조회기: (source_id, domain_id) 캐시 → 없거나 오래됨·instId 미존재 → /agent/upgrade/list 1건
        └─ alarm:raw 페이로드 apm.host_address · apm.host_address_status · apm.pod
[noise_gate] 조사 제출 시 _attach_polestar_link (host_link_enabled와 독립 — 검토 E)
   └─ apm.pod == true 이고 상태 ok면 → 모든 존 폴스타 ipaddress 정확 일치 프로브
        ├─ 서버 1대 → meta.hints.polestar_link {status: linked, basis: node_address, …}
        └─ 0대·여러 대·실패 → {status: unresolved, reason: node_*, pod: true}
[sre_agent] resolve_polestar_source
   └─ linked → 그 db_id·server_name / pod·unresolved → 설정 매핑 대체 금지 · 사유 문장
```

### 2.1 `apm_gateway` — 웹 세션 노드 주소 조회기 (W1·W2)

**배치**: 제니퍼 자격증명은 게이트웨이에만 둔다(D-274 ③). 새 어댑터 모듈 `apm_gateway/apm_gateway/adapters/jennifer/web_session.py`(로그인·세션·요청) + `web_fields.py`(응답 파싱) · 응용 `application/node_address.py`(캐시·조회 정책).

**허용 목록 (코드 상수 · Open API 목록과 분리)**

| 메서드 | 경로 | 쿼리/폼 키 | 비고 |
|---|---|---|---|
| GET | `/login` | — | 로그인 직전 세션 쿠키 받기(본문은 버림 · §0-17) |
| POST | `/login/page` | 폼 `id`·`password`·`autoLogin`(`false` 고정)·`redirect`(빈 값 고정) · 헤더 `Origin`·`Referer`(=`<웹 URL>/login`) | 로그인만 — b0 https는 사전 세션·헤더 없으면 거부(§0-17) |
| ~~POST~~ | ~~`/login/duplicate`~~ | — | **제외** — cm_prd·cm_stg에서 404 · 없이도 로그인 성립(§0-16 · b0 재실측에서 같으면 확정) |
| GET | `/agent/upgrade/list` | `sid`(정수) | 조회 1종 |

- 그 밖의 경로·메서드는 네트워크 호출 전에 거부한다(기존 `allowlist.py`와 같은 규칙 — 정확 일치 · `..`·`//`·`%`·`\\` 거부 · 설정으로 넓히지 않음).
- `upgrade` 화면의 쓰기 동작(업그레이드 실행 등)은 경로가 달라도 전부 목록 밖이다.

**세션**
- 소스별 1세션(동시 실행 1 · FIFO). 쿠키는 **메모리에만** 둔다. `ID<포트>`·`PASSWORD<포트>` 쿠키는 받는 즉시 버린다(세션 유지에 필요한지는 W0 — 필요하면 메모리 보관만 · 로그·파일·예외 메시지 금지).
- 조회 응답이 `/login`으로 리다이렉트되거나 HTML이면 **세션 만료**로 보고 1회 재로그인 후 재시도. 만료 시간을 가정하지 않는다.
- **로그인 실패 차단기**: 연속 실패 3회 → 30분 정지(뷰서버 잠금 정책은 없음 — 무의미한 반복 로그인 억제 · 정지 로그에 「비밀번호 변경·초기화 여부 확인」 안내) · 정지 중에는 `host_address_status = login_blocked`. 성공 시 초기화.
- 로그·감사·오류 사유에 `password`·쿠키 값이 남지 않게 기존 자격증명 가림(`domain/credentials.py`)을 요청·응답 양쪽에 적용한다.

**응답 처리 — 허용 칸만**
- 원소마다 `instId`·`sid`·`hostName`·`ip`·`hostAddress`·`status`·`statusMsg`·`lastLiveTime`만 남기고 **나머지는 파싱 직후 폐기**(`jmx.*` 자격증명 포함). 모양이 다르면(배열 아님·`instId` 없음) 형태 오류로 처리하고 캐시를 갱신하지 않는다.
- `hostAddress`·`ip`는 IPv4 형식 검증. 형식 밖이면 그 원소의 주소를 비운다.

**캐시·호출 정책**
- 키 `(source_id, sid)` → `{instId: {host_address, ip, host_name, status}}` + 받은 시각. 메모리(게이트웨이 프로세스).
- **알람 경로는 캐시 나이가 최소 간격(60초)을 넘으면 재조회한다**(검토 C — 같은 `instId` 파드가 노드를 옮기는 재스케줄은 장애 시점에 몰린다. TTL 15분 재사용은 옛 노드 자동 연결 위험). 결과적으로 알람이 난 도메인당 분당 1회 이하. `JENNIFER_NODE_ADDRESS_TTL_SECONDS`(900)는 MCP 표면(`apm_instance_map`)·실패 시 `stale` 판정 상한에만 쓴다.
- 호출 타임아웃 5초 · 실패하면 캐시(TTL 안)가 있으면 그 값과 상태 `stale`(받은 시각 포함) — **`stale`은 자동 연결 근거로 쓰지 않는다**(noise_gate가 unresolved 사유로만 처리).
- **도메인 종류 기억**(검토 A): 마지막 성공 조회에서 도메인의 종류(`pod` = 원소 전부 `hostAddress ≠ ip` · `vm` = 전부 같음 · `mixed`)를 TTL 없이 메모리에 둔다. 조회 실패 중에도 `pod` 도메인이면 `apm.pod = true`를 싣는다(`vm`·`mixed`·모름이면 싣지 않음).
- **조회 시점**(검토 I): 폴러 `_publish`에서 **멱등 키로 새 이벤트임이 확인된 뒤**, `XADD` 전에 조회한다. 같은 주기·같은 도메인은 캐시로 1회만 호출된다.
- 전체 도메인 주기 조회는 하지 않는다(§0-14).

**페이로드 계약 (`alarm:raw` · `apm` 블록 가산 — 기존 키 불변)**

| 키 | 값 |
|---|---|
| `apm.host_address` | 노드 IP 문자열 또는 `""` |
| `apm.host_address_status` | `ok` · `stale` · `not_found`(목록에 instId 없음) · `disabled`(소스 계정 미설정) · `login_failed` · `login_blocked` · `error` |
| `apm.pod` | `true`(그 원소 `hostAddress ≠ ip` · 또는 조회 실패 중 도메인 종류가 `pod`) · `false`(`hostAddress = ip`) · **판정 불가면 키 없음**. 파드 판정은 **같은 웹 목록 행의 `ip`와 `hostAddress`**로만 한다 — 페이로드 `ipAddress`(인벤토리 출처 · b0 콜드 적재 중 빈 값)와 비교하지 않는다(검토 B) |

- 플래그 off면 두 키를 **싣지 않는다**(현행 페이로드와 비트 동일).
- 폴러의 이벤트 처리를 막지 않는다 — 조회 대기는 상한 5초, 넘으면 `error`로 싣고 진행.

**MCP 표면 (선택 · W2)**: `apm_instance_map` 결과 인스턴스에 `host_address`(같은 캐시) 칸 가산 — 수동 조사·채팅 경로용. 플래그 off면 칸 없음.

**설정 (게이트웨이 `.env` · 소스별)**

| 키 | 기본 | 설명 |
|---|---|---|
| `JENNIFER_NODE_ADDRESS_ENABLED` | `false` | 기능 전체 스위치(기본 off = 현행 동일) |
| `JENNIFER_<ID>_WEB_USER` · `JENNIFER_<ID>_WEB_PASSWORD` | (없음) | 소스별 전용 모니터링 계정. 비면 그 소스는 `disabled` |
| `JENNIFER_<ID>_WEB_URL` | `JENNIFER_<ID>_API_URL` | 뷰서버 웹 주소(같으면 생략) |
| `JENNIFER_<ID>_WEB_TLS_VERIFY` | `true` | https 인증서 검증 — b0는 사설 인증서·CA 미보유로 `false`(§0-17 · 경고 로그 1회) |
| `JENNIFER_NODE_ADDRESS_TTL_SECONDS` | `900` | 도메인 캐시 TTL |
| `JENNIFER_NODE_ADDRESS_MIN_REFETCH_SECONDS` | `60` | 같은 도메인 재조회 최소 간격 · 알람 경로의 캐시 신선도 상한(검토 C) |

- 설정 관리 화면(plans/191): 소스별 접미 표(`interface/config_admin_adapter.py` · `config.SOURCE_KEY_SUFFIXES`)에 `WEB_USER`(string)·`WEB_PASSWORD`(**secret**)·`WEB_URL`(string)·`WEB_TLS_VERIFY`(bool), 전역 표에 위 3개 키를 등재한다(검토 H). `.env.example`(게이트웨이) 동반.

### 2.2 `noise_gate` — 노드 주소 연결 (W3)

- 위치: `noise_gate/application/nodes/investigation_trigger.py` `_attach_polestar_link`(D-348 부착 지점 — 단독·사건·수동 세 경로 대칭).
- 읽는 곳: `event.raw_payload["apm"]`에서 직접 읽는다. `apm_trigger_hints`의 고정 키 집합은 건드리지 않는다(검토 G — 키 추가는 플래그 off 비트 동일을 깬다).
- 조건: `apm.pod is True`. `false`·키 없음이면 이 경로를 타지 않는다(Q4 · 현행).
- **`host_link_enabled`와 독립**(검토 E): 현재 `_attach_polestar_link`는 그 플래그가 꺼져 있으면 즉시 돌아간다. 노드 연결은 게이트웨이가 `apm.pod`를 실었을 때만 작동하므로 별도 플래그 없이 이 조기 반환보다 먼저 판정한다(게이트웨이 플래그 off = 키 없음 = 비트 동일). D-348 저장 연결 조회는 종전대로 그 플래그 뒤에 둔다.
- 검색: `probe_hosts(db_id, names=[], prefixes=[], ips=[host_address])`를 **등록된 폴스타 소스 전부**(존 조회 순서 · `host_sweep` 규약)에 소스별 개별 try로 돈다. 판정은 순수 함수(`noise_gate/domain/host_link.py`에 `node_address_match(rows)` 가산): 서로 다른 서버 1대 → linked · 0대 → `node_not_registered` · 2대 이상 → `node_ambiguous` · 소스 일부 실패 + 0대 → `node_unchecked`(「확인하지 못함」과 「없음」을 합치지 않는다) · 소스 일부 실패 + 1대 → linked + 힌트 `unchecked_db_ids`(근거에 실패 존 표시 · D-355 부기 2).
- 힌트: linked면 `{status: "linked", db_id, server_name, hostname, basis: "node_address", host_address, at}` · 아니면 `{status: "unresolved", reason: <위 사유>, pod: true, host_address}`. 게이트웨이 상태가 `ok`가 아니면(`stale` 포함 — 검토 C) 프로브 없이 `{status: "unresolved", reason: "node_lookup_<상태>", pod: true}`. `pod` 키는 `apm.pod is True`일 때만 붙는다(검토 A — 판정 불가는 현행).
- **우선순위**(Q2 · 검토 D): ① 노드 주소 linked → ② 저장된 확정 연결(D-348 · `host_link_enabled`일 때) → ③ 파드 unresolved 사유 → ④ (파드 아님) 현행. 노드 linked와 저장 연결이 다르면 힌트에 `previous_link: {db_id, server_name}`를 함께 싣는다(근거 문장에 둘 다).
- 노드 연결은 **저장하지 않는다**(파드가 옮겨 다님 — 알람 시점 사실).
- 캐시 없음(D-224 entity_locator 규약 4 — 방금 등록된 서버).

### 2.3 `sre_agent` — 소스 결정·근거 문장 (W3)

- `playbook_runner.py` `POLESTAR_LINK_BASIS`에 `node_address`: 「제니퍼 노드 주소 일치 — {host_address} (알람 시점 · 자동)」.
- `resolve_polestar_source`: 힌트가 `unresolved` + `pod: true`면 **설정 매핑으로 대체하지 않는다** → `(None, 사유)` · 사유 문장 「폴스타 서버 미확정 — K8s 파드(노드 {사유})」 · 사유 어휘: 노드 IP 폴스타 미등록 / 노드 IP가 여러 서버에 일치 / 노드 주소 조회 실패(로그인·형태·시간 초과) / 일부 존 확인 못 함. `pod` 없는 `unresolved`는 현행 순서 그대로(196 §5.2).
- `polestar_link()` 검증: `basis: node_address`인 linked도 `db_id`·`server_name` 필수(현행 규칙).
- **사전 수집 경로**(검토 F): `evidence_prefetch.py`의 APM 폴스타 소스 결정은 지금 사건 `infra_db_ids`만 본다 → `basis: node_address` linked면 그 `db_id`·`server_name`으로 폴스타 축을 연다(`infra_db_ids`가 있으면 그것 우선 — 현행 순서). `topology_neighbors.link_neighbor`는 `previous_confirmed`만 이웃으로 받는다 — 노드 linked는 이웃이 아니라 **대상 서버 자체**이므로 이웃 규칙은 그대로 두고, `previous_link`가 있으면 그것을 이웃 후보로 쓴다.
- 폴스타 축 단계 사유 표시는 기존 「서버 선택 대기」 대신 위 사유(파드는 사람이 골라도 다음 재기동에 무효라 선택 UI를 열지 않는다 — Q3 부속).

### 2.4 매뉴얼 (D-255 · W4)

- 관리자: 게이트웨이 웹 계정 설정(소스별 전용 모니터링 계정 · 최소 권한 · 잠금 차단기 · 끄기) · 상태 값 의미.
- 사용자: 장애 진단 화면 근거 문장 「제니퍼 노드 주소 일치」와 「폴스타 서버 미확정 — K8s 파드(…)」 의미.

---

## 3. 보안

| 위험 | 대응 |
|---|---|
| 웹 계정이 쓰기 가능한 화면(업그레이드 등)에 접근 | **모니터링 권한만 가진 전용 계정**(§0-9) · 허용 목록이 POST는 로그인만 · GET은 목록 1종 |
| 사람 계정과 중복 로그인 충돌 | 소스별 **전용 계정**(사람 계정 공유 금지) |
| 비밀번호·쿠키 누출 | 메모리 보관만 · `PASSWORD<포트>` 쿠키 즉시 폐기(W0로 필요 여부 확인) · 로그·감사·예외 가림 · `.env` 권한 0600 |
| 응답의 JMX 자격증명 | 파싱 직후 허용 칸 외 폐기 · 게이트웨이 밖으로 나가지 않음 |
| `http://` 평문 전송 | W0에서 뷰서버 스킴 확인 · `http`면 결정 기록에 위험 수용 명시(Q5) |
| 반복 로그인 실패 | 연속 실패 3회 → 30분 정지(잠금 정책 없음 · 잠기면 관리자 해제 + 비밀번호 초기화 → `.env` 갱신) |
| 비공개 내부 API 변경 | 형태 검증 실패 = `error` 상태로 노출(침묵 폴백 금지) · 제니퍼 업그레이드 시 재실측 항목으로 결정 기록 |
| 뷰서버 부하 | 알람 도메인만 · TTL 15분 · 재조회 최소 60초 · 소스당 동시 1 |

---

## 4. Wave

| Wave | 범위 | 패키지 | 크기 |
|---|---|---|---|
| W0 | 내부망 실측 잔여(§7) | 내부망 | 실측만 |
| W1 | 웹 세션 클라이언트(브라우저 방식 로그인·재로그인·차단기·허용 목록·가림·TLS 검증 설정) · 응답 허용 칸 파서 · 노드 주소 조회기(캐시·최소 간격·도메인 종류 기억·`pod` 판정) · 설정 + 설정 관리 표(secret) · 단위 테스트(가짜 웹 서버: 리다이렉트 만료·형태 오류·`jmx` 폐기·차단기·허용 목록 밖 거부·사전 GET 없으면 거부) | `apm_gateway` | 중 |
| W2 | 폴러 페이로드 `apm.host_address`·`host_address_status`·`apm.pod` 가산(멱등 확인 뒤 · 플래그 off 비트 동일) · `apm_instance_map` 칸 가산 · 경계 테스트 | `apm_gateway` | 소 |
| W3 | noise_gate 노드 연결(순수 판정 함수 + 부착 3경로 · `host_link_enabled` 독립 · 우선순위) · sre_agent 근거·소스 결정(파드 미확정 매핑 대체 금지 · 사전 수집 경로 포함) · 계약 테스트(페이로드 사본) | `noise_gate` · `sre_agent` | 중 |
| W4 | 매뉴얼(관리자·사용자) · `.env.example`(게이트웨이) · D-355 본문 등재 · D-348·D-274 부기 · `plans/196` W5 이관 표기 | 문서 | 소 |

회귀: D-303 — 계획 끝 1회 `regress.py --base <계획 시작 SHA> --files <바꾼 파일>`. `mcp_server`·`apm_gateway`는 루트 수집 밖이라 `../.venv/bin/python -m pytest`로 따로 돈다. `overfit_check` 스캔 대상(`noise_gate/domain`)에 폴스타 리터럴을 독스트링까지 넣지 않는다.

---

## 5. 사용자 결정 — **확정(2026-10-29 · Q1~Q4 권고안 · Q5 운영 답변 · D-355)**

| # | 질문 | 권고 → 확정 |
|---|---|---|
| Q1 | 노드 주소가 폴스타 서버 **정확히 1대**와 일치하면 사람 확인 없이 자동 연결할지 — **D-348 「자동 결합은 확정 연결만 · IP 일치는 유력(사람 선택)」의 예외** | ✅ 확정 — **자동 연결**. 이 IP는 이름 유사가 아니라 제니퍼가 기록한 실행 노드 주소이고 1건 정확 일치일 때만 쓴다(D-188 정확 일치 자동 승격과 같은 등급). 파드명은 재기동마다 바뀌어 사람 확정이 누적되지 않는다. 근거 문장으로 「자동」임을 항상 보인다 |
| Q2 | 같은 알람에 저장된 확정 연결(D-348)과 노드 주소 연결이 함께 있으면 어느 쪽을 쓸지 | ✅ 확정 — **노드 주소 우선**(알람 시점 사실). 둘이 다르면 근거에 둘 다 적는다 |
| Q3 | 파드인데 노드를 정하지 못하면 설정 매핑(b0 → polestar_b0)으로 대체하지 않을지 — **`plans/196` §5.2 「unresolved는 설정 매핑을 이기지 않는다」 순서 변경**(파드 한정) | ✅ 확정 — **대체하지 않음**. 지금 대체는 틀린 존 0건(침묵 오답)이다. 파드는 사람 선택 UI도 열지 않는다(다음 재기동에 무효) |
| Q4 | VM(`hostAddress = ip`)에도 노드 주소 자동 연결을 적용할지 | ✅ 확정 — **적용하지 않음**(현행 비트 동일). VM은 기존 이름·IP 후보 + 확정 연결로 충분하다 |
| Q5 | 웹 계정 운영 — 소스별 전용 모니터링 계정 발급 · 뷰서버가 `http`라면 평문 전송 위험 수용 | **사용자 확정(2026-10-29)**: 소스별 전용 계정 발급 완료(조회 권한) · b0 = `https` · cm_prd·cm_stg = `http` · 평문 전송 위험 수용(D-355에 기록) |

---

## 6. 범위 밖 · 후속 후보

- **크로스소스 사건 키**(`plans/144`): 파드 알람을 노드 인프라 알람과 같은 사건으로 묶는 데 노드 주소를 쓸 수 있다. 한 노드에 파드가 여럿이라 과묶음 위험(D-349 · 시연 중 TICKET 증가 우려)이 있어 이 계획에서는 다루지 않는다.
- **채팅 질의**(`plans/165` F3): 알람 없이 파드 이름으로 묻는 text2sql 승계 — W2의 `apm_instance_map` 칸이 재료가 된다.
- 저장소 어댑터(`apm_gateway/jennifer_adapter`)의 `k8s.nodeName` 읽기는 운영 jar에 클래스가 없어 항상 null이다(§0-6) — 어댑터 README에 실측 사실만 부기한다(W4).

---

## 7. W0 내부망 실측 체크리스트 — 완료(2026-10-29)

> 2~6은 `apm_gateway/scripts/probe_web_session.py` 한 번으로 확인한다(T0 TLS · T1 duplicate 생략 로그인 · T3 중복 로그인 · T4 JSESSIONID만 · T5 ID/PASSWORD 쿠키만 · T6 무효 세션 · T7 목록 분포 · 옵트인 T8 잘못된 비밀번호 1회 · T9 유휴 만료). 출력에 비밀값·쿠키 값·IP·호스트명 없음.

1. ~~뷰서버 스킴~~ — **확인(2026-10-29)**: b0 `https` · cm_prd·cm_stg `http`. b0 인증서가 사설이면 CA 지정 설정이 필요한지 함께 본다.
2. ~~duplicate 없이 로그인~~ — **확인(3소스)**: duplicate는 전부 404 · 로그인은 사전 `GET /login` + `POST /login/page`(`Origin`·`Referer`) — b0는 이 방식에서만 성립.
3. ~~JSESSIONID만으로 조회~~ — **확인(cm)**: 성공. 같은 계정 재로그인해도 기존 세션 유지(중복 로그인 무해). ID·PASSWORD 쿠키가 「삭제」 지시인지는 보강 스크립트의 `set_cookie` `(삭제)` 표시로 재확인.
4. ~~로그인 실패 응답·잠금 기준~~ — **확인**: `/login/page` 302 → `/login`(성공은 `/`) · 쿠키 없음. 잠금 정책은 3소스 모두 **없음**(사용자 확인) · 잠기면 관리자가 풀고 비밀번호가 초기화된다 → 게이트웨이 「3회 실패 → 30분 정지」는 잠금 방지가 아니라 **무의미한 반복 로그인 억제**로 유지하고, 정지 사유(`login_blocked`)에 「비밀번호 변경 여부 확인」을 안내한다.
5. 세션 만료 시 응답 — **모양 확인(cm)**: 무효·무쿠키 모두 `/login` 302. 실제 유휴 수명은 미측정(T9 `--idle` 옵트인 · 필수 아님 — 리다이렉트 시 1회 재로그인 설계로 흡수).
6. ~~status·instId 중복~~ — **확인(cm)**: `3`(빈 statusMsg)·`0:Stopped` · 중복 0. 노드 주소는 status와 무관하게 쓴다(정지 인스턴스도 `hostAddress` 보유).
7. ~~전용 계정 발급~~ — **완료(2026-10-29)**: 소스 3개 · 조회 권한.
8. ~~b0 로그인 실패 원인~~ — **해결(2026-10-29)**: 요청 방식 차이. 브라우저 방식으로 성공(§0-17·19). 사전 세션과 헤더 중 무엇이 필수인지는 가르지 않고 둘 다 구현한다. W1 반영: `JENNIFER_<ID>_WEB_URL`(b0 = https:7901) · `JENNIFER_<ID>_WEB_TLS_VERIFY`(b0 `false`).
9. ~~목록 100건 상한~~ — **확인**: 잘림 없음(§0-18 · 도메인 등록 상한 100).

---

## 8. 수용 기준

- 플래그 off: `alarm:raw` 페이로드·조사 결과·후보 검색이 현행과 비트 동일(스냅샷 테스트).
- 파드 알람(가짜 웹 서버 + 폴스타 픽스처): B0 소스 알람이 CM-GP 서버로 연결되고 근거 문장에 노드 주소·「자동」이 보인다.
- 노드 IP가 폴스타에 없음·여러 대·조회 실패 각각: 설정 매핑으로 대체되지 않고 사유 문장이 다르다.
- VM 회귀 0(검토 A·B): 조회 실패·`not_found`·인벤토리 `ipAddress` 빈 값인 VM 알람은 `pod` 키 없음 → 설정 매핑 대체·저장 연결이 현행 그대로.
- `stale`은 자동 연결하지 않는다(검토 C) · 캐시 60초 초과 시 알람 경로 재조회.
- `host_link_enabled=false`에서도 파드 노드 연결이 작동하고, 키가 없으면 현행과 같다(검토 E).
- sre_agent 사전 수집이 노드 linked의 `db_id`·`server_name`으로 폴스타 축을 연다(검토 F).
- 게이트웨이: 허용 목록 밖 요청 0 · 응답의 `jmx` 칸이 어떤 출력(페이로드·MCP·로그)에도 없다 · 로그에 비밀번호·쿠키 값 0 · 연속 로그인 실패 3회 뒤 호출 0.
- 경계: `apm_gateway` ↔ 본체·`noise_gate`·`sre_agent` import 0 유지(`tests/test_boundary.py`).

---

## 9. 구현 결과 (2026-10-29)

| Wave | 파일 |
|---|---|
| W1·W2 | `apm_gateway/apm_gateway/adapters/jennifer/web_session.py`·`web_fields.py`(신규) · `application/node_address.py`(신규) · `config.py` · `interface/config_admin_adapter.py` · `application/poller.py` · `application/instance_tools.py` · `application/apm_core.py` · `application/tools.py` · `__main__.py` · `.env.example` · 테스트 `tests/test_plan202_web_session.py`·`test_plan202_payload.py`·`test_plan202_verify.py` · `tests/test_config_admin.py`(읽기 전용 기대 집합) |
| W3 | `noise_gate/domain/host_link.py`(`node_address_match`) · `noise_gate/application/host_candidates.py`(`node_address_link`·`node_lookup_db_ids`) · `noise_gate/application/nodes/investigation_trigger.py`(`_node_address_hint`) · `sre_agent/sre_agent/application/playbook_runner.py` · `playbook_steps.py`(`polestar_unresolved_text`) · `evidence_prefetch.py`(`_node_link`) · 테스트 `noise_gate/tests/test_plan202_*.py`·`sre_agent/tests/test_plan202_*.py` |
| W4 | `src/static/js/diagnosis-view.js`(`HOST_POD_RE` · 노드 연결 근거 linked 표시 · [연결 변경] 숨김) · `src/static/index.html`(v=14) · 매뉴얼 `scripts/manual/{features.yaml,content/admin.md,content/user.md}` → `src/static/manual/{admin,user}.html`·`help_index.json`(A-88 · U-70 15번 — 병합 시 A-87·14번에서 이동) · `apm_gateway/jennifer_adapter/README.md` · 테스트 `tests/test_api/test_ui_plan196_diagnosis.py`·`test_plan196_verify.py`·`test_ui_plan187_w15_diagnosis.py` · D-355 부기 1 · D-274 부기 |

**이탈(근거)**: ~~일부 존 프로브 실패면 1대 일치여도 `node_unchecked`~~ → **2026-10-30 사용자 판단으로 연결로 변경**(존 간 중복 등록 거의 없음 · 근거에 「일부 존 확인 못 함(db_id)」 · D-355 부기 2) · 파드 미확정은 폴스타 축 「생략」에 사유 문장만 교체(결손 집계 불변) · 실패 뒤 `MIN_REFETCH` 동안 같은 실패 상태 재사용·소스 단위 전송 실패 백오프(웹 무응답 시 폴러 직렬 지연 방지) · 캐시에 없는 instId는 10초 하한으로 재조회 · 로그인 성공은 같은 호스트 `/` 302만 · 단일 설정(소스 `default`)은 웹 키 없음 = `disabled` · 사용자 선택 연결(재조회 요청)은 노드 판정보다 우선.
