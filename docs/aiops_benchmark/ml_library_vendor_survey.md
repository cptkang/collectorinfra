# AIOps ML 장애 진단·예측·RCA — 라이브러리·HolmesGPT 확장·벤더 AI 기능 조사

> **보관 메모(2026-09-17)**: `plans/101`(ML 기반 장애 진단·예측·RCA) 작성용 조사 원본. 조사 에이전트가 작성했고, 계획서 §3·§14가 이 문서의 항목 ID(§1~§6)를 인용한다. 설계 해석의 정본은 계획서이며, 이 문서는 서지·수치·검증 등급의 근거다. 성능 수치는 원문 대조(V1)분만 적었다.
>
> **v2 정정(같은 날)**: §2.5·§5.1의 배치 권고("mcp_server 안의 ML 도구 또는 전용 `ml_worker` venv")는 사용자 지시로 **`sre_agent` 편입**으로 대체됐다 — `plans/101` §4.2. `sre_agent` venv(py3.13) 의존성 해석 실측은 `plans/101` §3.5.1이 정본이다.

- 조사일: **2026-09-17** (모든 버전·날짜는 이날 실측)
- 실측 방법
  - PyPI JSON API(`https://pypi.org/pypi/<pkg>/json`): 최신 버전, 최초 업로드 시각, `requires_dist`, `requires_python`
  - GitHub REST API(`gh api repos/<r>`): `archived`, `pushed_at`, 기본 브랜치 최신 커밋일, latest release
  - Hugging Face API(`/api/models/<id>?blobs=true`): 가중치 라이선스, 파라미터 수(safetensors total), 가중치 파일 크기
  - 공식 문서와 소스(원문 fetch)
  - **아무 패키지도 설치하지 않았다.**
- 표기
  - ✔ 실측 확인
  - △ 간접 근거(검색 스니펫이나 2차 인용)
  - ✖/미확인: 확인하지 못함
  - 벤더 자료는 전부 **「벤더 공개 서술 — 독립 검증 아님」**이다.
- 로컬 환경 실측(읽기만, 판단 기준으로 씀)
  - 루트 `.venv`(개발 머신): Python 3.12.11 · numpy 2.5.1 · pandas **3.0.5** · scipy **1.18.0** · scikit-learn 1.9.0 · statsmodels 0.14.6 · mcp 1.29.1 · torch 2.13.0
  - `sre_agent/.venv`: Python 3.13.1 · holmesgpt **0.36.0** · mcp 1.25.0. numpy·pandas는 설치돼 있지 않다.
  - 폐쇄망 운영 서버의 Python·패키지 버전은 **미확인**이다(§6).

---

## §1 Python 라이브러리 비교

### 1.0 이 표를 읽기 전에 알아둘 공통 발견

1. **pandas 3 · scipy 1.18 환경과 충돌하는 패키지가 많다.** 루트 venv는 pandas 3.0.5이고 mcp_server도 이 venv를 공유한다. 여기에 막히는 상한 핀이 있는 패키지는 다음과 같다.
   - `statsforecast`·`mlforecast`·`utilsforecast`(pandas<3.0)
   - `sktime`(numpy<2.5 · pandas<3 · scikit-learn<1.8)
   - `gluonts`(pandas<3). Toto 2.0이 `gluonts[torch]`에 의존하므로 Toto도 해당한다.
   - `granite-tsfm`(scikit-learn<1.8)
   - `dowhy`(Python<3.13에서 scipy<=1.15.3)
   - `lingam`(scipy<=1.13.1이며 cp313 wheel이 없다)
   - `alibi-detect`·`salesforce-merlion`·`orion-ml`(numpy<2)
   - 근거: 각 PyPI `requires_dist` 실측
2. **Salesforce 계열 OSS는 사실상 동결 상태다.**
   - Merlion: GitHub `archived=true`(최종 push 2026-03-11)
   - CausalAI: `archived=true`(최종 push 2025-05-01)
   - PyRCA: 아카이브되지 않았지만 코드 커밋은 2023-09-06이 마지막이다. 2026-06-02 커밋은 SECURITY.md 업로드뿐이다.
3. **시계열 파운데이션 모델(TSFM)은 버전마다 가중치 라이선스가 바뀐다.**
   - TimesFM: ≤2.5는 Apache-2.0, **3.0은 비상업 전용**
   - Moirai: 전 버전 **CC-BY-NC-4.0**
   - TiRex: 1.x는 **NXAI Community License(연매출 1억 유로 초과 기업이 상용 제품·서비스에 쓰면 상용 라이선스 필요)**, TiRex-2는 Apache-2.0
   - 패키지(코드) 라이선스만 보고 판단하면 틀린다.
4. **PyPI의 Linux torch wheel은 CUDA 패키지를 의존으로 선언한다.** torch 2.14.0의 `requires_dist`에 `cuda-toolkit[...]==13.0.3`, `nvidia-cudnn-cu13==9.24.0.43`(Linux) 등이 들어 있다. manylinux cp311 torch wheel 하나가 554MB다. 폐쇄망 CPU 서버에 반입하려면 CPU 전용 인덱스 wheel을 따로 준비해야 한다([PyPI torch](https://pypi.org/project/torch/)).
5. **TSB-AD(NeurIPS 2024) 결론: "단순한 아키텍처와 통계 기법이 더 나은 경우가 많다."** 원문: *"simpler architectures and statistical methods often yield better performance"*([TSB-AD README](https://github.com/TheDatumOrg/TSB-AD)). 1차 스택을 torch 없이 구성하자는 근거다.

### 1.1 RCA · 인과

| 패키지 | 최신(릴리스일) | GitHub 유지 상태 | 라이선스 | Python | 주요 의존·무게 | 제공 알고리즘 | 반입 난이도 | 평가 |
|---|---|---|---|---|---|---|---|---|
| [sfr-pyrca](https://pypi.org/project/sfr-pyrca/) (Salesforce PyRCA) · [GH](https://github.com/salesforce/PyRCA) | 1.0.1 (2023-06-27) | 미아카이브. 코드 최종 2023-09-06, 2026-06-02 SECURITY.md만 추가 | BSD-3 | >=3.7 | **scikit-learn<1.2**, `javabridge`(JDK 필요) | ε-Diagnosis, Bayesian Inference RCA, Random Walk RCA, RCD, Hypothesis-Testing RCA, 인과그래프 빌더 | 상(JDK + 구 sklearn) | **회피**(알고리즘만 참고). PyPI의 `pyrca`는 무관한 동명 패키지(syncsoft, 2019)이니 반입 시 주의 |
| [RCAEval](https://pypi.org/project/RCAEval/) · [GH](https://github.com/phamquiluan/RCAEval) | 1.7.0 (2026-09-03) | 활발(최종 커밋 2026-09-06) | 자체 코드 MIT. 포함 방법별로 라이선스가 섞여 있다(CausalRCA·RUN은 **무라이선스**) | README 권장 3.12 | extra `default`에 **164개 정확 핀**(2023년대 버전), `rcd` 170개, `eventadl` 80개. apt 패키지(graphviz 등) 필요 | BARO, CIRCA, RCD, ε-Diagnosis, MicroCause, CausalRCA, RUN, EasyRCA, MSCRED, TraceRCA, MicroRank, PDiagnose, Multi-source BARO/RCD/CIRCA, TORAI, EventADL | 상 | **평가 하네스로만 참고**. 런타임 반입은 회피(§4 데이터셋 참조) |
| [fse-baro](https://pypi.org/project/fse-baro/) (BARO) · [GH](https://github.com/phamquiluan/baro) | 0.2.2 (2025-06-10) | 최종 커밋 2026-02-02 | MIT | 미명시 | numpy·pandas·sklearn. `pytest`·`matplotlib`·`requests`가 **런타임 의존**에 포함 | MultivariateBOCPD(베이지안 온라인 변경점) + RobustScorer(robust 점수화로 근본원인 지표 순위) | 하 | **1차 참고**. RobustScorer는 단순하므로 의존을 들이지 말고 재구현을 권장 |
| [dowhy](https://pypi.org/project/dowhy/) · [GH](https://github.com/py-why/dowhy) | 0.14 (2025-11-08) | 활발(2026-09-16) | MIT | >=3.9,<3.14 | causal-learn, cvxpy, numba, statsmodels. **py<3.13이면 scipy<=1.15.3**, py3.13이면 scipy>=1.15 | `dowhy.gcm`: `attribute_anomalies`, `anomaly_scores`, `distribution_change`, `distribution_change_robust`, `arrow_strength`, `intrinsic_causal_influence`, `counterfactual_samples` 등. 이름은 `gcm/__init__.py` 실측이다. 마이크로서비스 RCA 예제 노트북 제공 | 중 | **2차 채택 후보**. 토폴로지(인과 그래프)가 주어질 때 이상 기여도를 산출한다. 루트 venv(py3.12·scipy 1.18)와는 충돌하고 py3.13 venv에서는 설치 가능하다 |
| [causal-learn](https://pypi.org/project/causal-learn/) · [GH](https://github.com/py-why/causal-learn) | 0.1.4.8 (2026-07-11) | 활발(2026-09-04) | MIT | >=3.7 | graphviz(py), momentchi2, statsmodels, pydot | PC, FCI, CD-NOD, GES, ExactSearch, GRaSP, BOSS, Granger, LiNGAM(FCM), ANM, PNL, GIN(소스 트리 실측) | 하~중 | **2차 채택 후보**(지표 간 인과 구조 탐색, Granger) |
| [tigramite](https://pypi.org/project/tigramite/) · [GH](https://github.com/jakobrunge/tigramite) | 5.2.10.1 (2026-01-14) | 최종 커밋 2026-01-14 | **GPL-3.0(이상)** ✔ | 미명시 | numpy, scipy, six | PCMCI, PCMCI+, LPCMCI, J-PCMCI+, RPCMCI, causal effects/mediation | 의존은 하. 라이선스 부담은 상 | **회피**. GPL 카피레프트라 사내 배포물에 결합하려면 법무 검토가 필요하다. 오프라인 연구 분석용으로만 검토 |
| [lingam](https://pypi.org/project/lingam/) · [GH](https://github.com/cdt15/lingam) | 1.13.0 (2026-07-22) | 활발(2026-09-08) | MIT | >=3.10 | **scipy<=1.13.1**(cp310~312 wheel만 있고 cp313 없음), pygam, semopy, psy | DirectLiNGAM, VARLiNGAM, VARMALiNGAM, RCD, CAM-UV, RESIT, LiM, GroupLiNGAM 등 | 중(구 scipy 고정) | **참고**. 현 환경의 scipy와 충돌한다 |
| [cdt](https://pypi.org/project/cdt/) · [GH](https://github.com/FenTechSolutions/CausalDiscoveryToolbox) | 0.6.0 (2022-08-23) | 최종 push 2025-10-13(머지만) | GH는 MIT, PyPI 메타데이터는 "Apache 2.0"으로 **서로 다르다** | README 3.5+ (Docker는 3.6) | *"based on Numpy, Scikit-learn, Pytorch and R"* — **R 런타임 필요** | 그래프·pairwise 인과 탐색 | 상(R + torch) | **회피** |

**RCA 범주 권고**
- 1차: 결정적 RCA를 자체 구현한다.
  - 토폴로지 가중 상관과 시차 교차상관
  - 변경점 시각 정렬(ruptures)
  - BARO식 robust 점수화
  - 이유: 외부 RCA 패키지는 동결(PyRCA)됐거나 연구용 의존 폭탄(RCAEval)이다.
- 2차: `causal-learn`(PC·Granger)으로 후보 인과 그래프를 만들고, 폴스타 토폴로지로 가지치기한 뒤 `dowhy.gcm.attribute_anomalies`로 기여도를 산출한다. dowhy는 **py3.13 venv 또는 별도 워커**에서 돌린다.
- 평가: RCAEval 데이터셋과 방법 구현을 오프라인 벤치마크로만 쓴다.

### 1.2 이상탐지

| 패키지 | 최신(릴리스일) | GitHub 유지 상태 | 라이선스 | Python | 주요 의존·무게 | 제공 알고리즘 | 반입 난이도 | 평가 |
|---|---|---|---|---|---|---|---|---|
| [pyod](https://pypi.org/project/pyod/) · [GH](https://github.com/yzhao062/pyod) | **3.6.5 (2026-08-17)** | 활발(2026-09-16). 3.x가 한 달에 1~2회 릴리스 | BSD-2 | >=3.9(분류 3.9~3.13) | numba, scipy, sklearn, joblib, matplotlib. torch는 extra(`torch`,`graph`,`huggingface`) | 탐지기 61종(표 형식·시계열·그래프·텍스트·이미지·오디오). 시계열: `TimeSeriesOD`(윈도우 브리지), `MatrixProfile`, `SpectralResidual`, `KShape`, `SAND`(스트리밍), `LSTMAD`, `AnomalyTransformer`. `ADEngine`. **`pyod[mcp]` extra로 MCP 서버 제공**(`mcp>=1.0`) | 하 | **1차 채택**. 표 형식(IForest·ECOD 등)과 시계열 탐지를 한 API로 제공한다 |
| [scikit-learn](https://pypi.org/project/scikit-learn/) `IsolationForest` | 1.9.1 (2026-09-10) | 활발 | BSD-3 | >=3.11 | 이미 설치(1.9.0) | IsolationForest, LOF, OneClassSVM, EllipticEnvelope | 없음 | **1차 채택**(추가 반입 0) |
| [river](https://pypi.org/project/river/) · [GH](https://github.com/online-ml/river) | 0.26.1 (2026-08-21) | 활발(2026-09-14) | BSD-3 | **>=3.11** | numpy>=2.2.5, scipy, narwhals. 플랫폼 wheel 50개(평균 2.7MB) | `anomaly`: HalfSpaceTrees, LODA, LocalOutlierFactor, OneClassSVM, GaussianScorer, StandardAbsoluteDeviation, PredictiveAnomalyDetection, QuantileFilter/ThresholdFilter(모듈 실측) | 하 | **1차 채택**. 노이즈 게이트에 **온라인·증분** 정량 신호를 준다 |
| [stumpy](https://pypi.org/project/stumpy/) · [GH](https://github.com/stumpy-dev/stumpy) | 1.14.1 (2026-02-08) | 활발(2026-09-12) | BSD-3 | >=3.10 | numpy, scipy, **numba>=0.61.2** | Matrix Profile(`stump`, `stumped`(dask), `gpu_stump`), 다차원 `mstump`, 세그먼트 `fluss`, chain `atsc`/`allc` | 하 | **1차 후보**. discord(이상 부분열)와 반복 패턴 탐지 |
| [ruptures](https://pypi.org/project/ruptures/) · [GH](https://github.com/deepcharles/ruptures) | 1.1.10 (2025-09-10) | 코드 최종 2026-05-26 | BSD-2 | >=3.9,**<3.14** | numpy, scipy. C 확장 wheel 25개 | Pelt, Binseg, BottomUp, Dynp, KernelCPD, Window, L1Potts | 하 | **1차 채택**. 변경점으로 배포·설정 변경 시점을 추정하고 RCA 시각 정렬에 쓴다 |
| [salesforce-merlion](https://pypi.org/project/salesforce-merlion/) · [GH](https://github.com/salesforce/Merlion) | 2.0.4 (2024-06-20) | **archived=true**(최종 push 2026-03-11) | BSD-3 | >=3.7 | numpy<2, prophet, lightgbm, py4j | 이상탐지·예측 통합 | 상 | **회피**(아카이브, numpy<2) |
| [alibi-detect](https://pypi.org/project/alibi-detect/) · [GH](https://github.com/SeldonIO/alibi-detect) | 0.13.0 (2025-12-11) | 최종 2025-12-11 | **Business Source License 1.1**. 비프로덕션 사용만 허용되고, 비영리 교육기관 외 프로덕션 사용은 상용 라이선스가 필요하다. 각 버전은 4년 뒤 Apache-2.0으로 전환 | >=3.9 | numpy<2, opencv, transformers, numba<0.60 | outlier·drift·adversarial 탐지 | 상 | **회피**(라이선스와 numpy<2) — [LICENSE](https://github.com/SeldonIO/alibi-detect/blob/master/LICENSE) |
| [adtk](https://pypi.org/project/adtk/) · [GH](https://github.com/arundo/adtk) | 0.6.2 (2020-04-17) | 코드 최종 커밋 2020-04-17 | MPL-2.0 | 분류 3.5~3.8 | statsmodels 등 | 규칙·임계·계절 탐지기 조합 | 하 | **회피**(6년 방치) |
| [kats](https://pypi.org/project/kats/) · [GH](https://github.com/facebookresearch/Kats) | 0.2.0 (2022-03-15) | 커밋은 2026-08-19까지 있으나 Pyre 타입 억제 등 내부 동기화뿐이고 **릴리스는 없다** | MIT | 분류 3.7/3.8 | `pystan==2.19.1.1`, `fbprophet==0.7.1`, numpy<1.22, scipy<1.8, statsmodels==0.12.2, torch, gpytorch, ax-platform==0.2.4 | 변경점·이상·예측 | **설치 불가**(py3.11+) | **회피** |
| [orion-ml](https://pypi.org/project/orion-ml/) · [GH](https://github.com/sintel-dev/Orion) | 0.7.1 (2025-03-17) | 활발(2026-09-14, 2026-03 Chronos 2 파이프라인 추가) | MIT | **>=3.8,<3.12** | tensorflow<2.15, torch<2.6, numpy<2, azure anomalydetector | MLBlocks 파이프라인 기반 딥러닝·통계 탐지 | 상 | **회피**(py<3.12 · TF). 파이프라인 설계만 참고 |
| [luminaire](https://pypi.org/project/luminaire/) · [GH](https://github.com/zillow/luminaire) | 0.4.3 (2024-01-31) | 2026-06-02 린트 수정 커밋 | Apache-2.0 | 분류 3.7~3.9 | **numpy<=1.22.4**(cp311+ wheel 없음), statsmodels<=0.13.5, hyperopt | 자동 구성 이상탐지 | 상 | **회피** |
| [TSB-AD](https://pypi.org/project/TSB-AD/) · [GH](https://github.com/TheDatumOrg/TSB-AD) | 1.5 (2025-02-18) | 활발(2026-09-07). 2026-04-01 리더보드 커뮤니티 제출 개시 | Apache-2.0 | 3.8~3.12 | **torch 기본 의존** | 탐지기 40종 벤치마크, 평가지표 VUS-PR | 중 | **평가 참고**(런타임 회피) |

**이상탐지 범주 권고**
- 1차(torch 불필요, 전부 BSD/MIT)
  - 배치: `scikit-learn` IsolationForest(이미 있음)와 `pyod`
  - 온라인: `river` HalfSpaceTrees
  - 변경점: `ruptures`
  - discord: `stumpy`
  - 기존 `statsmodels` STL 잔차와 MAD 기준선을 **먼저** 둔다.
- 알고리즘을 추가할 때는 TSB-AD 결론(단순 기법 우위)에 따라 VUS-PR로 사내 라벨셋에서 이긴 경우에만 승격한다.
- PyOD 3의 MCP 서버(`pyod mcp serve`)는 탐지기 지식·계획 도구 성격이다. 우리 데이터에 대한 결정적 탐지 도구는 아니므로 **채택하지 않는다**. 대신 mcp_server에 자체 도구를 둔다.

### 1.3 예측

| 패키지 | 최신(릴리스일) | GitHub 유지 상태 | 라이선스 | Python | 주요 의존·무게 | 제공 알고리즘 | 반입 난이도 | 평가 |
|---|---|---|---|---|---|---|---|---|
| (기존) statsmodels | 0.15.0 (2026-08-27). 설치본은 0.14.6 | 활발 | BSD-3 | >=3.10 | 이미 사용 중 | STL, ETS(ExponentialSmoothing), SARIMAX | 없음 | **1차**(추가 반입 0) |
| [statsforecast](https://pypi.org/project/statsforecast/) · [GH](https://github.com/Nixtla/statsforecast) | 2.1.1 (2026-07-16) | 활발(2026-09-16) | Apache-2.0 | >=3.10 | **pandas<3.0.0**, coreforecast, fugue, utilsforecast(pandas<3), statsmodels>=0.14.5 | AutoARIMA, AutoETS, AutoCES, AutoTheta, MSTL, MFLES, TBATS, GARCH/ARCH, Croston 계열, ConformalSeasonalPool 등(`models.py` 클래스 실측) | 중 | **2차 후보**. 성능은 최선이지만 **현 pandas 3 환경과 충돌**하므로 별도 venv 워커로 운영한다 |
| [mlforecast](https://pypi.org/project/mlforecast/) · [GH](https://github.com/Nixtla/mlforecast) | 1.1.0 (2026-07-10) | 활발 | Apache-2.0 | >=3.10 | pandas<3.0 | lag 특징 기반 ML 회귀 예측 | 중 | 참고 |
| [neuralforecast](https://pypi.org/project/neuralforecast/) · [GH](https://github.com/Nixtla/neuralforecast) | 3.2.2 (2026-09-08) | 활발 | Apache-2.0 | >=3.10 | **torch>=2.9.1**, pytorch-lightning<2.6 | 딥러닝 예측 모델군 | 상 | **회피**(1·2차) |
| [prophet](https://pypi.org/project/prophet/) · [GH](https://github.com/facebook/prophet) | 1.4.0 (2026-08-15) | 활발(2026-08-27) | MIT | >=3.10 | cmdstanpy. wheel에 CmdStan 번들(플랫폼 wheel 평균 11.9MB) | 가법 추세·계절·휴일 | 중 | 참고(휴일·이벤트 효과가 필요할 때) |
| [darts](https://pypi.org/project/darts/) · [GH](https://github.com/unit8co/darts) | 0.47.0 (2026-09-04) | 활발 | Apache-2.0 | >=3.10 | pandas>=2.2(상한 없음), shap, xarray, pyod, holidays, nfoursid. torch는 extra | 통계·ML·DL 통합 API와 이상탐지 모듈 | 중(기본 의존 31개) | 2차 참고(pandas 3과 충돌하는 핀은 없음) |
| [sktime](https://pypi.org/project/sktime/) · [GH](https://github.com/sktime/sktime) | 1.1.0 (2026-07-28) | 활발 | BSD-3 | >=3.10,<3.15 | **numpy<2.5, pandas<3, scikit-learn<1.8**. wheel 36MB | 통합 인터페이스 | 중 | **회피**(현 환경 3중 충돌) |
| [gluonts](https://pypi.org/project/gluonts/) · [GH](https://github.com/awslabs/gluonts) | 0.17.0 (2026-07-31) | 활발 | Apache-2.0 | >=3.10,<3.15 | **pandas<3** | 확률 예측 DL 프레임워크 | 중 | 참고(Toto 의존 경로로만) |
| [greykite](https://pypi.org/project/greykite/) · [GH](https://github.com/linkedin/greykite) | 1.1.0 (2025-02-20) | 최종 커밋 2025-02-20 | BSD-2 | >=3.10 | `numpy==1.26.0`, pandas<2, `scikit-learn==1.3.1`, `holidays==0.13`, `pytest==8.3.4`를 **런타임 정확 핀** | Silverkite | 상 | **회피** |

**예측 범주 권고**
- 1차: `statsmodels` STL+ETS로 **디스크·테이블스페이스 소진 시점, 용량 추세**를 예측한다. 추가 반입 0.
- 2차: `statsforecast`(MSTL·AutoETS)를 **pandas<3 전용 ML 워커 venv**에서 돌린다. mcp_server와 같은 venv에 넣지 않는다.
- 3차: TSFM(§1.4)을 보안 심사 후 도입한다.

### 1.4 시계열 파운데이션 모델(TSFM)

가중치 크기는 HF safetensors·ckpt 파일 합계 실측값이다.

| 모델/패키지 | 패키지 최신 | GitHub | 코드 라이선스 | **가중치 라이선스(상업 사용)** | 파라미터 · 파일 크기 | Python · torch | CPU 추론 | 평가 |
|---|---|---|---|---|---|---|---|---|
| [chronos-forecasting](https://pypi.org/project/chronos-forecasting/) · [GH](https://github.com/amazon-science/chronos-forecasting) | 2.3.2 (2026-09-08) | 활발 | Apache-2.0 | ✔ **Apache-2.0**: [chronos-2](https://huggingface.co/amazon/chronos-2), [chronos-bolt-*](https://huggingface.co/amazon/chronos-bolt-small), [autogluon/chronos-2-small](https://huggingface.co/autogluon/chronos-2-small) | Chronos-2 119.5M(478MB) · chronos-2-small 27.9M(112MB) · Bolt tiny 8.7M(35MB)/mini 21.2M(85MB)/small 47.7M(191MB)/base 205M(821MB) · T5-large 709M(2.8GB) | >=3.10 · torch<3,>=2.2, transformers<6, accelerate | ✔ 모델 카드: Chronos-2 *"supporting both GPU and CPU inference"*, Bolt *"both CPU and GPU"*. Bolt는 원 Chronos 대비 *"up to 250 times faster"* | **3차 채택 후보(1순위)**. 보안 심사 대상은 safetensors 1개 |
| [timesfm](https://pypi.org/project/timesfm/) · [GH](https://github.com/google-research/timesfm) | 3.0.2 (2026-09-09) | 활발 | Apache-2.0 | ✔ **≤2.5는 Apache-2.0**([2.5-200m-pytorch](https://huggingface.co/google/timesfm-2.5-200m-pytorch)) · ✖ **3.0은 `timesfm-non-commercial-license-v1.0`** — *"non-commercial and non-production use"*([3.0 카드](https://huggingface.co/google/timesfm-3.0-pytorch), README도 동일하게 명시) | 2.5: 231M(925MB) · 3.0: 331M(1.3GB) · 2.0-500m: 499M(4.0GB) | >=3.10 · base 의존은 numpy·hf_hub·safetensors뿐이고 torch/flax/mlx는 extra | 미확인(실측 금지로 벤치 없음) | **2.5만 3차 후보**, **3.0은 회피**(운영 사용 금지 조항) |
| [toto-2](https://pypi.org/project/toto-2/) / [toto-models](https://pypi.org/project/toto-models/) · [GH](https://github.com/DataDog/toto) | toto-2 2.0.0 · toto-models 1.0.0 (2026-06-04). Toto 1.0용 [toto-ts](https://pypi.org/project/toto-ts/) 0.2.0 (2026-02-26) | 활발(2026-09-14) | Apache-2.0 | ✔ **Apache-2.0**([Toto-2.0-4m](https://huggingface.co/Datadog/Toto-2.0-4m) 등, [Toto-Open-Base-1.0](https://huggingface.co/Datadog/Toto-Open-Base-1.0)) | 2.0: 4m(17MB)/22m(88MB)/313m(1.25GB)/1B(4.2GB)/2.5B(9.8GB) · 1.0: 151M(605MB). **관측 지표(observability) 특화**, BOOM 벤치 1위(Datadog 주장) | **>=3.12** · torch>=2.4, `gluonts[torch]>=0.16`(pandas<3), dd-unit-scaling. toto-ts는 torch==2.7.0 등 30여 개 정확 핀 | 모델 카드상 4m은 *"Edge / CPU deployment"* 권장. 지연 수치는 A100 기준뿐 | **3차 후보(도메인 적합성 최고)**. 단 py>=3.12이고 gluonts가 pandas<3을 요구하므로 전용 venv가 필요하다 |
| [uni2ts](https://pypi.org/project/uni2ts/) (Moirai) · [GH](https://github.com/SalesforceAIResearch/uni2ts) | 2.0.0 (2025-11-04) | 최종 2026-06-02 | Apache-2.0 | ✖ **CC-BY-NC-4.0**(Moirai 1.0/1.1/MoE/2.0 전부. [2.0-R-small](https://huggingface.co/Salesforce/moirai-2.0-R-small)) | 2.0-small 11.4M(46MB) · 1.1-base 91M(365MB) | >=3.10 · **torch<2.5**(cp313 wheel 없음), jax[cpu], numpy~=1.26, scipy~=1.11.3 | 미확인 | **회피**(비상업 라이선스) |
| [momentfm](https://pypi.org/project/momentfm/) (MOMENT) · [GH](https://github.com/moment-timeseries-foundation-model/moment) | 0.1.4 (2025-03-19) | 최종 2026-02-10 | MIT | ✔ MIT([MOMENT-1-small](https://huggingface.co/AutonLab/MOMENT-1-small)) | small 38M(303MB) · large 346M(2.8GB). 예측·**재구성 기반 이상탐지**·분류 | >=3.10이지만 **numpy==1.25.2(cp≤311)**, **transformers==4.33.3**, torch~=2.0 | 미확인 | **참고**(정확 핀 때문에 py3.12/3.13 불가) |
| [Lag-Llama](https://github.com/time-series-foundation-models/lag-llama) | **PyPI 없음** | 최종 커밋 2025-06-06 | Apache-2.0 | ✔ Apache-2.0 | 2.4M(39MB) | 소스 설치 | — | **회피**(정체·미배포) |
| [tirex-ts](https://pypi.org/project/tirex-ts/) (TiRex 1.x) · [GH](https://github.com/NX-AI/tirex) | 1.4.2 (2026-06-09) | 활발(2026-09-07) | NXAI Community | ✖ **NXAI Community License** §2: *연매출 1억 유로 초과 Licensee가 NXAI Material을 Commercial Product or Service에 포함하면 상용 라이선스 필요*. 표기 의무("Built with technology from NXAI")도 있다([LICENSE](https://huggingface.co/NX-AI/TiRex/blob/main/LICENSE)) | ckpt 합계 283MB | >=3.10 · torch | 미확인 | **회피**(금융권 대기업은 임계 초과 가능성이 높다. 사내 전용 사용이 "Commercial Service"에 해당하는지는 법무 판단 필요) |
| [tirex-2](https://pypi.org/project/tirex-2/) (TiRex-2) · [GH](https://github.com/NX-AI/tirex-2) | 0.2.1 (2026-08-05) | 활발(2026-09-16) | Apache-2.0 | ✔ **Apache-2.0**([TiRex-2](https://huggingface.co/NX-AI/TiRex-2)) | 활성 파라미터 38.4M(단변량)+44.1M(다변량). `model.ckpt` 381MB(safetensors가 아님) | >=3.11 · torch>=2.8, `xlstm~=2.0.3`, `flashrnn` | 카드 예시가 `device="cpu"` | **관찰**. 출시 3개월 미만이고, ckpt 형식은 pickle 계열로 추정되어(△) 보안 심사 부담이 있다 |
| [granite-tsfm](https://pypi.org/project/granite-tsfm/) (IBM TTM) · [GH](https://github.com/ibm-granite/granite-tsfm) | 0.3.9 (2026-08-28) | 활발(2026-09-11) | Apache-2.0 | ✔ Apache-2.0([ttm-r2](https://huggingface.co/ibm-granite/granite-timeseries-ttm-r2)) | **0.8M(3MB)** 초경량 | >=3.11,<3.14 · torch>=2.10,<2.12, **scikit-learn<1.8** | 경량이라 유리하나 수치 미확인 | **참고**(sklearn 상한 충돌) |

**TSFM 권고**
- 1·2차에는 넣지 않는다. 이유는 셋이다: torch 반입, 가중치 보안 심사, CPU 지연 미실측.
- 3차 후보는 **Chronos-Bolt small/mini 또는 chronos-2-small**(Apache, safetensors, CPU 공식 지원)과 **Toto-2.0-4m/22m**(Apache, 관측 지표 특화)이다. 전용 venv 워커에 두고 결과만 MCP로 노출한다.
- **TimesFM 3.0·Moirai·TiRex 1.x는 라이선스로 배제**한다.

### 1.5 로그

| 패키지 | 최신(릴리스일) | GitHub 유지 상태 | 라이선스 | Python | 의존·무게 | 알고리즘 | 반입 난이도 | 평가 |
|---|---|---|---|---|---|---|---|---|
| [drain3](https://pypi.org/project/drain3/) · [GH](https://github.com/logpai/Drain3)(IBM→logpai 이관) | 0.9.11 (2022-07-17) | 코드 최종 2025-02-04(타이핑·성능 개선 후 릴리스 없음) | MIT(LICENSE.txt) | `^3.7` | jsonpickle, cachetools. redis·kafka는 선택(영속화). **sdist만 배포**라 빌드에 `poetry-core` 필요 | Drain 온라인 로그 템플릿 마이닝, 마스킹, 상태 영속화(file/redis/kafka) | 하~중(오프라인 빌드 준비) | **1차 채택**(로그를 쓸 경우). 영속 상태를 **jsonpickle**로 복원하므로 신뢰 경계 밖 파일은 로딩 금지 |
| [logparser3](https://pypi.org/project/logparser3/) · [GH](https://github.com/logpai/logparser) | 1.0.4 (2023-09-14) | 최종 2025-06-10 | PyPI 메타는 Apache-2.0. GH는 LICENSE.md(NOASSERTION) | >=3.6 | `regex==2022.3.2` 정확 핀 | 로그 파서 연구 구현 모음(벤치마크용) | 중 | **참고**(파서 비교 평가용) |
| [loglizer](https://pypi.org/project/loglizer/) · [deep-loglizer](https://github.com/logpai/deep-loglizer) | loglizer 1.0 (2020-12-23). deep-loglizer는 미배포 | loglizer 최종 2023-03, deep-loglizer 최종 2021-10 | MIT / Apache-2.0 | — | — | 로그 이상탐지 ML/DL 연구 구현 | — | **회피**(연구 참고만) |

### 1.6 특징 추출

| 패키지 | 최신(릴리스일) | GitHub | 라이선스 | Python | 의존 | 내용 | 평가 |
|---|---|---|---|---|---|---|---|
| [tsfresh](https://pypi.org/project/tsfresh/) · [GH](https://github.com/blue-yonder/tsfresh) | 0.21.2 (2026-05-31) | 최종 2026-07-06 | MIT | >=3.9 | stumpy, pywavelets, statsmodels, cloudpickle | *"Feature extraction based on scalable hypothesis tests"*. 다수 특징과 다중검정 기반 선택 | **2차 후보**(알람·장애 분류기 특징) |
| [tsfel](https://pypi.org/project/tsfel/) · [GH](https://github.com/fraunhoferportugal/tsfel) | 0.2.0 (2025-08-20) | 활발(2026-09-15) | BSD-3 | 미명시 | **ipython이 런타임 의존**, pywavelets, statsmodels | 통계·시간·스펙트럼·프랙탈 도메인 특징 | 2차 후보(ipython 의존 주의) |

### 1.7 서빙·모델 관리·직렬화 보안

| 패키지 | 최신(릴리스일) | 라이선스 | Python | 무게 | 평가 |
|---|---|---|---|---|---|
| [skops](https://pypi.org/project/skops/) · [GH](https://github.com/skops-dev/skops) | 0.15.0 (2026-09-16) | MIT | >=3.9 | 가벼움(sklearn·numpy·scipy·prettytable) | **1차 채택**. `skops.io`는 *"Secure persistence of sklearn estimators ... without using pickle"*. `get_untrusted_types()` 검토 후에만 `trusted` 지정([문서](https://skops.readthedocs.io/en/stable/persistence.html)). 한계: 임의 코드 불가, LightGBM/XGBoost 등은 감사 대상이 아니다. sklearn 버전 간 로딩은 비권장 |
| [onnxruntime](https://pypi.org/project/onnxruntime/) + [skl2onnx](https://pypi.org/project/skl2onnx/) | 1.30.0 (2026-09-10) / 1.20.0 (2026-01-30) | MIT / Apache-2.0 | >=3.11 / >=3.8 | ORT wheel 평균 19MB | **1차 선택지**. 추론 전용 격리가 필요할 때 쓴다. 코드 실행 없는 그래프 포맷이다 |
| [joblib](https://pypi.org/project/joblib/) / pickle | 1.6.0 (2026-08-31) | BSD-3 | >=3.10 | — | **신뢰 경계를 넘는 모델 전달에 사용 금지**. scikit-learn 공식 문서가 pickle·joblib·cloudpickle 모두 *"Loading can execute arbitrary code"*로 명시한다([model_persistence.rst](https://github.com/scikit-learn/scikit-learn/blob/main/doc/model_persistence.rst)) |
| [mlflow](https://pypi.org/project/mlflow/) · [GH](https://github.com/mlflow/mlflow) | 3.16.1 (2026-09-16) | Apache-2.0 | >=3.10 | 무거움: full 59개 의존, skinny도 databricks-sdk·opentelemetry·fastapi·gitpython 등 | **2차(필요 시)**. 1차는 SQLite에 모델 메타(학습 구간·파라미터·지표·파일 SHA-256)를 두는 것으로 충분하다. MLflow 모델 flavor 상당수가 cloudpickle 기반이라는 점도 심사 항목 |

---

## §2 HolmesGPT 확장 지점

### 2.1 버전·거버넌스 실측

| 항목 | 값 | 근거 |
|---|---|---|
| 최신 릴리스 | **0.42.0**: PyPI 업로드 2026-09-16, GitHub release 2026-09-16 | [PyPI](https://pypi.org/project/holmesgpt/) · [Releases](https://github.com/HolmesGPT/holmesgpt/releases) |
| 릴리스 빈도 | 2026-01~09에 알파 포함 60여 개 버전. 주 단위 마이너 릴리스 | PyPI 릴리스 목록 실측 |
| 저장소 | `HolmesGPT/holmesgpt`(robusta-dev에서 이관). 미아카이브, 최신 커밋 2026-09-16, Apache-2.0 | GitHub API |
| CNCF | **Sandbox**, 2025-10-08 채택. Incubation 이력 없음 | [CNCF](https://www.cncf.io/projects/holmesgpt/) |
| Python | `>=3.10,<3.14` | PyPI `requires_python` |
| mcp 핀 | 0.42.0은 **`mcp==1.28.1`**(정확 핀), 0.36.0은 `mcp==v1.25.0`. 둘 다 우리 `mcp<2` 상한과 호환 | PyPI `requires_dist` |
| 설치 무게 | 기본 의존 61개: litellm==1.89.0, supabase==2.28.1, kubernetes, confluent-kafka, google-cloud-aiplatform, opensearch-py, azure-identity, pymongo 등 | PyPI `requires_dist` |

**우리 고정 버전과의 차이**
- `sre_agent/pyproject.toml`은 `holmesgpt>=0.36.0`이다. **하한만 있고 상한이 없다.** 설치본은 0.36.0(PyPI 2026-07-13)이고 mcp는 1.25.0이다.
- 최신까지 **0.37.0 → 0.38.0~0.38.2 → 0.39.0 → 0.40.0 → 0.41.0 → 0.42.0**, 마이너 6단계 차이다.
- 우리 설계에 영향이 있는 변경

| 시점 | 변경 | 우리 영향 |
|---|---|---|
| 0.26.0 | runbook(`custom_runbook_catalogs` + catalog.json) 폐지, **Skills(`SKILL.md`)로 대체** | 0.36.0 설치본에 이미 포함. 런북은 SKILL.md로 작성 ([skills.md](https://github.com/HolmesGPT/holmesgpt/blob/master/docs/reference/skills.md)) |
| 2026-03-08 | **`/api/investigate` 엔드포인트와 구조화 조사 출력 모듈 삭제**, `/api/chat`으로 통합 ([PR #1688](https://github.com/HolmesGPT/holmesgpt/pull/1688)) | sre_agent는 Python SDK(`Config`·`build_initial_ask_messages`·`ToolCallingLLM`)를 쓰므로 직접 영향 없음(`sre_agent/diagnosis.py` import 실측) |
| 0.40.0 | Kubernetes·slab·kubevela 등 toolset **command injection 수정**, http/internet toolset **SSRF 수정**, bash toolset 인자 검증 강화 ([0.40.0](https://github.com/HolmesGPT/holmesgpt/releases/tag/0.40.0)) | 보안 수정이 들어 있다. 0.36.0 고정을 유지하려면 근거가 필요하다 |
| 0.41.0 | `skill_repos`(git 동기화 스킬 라이브 갱신) | 폐쇄망에선 git 원격이 없으므로 `custom_skill_paths`를 사용 |
| **0.42.0** | **MCP `structuredContent`를 모델에 노출하고 null 선택 파라미터를 제거** ([PR #2459](https://github.com/HolmesGPT/holmesgpt/pull/2459), 2026-09-12 머지). 이전에는 도구 결과 중 **text 요약만** 모델에 전달돼 id 등 구조화 데이터가 사라졌다 | **0.36.0에서 ML MCP 도구 결과는 반드시 text content에 담아야 한다**(JSON 직렬화 문자열) |

### 2.2 확장 방식 6종

#### (1) MCP 서버를 toolset으로 연결하는 방식(권장 경로)
문서 원문: [remote-mcp-servers.md](https://github.com/HolmesGPT/holmesgpt/blob/master/docs/data-sources/remote-mcp-servers.md) · 렌더본 https://holmesgpt.dev/latest/data-sources/remote-mcp-servers/

- 전송 방식 3종
  - **`streamable-http`**: *"Recommended"*
  - `stdio`: CLI에선 직접 쓸 수 있고, Kubernetes에선 Supergateway로 HTTP화해야 한다
  - `sse`: **Deprecated**. URL이 `/sse`로 끝나지 않으면 자동으로 붙인다
- 설정 키: `mcp_servers.<name>.description`, `config.url`, `config.mode`, `config.headers`, `config.extra_headers`, `config.health_check_tool`, `config.icon_url`, `llm_instructions`
  - `extra_headers`는 서버 모드에서 `{{ request_context.headers['X'] }}` 템플릿을 쓴다.
  - `health_check_tool`은 인자가 없는 읽기 전용 도구여야 하며, 기동 시 `{}`로 호출해 인증을 검증한다.
  - `llm_instructions`는 *"tells Holmes WHEN and HOW to use this server"*.
  - stdio는 `config.command`·`args`·`env`를 쓴다.
- 구 형식(`url`을 최상위에 둠)도 동작하지만 마이그레이션 경고를 남긴다.
- **우리 상태**: sre_agent가 폴스타 MCP를 `{"mode": "sse", "url": url, "health_check_tool": "list_sources"}`로 등록한다(`sre_agent/interface/mcp_service.py:115` 실측). mcp_server 쪽은 `mcp.sse_app()`이다(같은 파일 305행). HolmesGPT 문서상 SSE는 deprecated이므로 ML 도구를 추가할 때 streamable-http 전환을 검토한다.

설정 예시(우리 환경 가정. 도구명은 설계 예시이고 URL·토큰은 가상 값):

```yaml
# ~/.holmes/config.yaml (CLI) 또는 Python SDK Config(mcp_servers=...)에 같은 dict로 전달
mcp_servers:
  aiops_ml:
    description: "폴스타·제니퍼·DPM·Prometheus 지표에 대한 결정적 이상탐지·예측·RCA 점수"
    config:
      url: "http://mcp-server.internal:8765/mcp"
      mode: streamable-http
      headers:
        Authorization: "Bearer {{ env.AIOPS_MCP_TOKEN }}"
      health_check_tool: ml_model_status      # 인자 없는 읽기 전용 도구
    llm_instructions: |
      장애·알람 조사에서 지표 이상 여부를 판단할 때는 추측하지 말고 먼저 detect_anomalies를 호출하라.
      근본원인 후보가 여러 개면 rank_root_causes로 점수를 받고, 점수와 근거 지표를 그대로 인용하라.
      용량·소진 질문에는 forecast_exhaustion을 사용하라. 점수는 확률이 아니라 상대 순위다.
```

```python
# sre_agent의 현행 사용 방식(Python SDK). Config.mcp_servers 인자는 0.36.0에서 실측됨(diagnosis.py 주석)
from holmes.config import Config
config = Config(model=..., api_key=..., mcp_servers={
    "aiops_ml": {"description": "...", "config": {"url": "http://.../mcp", "mode": "streamable-http"},
                 "llm_instructions": "..."}})
```

#### (2) 커스텀 YAML toolset(셸 명령형)
원문: [custom-toolsets.md](https://github.com/HolmesGPT/holmesgpt/blob/master/docs/data-sources/custom-toolsets.md)

- 구조: `toolsets.<name>` 아래에 `description` · `prerequisites` · `tags` · `installation` · `tools[]`를 둔다. `tools[]` 항목은 `name`, `description`, `command`, `parameters`(선택)다.
- 변수 문법
  - `{{ var }}`: LLM이 채움
  - `${ENV}`: 환경변수, LLM에 비노출
  - `{{ env.X }}`, `{{ request_context.headers[...] }}`
- 실행: `holmes ask ... --custom-toolsets=toolsets.yaml`
- 판단: ML CLI를 셸로 감쌀 수는 있다. 그러나 셸 실행 표면이 생기고(0.40.0 command injection 수정 이력 참고) 결과가 비구조 텍스트다. **MCP 경로보다 열위**다.

#### (3) HTTP 커넥터(`type: http`)
원문: [api-toolsets.md](https://github.com/HolmesGPT/holmesgpt/blob/master/docs/data-sources/api-toolsets.md)

- `config.endpoints[].hosts/paths/methods` 허용목록, `auth`(basic/bearer/header), `verify_ssl`, `timeout_seconds`, `llm_instructions`
- 판단: ML을 REST로 노출하면 붙일 수 있지만, 우리는 이미 mcp_server가 있으므로 불필요하다.

#### (4) Python toolset(SDK 내장 확장)
원문: [python-sdk.md](https://github.com/HolmesGPT/holmesgpt/blob/master/docs/reference/python-sdk.md)

- 구성: `Toolset` 서브클래스 + `Tool._invoke()`가 `StructuredToolResult`를 반환 + `CallablePrerequisite` 헬스체크. `Config(additional_toolsets=[...])`로 주입한다.
- 이 API가 0.36.0에 있는지는 **미확인**이다(0.42 master 문서 기준).
- 판단: sre_agent 프로세스에 ML 의존(numpy 등)을 끌어들이게 된다. D-139 경계(sre_agent는 MCP 계약만)와 충돌하므로 **비권장**.

#### (5) Skills(구 runbook 카탈로그)
- 형식: 디렉토리마다 `SKILL.md` 하나. frontmatter는 `name`(선택)과 `description`(필수이며 매칭에 쓰인다), 본문 권장 섹션은 Goal/Workflow/Synthesize Findings/Recommended Remediation Steps다.
- 동작: Holmes가 카탈로그에서 매칭한 뒤 `fetch_skill` 도구로 가져와 단계를 실행한다.
- 로딩: CLI는 `custom_skill_paths`, `skill_repos`(https git만)를 쓴다. Helm은 `customSkills`/`customSkillPaths`/`skillRepos`를 쓴다. 경로당 2단계 깊이까지 스캔하고, 내장 스킬과 이름이 같으면 사용자 스킬이 우선한다.
- 적용 예(폐쇄망: `custom_skill_paths`로 로컬 디렉토리):

```markdown
---
name: polestar-cpu-saturation
description: 폴스타 서버 CPU 사용률 알람의 원인 조사(동시간대 이상 지표·변경점·APM 트랜잭션 연계)
---
## Goal
CPU 포화 알람이 단독 이상인지, 상위 원인(배치·트래픽·DB 대기)의 결과인지 판정한다.
## Workflow
1. aiops_ml.detect_anomalies(host, window=2h)로 동일 호스트의 이상 지표 목록을 받는다
2. aiops_ml.detect_changepoints로 변경점 시각을 받아 알람 시각과 정렬한다
3. aiops_ml.rank_root_causes(anchor=cpu_usage)로 후보 지표·연계 인스턴스 순위를 받는다
4. 1위 후보가 WAS면 제니퍼 트랜잭션 도구로, DB면 DPM 도구로 증거를 확인한다
## Synthesize Findings
점수는 상대 순위다. 증거 지표 원값을 인용하지 못하면 결론을 보류한다.
```

#### (6) HTTP API의 구조화 출력
원문: [http-api.md](https://github.com/HolmesGPT/holmesgpt/blob/master/docs/reference/http-api.md)

- `POST /api/chat`의 `response_format`에 `{"type":"json_schema","json_schema":{"name":...,"strict":true,"schema":{...}}}`를 넣는다. 응답의 **`analysis` 필드에 JSON 문자열**이 담긴다.
- 그 밖에 `stream`(SSE 이벤트), `enable_tool_approval`, `frontend_tools`, `behavior_controls`가 있다.
- 현재 master 서버의 라우트는 `/api/chat`, `/api/model`, `/api/info`, `/healthz`, `/readyz`, `/api/oauth/callback`이다(`server.py` 실측). **`/api/investigate`는 없다.**

### 2.3 내장 메트릭 도구와 ML 기능 유무

- **Prometheus toolset `prometheus/metrics`**
  - 도구 8종: `list_prometheus_rules`, `get_metric_names`, `get_label_values`, `get_all_labels`, `get_series`, `get_metric_metadata`, `execute_prometheus_instant_query`, `execute_prometheus_range_query`([prometheus.py](https://github.com/HolmesGPT/holmesgpt/blob/master/holmes/plugins/toolsets/prometheus/prometheus.py) 실측)
  - 설정: `subtype`(prometheus/victoriametrics/…), `prometheus_url`, `query_timeout_seconds_default`(20)/`hard_max`(180), `tool_calls_return_data`, `additional_labels`([prometheus.md](https://github.com/HolmesGPT/holmesgpt/blob/master/docs/data-sources/builtin-toolsets/prometheus.md))
  - 이상탐지·예측·상관 도구는 **없다.** 지침 템플릿에 있는 관련 문장은 스파이크 조사 시 `max_points`를 높이라는 한 줄뿐이다.
- 그 밖의 메트릭 계열: `grafana-mcp`(Grafana 공식 MCP를 통한 대시보드·쿼리), victoriametrics, datadog, newrelic 등. 이상탐지·상관 기능은 **데이터 소스 쪽 기능을 호출할 때만** 쓸 수 있다(예: Grafana Cloud의 Sift. §3 참조).
- **ML·통계 도구 결합 권장 패턴**: 공식 문서와 이슈에 **전용 가이드가 없다.**
  - 이슈 검색 "anomaly detection"·"forecast"·"machine learning"·"statistical"·"correlation" 결과는 평가 케이스와 toolset 추가 건뿐이었다(2026-09-17 GitHub search).
  - 평가 픽스처 `73a/73b_time_window_anomaly`는 **LLM이 로그 시간창에서 이상을 추론**하는지 시험한다([fixtures](https://github.com/HolmesGPT/holmesgpt/tree/master/tests/llm/fixtures/test_ask_holmes)). ML을 내장하지 않고 LLM 추론과 데이터 도구로 푸는 설계 철학으로 해석된다(△ 해석).
  - Operator(K8s CRD 기반 Scheduled/Triggered HealthCheck)는 **alpha**이고, *"Each health check triggers an LLM call"*이라 과금 경로다([operator/index.md](https://github.com/HolmesGPT/holmesgpt/blob/master/docs/operator/index.md)). 우리 비-K8s 환경과 D-127(과금 승인) 기준에서 부적합하다.

### 2.4 컨텍스트 관리: ML 도구 결과 형태에 주는 제약
원문: [context-management.md](https://github.com/HolmesGPT/holmesgpt/blob/master/docs/reference/context-management.md)

1. **단일 도구 결과가 크면 디스크로 내보낸다.** `TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_PCT`를 넘는 결과는 파일로 저장하고, 대화에는 미리보기와 경로만 남긴다. 디스크를 쓸 수 없으면 *"drops the data entirely"*.
2. **대화 이력 압축**은 LLM 호출이므로 비용이 생긴다. `ENABLE_CONVERSATION_HISTORY_COMPACTION`은 기본 true다.

→ ML MCP 도구는 **원시 시계열을 반환하지 말고** 다음만 반환해야 한다.
- 판정
- 점수(상위 k개)
- 근거 지표의 요약 통계
- 창·모델 버전

### 2.5 설계 시사점(ML→HolmesGPT 공급)
1. **결정적 계산은 mcp_server 쪽(또는 전용 ML 워커 venv)에 두고, sre_agent는 MCP로 소비만 한다.** D-139의 "sre_agent 양방향 import 0"과 일치하고, sre_agent venv(py3.13, numpy 미설치)를 가볍게 유지한다.
2. 도구 응답은 **text content에 JSON 문자열**로 넣고 `structuredContent`를 병행한다. 0.36.0은 text만 소비하고, 0.42.0+는 둘 다 소비한다.
3. `llm_instructions`와 SKILL.md로 **"언제·어떤 순서로" ML 도구를 쓰는지** 결정적으로 지시한다.
4. 전송은 streamable-http로 두고, 인증 헤더와 `health_check_tool`을 설정한다.
5. 업그레이드 판단: 0.40.0 보안 수정과 0.42.0 structuredContent를 근거로 **0.42.x로 올리는 검토**를 권고한다. 단 D-127에 따라 실 LLM 회귀 실행은 건별 승인을 받는다. pyproject에 상한 핀이 없으므로 **폐쇄망 wheelhouse 버전이 사실상 고정점**이다.

---

## §3 국내·상용 관측 솔루션의 AI 기능

> 이 절의 모든 기능 서술은 **벤더 공개 서술 — 독립 검증 아님**이다.

### 3.1 기능 비교표

| 벤더/제품 | AI 기능(공개 명칭) | 공개된 메커니즘 서술 | 외부 연동 표면 | 폐쇄망 | 출처 |
|---|---|---|---|---|---|
| **제니퍼소프트 JENNIFER 5**(APM) | **Anomaly Event**(이상치 탐지) · **Metrics 상관분석** · **X-View 패턴 인식**(파도치기·폭포수·물방울·단순 폭주 4종 기본 패턴 + 사용자 패턴) · 유사 애플리케이션 검색 · 대표 트랜잭션 필터링 · **Application Insights**(규칙 기반, 최근 10분 분류) · 인사이트 챗 · 스택트레이스/트랜잭션/프로파일 인사이트(LLM) · 도움말 챗봇(RAG) | 이상치: 과거 데이터로 정상 기준선을 자동 설정(통계). 메트릭 이상 탐지 대상은 응답시간·액티브 서비스·동시 사용자·CPU·메모리 5종. 상관: **Pearson 상관계수 + 시차(time-lag) 상관**. X-View: **Few-shot 딥러닝을 브라우저에서 실행**. 인사이트 챗: **Open API를 tool로 호출**하고 호출 내역을 공개 | ✔ **Open API**: OpenAPI 3.0.3, `info.version` 5.6.4(gh-pages 스펙 실측). `/api/dbmetrics/{domain,instance,business}`, `/api/realtime/*`, `/api/dbsearch/event`, `/api/dbsearch/error`, `/api/activeService/list`, `/api/transaction/*` 등. Bearer 토큰. ✔ **공식 MCP**: LLM 프록시 서버가 MCP 서버를 겸한다. **Streamable HTTP `<llm-proxy>:port/mcp`**, 헤더 `X-Jennifer-Api-Url`·`X-Jennifer-Api-Token`, *"MCP 서버는 제니퍼 OpenAPI를 통해 데이터를 조회하므로"*, **5.6.5+ · JDK 17+**, 도구 예시 `metrics_list`·`transaction_list_by_application`·`sql_statistics`. 이벤트 어댑터(SNMP·Slack·PagerDuty 등, Java) | ✔ 프라이빗 서버 LLM(vLLM 권장·OpenAI 호환·Solar·Azure OpenAI·Bedrock). **브라우저 LLM**: 완전 폐쇄망 0순위는 LiteRT-LM Web(Gemma4-E2B-IT), 2순위는 ONNX Runtime(Qwen3-0.6B) | [인사이트 블로그 2025-12-22](https://jennifersoft.com/ko/blog/tech/2025-12-22-jenniferai-jenniferinsights/) · [2026-01-26](https://jennifersoft.com/ko/blog/tech/2026-01-26-jenniferai-aimonitoring/) · [주요 기능](https://jennifersoft.com/ko/product/apm/features/) · [설치가이드 AI/MCP 장](https://docs.jennifersoft.com/ko/jennifer5_installation_guide/1cb5cd771533968d)(원문 markdown 직접 fetch) · [OpenAPI 스펙](https://openapi.jennifersoft.com/)([gh-pages](https://github.com/jennifersoft/jennifer5-open-api)) · [어댑터 리포](https://github.com/orgs/jennifersoft/repositories) |
| **엑셈 XAIOps(싸이옵스)** | 이상탐지 · 부하 예측 · 장애 예측 · 근본원인분석 · 비정상 로그 식별 · 스마트 알람 · LLM 챗봇 **QURI(큐리)** | *"각 지표별로 최적의 3가지 알고리즘 모델"*(△ 검색 스니펫, 원문 블로그는 인증서 만료로 접근 실패) · 딥러닝 다차원 hidden layer(△) · **30~60분 선행 예측**(✔ 공식 페이지) · 30분 이내 단기 부하 예측(△) · 1~12개월 장기 부하 예측(✔ 2023-11 기사) · "정확도 약 90% 이상"(벤더 주장, 검증 불가) · LLM이 이상탐지 결과를 해석하고 해결책 제안(✔ 2025-03 기사) · **알고리즘 명칭 비공개** | DPM·APM/E2E·서버·네트워크·ITSM 이벤트 통합(카드사 구축 기사). **외부 공개 API·MCP 문서는 미확인** | 공공·금융 온프레미스 구축 사례 다수(기사). 2025-12 발표한 **엑셈블(ExemBL)**은 온프레미스·폐쇄망용 LLM·에이전트 운영 플랫폼(2026-04 기사) | [EXEM AIOps](https://ex-em.com/en/service/aiops) · [AI타임스 2023-11-10](https://www.aitimes.kr/news/articleView.html?idxno=29350) · [ZDNet 2025-03-18](https://zdnet.co.kr/view/?no=20250318103039) · [인더스트리뉴스 2024-01-08(QURI)](https://www.industrynews.co.kr/news/articleView.html?idxno=52038) · [더벨 2026-04-16](https://m.thebell.co.kr/m/newsview.asp?newskey=202604141519167040104944) |
| **엑셈 MaxGauge(DPM) / InterMax(APM)** | MaxGauge 자체의 AI 이상탐지·예측 기능 공개 서술은 **미확인**. AI 기능은 XAIOps가 이 데이터를 받아 수행하는 구조로 서술된다(△) | — | **외부 공개 REST API 문서 미확인** | — | 검색 결과 없음(§6) |
| **와탭(WhaTap)** | **Metric Anomaly Detection**(AI 이상 탐지) · AI Assistant · AI 에이전트 | *"compare the patterns of various metrics with the expected patterns learned by the AI"*. 과거 반복 패턴으로 예측 대역을 만들고 이탈 정도를 주황/빨강 점으로 표시. 드래그 구간 기준 최대 6시간. **알고리즘·학습 기간 비공개** | ✔ **공식 오픈 MCP 서버** [whatap-open-mcp](https://github.com/whatap/whatap-open-mcp)(MIT, Node.js, 2026-02 생성, 2026-09-16 push). 도구 8종 중 **`whatap_apm_anomaly`**는 MXQL 4종(TPS·응답시간·에러·액티브 트랜잭션)을 병렬 조회한 뒤 **MCP 서버 안에서 통계 분석**해 지연 급증·에러 폭증·TPS 하락·트랜잭션 적체를 요약한다(`src/tools/yard.ts` 실측). `WHATAP_API_URL`로 온프레미스·정부망 지정 가능 | 온프레미스 URL 오버라이드 지원(README) | [WhaTap Docs](https://docs.whatap.io/en/php/metrics-detect-anormal) · [whatap-open-mcp](https://github.com/whatap/whatap-open-mcp) |
| 참고: **Grafana Cloud ML** | Metric forecasting · Outlier detection · **Sift**(자동 조사) · dynamic alerting | Outlier: **DBSCAN**(가장 큰 밀도 클러스터 밖 시리즈) / **MAD**(24시간 rolling median 대비 거리). 민감도 Low/Medium/High. Forecast: 기본 모델이 기존 추세를 따른다고 가정하고, 학습 기본 90일·불확실성 구간 0.95(알고리즘명 비공개). Sift: Error Pattern Logs(유사 로그 그룹의 급증), Kube Crashes(OOMKill 등) 체크 | Grafana Cloud 전용. 오픈소스 **augurs**(Rust+Python/JS 바인딩, Apache-2.0: MSTL/ETS 예측·MAD/DBSCAN 이상치·변경점·계절성 탐지)를 scenes-ml이 WASM으로 사용. PyPI `augurs`는 0.8.0(2024-12-23)으로 Rust 릴리스 v0.10.2(2026-02-24)보다 뒤처짐 | Cloud 기능은 폐쇄망 불가 | [Outlier](https://grafana.com/docs/grafana-cloud/machine-learning/dynamic-alerting/outlier-detection/) · [Forecasting](https://grafana.com/docs/grafana-cloud/machine-learning/dynamic-alerting/forecasting/) · [Sift](https://grafana.com/docs/grafana-cloud/machine-learning/sift/sift/) · [augurs](https://github.com/grafana/augurs) |
| 참고: **Elastic ML** | Anomaly detection jobs | *"clustering, various types of time series decomposition, Bayesian distribution modeling, and correlation analysis"*. 점수는 노이즈 감소를 위해 집계하고 정규화해 순위화 | Elasticsearch 내장 | 자가 설치 가능하나 **유료 구독 필요**(티어 미확인: 문서는 "appropriate license"만 명시) | [알고리즘](https://www.elastic.co/docs/explore-analyze/machine-learning/anomaly-detection/ml-ad-algorithms) · [요건](https://www.elastic.co/guide/en/security/current/ml-requirements.html) |
| 참고: **Datadog** | Anomaly monitor · Watchdog · **Toto**(오픈 가중치) | **basic** = *"simple lagging rolling quantile"*. **agile** = *"robust version of the SARIMA algorithm"*. **robust** = *"seasonal-trend decomposition"*. 계절 알고리즘은 **최소 계절 주기의 3배 이력**이 필요하고 최대 6주를 쓴다. Toto 2.0 블로그는 Watchdog 적용 여부를 언급하지 않는다 | SaaS | 불가(Toto 가중치만 반입 가능) | [Anomaly monitor](https://docs.datadoghq.com/monitors/types/anomaly/) · [Watchdog](https://docs.datadoghq.com/watchdog/alerts/) · [Toto 2.0 블로그](https://www.datadoghq.com/blog/ai/toto-2/) |

### 3.2 우리가 구현할 것과 벤더 API로 가져올 것

| 영역 | 판단 | 근거 |
|---|---|---|
| **WAS 단위 이상(응답시간·액티브 서비스·동시사용자·CPU·메모리)** | **제니퍼 Anomaly Event를 가져온다.** 중복 구현하지 않는다 | 제니퍼가 5개 지표 자동 기준선을 이미 제공한다. 결과를 받을 경로는 `/api/dbsearch/event`로 추정되나, **Anomaly Event가 이 API로 조회되는지는 미확인**이다(스펙에 `anomaly` 문자열 0건). 확인 전에는 `/api/dbmetrics/instance`로 받은 지표에 우리 기본 탐지를 **보조로만** 건다 |
| **X-View 패턴(파도치기·폭포수)·트랜잭션/스택 해석** | **제니퍼에 맡긴다**(브라우저 딥러닝·LLM 인사이트) | API로 패턴 판정 결과를 노출하는지 **미확인**. 우리는 `/api/transaction/*`·프로파일 원문을 증거로 sre_agent에 공급만 한다 |
| **DB 성능 이상·부하 예측** | **XAIOps를 쓰는 기관이면 그 결과를 가져온다. 아니면 DPM 지표에 우리 1차 탐지를 건다** | XAIOps는 30~60분 선행 예측과 RCA를 주장한다. 그러나 **외부 API가 미확인**이라 연동 가능성부터 실측해야 한다(운영 기관의 XAIOps 도입 여부도 미확인) |
| **크로스 소스 상관·RCA**(폴스타 인프라 ↔ 제니퍼 WAS ↔ DPM DB ↔ Prometheus) | **우리가 구현한다(핵심 차별 영역)** | 어느 벤더도 타 벤더 데이터를 합친 RCA를 제공하지 않는다. 제니퍼 상관분석은 제니퍼 지표 안에서만 한다. 폴스타 토폴로지 + 시차 상관 + 변경점 정렬 + (2차) 인과 기여도 |
| **알람 노이즈 게이트 정량 신호** | **우리가 구현한다** | 노이즈 게이트는 우리 스트림(`alarm:raw`) 위의 온라인 판단이다. river(HalfSpaceTrees)와 STL 잔차 z/MAD를 쓴다. 벤더 이상 이벤트는 **입력 신호 중 하나**로 편입한다 |
| **용량·소진 예측**(디스크·테이블스페이스·세션) | **우리가 구현한다**(statsmodels → statsforecast → TSFM 단계) | 폴스타는 AI 예측을 공개하지 않는다(미확인). XAIOps 장기 예측은 도입 기관에 한해 대체 가능 |
| **LLM 해석·챗** | **벤더 챗봇은 가져오지 않는다.** HolmesGPT와 text2sql이 담당한다 | 제니퍼 MCP 채택 판정은 plans/87 §0.5에서 이미 「미채택 — Open API 직접 호출」로 확정됐다. 이유는 LLM 프록시 제품 전체를 운영해야 하고 권한 경계가 동일하기 때문이다 |
| **메커니즘 기준선** | Grafana(MAD/DBSCAN 그룹 이상치), Datadog(rolling quantile, robust STL, **3× 계절 이력 규칙**), Elastic(분포 모델링 + 정규화 점수)을 **우리 1차 알고리즘 설계 기준**으로 차용 | 공개 서술된 단순 기법과 TSB-AD 결론이 일치한다 |

---

## §4 오픈 데이터셋·벤치마크

| 데이터셋 | 내용·규모 | 라이선스·이용 조건 | 알려진 문제 | 폐쇄망 반입 | 사내 평가 적합성 |
|---|---|---|---|---|---|
| **RCAEval** ([GH](https://github.com/phamquiluan/RCAEval), [HF](https://huggingface.co/datasets/phamquiluan/RCAEval)) | 9개 데이터셋, **735 장애 케이스**, 11종 결함. RE1(메트릭만, 375) · RE2(메트릭·로그·트레이스, 270) · RE3(코드 수준 결함, 90) · TORAI. 시스템은 Online Boutique·Sock Shop·Train Ticket. 근본원인 서비스·지표 주석 포함 | 데이터·자체 코드 **MIT**(HF 카드 `mit`). 포함 베이스라인은 개별 라이선스(일부 무라이선스) | 벤치마크용 K8s 마이크로서비스 결함 주입이라 **인프라 DB 중심 도메인과 차이**가 있다 | HF Parquet **3.44GB**(실측). 파일 단위로 받을 수 있다 | **상**. RCA 순위 알고리즘 회귀 테스트(AC@k)에 적합하다. 1차 RCA 구현의 오프라인 검증 기준 |
| **AIOps Challenge**(청화대 NetMan) ([2020 Data](https://github.com/NetManAIOps/AIOps-Challenge-2020-Data)) | 2020: China Mobile Zhejiang 실운영 마이크로서비스(비즈니스·인프라 지표·트레이스·장애 라벨). 2021: 상업 은행 2곳 시스템. 2022: Hipster Shop(△ 검색 요약) | README: *"only uses the basic data for non-commercial purposes such as scientific research or classroom teaching"*(대회 조건). GitHub 라이선스 없음 | 비상업 조건. 연도별 형식이 제각각 | Tsinghua Cloud/Google Drive 링크 | **중**(2021 은행 데이터가 도메인상 가깝다). **사내 평가 사용도 비상업 조건 해석에 법무 확인 필요** |
| **SMD**(Server Machine Dataset) ([OmniAnomaly](https://github.com/NetManAIOps/OmniAnomaly)) | 28 엔티티 × 38차원. 학습 708,405 / 테스트 708,420, 이상 비율 4.16% | 리포 LICENSE는 MIT(코드). **데이터 별도 라이선스 명시 없음(미확인)** | Wu & Keogh가 지적한 벤치마크 결함(자명성·비현실적 이상 밀도·오라벨·run-to-failure 편향)이 이 계열 데이터 전반에 해당한다([arXiv 2009.13807](https://arxiv.org/abs/2009.13807), TKDE 2023). 2026-06 논문은 *"no cross-channel rupture occurs without an accompanying univariate deviation"*으로 다변량 벤치마크 이상이 대부분 단변량임을 보고했다([arXiv 2606.02670](https://arxiv.org/abs/2606.02670)) | 소용량(리포 동봉) | **중하**. 서버 지표라 형식은 가깝지만 점수 해석에 주의 |
| **PSM**(Pooled Server Metrics, eBay) ([RANSynCoders](https://github.com/eBay/RANSynCoders)) | eBay 서버 노드 지표 | 코드 BSD-3, **샘플 데이터 CC BY 4.0**(README 명시) | 위와 동일한 벤치마크 결함 우려 | 리포 동봉 | **중**(라이선스가 명확해 사내 사용이 쉽다) |
| **SWaT**(iTrust SUTD) ([Datasets](https://www.sutd.edu.sg/itrust/itrust-labs/datasets/)) | 수처리 테스트베드 ICS 센서·액추에이터 | **신청서 제출 후 제공**(최대 3영업일). 논문 사용 시 출처 명시. 2022-07-01 이후 후속 지원 없음. 재배포 조건 미확인 | ICS 도메인이라 IT 인프라와 다르다. 위 결함 논의 대상 | 신청 절차가 필요하다 | **하**(도메인 불일치) |
| **TSB-AD-U/M** ([GH](https://github.com/TheDatumOrg/TSB-AD)) | 40개 원천에서 큐레이션한 **1,070 시계열**. 단변량/다변량 분리. 평가지표 VUS-PR 권고 | 큐레이션 스텝은 Apache-2.0. **원천 데이터셋마다 개별 라이선스**(프로젝트 페이지 표 참조) | 결함 논의를 반영해 재큐레이션한 것이 장점 | zip 다운로드(`thedatum.org`). 크기 미확인 | **상**. 이상탐지 알고리즘 선택(VUS-PR)의 1차 기준. 원천 라이선스 선별 필요 |
| **LogHub** ([GH](https://github.com/logpai/loghub)) / **Loghub-2.0** ([GH](https://github.com/logpai/loghub-2.0)) | HDFS·Hadoop·Spark·BGL·Thunderbird·Windows·Linux·OpenSSH 등 시스템 로그. 2.0은 템플릿 주석 대규모화(HDFS 1,117만 줄·46 템플릿 등) | LICENSE: *"freely available for research or academic work"* + 인용 조건. **상업·사내 운영 목적 사용 조건 불명확** | 원본이 **비익명화**(*"NOT sanitized, anonymized"*) | Zenodo. 대형(Thunderbird 29.6GiB, Windows 26.1GiB) | **중**. Drain3 파서 정확도 평가용으로는 적합(소형 시스템 위주 선별) |
| **BOOM**(Datadog) ([HF](https://huggingface.co/datasets/Datadog/BOOM)) | **관측 지표 예측 벤치**. 약 3.5억 포인트, 32,887 variate, 2,807 시계열. 제로 인플레이션·급변·비정형 계절성·고카디널리티 특성 명시. 고객 데이터 미포함(사전 운영환경 내부 모니터링) | **Apache-2.0** | Datadog 자사 모델(Toto) 평가에 유리하게 구성됐을 가능성(△ 이해상충 일반론) | HF **2.81GB**, 8,429 파일(실측) | **상**. 용량·지표 예측 모델 선택 기준(도메인이 가장 가깝다) |
| **GIFT-Eval**(Salesforce) ([HF](https://huggingface.co/datasets/Salesforce/GiftEval), [GH](https://github.com/SalesforceAIResearch/gift-eval)) | 범용 예측 벤치(Toto 블로그 기준 97개 데이터셋 설정). 리더보드는 HF Space | HF 카드 **Apache-2.0**. 원천 데이터 라이선스 혼재 여부 **미확인** | 범용이라 관측 도메인 비중이 작다. TSFM 사전학습 데이터와 겹쳐 오염 가능. TimesFM 3.0은 fev-bench 중복 데이터를 제외했다고 명시 | HF **1.59GB**(실측) | **중**. TSFM 일반 성능 참고용 |

**평가 설계 권고**
1. **사내 라벨셋이 최우선이다.** 폴스타 알람 이력 + 장애 보고서(ITSM)에서 "근본원인 호스트·지표" 라벨을 만든다. 공개 벤치는 알고리즘 후보를 걸러내는 데만 쓴다.
2. 이상탐지는 TSB-AD-U/M에서 원천 라이선스가 허용되는 부분집합으로 **VUS-PR**을 본다. 점-조정(point-adjust) F1은 쓰지 않는다(결함 논의 반영).
3. RCA는 RCAEval RE1(메트릭 전용, MIT)로 **AC@1/AC@3** 회귀 기준을 만든다.
4. 예측은 BOOM(Apache)에서 CRPS·MASE를 본다. 사내 폴스타 `cmm_metric_stat_[h,d]` 이력으로 재검증한다.

---

## §5 권장 스택

폐쇄망·CPU·라이선스(BSD/MIT/Apache만) 기준 최소 구성이다.

### 5.1 원칙
- **배치 위치**: 계산은 **mcp_server(루트 venv) 안의 ML 도구** 또는 **전용 `ml_worker` 프로세스·venv**에서 한다. sre_agent·노이즈 게이트에는 **결과(MCP 도구 응답 또는 Redis 신호)만** 공급한다. D-139 경계와 같은 구조다.
- **venv 분리 규칙**: 루트 venv(pandas 3·scipy 1.18·sklearn 1.9)와 충돌하는 패키지는 루트에 넣지 않는다. 대상은 statsforecast·sktime·gluonts/Toto·dowhy(py<3.13)·lingam이다.
- **모델 파일**: pickle·joblib은 신뢰 경계를 넘지 않게 한다. sklearn 모델은 **skops**, 추론 격리는 **ONNX**, TSFM은 **safetensors만** 허용한다.
- **라이선스 배제 목록**
  - 비상업: TimesFM 3.0, Moirai 전 버전(CC-BY-NC)
  - 조건부 상용: TiRex 1.x(NXAI)
  - BSL: Alibi Detect
  - 카피레프트: tigramite(GPL-3.0, 결합 배포 시)
  - 대회 비상업 조건: AIOps Challenge 데이터(법무 확인 전 사용 보류)

### 5.2 단계별 구성

| 단계 | 목적 | 1차 후보(채택) | 2차 후보(대안·확장) | 반입 부담 |
|---|---|---|---|---|
| **0 (현행 재사용)** | 기준선 | `statsmodels`(STL 잔차, ETS) · numpy/pandas · `scikit-learn` IsolationForest(설치본 1.9.0) | — | 0 |
| **1 (torch 없음)** | 이상탐지·변경점·온라인 신호 | `pyod` 3.6.5 · `river` 0.26.1(HalfSpaceTrees, py>=3.11) · `ruptures` 1.1.10(py<3.14) · `stumpy` 1.14.1 | `augurs` Python 바인딩(MAD/DBSCAN 그룹 이상치). 다만 PyPI 0.8.0이 2024-12로 정체 | 소(wheel 수 MB대, numba 공유) |
| **1** | RCA | **자체 구현**: 토폴로지 가중 시차 교차상관 + 변경점 시각 정렬 + BARO RobustScorer 방식 재구현(의존 0) | `fse-baro` 0.2.2 직접 사용(MIT, 런타임에 pytest 등 불필요 의존) | 0~소 |
| **1** | 로그 템플릿 | `drain3` 0.9.11(sdist, `poetry-core`·jsonpickle·cachetools 동반) | `logparser3`(평가 비교용) | 소 |
| **1** | 모델 영속화 | `skops` 0.15.0 · SQLite 모델 메타(구간·파라미터·SHA-256) | `onnxruntime` 1.30.0 + `skl2onnx` 1.20.0 | 소 |
| **2 (전용 워커 venv)** | 예측 고도화 | `statsforecast` 2.1.1(MSTL·AutoETS·AutoARIMA). **pandas<3 venv** | `darts` 0.47.0(pandas 3 허용, 의존 31개) | 중 |
| **2** | 인과 RCA | `causal-learn` 0.1.4.8(PC·Granger) → `dowhy` 0.14 `gcm.attribute_anomalies`/`distribution_change`(**py3.13 venv**, 또는 py3.12 + scipy<=1.15.3) | `lingam`(py≤3.12 + scipy<=1.13.1 전용) | 중(cvxpy 등) |
| **2** | 특징 추출(알람 분류기) | `tsfresh` 0.21.2 | `tsfel` 0.2.0 | 소~중 |
| **2** | 모델 레지스트리 | (필요 시) `mlflow-skinny` | — | 중 |
| **3 (보안 심사 후, 선택)** | 제로샷 예측·희소 이력 지표 | **Chronos-Bolt small(48M·191MB) 또는 chronos-2-small(28M·112MB)**: Apache, safetensors, CPU 공식 지원 | **Toto-2.0-4m/22m**(Apache, 관측 특화, **py>=3.12**, gluonts pandas<3 → 전용 venv) · TimesFM **2.5**(Apache, 925MB) | 대(torch CPU wheel 별도 인덱스 + transformers 등) |

### 5.3 산출물 계약(HolmesGPT·노이즈 게이트 공급)
- **MCP 도구**(mcp_server): `detect_anomalies`, `detect_changepoints`, `rank_root_causes`, `forecast_exhaustion`, `ml_model_status`(헬스체크용)
  - 응답은 **text JSON**(0.36 호환)과 `structuredContent`를 병행한다.
  - 상위 k개, 요약 통계, 창·모델 버전만 담는다. 원시 시계열은 반환하지 않는다(§2.4 spill 회피).
- **노이즈 게이트 신호**: 알람 단위로 `anomaly_score`(river), `stl_resid_z`, `changepoint_near`(bool/거리)를 Redis 필드로 공급한다. 플래그 기본은 off다(`plans/80` §5.4-③ 원칙).
- **벤더 신호 편입**: 제니퍼 이벤트(Anomaly Event 포함 여부 확인 후)와 XAIOps 예측(API 확인 후)은 **입력 특징**으로 받고 재계산하지 않는다.

---

## §6 조사 한계와 미확인 목록

1. **CPU 추론 지연·메모리 미실측.** 지시에 따라 아무것도 설치하지 않았다. TSFM의 CPU 적합성은 모델 카드 서술(Chronos-2·Bolt "CPU 지원", Toto-4m "Edge/CPU 권장")에만 근거한다. 도입 전 격리 환경 벤치가 필요하다.
2. **폐쇄망 운영 서버의 Python·패키지 버전 미확인.** 충돌 판정은 개발 머신 루트 venv(py3.12.11·pandas 3.0.5·numpy 2.5.1·scipy 1.18.0) 실측 기준이다. CLAUDE.md는 본체 ">=3.11"이다. py3.11 운영이면 numpy 2.5·scipy 1.18(py>=3.12 요구)을 쓸 수 없으므로 판정이 달라진다.
3. **HolmesGPT**
   - holmesgpt.dev 렌더 페이지는 WebFetch가 실패해 **GitHub `docs/` 원문(master)**으로 대체했다. 문서는 0.42 master 기준이다.
   - 0.36.0에서 `additional_toolsets`(Python toolset)·`llm_instructions` 키가 동작하는지는 **미확인**이다. `health_check_tool`은 우리 코드가 0.36.0에 이미 넘기고 있지만(`mcp_service.py:115`), 실제로 적용되는지는 검증하지 않았다.
   - 0.36.0이 `structuredContent`를 버리는 동작은 PR #2459 본문의 "Before" 서술에 근거한다(0.36.0 코드는 직접 읽지 않았다).
4. **제니퍼**
   - Anomaly Event·X-View 패턴 판정·Application Insights 결과가 **Open API로 조회되는지 미확인**이다. 스펙 5.6.4에 `anomaly` 문자열이 0건이다. `/api/dbsearch/event` 응답에 포함되는지는 실 서버 확인이 필요하다.
   - 5.7.0 릴리스(2026-08-13)는 plans/87 [J-18] 인용이다. 이번엔 SPA라 원문을 재fetch하지 못했다.
   - JENNIFER 6 공개 근거는 없다.
5. **엑셈**
   - XAIOps 알고리즘 명칭·모델 구조는 **비공개**다. "지표별 3가지 알고리즘"은 검색 스니펫(△)이고, 원문 블로그(blog.ex-em.com)는 인증서 만료로 접근하지 못했다.
   - **MaxGauge·InterMax·XAIOps의 외부 REST API/MCP 공개 문서는 미확인**이다.
   - 우리 운영 기관의 DPM이 MaxGauge인지, XAIOps를 도입했는지도 미확인이다.
6. **와탭**: 이상탐지 알고리즘·학습 기간이 비공개다. 우리 환경 도입 여부는 미확인(참고 사례로만 다룸).
7. **Grafana Cloud 예측 알고리즘명**: Cloud 문서에 명시가 없다. augurs(MSTL/ETS)는 scenes-ml(대시보드 확장)에서 쓰인다는 점만 확인했고, Cloud ML 백엔드와 동일한지는 미확인이다.
8. **Elastic ML** 필요 구독 티어는 미확인이다(문서는 "appropriate license"로만 표기).
9. **데이터셋 라이선스 공백**
   - SMD 데이터 자체 라이선스 명시 없음
   - SWaT 재배포 조건
   - GIFT-Eval·TSB-AD 원천 데이터셋별 라이선스(개별 확인 필요)
   - AIOps Challenge 2021/2022 개별 조건(2020 README만 확인)
   - LogHub의 사내 운영 목적 사용 가부
10. **TiRex-2 `model.ckpt` 직렬화 형식**: pickle 계열로 추정(△)했을 뿐 파일 헤더는 확인하지 않았다. 보안 심사 시 `torch.load(weights_only=True)` 가능 여부를 확인해야 한다.
11. **cdt 라이선스 표기 불일치**(GitHub MIT ↔ PyPI "Apache 2.0")는 원인 미확인이다(회피 대상이라 추가 조사 생략).
12. **Kats**: 2026년 커밋이 있으나 PyPI 릴리스가 없는 이유(내부 Meta 동기화 추정)는 미확인이다. 판정(회피)에는 영향이 없다.
13. 벤더 성능 주장("정확도 90% 이상", "BOOM 전 지표 1위")은 **독립 검증되지 않았다.**
