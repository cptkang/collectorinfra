"""`--verify-assets` — 반입된 지식 자산 SQL 읽기 전용 실행 검증 (plans/143 W5 · D-316 ⑤).

현재 저장소의 반입본을 읽어 SQL을 모은다.

- K2 쿼리 예시: `config/db_profiles/itam.yaml` `query_examples` — 항목 `id`(식별자 모양일
  때), 없으면 질문 해시(`k2-<10자>`)
- K4 DB 전용 규칙 섹션: `config/knowledge/itam/prompt_template.yaml` `section`의 펜스 SQL —
  `k4-<순번>`
- K8 결정적 조립 템플릿: `config/knowledge/itam/query_templates.yaml`의 active 템플릿 × 슬롯
  조합 대표값(`sample_bindings` · code 슬롯은 프로필 `code_values` 첫 값) — `<템플릿 id>#<순번>`.
  code 슬롯 대표값이 없으면(외부망) 그 몫을 「보류」(`<템플릿 id>#pending`)로 남긴다.

실행은 `--check-oracle`과 같은 읽기 전용 경로다 — 러너 선검사(`is_select_only`) + `SQLGuard` +
부수효과 함수 · MariaDB 실행 주석(`/*! … */` — `SQLGuard`도 거절 · 사유는 따로) 차단을 통과한 SQL만
MCP readonly 클라이언트(`scripts.scenario.oracle._open_client`)로 돌린다. `DB_BACKEND=direct`면
멈춘다. 바깥 행 제한(`LIMIT 101`)으로 감싸 행 수 구간만 잰다.

산출 `asset_verification.yaml`(7번째 반출 파일)은 항목 ID · 종류 · 지문(SQL 해시 앞 12자) · ok ·
오류 범주(짧은 열거 — DB 오류 원문 없음) · 행 수 구간(`0`·`1~10`·`11~100`·`100+`)만 싣는다. 결과
행·값·SQL 원문은 남기지 않는다. 누출 관문을 통과해야 `run.json`·`report.md`와 함께 쓴다(실패면
`leak_check.json`만). 이 모드의 SQL 실행은 D-301 ③ 「벤치 DB 조회 0」의 명시 예외다(D-316 ⑤).
"""

from __future__ import annotations

import asyncio
import getpass
import hashlib
import re
import socket
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from . import DB_ID, REPO_ROOT, RESULTS_ROOT
from . import redact as rd

VERIFICATION_FILE = "asset_verification.yaml"
FORMAT_VERSION = 1
ENGINE = "mariadb"
#: 바깥 행 제한 — `100+` 구간을 가를 만큼만 읽는다
OUTER_LIMIT = 101
ROW_BUCKETS: tuple[str, ...] = ("0", "1~10", "11~100", "100+")

KIND_EXAMPLE = "K2"
KIND_SECTION = "K4"
KIND_TEMPLATE = "K8"

#: 오류 범주(짧은 열거) — DB 오류 문구는 분류에만 쓰고 버린다
ERR_GUARD = "guard_rejected"
ERR_BIND = "bind_failed"
ERR_TIMEOUT = "timeout"
ERR_UNKNOWN_OBJECT = "unknown_object"
ERR_SYNTAX = "syntax"
ERR_PERMISSION = "permission"
ERR_CONNECTION = "connection"
ERR_DB = "db_error"
PENDING_CODE = "pending_code_values"
ERROR_CATEGORIES: tuple[str, ...] = (
    ERR_GUARD,
    ERR_BIND,
    ERR_TIMEOUT,
    ERR_UNKNOWN_OBJECT,
    ERR_SYNTAX,
    ERR_PERMISSION,
    ERR_CONNECTION,
    ERR_DB,
)

_ITEM_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_CLASSIFY: tuple[tuple[str, re.Pattern[str]], ...] = (
    (ERR_TIMEOUT, re.compile(r"(?i)time[d ]?\s*out")),
    (
        ERR_UNKNOWN_OBJECT,
        re.compile(r"(?i)doesn't exist|does not exist|unknown (?:column|table)|no such|\b1146\b|"
                   r"\b1054\b"),
    ),
    (ERR_SYNTAX, re.compile(r"(?i)syntax|\b1064\b")),
    (ERR_PERMISSION, re.compile(r"(?i)denied|permission|read[- ]?only|\b1142\b")),
    (ERR_CONNECTION, re.compile(r"(?i)connect|unreachable|refused")),
)
KST = timezone(timedelta(hours=9))

#: 실행기 — 읽기 전용 클라이언트의 `execute_sql`(결과의 `rows`·`truncated`).
Execute = Callable[[str], Awaitable[Any]]


@dataclass(frozen=True)
class VerifyItem:
    """검증 항목 하나 — `sql`이 없으면 `pending`(보류) 또는 `error`(실행 전 실패) 범주를 갖는다."""

    id: str
    kind: str
    sql: str | None
    pending: str | None = None
    error: str | None = None
    slots: tuple[str, ...] | None = None


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def row_bucket(count: int) -> str:
    """행 수 → 구간(`0` · `1~10` · `11~100` · `100+`)."""
    if count <= 0:
        return ROW_BUCKETS[0]
    if count <= 10:
        return ROW_BUCKETS[1]
    if count <= 100:
        return ROW_BUCKETS[2]
    return ROW_BUCKETS[3]


def classify_error(exc: BaseException) -> str:
    """예외 → 오류 범주(원문은 버린다)."""
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return ERR_TIMEOUT
    if isinstance(exc, (ConnectionError, OSError)):
        return ERR_CONNECTION
    text = f"{type(exc).__name__} {exc}"
    for category, pattern in _CLASSIFY:
        if pattern.search(text):
            return category
    return ERR_DB


# ──────────────────────────────────────────────
# 항목 수집
# ──────────────────────────────────────────────


def _read_yaml(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None


def _source_mark(value: Any, count: int) -> dict[str, Any] | None:
    if not count:
        return None
    text = value if isinstance(value, str) else yaml.safe_dump(value, allow_unicode=True)
    return {"fp": fingerprint(text), "n": count}


def _unique(base: str, seen: set[str]) -> str:
    candidate, index = base, 2
    while candidate in seen:
        candidate, index = f"{base}-{index}", index + 1
    seen.add(candidate)
    return candidate


def example_items(profile: Mapping[str, Any]) -> list[VerifyItem]:
    """K2 — 프로필 `query_examples`."""
    items: list[VerifyItem] = []
    seen: set[str] = set()
    for example in profile.get("query_examples") or []:
        if not isinstance(example, Mapping):
            continue
        sql = str(example.get("sql") or "").strip()
        raw_id = example.get("id")
        if isinstance(raw_id, str) and _ITEM_ID.match(raw_id):
            base = raw_id
        else:
            basis = str(example.get("question") or sql)
            base = "k2-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:10]
        items.append(VerifyItem(_unique(base, seen), KIND_EXAMPLE, sql or None,
                                error=None if sql else ERR_GUARD))
    return items


def section_items(document: Any, db_id: str = DB_ID) -> list[VerifyItem]:
    """K4 — DB 전용 규칙 섹션의 펜스 SQL(생성 어댑터와 같은 담당 조건: `db_id` 일치 · 섹션 있음)."""
    from src.schema_cache.asset_generation_service import _FENCE_RE

    if not (
        isinstance(document, Mapping)
        and document.get("db_id") == db_id
        and isinstance(document.get("section"), str)
    ):
        return []
    blocks = [b.strip() for b in _FENCE_RE.findall(document["section"])]
    return [
        VerifyItem(f"k4-{i}", KIND_SECTION, block or None, error=None if block else ERR_GUARD)
        for i, block in enumerate(blocks, start=1)
    ]


def template_items(
    raw: Any, code_values: Mapping[str, Sequence[Any]] | None
) -> tuple[list[VerifyItem], int]:
    """K8 — active 템플릿 × 슬롯 조합 대표값 → ``(항목, 계약 위반 템플릿 수)``."""
    from src.domain import query_templates as qt

    if raw is None:
        return [], 0
    templates, issues = qt.parse_templates(raw)
    codes = {str(k): list(v) for k, v in (code_values or {}).items() if isinstance(v, list)}
    items: list[VerifyItem] = []
    for template in templates:
        if template.status != qt.STATUS_ACTIVE:
            continue
        for index, combo in enumerate(qt.sample_bindings(template, codes), start=1):
            bound = qt.bind_template(template, combo, codes)
            items.append(
                VerifyItem(
                    f"{template.id}#{index}",
                    KIND_TEMPLATE,
                    bound.sql,
                    error=None if bound.sql else ERR_BIND,
                    slots=tuple(bound.slot_names) if bound.sql else tuple(sorted(combo)),
                )
            )
        if any(s.type == qt.SLOT_CODE and not codes.get(s.column or "") for s in template.slots):
            items.append(
                VerifyItem(f"{template.id}#pending", KIND_TEMPLATE, None, pending=PENDING_CODE)
            )
    # 위반 사유는 `"<id>: 사유"`(또는 `"#순번: 사유"`) — 문구는 버리고 템플릿 수만 센다
    return items, len({issue.split(":", 1)[0] for issue in issues})


def collect_items(
    repo_root: Path = REPO_ROOT, db_id: str = DB_ID
) -> tuple[list[VerifyItem], dict[str, Any]]:
    """반입본 → ``(검증 항목, 원천 지문)``. 원천 지문은 파일별 내용 해시·건수(값 없음)."""
    from src.db_adapters.template_assembler import TEMPLATE_PATH
    from src.schema_cache.asset_store import ASSET_PATHS

    profile = _read_yaml(repo_root / "config" / "db_profiles" / f"{db_id}.yaml")
    profile = profile if isinstance(profile, Mapping) else {}
    section_doc = _read_yaml(repo_root / ASSET_PATHS["prompt_template"].format(db_id=db_id))
    templates_raw = _read_yaml(repo_root / TEMPLATE_PATH.format(db_id=db_id))
    raw_codes = profile.get("code_values")
    examples = example_items(profile)
    sections = section_items(section_doc, db_id)
    templates, contract_issues = template_items(
        templates_raw, raw_codes if isinstance(raw_codes, Mapping) else None
    )
    sources = {
        "query_examples": _source_mark(profile.get("query_examples"), len(examples)),
        "prompt_template": _source_mark(
            section_doc.get("section") if isinstance(section_doc, Mapping) else None,
            len(sections),
        ),
        "query_templates": _source_mark(
            templates_raw, len({item.id.split("#", 1)[0] for item in templates})
        ),
        "template_contract_issues": contract_issues,
    }
    return [*examples, *sections, *templates], sources


# ──────────────────────────────────────────────
# 실행
# ──────────────────────────────────────────────


def _guard_ok(sql: str) -> bool:
    """러너 선검사 + `SQLGuard` + 부수효과 함수 · 실행 주석 차단(쓰기·다중 문장은 여기서 끝난다)."""
    from scripts.eval_text2sql import is_select_only
    from src.domain.query_templates import has_executable_comment
    from src.schema_cache.asset_generation_service import _SIDE_EFFECT_FUNC_RE
    from src.security.sql_guard import SQLGuard

    if has_executable_comment(sql) or not is_select_only(sql):
        return False
    safe, _reason = SQLGuard().is_safe_select(sql)
    return safe and _SIDE_EFFECT_FUNC_RE.search(sql) is None


def _record(item: VerifyItem, **fields: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": item.id,
        "kind": item.kind,
        "fp": fingerprint(item.sql) if item.sql else None,
        "ok": None,
        "error": None,
        "rows": None,
    }
    if item.slots is not None:
        out["slots"] = list(item.slots)
    out.update(fields)
    return out


async def averify(
    items: Sequence[VerifyItem],
    execute: Execute | None,
    *,
    unavailable: str = ERR_CONNECTION,
    timeout_sec: float = 30.0,
) -> list[dict[str, Any]]:
    """항목을 차례로 검사·실행한다 → 항목 결과(값 없음). `execute`가 None이면 실행 대상은
    `unavailable` 범주로 남긴다. 절대 예외를 올리지 않는다(항목 단위 범주)."""
    from src.utils.sql_dialect import row_limit_clause

    results: list[dict[str, Any]] = []
    for item in items:
        if item.pending:
            results.append(_record(item, error=item.pending))
            continue
        if item.error or not item.sql:
            results.append(_record(item, ok=False, error=item.error or ERR_GUARD))
            continue
        text = item.sql.strip().rstrip(";").strip()
        if not _guard_ok(text):
            results.append(_record(item, ok=False, error=ERR_GUARD))
            continue
        if execute is None:
            results.append(_record(item, ok=False, error=unavailable))
            continue
        wrapped = f"SELECT * FROM ({text}) q {row_limit_clause(ENGINE, OUTER_LIMIT)}"
        try:
            result = await asyncio.wait_for(execute(wrapped), timeout=timeout_sec)
        except Exception as exc:  # noqa: BLE001 — 항목 단위 범주로 남긴다(원문 버림)
            results.append(_record(item, ok=False, error=classify_error(exc)))
            continue
        rows = getattr(result, "rows", None) or []
        fields: dict[str, Any] = {"ok": True, "rows": row_bucket(len(rows))}
        if getattr(result, "truncated", False):
            fields["truncated"] = True
        results.append(_record(item, **fields))
    return results


async def averify_with_client(
    items: Sequence[VerifyItem],
    open_client: Callable[[], AbstractAsyncContextManager[Any]],
) -> list[dict[str, Any]]:
    """클라이언트 하나로 전부 돈다. 연결이 안 되면 실행 대상 항목을 연결 범주로 남긴다."""
    try:
        async with open_client() as client:
            return await averify(items, client.execute_sql)
    except Exception as exc:  # noqa: BLE001 — 연결 실패는 범주로 끝낸다(침묵 통과 없음)
        return await averify(items, None, unavailable=classify_error(exc))


# ──────────────────────────────────────────────
# 산출
# ──────────────────────────────────────────────


def build_document(
    *, run_id: str, results: Sequence[Mapping[str, Any]], sources: Mapping[str, Any]
) -> dict[str, Any]:
    """`asset_verification.yaml` 본문."""
    by_kind: dict[str, Counter[str]] = {}
    for item in results:
        state = "pending" if item["ok"] is None else ("ok" if item["ok"] else "error")
        by_kind.setdefault(str(item["kind"]), Counter())[state] += 1
    errors = Counter(str(i["error"]) for i in results if i["ok"] is False)
    return {
        "version": FORMAT_VERSION,
        "run_id": run_id,
        "db_id": DB_ID,
        "row_buckets": list(ROW_BUCKETS),
        "sources": dict(sources),
        "summary": {
            "total": len(results),
            "ok": sum(1 for i in results if i["ok"] is True),
            "error": sum(1 for i in results if i["ok"] is False),
            "pending": sum(1 for i in results if i["ok"] is None),
            "by_kind": {k: dict(sorted(v.items())) for k, v in sorted(by_kind.items())},
            "by_error": dict(sorted(errors.items())),
        },
        "items": [dict(i) for i in results],
    }


def stage_files(run_meta: Mapping[str, Any], document: Mapping[str, Any]) -> dict[str, str]:
    """메모리의 산출물 3종(관문 전) — `run.json` · `asset_verification.yaml` · `report.md`."""
    import json

    from .report import render_verification_report

    return {
        "run.json": json.dumps(run_meta, ensure_ascii=False, indent=2) + "\n",
        VERIFICATION_FILE: yaml.safe_dump(dict(document), allow_unicode=True, sort_keys=False),
        "report.md": render_verification_report(run_meta, document),
    }


def run_verify(
    *,
    policy: Any,
    env: str,
    provenance: Mapping[str, Any],
    user_values: Mapping[str, str | None],
    dsn: str | None,
    cfg: Any = None,
    repo_root: Path = REPO_ROOT,
    results_root: Path = RESULTS_ROOT,
    open_client: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
    say: Callable[[str], None] = print,
) -> int:
    """검증 모드 본체 → 종료 코드(0 전부 성공·보류 · 1 오류 항목·관문 실패·중단)."""
    from scripts.scenario.oracle import _open_client, _run_coroutine

    if cfg is None:
        from src.config import load_config

        cfg = load_config()
    if getattr(cfg, "db_backend", None) == "direct":
        say("[중단] DB_BACKEND=direct — 검증은 MCP readonly 경로에서만 돈다(D-003 · plans/122 G-9)")
        return 1
    items, sources = collect_items(repo_root)
    if not items:
        say("[중단] 검증할 자산 SQL이 없다(K2 예시·K4 섹션 SQL·K8 템플릿 0건)")
        return 1
    started = datetime.now(KST).replace(microsecond=0)
    run_id = f"verify-{started.strftime('%Y%m%d-%H%M%S')}"
    opener = open_client or (lambda: _open_client(cfg, DB_ID))
    results = _run_coroutine(lambda: averify_with_client(items, opener))
    document = build_document(run_id=run_id, results=results, sources=sources)
    out_dir = results_root / run_id
    run_meta = {
        "run_id": run_id,
        "mode": "verify_assets",
        "started_at": started.isoformat(),
        "finished_at": datetime.now(KST).replace(microsecond=0).isoformat(),
        "git": dict(provenance),
        "env": env,
        "db_backend": getattr(cfg, "db_backend", None),
        "mcp_endpoint": rd.dsn_scheme(
            str(getattr(getattr(cfg, "dbhub", None), "server_url", "") or "")
        ),
        "itam_dsn": rd.dsn_scheme(dsn),
        "operator": rd.mask_identifier(getpass.getuser()),
        "host": rd.mask_identifier(socket.gethostname()),
        "policy_scope": getattr(policy, "scope", None),
        "files": ["run.json", VERIFICATION_FILE, "report.md", "leak_check.json"],
        "summary": document["summary"],
        "results_dir": rd.display_path(out_dir, repo_root=repo_root, home=Path.home()),
    }
    gate = rd.LeakGate(
        policy=policy, vault=rd.PiiVault.from_policy(policy), user_values=user_values
    )
    ok, violations = rd.write_gated(out_dir, stage_files(run_meta, document), gate)
    summary = document["summary"]
    say(
        f"[verify-assets] 항목 {summary['total']} · 성공 {summary['ok']} · 오류 {summary['error']} "
        f"· 보류 {summary['pending']} · 오류 범주 {summary['by_error'] or '{}'}"
    )
    say(f"[verify-assets] 산출물 {'기록' if ok else '미기록(누출 관문 실패)'} — "
        f"{run_meta['results_dir']}")
    if not ok:
        for violation in violations[:20]:
            say(f"  - {violation['file']} · {violation['field']} · {violation['rule']}")
        return 1
    return 1 if summary["error"] else 0
