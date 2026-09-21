# Capability Map: 93 벤치마크 기반 환경변수 최적화·간소화

> **작성일** 2026-09-11 · **계획서** `plans/93-benchmark-driven-config-simplification.md`(2026-09-21 종료 · 잔여는 `plans/109-WIP-config-simplification-consolidated.md`) · **결정 예약** D-211
> **게이트**: G-1~G-13은 사용자 지시(*"구현을 진행하라"*)에 따라 **계획서 권고안으로 진행**한다(`plans/90` 선례).
> **범위**: 실 LLM을 호출하지 않는 Wave만 구현한다(T1~T3 · B0~B5). B6 이후는 실행 단계이지 구현 단계가 아니다.

## 모듈

| Module id | 책임 | Depends on |
|---|---|---|
| `bench-catalog` | 설정 카탈로그 어댑터 — `field_index()` 래핑 · F1 축 후보 필터 · L1 4자 정합(`.env`/`.env.example`/`config.py`/카탈로그) · 가림 대상 판정 | — |
| `bench-probe` | 자식 프로세스 격리 실행 — 경량 config 로드·실효값 에코 · 주입 실효성(L3) 판정 재료 | — |
| `bench-validate` | 트랙 T L1~L4 — 정합 · 기동 안전성 · 주입 실효 · 소비 실증 | `bench-catalog`, `bench-probe` |
| `bench-axes` | 축 **자동 선별**(F1→F2 영향경로) · arm 전개(OFAT/쌍) · `axes.yaml` 생성 | `bench-catalog` |
| `bench-report` | 설정 건강 보고서 · **전건 커버리지 장부** · 판정 문장 5어휘 | `bench-validate` |
| `bench-cli` | 단일 진입 `python -m scripts.bench` · preflight · 프로바이더 판정(§4.4) | 전부 |

**Build order**: `bench-catalog` → `bench-probe` → `bench-validate` → `bench-axes` → `bench-report` → `bench-cli`

## 이번 범위에서 제외 (계획서에는 있으나 후속)

| 제외 | 사유 |
|---|---|
| `bench-runner`(arm 실행·자동 재개) | `plans/94`의 실행 원자 위에 서는 설계(§1.5)인데 94가 미구현이다. 94 착수 후 그 위에 올린다 |
| `bench-optimize`(처분 제안·D-161 4항) | 입력이 성능 리포트(B7 산출)다. 측정 없이 만들면 검증할 수 없다 |
| L5 성능 스위프 | 실 LLM 필요 — 구현이 아니라 실행 단계 |

> 위 셋을 빼도 **T1~T3(전수 설정 검증)과 건강 보고서는 완결**된다. 그것만으로 계획서가 약속한
> *"바꿨는데 왜 안 바뀌지"*(가림 목록)와 고아 키가 드러난다 — 계획서 §8이 *"T1은 선행 0"* 이라고 적은 지점이다.

## 경계 규칙

- **신규 config 필드 0 · 신규 `enable_*` 플래그 0**(D-162). 제어는 CLI 인자와 자식 프로세스 env 주입뿐이다.
- **`src/`를 수정하지 않는다.** 전량 `scripts/bench/`에 둔다(`arch_check.py:290`이 `scripts`를 스캔 제외).
- **`.env`·`.encenv`·`config.py`를 쓰지 않는다.** 읽기만 한다(계획서 V5).
- 산출물은 `results/bench/<run_id>/` 한 폴더에 모은다.
