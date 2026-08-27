# 24. `profile="middleware"` 요구 명세 — OS 레벨 근사

> 근거: `plans/78` **W7-1단계**(§4.7.1-A) · **D-168** · 관련 D-035(결정적=판단) · D-118(경계)
> 작성일: 2026-08-27 · 상태: **구현 완료**(v2 — 사용자 지시로 `sre_agent` 구현까지 편입)
> 짝 산출물: `config/middleware_signatures.yaml` · `src/domain/middleware.py`

## 0. 이 문서의 성격

**v2 갱신(2026-08-27)** — 초판은 요구 명세였다. `sre_agent`가 같은 저장소 안에 있다는 사용자
지시에 따라 **구현까지 완료**했다.

**경계는 그대로다**(D-118) — 본체는 `sre_agent`를 **import하지 않는다**(전수 grep 0건 확인).
편집이 허용되는 것과 import가 허용되는 것은 다르다. `sre_agent`는 자체 venv·자체 프로세스이며,
연결은 MCP 계약으로만 이뤄진다.

| 산출물 | 위치 | 상태 |
|---|---|---|
| 식별 규칙 선언 파일 | `config/middleware_signatures.yaml`(13종) | ✅ |
| 결정적 매처 | `src/domain/middleware.py` | ✅ |
| **조사 프로파일** | `sre_agent/sre_agent/toolset_profiles.py::middleware_profile()` | ✅ **v2** |
| **조사 초점 지침** | 같은 파일 `MIDDLEWARE_FOCUS_NOTE` | ✅ **v2** |

## 1. ★ `VM_DIAG_ALLOW` 확장이 **불필요하다** — 실측 근거

W7-1이 요구하는 신호는 **전부 기존 allowlist로 얻을 수 있다.**
2026-08-27 `sre_agent/sre_agent/toolset_profiles.py::VM_DIAG_ALLOW` 실측:

```
dig, dmesg, free, iostat, ip …, journalctl, last, lsblk, lsof, mpstat, netstat,
nslookup, numastat, pidstat, ping -c, ps, sar, smartctl -H, ss, sysctl -a,
systemctl is-active, systemctl list-units, systemctl status, top -b, traceroute,
uptime, vmstat
```

| 필요 신호 | 명령 | allowlist |
|---|---|---|
| 프로세스 존재·기동 인자(힙 설정 등) | `ps` | ✅ 보유 |
| 리스닝 포트·연결 수 | `ss` | ✅ 보유 |
| 프로세스 자원 점유 | `top -b`, `pidstat` | ✅ 보유 |
| 최근 시스템 로그 | `journalctl` | ✅ 보유 |
| 서비스 상태 | `systemctl status` | ✅ 보유 |
| 열린 파일·소켓(선택) | `lsof` | ✅ 보유 |

> **프로파일의 실체는 새 명령이 아니라 기존 명령의 조합·해석이다.**
> 새 명령을 열면 그만큼 공격 표면이 늘어난다 — 안 열어도 되면 안 여는 것이 맞다.

## 2. 구현 — `sre_agent` (v2 완료)

1. **`middleware_profile(extra_allow=None)` 추가** — `vm_profile` 전례를 따른다.
   **allowlist가 `vm_profile`과 완전히 동일**하며 `VM_DIAG_ALLOW` 변경이 **0**이다(§1).
   테스트가 이 등가성을 고정한다(`test_allowlist_identical_to_vm_profile`).
   그러면 왜 별도 함수인가 — **차이는 조사 초점**(`MIDDLEWARE_FOCUS_NOTE`)이다.
2. **수집 순서**: `ps` → (식별 결과의 pid로) `pidstat`·`ss` → 필요 시 `journalctl`.
   식별이 먼저다 — 전체 호스트 로그를 긁으면 컨텍스트가 폭증한다.
3. **부하 가드 승계**(D-117 · 78 §4.4 갭 ③): 조사 대상이 이미 포화 상태일 수 있으므로
   `nice`·`timeout`을 그대로 적용한다. **조사가 장애를 악화시켜서는 안 된다.**
4. **반환 계약**: 원시 `ps` 출력(또는 `[{pid, cmdline}]`)을 그대로 실어 보낸다 —
   **판정은 본체의 결정적 매처가 한다**(§3). `sre_agent`가 미들웨어 종류를 추정하지 않는다.

## 3. 식별 — 본체 소관 (구현 완료)

`src/domain/middleware.py::identify(processes)` → `MiddlewareScan`

- **규칙은 `config/middleware_signatures.yaml`에만** 있다(하네스 표 29 G).
  새 미들웨어는 **항목 추가만으로** 식별된다 — 코드 변경이 필요하면 설계 실패다.
- **판정 100% 결정적**(D-035) — LLM 미사용. 미식별은 *"모델이 못 맞혔다"* 가 아니라
  *"규칙에 없다"* 로 귀결되어 규칙을 고쳐 개선할 수 있다.
- **미식별도 사유와 함께 반환**한다(`unmatched: [{pid, cmdline, reason}]`).
  빈 결과는 *"미들웨어 없음"* 과 *"판정 실패"* 를 구분하지 못한다.

## 4. 선행 실측 2항 (W7-1 수용 기준)

### ① 미들웨어를 OS 레벨로 식별할 수 있는가 — **가능**

프로세스명·기동 인자 패턴으로 식별된다. 대표 근거:

| 종류 | 판별 신호(cmdline) | 인스턴스 구분 |
|---|---|---|
| Tomcat | `org.apache.catalina.startup.Bootstrap` | `-Dcatalina.base=` |
| WebLogic | `weblogic.Server` | `-Dweblogic.Name=` |
| JBoss/WildFly | `org.jboss.as.standalone` | `-Djboss.server.name=` |
| JEUS | `jeus.server.ServerBootstrapper` | `-Djeus.server.name=` |
| nginx | `nginx: master process` | `-c <conf>` |
| Redis | `redis-server` | bind 주소·포트 |

**Java 계열은 기동 인자가 풍부해** 힙 설정(`-Xmx`)·도메인·인스턴스명까지 함께 얻는다.
**한계**: 컨테이너 안에서 도는 미들웨어는 호스트 `ps`에서 인자가 잘리거나 네임스페이스가
달라 보일 수 있다 — 컨테이너 환경 편입 시 별도 실측이 필요하다.

### ② 조사 대상 미들웨어 종류 — **기본 13종 · 조직 확정 대기**

WAS(Tomcat · JBoss/WildFly · WebLogic · WebSphere · JEUS) ·
웹서버(nginx · Apache httpd · WebtoB) ·
캐시·메시징(Redis · Kafka · RabbitMQ · ZooKeeper) · TP(Tmax).

> **⚠ 이 목록은 국내 엔터프라이즈 관행 기준 기본 세트다.** 조직의 실제 미들웨어 목록은
> **확인이 필요**하며, 확인되면 선언 파일에서 조정한다. **확장이 데이터로 끝나므로
> 확인 지연이 착수를 막지 않는다**(SPEC S5로 실증).

## 5. 오탐 방지

`grep tomcat` 같은 조사 명령 자신이 미들웨어로 잡히면 조사 대상이 오염된다.
선언 파일의 `global_exclude`(`grep `·`pgrep `·`/usr/bin/ps `)와 규칙별 `exclude_any`로 막고,
**테스트로 고정**한다(`test_exclusion_prevents_false_positive`).

## 6. 범위 밖

| 항목 | 소관 |
|---|---|
| 조사 결과 **소비·표시**(브리핑 반영) | **W4**(`plans/80` WU-19) — WU-05 선행이라 대기 |
| APM 연계(정본 소스) | **W7-2** — R-11(도입 시점·벤더 미정) 대기 |
| 컨테이너 내부 미들웨어 | 미검토 — 편입 시 §4 ① 한계 참조 |
