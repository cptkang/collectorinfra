---
name: refactoring
description: 동작 불변 구조 리팩터링(모듈 분할·함수 분해·중복 제거·SSOT 일원화·패턴 도입)을 이 저장소 규약(D-336·D-331·D-161·D-089·D-139)대로 계획·실행·검증한다. 「리팩터링」「코드 스멜」「God module 쪼개기」「중복 제거」「SSOT로 모으기」「디자인 패턴 적용」「SOLID」「plans/166 Wave 진행」처럼 동작을 바꾸지 않고 구조를 바꿀 때 쓴다. 동작이 바뀌는 변경(보안 강화·기본값 통일)에는 쓰지 않는다 — 그건 별도 계획이다.
---

# 동작 불변 구조 리팩터링

리팩터링은 **관찰 가능한 동작을 바꾸지 않고** 내부 구조를 바꾸는 일이다(Fowler 2018). 이 저장소에서는 「동작 불변」을 말로 주장하지 않고 **게이트로 실증**한다(D-134 선례 → D-336 일반화). 근거 문헌은 `references/literature.md`, 스멜→처방 표는 `references/smell-catalog.md`.

## 언제 쓰고 언제 쓰지 않는가

- 쓴다: 거대 모듈·함수 분해, 쌍 경로 복붙 공통화, 상수·판정·설정 사본 일원화, 순환 import 해소, `plans/166`(및 그 후속 W3~W5 계획) 작업.
- 쓰지 않는다: 응답·차단·기본값·문구·판정 신호가 바뀌는 변경. **D-336 ⑥** — 행동 변경은 리팩터 커밋·계획에 섞지 않고 별도 계획으로 뺀다(성공 기준이 정반대: 스냅샷 불변 vs 변경 단언). 보안 강화는 `secure-coding` 스킬과 `plans/163`.

## 시작 전 확인 (5분)

1. 대상 영역 결정 grep: `grep -n '<키워드>' docs/02_decision.md` → 걸린 D 파일만 읽는다. 최소 확인: **D-336**(리팩터 규약) · **D-331**(게이트 조정) · **D-161**(코드에 분기가 있다는 사실만으로 죽은 경로 판정 금지 — 사다리 2·3·4단과 플래그 분기는 제거 대상 아님) · **D-089**(DB별 SQL은 `src/db_adapters/{db}/` 격리 · 노드의 어댑터 모듈 직접 import는 **허용** · 두 번째 어댑터 전까지 훅 확대 금지) · **D-139/D-274**(패키지 경계 — `mcp_server`·`apm_gateway`·`sre_agent`와 양방향 import 0).
2. `plans/166`에 해당 스멜 ID(X·A·O·N·I·G·M)가 있으면 그 행의 처방·검증 칸을 따른다. 없는 스멜이면 아래 「측정」부터.
3. 실수 이력: `grep -n '^### ' docs/18_known_mistakes.md | grep -i '<키워드>'`.
4. 계획 시작 SHA를 기록한다(`git rev-parse HEAD`). 기준선 대조는 `git worktree add --detach <dir> <SHA>` — stash 금지.

## 절차

### 1. 측정 — 스멜은 숫자로 고른다

```bash
python .claude/skills/refactoring/scripts/smell_metrics.py [경로…] --top 20 [--getattr] [--silent]
python scripts/arch_check.py --ci --verbose      # 계층 방향·팬인/팬아웃
```
- 근사 cc·줄 수만으로 고르지 않는다. **변경이 몰리는 곳**(같은 파일을 여러 계획이 고침, docstring에 계획 번호가 누적, Known Mistakes에 같은 영역 사고 반복)이 우선이다 — 스멜과 변경·결함 경향의 연관은 실증돼 있지만(Khomh 2012 · Palomba 2018) 모든 스멜이 해로운 것은 아니다.
- 이름이 같다고 중복이 아니다. `_norm`·`_normalize`처럼 본문이 다른 개념은 통합하지 않는다. 중복은 본문(AST·정규화 토큰)으로 판정한다. 의도된 복제(포크·실험 경로)는 그대로 둘 수 있다(Kapser & Godfrey 2008).

### 2. 안전망 먼저 — 특성화 테스트 (D-336 ⑤)

제품 코드보다 **먼저, 별 커밋(가능하면 별 Wave)으로** 현행 동작을 고정한다(Feathers 2004 — characterization test). 기대값은 「맞는 값」이 아니라 「지금 값」이다.

| 대상 | 고정 수단 |
|---|---|
| API·SSE 응답 | 페이로드 **키 집합** 스냅샷(값은 비결정이면 제외) |
| LangGraph 배선 | 플래그 매트릭스별 `graph.get_graph()` 노드·엣지 집합 |
| 판정 함수(정책·라우팅) | 입력 조합 × 출력(tier·reason 등) 골든 |
| 프롬프트 | `python scripts/prompt_render_diff.py --ci` · `tests/test_prompt_render_matrix.py` sha256 |
| 시맨틱 모델 | `python scripts/catalog_diff.py --ci` |
| Redis 키 | fakeredis `scan_iter` 키·값 스냅샷 |
| MCP 도구 | `list_tools()` name·inputSchema·description JSON |
| 노트·체인 순서 | 호출 순서 리스트 단언 |

이후 단계에서 **스냅샷을 갱신해야 한다면 그 변경은 리팩터가 아니다** — 되돌리고 별도 계획으로 뺀다.

### 3. 작은 단계 — 이동과 분해를 섞지 않는다 (D-336 ⑦)

순서: **(a) 순수 이동(Move) → (b) 이름 정리 → (c) 로직 분해(Extract)**. 각 단계는 별 커밋이고 커밋 하나 = 스멜 ID 하나. 리팩터 자체가 결함을 들이는 경우가 실증돼 있다(Bavota 2012 — 유형별로 위험이 다름 · Kim 2012 — 실무자가 회귀 버그·병합 충돌을 위험으로 꼽음 · Murphy-Hill 2012 — 실무자는 리팩터와 기능 변경을 섞는 경향).

**이동 규칙**
- **재노출 shim 1사이클**(D-336 ①): 옮긴 이름을 원 모듈에서 `from .new import name as name`으로 다시 노출한다. shim 제거는 **다음 계획**에서 `grep -rw <이름>` 0 확인 후.
- **patch seam 보존**(D-336 ②): 옮기기 전에 `grep -rn "monkeypatch.setattr(.*<원모듈>\|patch(\"<원모듈 경로>" tests/`. 원 모듈 전역을 읽거나 원 모듈에서 patch되는 심볼을 **호출하는** 함수는 원 모듈(Facade)에 남긴다. 기준: **테스트 파일 수정 0 · 순환 import 0**. (재노출만으로는 `monkeypatch.setattr(old_mod, "X", …)`가 새 모듈의 참조를 바꾸지 못한다.)
- **`_` 공유 함수**(D-336 ③): 모듈 밖에서 import되는 `_name`은 공개명으로 바꾸고 `_name = name` 별칭을 1사이클 둔다.
- **overfit 기준선은 경로 키**(D-336 ④): 리터럴이 든 코드를 옮기면 새 경로가 신규 유입으로 걸린다. `scripts/overfit_baseline.json`에서 **자기 이동분만** 원 경로 항목을 새 경로로 옮긴다. `--update-baseline` 전면 재생성 금지. `overfit_check.py` EXCLUDE 대상 파일을 쪼개면 새 파일도 EXCLUDE에 대칭 추가.
- **계층 매핑**: `arch_check.py`는 최장 prefix 매칭 — 새 하위 패키지는 부모 계층을 상속한다. 다른 계층으로 내리는 이동은 `MODULE_LAYER_MAP`을 먼저 확인한다(예: `db_adapters/`·`tools/`·`semantic/`은 infrastructure가 아니라 **application**).

**분해 규칙**
- Extract Function은 단계명을 붙이는 일이다. 분기 순서·예외 처리·로그 문구를 바꾸지 않는다(로그 순서는 caplog로 고정).
- 조기 반환 사슬을 표로 바꿀 때 **평가 순서를 그대로** 옮긴다(예: D-048 심각도3 절대 PAGE가 앞).
- 클로저 캡처 변수가 많으면 먼저 작은 `@dataclass` 컨텍스트로 묶고(Introduce Parameter Object) 그다음 쪼갠다.

### 4. 원칙·패턴 적용 기준 — 필요한 곳에만

판단 순서: **함수 → 모듈 → 데이터(dict/tuple 표) → 클래스 → 패턴**. 앞 단계로 풀리면 멈춘다. 깊은 모듈(작은 인터페이스·큰 구현)을 선호하고 얕은 래퍼 층을 늘리지 않는다(Ousterhout 2018).

| 원칙/패턴 | 이 저장소에서 쓰는 조건 | 쓰지 않는 조건 |
|---|---|---|
| **SSOT** | 같은 값·판정·키가 2벌 이상이고 실제로 어긋난 적이 있거나 어긋나면 결과가 갈린다 | 이름만 같고 의미가 다르다 |
| **SRP** (Extract Module/Class) | 한 파일에 서로 다른 이유로 바뀌는 책임 구간이 **줄 범위로** 구분된다(발산 변경) | 길기만 하고 한 책임이다 |
| **OCP** | 확장 축이 실제로 열려 있다(새 항목이 계속 추가됨) → 표 주도 | 축이 닫혀 있다(단 4개·보기 5종) |
| **DIP** | 하위 계층이 전역 `load_config()`·`os.getenv`를 직접 읽어 테스트가 monkeypatch에 의존 → 인자·생성자 주입 | 구현이 하나뿐인데 Protocol을 새로 만든다 |
| **ISP/LSP** | 공개 인터페이스를 쪼갤 때 소비처별 실제 사용 메서드 집합이 다르다 | 추측성 인터페이스 |
| **순서 있는 규칙 표**(Chain of Responsibility의 함수 튜플형) | 순서가 의미를 갖는 if/early-return 사슬, 단계 번호 중간 삽입이 반복됨 | 규칙 2~3개 |
| **표 주도 dispatch**(경량 Registry) | 분기 축이 닫힌 어휘(`kind`·노드명·`target`)인 if/elif 사슬 | 분기마다 공유 상태가 크게 다름 |
| **Facade 완결** | Facade가 이미 있는데 외부가 private 멤버로 우회한다 → 공개 메서드 추가 후 닫기 | 새 Facade 층 신설 |
| **Composition** | 거대 클래스가 키 공간·하위 책임별로 메서드 군을 가진다 → 하위 객체 보유 + 위임 | 상속·믹스인으로 쪼개기 |
| **TypedDict 선언** | dict 상태의 키 오류 사고가 반복된다 — 런타임 불변·mypy만으로 잡는다 | `AgentState` 전면 dataclass/pydantic 전환(체크포인터 델타 병합과 충돌) |
| Strategy·Template Method·Abstract Factory | 구현 3개 이상이 **런타임에** 갈아끼워지고 공유 상태가 작다 | 위 조건 미충족 — 함수 인자·dict로 충분 |

GoF 패턴은 「반복되는 설계 문제의 이름」이지 목표가 아니다(Gamma 1994). 패턴 도입을 제안할 때는 **도입하지 않는 대안과 그 기각 근거**를 같이 적는다(`plans/166` §4.2 형식).

### 5. 금지 — 이 저장소에서 리팩터로 하지 않는 것

- 사다리 단(1~4)·기능 플래그 분기 제거(D-161 · D-251). 「코드에 남아 있다」는 사실만으로 죽은 경로라 판정하지 않는다 — 폐기는 D-161 ② 4항 실측을 붙인 별도 결정.
- `mcp_server`·`apm_gateway`·`sre_agent`와 공유 라이브러리 신설(D-139·D-274). 계약은 문서 + 계약 스냅샷 테스트로 SSOT를 대신한다.
- DB 방언 Strategy·어댑터 훅 확대(D-089 · Plan 63 §9).
- MCP 도구 이름·inputSchema 변경(외부 계약).
- 프롬프트 바이트 변경(바뀌면 리팩터가 아니다).
- `except Exception` 일괄 치환. 침묵 핸들러 군집에 로그 한 줄을 넣는 것까지만(CLAUDE.md 「침묵 폴백 금지」).
- `getattr(cfg, "필드", 기본)` 폴백 신규 작성 — 존재하는 필드는 `cfg.<그룹>.<필드>` 직접 접근(D-331 ②).

### 6. 검증과 보고

1. 자기 테스트 + 특성화 스냅샷 통과(스냅샷 무갱신).
2. `python scripts/regress.py --base <계획 시작 SHA> --no-tests --files <바꾼 파일…>` (Wave 중간). 계획 끝 1회 모듈 단위 `regress.py --base <SHA> --files <누적…>`. 전체 회귀는 사용자 요청 시만(D-303). 도구가 낸 `[전체 회귀 권고]` 블록은 보고에 그대로 옮긴다.
3. `python -c "import src.graph"` 등 import 스모크 · 순환 확인.
4. arch_check 경고 수는 **D-331 ① 적용 후 기준선**(43)과 비교한다 — 규칙 조정으로 줄어든 수를 개선으로 세지 않는다.
5. 보고: 바꾼 파일 · 스멜 ID · 이동/분해 구분 · 게이트 결과(범위 줄 그대로 — `범위: 모듈 단위 — 전체 미실행`) · 남긴 shim·별칭 목록(다음 계획의 제거 대상) · 매뉴얼 미반영 사유(「사용자 접점 변경 없음」 · D-255).

## 독립 패키지

`mcp_server`·`apm_gateway` 테스트는 `cd <패키지> && ../.venv/bin/python -m pytest`, `sre_agent`는 자체 `.venv`. 루트 수집 밖이라 `regress.py`가 돌려 주지 않는다 — 직접 돌리고 보고에 적는다.
