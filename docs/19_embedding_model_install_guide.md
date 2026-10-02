# 임베딩 모델 설치 가이드 — multilingual-e5-small 및 관련 구성 요소

> **대상 독자**: 개발자·운영자·보안 담당자.
> **목적**: Plan 60 B-7(D-114) 로컬 임베딩 주석 기능(L-2 의미적 근접중복 주석·L-4 토폴로지+텍스트 유사도 주석)에 필요한 **임베딩 모델과 패키지를 다운로드·검증·배치·설정**하는 전 과정을 안내한다.
> **관련 문서**: 보안 반입 협의는 `plan60_embedding_import_security_review.md`, 설계 결정은 `02_decision.md` D-114, 기능 설계는 `plans/60-noise-cancellation-benchmark-refinement.md` §15.3.

---

## 1. 개요 — 설치 전에 이해할 것

### 1.1 무엇을 설치하는가

임베딩 기능은 **세 가지 구성 요소**가 모두 갖춰져야 동작한다:

| 구성 요소 | 내용 | 없으면 |
|-----------|------|--------|
| **① Python 패키지** | `sentence-transformers`(+ torch 등 전이 의존성) | 앱은 정상 기동, 임베딩만 비활성(inert) |
| **② 모델 파일** | `multilingual-e5-small` 로컬 디렉토리 (466MB) | 위와 동일 (경고 로그 1회 후 skip) |
| **③ 앱 설정** | `.env`의 `NOISE_*` 키 3개 (경로 + 옵트인 플래그 2개) | 기능 꺼짐 (옵트인 — 기본 off) |

### 1.2 핵심 설계 원칙 (반드시 숙지)

- **런타임 다운로드 절대 금지**: 애플리케이션(`AlarmEmbeddingProvider`)은 어떤 경우에도 인터넷에서 모델을 내려받지 않는다. 로드 직전 `HF_HUB_OFFLINE=1`·`TRANSFORMERS_OFFLINE=1`을 강제 설정하고, **설정된 경로가 실존하는 디렉토리일 때만** 로드한다. 경로가 비었거나 파일이거나 없으면 임베딩만 조용히 꺼진다.
- **회귀 0**: 임베딩은 감사 레코드·noise_ctx **주석 전용**이다(D-035 경계). 모델이 없어도, 오탐이 나도 통보 판정(SUPPRESS/PAGE)은 절대 변하지 않는다.
- **safetensors만 반입**: pickle 형식(`.bin`)은 임의 코드 실행 위험이 있어 반입하지 않는다. e5-small은 원본 리포지토리에 `.bin`이 없어(safetensors만) 보안상 깨끗하다.

### 1.3 모델 선택

| 모델 | 상태 | 차원 | 용량 | 유사도 임계 | 특징 |
|------|------|------|------|------------|------|
| **`intfloat/multilingual-e5-small`** | ✅ **확정** (2026-07-23) | 384 | 466MB | **0.87** | 경량. 한/영 근접중복 완전 분리(근접 min 0.893 > 이질 max 0.852). 분리 마진 좁음(+0.041) |
| `BAAI/bge-m3` | 대안 | 1024 | ~2.3GB | ~0.65 (재튜닝 필요) | 분리 마진 3.5배 넓음(+0.145). e5-small 오탐이 실증되면 교체 후보 |
| `all-MiniLM-L6-v2` 등 영어 전용 | ❌ 사용 금지 | — | — | — | 한국어 판별력 부족 실측됨 — **반드시 다국어 모델** 사용 |

---

## 2. 사전 요건

- Python 3.12 이상 (프로젝트 기준 3.12.11)
- 프로젝트 venv 활성화 상태
- (다운로드 단계만) 인터넷 접근 가능한 PC + `huggingface_hub` CLI

> **주의 — CLI 명령 변경**: 구 명령 `huggingface-cli`는 **폐기되어 동작하지 않는다**. 반드시 신 명령 **`hf`** 를 사용한다 (`pip install -U huggingface_hub` 로 설치, v1.24.0 기준 실측).

---

## 3. 패키지 설치

### 3.1 온라인 환경 (개발 머신)

```bash
cd /path/to/collectorinfra
pip install ".[semantic]"
```

`pyproject.toml`의 `[project.optional-dependencies].semantic` 그룹이 설치된다. `torch`는 `sentence-transformers`가 자동으로 끌어온다. **필수 dependencies에는 편입되어 있지 않으므로** 이 단계를 건너뛰어도 앱은 정상 기동한다.

### 3.2 폐쇄망 환경 (wheel 반입)

인터넷 가능한 PC에서 wheel을 수집한 뒤 반입한다. **버전은 dev 실측 기준으로 고정**을 권장한다:

| 패키지 | 버전(실측 고정 권장) | 비고 |
|--------|---------------------|------|
| `sentence-transformers` | 5.6.0 | 임베딩 상위 API |
| `torch` | 2.13.0 (**CPU 전용 wheel**) | 최대 용량 — CPU wheel로 반입 용량 절감 |
| `transformers` | 5.14.1 | 모델 로더 |
| `tokenizers` | 0.22.2 | |
| `safetensors` | 0.8.0 | 비pickle 가중치 로더 |
| `numpy` | 2.5.1 | 코사인 유사도 |
| `scipy` / `scikit-learn` | 1.18.0 / 1.9.0 | transformers 전이 의존성 |

```bash
# [온라인 PC] wheel 수집 (CPU torch 인덱스 사용)
pip download "sentence-transformers==5.6.0" "numpy==2.5.1" \
  --dest ./wheels --extra-index-url https://download.pytorch.org/whl/cpu

# [폐쇄망] 반입 후 오프라인 설치
pip install --no-index --find-links ./wheels sentence-transformers numpy
```

> 패키지 wheel 자체도 보안팀 반입 협의 대상이다 — `plan60_embedding_import_security_review.md` §3 참조.

---

## 4. 모델 다운로드 (인터넷 가능한 PC에서)

### 4.1 확정 모델 — multilingual-e5-small

```bash
hf download intfloat/multilingual-e5-small \
  --local-dir ./multilingual-e5-small \
  --exclude "*.bin" "*.h5" "*.ot" "onnx/*"
```

- `--exclude`로 pickle(`.bin`)·비사용 형식을 제외한다 (e5-small은 원본에 `.bin`이 없으나 방어적으로 지정).
- 결과 디렉토리 구성 (합계 약 466MB):

| 파일 | 용량 | 역할 |
|------|------|------|
| `model.safetensors` | 449MB | 가중치 (safetensors — 비pickle) |
| `tokenizer.json` | 16MB | fast 토크나이저 |
| `config.json` 외 설정 5종 | <1MB | 모델·ST 설정 |
| `1_Pooling/`, `2_Normalize/` | <1MB | mean pooling + L2 정규화 모듈 |

> **다운로드가 안 될 때**: 세션에 `HF_HUB_OFFLINE=1` 또는 `TRANSFORMERS_OFFLINE=1` 환경변수가 남아 있으면 네트워크가 정상이어도 다운로드가 차단된다(실제 사례 — dev에서 "hub 차단"으로 오인). `unset HF_HUB_OFFLINE TRANSFORMERS_OFFLINE` 후 재시도한다. 네트워크 자체는 `curl -I https://huggingface.co`로 확인(HTTP 200이면 정상).

### 4.2 대안 모델 — bge-m3 (교체 시에만)

```bash
hf download BAAI/bge-m3 --local-dir ./bge-m3 --exclude "*.bin" "onnx/*"
```

교체 절차는 §9 참조 — **임계 재튜닝이 반드시 동반**되어야 한다.

---

## 5. 무결성 검증 (보안 협의·반입 대조용)

다운로드 직후 해시 목록을 생성하고, 폐쇄망 반입 후 동일 명령으로 재생성해 **두 목록이 일치하는지 대조**한다:

```bash
find multilingual-e5-small -type f -exec shasum -a 256 {} \; | sort -k2 > e5-small.sha256
```

**기준 해시** (2026-07-23 dev 실측, 보안 문서 §2 등재):

```
model.safetensors  SHA256 = 7a77d5da5ee721c7c740e4082447d3026b6521d3eac5edb93edb6aa88f03b7d7
```

- 이 값이 다르면 **반입을 중단**하고 재다운로드·재검증한다.
- 라이선스: 모델 MIT (재배포·사내 사용 허용).
- 반입 파일 중 `.bin`(pickle)이 없는지 최종 확인: `find multilingual-e5-small -name "*.bin"` → 결과가 없어야 정상.

---

## 6. 배치 (저장 위치)

| 환경 | 권장 경로 | 이유 |
|------|-----------|------|
| **운영 (폐쇄망)** | `/opt/models/multilingual-e5-small/` | 보안 문서 §7 표준 경로. 앱 계정 **읽기 전용** 권한 부여 |
| **개발 머신** | `~/models/multilingual-e5-small/` | **repo 바깥** — 대용량 파일이 git에 섞이는 사고 방지 |

```bash
# 운영 배치 예시
sudo mkdir -p /opt/models
sudo cp -r ./multilingual-e5-small /opt/models/
sudo chmod -R a-w,a+rX /opt/models/multilingual-e5-small   # 읽기 전용
```

설정에는 **파일이 아니라 디렉토리 경로**를 지정한다는 점에 유의한다.

---

## 7. 애플리케이션 설정 (`.env`)

`NoiseGateConfig`의 env 접두어는 `NOISE_`다. 다음 3개 키를 설정한다:

```
NOISE_EMBEDDING_MODEL_PATH=/opt/models/multilingual-e5-small
NOISE_SEMANTIC_DEDUP_ANNOTATION_ENABLED=true
NOISE_TOPOLOGY_TEXT_FUSION_ENABLED=true
```

| 키 | 기본값 | 설명 |
|----|--------|------|
| `NOISE_EMBEDDING_MODEL_PATH` | `""` (비활성) | 모델 **디렉토리** 절대경로. 미설정·비디렉토리 → 임베딩 inert |
| `NOISE_SEMANTIC_DEDUP_ANNOTATION_ENABLED` | `false` | L-2 의미적 근접중복 주석 옵트인 |
| `NOISE_TOPOLOGY_TEXT_FUSION_ENABLED` | `false` | L-4 토폴로지+텍스트 유사도 주석 옵트인 |
| `NOISE_EMBEDDING_SIMILARITY_THRESHOLD` | `0.87` | L-2 근접중복 임계 — **e5-small 실측 분포 기준**(이질 max 0.852 < 0.87 < 근접 min 0.893). 모델 교체 시 반드시 재튜닝 |
| `NOISE_EMBEDDING_TIMEOUT_SECONDS` | `2.0` | 임베딩 hot-path 예산(초) |

> **`.env` 작성 규칙 (프로젝트 공통)**: 인라인 주석 금지 — 주석은 반드시 별도 줄에 쓴다. 특히 빈 값 뒤에 주석을 붙이면 값으로 파싱되는 사고가 있었다(`18_known_mistakes.md`).

---

## 8. 동작 확인

### 8.1 즉석 확인 (스니펫)

```bash
python -c "
from src.alarm.infrastructure.embedding_provider import AlarmEmbeddingProvider
p = AlarmEmbeddingProvider(model_path='/opt/models/multilingual-e5-small')
print('한/영 근접중복:', p.similarity('CPU 사용률 90% 초과', 'CPU utilization exceeded 90%'))
print('이질 쌍:', p.similarity('CPU 사용률 90% 초과', '디스크 여유공간 부족'))"
```

- 정상: 근접 쌍 ≥ 0.89, 이질 쌍 ≤ 0.86 수준의 수치 출력 (0.87 임계로 분리).
- 미가용: 경고 로그 1회 후 `None` — 앱 기동·게이트 동작에는 영향 없다.

### 8.2 실모델 옵트인 테스트

CI·폐쇄망에서는 자동 스킵되며, 모델 경로를 env로 지정할 때만 실행된다:

```bash
E5_MODEL_PATH=/opt/models/multilingual-e5-small \
  python -m pytest tests/test_alarm/test_embedding_provider_realmodel.py -v
```

4개 테스트(로컬 로드·근접>임계>이질 분리 고정)가 통과하면 설치 완료다.

---

## 9. 모델 교체 절차 (bge-m3로 전환 시)

운영 실데이터에서 e5-small의 좁은 분리 마진(+0.041)으로 오탐(이질 알람의 근접중복 주석)이 확인되면 bge-m3로 교체할 수 있다:

1. §4.2로 bge-m3 다운로드 → §5 해시 생성 → 보안팀 반입 협의(신규 아티팩트이므로 재협의 필요) → §6 배치 (`/opt/models/bge-m3/`)
2. `.env` 변경: `NOISE_EMBEDDING_MODEL_PATH=/opt/models/bge-m3`
3. **임계 재튜닝 필수**: `NOISE_EMBEDDING_SIMILARITY_THRESHOLD=0.65` 부근에서 시작해 실데이터 분포로 보정 (bge-m3 실측: 근접 min 0.714 / 이질 max 0.569)
4. §8 동작 확인 재수행. 미교정 임계(0.87)로 bge-m3를 쓰면 근접중복을 전부 놓친다(bge-m3 근접 max < 0.87).

---

## 10. 트러블슈팅

| 증상 | 원인 | 조치 |
|------|------|------|
| `huggingface-cli: command not found` 또는 deprecated 경고 | 구 CLI 폐기 | `hf download ...` 사용 (§2) |
| 온라인 PC인데 다운로드 실패 | 세션에 `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE` 잔재 | `unset` 후 재시도, `curl -I https://huggingface.co`로 네트워크 확인 (§4.1) |
| 임베딩 주석이 전혀 안 붙음 | ① 옵트인 플래그 off ② 경로 미설정/오타/파일 지정 ③ 패키지 미설치 | `.env` 3개 키 확인(§7), 경로가 **디렉토리**인지 확인, `pip show sentence-transformers` |
| 로그에 "임베딩 미가용" 경고 1회 | 정상 동작 — 모델/패키지 부재 시 의도된 graceful 강등 | 활성화하려면 §3·§4 수행. 쓰지 않을 거면 무시(게이트 영향 0) |
| 근접중복이 안 잡힘 (주석 0건) | 임계와 모델 불일치 (예: bge-m3에 0.87) | 모델별 임계 확인 (§9) |
| 반입 후 해시 불일치 | 전송 중 손상·변조 | 반입 중단, 재다운로드·재대조 (§5) |
| 실모델 테스트가 skip됨 | `E5_MODEL_PATH` 미설정 — 의도된 동작 | §8.2 명령으로 경로 지정 실행 |

---

## 11. 참조

- `docs/plan60_embedding_import_security_review.md` — 보안팀 반입 협의 문서 (아티팩트 해시·패키지 버전·오프라인 동작 근거·검토 포인트)
- `docs/02_decision.md` **D-114** — B-7 로컬 임베딩 주석 결정 (e5-small 확정·임계 0.87)
- `plans/60-noise-cancellation-benchmark-refinement.md` §15.3(L-2/L-4)·§15.4(D-035 경계 불변식)
- `src/alarm/infrastructure/embedding_provider.py` — 로드·inert·캐시 동작의 단일 출처(코드)
