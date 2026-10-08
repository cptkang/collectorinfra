"""지식 자산 근거 묶음(`--evidence`) · 원천 파일 검증(`--validate-knowledge`).

plans/143 W1·W2 · D-316.

**근거 묶음** — 반출 run(`schema_catalog.yaml`·`trace.jsonl` · 있으면 `code_samples.yaml`)과 저장소
현행 정의(`config/db_profiles/itam.yaml`)·시드 업무 영역·폐쇄망 시나리오·정답 SQL·원천 지식
파일(철회 항목)·직전 검증 결과(`knowledge/validation.yaml`)에서 `src.domain.knowledge_evidence`로
자산별 입력을 만들고, 누출 관문(5규칙 · `redact.LeakGate`)을 통과할 때만
`<run>/knowledge_evidence/`에 쓴다(실패면 `leak_check.json`만 · 종료 1). 같은 입력이면 바이트가
같다. LLM·DB 0. 반출 run에 `substitutions.yaml`(3회차부터 · 형식 보존 가짜 값 · plans/145 §2.6)이
있으면 묶음이 가짜 값을 실어 나르는 것은 막지 않고(외부망 작업 자료) 색인에 가짜 값 수와 「자산에
쓰지 않는다」 고지를 한 줄 싣는다.

**검증** — 원천 파일(`src.domain.knowledge_assets` 계약)을 결정적으로 검사한다. 정적 검사는 도메인
모듈, SQL은 여기서 더한다(K2·K4·K8 모두 MariaDB 실행 주석 `/*! … */`는 실행 전 거절): K2
`SQLGuard` → 「DB 구조」 탭과 같은 검사(SELECT 한 문장·행 제한 · 부수효과 함수 · `validate_sql`
실존·한글 식별자) → `tables` 칸 일치 → 모의 DB 실행(바깥 행 제한) ·
K4 D-294 ③ 섹션 검증(`_validate_section`) → 섹션 SQL 모의 DB 실행. 치환 코드값(근거 run의
`code_samples.yaml`)과 형식 보존 치환값(근거 run의 `substitutions.yaml` — 차단 전용 · 한 글자
값 제외 `build_assets.blocked_fakes`)이 리터럴로 나오는 항목은 모든 자산에서 거절한다. 근거 run
리터럴(실행 SQL의 문자열·숫자 리터럴 · 반출 카탈로그 주석과 반출 테이블 정의 글의 코드 열거 값 —
`load_evidence_literals`)이 토큰으로 나오는 항목도 철회 항목까지 거절한다(`evidence_literal` · 원천
파일은 커밋된다 — D-301 · D-308). K6은 정의 `kind` 파생 규칙을
같은 글 검사로 본다. K8(`query_templates.yaml` · 머리 `version: 1` + `templates:` 목록)은 공통 칸 →
`check_templates`(계약 · 테이블·code 컬럼 실존) → 조회 대상 → 슬롯 전 조합(`sample_bindings`)을
바인딩해 `SQLGuard` → 부수효과 함수 → `validate_sql` → 모의 DB 실행(바깥 행 제한). code 슬롯
대표값은 근거 run `code_samples.yaml`의 치환값(`substitution: ok` — 모의 DB 행과 같은 값)이고,
없으면 그 조합을 `code_unverified`(「코드값 없음 — 실행 보류」)로 표시해 `db_unverified`처럼
친다. 실행 오류 문구의 코드값은 가린다.

DB 실행기는 P2(`build_assets._run_p2`)와 같은 방식으로 주입한다 — `itam` 소스 클라이언트(모의 DB로
돌려 둔 MCP 소스)에 연결해 상태·대표 테이블을 확인하고, 못 쓰면 실행 대상 항목을 `db_unverified`로
표시해 실패로 친다(침묵 통과 없음). `--static-only`면 정적 검사만 하고 `db_unverified`를 실패로 치지
않되 출력 첫 줄에 「모의 DB 실행 미실시」를 적는다.

빌더(`build_assets.knowledge_overlay` · W4)는 `avalidate_dir` 결과를 `overlay_from_result`로 받아
통과한 active 항목만 옮긴다.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol

import yaml

from src.domain import knowledge_assets as ka
from src.domain import knowledge_evidence as ke
from src.domain import query_templates as qt
from src.domain.schema_snapshot import bare_name, build_snapshot
from src.schema_cache.structure_store import _dump_yaml_exact

from . import CLOSED_POLICY_PATH, CLOSED_SCENARIOS_PATH, DB_ID, REPO_ROOT, RESULTS_ROOT
from . import build_assets as ba
from . import catalog as cat
from . import judge as jd
from . import redact as rd

KNOWLEDGE_DIR_REL = Path("testdata/itam_bench/closed/knowledge")
EVIDENCE_DIR = "knowledge_evidence"
TRACE_FILE = "trace.jsonl"
#: 직전 검증 결과의 관례 경로 — 근거 묶음이 읽는다
#: (`--validate-knowledge --out <knowledge>/validation.yaml`)
VALIDATION_FILE = "validation.yaml"
ENGINE = "mariadb"
#: 실행 대상 항목을 실행하지 못했을 때의 문제 코드
DB_UNVERIFIED = "db_unverified"
DB_FAILED = "db_failed"
SQL_GUARD = "sql_guard"
SQL_INVALID = "sql_invalid"
TABLES_MISMATCH = "tables_mismatch"
SUBSTITUTED = "substituted_literal"
EVIDENCE_LITERAL = ka.EVIDENCE_LITERAL
SECTION_INVALID = "section_invalid"
TEMPLATE_CONTRACT = "template_contract"
TEMPLATE_BIND = "template_bind"
#: code 슬롯 대표값(치환 코드값)이 없어 그 조합을 실행하지 못했다 — `db_unverified`처럼 친다
CODE_UNVERIFIED = "code_unverified"
#: 실행 오류 문구에서 바인딩한 코드값을 가리는 표지
_CODE_MASK = "<코드값>"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_INPUT = 2


class SqlExecutor(Protocol):
    """DB 실행기 — 읽기 전용 소스 클라이언트(`execute_sql` 결과의 `rows`)."""

    async def execute_sql(self, sql: str) -> Any: ...


class _NoExecute:
    """정적 검사용 — 실행하지 않고 빈 결과를 돌려준다(검사 함수의 실행 단계를 건너뛴다)."""

    async def execute_sql(self, sql: str) -> Any:
        return SimpleNamespace(rows=[])


# ──────────────────────────────────────────────
# 입력
# ──────────────────────────────────────────────


def _read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_profile(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """현행 ITAM 프로필(`allowed_tables`·`table_definitions`).

    Raises:
        ba.BuildError: 프로필 없음(`EXIT_INPUT`)
    """
    path = Path(repo_root) / ba.PROFILE_REL
    if not path.is_file():
        raise ba.BuildError(f"프로필이 없습니다: {ba.PROFILE_REL}", EXIT_INPUT)
    doc = _read_yaml(path)
    return doc if isinstance(doc, dict) else {}


def load_schema(path: Path) -> dict[str, Any]:
    """대조 카탈로그 → 캐시 모양 스키마(``{"tables": {t: {"columns": [...]}}}``).

    `itam_schema.json`(앱 스키마 캐시) · 반출 `schema_catalog.yaml` · 반출 run 디렉터리를 받는다.

    Raises:
        ba.BuildError: 없음·형식 오류(`EXIT_INPUT`)
    """
    path = Path(path)
    if path.is_dir():
        path = path / ba.CATALOG_FILE
    if not path.is_file():
        raise ba.BuildError(f"대조 카탈로그가 없습니다: {path}", EXIT_INPUT)
    text = path.read_text(encoding="utf-8")
    data = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    if isinstance(data, Mapping) and isinstance(data.get("schema"), Mapping):
        data = data["schema"]
    elif isinstance(data, Mapping) and "approved_profile" in data:  # 반출 카탈로그
        data = ba.schema_cache_dict(data)
    if not isinstance(data, Mapping) or not isinstance(data.get("tables"), Mapping):
        raise ba.BuildError(f"대조 카탈로그에 `tables` 매핑이 없습니다: {path}", EXIT_INPUT)
    return dict(data)


def schema_columns(schema: Mapping[str, Any]) -> dict[str, list[str]]:
    """``{테이블: [컬럼…]}``."""
    return {
        str(t): [str(c.get("name")) for c in (info.get("columns") or []) if isinstance(c, Mapping)]
        for t, info in (schema.get("tables") or {}).items()
    }


def schema_types(schema: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """``{테이블: {컬럼: 타입}}``."""
    return {
        str(t): {
            str(c.get("name")): str(c.get("type") or "")
            for c in (info.get("columns") or []) if isinstance(c, Mapping)
        }
        for t, info in (schema.get("tables") or {}).items()
    }


def knowledge_documents(knowledge_dir: Path) -> dict[str, Any]:
    """원천 파일 문서(있는 것만 · 읽지 못하면 뺀다)."""
    out: dict[str, Any] = {}
    for name in ka.KNOWLEDGE_FILES:
        path = Path(knowledge_dir) / name
        if path.is_file():
            try:
                out[name] = _read_yaml(path)
            except yaml.YAMLError:
                continue
    return out


def evidence_runs(knowledge_dir: Path) -> set[str]:
    """원천 항목이 근거로 든 run ID 집합."""
    runs: set[str] = set()
    for doc in knowledge_documents(knowledge_dir).values():
        if not isinstance(doc, Mapping):
            continue
        listed = doc.get("items") or doc.get("templates") or []
        for item in listed if isinstance(listed, list) else []:
            if isinstance(item, Mapping) and isinstance(item.get("evidence"), str):
                runs.add(item["evidence"].strip())
    runs.discard("")
    return runs


def load_code_values(
    runs: Iterable[str], results_root: Path = RESULTS_ROOT
) -> tuple[set[str], list[str]]:
    """근거 run들의 치환 코드값 대조 집합(`build_assets.blocked_values`).

    Returns:
        ``(집합, 파일이 있던 run)``
    """
    values: set[str] = set()
    found: list[str] = []
    for run_id in sorted(runs):
        run_dir = Path(results_root) / run_id
        if not (run_dir / ba.CODE_SAMPLES_FILE).is_file():
            continue
        catalog, samples = ba.load_export(run_dir)
        values |= ba.blocked_values(samples, catalog, ba.catalog_texts(catalog))[0]
        found.append(run_id)
    return values, found


def load_substitution_values(
    runs: Iterable[str], results_root: Path = RESULTS_ROOT
) -> tuple[set[str], list[str], dict[str, int]]:
    """근거 run들의 형식 보존 치환값 대조 집합(`build_assets.export_fakes` 합집합) — 차단 전용(K8
    code 슬롯 대표값으로 쓰지 않는다 · 대표값은 `load_code_samples`).

    Returns:
        ``(집합, 파일이 있던 run, 요약 합 {"values", "short_skipped", "identifier_skipped"})``

    Raises:
        ba.BuildError: `substitutions.yaml` 형식 오류 · 그 run의 반출 카탈로그 없음(닫힌 쪽)
    """
    values: set[str] = set()
    found: list[str] = []
    counts = {"values": 0, "short_skipped": 0, "identifier_skipped": 0}
    for run_id in sorted(runs):
        run_dir = Path(results_root) / run_id
        if not (run_dir / rd.SUBSTITUTIONS_FILE).is_file():
            continue
        catalog, _ = ba.load_export(run_dir)
        blocked, summary = ba.export_fakes(run_dir, catalog)
        values |= blocked
        for key in counts:
            counts[key] += int((summary or {}).get(key) or 0)
        found.append(run_id)
    return values, found, counts


def code_samples_map(samples: Mapping[str, Any] | None) -> dict[str, list[str]]:
    """`code_samples.yaml` → ``{"table.column"(소문자): [치환값…]}`` — `substitution: ok`·값 있는
    것만(모의 DB 행 생성기 `load_code_samples`와 같은 규칙). K8 code 슬롯 대표값으로 쓴다."""
    out: dict[str, list[str]] = {}
    columns = (samples or {}).get("columns") or {}
    for key, node in columns.items() if isinstance(columns, Mapping) else ():
        if not isinstance(node, Mapping) or node.get("substitution") != "ok":
            continue
        values = [str(v) for v in node.get("values") or [] if str(v)]
        if "." in str(key) and values:
            merged = out.setdefault(str(key).lower(), [])
            merged += [v for v in values if v not in merged]
    return out


def load_code_samples(
    runs: Iterable[str], results_root: Path = RESULTS_ROOT
) -> dict[str, list[str]]:
    """근거 run들의 K8 code 슬롯 대표값(`code_samples_map` 합집합 · run 이름 순)."""
    out: dict[str, list[str]] = {}
    for run_id in sorted(runs):
        path = Path(results_root) / run_id / ba.CODE_SAMPLES_FILE
        if not path.is_file():
            continue
        loaded = _read_yaml(path)
        found = code_samples_map(loaded if isinstance(loaded, Mapping) else None)
        for key, values in found.items():
            merged = out.setdefault(key, [])
            merged += [v for v in values if v not in merged]
    return out


#: 반출 테이블 정의에서 코드 열거를 읽지 않는 칸(고정 열거·스키마 식별자)
_DEFINITION_FIXED_FIELDS = frozenset({"kind", "origin", "key_columns"})


def _string_values(node: Any) -> Iterable[str]:
    """중첩 구조의 문자열 값(키 제외)."""
    if isinstance(node, Mapping):
        for value in node.values():
            yield from _string_values(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _string_values(value)
    elif isinstance(node, str):
        yield node


def run_literals(run_dir: Path) -> set[str]:
    """반출 run 하나의 근거 리터럴 대조 집합(`ka.evidence_literal_values` 적용 뒤).

    - 실행 SQL(`trace.jsonl` 턴의 `executed_sqls` — 근거 묶음 `turns.yaml`과 같은 원천)의
      문자열·숫자 리터럴(`ka.sql_literals`). 가림 표지(`<가림…>`)를 품은 조각과 그 턴 질문에
      그대로 있는 조각(커밋된 시나리오 글 — 가림기 `_prompt_word`와 같은 근거)은 뺀다.
    - 반출 카탈로그 테이블·컬럼 주석(`meaning`)과 반출 테이블 정의 글(`approved_profile.
      table_definitions`의 `kind`·`origin`·`key_columns` 밖 칸)의 코드 열거 값(`parse_comment_enum`
      키 · 라벨은 싣지 않는다 — 정의 글은 원 코드값이 있어도 반출된다 · D-311 부기).
    - 반출 카탈로그의 테이블·컬럼 이름과 같은 값은 뺀다.

    3회차 반출부터 실행 SQL 리터럴은 가림 표지가 아니라 형식 보존 가짜 값(plans/145)이라 근거
    리터럴로 잡힌다 — 그 값이 든 자산 항목이 거절되는 것은 의도된 동작이다(가짜 값은 자산에
    쓰지 않는다).
    """
    from src.domain.schema_inference import parse_comment_enum

    run_dir = Path(run_dir)
    values: list[str] = []
    trace_path = run_dir / TRACE_FILE
    if trace_path.is_file():
        for line in trace_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            prompt = str(record.get("prompt") or "")
            for executed in record.get("executed_sqls") or []:
                if not isinstance(executed, Mapping):
                    continue
                values += [
                    v for v in ka.sql_literals(str(executed.get("sql") or ""))
                    if rd.MASK.rstrip(">") not in v and v not in prompt
                ]
    names: set[str] = set()
    if (run_dir / ba.CATALOG_FILE).is_file():
        catalog, _ = ba.load_export(run_dir)
        texts: list[str] = []
        for table, info in catalog["tables"].items():
            names.add(str(table))
            if not isinstance(info, Mapping):
                continue
            texts.append(str(info.get("meaning") or ""))
            for column in info.get("columns") or []:
                if isinstance(column, Mapping):
                    names.add(str(column.get("name")))
                    texts.append(str(column.get("meaning") or ""))
        definitions = (catalog.get("approved_profile") or {}).get("table_definitions")
        for entry in definitions.values() if isinstance(definitions, Mapping) else ():
            if isinstance(entry, Mapping):
                texts += [
                    text for key, value in entry.items()
                    if key not in _DEFINITION_FIXED_FIELDS for text in _string_values(value)
                ]
        values += [code for text in texts for code in parse_comment_enum(text)]
    return ka.evidence_literal_values(values, exempt=names)


def load_evidence_literals(
    runs: Iterable[str], results_root: Path = RESULTS_ROOT
) -> tuple[set[str], list[str]]:
    """근거 run들의 근거 리터럴 대조 집합(`run_literals` 합집합).

    Returns:
        ``(집합, 실행 SQL 기록·반출 카탈로그 중 하나라도 있던 run)``
    """
    values: set[str] = set()
    found: list[str] = []
    for run_id in sorted(runs):
        run_dir = Path(results_root) / run_id
        if not ((run_dir / TRACE_FILE).is_file() or (run_dir / ba.CATALOG_FILE).is_file()):
            continue
        values |= run_literals(run_dir)
        found.append(run_id)
    return values, found


# ──────────────────────────────────────────────
# 근거 묶음 (W1)
# ──────────────────────────────────────────────


def _oracle_sqls(scenarios: Mapping[str, Any] | None) -> dict[str, str]:
    """시나리오 오라클 ID → 정답 SQL 글(파일이 있는 것만)."""
    from scripts.scenario.oracle import oracle_sql_path

    out: dict[str, str] = {}
    for raw in (scenarios or {}).get("scenarios") or []:
        for turn in (raw.get("turns") or []) if isinstance(raw, Mapping) else []:
            if not isinstance(turn, Mapping):
                continue
            oracle = (turn.get("expect") or {}).get("oracle")
            if isinstance(oracle, Mapping) and oracle.get("id"):
                path = oracle_sql_path(str(oracle["id"]), ENGINE)
                if path is not None and path.is_file():
                    out[str(oracle["id"])] = path.read_text(encoding="utf-8")
    return out


def build_evidence_files(
    run_dir: Path,
    *,
    repo_root: Path = REPO_ROOT,
    scenarios_path: Path = CLOSED_SCENARIOS_PATH,
    knowledge_dir: Path | None = None,
) -> dict[str, str]:
    """근거 묶음 텍스트 ``{파일 이름: YAML}``(쓰기 없음).

    Raises:
        ba.BuildError: 반출 카탈로그·프로필 없음(`EXIT_INPUT`)
    """
    run_dir = Path(run_dir)
    repo_root = Path(repo_root)
    catalog, samples = ba.load_export(run_dir)
    fakes = ba.load_substitutions(run_dir)
    trace_path = run_dir / TRACE_FILE
    records = (
        [
            json.loads(line)
            for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        if trace_path.is_file() else []
    )
    profile = load_profile(repo_root)
    seed_path = repo_root / ba.SEED_DEFINITIONS_REL
    seed = _read_yaml(seed_path) if seed_path.is_file() else {}
    scenarios = _read_yaml(Path(scenarios_path)) if Path(scenarios_path).is_file() else None
    knowledge_dir = Path(knowledge_dir) if knowledge_dir else repo_root / KNOWLEDGE_DIR_REL
    validation_path = knowledge_dir / VALIDATION_FILE
    validation = _read_yaml(validation_path) if validation_path.is_file() else None
    docs = ke.build_evidence(
        run_id=run_dir.name,
        catalog=catalog,
        records=records,
        definitions=profile.get("table_definitions") or {},
        allowed=[str(t) for t in profile.get("allowed_tables") or []],
        groups=(seed or {}).get("groups") if isinstance(seed, Mapping) else None,
        scenarios=scenarios,
        oracle_sqls=_oracle_sqls(scenarios),
        knowledge=knowledge_documents(knowledge_dir),
        validation=validation if isinstance(validation, Mapping) else None,
        fix_hints=jd.TAXONOMY,
        inputs={
            ba.CATALOG_FILE: True,
            TRACE_FILE: trace_path.is_file(),
            "report.md": (run_dir / "report.md").is_file(),
            ba.CODE_SAMPLES_FILE: samples is not None,
            "scenarios": scenarios is not None,
            "previous_validation": validation is not None,
        },
    )
    if fakes is not None:  # 1·2회차(파일 없음)는 색인 바이트 그대로
        docs[ke.INDEX_FILE]["substitutions"] = (
            f"가짜 값 {len(set(fakes) - {''})}건({rd.SUBSTITUTIONS_FILE}) — 형식 보존 치환값 · "
            "원값 아님 · 자산에 쓰지 않는다"
        )
    return {name: _dump_yaml_exact(doc) for name, doc in docs.items()}


def run_evidence(
    run_dir: Path,
    *,
    policy_path: Path = CLOSED_POLICY_PATH,
    scenarios_path: Path = CLOSED_SCENARIOS_PATH,
    user_values: Mapping[str, str | None] | None = None,
    repo_root: Path = REPO_ROOT,
    knowledge_dir: Path | None = None,
) -> int:
    """근거 묶음을 만들고 누출 관문을 통과하면 `<run>/knowledge_evidence/`에 쓴다 → 종료 코드.

    이전 근거 파일(`*.yaml`)은 먼저 지운다 — 관문 실패 때 옛 묶음이 남아 새것처럼 읽히지 않게.
    """
    try:
        files = build_evidence_files(
            run_dir, repo_root=repo_root, scenarios_path=scenarios_path, knowledge_dir=knowledge_dir
        )
        policy = cat.load_policy(Path(policy_path))
    except (ba.BuildError, cat.CatalogError, FileNotFoundError) as e:
        print(f"[evidence] 중단: {e}")
        return getattr(e, "exit_code", EXIT_INPUT)
    gate = rd.LeakGate(
        policy=policy, vault=rd.PiiVault.from_policy(policy), user_values=dict(user_values or {})
    )
    out_dir = Path(run_dir) / EVIDENCE_DIR
    if out_dir.is_dir():
        for stale in out_dir.glob("*.yaml"):
            stale.unlink()
    ok, violations = rd.write_gated(out_dir, files, gate)
    if not ok:
        print(f"[evidence] 누출 관문 실패 {len(violations)}건 — 근거 묶음을 쓰지 않았다(위치만):")
        for v in violations[:20]:
            print(f"  - {v['file']} · {v['field']} · {v['rule']}")
        return EXIT_FAILED
    print(f"[evidence] 반출 run {Path(run_dir).name} → {out_dir} (누출 관문 통과)")
    for name in sorted(files):
        print(f"  {name}  {len(files[name].encode('utf-8'))} B")
    return EXIT_OK


# ──────────────────────────────────────────────
# 검증 (W2)
# ──────────────────────────────────────────────


@dataclass
class _Context:
    catalog: ka.Catalog
    snapshot: dict[str, Any]
    schema_info: dict[str, Any]
    sql_checker: Callable[[str, Mapping[str, Any], str, str], list[str]] | None
    code_values: set[str]
    executor: SqlExecutor | None
    engine: str
    columns: dict[str, list[str]] = field(default_factory=dict)
    code_samples: dict[str, list[str]] = field(default_factory=dict)
    evidence_literals: set[str] = field(default_factory=set)
    fake_values: set[str] = field(default_factory=set)

    def check(self, client: Any) -> Any:
        from src.schema_cache.asset_generation_service import _SqlCheck

        return _SqlCheck(client, self.sql_checker, self.schema_info, self.engine, DB_ID)


#: MariaDB 실행 주석 — `SQLGuard`도 거절하나 사유를 밝히려고 실행 전에 따로 거절한다
_EXEC_COMMENT_REASON = "MariaDB 실행 주석(/*! … */)은 쓸 수 없습니다 — 주석 안이 실행된다"


def _issue(code: str, message: str) -> dict[str, str]:
    return ka.issue(code, message)


def _substitution_issues(
    name: str, item: Mapping[str, Any], values: set[str], label: str = "치환 코드값"
) -> list[dict[str, str]]:
    """치환값 리터럴(위치만 · 값 없음) — `label`은 문구의 값 종류(치환 코드값 · 형식 보존
    치환값)."""
    if not values:
        return []
    hits = ba.substitution_hits({name: ("", dict(item))}, values)
    if not hits:
        return []
    return [_issue(SUBSTITUTED, f"{label}이 나옵니다(위치: {'; '.join(hits[:5])})")]


def _evidence_literal_issues(
    name: str, item: Mapping[str, Any], values: set[str]
) -> list[dict[str, str]]:
    """근거 run 리터럴(위치만 · 값 없음) — `evidence` 칸(run ID)은 보지 않고, K8 슬롯 이름(자리표
    `:slot`·`:slot_start`·`:slot_end`)과 같은 값은 그 항목에서 뺀다. 대조는 토큰 경계
    (`build_assets.substitution_hits`)."""
    slots = item.get("slots") if name == ka.TEMPLATES_FILE else None
    names = {
        f"{s['name']}{suffix}".casefold()
        for s in (slots if isinstance(slots, list) else [])
        if isinstance(s, Mapping) and isinstance(s.get("name"), str)
        for suffix in ("", "_start", "_end")
    }
    wanted = {v for v in values if v.casefold() not in names}
    if not wanted:
        return []
    body = {k: v for k, v in item.items() if k != "evidence"}
    hits = ba.substitution_hits({name: ("", body)}, wanted)
    if not hits:
        return []
    return [_issue(EVIDENCE_LITERAL, f"근거 run 리터럴이 나옵니다(위치: {'; '.join(hits[:5])})")]


async def _example_issues(item: Mapping[str, Any], ctx: _Context) -> list[dict[str, str]]:
    """K2 — SQLGuard → 탭 검사(validate_sql) → `tables` 일치 → (정적 통과분만) DB 실행."""
    from src.schema_cache.asset_generation_service import _execute_check
    from src.security.sql_guard import SQLGuard
    from src.sql_validation import _extract_cte_names

    out = ka.example_item_issues(item, ctx.catalog)
    sql = str(item.get("sql") or "").strip().rstrip(";").strip()
    if not sql:
        return out
    safe, reason = SQLGuard().is_safe_select(sql)
    if qt.has_executable_comment(sql):
        out.append(_issue(SQL_GUARD, _EXEC_COMMENT_REASON))
    elif not safe:
        out.append(_issue(SQL_GUARD, reason))
    else:
        _, error = await _execute_check(ctx.check(_NoExecute()), sql)
        if error:
            out.append(_issue(SQL_INVALID, error))
    ctes = {c.lower() for c in _extract_cte_names(sql)}
    used = {t.lower() for t in jd.sql_tables(sql)} - ctes
    declared = {bare_name(str(t)).lower() for t in item.get("tables") or []}
    if used != declared:
        only_sql = ", ".join(sorted(used - declared)) or "-"
        only_declared = ", ".join(sorted(declared - used)) or "-"
        out.append(_issue(
            TABLES_MISMATCH,
            f"`tables`와 SQL 참조 테이블이 다릅니다(SQL만: {only_sql} · tables만: {only_declared})",
        ))
    return out


async def _execute_example(item: Mapping[str, Any], ctx: _Context) -> list[dict[str, str]]:
    from src.schema_cache.asset_generation_service import _execute_check

    if ctx.executor is None:
        return [_issue(DB_UNVERIFIED, "모의 DB 실행 미실시 — SQL을 실행하지 못했다")]
    sql = str(item.get("sql") or "").strip().rstrip(";").strip()
    _, error = await _execute_check(ctx.check(ctx.executor), sql)
    return [_issue(DB_FAILED, f"모의 DB 실행 실패: {error}")] if error else []


async def _section_static(text: str, ctx: _Context) -> tuple[list[dict[str, str]], int]:
    """K4 — D-294 ③ 섹션 검증(실행 없이) + 한글 식별자·사용률 → ``(문제, SQL 블록 수)``."""
    from src.schema_cache.asset_generation_service import _validate_section

    _, result = await _validate_section(ctx.check(_NoExecute()), text, ctx.snapshot)
    out = [_issue(SECTION_INVALID, str(e)) for e in result["errors"]]
    if qt.has_executable_comment(text):
        out.append(_issue(SQL_GUARD, _EXEC_COMMENT_REASON))
    out += ka.section_item_issues(text, ctx.catalog)
    return out, len(result["sql_checks"])


async def _execute_section(text: str, ctx: _Context) -> list[dict[str, str]]:
    from src.schema_cache.asset_generation_service import _validate_section

    if ctx.executor is None:
        return [_issue(DB_UNVERIFIED, "모의 DB 실행 미실시 — 섹션 SQL을 실행하지 못했다")]
    _, result = await _validate_section(ctx.check(ctx.executor), text, ctx.snapshot)
    return [_issue(DB_FAILED, f"모의 DB 실행 실패: {e}") for e in result["errors"]]


@dataclass
class _BoundSql:
    """K8 바인딩 조합 하나 — 표지(슬롯 이름만) · 조립 SQL · 오류 문구에서 가릴 코드값."""

    label: str
    sql: str
    hidden: tuple[str, ...]


def _mask(text: str, hidden: Iterable[str]) -> str:
    for value in sorted(set(hidden), key=len, reverse=True):
        text = text.replace(value, _CODE_MASK)
    return text


def _bound_sql_problem(sql: str, ctx: _Context) -> tuple[str, str] | None:
    """조립 SQL — `SQLGuard` → 부수효과 함수 → `validate_sql`(주입 검사기) → ``(코드, 사유)``."""
    from src.schema_cache.asset_generation_service import _SIDE_EFFECT_FUNC_RE
    from src.security.sql_guard import SQLGuard

    if qt.has_executable_comment(sql):
        return SQL_GUARD, _EXEC_COMMENT_REASON
    safe, reason = SQLGuard().is_safe_select(sql)
    if not safe:
        return SQL_GUARD, reason
    if (found := _SIDE_EFFECT_FUNC_RE.search(sql)) is not None:
        return SQL_INVALID, f"부수효과가 있는 함수는 쓸 수 없습니다: {found.group(1)}"
    if ctx.sql_checker is None:
        return SQL_INVALID, "SQL 검증기가 없어 실행하지 않았습니다"
    try:
        problems = ctx.sql_checker(sql, ctx.schema_info, ctx.engine, DB_ID)
    except Exception as e:  # noqa: BLE001 — 검증기 실패는 실행하지 않는 쪽으로
        return SQL_INVALID, f"SQL 검증 실패: {type(e).__name__}"
    if problems:
        return SQL_INVALID, "SQL 검증 실패: " + " / ".join(str(p) for p in problems[:3])
    return None


def _template_static(
    item: Mapping[str, Any], ctx: _Context
) -> tuple[list[dict[str, str]], list[_BoundSql], list[dict[str, str]]]:
    """K8 — 계약·식별자 실존 → 조회 대상 → 슬롯 전 조합 바인딩 → 조립 SQL 검사.

    Returns:
        ``(문제, 실행할 조합, 코드값 없어 보류한 조합 표지)``
    """
    raw = {"version": qt.TEMPLATE_FILE_VERSION, "templates": [dict(item)]}
    problems = qt.check_templates(raw, ctx.columns)
    if problems:
        return [_issue(TEMPLATE_CONTRACT, p) for p in problems], [], []
    template = qt.parse_templates(raw)[0][0]
    out: list[dict[str, str]] = []
    outside = sorted(
        t for t in template.tables if bare_name(t).strip("`").lower() not in ctx.catalog.allowed
    )
    if outside:
        out.append(_issue(ka.TABLE_NOT_ALLOWED, f"조회 대상 밖 테이블: {', '.join(outside)}"))
    code_slots = [s for s in template.slots if s.type == qt.SLOT_CODE]
    codes = {
        str(s.column): ctx.code_samples[str(s.column).lower()]
        for s in code_slots if ctx.code_samples.get(str(s.column).lower())
    }
    combos = qt.sample_bindings(template, codes)
    total = 1
    for spec in template.slots:
        total *= 1 if spec.required else 2
    pending: list[dict[str, str]] = []
    if total > len(combos):
        missing = [s.name for s in code_slots if str(s.column) not in codes]
        pending.append(_issue(
            CODE_UNVERIFIED,
            f"코드값 없음 — 실행 보류(슬롯 {', '.join(missing)} · "
            f"조합 {total - len(combos)}/{total})",
        ))
    code_names = {s.name for s in code_slots}
    bound: list[_BoundSql] = []
    for index, combo in enumerate(combos, 1):
        label = f"조합 {index}/{len(combos)}({', '.join(sorted(combo)) or '슬롯 없음'})"
        hidden = tuple(str(v) for name, v in combo.items() if name in code_names)
        result = qt.bind_template(template, combo, codes)
        if result.sql is None:
            out.append(_issue(TEMPLATE_BIND, f"{label}: {result.reason} — {result.detail}"))
            continue
        problem = _bound_sql_problem(result.sql, ctx)
        if problem is not None:
            out.append(_issue(problem[0], f"{label}: {_mask(problem[1], hidden)}"))
        else:
            bound.append(_BoundSql(label, result.sql, hidden))
    return out, bound, pending


async def _execute_template(bound: list[_BoundSql], ctx: _Context) -> list[dict[str, str]]:
    """K8 조합 SQL을 바깥 행 제한으로 감싸 실행한다(오류 문구의 코드값은 가린다)."""
    from src.schema_cache.asset_generation_service import SQL_CHECK_LIMIT
    from src.utils.sql_dialect import row_limit_clause

    if not bound:
        return []
    if ctx.executor is None:
        return [_issue(
            DB_UNVERIFIED, f"모의 DB 실행 미실시 — 템플릿 조합 {len(bound)}개를 실행하지 못했다"
        )]
    limit = row_limit_clause(ctx.engine, SQL_CHECK_LIMIT)
    out: list[dict[str, str]] = []
    for b in bound:
        try:
            await ctx.executor.execute_sql(f"SELECT * FROM ({b.sql}) q {limit}")
        except Exception as e:  # noqa: BLE001 — 실행 실패는 사유로 돌려준다
            error = _mask(f"{type(e).__name__}: {e}", b.hidden)
            out.append(_issue(DB_FAILED, f"모의 DB 실행 실패 {b.label}: {error}"))
    return out


def _result(
    name: str, label: str, issues: list[dict[str, str]], static_only: bool
) -> dict[str, Any]:
    tolerated = {DB_UNVERIFIED, CODE_UNVERIFIED} if static_only else set()
    return {
        "file": name, "id": label,
        "ok": all(i["code"] in tolerated for i in issues),
        "issues": issues,
    }


async def _validate_file(
    name: str, parsed: ka.ParsedFile, ctx: _Context, static_only: bool
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """한 파일 → ``(항목 결과, 철회 목록)`` — 파일 단위 결과는 id ``*``."""
    if parsed.file_issues:
        return [_result(name, "*", parsed.file_issues, static_only)], []
    withdrawn = [
        {"file": name, "id": e.label, "reason": e.item.get("reason")}
        for e in parsed.entries if e.withdrawn and not e.issues
    ]
    results: list[dict[str, Any]] = [
        _result(name, e.label, list(e.issues), static_only)
        for e in parsed.entries if e.withdrawn and e.issues
    ]
    # 철회 항목도 커밋된다 — 근거 리터럴·형식 보존 치환값은 철회 항목에서도 거절(원천은 git 추적
    # — D-311 ② 「지식 원천에 넣지 않는다」)
    for e in parsed.entries:
        found = (
            _evidence_literal_issues(name, e.item, ctx.evidence_literals)
            + _substitution_issues(
                name, e.item, ctx.fake_values, f"형식 보존 치환값({rd.SUBSTITUTIONS_FILE})"
            )
            if e.withdrawn and not e.issues else []
        )
        if found:
            results.append(_result(name, e.label, found, static_only))
            withdrawn = [w for w in withdrawn if w["id"] != e.label]
    active = parsed.active
    items = [e.item for e in active]
    cross: dict[int, list[dict[str, str]]] = {}
    if name == ka.SYNONYMS_FILE:
        for source in (ka.ambiguous_synonyms(items), ka.duplicate_targets(items)):
            for index, found in source.items():
                cross.setdefault(index, []).extend(found)
    elif name == ka.DESCRIPTIONS_FILE:
        cross = ka.duplicate_targets(items)
    elif name == ka.EXAMPLES_FILE:
        cross = ka.duplicate_targets(
            items, key=lambda i: str(i.get("question") or "").strip().casefold(), label="같은 질문"
        )
    passing_texts: list[str] = []
    for index, entry in enumerate(active):
        issues = list(entry.issues) + cross.get(index, [])
        item = entry.item
        if not entry.issues:
            if name == ka.GUIDE_FILE:
                issues += ka.guide_item_issues(str(item["text"]), ctx.catalog)
            elif name == ka.EXAMPLES_FILE:
                issues += await _example_issues(item, ctx)
            elif name == ka.DESCRIPTIONS_FILE:
                issues += ka.description_item_issues(item, ctx.catalog)
            elif name == ka.SYNONYMS_FILE:
                issues += ka.synonym_item_issues(item, ctx.catalog)
            blocks = 0
            bound: list[_BoundSql] = []
            pending: list[dict[str, str]] = []
            if name == ka.SECTION_FILE:
                found, blocks = await _section_static(str(item["text"]), ctx)
                issues += found
            elif name == ka.TEMPLATES_FILE:
                found, bound, pending = _template_static(item, ctx)
                issues += found
            issues += _substitution_issues(name, item, ctx.code_values)
            issues += _substitution_issues(
                name, item, ctx.fake_values, f"형식 보존 치환값({rd.SUBSTITUTIONS_FILE})"
            )
            issues += _evidence_literal_issues(name, item, ctx.evidence_literals)
            if not issues and name == ka.EXAMPLES_FILE:
                issues += await _execute_example(item, ctx)
            elif not issues and name == ka.SECTION_FILE and blocks:
                issues += await _execute_section(str(item["text"]), ctx)
            elif not issues and name == ka.TEMPLATES_FILE:
                issues += await _execute_template(bound, ctx) + pending
        result = _result(name, entry.label, issues, static_only)
        results.append(result)
        if result["ok"] and name in (ka.GUIDE_FILE, ka.SECTION_FILE):
            passing_texts.append(str(item["text"]))
    if name == ka.GUIDE_FILE and passing_texts:
        total = ka.guide_total_issues(passing_texts)
        if total:
            results.append(_result(name, "*", total, static_only))
    if name == ka.SECTION_FILE and passing_texts:
        from src.schema_cache.asset_generation_service import _validate_section

        _, joined = await _validate_section(
            ctx.check(_NoExecute()), ka.join_texts(passing_texts), ctx.snapshot
        )
        if joined["errors"]:
            results.append(_result(
                name, "*", [_issue(SECTION_INVALID, f"연결본: {e}") for e in joined["errors"]],
                static_only,
            ))
    return results, withdrawn


async def avalidate_dir(
    knowledge_dir: Path,
    *,
    catalog: Mapping[str, Any],
    allowed: Iterable[str],
    executor: SqlExecutor | None,
    sql_checker: Callable[[str, Mapping[str, Any], str, str], list[str]] | None = None,
    definitions: Mapping[str, Any] | None = None,
    code_values: set[str] | None = None,
    static_only: bool = False,
    engine: str = ENGINE,
    code_samples: Mapping[str, Iterable[str]] | None = None,
    evidence_literals: Iterable[str] | None = None,
    fake_values: Iterable[str] | None = None,
) -> dict[str, Any]:
    """원천 파일 디렉터리를 검증한다(쓰기 없음).

    Args:
        knowledge_dir: 원천 파일 디렉터리
        catalog: 캐시 모양 스키마(`load_schema`) — 식별자 실존 대조 기준
        allowed: 조회 대상(`allowed_tables`) — SQL 검사 스키마는 이 테이블만
        executor: DB 실행기(None이면 실행 대상 항목은 `db_unverified`)
        sql_checker: `validate_sql` 검사기(`asset_sql_checker` 모양 · 없으면 SQL 항목은 거절)
        definitions: `table_definitions`(있으면 K6 파생 규칙도 검사해 싣는다)
        code_values: 치환 코드값 대조 집합(`load_code_values`)
        static_only: `db_unverified`·`code_unverified`를 실패로 치지 않는다
        code_samples: K8 code 슬롯 대표값 ``{"table.column": [치환값…]}``(`load_code_samples` ·
            없는 컬럼의 조합은 `code_unverified`)
        evidence_literals: 근거 run 리터럴 대조 집합(`load_evidence_literals` — 대조 카탈로그의
            테이블·컬럼 이름과 같은 값은 여기서 한 번 더 뺀다)
        fake_values: 형식 보존 치환값 대조 집합(`load_substitution_values` — 차단 전용 · K8 대표값에
            섞지 않는다)

    Returns:
        ``{"static_only", "db_executed", "results": [{file, id, ok, issues:[{code, message}]}],
        "withdrawn", "skipped", "derived": {"query_rules", "issues"}, "summary"}`` — 파일 단위
        결과(id ``*``)가 실패면 그 파일의 연결 산출물(K1 가이드·K4 섹션)을 쓰지 않는다
    """
    from src.schema_cache.db_structure_service import schema_dict_from_snapshot

    allowed_list = [str(t) for t in allowed]
    scope = {bare_name(t).lower() for t in allowed_list}
    snapshot = build_snapshot(dict(catalog))
    tables = {
        t: d for t, d in (snapshot.get("tables") or {}).items() if bare_name(t).lower() in scope
    }
    snapshot = {**snapshot, "tables": tables, "table_count": len(tables)}
    columns = schema_columns(catalog)
    ctx = _Context(
        catalog=ka.make_catalog(columns, allowed_list),
        snapshot=snapshot,
        schema_info=schema_dict_from_snapshot(snapshot),
        sql_checker=sql_checker,
        code_values=set(code_values or ()),
        executor=executor,
        engine=engine,
        columns=columns,
        code_samples={
            str(k).lower(): [str(v) for v in values] for k, values in (code_samples or {}).items()
        },
        evidence_literals=ka.evidence_literal_values(
            evidence_literals or (), exempt=[n for t, cols in columns.items() for n in (t, *cols)]
        ),
        fake_values=set(fake_values or ()),
    )
    results: list[dict[str, Any]] = []
    withdrawn: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for name in ka.KNOWLEDGE_FILES:
        path = Path(knowledge_dir) / name
        if not path.is_file():
            continue
        try:
            document = _read_yaml(path)
        except yaml.YAMLError as e:
            results.append(_result(
                name, "*", [_issue(ka.FILE_INVALID, f"YAML 오류: {e}")], static_only
            ))
            continue
        if name == ka.TEMPLATES_FILE:  # K8은 런타임 형식(`templates:`) — 공통 칸 파싱용으로 옮긴다
            if not (isinstance(document, Mapping) and isinstance(document.get("templates"), list)):
                results.append(_result(
                    name, "*", [_issue(ka.FILE_INVALID, "`templates`가 목록이 아닙니다(K8)")],
                    static_only,
                ))
                continue
            document = {"version": document.get("version"), "items": document["templates"]}
        file_results, file_withdrawn = await _validate_file(
            name, ka.parse_file(name, document), ctx, static_only
        )
        results += file_results
        withdrawn += file_withdrawn
    derived: dict[str, Any] = {"query_rules": [], "issues": []}
    if definitions:
        rules = ka.derive_kind_rules(
            definitions, columns, types=schema_types(catalog), allowed=allowed_list
        )
        derived["query_rules"] = rules
        derived["issues"] = [
            {"index": i, **found}
            for i, rule in enumerate(rules)
            for found in ka.text_form_issues(rule) + ka.mention_issues(rule, ctx.catalog)
        ]
    failed = [r for r in results if not r["ok"]]
    return {
        "static_only": static_only,
        "db_executed": executor is not None,
        "results": results,
        "withdrawn": withdrawn,
        "skipped": skipped,
        "derived": derived,
        "summary": {
            "items": sum(1 for r in results if r["id"] != "*"),
            "ok": sum(1 for r in results if r["ok"] and r["id"] != "*"),
            "failed": len(failed),
            DB_UNVERIFIED: sum(
                1 for r in results if any(i["code"] == DB_UNVERIFIED for i in r["issues"])
            ),
            CODE_UNVERIFIED: sum(
                1 for r in results if any(i["code"] == CODE_UNVERIFIED for i in r["issues"])
            ),
            "withdrawn": len(withdrawn),
            "derived_query_rules": len(derived["query_rules"]),
            "derived_issues": len(derived["issues"]),
        },
    }


def validate_dir(knowledge_dir: Path, **kwargs: Any) -> dict[str, Any]:
    """`avalidate_dir`의 동기 진입(이벤트 루프 밖에서 부른다)."""
    return asyncio.run(avalidate_dir(knowledge_dir, **kwargs))


def passed(result: Mapping[str, Any]) -> bool:
    """검증 전체 통과 — 실패 결과 0 · K6 파생 규칙 문제 0."""
    return not result["summary"]["failed"] and not result["summary"]["derived_issues"]


# ──────────────────────────────────────────────
# 빌더 오버레이 재료 (W4)
# ──────────────────────────────────────────────

#: 오버레이 검증 방식 표지(빌더 머리 주석)
VERIFIED_DB = "모의 DB 실행"
VERIFIED_STATIC = "정적 검사만 — 모의 DB 미실행"


def _catalog_key(
    table: Any, column: Any, columns: Mapping[str, Iterable[str]]
) -> str:
    """원천 `table`·`column` → 카탈로그 원 이름의 ``table.column``(대소문자·맨 이름 무시 대조)."""
    names = {
        bare_name(str(t)).lower(): (bare_name(str(t)), list(cols)) for t, cols in columns.items()
    }
    found = names.get(bare_name(str(table)).lower())
    if found is None:
        return f"{table}.{column}"
    wanted = str(column).strip().casefold()
    original = next((c for c in found[1] if str(c).strip().casefold() == wanted), str(column))
    return f"{found[0]}.{original}"


def overlay_from_result(
    knowledge_dir: Path,
    result: Mapping[str, Any],
    columns: Mapping[str, Iterable[str]],
    *,
    verification: str,
) -> dict[str, Any]:
    """검증 결과에서 **통과한 active 항목만** 골라 빌더 오버레이 재료를 만든다(쓰기 없음).

    파일 단위 결과(id ``*``)가 실패한 파일은 통째로 뺀다(K1 연결 길이 · K4 연결본 검사). K6은 파생
    규칙 중 문제 없는 것만. K2 `description`은 프롬프트 예시 블록 칸 이름 `explanation`으로 바꾸고
    원천 `id`를 함께 싣는다(검증 모드·켜고 끄기의 항목 대조용 — 소비처는 추가 키를 읽지 않는다).
    ``withdrawn_synonyms``는 원천 유사어 항목(철회·거절 포함)의 낱말 중 이번에 통과하지 못한
    것이다 — 빌더가 기존 시드 파일에서 걷어 낸다(철회 반영).

    Returns:
        ``{"runs", "verification", "query_guide", "query_examples", "query_rules", "section",
        "descriptions", "synonyms", "templates", "withdrawn_synonyms", "counts"}`` — ``counts``의
        K6 외 합이 0이면 오버레이할 것이 없다(`overlay_size`)
    """
    ok_ids: dict[str, set[str]] = {}
    file_failed: set[str] = set()
    for r in result["results"]:
        if r["id"] == "*":
            if not r["ok"]:
                file_failed.add(r["file"])
        elif r["ok"]:
            ok_ids.setdefault(r["file"], set()).add(str(r["id"]))
    docs = knowledge_documents(knowledge_dir)

    def passing(name: str) -> list[dict[str, Any]]:
        doc = docs.get(name)
        key = "templates" if name == ka.TEMPLATES_FILE else "items"
        raw = doc.get(key) if isinstance(doc, Mapping) and name not in file_failed else None
        return [
            dict(i) for i in (raw if isinstance(raw, list) else [])
            if isinstance(i, Mapping) and i.get("status") == ka.STATUS_ACTIVE
            and str(i.get("id")) in ok_ids.get(name, set())
        ]

    guide = passing(ka.GUIDE_FILE)
    examples = passing(ka.EXAMPLES_FILE)
    section = passing(ka.SECTION_FILE)
    descriptions = passing(ka.DESCRIPTIONS_FILE)
    synonyms = passing(ka.SYNONYMS_FILE)
    templates = passing(ka.TEMPLATES_FILE)
    derived = result.get("derived") or {}
    bad = {found["index"] for found in derived.get("issues") or []}
    rules = [r for i, r in enumerate(derived.get("query_rules") or []) if i not in bad]
    synonym_map: dict[str, list[str]] = {}
    for item in synonyms:
        merged = synonym_map.setdefault(_catalog_key(item["table"], item["column"], columns), [])
        merged += [str(w) for w in item["words"] if str(w) not in merged]
    withdrawn_synonyms: dict[str, list[str]] = {}
    synonyms_doc = docs.get(ka.SYNONYMS_FILE)
    raw = synonyms_doc.get("items") if isinstance(synonyms_doc, Mapping) else None
    for item in raw if isinstance(raw, list) else []:
        if not (isinstance(item, Mapping) and isinstance(item.get("words"), list)):
            continue
        key = _catalog_key(item.get("table"), item.get("column"), columns)
        gone = withdrawn_synonyms.setdefault(key, [])
        gone += [
            str(w) for w in item["words"]
            if str(w) not in synonym_map.get(key, []) and str(w) not in gone
        ]
    included = [*guide, *examples, *section, *descriptions, *synonyms, *templates]
    return {
        "runs": sorted({str(i.get("evidence")) for i in included}),
        "verification": verification,
        "query_guide": ka.join_texts(i["text"] for i in guide) if guide else None,
        "query_examples": [
            {
                "question": str(i["question"]).strip(),
                "sql": str(i["sql"]).strip().rstrip(";").strip(),
                "explanation": str(i["description"]).strip(),
                "id": str(i["id"]),
            }
            for i in examples
        ],
        "query_rules": rules,
        "section": ka.join_texts(i["text"] for i in section) if section else None,
        "descriptions": {
            _catalog_key(i["table"], i["column"], columns): str(i["text"]).strip()
            for i in descriptions
        },
        "synonyms": synonym_map,
        "templates": templates,
        "withdrawn_synonyms": {k: v for k, v in withdrawn_synonyms.items() if v},
        "counts": {
            "query_guide": len(guide),
            "query_examples": len(examples),
            "query_rules": len(rules),
            "prompt_section": len(section),
            "column_descriptions": len(descriptions),
            "synonyms": len(synonyms),
            "query_templates": len(templates),
        },
    }


def overlay_size(overlay: Mapping[str, Any]) -> int:
    """원천에서 온 통과 항목 수(K6 파생 규칙 제외)."""
    return sum(n for key, n in overlay["counts"].items() if key != "query_rules")


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────


@dataclass
class KnowledgeDeps:
    """검증 DB 의존성 — `itam` 소스 클라이언트 팩토리(async 컨텍스트) · SQL 검사기."""

    client_factory: Callable[[], AbstractAsyncContextManager[Any]]
    sql_checker: Callable[[str, Mapping[str, Any], str, str], list[str]] | None


def default_deps(cfg: Any = None) -> KnowledgeDeps:
    """P2(`default_p2_deps`)와 같은 클라이언트·검사기 — `itam` 소스(모의 DB로 돌려 둔 MCP 소스).

    설정은 연결할 때 읽는다(`cfg`가 None이면 `load_config()` — 정적 전용 실행은 읽지 않는다).
    """
    from src.api.routes.db_structure import asset_sql_checker

    def client_factory() -> AbstractAsyncContextManager[Any]:
        from src.config import load_config
        from src.db import get_db_client

        return get_db_client(cfg if cfg is not None else load_config(), db_id=DB_ID)

    return KnowledgeDeps(client_factory=client_factory, sql_checker=asset_sql_checker)


async def _probe(client: Any, allowed: list[str]) -> str | None:
    """연결 확인 — 상태 · 대표 조회 대상 테이블 읽기(모의 DB가 아닌 소스를 거른다).

    Returns:
        문제 사유 또는 None
    """
    if not bool(await client.health_check()):
        return "health_check 실패"
    if allowed:
        table = sorted(allowed)[0]
        try:
            await client.execute_sql(f"SELECT 1 FROM {table} LIMIT 1")
        except Exception as e:  # noqa: BLE001 — 사유로 돌려준다
            return (
                f"대표 테이블 `{table}` 조회 실패({type(e).__name__}) — "
                "모의 DB(108테이블)가 아닐 수 있다"
            )
    return None


async def _run_with_db(
    knowledge_dir: Path, deps: KnowledgeDeps | None, static_only: bool, **kwargs: Any
) -> tuple[dict[str, Any], str | None]:
    """DB에 연결해(정적 전용이 아니면) 검증한다 → ``(결과, 연결 불가 사유)``."""
    if static_only or deps is None:
        result = await avalidate_dir(
            knowledge_dir, executor=None, static_only=static_only,
            sql_checker=deps.sql_checker if deps else None, **kwargs,
        )
        return result, None if static_only else "DB 의존성 없음"
    async with AsyncExitStack() as stack:
        reason: str | None
        client: Any = None
        try:
            client = await stack.enter_async_context(deps.client_factory())
            reason = await _probe(client, list(kwargs.get("allowed") or []))
        except Exception as e:  # noqa: BLE001 — 연결 실패는 사유로 끝낸다(침묵 통과 없음)
            reason = f"{DB_ID} 소스 연결 실패: {type(e).__name__}: {e}"
        result = await avalidate_dir(
            knowledge_dir, executor=None if reason else client, static_only=False,
            sql_checker=deps.sql_checker, **kwargs,
        )
        return result, reason


def print_summary(result: Mapping[str, Any], *, reason: str | None) -> None:
    """사람이 읽는 요약(첫 줄은 DB 실행 여부)."""
    s = result["summary"]
    if result["static_only"]:
        print(
            "모의 DB 실행 미실시(--static-only) — 정적 검사만 했다 · "
            "db_unverified·code_unverified는 실패로 치지 않는다"
        )
    elif reason:
        print(f"모의 DB 실행 미실시 — {reason} · SQL 항목은 db_unverified(실패)")
    else:
        print("모의 DB 실행 — SQL 항목을 읽기 전용으로 실행했다")
    print(
        f"[validate-knowledge] 항목 {s['items']} · 통과 {s['ok']} · 실패 결과 {s['failed']} · "
        f"db_unverified {s[DB_UNVERIFIED]} · 코드값 없어 실행 보류 {s[CODE_UNVERIFIED]} · "
        f"철회 {s['withdrawn']} · "
        f"K6 파생 규칙 {s['derived_query_rules']}(문제 {s['derived_issues']})"
    )
    for skip in result["skipped"]:
        print(f"  건너뜀: {skip['file']} — {skip['reason']}")
    for r in result["results"]:
        if r["ok"]:
            for found in r["issues"]:  # 정적 전용에서 통과로 친 보류도 침묵하지 않는다
                if found["code"] == CODE_UNVERIFIED:
                    print(f"  보류 {r['file']} {r['id']}: {found['message']}")
            continue
        for found in r["issues"]:
            print(f"  거절 {r['file']} {r['id']}: {found['code']} — {found['message']}")
    for found in result["derived"]["issues"]:
        print(f"  K6 규칙 {found['index']}: {found['code']} — {found['message']}")


def run_validate(
    knowledge_dir: Path,
    *,
    catalog_path: Path | None = None,
    static_only: bool = False,
    out: Path | None = None,
    deps: KnowledgeDeps | None = None,
    repo_root: Path = REPO_ROOT,
    results_root: Path = RESULTS_ROOT,
) -> int:
    """원천 파일을 검증해 요약을 출력한다(`--out`이면 결과 YAML) → 종료 코드(0 통과 · 1 거절 ·
    2 입력 오류)."""
    knowledge_dir = Path(knowledge_dir)
    try:
        if not knowledge_dir.is_dir():
            raise ba.BuildError(f"원천 디렉터리가 없습니다: {knowledge_dir}", EXIT_INPUT)
        profile = load_profile(repo_root)
        schema = load_schema(catalog_path or Path(repo_root) / ba.SCHEMA_SEED_REL)
        runs = evidence_runs(knowledge_dir)
        code_values, sample_runs = load_code_values(runs, results_root)
        code_samples = load_code_samples(runs, results_root)
        literals, literal_runs = load_evidence_literals(runs, results_root)
        fakes, fake_runs, fake_counts = load_substitution_values(runs, results_root)
    except ba.BuildError as e:
        print(f"[validate-knowledge] 중단: {e}")
        return e.exit_code
    result, reason = asyncio.run(_run_with_db(
        knowledge_dir, deps, static_only,
        catalog=schema,
        allowed=[str(t) for t in profile.get("allowed_tables") or []],
        definitions=profile.get("table_definitions") or {},
        code_values=code_values,
        code_samples=code_samples,
        evidence_literals=literals,
        fake_values=fakes,
    ))
    result["db_unavailable_reason"] = reason
    result["code_samples_runs"] = sample_runs
    result["evidence_literal_runs"] = literal_runs
    result["substitutions_runs"] = fake_runs
    print_summary(result, reason=reason)
    if not any(Path(knowledge_dir, n).is_file() for n in ka.KNOWLEDGE_FILES):
        print(f"  원천 파일 0개 — {knowledge_dir}")
    print(f"  치환 코드값 대조: {', '.join(sample_runs) or '근거 run에 code_samples.yaml 없음'}")
    print(
        f"  근거 run 리터럴 대조: {', '.join(literal_runs) or '근거 run 산출물 없음'}"
        f"({len(literals)}개 · 값은 싣지 않는다)"
    )
    if fake_runs:  # 1·2회차(파일 없음)는 출력 그대로
        print(
            f"  형식 보존 치환값 대조: {', '.join(fake_runs)}({len(fakes)}개 · 짧은 값 "
            f"{fake_counts['short_skipped']}개·식별자 {fake_counts['identifier_skipped']}개 제외 · "
            "값은 싣지 않는다)"
        )
    if out is not None:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(_dump_yaml_exact(dict(result)), encoding="utf-8")
        print(f"  씀: {out}")
    return EXIT_OK if passed(result) else EXIT_FAILED
