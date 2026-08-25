# noise_gate — 알람 노이즈 캔슬링 패키지

폴스타 알람의 **노이즈 게이트**(4-티어 라우팅·dedup·상관·억제)와 알람 분석·통보 파이프라인을
담는 최상위 독립 패키지다. 종전 `src/alarm/`에서 분리했다(Plan 66 · D-139).

## 구조

```
noise_gate/
├── alarm_server/     # 폴스타 TCP 수신 → Redis XADD (독립 프로세스 진입점)
├── domain/           # 순수 판정 로직(정책·상관·이상탐지·지문·시그니처)
├── application/      # 노드·워커·분류기 (LangGraph 노드 = application)
├── infrastructure/   # Redis·폴스타 API·decision_store·SSE·MCP 클라이언트
├── orchestration/    # alarm_graph (그래프 조립)
├── prompts/          # LLM 프롬프트
├── tests/            # 이 패키지 전용 테스트
├── scripts/          # 목업 이벤트 생성기·시나리오 테스트
└── testdata/         # 이 패키지 전용 픽스처
```

수신부 기동:

```bash
python -m noise_gate.alarm_server   # TCP 9100 수신 → Redis Stream 'alarm:raw'
```

> **폴스타 템플릿 주의(D-163)**: `${severity}`는 한글 라벨(해제/주의/경고/심각)로 렌더링되므로 템플릿에
> 반드시 `"severity":"${severity}"`처럼 따옴표로 등록한다. 워커·API는 `domain/severity.py`로 정규화하고,
> 처리 실패 건은 ACK 전에 `alarm:dead` 스트림에 보관된다(`XRANGE alarm:dead - + COUNT 20`).

계층 규칙은 본체와 동일하며 `scripts/arch_check.py`가 `noise_gate.*` 매핑으로 함께 검사한다
(`alarm_server`는 수신·적재=infrastructure / 기동부=entry / 설정=config로 매핑).

## 이 패키지로 옮기지 않은 것 — `src/api/routes/alarm.py`

알람 REST 라우트(1,420줄)는 알람 전용 표면이지만 **본체 `src/api/`에 남긴다**. 이 라우트는
본체 FastAPI 앱의 인증 계층(`src.api.dependencies` — `require_user`·`alarm_zones_for_user`·
`resolve_stream_user`)에 묶여 있어, 옮기면 `noise_gate → src.api` 의존이 새로 생긴다. 현재는
`src/api/server.py`가 라우터를 mount하는 **한 방향**(앱 → 패키지)인데, 옮기면 패키지가 앱의
세션·JWT 계층을 되짚는 역방향이 더해져 결합이 오히려 나빠진다. 라우터 등록은 앱 조립(entry)의
일이므로 현 위치가 맞다. 인증을 mount 지점 주입으로 바꾸는 재설계가 선행되면 재검토 대상이다.

## 다른 독립 패키지와의 차이 — 같은 프로세스에서 돈다

`sre_agent/`·`mcp_server/`는 **별도 venv·별도 프로세스**이고 통신이 MCP 계약뿐이라 양방향
import가 0이다. `noise_gate`는 다르다:

- `src/api/server.py`가 `AlarmWorker`를 **같은 프로세스에서 기동**한다(D-048).
- `src/api/routes/alarm.py`가 알람 REST API를 노출하며 이 패키지를 직접 소비한다.
- 본체와 **같은 venv·같은 LangGraph/LLM/config 스택**을 공유한다.

따라서 `src/ → noise_gate/` 방향 의존은 **설계상 남는다**(entry 계층이 조립). 이를 없애려면
워커를 별도 프로세스로 떼고 SSE 브리지를 프로세스 간 계약으로 바꿔야 하며, 그건 D-048/D-049
재설계가 선행돼야 하는 별건이다.

역방향(`noise_gate → src`)은 최소로 유지한다 — 현재 `src.config`·`src.llm`·`src.utils`·
`src.routing`뿐이다.

## 레이아웃이 평탄한 이유 (컨테이너/패키지 2단 중첩 아님)

`sre_agent/sre_agent/`처럼 2단으로 두면 리포지토리 루트에서 `import noise_gate.domain`이
**해석되지 않는다** — 바깥 `noise_gate/`가 네임스페이스 패키지로 먼저 잡혀, editable 설치로
sys.path에 컨테이너를 주입해야만 동작한다(실측 확인). `src/api`가 런타임에 이 패키지를
import하는데 그 해석이 설치 상태에 의존하면 안 되므로(과거 "stale 비-editable `.venv/src`
로드" 사고 이력), 컨테이너 없이 **이 디렉토리 자체를 패키지**로 둔다. `src/`와 동일한 방식이다.

## 테스트

```bash
# 본체와 함께 (pyproject testpaths에 포함됨)
pytest

# 이 패키지만
pytest noise_gate/tests -q
```

## 예외 — 공유 Docker PG 픽스처

`testdata/pg/init/06_plan52_noise_fixtures.sql`은 노이즈 게이트 데이터지만 본체 `testdata/`에
남겨 둔다. 해당 디렉토리는 docker-compose가 `/docker-entrypoint-initdb.d`로 **통째 마운트**해
파일명 순서로 실행하며, 텍스트투SQL 골드 픽스처(07~09)가 같은 순서에 의존한다 — 떼어내면
픽스처 기동이 깨진다.
