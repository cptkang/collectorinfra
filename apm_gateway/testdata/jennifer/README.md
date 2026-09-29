# 제니퍼 로컬 검증 환경 (plans/87 §0.8 · J0-L)

제니퍼 5 서버(데이터 서버 + 뷰 서버)와 에이전트를 붙인 샘플 WAS를 로컬 Docker로 띄워
Open API 계약을 실측한다. **로컬 전용**이다. 운영 뷰 서버에서는 이 스크립트를 쓰지 않는다.
실측 결과는 `plans/87` §0.10에 있다.

## 구성

| 서비스 | 이미지 | 호스트 게시 | 내부 IP |
|---|---|---|---|
| `jennifer-server` | `eclipse-temurin:21-jdk` + 설치본(볼륨에 1회 풀기) | `127.0.0.1:17900` → 7900(뷰·Open API) | 172.29.87.10 (5000은 비게시) |
| `sample-was` | `tomcat:9.0-jdk11-temurin` + 제니퍼 Java 에이전트 | `127.0.0.1:18080` → 8080 | 172.29.87.20 |

- 샘플 WAS가 JDK 11인 이유: 뷰 서버 5.7.0.1이 배포하는 에이전트는 5.5.2.5이고, 이 에이전트는 JDK 17에서 JVM 기동을 실패시킨다(2026-09-29 실측).
- 샘플 앱: `/sample/slow.jsp?ms=`, `/sample/heap.jsp?mb=`(`reset=1`로 해제), `/sample/error.jsp`.

## 준비 (사용자)

1. 설치본 `jennifer-server-<버전>.zip`을 **저장소 밖** 디렉터리에 둔다. 라이선스를 받았으면 같은 디렉터리에 `license` 파일로 둔다.
2. `.env.example`을 `.env`로 복사하고 `JENNIFER_DIST_DIR`에 그 디렉터리의 절대 경로를 적는다.
3. Docker Desktop 자원: CPU 2개 이상, 메모리 8GB 이상.

## 기동 · 초기 설정 · 실측

```bash
cd apm_gateway/testdata/jennifer
docker compose up -d --build            # 서버가 healthy가 된 뒤 WAS가 뜬다

# 최고관리자 생성 + Open API 토큰 발급 (값은 저장소 밖에 보관)
JENNIFER_LOCAL_ADMIN_ID=jadmin JENNIFER_LOCAL_ADMIN_PW='<임의 값>' \
  python3 scripts/bootstrap_local.py > /path/outside/repo/jennifer-token.env

# Open API 실측 (결과: recorded/raw/<시각>/ — 커밋 제외)
set -a; . /path/outside/repo/jennifer-token.env; set +a
python3 scripts/probe_openapi.py
```

라이선스가 없으면 에이전트 로그에 `Rejected by data server. reason=no license found`가 남고,
인스턴스 단위 API는 HTTP 500 `{"exception":{"message":"1000 Domain is not connected"}}`을 돌려준다.

## 녹화 하네스 (`scripts/record_openapi.py`)

§5.2(e) 허용목록의 GET만 호출한다. 허용목록 밖 경로·쿼리 `token`·허용 밖 쿼리 키는 네트워크
호출 전에 거부한다. 도메인 → 인스턴스 → 트랜잭션 순으로 식별자를 찾아가며 녹화하고, 응답을
마스킹해 출처 표지와 함께 `recorded/<source>/`에 저장한다. 오류 응답도 오류 모델 fixture로 남긴다.

```bash
set -a; . /path/outside/repo/jennifer-token.env; set +a
JENNIFER_VERSION=5.7.0.1 python3 scripts/record_openapi.py --source local-docker
# 운영 뷰 서버(J0-O): 호스트명·IP를 같은 입력 → 같은 가명으로 바꾼다
python3 scripts/record_openapi.py --source ops-masked --base https://<뷰 서버>
```

- 파일 이름: `GET_<경로>__<variant>[__<label>].json`. variant는 `ok` · `domain_not_connected` ·
  `missing_param` · `status_<코드>`이다. `index.json`이 목록과 적용한 마스킹 규칙을 담는다.
- 마스킹(`scripts/masking.py`): 비밀·개인정보 키, 이메일·휴대폰·주민번호 값, SQL 리터럴,
  URL 쿼리 값을 가린다. `profile.txt`는 SQL 줄의 숫자만 가리고 시각·경과 ms는 남긴다.
- `--keep-raw`를 주면 마스킹 전 원본을 `recorded/raw/`(커밋 제외)에 남긴다.
- 현재 `recorded/local-docker/`는 라이선스 없는 상태의 녹화본이다(21건 · 대부분 `domain_not_connected`).
  평가판을 적용하면(J0-L-b) 다시 녹화한다.

## 목 Open API 서버 (`scripts/mock_openapi.py`)

녹화 fixture를 응답 원천으로 쓰고, 실측으로 확인한 실서버 동작을 흉내 낸다. 로컬 실서버와 같은
40건 프로브를 돌려 **상태 코드·응답 모양 불일치 0건**을 확인했다(2026-09-29).

```bash
python3 scripts/mock_openapi.py --port 17901 --token mock-token            # 녹화 상태 그대로 재생
python3 scripts/mock_openapi.py --port 17901 --token mock-token --mode connected     # ok fixture만
python3 scripts/mock_openapi.py --port 17901 --token mock-token --mode disconnected  # 라이선스 없음 흉내
```

| 흉내 내는 실서버 동작 | 목 서버 |
|---|---|
| 인증 | Bearer · 쿼리 `?token=`도 받음(게이트웨이가 거부해야 한다) · 없으면 401 |
| 필수 파라미터 누락 | 500 `{"exception":{"message":"Required request parameter ..."}}` |
| `profile.txt` + `Accept: application/json` | 404 |
| 허용목록 밖(민감 GET·쓰기 경로·`.xml`·POST 변형) | 실측과 같은 상태·모양으로 응답(개인정보 자리는 가짜 값) + **접근 기록** |

제어 경로(인증 없음 · 127.0.0.1 전용): `GET /__mock/hits`(접근 기록 — `allowlisted`·`query_token`
표지) · `POST /__mock/events`(EventData 13필드로 이벤트 주입 — 폴러 커서·멱등 검증용, `connected` 모드) ·
`GET /__mock/usage` · `POST /__mock/mode` · `POST /__mock/reset`.

한계: 실제 EVENT 발생과 필드 변형은 재현하지 못한다. 실데이터 모양은 평가판 녹화(J0-L-b) 뒤에 채워진다.

## 테스트

```bash
.venv/bin/python -m pytest apm_gateway/tests -q     # 루트 pytest 수집 경로 밖 — 명시 실행
```

## 정지 · 정리

```bash
docker compose down        # 컨테이너만 내린다(설치·관리자·토큰은 볼륨에 남는다)
docker compose down -v     # 볼륨까지 지운다(다음 기동 때 설치본을 다시 풀고 관리자를 다시 만든다)
```

## 주의

- WAS 서비스 환경변수에 `JENNIFER_*` 이름을 쓰지 않는다. 에이전트가 이 접두의 환경변수를 설정으로 읽는다.
  `JENNIFER_VIEW_URL` 등 4개를 두었을 때 에이전트가 `server_address` 대신 127.0.0.1:5000으로 접속했다.
- 컨테이너가 비정상 종료되면 `db.lock`이 볼륨에 남는다. 서버 entrypoint가 기동 때 지운다.
- 기동 전 점검(Bootstrap Check)은 기본으로 켜 둔다. 5.7.0.1은 Docker를 경고만 남기고 기동한다.
  자원이 모자라 기동이 막힐 때만 `.env`에 `DISABLE_BOOTSTRAP_CHECK=1`을 둔다(로컬 한정).
