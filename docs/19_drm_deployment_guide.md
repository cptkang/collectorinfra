# 19. DRM 해제 기능 운영 배포 가이드 (Plan 69 / D-123)

운영 서버(RHEL 9.6, `agent.service`)에 DRM 해제 기능을 반영하는 절차.

> **전제**: 배포는 파일 단위 수동 복사. 개발 PC에는 DRM 모듈을 설치할 수 없으므로
> **실기 검증은 이 서버가 유일**하다. 따라서 `DRM_ENABLED=false`로 먼저 배포하고
> 단계별로 확인하며 활성화한다(각 단계에서 롤백은 env 한 줄).

---

## 1. 배포 대상 파일

### 1-1. 저장소에서 복사하는 파일 (우리 코드)

| 파일 | 신규/수정 | 비고 |
|---|---|---|
| `src/infrastructure/drm/__init__.py` | 신규 | **디렉터리 통째로 신규** — 누락 주의 |
| `src/infrastructure/drm/detector.py` | 신규 | |
| `src/infrastructure/drm/decryptor.py` | 신규 | |
| `src/infrastructure/drm/scsl.py` | 신규 | |
| `src/infrastructure/drm/diagnostics.py` | 신규 | |
| **`tools/drm-wrapper/Decrypt.java`** | 신규 | **`tools/`는 기존에 없던 배포 경로 — 가장 빠뜨리기 쉬움** |
| `src/config.py` | 수정 | `DrmConfig` 추가 |
| `src/api/routes/query.py` | 수정 | `_resolve_uploaded_bytes` + 두 업로드 라우트 배선 |
| `src/api/routes/admin.py` | 수정 | `/admin/drm/status`·`/admin/drm/verify` |
| `src/security/audit_logger.py` | 수정 | `log_drm_decrypt` |
| `src/static/admin/dashboard.html` | 수정 | "DRM 연동" 탭 |
| `src/static/js/admin.js` | 수정 | 진단 화면 로직 |
| `.env` | 수정 | §3 참조 (`.env.example`의 `DRM_*` 블록) |

**Java 래퍼는 빌드 산출물이 없다.** 단일 소스 실행 방식이므로 `Decrypt.java`
소스 파일 하나만 복사하면 되고, `javac`·jar 패키징·CI 산출물은 일절 없다.

**신규 Python 패키지 의존성 없음.** 표준 라이브러리와 기존 패키지(openpyxl,
python-docx)만 사용하므로 `pip install`·wheel 반입이 필요 없다.

### 1-2. 저장소에 없는 파일 (소프트캠프 제공 — 별도 반입)

| 파일 | 출처 | 배치 위치 |
|---|---|---|
| `scsl.jar` | ITSM 신청 → 정보보호부 | `ServiceLnk/02_Module/02_ServiceLinker/` |
| `softcamp.properties` | 위 설치본에 포함(값은 직접 수정) | 동상 |
| `sckm.jar` + `keymanager_setting.properties` + `startup.sh`/`shutdown.sh`/`register.sh` | 위 설치본 | `ServiceLnk/02_Module/01_KeyManager/` |
| `keyDAC_SVR0.sc` | **KeyManager가 실행 중 자동 생성·갱신** | `ServiceLnk/04_KeyFile/` |
| JDK 21 | 인프라팀 반입 | 예: `/opt/jdk-21` 또는 rpm 설치 |

이 파일들은 **저장소에 커밋하지 않는다**(라이선스 자산·보안 모듈). 키 파일은 복사
대상이 아니라 KeyManager가 24시간 주기로 갱신하는 산출물이다.

---

## 2. 사전 준비 (앱 배포와 독립 — 먼저 끝내두면 좋다)

### 2-0. ServiceLnk 배치와 권한

수령한 `ServiceLnk` 디렉터리 구성:

```
ServiceLnk/
├── 01_Document/          가이드 문서
├── 02_Module/
│   ├── 01_KeyManager/    sckm.jar, keymanager_setting.properties, *.sh
│   └── 02_ServiceLinker/ scsl.jar, softcamp.properties
├── 03_Sample/            샘플 코드·테스트 파일 (첫 검증에 활용)
├── 04_KeyFile/           (비어 있음) KeyManager가 keyDAC_SVR0.sc 생성·갱신
├── 05_SCSLLog/           (비어 있음) ServiceLinker 로그 → softcamp.properties의 LogPath
└── 06_SCKMLog/           (비어 있음) KeyManager 로그
```

#### 배치 위치: `/opt/softcamp` 권장

```bash
sudo mkdir -p /opt/softcamp
sudo cp -a ServiceLnk/. /opt/softcamp/
```

- **앱 배포 트리 바깥에 둔다.** 배포가 파일 단위 수동 복사이므로 앱 디렉터리
  안에 두면 재배포 때 키 파일·로그를 덮어쓰거나 지울 위험이 있다. 벤더
  소프트웨어는 자체 수명주기를 갖는다.
- **`/opt`는 RHEL에서 서드파티 패키지의 표준 위치**(FHS)다. 가이드 샘플은
  `/home/softcamp/...`를 쓰지만 `/home`은 사용자 홈 영역이고, 별도 파티션에
  `noexec` 등 마운트 옵션이 걸려 있으면 스크립트 실행에서 문제가 생길 수 있다.
- 내용물을 `/opt/softcamp/` **바로 아래**에 두면 가이드 경로와 접두사만 달라져
  1:1로 번역된다 (`/home/softcamp/04_KeyFile/...` → `/opt/softcamp/04_KeyFile/...`).

#### 실행 계정: KeyManager를 `agent.service`와 같은 계정으로 돌린다

```bash
sudo systemctl show agent.service -p User        # 앱 실행 계정 확인
```

**출력이 `User=`(빈 값)이면 서비스는 root로 실행 중이다** — 시스템 서비스에서
`User=`를 지정하지 않으면 root가 기본값이다. 현 운영 서버가 이 경우다.

계정을 나누면 "KeyManager가 만든 키 파일을 앱이 못 읽는" 권한 문제가 생긴다.
어차피 앱 계정은 복호화를 위해 키 파일 읽기 권한이 **반드시** 필요하므로,
같은 계정으로 돌려도 노출 범위가 늘지 않는다. 따라서 **KeyManager도 root로**
돌리고 소유권을 맞춘다:

```bash
sudo chown -R root:root /opt/softcamp
```

> **서비스 계정 분리는 별건으로 다룬다.** 앱을 비-root 계정으로 옮기는 것은
> 보안상 바람직하지만, 앱이 이미 만들어 둔 파일(`logs/`, `checkpoints.db`,
> `temp/`, `.cache/`)의 소유권 이전과 포트 바인딩 권한까지 함께 검토해야 하는
> 별도 작업이다. **DRM 도입을 그 계기로 삼지 않는다** — 지금은 root로 통일해
> 변경 범위를 좁히고, 계정 분리는 독립 과제로 계획한다.

#### 권한

```bash
# 디렉터리 775 / 파일 664 (가이드 요구)
sudo find /opt/softcamp -type d -exec chmod 775 {} \;
sudo find /opt/softcamp -type f -exec chmod 664 {} \;
sudo chmod 775 /opt/softcamp/02_Module/01_KeyManager/*.sh   # 스크립트 실행 권한

# 단, 키 파일 디렉터리는 타 사용자 접근을 차단한다 (DRM 키 자료)
sudo chmod 750 /opt/softcamp/04_KeyFile
```

주의할 점 셋:

- **root 실행이면 mode 비트는 기능상 무의미하다**(root는 다 읽고 쓴다). 그래서
  권한 설정의 목적은 앱 동작이 아니라 **다른 로컬 사용자로부터의 보호**다.
  775는 others에 `r-x`를 주므로 `04_KeyFile`만은 750으로 조여 두는 것이 좋다.
- **로그 디렉터리는 쓰기 권한이 필요하다.** `scsl.jar`는 우리 subprocess 안에서
  앱 계정으로 실행되므로 `05_SCSLLog/`에 쓰지 못하면 복호화가
  **ret -73(ERROR_LOG_FAIL)** 로 실패한다. 읽기 권한만 확인하고 넘어가기 쉽다.
- **키 파일 권한은 `chmod`로 고정되지 않는다.** KeyManager가 24시간마다 다시
  만들기 때문에 수동 `chmod`는 다음 갱신에서 초기화된다. 조이려면 KeyManager
  **프로세스의 umask**로 제어한다(systemd unit의 `UMask=0027`).

### 2-1. JDK 설치 확인

```bash
java -version          # 1.8 이상 (반입 JDK 21)
which java             # 절대 경로 확인 → .env의 DRM_JAVA_BIN에 사용 (§4 트랩 참조)
```

### 2-2. KeyManager 설치·기동

```bash
cd /opt/softcamp/02_Module/01_KeyManager

# 1) 환경 설정 — 타계열사 Key 미수신 항목 주석 해제(본 시스템은 KBBANK 키만 수신)
#    로그 경로는 /opt/softcamp/06_SCKMLog 로 지정
vi keymanager_setting.properties

# 2) 서비스 등록 → 출력되는 서비스 ID를 기록
./register.sh

# 3) 기동
./startup.sh

# 4) 키 파일 수신 확인 (수 분 소요될 수 있음)
ls -l /opt/softcamp/04_KeyFile/keyDAC_SVR0.sc
tail /opt/softcamp/06_SCKMLog/*                  # 수신 실패 시 원인 확인
```

`register.sh`가 표시하는 서비스 ID를 `02_ServiceLinker/softcamp.properties`에
기입한다. **서비스 ID 40번부터는 register 표시 ID와 기입 ID가 다르므로** 가이드
우측 ID 기준으로 넣는다.

`softcamp.properties` 수정 항목:
- `LogPath` = `/opt/softcamp/05_SCSLLog`
- `TransLogPath` = `LogPath`와 **동일하게**(가이드 명시)
- `LinkSystemIP` = 이 서버 IP
- `LinkLoginId` = 영문 자율

`05_SCSLLog`·`06_SCKMLog` 두 경로는 앱 감사 로그와 함께 **보존 대상**에 포함하고
로테이션 정책을 적용한다(D-123 §2.8).

**주의**: KeyManager는 `startup.sh`로 띄우면 **재부팅 시 자동 기동되지 않는다.**
서버 재시작 후 수동 재실행이 필요하며, 잊으면 키가 갱신되지 않아 며칠 뒤
복호화가 실패한다(어드민 상태 패널의 키 파일 mtime으로 감지 가능). 운영 안정성을
위해 KeyManager도 systemd unit으로 감싸는 것을 권장한다:

```ini
# /etc/systemd/system/sckm.service (예시 — 경로·User는 환경에 맞게)
[Unit]
Description=Softcamp KeyManager
After=network-online.target

[Service]
Type=forking
# User= 생략 = root (agent.service와 동일하게 맞춘다)
UMask=0027                        # 키 파일 권한은 여기서 제어(chmod는 24h 후 초기화)
WorkingDirectory=/opt/softcamp/02_Module/01_KeyManager
ExecStart=/opt/softcamp/02_Module/01_KeyManager/startup.sh
ExecStop=/opt/softcamp/02_Module/01_KeyManager/shutdown.sh
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

---

## 3. `.env` 설정

```bash
# 1단계 배포에서는 반드시 false로 시작한다
DRM_ENABLED=false

DRM_JAVA_BIN=/usr/bin/java                       # ← 절대 경로 권장 (§4 트랩)
DRM_WRAPPER_PATH=<앱경로>/tools/drm-wrapper/Decrypt.java
DRM_SCSL_JAR_PATH=/opt/softcamp/02_Module/02_ServiceLinker/scsl.jar
DRM_PROPERTIES_PATH=/opt/softcamp/02_Module/02_ServiceLinker/softcamp.properties
DRM_KEY_FILE_PATH=/opt/softcamp/04_KeyFile/keyDAC_SVR0.sc
DRM_GROUP_ID=SECURITYDOMAIN
DRM_TEMP_DIR=<앱경로>/temp/drm                    # ← 명시 권장 (§4 트랩)
DRM_TIMEOUT_SEC=20
```

`.env`는 **인라인 주석 금지**(주석은 별도 줄). 설정은 프로세스 기동 시 1회
로드되므로(`load_config`는 `lru_cache`) **값을 바꾸면 반드시 서비스 재시작**해야
한다.

---

## 4. 배포 전 반드시 확인할 트랩 3가지

### (1) systemd의 PATH — `java`를 못 찾을 수 있다

운영자 셸에서는 `java`가 되는데 서비스에서는 안 되는 경우가 흔하다. systemd는
로그인 셸의 PATH를 물려받지 않으므로, JDK를 tarball로 `/opt`에 풀었다면
`DRM_JAVA_BIN=java`는 `FileNotFoundError`로 실패한다. **절대 경로를 쓰는 것이
가장 확실하다.** 서비스 계정으로 직접 확인:

```bash
sudo -u <서비스계정> /usr/bin/java -version     # 실제 경로로 치환
```

이 실패는 어드민 상태 패널의 "Java 런타임" 항목에 그대로 표시된다.

### (2) `DRM_TEMP_DIR` — 전용 디렉터리를 명시 지정한다

`agent.service`에 `PrivateTmp=yes`가 설정돼 있으면 서비스의 `/tmp`는 격리된
공간이라 운영자가 셸에서 들여다볼 수 없다. 앱 디렉터리 하위로 **명시 지정**하면
이 혼란을 피할 수 있다. 서비스가 root로 돌므로 권한도 함께 조인다.

```bash
sudo systemctl show agent.service -p PrivateTmp
sudo mkdir -p <앱경로>/temp/drm && sudo chmod 700 <앱경로>/temp/drm
```

**반드시 DRM 전용 디렉터리를 지정한다** — 다른 용도와 공유하는 경로(`/tmp`,
앱 루트 등)를 넣지 말 것. 어댑터가 기동 시 잔여 평문 파일을 정리하기 때문이다.
(정리 대상은 `<uuid32>_in|out.<ext>` 패턴의 자체 산출물로 한정되어 있어 오설정
시에도 남의 파일은 지우지 않지만, 전용 경로를 쓰는 편이 명확하다.)

> 참고: 코드가 `mkdir(mode=0o700, exist_ok=True)`로 생성하지만, **디렉터리가 이미
> 있으면 mode를 덮어쓰지 않는다.** 위처럼 미리 만들 때 `chmod 700`을 함께 준다.

### (3) 파일 권한 — 서비스 계정이 읽고 **쓸** 수 있어야 한다

`agent.service` 실행 계정이 `scsl.jar`·`softcamp.properties`·**키 파일**을 읽고,
`05_SCSLLog/`에 **쓸** 수 있어야 한다(로그 쓰기 실패는 ret -73). §2-0대로 계정을
통일했다면 자동 충족된다.

서비스가 root면 이 항목은 자동 충족되지만, 향후 계정을 분리하면 다시 점검해야
한다(계정 분리 시):

```bash
sudo -u <서비스계정> cat /opt/softcamp/04_KeyFile/keyDAC_SVR0.sc > /dev/null && echo "키 파일 읽기 OK"
sudo -u <서비스계정> touch /opt/softcamp/05_SCSLLog/.wtest && rm -f /opt/softcamp/05_SCSLLog/.wtest && echo "로그 쓰기 OK"
```

### (4) SELinux (RHEL 9.6 enforcing)

원인 불명의 권한 오류(파일은 보이는데 접근 거부)가 나면 SELinux를 의심한다.
`/opt` 하위 벤더 소프트웨어는 보통 문제되지 않지만, 확인은 다음으로 한다:

```bash
getenforce
sudo ausearch -m avc -ts recent | tail -20
```

---

## 5. 배포 절차

### 5-1. 코드 배포 (DRM 비활성 — 무영향)

```bash
# 1) 파일 복사 (§1-1 목록 — tools/ 디렉터리 누락 주의)

# 2) 반영 확인 grep
grep -rl "ScslCliDecryptor" <앱경로>/src/            # scsl.py, decryptor.py
grep -c "_resolve_uploaded_bytes" <앱경로>/src/api/routes/query.py   # 3 (정의 1 + 호출 2)
grep -c "admin/drm" <앱경로>/src/api/routes/admin.py                 # 2
ls -l <앱경로>/tools/drm-wrapper/Decrypt.java
grep "^DRM_ENABLED" <앱경로>/.env                    # false 인지 확인

# 3) 서비스 재시작
sudo systemctl restart agent.service
sudo systemctl status agent.service
sudo journalctl -u agent.service -n 50 --no-pager
```

**확인**: 어드민 → "DRM 연동" 탭이 열리고 `DRM_ENABLED = false`, "비활성" 요약이
표시되면 배선 정상. 이 상태에서는 업로드 처리가 기존과 100% 동일하므로, 기존
폼필 기능이 정상인지도 함께 확인한다.

### 5-2. KeyManager 상태 확인

어드민 DRM 탭 → 새로고침. **키 파일** 행에서 존재 여부와 최근 갱신 시각을 확인한다
(셸의 `ls -l`을 대체). 24시간을 크게 넘겼으면 KeyManager가 죽은 것이다.

### 5-3. DRM 활성화

```bash
sed -i 's/^DRM_ENABLED=false/DRM_ENABLED=true/' <앱경로>/.env
sudo systemctl restart agent.service
```

**확인**: DRM 탭에서 경로 4종·Java 런타임·작업 디렉터리가 모두 "정상"이고 요약이
**"정상 — 복호화 준비 완료"** 인지 확인. 하나라도 "확인 필요"면 §4 트랩부터 점검.

### 5-4. 복호화 진단 (실기 첫 검증)

DRM 탭 하단 "복호화 진단"에 **암호화된 .xlsx 샘플**을 업로드한다(사내 PC에서 아무
엑셀 파일을 한 번 열었다 닫으면 암호화된다).

기대 결과:

| 항목 | 정상 값 |
|---|---|
| 감지 결과 | `DRM 암호문 (SCDS)` |
| 선두 바이트 | `5343445341303032` (SCDSA002) |
| scsl 반환값 (ret) | `0` |
| ZIP 시그니처 | 확인됨 (PK) |
| 문서 파싱 | 폼필이 처리 가능한 파일로 확인됨 |

`ret`이 0이 아니면 그 값이 곧 원인이다:

| ret | 원인 | 조치 |
|---|---|---|
| -36 | scsl이 원본을 평문으로 판정 | 키·정책 설정 확인(감지는 암호문이었으므로 비정상) |
| 3000 / 3003 | 키 파일 없음 | KeyManager 기동·서비스 ID 확인 |
| 3030 | 인증되지 않은 키 파일 | **KeyManager 재시작** |
| -71 | 미지원 확장자 | `softcamp.properties` 확장자 설정 확인 |
| -81 | BYPASS 모드 | 정보보호부 문의 |
| -33 | 파일 접근 에러 | §4-(3) 권한 확인 |

10MB급 대용량 파일과 동시 2~3건 호출도 이 단계에서 함께 확인한다.

### 5-5. 폼필 전 구간

실제 암호화 양식을 정상 화면에서 업로드해 산출물을 받고 **전 칼럼**을 확인한다
(미리보기 일부가 아니라 실제 산출 파일 기준).

---

## 6. 롤백

어느 단계에서든 아래로 즉시 원복된다. 코드 롤백은 필요 없다.

```bash
sed -i 's/^DRM_ENABLED=true/DRM_ENABLED=false/' <앱경로>/.env
sudo systemctl restart agent.service
```

`false` 상태에서는 암호문 업로드 시 "DRM 해제가 비활성화되어 있습니다" 안내가
나가고(원인 불명 파싱 실패로 위장되지 않음), 평문 업로드는 기존과 동일하게 동작한다.

---

## 7. 운영 중 점검

- **DRM 탭 상태 패널**: 키 파일 갱신 시각이 24시간 이내인지. 재부팅 후 KeyManager
  재기동 누락을 여기서 잡는다.
- **감사 로그**: `logs/audit-YYYY-MM-DD.jsonl`의 `"event": "drm_decrypt"` 레코드.
  `mode` 필드로 양식 업로드(`form_fill`)와 어드민 진단(`admin_verify`)이 구분된다.
- **ServiceLinker 자체 로그**: `softcamp.properties`의 `LogPath`·`TransLogPath`
  경로. 앱 감사 로그와 함께 보존 대상에 포함하고 로테이션 정책을 적용한다.
  앱 로그의 `temp_file` 값으로 scsl 로그와 대사할 수 있다.

## 8. 서버 재부팅 시 체크리스트

1. `sudo systemctl status agent.service` — 앱 기동 확인
2. **KeyManager 기동 확인** — systemd 등록 전이라면 `startup.sh` 수동 실행 필요
3. 어드민 DRM 탭에서 "정상 — 복호화 준비 완료" 확인
