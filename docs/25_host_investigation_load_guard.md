# 대상 호스트 부하 가드 — 요구 계약 (Plan 78 W2-6 · W3-6 · **L-5는 Plan 81**)

> **78은 이 요구를 구현하지 않는다.** 조사 실행은 `sre_agent` toolset 소관이고(D-118 · 78 §0.3),
> 78은 **요구를 명시하고 계약으로 전달**한다. 이 문서가 그 계약의 정본이다.

## 왜 필요한가

본 계획의 **대표 시나리오가 *이미 포화된 서버*를 조사하는 것**이다 —
*"CPU 80% 이상인 서버를 조회하고 그 서버들의 프로세스/원인을 보여줘"*.
조사가 장애를 악화시키면 **계획의 목적 자체가 무너진다**(78 §4.4 갭 ③ · ETCLOVG E 계층).

## 요구 5항 (`sre_agent` 구현)

| # | 요구 | 근거 | 상태 |
|---|---|---|---|
| L-1 | 수집 명령에 **`nice` 우선순위 하향**을 건다 | 조사 프로세스가 이미 포화된 CPU를 더 뺏지 않는다 | ✅ **구현**(2026-08-27) |
| L-2 | 수집 명령 자체에 **timeout**을 건다 | 중앙에서 끊어도 **대상 호스트의 프로세스는 계속 돈다** — 중앙 타임아웃과 별개다 | ✅ **구현** |
| L-3 | `top -b` 등 반복 샘플링은 **1회 스냅샷 고정**(`-n 1` 류) | 장시간 점유 금지 | ✅ **구현** |
| L-4 | **동일 호스트 중복 동시 조사 금지** | 부하가 곱해진다 | ✅ **구현**(2026-08-27) — 본체는 락으로 직렬화, `sre_agent`는 in-flight 키로 거부 |
| **L-5** | **조사할 수 없는 대상을 조사하지 않는다** — 대상 가용성이 비정상이면 조사 전에 거부 | 죽은 호스트에서는 도구가 **에러가 아니라 빈 데이터**를 돌려준다 → ReAct가 전체 타임아웃(300s)까지 돌며 근거 없는 서술을 만들 수 있다(조사 1건 실측 161s) | ✅ **구현**(2026-08-28 · Plan 81 · D-175) |

### 구현 방식 — **allowlist가 유일한 실효 강제다**

`sre_agent/toolset_profiles.py`. `system_prompt_additions`(지침 주입)를 넘기는
**프로덕션 호출부가 0건**이었으므로(2026-08-27 실측) 지침만으로는 아무것도 강제되지 않았다.
allow/deny가 **prefix 매칭**이라는 성질을 이용한다:

```python
LOAD_GUARD_PREFIX = "timeout 20 nice -n 10 "     # L-1 + L-2
HEAVY_DIAG_COMMANDS = ("top -b -n 1", "vmstat", …, "journalctl", "dmesg", "lsof")
VM_DIAG_ALLOW = [*LIGHT_DIAG_COMMANDS, *(guarded(c) for c in HEAVY_DIAG_COMMANDS)]
```

무거운 명령은 **가드 형태로만** allow에 오르므로 가드 없는 형태가 **자동 거부**된다.
가벼운 명령(`uptime`·`ps`·`free`·`ss`·`ip … show`·`systemctl status` 등)은 래핑하지 않는다 —
즉시 끝나고 출력이 유계라 마찰만 커진다.

**L-3의 실체**: 종전 allow는 `"top -b"` 였다. `-n`이 없으면 **무한 실행**되므로,
포화된 호스트를 조사하다 **조사 자체가 호스트를 붙드는** 형태였다. `top -b -n 1`로 고정했다.

**★ 가드가 만든 신규 위험과 그 답**: `journalctl --vacuum`을 bare deny로만 두면
`timeout … nice … journalctl --vacuum`이 그 prefix로 시작하지 않아 **차단이 우회된다.**
allow와 deny에 **같은 `guarded()`를 적용**해 막았고, 테스트로 고정했다.

**강제와 안내는 한 세트다**: 안내 없이 강제만 걸면 무거운 명령이 전부 거부되어 조사가
무력화된다. `DiagnosisAgent.ask()`가 `LOAD_GUARD_NOTE`를 **기본 주입**한다(호출자 지침은 보존).

검증: `sre_agent/tests/test_load_guard.py` **32건**.

### L-5 구현 — **판정은 본체가, 거부는 `sre_agent`가**

`sre_agent`는 폴스타 DB를 읽지 않는다(D-118 양방향 import 0). 그래서 L-4와 같은 분업을 쓴다:

| | 판정 | 거부 |
|---|---|---|
| 어디서 | **본체** — `src/domain/host_availability.py`(순수) + `cmm_resource` 조회 | **`sre_agent`** — `_apply_sync_guards`의 6번째 가드 |
| 무엇으로 | `avail_status` · `is_maintenance` | 페이로드로 받은 `target_state` |

전달 경로는 두 진입점 모두 **후방 호환 확장**이다 — 알람은 `payload.meta.target_state`(값이 있을
때만 키 생성), 채팅은 `sre_diagnose(target_state=None)` 선택 인자. **필드가 없으면 통과**한다
(fail-open) — 이 가드는 보안 통제가 아니라 낭비 방지이므로, 정보가 없을 때 막으면 정상 조사를 잃는다.

**예외 하나**: 알람 자체가 **가용성/다운 계열**이면 판정을 싣지 않는다. 다운 알람의 대상은 당연히
가용하지 않으므로, 그대로 적용하면 *"왜 내려갔는지"* 조사가 통째로 막힌다(`is_availability_alarm`).

**슬롯을 잡지 않는다**: 가용성 거부는 dedup·시간당 예산·in-flight 키를 **획득하기 전에** 판정한다.
거부될 조사가 슬롯을 쓰면 정작 필요한 조사가 그 슬롯에서 밀린다.

검증: `sre_agent/tests/test_target_unavailable_guard.py` **18건**.

## 본체(78)가 이미 지키는 것

| 요구 | 본체 구현 | 위치 |
|---|---|---|
| L-4 | `(db_id, hostname)` 단위 **직렬화 락** — 같은 호스트 동시 조사가 1건이 된다 | `src/orchestration/process_query.py` `_inflight_lock` |
| 동시 대상 수 상한 | `COMPOSITE_FANOUT_CONCURRENCY`(기본 3) | `CompositeConfig` |
| 대상별/전체 타임아웃 | `COMPOSITE_TARGET_TIMEOUT_SECONDS`(10) · `COMPOSITE_TOTAL_TIMEOUT_SECONDS`(45) | 〃 |

L-4는 **본체와 `sre_agent` 양쪽에 필요하다** — 본체 락은 본체를 경유하는 조사만 막고,
`sre_agent`가 다른 경로(이벤트 자동 조사 등)로 같은 호스트를 동시에 잡을 수 있다.

### L-4 구현 — **dedup으로는 대체되지 않는다**

종전 `sre_agent`의 중복 억제는 `investigation_dispatcher`의 **fingerprint dedup**(알람 지문 기준)
하나였다. 따라서 **서로 다른 알람이 같은 호스트를 가리키면 동시 조사가 성립**했고 —
CPU 알람과 메모리 알람이 같은 서버를 가리키는 것은 흔하다 — L-4의 취지(부하가 곱해진다)가
그 경로에서 막히지 않았다.

| | fingerprint dedup TTL | **L-4 in-flight 가드** |
|---|---|---|
| 막는 것 | **같은 알람**의 재조사 | **서로 다른 알람이라도 같은 호스트**의 동시 조사 |
| 기준 | 지문 · TTL | `(db_id, host)` · 진행 중 여부 |

**구현**(`investigation_dispatcher`): `_inflight_hosts: {(db_id, host): (investigation_id, 획득 시각)}`.
`_apply_sync_guards`에서 dedup·예산과 같은 락 안에서 획득하고, **워커 `finally`와 스텁 경로**에서
해제한다(스텁은 워커가 돌지 않아 여기서 풀지 않으면 그 호스트가 **영구히 조사 불가**가 된다).

- **거부하지 직렬화하지 않는다** — 조사는 분 단위로 길어(실측 161s) submit을 붙들면 MCP 동기
  타임아웃(60s)을 넘긴다. 거부하고 사유(`host_investigation_in_flight`)를 남기면 호출자가
  진행 중인 조사의 브리핑을 받아 쓸 수 있다. **본체는 API 호출이 짧아 락으로 직렬화한다** —
  같은 요구, 다른 수단.
- **양쪽 진입점의 payload 형태가 다르다**(실측): 알람은 `payload["event"]["dbId"|"hostname"]`,
  pull 진단은 `payload["db_id"|"hostname"]`. 한쪽만 보면 그 경로에서 가드가 통째로 무력화된다.
- **자기 키만 해제한다** — 방어적 축출 뒤 다른 조사가 같은 키를 잡았을 수 있고, 무조건 pop하면
  남의 가드를 푼다.
- **방어적 축출**: 워커 유실로 남은 키는 `timeout × 2` 경과 시 sweep한다. 없으면 **가드가 장애가 된다**.

**잔여 한계(명시)**: `hostname`이 없으면 `serverName`으로 대체한다. 폴스타는 server_name ≠ hostname
이므로(D-046), 같은 호스트가 한 번은 hostname·한 번은 serverName으로 들어오면 **다른 키가 되어
가드를 비껴간다.** 이름 해소는 본체(`cmm_resource`) 소관이라 `sre_agent`에서 부를 수 없다(D-118).

검증: `sre_agent/tests/test_load_guard_l4.py` **18건**. 특히
`test_dedup_alone_would_not_have_blocked_it`이 **dedup으로 대체되지 않음**을 고정한다.

## 설정 이관

| 플래그 | 소관 |
|---|---|
| `HOST_DIAG_NICE` | **`sre_agent`** |
| `HOST_DIAG_CMD_TIMEOUT_SECONDS` | **`sre_agent`** |
| `HOST_ACCESS_MODE`(polestar_agent \| ssh) | **`sre_agent`** — 실행 채널은 그쪽 소관 |

## 전달 방법

조사 위임 페이로드(`sre_diagnose` / 트리거 `contract_version: "1"`)에 별도 필드를 **추가하지
않는다** — 계약 변경은 양쪽 동시 배포를 요구한다. 대신 이 문서를 정본으로 삼고,
`sre_agent` 측 toolset 프로파일이 L-1~L-4를 만족하도록 구현한다(`docs/24_middleware_profile_spec.md`와
같은 형태의 요구 명세).
