# AIOps 선진사례 벤치마크 자료 모음

collectorinfra AIOps 고도화를 위한 선진 솔루션 조사·분석 자료. 상세 출처·인덱스는 `aiops_research_dossier.md` 참조.

## 1. 벤치마킹
- `aiops_benchmark_brief.html` — 9개 플랫폼 × 10개 역량 벤치마크 + 7개 설계 패턴
- `aiops_capability_heatmap.png` — 9×10 역량 히트맵
- `aiops_platform_comparison.csv` — 플랫폼 상세 비교(분류·메커니즘·강점·한계·적합대상)

## 2. 도입 사례
- `aiops_adoption_casestudy_brief.html` — 13개 기업 도입 사례·방법론·폐루프 아키텍처 + collectorinfra 3-Wave 권고
- `aiops_adoption_cases.csv` — 13개 기업(산업·솔루션·적용범위·방법·정량효과)
- `aiops_adoption_effects.png` — 도입 효과 정량 성과 차트

## 3. 기능별 구현 메커니즘
- `aiops_mechanism_brief.html` — 기능별 판단기준·알고리즘·실제 구현방법 분해
- `aiops_mechanism_decomposition.csv` — 10개 기능 메커니즘 분해표
- `aiops_noise_mechanism.png` — 노이즈 캔슬링 신호→판정→라우팅 다이어그램

## 4. 벤더별 구현 기준 (구현 표준 문서)
- `aiops_vendor_implementation_reference.html` — 7기능 × 27항목 벤더별 구현 메커니즘 + collectorinfra 구현 기준
- `aiops_vendor_implementation_reference.csv` — 구현 메커니즘·알고리즘·전제조건 27행

## 5. 조사 자료 종합
- `aiops_research_dossier.md` — 조사 개요·결론·산출물 인덱스·주제별 출처 120건
- `aiops_research_sources.csv` — 출처 120건(주제·제목·URL)

## 6. ML 기반 장애 진단·예측·RCA 문헌 (2026-09-17 · `plans/101`)
- `ml_rca_literature.md` — RCA·장애 진단 49건 + 보조 22건(트레이스 없는 메트릭 RCA·벤치마크 비판·DB/APM 진단·변경 상관·LLM 에이전트), 원문 대조 등급 V1~V3
- `ml_anomaly_prediction_literature.md` — 이상탐지·장애 예측·용량 예측·시계열 파운데이션 모델 60항목 + 평가 프로토콜 12조
- `ml_library_vendor_survey.md` — Python 라이브러리 버전·라이선스 실측 · HolmesGPT 확장 지점 · 제니퍼·엑셈·와탭 AI 기능 · 데이터셋
- `ml_kaggle_competition_survey.md` (2026-09-20 · `plans/101` v3) — Kaggle 경기 10건·데이터셋 8계열·**누수 사례 6종** 조사. 결론은 "무엇이 이기는가"가 아니라 **무엇이 실제로 점수를 만들고 무엇이 누수인가**다. 경기 페이지는 JS 렌더링으로 정적 확인이 안 돼 메타·라이선스는 대부분 V3

## 관련 계획
- `../../plans/60-noise-cancellation-benchmark-refinement.md` — 노이즈 캔슬링 고도화 계획(E1~E5, D-067~071)
- `../../plans/101-TODO-ml-fault-diagnosis-prediction-rca.md` — ML 기반 장애 진단·예측·RCA 계획(D-223 예약)

> 출처는 벤더 공개 서술 기준. 도입효과 수치는 벤더/고객 발표 기준으로 독립검증 아님.
