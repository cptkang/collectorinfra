# 제니퍼 이벤트 알림 어댑터 (v0.2.0 · 초안)

제니퍼 뷰서버의 **이벤트 어댑터**(`EventHandler`)로, 이벤트 규칙에 연결된 이벤트를 collectorinfra **APM 게이트웨이**로 TCP push한다.
수신은 게이트웨이가 한다(D-195 G-4b). 게이트웨이 push 수신부는 **아직 없다**(§8).

근거 자료: 공식 [jennifer-view-extension-tutorial](https://github.com/jennifersoft/jennifer-view-extension-tutorial)(뷰서버 5.6.5.8 기준) ·
구 [jennifer-view-adapter-tutorial](https://github.com/jennifersoft/jennifer-view-adapter-tutorial) · **실물 extension jar 1.5.8·1.3.0을 `javap`로 실측**(2026-10-08 · JenniferSoft Nexus).

```
제니퍼 뷰서버 ──(이벤트 규칙에 어댑터 연결)──▶ AlarmEventAdapter.on(EventData[])
   └─ 값 사본 → 대기열(즉시 반환) → 데몬 스레드 1개 ──TCP · 1줄 JSON──▶ apm_gateway push 수신부(예정, 포트 9110 가칭)
                                                                   └─ parse_event → hostname 정합·마스킹·멱등 키 → alarm:raw
```

---

## 1. 새 가이드 대비 검토 결과

| # | 항목 | 새 가이드 / 실측 | 반영 |
|---|---|---|---|
| 1 | **허용 패키지** | 뷰서버 **5.6.5.10+**는 `server_view.conf`의 `extension_allowed_packages`에 없는 패키지의 어댑터를 **로드하지 않는다** | 등록 절차 §5-④에 필수 단계로 넣었다. 값 `com.collectorinfra.jennifer.*` |
| 2 | Java 버전 | 제니퍼 5.5+는 Java 17 + extension 1.5.8 | **실측: extension jar 자체가 Java 8 바이트코드(52)**. 그래서 어댑터도 Java 8 대상으로 빌드한다. Java 17·21 JVM에서도 그대로 로드된다(하위 호환) |
| 3 | EventData 필드 | 1.5.8에 `businessName`·`customMessage`·`domainDescription`·`domainGroupHierarchy`·`instanceData`가 추가됐다(실측: 1.3.0에는 `businessName`·`domainGroupHierarchy` 등이 없다) | 기본 14필드는 직접 읽고, 추가 필드는 리플렉션으로 읽는다. 같은 소스가 1.3.0·1.5.8 **양쪽으로 컴파일·실행**된다(실측) |
| 4 | `instanceData.hostName`·`ipAddress` | 1.5.8에서 인스턴스 호스트명·IP를 바로 준다 | 송신 JSON의 `instance` 블록으로 보낸다. 게이트웨이 hostname 정합 ①순위(APM hostName 직접 대조)에 쓸 재료다 |
| 5 | 로그 | `LogUtil.info/warn/debug/error(String)`(실측: `error(String, Throwable)`은 없다) | `LogUtil`이 있으면 쓰고, 없으면 `System.out`으로 낸다. 접두는 `[collectorinfra-adapter]` |
| 6 | 이벤트 레벨 | `FATAL`·`CRITICAL`·`WARNING`·`INFO` | `INFO`를 순위 1로 처리한다. ※ 게이트웨이 `config/event_levels.yaml`에는 `info`가 없어 경고(2)로 매핑된다. 수신부를 만들 때 정리한다 |
| 7 | `setAdapterId(String)` | 스킬 문서 예제에만 `@Override`로 나온다 | **실측: `EventHandler`에는 `on(EventData[])` 하나뿐**이라, 그 예제는 컴파일 오류가 난다. 어댑터 ID는 코드 상수 `collectorinfra_event`로 고정한다(§5-⑤) |
| 8 | 패키지명 | `com.aries.*`·기본 패키지 금지, FQCN 등록 | `com.collectorinfra.jennifer.adapter` — 규칙에 맞다 |
| 9 | 메뉴 이름 | 구 가이드는 「관리 > 어댑터 및 랩」, 새 가이드는 「설정 > SMTP 및 어댑터(+DB Plan)」 → [옵션] 팝업의 [어댑터 ID]·[사용자 정의 속성] | 절차를 새 가이드 기준으로 적었다. 버전에 따라 이름이 다를 수 있다 |
| 10 | 의존 라이브러리 | gson·jackson 등은 서버 제공(provided) | 외부 라이브러리 0. JSON은 직접 직렬화한다. 산출 jar = 우리 클래스 10개뿐이다 |

---

## 2. 코드 설명

패키지 `com.collectorinfra.jennifer.adapter` (`src/main/java/…`).

| 클래스 | 역할 | 핵심 동작 |
|---|---|---|
| `AlarmEventAdapter` | **등록 클래스**(`EventHandler` 구현) | 생성 시 1회 `어댑터 로드됨 v0.2.0 …` 로그(재기동 뒤 로드 판정용). `on()`은 이벤트를 값 사본으로 옮겨 대기열에 넣고 **즉시 반환**하며, 모든 예외를 잡아 로그만 남긴다. 제니퍼 이벤트 처리를 막거나 깨뜨리지 않는다 |
| `AdapterOptions` | 옵션 읽기 | `PropertyUtil.getValue("collectorinfra_event", key, 기본값)`. **이벤트 묶음마다 다시 읽으므로 옵션 변경은 재기동 없이 반영된다** |
| `TcpLineSender` | 송신 | JVM 전체에 데몬 스레드 1개. 연결을 유지하며 한 줄씩 보낸다. 실패하면 그 건을 대기열 앞에 되돌리고 0.5s→30s 지수 백오프로 재연결한다. 대기열(기본 1만 건)이 차면 오래된 건부터 버린다. 실패 로그는 1·2·3회째와 이후 20회마다만 남기고, 5분마다 통계(접수·송신·폐기·실패·대기)를 남긴다 |
| `EventJson` | 직렬화 | 1이벤트 = 1줄 JSON. 제어문자·따옴표·줄바꿈을 이스케이프한다 |
| `EventRecord` · `Reflect` | 값 사본 · 선택 필드 읽기 | 버전별로 있을 수도 없을 수도 있는 필드를 안전하게 읽는다(없으면 null) |
| `LevelFilter` | 선택적 최소 레벨 | 기본은 전부 송신. 해소(recovery·clear)는 항상 통과시킨다 |
| `Log` | 로그 | `LogUtil` 우선, 없으면 `System.out` |
| `SendTestEvent` | 연결 시험 CLI | 제니퍼 없이 시험 이벤트 1건을 보낸다(방화벽·수신 확인용) |

`devtest/` 폴더는 로컬 시험 전용 `PropertyUtil` 대체 클래스다. **빌드·배포 대상이 아니다.**

---

## 3. 옵션 — 사용자 정의 속성(Key/Value)

어댑터 ID는 반드시 **`collectorinfra_event`** 로 둔다.

| Key | 필수 | 기본 | 값 예 | 설명 |
|---|---|---|---|---|
| `target_host` | **필수** | (없음) | `10.x.x.x` | 게이트웨이 push 수신 호스트. 비어 있으면 송신하지 않는다 |
| `target_port` | 권장 | `9110` | `9110` | 수신 포트(가칭 — 수신부를 만들 때 확정) |
| `enabled` | 권장 | `true` | `false` → `true` | `false`면 즉시 송신을 멈춘다. **재기동 없는 끄기 스위치** |
| `source_id` | 선택 | `default` | `default` | 제니퍼가 여러 대일 때 구분자(게이트웨이 소스 id). 1대면 생략한다 |
| `min_level` | 선택 | (전부) | `warning` | 이 레벨 미만은 보내지 않는다 |
| `queue_capacity` | 선택 | `10000` | | 대기열 상한(건) |
| `connect_timeout_ms` | 선택 | `3000` | | 연결 타임아웃 |
| `max_backoff_ms` | 선택 | `30000` | | 재연결 대기 상한 |

---

## 4. 빌드 (무중단 · 사전 작업)

인터넷·Maven 없이 JDK(javac·jar)와 운영 뷰서버의 extension jar만 있으면 된다.

```sh
# 1) extension jar 위치 — 뷰서버(server.view) 쪽 파일을 쓴다
find $JENNIFER_HOME/server.view -name 'extension*.jar'
# 2) 빌드 (소스 폴더 apm_gateway/jennifer_adapter를 통째로 복사한 위치에서)
EXT_JAR=<위 경로> ./build.sh          # Windows: set EXT_JAR=... 그다음 build.bat
```

정상 출력(작업 기록에 남긴다):

```
클래스 수     : 10 (기대 10)
등록 클래스   : com.collectorinfra.jennifer.adapter.AlarmEventAdapter (포함)
바이트코드    :  major version: 52 (52 = Java 8)
sha256        : <해시>
완료          : …/dist/collectorinfra-jennifer-adapter-0.2.0.jar
```

- 뷰서버에 JDK가 없어도 된다(JRE만 있는 경우). 같은 extension jar를 다른 장비로 가져가 빌드하고 jar만 옮기면 된다. JDK 8~21 어느 것이든 된다.
- 컴파일 오류가 나면 extension jar가 뷰서버 것이 아니거나 구조가 예상과 다른 것이다. `javap -cp $EXT_JAR com.aries.extension.data.EventData`로 필드를 확인한다.

---

## 5. 운영 적용 절차 (재기동 1회)

> 원칙: **재기동 대상은 뷰서버 하나뿐**이다(이벤트 어댑터는 뷰서버에서 돈다). 데이터 서버(`server.data`)는 건드리지 않는다.
> 데이터 수집은 데이터 서버가 계속하므로, 재기동 동안 영향은 뷰서버 화면·Open API·어댑터 통보가 잠시 끊기는 것이다.
> 송신 개시는 재기동과 분리한다 — `enabled=false`로 등록·재기동한 뒤 로드를 확인하고, **재기동 없이** `true`로 켠다.

### 사전 확인 (무중단 · 작업일 전에)

| # | 확인 | 방법 | 기록 |
|---|---|---|---|
| ① | 뷰서버 버전 | 관리 화면 버전 정보 또는 설치본 이름 | 5.6.5.10 이상이면 §⑤의 허용 패키지가 **필수**다 |
| ② | 경로 | `JENNIFER_HOME` · `server.view/conf/server_view.conf` · 뷰서버 로그 디렉터리(예: `server.view/logs/`) · extension jar | 절대 경로 |
| ③ | 실행 계정 | `ps -ef \| grep -i jennifer` 로 뷰서버 프로세스 계정 확인 | jar 파일 소유자를 이 계정에 맞춘다 |
| ④ | 재기동 명령 | 5.7.0.1 설치본 기준 `$JENNIFER_HOME/server.view/bin/jennifer_view.sh stop` / `start` (운영 스크립트·서비스 등록 방식을 확인한다) | 명령 그대로 |
| ⑤ | 기존 설정 | `grep -n extension_allowed_packages server_view.conf` | 키가 이미 있으면 **쉼표로 덧붙인다**(덮어쓰면 기존 어댑터가 끊긴다) |
| ⑥ | 기존 어댑터 | 관리 화면 어댑터 목록 | ID `collectorinfra_event`와 중복되지 않는지 |
| ⑦ | 방화벽 | 수신 호스트에서 임시 리스너(아래)를 띄우고, 뷰서버에서 `SendTestEvent`를 실행한다 | 한 줄이 수신되면 통과 |

```sh
# 수신 호스트(임시 리스너 — 게이트웨이 수신부가 생기기 전 시험용)
python3 -c "import socket;s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('0.0.0.0',9110));s.listen(1);c,_=s.accept();[print(l.decode().rstrip()) for l in c.makefile('rb')]"
# 뷰서버
java -cp collectorinfra-jennifer-adapter-0.2.0.jar com.collectorinfra.jennifer.adapter.SendTestEvent <수신 호스트> 9110
```

### 작업 당일

**① jar 배치(무중단)**
```sh
mkdir -p $JENNIFER_HOME/adapter
cp collectorinfra-jennifer-adapter-0.2.0.jar $JENNIFER_HOME/adapter/
chown <뷰서버 계정> $JENNIFER_HOME/adapter/collectorinfra-jennifer-adapter-0.2.0.jar
chmod 644 $JENNIFER_HOME/adapter/collectorinfra-jennifer-adapter-0.2.0.jar
sha256sum $JENNIFER_HOME/adapter/collectorinfra-jennifer-adapter-0.2.0.jar   # 빌드 기록과 같은지 확인
```

**② `server_view.conf` 백업·수정(무중단 — 재기동 때 반영된다)** · 5.6.5.10 이상이면 필수
```sh
cp -p server_view.conf server_view.conf.bak.$(date +%Y%m%d%H%M)
# 키가 없을 때 — 맨 끝에 한 줄을 추가한다
extension_allowed_packages = com.collectorinfra.jennifer.*
# 키가 이미 있을 때 — 기존 값 뒤에 쉼표로 덧붙인다
extension_allowed_packages = <기존 값>,com.collectorinfra.jennifer.*
```

**③ 관리 화면 등록** — 「설정 > SMTP 및 어댑터(+DB Plan)」(구버전은 「관리 > 어댑터 및 랩」)

| 입력 | 값 |
|---|---|
| 유형 | 이벤트 |
| 클래스(전체 이름) | `com.collectorinfra.jennifer.adapter.AlarmEventAdapter` |
| 경로 | `$JENNIFER_HOME/adapter/collectorinfra-jennifer-adapter-0.2.0.jar`의 **절대 경로**(변수 말고 실제 경로) |

이어서 어댑터 행의 [옵션]을 연다 → **[어댑터 ID] = `collectorinfra_event`** → [사용자 정의 속성]에 아래를 넣고 저장한다.

| Key | Value |
|---|---|
| `enabled` | `false` ← 처음엔 꺼 둔다 |
| `target_host` | `<수신 호스트>` |
| `target_port` | `9110` |

※ 등록할 때 화면이 클래스 로드에 실패했다고 거부하면(5.6.5.10+에서 ②가 아직 반영되지 않았기 때문일 수 있다), ④ 재기동을 먼저 하고 그 뒤 ③을 한다.

**④ 뷰서버만 재기동**
```sh
$JENNIFER_HOME/server.view/bin/jennifer_view.sh stop
ps -ef | grep -i server.view | grep -v grep      # 프로세스가 끝났는지 확인
$JENNIFER_HOME/server.view/bin/jennifer_view.sh start
```

**⑤ 로드 확인**
```sh
grep -rn "collectorinfra-adapter" <뷰서버 로그 디렉터리>
#  기대: [collectorinfra-adapter] 어댑터 로드됨 v0.2.0 · 어댑터 ID collectorinfra_event · enabled=false · target=<호스트>:9110
grep -rniE "AlarmEventAdapter|collectorinfra|allowed_packages|ClassNotFound|NoClassDefFound" <뷰서버 로그 디렉터리>
```
- `enabled=false · target=…`이 보이면 옵션까지 정상이다.
- `target=(미설정)`이면 [어댑터 ID]가 `collectorinfra_event`가 아니다. ID를 고친다(재기동 불필요).
- 로드 줄이 없을 때: 뷰서버가 첫 이벤트 때 어댑터를 만들 수도 있다. 이벤트 규칙을 연결한 뒤(⑥) 다시 본다. 오류 줄이 있으면 §7을 본다.

**⑥ 이벤트 규칙 연결** — 「이벤트 규칙(이벤트 정책)」에서 보낼 규칙에 이 어댑터를 체크한다(또는 외부 연동을 켠다). 처음에는 규칙 1~2개로 시작한다.

**⑦ 송신 개시(재기동 없음)** — [옵션]에서 `enabled` = `true`로 바꿔 저장한다. 다음 이벤트부터 송신한다. 로그는 이렇게 이어진다.
```
송신기 시작: <호스트>:9110 · source_id=default · queue=10000 · min_level=(전부)
연결 수립: <호스트>:9110
```
수신부가 아직 없으면 `송신 실패 … 연속 N회`가 남고 이벤트는 대기열에 쌓인다(상한 1만 건, 넘치면 오래된 건부터 폐기). 제니퍼 동작에는 영향이 없다. 수신부가 준비될 때까지는 `enabled=false`로 두는 것을 권장한다.

### 되돌리기

| 상황 | 조치 | 재기동 |
|---|---|---|
| 송신만 멈춤 | [옵션] `enabled=false` | 불필요 |
| 어댑터 해제 | 이벤트 규칙에서 체크 해제 → 관리 화면에서 어댑터 삭제 | 불필요(화면 기준) |
| 완전 원복 | `server_view.conf`를 백업으로 복원하고 jar를 삭제 | 다음 정기 재기동 때 |

---

## 6. 송신 형식(계약)

TCP · UTF-8 · **1줄 = 이벤트 1건**(`\n` 종단, 문자열 안 줄바꿈은 이스케이프). 연결은 유지하며 재사용한다.

```json
{"kind":"jennifer.event","v":1,"sourceId":"default","adapterId":"collectorinfra_event","sentAt":1760000000000,
 "event":{"domainId":1000,"domainName":"..","instanceId":11,"instanceName":"was-01","time":1760000000000,
          "errorType":"ERROR_SERVICE_EXCEPTION","metricsName":"","eventLevel":"FATAL","message":"..","value":1.5,
          "otype":"INSTANCE","detailMessage":"..","serviceName":"/order","txid":"123",
          "domainDescription":"..","domainGroupHierarchy":["G1"],"instanceDescription":"..",
          "businessName":"주문업무","customMessage":".."},
 "instance":{"hostName":"gp-was-01","ipAddress":"10.0.0.5","platform":"Java","version":"5.6.5","description":"..",
             "k8s":null}}
```

- `event`의 키는 Open API 이벤트 응답과 같다. 수신부는 `apm_gateway/adapters/jennifer/fields.py`의 `parse_event`를 그대로 쓴다(실측: `ERROR_SERVICE_EXCEPTION` → `SERVICE_EXCEPTION` · kind `error`).
- 구버전에 없는 필드는 `null`, `instanceData`가 없으면 `"instance":null`, `txid` 0은 `""`, `value` NaN은 `null`.
- `message`·`detailMessage`에 개인정보가 섞일 수 있다. 마스킹은 수신부가 하므로 이 구간은 평문이다.

## 7. 문제 해결

| 증상 | 원인·조치 |
|---|---|
| 로그에 어댑터 줄이 없고 패키지 차단·로드 실패가 보임 | `extension_allowed_packages` 누락 또는 오타(값 `com.collectorinfra.jennifer.*`) → 수정 후 재기동 |
| `ClassNotFoundException` | 클래스 이름 오타 또는 경로 오류. `jar tf <jar> \| grep AlarmEventAdapter`로 확인 |
| `UnsupportedClassVersionError` | Java 8보다 높은 대상으로 빌드했다 → `build.sh`로 다시 빌드한다(바이트코드 52) |
| `target=(미설정)` | 어댑터 ID ≠ `collectorinfra_event`, 또는 속성 Key 오타 |
| `송신 실패 … ConnectException` 반복 | 수신부 미기동·방화벽 → §5 사전 확인 ⑦ |
| `대기열 가득 참 — 오래된 이벤트 폐기` | 수신부 장기 중단. `enabled=false`로 멈춘다 |

## 8. 미확인 · 후속

1. **게이트웨이 push 수신부 미구현** — 이것이 없으면 알람까지 이어지지 않는다(별도 계획 필요).
2. 등록 시점의 클래스 로드 시점(등록 즉시인지, 재기동 때인지, 첫 이벤트 때인지)과 5.6.5.10+ 차단의 화면 표시 방식. → 로컬 Docker 제니퍼 **5.7.0.1**(`apm_gateway/testdata/jennifer` · 라이선스 없이도 로드 확인 가능)에서 §5를 **리허설**하면 운영에서 한 번에 끝낼 수 있다.
3. 운영 `eventLevel` 실제 값 · 같은 장애에서 폴러와 push의 `time`/`txid` 동일성(멱등 키 일치 여부).

## 로컬 시험(개발자용)

실물 extension jar는 JenniferSoft Nexus(`https://maven.jennifersoft.com/nexus/content/groups/public/com/aries/extension/<버전>/extension-<버전>.jar`)에서 받는다.
실물 `PropertyUtil`은 뷰서버 밖에서 옵션을 읽지 못하므로, `devtest/`의 대체 클래스를 컴파일해 클래스패스 **맨 앞**에 두고 `-Dcollectorinfra_event.target_host=…`로 옵션을 준다.
