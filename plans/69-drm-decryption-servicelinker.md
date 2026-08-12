# Plan 69 — 양식 업로드 DRM 해제 (Softcamp ServiceLinker 연동)

- 작성일: 2026-08-07 (확정 통합본 — 조사·문답 경위는 git 이력 참조)
- 상태: **Phase 1·2·2b 구현 완료 (2026-08-12, D-123)** — 감지·라우트 대칭 배선·
  `ScslCliDecryptor`·`Decrypt.java`·어드민 진단 도구(§4.2), 테스트 35건 그린.
  잔여: Phase 0(ITSM·서비스 ID — §8 N2) → Phase 3(운영계 단계적 활성화, §4.1).
  실기 실측은 운영계에서만 수행(개발 PC는 DRM 모듈 설치 불가, `DRM_ENABLED` 토글)
- 선행: Plan 68 (폼필 결정적 경로 — 업로드 파이프라인 현행 구조), D-118 (HITL 폼필)
- 결정: **D-123** (`docs/02_decision.md` — 감지·래퍼·env 토글·재암호화 비범위)

---

## 0. 배경 — 문제 정의

사내 PC에는 Softcamp DRM(**ServiceLinker**, 설치 폴더명 `ServiceLnk`)이 설치되어
문서 파일(txt, doc(x), xls(x), pdf 등)이 **열람 시점에** 자동 암호화된다. 브라우저
업로드는 파일 바이트를 그대로 전송하므로 서버는 **암호문**을 수신하고, 이후
openpyxl/python-docx 파싱이 전부 실패한다.

### 0.1 영향 지점 (실측: `src/api/routes/query.py`)

| 엔드포인트 | 파일 종류 | 판정 |
|---|---|---|
| `POST /query/file` (L985) | xlsx/docx 양식 | **적용 대상** |
| `POST /query/file/stream` (L1125) | xlsx/docx 양식 | **적용 대상** — 위와 대칭 유지 |
| `POST /query/mapping-feedback` (L1495) | md 텍스트 | 비대상 (md는 암호화 대상 아님) |

### 0.2 환경과 배포 전략

- **개발**: 각자 Windows PC — **DRM 해제 모듈(KeyManager/ServiceLinker) 설치 불가**
  (정보보호부 확인). 개발계 Linux 서버는 아직 미구축.
- **운영**: 폐쇄망 Linux 서버 — **RHEL 9.6** (파일 단위 수동 배포 환경)

따라서 **DRM 해제가 실제로 동작하는 환경은 당분간 운영계뿐**이다. 코드는
개발/운영 동일하게 유지하고, 활성화는 `.env`의 `DRM_ENABLED` 하나로 제어한다:

| 환경 | `DRM_ENABLED` | 동작 |
|---|---|---|
| Windows 개발 PC / CI | `false` (기본값) | Passthrough — 현행과 완전 동일. DRM 코드는 단위 테스트(픽스처·mock)로만 검증 |
| 운영 RHEL 9.6 | `true` (연동 완료 후) | 감지 → scsl 래퍼 복호화 |
| (향후) 개발계 Linux | `true` | 구축 시 실측 환경을 운영계 → 개발계로 이관 |

부수 이점: 실행 환경이 Linux로 단일화되어 **이기종(Windows/Linux) 모듈 호출 차이
문제는 소멸**했다 — Windows KeyManager 설치 절차는 본 계획에서 제외한다.

---

## 1. 연동 스펙 (정보보호부 가이드 2종 전문 실측 — 확정)

> 가이드 원본(PDF, Windows 20p/Linux 17p, 2025.02~03)은 내부 정보 포함으로 저장소에
> 두지 않는다(로컬 별도 보관). 래퍼 구현에 필요한 내용은 본 절에 전부 반영됨.

### 1.1 구성 요소

- 디렉터리 표준: `ServiceLnk > 02_Module > 01_KeyManager / 02_ServiceLinker /
  03_Sample / 04_KeyFile` — 해당 경로 폴더 생성 + **권한 775 필수**.
- **KeyManager**: DRM 키 서버(AKS)로부터 주기적으로 키 파일
  (`04_KeyFile/keyDAC_SVR0.sc`)을 수신·갱신하는 상주 모듈.
  - Windows: 설치형 프로그램(6.0) — 관리자 권한 + Windows 서비스 등록
  - Linux: **Java 모듈** — `sckm.jar` + `keymanager_setting.properties` +
    `startup.sh`/`shutdown.sh`/`register.sh`
- **ServiceLinker(Java)**: `scsl.jar` — classpath에 포함해 호출. **키매니저·암복호화
  대상 데이터와 동일 시스템에 위치 필수**(로컬 파일 경로 기반). 설정은
  `02_ServiceLinker/softcamp.properties`.
- **Java 1.8 이상**(1.5 이하 불가). 반입 확정된 JDK 21(Windows·RHEL 9.6 두 종)로 충족.
- 라이선스: 사이트 라이선스 — 정보보호부 요청으로 발급.
- (참고) DLL 연동(`DSAPI.dll`)은 ASP/.NET용 — 우리는 Java 경로만 사용.

### 1.2 복호화 API (양 OS 동일)

```java
import SCSL.*;
SLDsFile sFile = new SLDsFile();
sFile.SettingPathForProperty("<...>/02_ServiceLinker/softcamp.properties");
int ret = sFile.CreateDecryptFileDAC(
    "<...>/04_KeyFile/keyDAC_SVR0.sc",  // 키파일 (KeyManager가 갱신)
    "SECURITYDOMAIN",                    // GroupID — 가이드상 "수정금지" 리터럴
    srcFile, dstFile);
// ret == 0 : 성공
// ret == -36 : 원본이 평문 — "에러 처리 불필요, 성공과 동일하게 적용"(가이드 명시)
```

- 보조 API: `SLBsUtil.isEncryptFile(path)`(1=암호화/0=평문), `SLDsEnvSet(0)`(확장자
  체크 해제). 암호화 계열(`SLDsEncFileDACV2` 등)은 재암호화 비범위(§8)라 미사용.
- 지원 확장자에 **xlsx·docx 명시 포함**. 미지원 확장자는 `-71` + 원본이 dst로 복사됨.
- `softcamp.properties` 수정 항목 3개: `TransLogPath`(=LogPath와 동일),
  `LinkSystemIP`(업무시스템 IP), `LinkLoginId`(영문 자율). **서비스 ID 기입 전
  보안담당자 문의 필수**.
- 통신 경로: 업무시스템 → 키 서버 `172.17.###.###` **:32000(키 수신)·:32002** —
  **동일 테넌트로 방화벽 불필요 확인 완료**(telnet 연결 검증).

### 1.3 주요 에러 코드 → 어댑터 처리 매핑

| ret | 의미 | 처리 |
|---|---|---|
| 0 | 성공 | dst 사용 |
| -36 | 원본이 평문 | 성공 취급. 방어적으로 dst 미생성 시 src 바이트 사용 |
| -71 | 미지원 확장자(원본이 dst로 복사됨) | 에러 노출(xlsx/docx는 지원 목록 — 발생 시 설정 문제) |
| -41 | 파일 손상 | "파일 손상" 에러 노출 |
| -81 | BYPASS 모드 | 운영 설정 이상 — 관리자 문의 에러 |
| 3000/3003 | 키파일 없음 | KeyManager 미동작 의심 — 관리자 문의 에러 |
| 3030 | 인증되지 않은 키파일 | 에러 노출 + **키매니저 재시작 필요**(가이드 명시) — 운영 조치 가이드에 기재 |
| 4001 | 복호화 실패 | 에러 노출 |
| 2002 | src=dst 경로 동일 | 래퍼 버그 방지 — temp in/out 파일명 상이하게 고정 |

### 1.4 암호문 파일 시그니처 (사내 실측)

| 오프셋 | 값 | 해석 |
|---|---|---|
| 0–7 | `53 43 44 53 41 30 30 32` "SCDSA002" | 공통 매직 (SCDS = Softcamp Document Security 추정 — 에러코드 `NO_SCDSFILE_ACCESS`와 부합) |
| 8–13 | `00 00 f0 06 00 00` | 헤더 길이(0x06f0=1776?) 추정 |
| 14– | 파일별 상이 | 키 자료/암호문 |

- 빈 파일·내용 있는 파일 모두 동일하게 `SCDS` 시작, xlsx/docx 간 동일, **원 확장자
  유지**. 판정은 `startswith(b"SCDS")` 단독(5~8바이트 `A002`는 버전 변동 대비
  판정 제외, 진단 로그만).

---

## 2. 설계

### 2.1 방식: Java CLI 래퍼 + subprocess

`scsl.jar`는 Java 라이브러리이므로 Python이 직접 로드할 수 없다. 얇은 Java CLI
래퍼(`scsl-decrypt.jar`)가 `scsl.jar`를 classpath로 로드해 복호화 API를 호출하고,
Python은 subprocess로 실행한다. JVM은 별도 프로세스라 Python 3.x 스택과 상호
제약이 없고, 크래시 격리·타임아웃 제어가 용이하다. 호출당 JVM 기동
오버헤드(수백 ms~수 초)는 폼필 작업 규모에서 허용 — 문제 시 JPype 상주 JVM으로
전환(폴백).

### 2.2 계층 배치 (Clean Architecture)

```
src/infrastructure/drm/
├── __init__.py
├── detector.py        # is_drm_encrypted(file_bytes, file_ext) -> bool  (결정적 감지)
├── decryptor.py       # DrmDecryptor 프로토콜 + PassthroughDecryptor
└── scsl.py            # ScslCliDecryptor (subprocess 어댑터)
tools/drm-wrapper/
└── Decrypt.java       # 래퍼 소스 — 단일 소스 실행(빌드 없음, §3.4)
```

- infrastructure 계층 → `python scripts/arch_check.py` 통과 확인 후 커밋.
- 호출부는 라우트 공통 헬퍼(§2.5) — interface→infrastructure 의존은 방향 위반 아님.

### 2.3 감지 로직 (`detector.py`) — 결정적, LLM 무관

1. 선두 4바이트 `PK\x03\x04`(ZIP) → 평문 xlsx/docx, 통과.
2. 선두 4바이트 `SCDS` → DRM 암호문 확정(§1.4) → 복호화 경로.
3. 둘 다 아니면 → "손상 또는 미지원 파일" 에러(암호문과 손상 파일 메시지 분리).

평문이면 JVM 호출 자체를 스킵한다(성능). 감지 오판 시에도 복호화 `-36` 규약이
안전망. 공식 판별 API(`isEncryptFile`)는 JVM 호출 비용 때문에 미사용.

### 2.4 복호화 어댑터 (`decryptor.py`, `scsl.py`)

```python
class DrmDecryptor(Protocol):
    async def decrypt(self, file_bytes: bytes, filename: str) -> bytes: ...

class PassthroughDecryptor:      # drm.enabled=false — 현행과 동일 동작
class ScslCliDecryptor:          # scsl.jar 래퍼 CLI subprocess 호출
```

`ScslCliDecryptor` 동작:
1. `drm.temp_dir`(전용 디렉터리, 권한 0700)에 입력 파일 기록 — **원 확장자 유지**
   (scsl이 확장자를 체크), in/out 파일명은 상이하게(에러 2002 예방)
2. `java -cp <scsl.jar> <Decrypt.java> <in> <out> <properties> <keyfile> <groupId>`
   실행(단일 소스 실행 — §3.4) — `asyncio.wait_for(…, timeout=drm.timeout_sec)`
   전체 가드
3. 래퍼는 `CreateDecryptFileDAC` 반환값을 stdout·종료코드(0·-36 → exit 0, 그 외
   exit 1)로 반영 — Python이 §1.3 표로 사유를 매핑해 사용자 메시지·로그 구성
4. `-36`은 성공 취급하되 dst 미생성 시 src 바이트 사용(방어) — dst 생성 여부는
   Phase 2 실측
5. `finally`에서 입력·출력 임시 파일 **무조건 삭제** (평문 잔존 금지). 기동 시
   `temp_dir` 잔여 파일 sweep 1회

래퍼는 `SettingPathForProperty()` → `CreateDecryptFileDAC()` 호출만 하는 수십 줄
규모로 유지 — 로직(감지·에러 분류·재시도)은 전부 Python 쪽에 둔다.

### 2.5 라우트 배선 — 두 경로 대칭 (Known Mistakes: 단일/멀티 경로 대칭)

공통 헬퍼 1개를 `/query/file`·`/query/file/stream` **양쪽에 동일하게** 삽입:

```python
async def _resolve_uploaded_bytes(file_bytes, file_ext, filename, config) -> bytes:
    # 평문이면 그대로 반환. DRM이면 복호화 후 반환. 실패 시 HTTP 400/502 예외.
```

- 삽입 위치: 파일 크기 검증 직후, `excel_to_csv_cached` 호출 **이전**. 이후
  파이프라인은 무변경 — state에는 항상 평문 바이트만 들어간다.
- §14 원본 보관(`_store_result`의 `uploaded_file`)도 평문 기준. **재암호화는 하지
  않는다** — 서버는 평문 반환, 사용자 PC에서 열람 시점에 클라이언트 DRM이 자동
  암호화한다(확인됨).

### 2.6 실패 처리 — 침묵 강등 금지

- 복호화 실패: HTTP 400/502 + "DRM 암호화 파일로 확인되나 복호화에 실패했습니다:
  \<사유\>" — 사유는 §1.3 매핑, 실패 컨텍스트(파일명·ret·stderr 요약)는 로그 가시화.
  `excel_to_csv_cached`처럼 warning 후 진행하는 방식 금지.
- `drm.enabled=false` 환경에서 암호문 감지 시: "이 서버는 DRM 해제가 비활성화되어
  있습니다…" — 원인 불명 파싱 에러로 위장되는 것을 차단.

### 2.7 설정 (`src/config.py`)

```python
class DrmConfig(BaseSettings):
    enabled: bool = False               # 기본 비활성 — 미연동 환경 무영향
    java_bin: str = "java"              # Java 1.8+ (반입은 JDK 21)
    wrapper_path: str = ""              # Decrypt.java 절대경로 (단일 소스 실행 — §3.4)
    scsl_jar_path: str = ""             # scsl.jar (소프트캠프 제공) 절대경로
    properties_path: str = ""           # 02_ServiceLinker/softcamp.properties
    key_file_path: str = ""             # 04_KeyFile/keyDAC_SVR0.sc
    group_id: str = "SECURITYDOMAIN"    # 가이드 "수정금지" 리터럴 — 변경 대비 주입 가능
    temp_dir: str = ""                  # 빈 값이면 시스템 temp 하위 자동 생성
    timeout_sec: int = 20
```

- `AppConfig`에 `drm: DrmConfig = Field(default_factory=DrmConfig)` (Known Mistakes:
  임포트 시점 고정 방지). `.env` 인라인 주석 금지, 판정은 pydantic 필드로만.
- KeyManager는 별도 상주 프로세스라 앱 설정 불필요(키 파일 경로만 공유).
- **`softcamp.properties`의 내용은 `.env`로 대체 불가** — 이 파일은 우리 앱이 아니라
  `scsl.jar`가 `SettingPathForProperty()`로 직접 읽는 ServiceLinker 자체 설정이다.
  우리 `.env`에는 그 파일의 **경로**(`properties_path`)만 담는다. 값 기입
  (`LinkSystemIP`/`LinkLoginId`/서비스 ID)은 운영 서버의 properties 파일에서 1회
  수행 — 서버가 1대뿐이라 env→properties 생성 자동화는 과설계로 보류.

### 2.8 보안·감사

- 복호화 시도마다 audit 로그: 사용자 ID, 파일명, 크기, 성공/실패, 사유(ret), 소요
  시간, temp 파일명. 파일 **내용**은 로그 금지.
- **ServiceLinker 자체 로그 보관**: scsl은 `LogPath`·`TransLogPath` 경로에 자체
  로그를 남긴다. 두 경로를 앱 audit 로그와 함께 보존 대상에 포함(보존 기간은 사내
  감사 기준 동일), 로테이션 정책 수립, 배포 문서에 경로·확인 명령 명시. audit
  레코드의 temp 파일명으로 scsl 로그와 대사 가능.
- 복호화 자격(서비스 ID)은 시스템 단위로 발급 — 운영자 개인 계정 재사용 금지.

---

## 3. 환경별 구성

### 3.1 공통 (Phase 0)

1. **ITSM 신청** + 설치본(`ServiceLnk` 디렉터리)·`scsl.jar` 수령
2. **서비스 ID 확인**(기입 전 보안담당자 문의 필수), `LinkSystemIP`·`LinkLoginId` 결정
3. **JDK 21 반입** — 필수는 RHEL 9.6용. Windows용은 래퍼 개발·컴파일 편의용(선택,
   JDK 자체는 DRM 모듈이 아니므로 설치 제약 없음)
4. ~~방화벽~~ — **불필요 확인 완료**(동일 테넌트, :32000·:32002 telnet 연결 검증)
5. `ServiceLnk` 표준 경로 폴더 생성 + 권한 775, `softcamp.properties` 3항목 수정
   (운영 서버 — env 대체 불가 사유는 §2.7)

### 3.2 Windows 개발 PC — DRM 비활성 개발 전용

- DRM 해제 모듈 설치 불가(정보보호부) → 항상 `DRM_ENABLED=false`(Passthrough).
  기존 폼필 개발·테스트는 현행과 동일하게 진행.
- DRM 코드 검증 수단: `SCDS` 픽스처 기반 감지 단위 테스트 + subprocess mock 기반
  어댑터 단위 테스트. **실기 복호화 실측은 불가** — 운영계에서 수행(§3.3, Phase 3).
- **래퍼 빌드 방식**: §3.4의 옵션 중 결정(§8-N9). 어느 안이든 개발 PC에 DRM
  모듈·KeyManager는 불필요하다.

### 3.3 Linux (운영, RHEL 9.6 폐쇄망) — 유일한 실기 환경

- KeyManager(Java 모듈) 기동:
  1. `keymanager_setting.properties` 수정 — **타계열사 Key 미수신 항목 주석 해제
     확정**(본 시스템은 타계열사 접근 금지 → KBBANK 키만 수신)
  2. `register.sh` 실행 → 표시된 서비스 ID를 `softcamp.properties`에 기입
     (**ID 40번부터는 register 표시 ID와 기입 ID가 상이** — 가이드 우측 ID 기준)
  3. `startup.sh` 기동 — **재부팅 시 수동 재실행 필요** → systemd unit 자동화 검토
- **배포 절차: `docs/19_drm_deployment_guide.md`** — 반영 파일 목록(신규 `tools/`
  디렉터리 포함), 확인 grep, systemd(`agent.service`) 재시작 절차, ret별 조치표,
  롤백. 특히 systemd 환경 트랩 3종(PATH에 java 없음 / `PrivateTmp` 격리 /
  키 파일 읽기 권한)을 배포 전 점검 항목으로 명시.

### 3.4 래퍼 빌드 방식 — **확정: 단일 소스 실행 (빌드 없음)**

래퍼는 `SLDsFile` 호출 2회가 전부인 수십 줄 Java 코드로, 1회 작성 후 사실상
불변이다. 별도 빌드 없이 운영 서버에서 소스를 직접 실행한다:

```
java -cp <scsl.jar> tools/drm-wrapper/Decrypt.java <in> <out> <properties> <keyfile> <groupId>
```

- JDK 11+의 single-file source launch(반입 JDK 21 지원). 배포물은 `Decrypt.java`
  소스 1개 — `scsl.jar`의 개발 PC 반출 불필요, 빌드 파이프라인 불필요.
- 매 호출 인메모리 컴파일 비용(수백 ms)은 JVM 기동과 같은 자릿수라 실질 무시.
- 소스 오류는 Phase 3-3(래퍼 단독 실측)에서 즉시 검출. 추후 CI 컴파일 검증이
  필요해지면 `SCSL` 스텁 클래스 기반 컴파일 검증을 추가할 수 있다(선택).

---

## 4. 구현 Phase

| Phase | 내용 | 산출물 | 게이트 |
|---|---|---|---|
| **0** | ITSM 신청 + 설치본·서비스 ID 수령 + JDK 반입 + 잔여 미결(§8) 확인 | 설치본·서비스 ID, D-123 등록 | 설치본 수령·ID 확정 |
| **1** | 감지 + 인터페이스 + Passthrough + 라우트 대칭 배선 + 에러 노출 + audit + 픽스처 | `src/infrastructure/drm/*`, 라우트 헬퍼, `DrmConfig`, 단위 테스트 | DRM 없이도 배포 가능(기본 무영향), arch_check 통과, 암호문 업로드 시 명확한 에러 메시지 |
| **2** | Java 래퍼(`Decrypt.java`) + `ScslCliDecryptor` 구현 (**실기 없이** — mock·픽스처 검증까지) | `tools/drm-wrapper/Decrypt.java`, `scsl.py`, e2e 스크립트(옵트인, 운영계용) | 단위 테스트 그린. 래퍼는 리뷰 검증(실컴파일·실행은 Phase 3-3) |
| **2b** ✔ | **어드민 DRM 진단 도구**(§4.2) — 상태 패널 + 진단 업로드(파일 미반환) | `drm/diagnostics.py`, `/admin/drm/status`·`/admin/drm/verify`, 대시보드 "DRM 연동" 탭 | 완료(2026-08-12): 비활성 상태 정상 렌더 + 진단 단위 테스트 10건 그린 |
| **3** | **운영계 단계적 활성화** (§4.1) | 배포 파일 목록·확인 명령 문서, 운영 `.env`, KeyManager 설치·기동 | 운영계 실측: 상태 패널 → 진단 업로드 → 폼필 전 구간 그린 후 `DRM_ENABLED=true` 전환 |
| 후속 | 개발계 Linux 서버 구축은 **미정**(비중요·미공개 시스템으로 운영 단독 운영도 허용) — 구축이 결정되면 KeyManager 추가 ITSM 후 실측 환경 이관 | — | 그 전까지는 §4.1 단계적 절차가 표준 |

### 4.1 Phase 3 상세 — 운영계가 유일한 실기 환경인 데 따른 단계적 절차

실기 검증을 거치지 않은 코드를 운영에 반영해야 하므로, **활성화 전 단계마다
운영 영향이 없음을 보장**하는 순서로 진행한다:

1. **코드 배포(`DRM_ENABLED=false`)** — Passthrough이므로 기존 동작과 완전 동일.
   배포 확인 grep + 어드민 DRM 탭이 "비활성"으로 정상 렌더되는지 확인(배선 검증)
2. **KeyManager 설치·기동** (§3.3) — 앱과 무관한 별도 프로세스. **상태 패널에서
   키 파일 생성·mtime 확인**(§4.2 — 셸 불필요)
3. **`DRM_ENABLED=true` 전환 + 상태 패널 확인** — 경로 4종·java 버전 OK 판정
4. **진단 업로드 실측**(§4.2) — 어드민 화면에서 암호화 샘플 → ret 0 + PK 시그니처
   + openpyxl/docx 파싱 성공. -36 시 dst 생성 여부, 10MB급, 동시 2~3건도 여기서
   실측(셸 래퍼 단독 실행은 진단 도구가 실패할 때의 백업 수단)
5. **폼필 전 구간** — 실제 양식 업로드 → 산출물 전 칼럼 확인

롤백은 어느 단계에서든 env 한 줄(`DRM_ENABLED=false`)로 즉시 — 코드 롤백 불필요.

Phase 1은 Phase 0과 병행 선행 가능 — 감지·에러 노출만으로도 현재의 "원인 불명
파싱 실패"가 "DRM 파일입니다"로 바뀌어 사용자 혼란이 즉시 준다.

### 4.2 어드민 DRM 진단 도구 (Phase 2b)

**동기**: 실기 환경이 운영계뿐이므로 검증마다 SSH·셸 접근이 필요한데, 폐쇄망에서는
그 절차 비용이 크다. 폼필 파이프라인과 **분리된 지점**에서 DRM 연동만 단독 검증하면
실패 시 원인이 DRM인지 폼필인지 즉시 갈린다. 부수 효과로 운영 중 "지금 DRM이 살아
있는가"를 상시 점검하는 도구가 된다.

**핵심 제약 — 복호화 결과 파일을 반환하지 않는다.** 임의 문서를 평문으로 돌려주는
엔드포인트는 서비스 계정 키로 만든 복호화 오라클이 되며, "양식 처리" 용도로 발급받은
연동의 범위를 벗어난다(§7 비범위 참조).

#### (1) 상태 패널 — `GET /admin/drm/status` (업로드 없음)

- `DRM_ENABLED` 현재 값
- 경로 4종(`wrapper_path`/`scsl_jar_path`/`properties_path`/`key_file_path`)의
  존재·읽기 가능 여부
- **키 파일 mtime과 경과 시간** — KeyManager가 24시간 주기로 갱신하므로 mtime이
  24시간+α를 넘겼으면 KeyManager 이상 신호. 배포 체크리스트의 `ls 04_KeyFile/`를
  화면으로 대체하며, 최빈 실패인 ret 3000/3003/3030의 원인을 즉시 판별한다
- `java -version` 결과(JVM 가용성·버전 ≥1.8), temp 디렉터리 존재·권한

#### (2) 진단 업로드 — `POST /admin/drm/verify` (파일 미반환)

암호화 샘플을 복호화까지 수행하되 응답은 진단 결과만:

- **입력**: 파일명·크기, 감지 결과(`drm`/`plain`/`unknown`), 선두 8바이트 hex
  (`SCDSA002` 육안 확인)
- **실행**: `ret` 원시값, 성공 여부, 소요 시간, temp 파일명(scsl 자체 로그 대사 키)
- **산출물 검증**: 크기, 선두 4바이트 `PK` 여부, **openpyxl/python-docx 실제 파싱
  성공 여부**(시트명·문단 수 수준까지만 — 셀 값은 응답 금지). "복호화됐다"가 아니라
  "폼필이 처리할 수 있는 파일이 나왔다"까지 확인된다
- **실패 시**: ret + §1.3 매핑 사유 + stderr 요약

평문 바이트가 응답에 없으므로 반복 호출로도 문서 내용은 복원되지 않는다.

#### (3) 설계 원칙

- **진단 엔드포인트는 실패도 200 + 구조화된 결과로 반환**한다(일반 업로드 라우트의
  400/502와 반대). 실패가 곧 진단 데이터이며, 화면이 에러로 깨지면 정작 필요한 ret
  값을 못 본다. 활성 경로 UI는 운영계에서 처음 보게 되므로 경로 없음·java 없음·
  타임아웃이 모두 화면에 표시되도록 방어적으로 구성한다.
- 어드민 인가(`require_admin_user`) + 업로드 상한 10MB + xlsx/docx 한정.
- 감사: 기존 `log_drm_decrypt`에 관리자 진단 구분을 추가해 별도 추적.
- 배치: `src/infrastructure/drm/diagnostics.py`(`check_environment`,
  `verify_sample`)에 로직을 두고 라우트는 얇게. UI는
  `src/static/admin/dashboard.html`의 기존 탭 옆에 "DRM 연동" 탭 1개.
- 개발 PC(`DRM_ENABLED=false`)에서도 "비활성" 상태가 정상 렌더되므로 **UI 자체는
  Windows에서 개발·검증 가능**하다.

## 5. 테스트 계획

- **단위**: `detector.py` — 평문(PK)/암호문(`SCDS` 픽스처: 실샘플 선두 64바이트만
  추출, 내용 미포함)/손상/빈 파일.
- **단위**: `ScslCliDecryptor` — subprocess mock으로 성공/실패/타임아웃/임시파일
  삭제 검증. mock 통과 ≠ 프로덕션(Known Mistakes) — Phase 2 실측이 게이트.
- **단위**: 진단 도구(§4.2) — 비활성/경로 없음 상태 렌더, 진단 응답 구조(mock
  decryptor), 감지 분기. 개발 PC에서 검증 가능한 범위까지.
- **e2e(옵트인)**: `RUN_DRM_E2E=1` — 실기 환경(당분간 운영계뿐)에서만. 실측 1차
  수단은 어드민 진단 화면(§4.1-4)이고, e2e는 자동화·회귀용.
- **회귀**: `DRM_ENABLED=false` + 평문 업로드 → 기존 폼필 테스트 전체 그린.

## 6. 성능·제약

- 업로드 상한 10MB, 복호화 타임아웃 20s(설정) — `/query/file` 전체 타임아웃
  (`server.file_query_timeout`) 내 포함 확인 후 필요 시 상향.
- JVM 기동 오버헤드는 Phase 2 실측. `scsl.jar` 크기·동시 호출 제약은 가이드에
  미기재 — Phase 2에서 실측, 문제 시 어댑터 세마포어(동시 1건) 적용.

## 7. 명시적 비범위

- **서버측 재암호화** — 서버는 평문 반환, 클라이언트 DRM이 열람 시점 자동 암호화(확인됨)
- xlsx/docx 외 포맷(pdf 등) 업로드 지원 (현행 라우트가 xlsx/docx만 수용 — 불변)
- DRM 정책 관리·권한 부여 UI (정보보호부 절차로 해결)
- **어드민 복호화 다운로드**(암호문 업로드 → 평문 파일 다운로드) — 검증 목적은
  §4.2 진단 도구로 전부 충족되므로 기본 비범위. 서비스 계정 키를 이용한 범용
  복호화 오라클이 되어 "양식 처리" 용도로 발급받은 연동 범위를 벗어난다. 운영상
  실사용 필요가 생기면 **정보보호부 승인(승인 범위·기간·대상 확장자 명시)을 받은
  뒤** 별도 env 플래그(기본 off)·전용 audit 이벤트·승인 티켓 번호 로깅을 조건으로
  추가한다.

## 8. 미결사항

| # | 항목 | 왜 필요한가 |
|---|---|---|
| N2 | ITSM 신청 절차·리드타임, **서비스 ID 발급**, `LinkSystemIP`·`LinkLoginId` 기입값 결정 (기입 위치는 운영 서버의 `softcamp.properties` — env 대체 불가, §2.7). **코드 구현 비차단** — Phase 3의 KeyManager 설정 시점에만 필요 | properties 기입·KeyManager 설정의 전제 |

해소됨: 방화벽(동일 테넌트 — telnet 검증 완료), 타계열사 Key 항목(주석 해제 확정,
§3.3-1), 개발계 서버(미정 — 운영 단독 허용, §4 후속), 래퍼 빌드 방식(단일 소스
실행 확정, §3.4).
