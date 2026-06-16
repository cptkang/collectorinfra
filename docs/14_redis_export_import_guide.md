# Redis Export / Import 가이드

collectorinfra 프로젝트의 Redis에 저장된 **스키마 캐시, 동의어 사전, CSV 캐시** 등을 다른 Redis 서버로 이관하거나, 백업·복원하기 위한 가이드.

도구 위치: `tools/redis_migration/`
산출물 위치: `tools/migdata/`

---

## 목차

1. [개요](#1-개요)
2. [대상 데이터](#2-대상-데이터)
3. [Export — Redis → JSON 파일](#3-export--redis--json-파일)
4. [Import — JSON 파일 → Redis](#4-import--json-파일--redis)
5. [직접 마이그레이션 — Redis → Redis](#5-직접-마이그레이션--redis--redis)
6. [운영 시나리오](#6-운영-시나리오)
7. [트러블슈팅](#7-트러블슈팅)

---

## 1. 개요

### 1.1 두 가지 이관 방식

| 방식 | 사용 도구 | 권장 상황 |
|---|---|---|
| **Export → Import** (파일 경유) | `exporter.py` + `importer.py` | 네트워크가 단절된 환경, 백업/복원, Redis 버전 차이가 큰 경우 |
| **직접 마이그레이션** (Redis ↔ Redis) | `__main__.py` (`python -m tools.redis_migration`) | 양쪽 Redis에 동시에 접근 가능하고 버전이 동일한 경우 |

### 1.2 디렉토리 구조

```
tools/
├── redis_migration/
│   ├── exporter.py              # Redis → JSON 파일
│   ├── importer.py              # JSON 파일 → Redis
│   ├── migrator.py              # Redis → Redis 직접 이관 (DUMP/RESTORE)
│   ├── config.py                # 마이그레이션 설정 dataclass
│   ├── __main__.py              # 직접 마이그레이션 CLI 진입점
│   └── migration.yaml.example   # YAML 설정 예시
│
└── migdata/                     # Export 산출물 저장소
    ├── redis_export_YYYYMMDD_HHMMSS.json
    └── manifest_YYYYMMDD_HHMMSS.txt
```

### 1.3 사전 요구사항

- Python 환경에 `redis` 패키지 설치 (`pip install redis` 또는 `requirements.txt` 설치 시 자동 포함)
- 소스 Redis가 기동 중이어야 함
  ```bash
  cd redis && docker-compose up -d
  redis-cli -h localhost -p 6380 ping   # PONG 응답 확인
  ```

---

## 2. 대상 데이터

기본적으로 Redis의 **전체 키**가 대상이며, 패턴 필터로 일부만 선택할 수 있다.

### 2.1 키 패턴별 설명

| 키 패턴 | 타입 | 설명 |
|---|---|---|
| `schema:{db_id}:meta` | hash | DB별 캐시 메타데이터 (fingerprint, cached_at, table_count 등) |
| `schema:{db_id}:tables` | hash | 테이블 스키마 (`table_name` → JSON) |
| `schema:{db_id}:relationships` | string | 테이블 간 관계 (JSON array) |
| `schema:{db_id}:descriptions` | hash | 컬럼 설명 (`table.column` → 한국어 설명) |
| `schema:{db_id}:synonyms` | hash | DB별 컬럼 유사단어 (`table.column` → JSON) |
| `schema:{db_id}:fingerprint_checked_at` | string | fingerprint 최종 검증 시각 (Unix timestamp) |
| `schema:{db_id}:structure_meta` | string | 구조 분석 결과 (EAV 패턴, 쿼리 가이드 등 JSON) |
| `synonyms:global` | hash | **글로벌 유사단어 사전** (column_name → {words, description}) |
| `synonyms:resource_types` | hash | RESOURCE_TYPE 값 유사단어 |
| `synonyms:eav_names` | hash | EAV NAME 속성명 유사단어 |
| `csv_cache:{sha256}` | string | CSV 변환 캐시 (TTL 7일, JSON) |

### 2.2 데이터 보존 사항

- **키 타입**: string, hash, list, set, zset 모두 지원
- **TTL**: 밀리초 단위 그대로 보존 (Import 시 `--no-preserve-ttl`로 끌 수 있음)
- **인코딩**: JSON 직렬화 시 `ensure_ascii=False`로 한국어 그대로 저장

---

## 3. Export — Redis → JSON 파일

### 3.1 기본 사용

```bash
# 전체 키 내보내기 (localhost:6380 → tools/migdata/)
python -m tools.redis_migration.exporter \
    --host localhost --port 6380 \
    --output tools/migdata
```

실행 결과:
- `tools/migdata/redis_export_YYYYMMDD_HHMMSS.json` — 키/값 전체 데이터
- `tools/migdata/manifest_YYYYMMDD_HHMMSS.txt` — 키 목록 요약 (타입·TTL·크기)

### 3.2 패턴 필터링

```bash
# 특정 DB의 스키마 캐시 + 글로벌 유사단어만 내보내기
python -m tools.redis_migration.exporter \
    --host localhost --port 6380 \
    --output tools/migdata \
    --patterns "schema:polestar:*" "synonyms:*"

# 동의어만 내보내기 (백업 용도)
python -m tools.redis_migration.exporter \
    --host localhost --port 6380 \
    --output tools/migdata \
    --patterns "synonyms:*"
```

### 3.3 인증/SSL 옵션

```bash
python -m tools.redis_migration.exporter \
    --host redis.example.com --port 6379 \
    --password "your_password" \
    --ssl \
    --output tools/migdata
```

### 3.4 CLI 옵션 전체

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--host` | `localhost` | Redis 호스트 |
| `--port` | `6380` | Redis 포트 |
| `--db` | `0` | DB 번호 (0–15) |
| `--password` | (빈값) | Redis 비밀번호 |
| `--ssl` | off | SSL/TLS 연결 |
| `--output` | `tools/migdata` | 출력 디렉토리 |
| `--patterns` | `*` (전체) | 내보낼 키 패턴 (공백 구분, 여러 개 가능) |
| `-v, --verbose` | off | 상세 로그 |

### 3.5 산출물 예시

**manifest 파일** (`manifest_20260518_104602.txt`):
```
# Redis Export Manifest
# exported_at: 2026-05-18T10:46:02+0900
# source: localhost:6380/0
# total_keys: 26
# data_file: redis_export_20260518_104602.json

hash     persistent           fields=71     schema:_default:descriptions
hash     persistent           fields=109    synonyms:global
hash     persistent           fields=27     synonyms:eav_names
hash     persistent           fields=19     synonyms:resource_types
string   persistent           len=11097    schema:polestar:structure_meta
...
```

**데이터 파일** (`redis_export_*.json`) 구조:
```json
{
  "metadata": {
    "exported_at": "2026-05-18T10:46:02+0900",
    "source": "localhost:6380/0",
    "total_keys": 26,
    "redis_version": "7.x.x"
  },
  "keys": [
    {
      "key": "synonyms:global",
      "type": "hash",
      "ttl_ms": -1,
      "value": { "field1": "...", "field2": "..." }
    },
    ...
  ]
}
```

---

## 4. Import — JSON 파일 → Redis

### 4.1 권장 절차 (Dry-run → 실제 적용)

```bash
# Step 1) dry-run으로 대상 키 확인 (실제 쓰기 없음)
python -m tools.redis_migration.importer \
    --input tools/migdata/redis_export_20260518_104602.json \
    --host <타겟호스트> --port <타겟포트> \
    --dry-run

# Step 2) 실제 import — 타겟에 동일 키가 없을 때 (기본: skip 정책)
python -m tools.redis_migration.importer \
    --input tools/migdata/redis_export_20260518_104602.json \
    --host <타겟호스트> --port <타겟포트>

# Step 3) 기존 키 덮어쓰기 (운영 데이터 유실 주의)
python -m tools.redis_migration.importer \
    --input tools/migdata/redis_export_20260518_104602.json \
    --host <타겟호스트> --port <타겟포트> \
    --overwrite
```

### 4.2 같은 Redis에 복원 (로컬 백업 복원)

```bash
python -m tools.redis_migration.importer \
    --input tools/migdata/redis_export_20260518_104602.json \
    --host localhost --port 6380 \
    --overwrite
```

### 4.3 TTL 동작 제어

기본은 export 시점의 TTL을 그대로 보존한다. 무시하고 영구 저장하려면:
```bash
python -m tools.redis_migration.importer \
    --input tools/migdata/redis_export_20260518_104602.json \
    --host localhost --port 6380 \
    --no-preserve-ttl
```

> **주의**: `csv_cache:*` 키는 export 시점에 남은 TTL(예: 7일 중 5일)을 보존한다. `--no-preserve-ttl`을 쓰면 만료 없이 저장되므로, 캐시 위생 관리 차원에서 권장하지 않는다.

### 4.4 CLI 옵션 전체

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--input` | **필수** | export된 JSON 파일 경로 |
| `--host` | `localhost` | 타겟 Redis 호스트 |
| `--port` | `6379` | 타겟 Redis 포트 |
| `--db` | `0` | DB 번호 |
| `--password` | (빈값) | Redis 비밀번호 |
| `--ssl` | off | SSL 사용 |
| `--overwrite` | false | 기존 키 삭제 후 덮어쓰기 |
| `--dry-run` | false | 실제 쓰기 없이 대상 확인만 |
| `--no-preserve-ttl` | false | TTL 보존 안 함 |
| `-v, --verbose` | off | 상세 로그 |

### 4.5 Import 결과 통계

성공 시 표준 출력에 통계가 표시된다:
```
=== Import 결과 ===
  총 키 수  : 26
  복원 성공 : 26
  건너뜀    : 0
  실패      : 0
  소요 시간 : 0.7초
```

- **복원 성공 (ok)**: 정상 저장됨
- **건너뜀 (skipped)**: `--overwrite` 미지정 상태에서 타겟에 이미 키가 존재
- **실패 (failed)**: 타입 불일치 또는 Redis 오류 — 로그 확인 필요

---

## 5. 직접 마이그레이션 — Redis → Redis

소스/타겟에 동시 접근 가능하면 파일 경유 없이 직접 이관할 수 있다. 내부적으로 **DUMP/RESTORE** 명령을 사용해 바이너리 수준으로 복사하고, 이관 후 자동 검증을 수행한다.

### 5.1 기본 사용

```bash
# 1) dry-run으로 대상 키 확인
python -m tools.redis_migration \
    --source-host localhost --source-port 6380 \
    --target-host <타겟호스트> --target-port <타겟포트> \
    --dry-run

# 2) 실제 이관 (기본: 기존 키 skip + 검증)
python -m tools.redis_migration \
    --source-host localhost --source-port 6380 \
    --target-host <타겟호스트> --target-port <타겟포트>

# 3) 기존 키 덮어쓰기
python -m tools.redis_migration \
    --source-host localhost --source-port 6380 \
    --target-host <타겟호스트> --target-port <타겟포트> \
    --overwrite

# 4) 특정 패턴만 이관 (기본 패턴 사용 시 schema:* synonyms:* csv_cache:*)
python -m tools.redis_migration \
    --source-host localhost --source-port 6380 \
    --target-host <타겟호스트> --target-port <타겟포트> \
    --patterns "schema:polestar:*" "synonyms:*"
```

### 5.2 YAML 설정 파일 사용

```bash
cp tools/redis_migration/migration.yaml.example tools/redis_migration/migration.yaml
# 파일 내 source/target 정보 수정 후:
python -m tools.redis_migration --config tools/redis_migration/migration.yaml
```

### 5.3 주요 CLI 옵션

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--source-host` | `localhost` | 소스 Redis 호스트 |
| `--source-port` | `6379` | 소스 Redis 포트 |
| `--source-db` | `0` | 소스 DB 번호 |
| `--source-password` | (빈값) | 소스 비밀번호 |
| `--source-ssl` | off | 소스 SSL |
| `--target-host` | **필수** | 타겟 Redis 호스트 |
| `--target-port` | `6379` | 타겟 Redis 포트 |
| `--target-db` | `0` | 타겟 DB 번호 |
| `--target-password` | (빈값) | 타겟 비밀번호 |
| `--target-ssl` | off | 타겟 SSL |
| `--patterns` | `schema:* synonyms:* csv_cache:*` | 이관 대상 키 패턴 |
| `--exclude` | (없음) | 제외할 키 패턴 |
| `--dry-run` | false | 실제 쓰기 없이 확인 |
| `--overwrite` | false | 기존 키 덮어쓰기 |
| `--no-verify` | false | 이관 후 검증 건너뛰기 |
| `--no-preserve-ttl` | false | TTL 보존 안 함 |
| `--batch-size` | `100` | SCAN 배치 크기 |
| `--config` | (없음) | YAML 설정 파일 경로 |

### 5.4 Export/Import 방식과의 차이

| 항목 | Export/Import | 직접 마이그레이션 |
|---|---|---|
| 직렬화 방식 | JSON (사람이 읽을 수 있음) | DUMP 바이너리 |
| Redis 버전 호환 | 큰 차이 OK (타입별 재구성) | 동일 또는 인접 버전 권장 |
| 네트워크 요구 | 단계별, 양쪽 동시 접근 불필요 | 양쪽 동시 접근 필요 |
| 백업 보관 | JSON 파일로 영구 보관 가능 | 보관 파일 없음 |
| 자동 검증 | 없음 (수동) | 있음 (`--no-verify`로 끌 수 있음) |
| 권장 상황 | 백업, 환경 분리, 다른 버전 이관 | 동일 환경 내 신속 이관 |

---

## 6. 운영 시나리오

### 6.1 정기 백업 (글로벌 동의어 보존)

운영자가 수동으로 등록한 `synonyms:*`은 손실 시 복구가 어려우므로 주기적으로 백업한다.

```bash
# 매일 실행 (예: crontab)
python -m tools.redis_migration.exporter \
    --host localhost --port 6380 \
    --output /backup/redis/$(date +%Y%m%d) \
    --patterns "synonyms:*"
```

### 6.2 개발 → 스테이징 → 운영 환경 동기화

개발 환경에서 만든 스키마 캐시와 동의어를 스테이징/운영에 반영:

```bash
# 1) 개발에서 export
python -m tools.redis_migration.exporter \
    --host dev-redis --port 6380 \
    --output ./release \
    --patterns "schema:*" "synonyms:*"

# 2) 산출물 검토 (manifest 파일 확인)
cat ./release/manifest_*.txt

# 3) 스테이징에 dry-run
python -m tools.redis_migration.importer \
    --input ./release/redis_export_*.json \
    --host staging-redis --port 6379 \
    --dry-run

# 4) 스테이징 적용
python -m tools.redis_migration.importer \
    --input ./release/redis_export_*.json \
    --host staging-redis --port 6379 \
    --overwrite

# 5) 운영 적용 (동일 절차)
```

### 6.3 캐시 손상 시 복구

스키마 캐시가 손상되거나 잘못된 데이터로 덮어써졌을 때:

```bash
# 가장 최근의 정상 백업 파일로 덮어쓰기
python -m tools.redis_migration.importer \
    --input /backup/redis/20260517/redis_export_*.json \
    --host localhost --port 6380 \
    --overwrite
```

### 6.4 운영 데이터 보호 — 부분 적용

운영의 글로벌 동의어는 보존하고 스키마 캐시만 갱신하려는 경우:

```bash
# Step 1) 운영의 글로벌 동의어 백업
python -m tools.redis_migration.exporter \
    --host prod-redis --port 6379 \
    --output ./prod-backup \
    --patterns "synonyms:*"

# Step 2) 스키마 캐시만 export/import (synonyms 제외)
python -m tools.redis_migration.exporter \
    --host dev-redis --port 6380 \
    --output ./release \
    --patterns "schema:*"

python -m tools.redis_migration.importer \
    --input ./release/redis_export_*.json \
    --host prod-redis --port 6379 \
    --overwrite
```

---

## 7. 트러블슈팅

### 7.1 Redis 컨테이너가 중지돼 있어 연결 실패

```
Could not connect to Redis at localhost:6380: Connection refused
```

→ `redis/docker-compose.yml`로 컨테이너를 기동한다:
```bash
cd redis && docker-compose up -d
redis-cli -h localhost -p 6380 ping   # PONG 확인
```

### 7.2 비밀번호 인증 오류

```
redis.exceptions.AuthenticationError: ...
```

→ `--password` 인자 또는 `.encenv`의 `REDIS_PASSWORD`를 확인. CLI 노출이 부담스럽다면 YAML 설정 파일을 사용한다.

### 7.3 Import 시 기존 키가 모두 skip됨

```
=== Import 결과 ===
  복원 성공 : 0
  건너뜀    : 26
```

→ 타겟에 동일 키가 이미 존재함. 의도라면 OK이지만, 덮어써야 한다면 `--overwrite` 추가.

### 7.4 직접 마이그레이션 시 RESTORE 실패

```
ResponseError: DUMP payload version or checksum are wrong
```

→ 소스와 타겟 Redis 버전 차이가 크기 때문. **Export/Import 방식**으로 전환한다 (타입별 재구성이라 버전 무관).

### 7.5 한국어 깨짐

산출물 JSON은 `ensure_ascii=False`로 저장되므로 깨지지 않는다. 만약 깨져 보인다면 에디터의 인코딩을 UTF-8로 설정하여 열어볼 것.

### 7.6 csv_cache TTL이 만료된 채로 import됨

Export 후 시간이 흘러 csv_cache의 잔여 TTL이 0 이하가 된 경우, import 시 즉시 만료 처리될 수 있다. **csv_cache는 일회성 캐시이므로 백업/복원 대상에서 제외**해도 무방하다:
```bash
python -m tools.redis_migration.exporter \
    --patterns "schema:*" "synonyms:*"   # csv_cache 제외
```

---

## 부록 A. 빠른 명령어 모음

```bash
# === Export ===
# 전체
python -m tools.redis_migration.exporter --host localhost --port 6380 --output tools/migdata
# 동의어만
python -m tools.redis_migration.exporter --host localhost --port 6380 --output tools/migdata --patterns "synonyms:*"
# 특정 DB만
python -m tools.redis_migration.exporter --host localhost --port 6380 --output tools/migdata --patterns "schema:polestar:*"

# === Import ===
# dry-run
python -m tools.redis_migration.importer --input tools/migdata/redis_export_*.json --host localhost --port 6380 --dry-run
# 새 키만 추가
python -m tools.redis_migration.importer --input tools/migdata/redis_export_*.json --host localhost --port 6380
# 덮어쓰기
python -m tools.redis_migration.importer --input tools/migdata/redis_export_*.json --host localhost --port 6380 --overwrite

# === 직접 마이그레이션 ===
python -m tools.redis_migration --source-host localhost --source-port 6380 --target-host target --target-port 6379
```

## 부록 B. 참고 문서

- 도구 상세: `tools/redis_migration/README.md`
- 환경변수: `docs/03_setup_guide.md`의 Redis 설정 절
- 시스템 구조: `docs/05_system_architecture.md`
