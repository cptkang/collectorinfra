# Redis 마이그레이션 도구

collectorinfra 프로젝트의 Redis 스키마 캐시 데이터를 다른 Redis 서버로 마이그레이션하는 도구.

## 구성 파일

```
tools/redis_migration/
├── exporter.py              # 현재 Redis → JSON 파일로 내보내기
├── importer.py              # JSON 파일 → 타겟 Redis로 복원
├── migrator.py              # Redis → Redis 직접 마이그레이션 (DUMP/RESTORE)
├── config.py                # 마이그레이션 설정 (dataclass)
├── __main__.py              # 직접 마이그레이션 CLI 진입점
└── migration.yaml.example   # YAML 설정 파일 예시

tools/migdata/
├── redis_export_YYYYMMDD_HHMMSS.json   # Export 데이터 파일
└── manifest_YYYYMMDD_HHMMSS.txt        # 키 목록 매니페스트
```

---

## 현재 Export된 데이터

> Export 일시: **2026-04-01 10:01:51 (KST)**
> 소스: `localhost:6380/0` (Docker: collectorinfra-redis)
> 데이터 파일: `tools/migdata/redis_export_20260401_100151.json` (124 KB)

### Export 데이터 요약

| 구분 | 키 수 | 설명 |
|---|---|---|
| 전체 | **27** | 모든 키 |
| schema 관련 | 23 | 4개 DB(\_default, default, polestar, unknown)의 스키마 캐시 |
| synonyms 관련 | 3 | 글로벌/RESOURCE_TYPE/EAV NAME 유사단어 사전 |
| csv_cache | 1 | CSV 변환 캐시 (TTL ~5일 남음) |

### DB별 스키마 캐시 상세

#### `_default` DB

| 키 | 타입 | 크기 |
|---|---|---|
| `schema:_default:meta` | hash | 6 fields |
| `schema:_default:tables` | hash | 3 fields (3개 테이블) |
| `schema:_default:relationships` | string | 113 bytes |
| `schema:_default:descriptions` | hash | 71 fields (71개 컬럼 설명) |
| `schema:_default:synonyms` | hash | 71 fields (71개 컬럼 유사단어) |
| `schema:_default:fingerprint_checked_at` | string | timestamp |

#### `default` DB

| 키 | 타입 | 크기 |
|---|---|---|
| `schema:default:meta` | hash | 6 fields |
| `schema:default:tables` | hash | 5 fields (5개 테이블) |
| `schema:default:relationships` | string | 228 bytes |

#### `polestar` DB

| 키 | 타입 | 크기 |
|---|---|---|
| `schema:polestar:meta` | hash | 6 fields |
| `schema:polestar:tables` | hash | 2 fields (2개 테이블) |
| `schema:polestar:relationships` | string | 2 bytes |
| `schema:polestar:descriptions` | hash | 71 fields (71개 컬럼 설명) |
| `schema:polestar:synonyms` | hash | 71 fields (71개 컬럼 유사단어) |
| `schema:polestar:structure_meta` | string | 10,603 bytes (구조 분석 결과) |
| `schema:polestar:fingerprint_checked_at` | string | timestamp |

#### `unknown` DB

| 키 | 타입 | 크기 |
|---|---|---|
| `schema:unknown:meta` | hash | 6 fields |
| `schema:unknown:tables` | hash | 2 fields (2개 테이블) |
| `schema:unknown:relationships` | string | 2 bytes |
| `schema:unknown:fingerprint_checked_at` | string | timestamp |
| `schema:unknown:structure_meta:meta` | hash | 6 fields |
| `schema:unknown:structure_meta:relationships` | string | 2 bytes |
| `schema:unknown:structure_meta:fingerprint_checked_at` | string | timestamp |

### 글로벌 유사단어 사전

| 키 | 타입 | 크기 | 설명 |
|---|---|---|---|
| `synonyms:global` | hash | 109 fields | 컬럼명 → 유사단어 매핑 (전역) |
| `synonyms:resource_types` | hash | 19 fields | RESOURCE_TYPE 값 → 유사단어 |
| `synonyms:eav_names` | hash | 27 fields | EAV 속성명 → 유사단어 |

### CSV 캐시

| 키 | 타입 | 크기 | TTL |
|---|---|---|---|
| `csv_cache:5e9d33be...` | string | 753 bytes | ~5일 남음 (원래 TTL 7일) |

---

## 사용 방법

### 방법 1: Export/Import (권장 — 파일 기반)

네트워크가 단절된 환경이나, 백업 후 안전하게 복원할 때 사용한다.

#### Step 1. Export (현재 Redis → JSON 파일)

이미 export된 파일이 있으므로 **새로 export할 필요가 없다면 Step 2로 건너뛴다.**

```bash
# 전체 키 내보내기 (localhost:6380 → tools/migdata/)
python -m tools.redis_migration.exporter \
    --host localhost --port 6380 \
    --output tools/migdata

# 특정 패턴만 내보내기 (예: polestar DB와 글로벌 사전만)
python -m tools.redis_migration.exporter \
    --host localhost --port 6380 \
    --output tools/migdata \
    --patterns "schema:polestar:*" "synonyms:*"

# 상세 로그 출력
python -m tools.redis_migration.exporter \
    --host localhost --port 6380 \
    --output tools/migdata -v
```

생성 파일:
- `redis_export_YYYYMMDD_HHMMSS.json` — 키/값 전체 데이터
- `manifest_YYYYMMDD_HHMMSS.txt` — 키 목록 요약 (타입, TTL, 크기)

#### Step 2. Export 파일을 타겟 서버로 전송

```bash
# SCP로 전송
scp tools/migdata/redis_export_20260401_100151.json user@target-server:/path/to/collectorinfra/tools/migdata/

# 또는 rsync
rsync -avz tools/migdata/ user@target-server:/path/to/collectorinfra/tools/migdata/
```

#### Step 3. Import (JSON 파일 → 타겟 Redis)

```bash
# 1) dry-run으로 먼저 확인 (실제 쓰기 없음)
python -m tools.redis_migration.importer \
    --input tools/migdata/redis_export_20260401_100151.json \
    --host <타겟호스트> --port <타겟포트> \
    --dry-run

# 2) 실제 복원 (타겟에 키가 없는 경우)
python -m tools.redis_migration.importer \
    --input tools/migdata/redis_export_20260401_100151.json \
    --host <타겟호스트> --port <타겟포트>

# 3) 기존 키가 있으면 덮어쓰기
python -m tools.redis_migration.importer \
    --input tools/migdata/redis_export_20260401_100151.json \
    --host <타겟호스트> --port <타겟포트> \
    --overwrite

# 4) 비밀번호가 있는 Redis
python -m tools.redis_migration.importer \
    --input tools/migdata/redis_export_20260401_100151.json \
    --host <타겟호스트> --port <타겟포트> \
    --password "your_password"

# 5) TTL 보존 없이 복원 (csv_cache 등의 TTL을 무시)
python -m tools.redis_migration.importer \
    --input tools/migdata/redis_export_20260401_100151.json \
    --host <타겟호스트> --port <타겟포트> \
    --no-preserve-ttl
```

### 방법 2: 직접 마이그레이션 (Redis → Redis)

소스와 타겟 Redis에 **동시에 네트워크 접근 가능**할 때 사용한다.
DUMP/RESTORE 명령으로 바이너리 수준 복사하며, 마이그레이션 후 자동 검증한다.

```bash
# 1) dry-run으로 대상 키 확인
python -m tools.redis_migration \
    --source-host localhost --source-port 6380 \
    --target-host <타겟호스트> --target-port <타겟포트> \
    --dry-run

# 2) 실제 마이그레이션 (기본: 기존 키 스킵 + 검증)
python -m tools.redis_migration \
    --source-host localhost --source-port 6380 \
    --target-host <타겟호스트> --target-port <타겟포트>

# 3) 기존 키 덮어쓰기
python -m tools.redis_migration \
    --source-host localhost --source-port 6380 \
    --target-host <타겟호스트> --target-port <타겟포트> \
    --overwrite

# 4) 특정 패턴만 마이그레이션 (예: polestar만)
python -m tools.redis_migration \
    --source-host localhost --source-port 6380 \
    --target-host <타겟호스트> --target-port <타겟포트> \
    --patterns "schema:polestar:*" "synonyms:*"

# 5) YAML 설정 파일 사용
cp tools/redis_migration/migration.yaml.example tools/redis_migration/migration.yaml
# migration.yaml에서 source/target 정보 수정 후:
python -m tools.redis_migration \
    --config tools/redis_migration/migration.yaml

# 6) 비밀번호 + SSL
python -m tools.redis_migration \
    --source-host localhost --source-port 6380 \
    --target-host <타겟호스트> --target-port <타겟포트> \
    --target-password "your_password" --target-ssl
```

---

## 마이그레이션 대상 키 구조

| 키 패턴 | 타입 | 설명 |
|---|---|---|
| `schema:{db_id}:meta` | hash | DB별 캐시 메타 (fingerprint, cached_at, table_count 등) |
| `schema:{db_id}:tables` | hash | 테이블 스키마 (table_name → JSON) |
| `schema:{db_id}:relationships` | string | 테이블 간 관계 (JSON array) |
| `schema:{db_id}:descriptions` | hash | 컬럼 설명 (table.column → 한국어 설명) |
| `schema:{db_id}:synonyms` | hash | DB별 유사단어 (table.column → JSON {words, sources}) |
| `schema:{db_id}:fingerprint_checked_at` | string | fingerprint 최종 검증 시각 (Unix timestamp) |
| `schema:{db_id}:structure_meta` | string | 구조 분석 결과 (EAV 패턴, 쿼리 가이드 등 JSON) |
| `synonyms:global` | hash | 글로벌 유사단어 사전 (column_name → {words, description}) |
| `synonyms:resource_types` | hash | RESOURCE_TYPE 값 유사단어 (value → [단어]) |
| `synonyms:eav_names` | hash | EAV NAME 유사단어 (속성명 → [단어]) |
| `csv_cache:{sha256}` | string | CSV 변환 캐시 (TTL 7일, JSON) |

---

## CLI 옵션 요약

### exporter.py

```
python -m tools.redis_migration.exporter [옵션]
```

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--host` | localhost | Redis 호스트 |
| `--port` | 6380 | Redis 포트 |
| `--db` | 0 | DB 번호 |
| `--password` | (빈값) | Redis 비밀번호 |
| `--output` | tools/migdata | 출력 디렉토리 |
| `--patterns` | 전체(\*) | 내보낼 키 패턴 (공백 구분) |
| `-v, --verbose` | off | 상세 로그 |

### importer.py

```
python -m tools.redis_migration.importer --input <파일> [옵션]
```

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--input` | **(필수)** | export된 JSON 파일 경로 |
| `--host` | localhost | 타겟 Redis 호스트 |
| `--port` | 6379 | 타겟 Redis 포트 |
| `--db` | 0 | DB 번호 |
| `--password` | (빈값) | Redis 비밀번호 |
| `--overwrite` | false | 기존 키 덮어쓰기 |
| `--dry-run` | false | 실제 쓰기 없이 대상 키 확인만 |
| `--no-preserve-ttl` | false | TTL 보존하지 않음 |
| `-v, --verbose` | off | 상세 로그 |

### __main__.py (직접 마이그레이션)

```
python -m tools.redis_migration --target-host <호스트> [옵션]
```

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--source-host` | localhost | 소스 Redis 호스트 |
| `--source-port` | 6379 | 소스 Redis 포트 |
| `--source-db` | 0 | 소스 DB 번호 |
| `--source-password` | (빈값) | 소스 비밀번호 |
| `--source-ssl` | false | 소스 SSL |
| `--target-host` | **(필수)** | 타겟 Redis 호스트 |
| `--target-port` | 6379 | 타겟 Redis 포트 |
| `--target-db` | 0 | 타겟 DB 번호 |
| `--target-password` | (빈값) | 타겟 비밀번호 |
| `--target-ssl` | false | 타겟 SSL |
| `--patterns` | schema:\* synonyms:\* csv_cache:\* | 마이그레이션 키 패턴 |
| `--exclude` | (없음) | 제외할 키 패턴 |
| `--dry-run` | false | 실제 쓰기 없이 확인 |
| `--overwrite` | false | 기존 키 덮어쓰기 |
| `--no-verify` | false | 마이그레이션 후 검증 건너뛰기 |
| `--no-preserve-ttl` | false | TTL 보존 안 함 |
| `--batch-size` | 100 | SCAN 배치 크기 |
| `--config` | (없음) | YAML 설정 파일 경로 |
| `-v, --verbose` | off | 상세 로그 |

---

## 주의사항

1. **dry-run 먼저 실행**: 마이그레이션 전 반드시 `--dry-run`으로 대상 키를 확인한다.
2. **overwrite 주의**: `--overwrite`는 타겟의 기존 데이터를 삭제 후 덮어쓴다. 타겟에서 운영자가 수동 등록한 유사단어가 있으면 유실될 수 있다.
3. **csv_cache TTL**: csv_cache 키는 7일 TTL이 설정되어 있다. `--no-preserve-ttl` 옵션을 사용하면 TTL 없이 영구 저장된다.
4. **비밀번호 관리**: Redis 비밀번호는 CLI 인자 대신 `.encenv` 파일이나 YAML 설정 파일에서 관리하는 것을 권장한다.
5. **타겟 Redis 버전**: 소스와 타겟의 Redis 버전이 크게 다르면 DUMP/RESTORE(직접 마이그레이션) 방식이 실패할 수 있다. 이 경우 Export/Import 방식을 사용한다.
6. **글로벌 사전 보존**: 글로벌 유사단어 사전(`synonyms:*`)은 운영자가 수동으로 등록한 데이터를 포함한다. 마이그레이션 시 반드시 포함시킨다.
