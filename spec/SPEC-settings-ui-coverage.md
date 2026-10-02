# Spec: 설정 UI 커버리지 완결 (Plan 83 트랙 C)

> 요구·실측 근거의 정본은 **`plans/83` §3**, 기반 설계는 **`plans/68`**(설정 웹UI 전면 개편)이다.
> 모듈 id: **`settings-ui-coverage`** (`CAPABILITY-MAP-83.md`) · 착수 결정: **D-179**(예약 등재 완료).

## Objective

"`.env`의 옵션들을 관리자 페이지에서 모두 UI로 정의할 수 있는가"에 대한 **실측 답은 '그렇다'**이다
(2026-08-28 `field_index()` 직접 실행):

```
카탈로그 290필드 (NOISE_ 92 · 시크릿 12 · 미소비 20)
.env 139키          → 카탈로그 미포함 0건
.env.example 186키  → 카탈로그 미포함 0건
```

Plan 68이 채택한 pydantic 인트로스펙션 SSOT(D-129)가 의도대로 동작 중이고, 파일에 없는 config
전용 필드까지 UI에 나온다(290 > 186). **따라서 이 모듈은 "빠진 옵션을 채우는" 일이 아니다.**

**이 스펙이 만드는 것**: 남은 갭 3건의 해소.

- **C-1 섹션 미분류 8키** — `SECTION_BY_KEY`(`settings_catalog.py:70`)에 누락돼 소제목 없이 렌더된다
  (`admin.js:404`는 `item.section`이 있을 때만 소제목을 만든다). 발견성이 떨어진다.
  대상: `NOISE_ANOMALY_METRIC_SOURCE_MAP_CSV` · `NOISE_ANNOTATION_LLM_*`(4) · `NOISE_INVESTIGATION_FOLLOWUP_*`(3)
- **C-2 경계 밖 설정** — `mcp_server/.env`(8키) · `mcp_server/config.toml` · `sre_agent/.env.example`(5키)는
  **별도 venv·별도 프로세스**라 본체 카탈로그 밖이다. 화면에 아무 설명이 없어 "빠진 것"처럼 보인다.
- **C-3 시크릿 안내** — ~~사전 안내가 약하다~~ → **구현 중 실측 정정(2026-08-28)**: `admin.js:470`이 이미 `🔒 .encenv 관리` 배지와 "`.env` 수정은 반영되지 않습니다" 툴팁을 상시 노출하고 있었다. **추가 작업 불요** — 계획 단계의 판단이 틀렸다.

**하지 않는 것**: 별도 패키지 설정의 **웹UI 쓰기**. 본체가 다른 프로세스의 파일 시스템·재기동을
전제하게 되어 D-139(패키지 경계·양방향 import 0)를 침식한다. "어디서 관리하는지"를 명시하는 것이
옳은 해법이다. 미소비 20키의 정리(코드에서 읽지 않는 필드 제거) 역시 별건이다.

## Tech Stack

기존 스택. Python 3.11 · pydantic v2 인트로스펙션 · 바닐라 JS. **신규 의존성 0**.

## Commands

```bash
.venv/bin/python -m pytest tests/test_api/test_settings_catalog_sections.py -q
.venv/bin/python -m pytest tests/test_api/ -q -k settings
.venv/bin/python -m pytest tests/ noise_gate/ -q          # 전건 회귀
.venv/bin/python scripts/arch_check.py --ci

# 커버리지 실측(회귀 확인용 수동 스크립트)
.venv/bin/python -c "from src.api.settings_catalog import field_index, SECTION_BY_KEY; \
idx=field_index(); print(len(idx), len([k for k in idx if k.startswith('NOISE_')]), \
len([k for k in idx if k.startswith('NOISE_') and k not in SECTION_BY_KEY]))"
```

## Project Structure

```
src/api/settings_catalog.py                  → SECTION_BY_KEY 구획 매핑(SSOT)
src/static/admin/dashboard.html              → 설정 탭 마크업(경계 안내 블록 추가 위치)
src/static/js/admin.js                       → 섹션 소제목 렌더(:404) · 시크릿 배지
tests/test_api/test_settings_catalog_sections.py → 섹션 분류 회귀 테스트(기존 파일 확장)
```

## Code Style

`SECTION_BY_KEY`는 키 → 한국어 구획명 평면 dict다. 기존 항목의 구획명 표기를 그대로 따른다.

```python
SECTION_BY_KEY: dict[str, str] = {
    # ... 기존 항목 ...
    # (Plan 83 C1) 미분류 해소 — 구획명은 인접 키의 표기를 따른다
    "NOISE_ANOMALY_METRIC_SOURCE_MAP_CSV": "이상탐지(동적 baseline)",
    "NOISE_ANNOTATION_LLM_CLASSIFICATION_ENABLED": "주석 LLM 분류",
}
```

## Testing Strategy

- 기존 `tests/test_api/test_settings_catalog_sections.py`를 확장한다.
- **핵심 테스트(재발 방지)**: `field_index()`의 모든 `NOISE_` 키가 `SECTION_BY_KEY`에 존재함을 단언.
  신규 플래그가 늘 때 자동으로 실패한다 — `alarm-view-level`이 추가할 `NOISE_SSE_SUPPRESSED_ENABLED`도
  이 테스트에 걸린다(모듈 간 의존의 실체).
- 커버리지 회귀: `.env`·`.env.example` 키가 전부 카탈로그에 있음을 단언(현재 0건 누락을 고정).

## Boundaries

- **Always**: 구획명은 기존 표기 관례를 따름 · 변경 후 전건 회귀 + `arch_check --ci`
- **Ask first**: 시크릿 판정 규칙 변경 · 카탈로그 제외 목록(`UNCONSUMED_KEYS` 등) 변경 ·
  기존 구획명 일괄 개명
- **Never**: 별도 패키지 설정 파일을 본체 UI에서 **쓰기** · 시크릿 값을 응답에 노출 ·
  카탈로그에서 필드를 임의 제거

## Success Criteria

- [ ] `field_index()`의 모든 `NOISE_` 키가 `SECTION_BY_KEY`에 존재(미분류 **0건**)
- [ ] 신규 `NOISE_` 키를 매핑 없이 추가하면 섹션 테스트가 **실패**한다(재발 방지 동작 확인)
- [ ] `.env`·`.env.example` 키의 카탈로그 미포함이 **0건**임을 테스트가 고정
- [ ] 설정 탭에 "이 화면 범위 밖 설정" 안내(파일 경로·관리 주체·재기동 방법)가 표시된다
- [x] 시크릿 필드에 `.encenv` 관리 배지가 상시 노출된다 — **기존 구현으로 이미 충족**(실측 정정)
- [ ] 기존 설정 조회·저장 동작 회귀 0 · `pytest` 전건 통과 · `arch_check --ci` 0위반

## Open Questions

없음.
