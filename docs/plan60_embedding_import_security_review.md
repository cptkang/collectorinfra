# Plan 60 B-7 — 로컬 임베딩 모델·패키지 폐쇄망 반입 보안 협의 문서

> 작성일: 2026-07-23 | 대상: Plan 60 B-7(로컬 임베딩 — §15.3 L-2/L-4) | 관련 결정: D-114, §15.4 D-035
> 목적: 사내 폐쇄망 운영 환경에 로컬 임베딩 모델·의존 패키지를 반입하기 위한 **보안 검토·행정 협의** 근거 정리.
> **본 문서는 협의용 근거이며, 실제 반입·운영 활성화는 보안팀 검토 완료를 선행 조건으로 한다.**

---

## 1. 목적·범위 (무엇을·왜)

Plan 60 B-7은 알람 노이즈 캔슬링에 **로컬 문장 임베딩**을 도입한다. 용도는 **관측성·주석 전용**이며(§15.4 D-035 불변식):

- **L-2 의미적 근접중복 주석**: 결정적 지문(`compute_fingerprint`)이 놓치는 "표현만 다른 동일 재발"을 임베딩 유사도로 감지해 **재발 count 병합 후보로 주석**한다. **억제 판정은 결정적 지문 불변** — 임베딩은 판정에 영향 0.
- **L-4 토폴로지+텍스트 융합 주석**: E4 의존성 그래프의 root 귀속에 알람 텍스트 유사도를 첨부해 **root 귀속 신뢰도를 설명 보강**한다. **cascaded/SUPPRESS/DASHBOARD 판정 불변**.

**임베딩 출력은 결정적 게이트 판단(SUPPRESS/PAGE/티어)·억제 지문·상관 군집·다홉 억제를 절대 변경하지 않는다**(코드 검증: `src/alarm/domain/`의 임베딩 참조 0건, `decide_notification` 시그니처에 임베딩 인자 없음, 티어 판정 주석 유무 비트동일 테스트 고정). 임베딩 유사 판단을 근거로 한 자동 등록(유사어·매핑 등) 쓰기 지점은 없다(오염 자기강화 루프 차단).

---

## 2. 반입 대상 — 모델

| 모델 | 차원 | 다국어 | 라이선스 | 가중치 형식 | 비고 |
|------|------|--------|----------|-------------|------|
| **`intfloat/multilingual-e5-small`** (✅ **확정** — 사용자 지시·2026-07-23 실측) | 384 | ✅ 100+ | MIT | safetensors | 경량(**466MB**)·한/영 혼용 알람 근접중복 완전 분리(§5). 확정 임계 **0.87**. |
| `BAAI/bge-m3` (대안·더 넓은 마진) | 1024 | ✅ 100+ | MIT | safetensors | 고성능·무거움(~2.3GB)·분리 마진 3.5배(§5). e5-small 마진이 문제되면 교체 후보. |
| `sentence-transformers/all-MiniLM-L6-v2` (참고) | 384 | ❌ 영어 | Apache-2.0 | safetensors | 한국어 판별 약함 — **다국어 모델 필요성 실증용 대조**. |

### 확정 모델 아티팩트 — `intfloat/multilingual-e5-small` (2026-07-23 로컬 실측)

| 파일 | 용량 | 비고 |
|------|------|------|
| **`model.safetensors`** | 449 MB | 가중치 — **safetensors(비pickle·검증형)**. SHA256 = `7a77d5da5ee721c7c740e4082447d3026b6521d3eac5edb93edb6aa88f03b7d7` |
| `tokenizer.json` | 16 MB | fast 토크나이저 |
| `config.json`·`config_sentence_transformers.json`·`sentence_bert_config.json`·`tokenizer_config.json`·`modules.json` | <1 MB | 설정 |
| `1_Pooling/`·`2_Normalize/` | <1 MB | ST 모듈(mean pooling + L2 정규화) |
| `README.md` | 0.5 MB | 모델 카드 |
| **합계** | **466 MB** | `model_type=bert`·hidden 384·max_position 512 |

- **pickle(`pytorch_model.bin`) 없음** — safetensors만 반입(임의 코드 실행 위험 0). ✅
- **라이선스 MIT**(모델). 재배포·사내 사용 허용.
- **해시 대조**: 반입 시 위 `model.safetensors` SHA256을 배포처(HF 공식 리포지토리 릴리스)와 재대조. 토크나이저 파일도 동일 절차.

> 본 실측은 dev 환경에서 세션 오프라인 env(`HF_HUB_OFFLINE=1`) 하에 **로컬 디렉토리에서 로드**해 수행했다(다운로드는 dev 검증 1회에 한해 오프라인 env를 임시 해제하고 수행 — provider의 런타임 다운로드 금지 설계는 불변). 앞서 "HF hub 차단"으로 보고했던 것은 실제 네트워크 차단이 아니라 **세션에 남아 있던 `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE` 환경변수** 때문이었음이 재실측(`curl huggingface.co` → HTTP 200)으로 확인됐다.

---

## 3. 반입 대상 — 패키지 (버전 고정)

실측 dev 설치 기준(운영 반입 시 동일 버전 고정 권장, 폐쇄망 wheel 반입):

| 패키지 | 버전(실측) | 비고 |
|--------|-----------|------|
| `sentence-transformers` | 5.6.0 | 임베딩 상위 API |
| `torch` | 2.13.0 | 추론 백엔드(CPU) — 최대 반입 용량 |
| `transformers` | 5.14.1 | 모델 로더 |
| `tokenizers` | 0.22.2 | |
| `safetensors` | 0.8.0 | pickle 회피 가중치 로더 |
| `numpy` | 2.5.1 | 코사인 |
| `scipy` | 1.18.0 | transformers 전이 |
| `scikit-learn` | 1.9.0 | transformers 전이 |

> `pyproject.toml`의 기존 `[project.optional-dependencies].semantic` 그룹(`sentence-transformers`·`numpy`)이 이를 커버한다. **필수 `dependencies`에는 편입하지 않는다**(미반입 시 앱은 정상 기동·임베딩만 inert). CPU 전용 torch wheel(경량)로 반입 용량을 줄일 수 있다.

---

## 4. 오프라인 동작 근거 (외부 통신 없음 — 코드 검증)

`src/alarm/infrastructure/embedding_provider.py`의 `AlarmEmbeddingProvider`는 **런타임 외부 통신을 하지 않도록** 다층 설계됐다:

1. **lazy import**: `sentence_transformers`·`numpy`를 클래스 메서드 내부에서만 import. 미반입 시 모듈 import는 성공하고 기능만 inert(앱 기동 영향 0).
2. **로컬 디렉토리 전용 로드**: `embedding_model_path`가 설정됐고 `os.path.isdir(path)`가 True일 때만 로드 시도. **hub 이름(비디렉토리)이면 로드 자체를 시도하지 않는다**(다운로드 시도 0 — 테스트 `test_hub_name_path_inert_no_download`가 SentenceTransformer 미호출로 실증).
3. **오프라인 환경변수 강제**: 로드 직전 `HF_HUB_OFFLINE=1`·`TRANSFORMERS_OFFLINE=1` 설정(이중 차단). SentenceTransformer는 `local_files_only` 파라미터도 지원함(실측 확인 — 추가 안전장치로 활용 가능).
4. **graceful inert**: 미설치·경로부재·비디렉토리·로드/인코딩 실패 시 **경고 1회 후 None 지속**(침묵 강등 금지·재시도 폭주 금지). 이 경우 노이즈 게이트는 임베딩 주석 없이 정상 동작(비트동일).
5. **옵트인**: `semantic_dedup_annotation_enabled`·`topology_text_fusion_enabled` 모두 기본 off. off면 provider를 생성조차 하지 않는다.

> 실측: HF hub 네트워크가 차단된 본 환경에서 캐시된 로컬 모델 스냅샷 경로를 provider에 주면 **다운로드 없이 로드·인코딩**됐고(§5), 경로가 없거나 잘못되면 inert로 graceful 강등했다.

---

## 5. 실측 결과 (dev venv·2026-07-23)

확장 케이스(근접중복 6쌍 + 이질 6쌍, 한/영 혼용 알람 텍스트)로 `AlarmEmbeddingProvider` 로컬 로드 후 분포 측정:

| 모델 | 차원 | 근접 min/mean/max | 이질 min/mean/max | **분리(근접min−이질max)** | 확정 임계 |
|------|------|-------------------|-------------------|---------------------------|-----------|
| **`multilingual-e5-small`** (확정) | 384 | 0.893 / 0.916 / 0.946 | 0.820 / 0.842 / 0.852 | **+0.041** (완전 분리·좁은 마진) | **0.87** |
| `bge-m3` (대안) | 1024 | 0.714 / 0.772 / 0.875 | 0.499 / 0.539 / 0.569 | **+0.145** (완전 분리·넓은 마진) | ~0.65 |
| `all-MiniLM`(영어·참고) | 384 | — | — | 판별 약함(근접 0.57 vs 이질 0.46) | — |

**핵심 판정**:
1. provider는 로컬 경로에서 e5-small을 **다운로드 없이**(오프라인 env 유지) 로드·인코딩한다(차원 384).
2. **e5-small은 근접중복과 이질을 완전 분리한다(+0.041)** → **유효**. 단 절대 유사도가 0.82~0.95로 압축돼 있어 **분리 마진이 bge-m3(+0.145)보다 3.5배 좁다**. 따라서 기본 임계 0.85는 부적정(이질 max 0.852 > 0.85 → 오탐)이며, **실측 분포 기준 임계 0.87**(이질 max 0.852 < 0.87 < 근접 min 0.893)로 재튜닝했다(config 반영).
3. **모델 카드 prefix 실측**: e5 계열의 `query: ` prefix는 분리를 미미하게만 개선(+0.041 → +0.045) — 본 대칭 알람-알람 비교에서는 실익이 없어 provider에 반영하지 않고(단순성) 문서에만 기록한다. 필요 시 operator가 텍스트 prefix를 붙일 수 있다.
4. **트레이드오프(사용자 판단 사항)**: e5-small은 **유효하나 마진이 좁다**. 사용자 지시대로 **확정**하되, 운영 실데이터에서 마진이 부족하면(이질 압축으로 오탐 증가) **bge-m3(마진 3.5배·단 2.3GB로 무거움)로 교체**를 권고한다. 운영 롤아웃 전 실데이터로 임계·모델 최종 재보정 필수.

> 옵트인 실모델 검증 테스트 `tests/test_alarm/test_embedding_provider_realmodel.py`(`E5_MODEL_PATH` 설정 시 실행·미설정 시 스킵)로 위 분리(근접>0.87>이질)를 고정했다.

---

## 6. 보안 검토 포인트

1. **가중치 형식 — safetensors 우선, pickle 회피**: 반입 모델은 `model.safetensors`(비실행·검증 가능 형식)만 사용하고, `pytorch_model.bin`(pickle — 임의 코드 실행 위험)은 **로드 회피**한다(가능하면 반입 아티팩트에서 제외). safetensors 0.8.0 반입.
2. **해시·서명 대조**: 반입 모델 `model.safetensors`·토크나이저 파일의 SHA256을 배포처 공식 릴리스와 대조. 패키지는 PyPI 공식 wheel 해시 대조.
3. **런타임 네트워크 무접근 검증**: 운영 배포 후 `HF_HUB_OFFLINE=1`·`TRANSFORMERS_OFFLINE=1` 환경에서 임베딩 경로가 외부 통신 없이 동작함을 방화벽 로그로 확인(§4 코드 근거 + 운영 실증).
4. **라이선스**: 모델(e5-small MIT / bge-m3 MIT / all-MiniLM Apache-2.0)·패키지(각 OSS 라이선스) 각각 사내 라이선스 정책 검토. MIT/Apache-2.0는 통상 사내 사용 허용.
5. **`trust_remote_code` 미사용**: 커스텀 모델 코드 실행 경로(원격 코드) 없음 — 표준 sentence-transformers 아키텍처만 사용.
6. **데이터 유출 없음**: 임베딩은 인프로세스 CPU 상주. 알람 텍스트가 외부로 나가지 않는다(외부 API·telemetry 없음).

---

## 7. 운영 배포 절차 (반입 승인 후)

1. 보안팀 반입 승인(모델·패키지 해시·라이선스 검토 완료).
2. 폐쇄망 내부에 패키지 wheel 반입·설치(`pip install .[semantic]` 상당, CPU torch).
3. 모델 파일을 내부 서버 로컬 디렉토리에 배치(예: `/opt/models/multilingual-e5-small/`).
4. `.env`에 `NOISE_EMBEDDING_MODEL_PATH=/opt/models/multilingual-e5-small` 설정(디렉토리 경로).
5. 옵트인 플래그 활성: `NOISE_SEMANTIC_DEDUP_ANNOTATION_ENABLED=true`(L-2)·`NOISE_TOPOLOGY_TEXT_FUSION_ENABLED=true`(L-4).
6. `embedding_similarity_threshold`를 실데이터로 재보정(모델 확정 후).
7. 미반입/비활성 시 노이즈 게이트는 임베딩 주석 없이 정상 동작(회귀 0).

---

## 8. 미결·후속

- ✅ **e5-small 아티팩트·실측 완료(2026-07-23)**: 파일 목록·`model.safetensors` SHA256·용량(466MB)·라이선스(MIT)·safetensors 확인(§2), 근접중복 완전 분리·임계 0.87 확정(§5). **반입 시 배포처 릴리스와 SHA256 재대조만 남음**(보안팀 최종 검증).
- **모델 최종 확정 유보 사항(사용자 판단)**: e5-small은 유효하나 분리 마진이 bge-m3의 1/3.5로 좁다(§5). 사용자 지시로 e5-small 확정했으나, **운영 실데이터에서 오탐(이질 압축)이 확인되면 bge-m3로 교체** 결정 여지를 남긴다.
- **hot-path 성능**: 임베딩 인코딩 예산(`embedding_timeout_seconds=2.0`)·캐시(LRU) 하에서 대량 알람 처리량 부하 실측(운영 롤아웃 전).
