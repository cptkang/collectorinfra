"""안전 요약 `summary_safe.json` · run 메타 `run_meta.json` — 화이트리스트 직렬화 (plans/164 W2a).

1회차 E2 run 은 누출 관문이 실패해 근거가 0이었다(§1). 관문은 원값 대조 장치라 새 어휘를 막지
못하므로, **허용 키·허용 값 어휘로만 만든** 요약을 관문 성패와 무관하게 남긴다(§4.2(a) · G-1).

- 잎은 정수·실수·불·null, 또는 닫힌 어휘 문자열뿐이다 — 레지스트리 보기·인자·소스·도구·locus 형식
  (`hygiene.CodeVocab`) · 로드된 시나리오 id·턴 · 판정기 축·분류·사유 · 게이트웨이 오류 코드
  (`record.GATEWAY_ERROR_CODES` 등) · 건너뜀 사유 정규식 화이트리스트(`skip_code`) · ISO 시각·SHA·
  버전 숫자. 어휘 밖 문자열은 버리고 `dropped.<칸>`에 건수만 남긴다.
- 넣지 않는 것(경로째): 프롬프트·응답·`answer_lines`·대상 문자열·`selected`·`candidates`·인자 값·
  `reason` 문장·예외 메시지·PID·도메인 번호·인벤토리 이름.
- 쓰기 전 **이중 관문**: ① 모든 키·문자열 잎을 같은 어휘로 다시 대조(`violations`) ② `ApmLeakGate`의
  원이름·카나리아·정규식·사용자 정보 규칙. 하나라도 걸리면 파일을 쓰지 않는다.
  계획서(§4.2(a))는 ②에 토큰 대조(`TOKEN_FILES` 추가)를 적었지만, W1 에서 토큰 대조가 저장소 코드
  어휘와 거짓 양성을 내 닫힌 어휘 대조로 대신한 것과 같이 ①로 대신한다 — 모든 잎이 코드·레지스트리·
  시나리오 출처라 토큰 대조보다 엄격하다.
- 콘솔·실행 로그의 run 끝 요약 줄은 이 dict 에서만 만든다(`render_console`).

`run_meta.json`(`run_meta_doc`)은 run 디렉터리 관리 메타다 — 관문을 거친 산출물 `run.json`(드라이버
메타·판정 수치)과 **다른 파일**이다. 시작 시 `running`으로 쓰고 끝(예외 포함)에 갱신하며, 다음 Wave
(`--status`·`--audit-digest <run_id>`·`--compare`·`--replay`·`--regate`)가 인자를 채우는 고정
스키마다.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .driver import (
    GUARD_ABORTS,
    HEAVY_GATE_REASON,
    INVENTORY_CALLS,
    inventory_error_summary,
    safe_abort_reason,
)
from .hygiene import (
    DROPPED_SUFFIX,
    REQUIRED_EVIDENCE,
    REQUIRED_EVIDENCE_UNJUDGED,
    SKIP_KEYS,
    CodeVocab,
)
from .judge import (
    AXES,
    CATEGORY_BY_NAME,
    STAGE_RESOLVE,
    STAGE_UNKNOWN,
    STAGE_VIEW,
    TARGET_FAIL_REASONS,
    error_stages,
    target_fail_reason,
)
from .record import ERROR_CODES, Call, Observed, PlanTask, Reselect, TargetText, TurnRecord, Verdict
from .rundir import RUN_META, SCENARIO_COPY
from .scenarios import RegistryInfo, Scenario, is_allowed_placeholder

SCHEMA = 1
SAFE_SUMMARY_FILE = "summary_safe.json"
RUN_META_FILE = RUN_META

#: 시각(ISO-8601 · 오프셋 필수 — 실행 머신 로컬 시각 `datetime.now().astimezone()`).
ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?[+-]\d{2}:\d{2}")
SHA_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
#: `__main__.make_run_id` — `YYYYmmdd-HHMMSS-<env>-<SHA 7자>`(SHA 를 못 읽으면 `unknown`) ·
#: `--smoke` run 은 끝에 `-smoke`(plans/164 W2b).
RUN_ID_RE = re.compile(r"\d{8}-\d{6}-e[12]-(?:[0-9a-f]{7}|unknown)(?:-smoke)?")
#: 뷰 서버 버전(숫자 점 표기 — `guard.parse_version`이 받는 모양).
VERSION_RE = re.compile(r"\d{1,4}(?:\.\d{1,6}){1,4}")
#: 고정 건너뜀·판정 불가 사유(`guard.schedule` · `driver` · `__main__.HEAVY_OFF` · `judge`).
FIXED_SKIPS: frozenset[str] = frozenset({
    "e2_skip", "heavy_off", "unknown_scenario", HEAVY_GATE_REASON, "no_observation",
    "turn_error",
})
_HEAVY_CAP = re.compile(r"heavy_cap:\d{1,4}")
STATUSES = ("running", "done", "failed", "aborted")
ENVS = ("e1", "e2")
LLMS = ("fake", "mlx", "body")
#: LLM 평면 provider 이름(`src/config.py` Literal · 가짜 LLM · 못 읽음).
PLANES: frozenset[str] = frozenset({"fake", "mlx", "fabrix", "ollama", "gemini", "vllm", "unknown"})
#: 사다리 단(`src.observability.ladder.LadderTier` 값) · 확정 사유 어휘 5종(같은 파일 `_REASON_*`).
TIERS: frozenset[str] = frozenset({"deep_agent", "intent_orchestration", "semantic_router",
                                   "legacy"})
LADDER_REASONS: frozenset[str] = frozenset({"none", "orchestrator_unavailable", "package_missing",
                                            "intent_flag_on", "semantic_routing_off"})
#: 누출 관문 규칙(`scripts/itam_bench/redact.py` `GATE_RULES` 사본 — 관문은 그 이름으로 보고한다).
GATE_RULES: frozenset[str] = frozenset({"canary", "pii_value", "pii_regex", "schema_form",
                                        "user_info", "substitution", "code_original"})
#: 관문 대상 파일 이름(`hygiene.OUTPUT_FILES` + 안전 요약 · 실행 로그).
GATE_FILES: frozenset[str] = frozenset({"trace.jsonl", "run.json", "probe.jsonl", "coverage.md",
                                        "report.md", SAFE_SUMMARY_FILE, "run.log"})
#: 사용자 정보 종류(`__main__.run_user_values` 키 — 값은 싣지 않는다).
USER_INFO_KINDS: frozenset[str] = frozenset({"login_id", "os_user", "host", "host_short", "home"})
QUARANTINE = ("none", "default_local", "explicit")
#: 로컬 기본 파일 출처(`~/apm_bench_local/` 자동 탐색 — plans/164 W2b) · 벤치 본체 사용자 종류.
FILE_SOURCES = ("default", "e1_default", "local_default", "explicit")
VIEW_VERSION_SOURCES = ("explicit", "bench_local")
BENCH_USERS = ("anonymous", "login_user")
#: run 메타 `kit.placeholders` 칸(`kit.KitReport.counts`).
KIT_COUNTS = ("present", "absent", "partial", "unchecked", "excluded")
STAGES = (STAGE_VIEW, STAGE_RESOLVE, STAGE_UNKNOWN)

#: 안전 요약·run 메타의 구조 키(어휘 검사 대상 — 여기 없는 키도 쓰기 전 검사에 걸린다).
STRUCT_KEYS: frozenset[str] = frozenset({
    "schema", "run_id", "mode", "env", "llm", "status", "exit_code", "started_at", "ended_at",
    "elapsed_sec", "sha", "dirty", "scenarios", "source", "sha256", "copied", "flags", "probe",
    "skip_heavy", "only", "limit", "debug_raw", "user", "term_policy", "placeholders",
    "view_server_version", "gate", "passed", "violations", "rules", "safe_summary_written",
    "quarantine", "regated_at", "regate_sha", "default", "explicit", "e1_default",
    "default_local", "none", "run",
    "tier", "degraded_reason", "directional", "planes", "worker", "orchestrator", "turns",
    "apm_observed_turns", "aborted", "gateway_var_absent", "probe_calls", "probe_rows",
    "run_invalid", "evidence_missing", "calls", "by_source", "by_source_other", "total_calls",
    "tool_calls", "timeouts", "quota_hits", "baseline_ms", "inventory", "instances",
    "error_codes", "error_exceptions", "error_other", "summary", "counts", "records", "skipped",
    "unjudged", "normal", "clarify", "refusal", "turn", "pass", "total", "accuracy", "fail", "na",
    "axes", "by_split", "by_cov", "categories", "primary_failure_categories",
    "primary_categories", "error_calls", "tool", "code", "stage", "count", "resolve_miss",
    "target_fail_reasons", "locus_top", "unexpected", "failed", "id", "split", "turn_pass",
    "locus", "rows_zero", "target_reason", "clarify_kind", "user_info", "path", "items", "file",
    "record", "field", "rule", "user_info_replaced", "dropped", "kind", "regate", "skip_other",
    "gateway_var", "preexisting", "created", "new", "changed",
})
#: 관문 위반 필드 경로 구간 어휘 — 레코드 계약 키 · 근거 묶음 · 코드 어휘 칸 · run 메타 키.
#: 밖의 구간은 `*`로 바꾼다(키에 LLM 이 낸 인자 이름이 올 수 있다).
_RECORD_KEYS: frozenset[str] = frozenset(
    k for cls in (TurnRecord, Observed, Verdict, Call, PlanTask, Reselect, TargetText)
    for k in cls.__annotations__
)
_EVIDENCE_KEYS: frozenset[str] = frozenset(
    seg for p in (*REQUIRED_EVIDENCE, *REQUIRED_EVIDENCE_UNJUDGED) for seg in p.split(".")
) | {"level", "evidence"}
_RUN_JSON_KEYS: frozenset[str] = frozenset({
    "run_id", "sha", "dirty", "env", "started_at", "finished_at", "view_server_version",
    "profile", "llm", "gateway_settings", "schedule", "inventory", "auth", "planes", "tier",
    "degraded_reason", "guard", "aborted", "abort_reason", "budget", "fingerprints",
    "apm_section", "counts", "turn_accuracy", "run_invalid", "probe_rows", "evidence_missing",
    "user_info_replaced", "skip_heavy", "turns", "apm_observed_turns", "gateway_var_absent",
    "elapsed_sec", "probe_calls", "fake_llm_requests", "name", "names", "errors",
    "gateway_var", "preexisting", "created", "new", "changed",
})
_FIELD_SEG = re.compile(r"([^.\[\]]+)((?:\[\d+\])*)")
_INDEX = re.compile(r"#\d+")


@dataclass(frozen=True)
class SafeVocab:
    """안전 요약 어휘 — 칸별 생성 검사와 쓰기 전 사후 검사(`violations`)가 같이 쓴다.

    Attributes:
        code: 코드 어휘(레지스트리 보기·인자·소스·도구·locus · 판정기 분류·축)
        scenario_ids: 로드된 시나리오 id
        turn_ids: `<시나리오 id>#<턴>`
        splits: 시나리오 split
        covs: 표 A 경로 id(`by_cov` 키 · `was_once:<id>`)
        fields: 관문 위반 필드 경로 구간 어휘(`field_path`)
        literals: 허용 문자열 전부(사후 검사용 — 형식 검사 대상은 `ok`가 따로 본다)
    """

    code: CodeVocab
    scenario_ids: frozenset[str]
    turn_ids: frozenset[str]
    splits: frozenset[str]
    covs: frozenset[str]
    fields: frozenset[str]
    literals: frozenset[str]

    def ok(self, text: str) -> bool:
        """문자열 하나가 어휘 안인가(키·값 공통)."""
        if text in self.literals:
            return True
        if any(p.fullmatch(text) for p in (ISO_RE, SHA_RE, SHA256_RE, RUN_ID_RE, VERSION_RE)):
            return True
        if self.code.allows("locus", text):
            return True
        if skip_code(text, self) == text:
            return True
        return self.field_ok(text)

    def field_ok(self, path: str) -> bool:
        """관문 위반 필드 경로가 `field_path` 모양(구간 어휘 · `#N` · `*` · `[N]`)인가."""
        if not path or len(path) > 300:
            return False
        for part in path.split("."):
            match = _FIELD_SEG.fullmatch(part)
            if match is None:
                return False
            seg = match.group(1)
            if not (seg == "*" or _INDEX.fullmatch(seg) or seg in self.fields):
                return False
        return True


def build_vocab(scenarios: Sequence[Scenario], code: CodeVocab) -> SafeVocab:
    """로드된 시나리오 · 코드 어휘 → 안전 요약 어휘."""
    ids = frozenset(s.id for s in scenarios)
    turn_ids = frozenset(f"{s.id}#{i}" for s in scenarios for i in range(len(s.turns)))
    splits = frozenset(s.split for s in scenarios)
    covs = frozenset(c for s in scenarios for c in s.cov_paths)
    inv_tools = {tool for tool, _, _ in INVENTORY_CALLS}
    words = frozenset(w for group in code.words.values() for w in group)
    literals = (
        STRUCT_KEYS | ids | turn_ids | splits | covs | words | code.views | code.sources
        | code.loci | frozenset(f"sources:{s}" for s in code.sources)
        | ERROR_CODES | frozenset(STAGES) | frozenset(TARGET_FAIL_REASONS) | frozenset(AXES)
        | frozenset(CATEGORY_BY_NAME) | frozenset(STATUSES) | frozenset(ENVS) | frozenset(LLMS)
        | PLANES | TIERS | LADDER_REASONS | GATE_RULES | GATE_FILES | USER_INFO_KINDS
        | frozenset(QUARANTINE) | FIXED_SKIPS | GUARD_ABORTS | {"budget:other", "other"}
        | frozenset(f"budget:{s}" for s in code.sources) | {"refusal"}
        | frozenset(f"clarify:{k}" for k in code.words.get("clarify_kind", ()))
        | frozenset(f"{t}:{c}" for t in inv_tools for c in ERROR_CODES) | inv_tools
        | frozenset(REQUIRED_EVIDENCE) | frozenset(REQUIRED_EVIDENCE_UNJUDGED)
        | frozenset(f"{k}{DROPPED_SUFFIX}" for k in _DROP_NAMES)
    )
    fields = (_FIELD_KEYS | code.views | frozenset(CATEGORY_BY_NAME)
              | frozenset(a for args in code.view_args.values() for a in args))
    return SafeVocab(code=code, scenario_ids=ids, turn_ids=turn_ids, splits=splits, covs=covs,
                     fields=fields, literals=frozenset(literals))


#: `dropped` 칸 이름(`<이름>_dropped` — 버린 건수).
_DROP_NAMES: tuple[str, ...] = (
    "turns", "split", "categories", "locus", "tools", "codes", "clarify_kind", "target_reason",
    "unexpected", "sources", "tier", "degraded_reason", "planes", "aborted",
    "view_server_version", "by_split", "by_cov", "evidence", "gate_items", "user_info_kinds",
)


def skip_code(text: Any, vocab: SafeVocab) -> str | None:
    """건너뜀·판정 불가 사유 → 정규식 화이트리스트 통과 값(아니면 None).

    고정 `e2_skip`·`heavy_off`·`unknown_scenario`·`no_observation`·`heavy_gate:<고정 문구>` ·
    `aborted:(quota_429|timeout_ratio|latency_3x|budget:<레지스트리 소스>)` · `was_once:<표 A 경로·
    시나리오 id>` · `heavy_cap:<수>` · `placeholder_missing:<허용 자리표 이름>` · `turn_error:<자유
    문장>`은 접두 `turn_error`만.
    """
    if not isinstance(text, str):
        return None
    if text in FIXED_SKIPS or _HEAVY_CAP.fullmatch(text):
        return text
    head, _, rest = text.partition(":")
    if head == "turn_error":
        return "turn_error"
    if head == "aborted":
        return text if safe_abort_reason(rest, vocab.code.sources) == rest else None
    if head == "was_once":
        return text if rest in vocab.covs or rest in vocab.scenario_ids else None
    if head == "placeholder_missing":
        return text if is_allowed_placeholder(rest) else None
    return None


_FIELD_KEYS: frozenset[str] = (
    _RECORD_KEYS | _EVIDENCE_KEYS | _RUN_JSON_KEYS | SKIP_KEYS
    | frozenset(f"{k}{DROPPED_SUFFIX}" for k in SKIP_KEYS) | frozenset(AXES)
)


def field_path(path: Any, vocab: SafeVocab) -> str | None:
    """관문 위반 필드 경로 → 구간 어휘(`SafeVocab.fields`) 밖 구간은 `*`로 바꾼 경로."""
    if not isinstance(path, str) or not path:
        return None
    out: list[str] = []
    for part in path.split("."):
        match = _FIELD_SEG.fullmatch(part)
        if match is None:
            out.append("*")
            continue
        seg, index = match.group(1), match.group(2)
        out.append((seg if seg in vocab.fields or _INDEX.fullmatch(seg) else "*") + index)
    text = ".".join(out)
    return text if vocab.field_ok(text) else None


# --- 안전 요약 ----------------------------------------------------------------------------


class _Drops:
    """버린 건수 — `dropped` 칸(이름은 `_DROP_NAMES` 중 하나 + 접미사)."""

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()

    def keep(self, name: str, value: Any, allowed: Any) -> Any:
        """문자열 `value`가 `allowed`(집합 · 술어)에 있으면 그대로, 아니면 None(건수 +1).

        문자열이 아닌 값(dict·list 등 — 해시 불가)은 대조하지 않고 버린다(V-5).
        """
        if value is None:
            return None
        if isinstance(value, str) and (allowed(value) if callable(allowed)
                                       else value in allowed):
            return value
        self.counts[f"{name}{DROPPED_SUFFIX}"] += 1
        return None

    def keep_all(self, name: str, values: Any, allowed: Any) -> list[str]:
        """목록 원소를 거른다(목록이 아니면 통째로 버린다 — 문자열을 글자로 돌지 않게)."""
        if not isinstance(values, (list, tuple)):
            if values:
                self.counts[f"{name}{DROPPED_SUFFIX}"] += 1
            return []
        return [v for v in (self.keep(name, x, allowed) for x in values) if v is not None]

    def keys(self, name: str, table: Mapping[Any, Any], allowed: Any) -> dict[str, Any]:
        """사전 키를 거른다(값은 수만 — 수가 아니면 버린다 · 사전이 아니면 통째로 버린다)."""
        out: dict[str, Any] = {}
        if not isinstance(table, Mapping):
            if table:
                self.counts[f"{name}{DROPPED_SUFFIX}"] += 1
            return out
        for key, value in table.items():
            if self.keep(name, key, allowed) is not None and _number(value):
                out[str(key)] = value
        return out


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any) -> bool:
    return value is None or (isinstance(value, (int, float)) and not isinstance(value, bool))


def _num(value: Any) -> int | float | None:
    return value if _number(value) else None


def _table(row: Any) -> dict[str, Any]:
    """`{pass, total, accuracy}`·축 표 한 행 — 수만."""
    if not isinstance(row, Mapping):
        return {}
    return {k: _num(row.get(k)) for k in ("pass", "fail", "na", "total", "accuracy") if k in row}


def _summary_part(summary: Mapping[str, Any], vocab: SafeVocab, drops: _Drops,
                  records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """`judge.summarize` 결과 → 수와 어휘 키만."""
    cats = frozenset(CATEGORY_BY_NAME)
    axes = {a: _table(r) for a, r in (summary.get("axes") or {}).items() if a in AXES}
    by_split = {}
    for split, row in (summary.get("by_split") or {}).items():
        if drops.keep("by_split", split, vocab.splits) is not None:
            by_split[split] = {"turn": _table(row.get("turn")),
                               "axes": {a: _table(r) for a, r in (row.get("axes") or {}).items()
                                        if a in AXES}}
    by_cov = {}
    for cov, row in (summary.get("by_cov") or {}).items():
        if drops.keep("by_cov", cov, vocab.covs) is not None:
            by_cov[cov] = _table(row)
    error_calls = []
    for item in summary.get("error_calls") or []:
        tool = drops.keep("tools", item.get("tool"), vocab.code.words.get("tool", frozenset()))
        code = drops.keep("codes", item.get("code"), ERROR_CODES)
        stage = item.get("stage") if item.get("stage") in STAGES else None
        if tool and code and stage:
            error_calls.append({"tool": tool, "code": code, "stage": stage,
                                "count": _num(item.get("count"))})
    locus: Counter[str] = Counter()
    for r in records:
        verdict = r.get("verdict") or {}
        if verdict.get("turn_pass") or verdict.get("skipped"):
            continue
        locus.update(drops.keep_all("locus", verdict.get("locus") or [],
                                    lambda s: vocab.code.allows("locus", s)))

    def reasons(key: str) -> dict[str, int]:
        out: Counter[str] = Counter()
        for item in summary.get(key) or []:
            code = skip_code(item.get("reason"), vocab)
            if code is None:
                drops.counts["skip_other"] += 1
            else:
                out[code] += 1
        return dict(out.most_common())

    def side(key: str) -> dict[str, Any]:
        row = summary.get(key) or {}
        return {**_table(row),
                "failed": drops.keep_all("turns", row.get("failed") or [], vocab.turn_ids)}

    unexpected = Counter(drops.keep_all(
        "unexpected", [u.get("kind") for u in summary.get("unexpected") or []], vocab.ok))
    return {
        "counts": {k: _num(v) for k, v in (summary.get("counts") or {}).items()
                   if k in STRUCT_KEYS},
        "run_invalid": bool(summary.get("run_invalid")),
        "turn": _table(summary.get("turn")),
        "axes": axes,
        "by_split": by_split,
        "by_cov": by_cov,
        "categories": drops.keys("categories", summary.get("categories") or {}, cats),
        "primary_failure_categories": drops.keys(
            "categories", summary.get("primary_failure_categories") or {}, cats),
        "primary_categories": drops.keys("categories", summary.get("primary_categories") or {},
                                         cats),
        "error_calls": error_calls,
        "resolve_miss": {k: _num((summary.get("resolve_miss") or {}).get(k))
                         for k in ("turns", "calls")},
        "target_fail_reasons": drops.keys("target_reason",
                                          summary.get("target_fail_reasons") or {},
                                          frozenset(TARGET_FAIL_REASONS)),
        "locus_top": dict(locus.most_common(10)),
        "skipped": reasons("skipped"),
        "unjudged": reasons("unjudged"),
        "clarify": side("clarify"),
        "refusal": side("refusal"),
        "unexpected": dict(unexpected.most_common()),
    }


def _turn_row(record: Mapping[str, Any], vocab: SafeVocab, drops: _Drops,
              registry: RegistryInfo | None, user_info: int | None) -> dict[str, Any] | None:
    """턴 레코드 하나 → 어휘만 남긴 행(시나리오 id·턴이 로드된 시나리오 밖이면 None)."""
    ref = f"{record.get('scenario_id')}#{record.get('turn')}"
    if ref not in vocab.turn_ids:
        drops.counts[f"turns{DROPPED_SUFFIX}"] += 1
        return None
    verdict: Mapping[str, Any] = record.get("verdict") or {}
    observed: Mapping[str, Any] = record.get("observed") or {}
    detail: Mapping[str, Any] = verdict.get("axis_detail") or {}
    tools = vocab.code.words.get("tool", frozenset())
    stages = iter(error_stages(observed, registry))
    calls = []
    for call in observed.get("calls") or []:
        if not isinstance(call, Mapping):
            continue
        error = call.get("error")
        stage = next(stages, {}).get("stage") if error else None
        rows = call.get("rows")
        calls.append({
            "tool": drops.keep("tools", call.get("tool"), tools),
            "code": drops.keep("codes", error, ERROR_CODES) if error else None,
            "stage": stage if stage in STAGES else None,
            "rows_zero": (rows == 0) if isinstance(rows, int) and not isinstance(rows, bool)
            else None,
        })
    axes = {a: s for a, s in (verdict.get("axes") or {}).items()
            if a in AXES and s in vocab.code.words.get("axis_status", frozenset())}
    target_reason = (target_fail_reason(detail.get("target") or {})
                     if axes.get("target") == "fail" else None)
    unexpected = detail.get("unexpected")
    skipped, unjudged = verdict.get("skipped"), detail.get("unjudged")
    row: dict[str, Any] = {
        "id": ref,
        "split": drops.keep("split", record.get("split"), vocab.splits),
        "turn_pass": bool(verdict.get("turn_pass")),
        "axes": axes,
        "categories": drops.keep_all("categories", verdict.get("categories") or [],
                                     frozenset(CATEGORY_BY_NAME)),
        "locus": drops.keep_all("locus", verdict.get("locus") or [],
                                lambda s: vocab.code.allows("locus", s)),
        "calls": calls,
        "target_reason": drops.keep("target_reason", target_reason,
                                    frozenset(TARGET_FAIL_REASONS)),
        "clarify_kind": drops.keep("clarify_kind", observed.get("clarify_kind"),
                                   vocab.code.words.get("clarify_kind", frozenset())),
        "unexpected": drops.keep("unexpected", unexpected, vocab.ok) if unexpected else None,
        "skipped": skip_code(skipped, vocab) if skipped else None,
        "unjudged": skip_code(unjudged, vocab) if unjudged else None,
        "user_info": user_info,
    }
    for key, raw in (("skipped", skipped), ("unjudged", unjudged)):
        if raw and row[key] is None:
            drops.counts["skip_other"] += 1
    return row


def _gateway_var(value: Any) -> dict[str, Any] | None:
    """`kit.var_changes` → 이번 run 의 var 생성·변경(시작 전 존재 · 디렉터리 생성 여부 · 새 파일 ·
    바뀐 파일 수 — 이름 없음). 없거나 모양이 다르면 None."""
    if not isinstance(value, Mapping) or not value:
        return None

    def count(key: str) -> int | None:
        n = value.get(key)
        return n if isinstance(n, int) and not isinstance(n, bool) else None

    return {"preexisting": value.get("preexisting") is True,
            "created": value.get("created") is True,
            "new": count("new"), "changed": count("changed")}


def build_safe_summary(
    records: Sequence[Mapping[str, Any]],
    meta: Mapping[str, Any],
    *,
    run_id: str,
    env: str,
    summary: Mapping[str, Any],
    vocab: SafeVocab,
    registry: RegistryInfo | None,
    directional: bool,
    gate_passed: bool | None,
    violations: Sequence[Mapping[str, Any]],
    user_info_counts: Mapping[str, int] | None = None,
    user_info_by_turn: Mapping[str, int] | None = None,
    evidence_missing: Sequence[tuple[str, int, str]] = (),
    probe_rows: int = 0,
) -> dict[str, Any]:
    """판정된 레코드(치환 전도 된다 — 어휘만 남긴다) · 드라이버 메타 · 집계 → 안전 요약 dict.

    Args:
        records: 판정이 붙은 턴 레코드(치환 전 원본 — 문자열은 어휘 대조로만 싣는다)
        meta: 드라이버 run 메타(인벤토리 이름 · PID 칸은 읽지 않는다)
        run_id · env: run 식별
        summary: `judge.summarize(records)`
        vocab: `build_vocab` 결과
        registry: 오류 호출 단계 판별용(`judge.error_stages`)
        directional: 방향 확인용 run(E1·MLX — `loop.directional_reasons`)
        gate_passed · violations: 산출물 누출 관문 결과(None = 관문 전 중단)
        user_info_counts: 사용자 정보 치환 종류별 건수(run 전체)
        user_info_by_turn: `<id>#<턴>` → 치환 건수
        evidence_missing: `hygiene.check_evidence` 결과(시나리오 id · 턴 · 칸 경로)
        probe_rows: P층 행 수
    """
    drops = _Drops()
    guard: Mapping[str, Any] = meta.get("guard") or {}
    sources = vocab.code.sources
    by_source = {s: _num(n) for s, n in (guard.get("calls_by_source") or {}).items()
                 if s in sources}
    other_calls = sum(int(n) for s, n in (guard.get("calls_by_source") or {}).items()
                      if s not in sources and _number(n) and n is not None)
    inventory: Mapping[str, Any] = meta.get("inventory") or {}
    inv_errors = inventory_error_summary(inventory.get("errors") or [])
    planes = _mapping(meta.get("planes"))
    version = meta.get("view_server_version")
    turns = [_turn_row(r, vocab, drops, registry,
                       (user_info_by_turn or {}).get(f"{r.get('scenario_id')}#{r.get('turn')}"))
             for r in records]
    missing = []
    for sid, turn, path in evidence_missing:
        ref = f"{sid}#{turn}"
        if ref in vocab.turn_ids and (path in REQUIRED_EVIDENCE
                                      or path in REQUIRED_EVIDENCE_UNJUDGED):
            missing.append({"turn": ref, "path": path})
        else:
            drops.counts[f"evidence{DROPPED_SUFFIX}"] += 1
    items = []
    for v in violations:
        file = v.get("file")
        items.append({
            "file": file if file in GATE_FILES else None,
            "record": v.get("record") if isinstance(v.get("record"), int) else None,
            "field": field_path(v.get("field"), vocab),
            "rule": v.get("rule") if v.get("rule") in GATE_RULES else None,
        })
    rules = Counter(str(v.get("rule")) for v in violations if v.get("rule") in GATE_RULES)
    users = drops.keys("user_info_kinds", dict(user_info_counts or {}), USER_INFO_KINDS)
    aborted = safe_abort_reason(meta.get("aborted"), sources)
    doc: dict[str, Any] = {
        "schema": SCHEMA,
        "run_id": run_id if RUN_ID_RE.fullmatch(run_id) else None,
        "env": env if env in ENVS else None,
        "run": {
            "tier": drops.keep("tier", meta.get("tier"), TIERS),
            "degraded_reason": drops.keep("degraded_reason", meta.get("degraded_reason"),
                                          LADDER_REASONS),
            "directional": bool(directional),
            "planes": {k: drops.keep("planes", planes.get(k), PLANES)
                       for k in ("worker", "orchestrator")},
            "turns": _num(meta.get("turns")),
            "apm_observed_turns": _num(meta.get("apm_observed_turns")),
            "aborted": aborted,
            "elapsed_sec": _num(meta.get("elapsed_sec")),
            "view_server_version": drops.keep(
                "view_server_version", version,
                lambda s: VERSION_RE.fullmatch(s) is not None) if version and env == "e2"
            else None,
            # 하위 호환 — 「run 끝 시점에 apm_gateway/var 가 없었다」(존재 여부뿐 · 이번 run 이
            # 만들었는지는 모른다). 이번 run 의 생성·변경은 `gateway_var`(전후 비교 · W4-1)를 본다
            "gateway_var_absent": meta.get("gateway_var_absent")
            if isinstance(meta.get("gateway_var_absent"), bool) else None,
            "gateway_var": _gateway_var(meta.get("gateway_var")),
            "probe_calls": _num(meta.get("probe_calls", 0)),
            "probe_rows": probe_rows,
            "run_invalid": bool(summary.get("run_invalid")),
            "skip_heavy": bool(meta.get("skip_heavy")),
            "evidence_missing": len(evidence_missing),
        },
        "calls": {
            "by_source": by_source,
            "by_source_other": other_calls,
            **{k: _num(guard.get(k)) for k in ("total_calls", "tool_calls", "timeouts",
                                               "quota_hits", "baseline_ms")},
        },
        "inventory": {
            "calls": _num(inventory.get("calls")),
            "instances": _num(inventory.get("names")),
            "error_codes": inv_errors["codes"],
            "error_exceptions": inv_errors["exceptions"],
            "error_other": inv_errors["other"],
        },
        "summary": _summary_part(summary, vocab, drops, records),
        "turns": [t for t in turns if t is not None],
        "evidence_missing": missing,
        "gate": {"passed": gate_passed, "violations": len(violations),
                 "rules": dict(sorted(rules.items())), "items": items},
        "user_info_replaced": users,
        "dropped": dict(sorted(drops.counts.items())),
    }
    return doc


def violations(obj: Any, vocab: SafeVocab) -> int:
    """어휘 밖 문자열(키 포함)·허용 밖 잎 형태 수 — 값은 돌려주지 않는다(그 자체가 유출원일 수
    있다)."""
    if isinstance(obj, str):
        return int(not vocab.ok(obj))
    if isinstance(obj, Mapping):
        return sum(violations(k, vocab) + violations(v, vocab) for k, v in obj.items())
    if isinstance(obj, (list, tuple)):
        return sum(violations(v, vocab) for v in obj)
    return int(not (obj is None or isinstance(obj, (bool, int, float))))


def check_safe_summary(doc: Mapping[str, Any], vocab: SafeVocab,
                       gate: Any) -> tuple[str | None, dict[str, int]]:
    """이중 관문 — (기록할 본문 · 통과 못 하면 None, 규칙별 위반 건수).

    ① 직렬화 → 다시 읽은 dict 의 키·문자열 잎 어휘 대조(`closed_vocab`) ② 관문(`gate.check`)의
    원이름·카나리아·정규식·사용자 정보 규칙. 위반 위치·값은 돌려주지 않는다(건수만).

    ②는 이 파일 내용에 대한 위반만 센다 — 관문의 파일 무관 위반(`file` 없음 · 가짜 값 충돌 등)은
    1차 관문(산출물)이 이미 판정했고, 여기서 세면 안전 요약이 늘 막힌다(V-4).
    """
    text = json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
    found: Counter[str] = Counter()
    closed = violations(json.loads(text), vocab)
    if closed:
        found["closed_vocab"] = closed
    if gate is not None:
        found.update(str(v.get("rule")) for v in gate.check({SAFE_SUMMARY_FILE: text})
                     if v.get("file") == SAFE_SUMMARY_FILE)
    return (None if found else text), dict(sorted(found.items()))


def _pct(value: Any) -> str:
    return "—" if not isinstance(value, (int, float)) else f"{value * 100:.1f}%"


def _top(table: Mapping[str, Any], n: int = 10) -> str:
    items = list(table.items())[:n]
    return ", ".join(f"{k} {v}" for k, v in items) if items else "없음"


def render_console(doc: Mapping[str, Any], *, directional_head: str) -> list[str]:
    """run 끝 요약 줄 — 안전 요약 dict 에서만 만든다(실행 로그 = 콘솔 · plans/164 §4.2(a)·§4.3-5).

    실패 턴 대표 분류를 먼저, 종전 대표 분류는 「통과 턴 포함」 표지와 함께 둔다.
    """
    run, calls, inv, summ = doc["run"], doc["calls"], doc["inventory"], doc["summary"]
    lines: list[str] = []
    if run.get("directional"):
        lines.append(f"[{directional_head}] env {doc.get('env')} · 평면 {run.get('planes')}")
    lines.append(f"사다리     : {run.get('tier')} (사유 {run.get('degraded_reason')})")
    lines.append(f"턴         : 실행 {run.get('turns')} · APM 관측 {run.get('apm_observed_turns')}"
                 f" · 건너뜀 {summ['skipped'] or '없음'} · 판정 불가 {summ['unjudged'] or '없음'}")
    inv_errors = {**inv["error_codes"], **{f"{t}:예외": n for t, n in
                                           inv["error_exceptions"].items()}}
    lines.append(
        f"호출       : 소스별 {calls['by_source']}"
        + (f" (그 밖 {calls['by_source_other']})" if calls.get("by_source_other") else "")
        + f" · 타임아웃 {calls.get('timeouts')} · 429 {calls.get('quota_hits')} · 기준선 "
        f"{calls.get('baseline_ms')}ms · 인벤토리 도구 {inv.get('calls')}회 · 인스턴스 "
        f"{inv.get('instances')}" + (f" · 인벤토리 실패 {inv_errors}" if inv_errors else "")
        + (f" · 그 밖 실패 {inv['error_other']}" if inv.get("error_other") else "")
        + f" · P층 {run.get('probe_calls')}회")
    lines.append(f"중단 사유  : {run.get('aborted') or '없음'} · 소요 {run.get('elapsed_sec')}초 · "
                 f"뷰 서버 {run.get('view_server_version') or '해당 없음'}")
    turn = summ["turn"]
    split_text = " · ".join(f"{k} {_pct(v['turn'].get('accuracy'))} "
                            f"({v['turn'].get('pass')}/{v['turn'].get('total')})"
                            for k, v in summ["by_split"].items())
    lines.append(f"턴 정확도  : {_pct(turn.get('accuracy'))} ({turn.get('pass')}/"
                 f"{turn.get('total')})" + (f" · {split_text}" if split_text else ""))
    lines.append("축         : " + " · ".join(f"{a} {_pct(r.get('accuracy'))}"
                                             for a, r in summ["axes"].items()))
    lines.append(f"대표 분류  : 실패 턴 {_top(summ['primary_failure_categories'], 99)}")
    lines.append(f"             (통과 턴 포함) {_top(summ['primary_categories'], 99)}")
    errs = [f"{e['tool']}·{e['code']}·{e['stage']} {e['count']}"
            for e in summ["error_calls"][:10]]
    lines.append("오류 호출  : " + (", ".join(errs) if errs else "없음")
                 + f" · resolve_miss 턴 {summ['resolve_miss'].get('turns')} · 호출 "
                 f"{summ['resolve_miss'].get('calls')}")
    lines.append(f"target 사유: {_top(summ['target_fail_reasons'])}")
    lines.append(f"locus 상위 : {_top(summ['locus_top'])}")
    missing = doc["evidence_missing"]
    lines.append(f"근거 누락  : {run.get('evidence_missing')}"
                 + (" — " + ", ".join(f"{m['turn']} {m['path']}" for m in missing[:5])
                    if missing else ""))
    replaced = {k: n for k, n in (doc.get("user_info_replaced") or {}).items() if n}
    if replaced:
        lines.append(f"사용자 정보: 치환 {replaced}")
    if doc.get("dropped"):
        lines.append(f"어휘 밖    : 버림 {doc['dropped']}")
    return lines


# --- run 메타(`run_meta.json`) ---------------------------------------------------------------


def _enum(value: Any, allowed: Iterable[str]) -> str | None:
    return value if isinstance(value, str) and value in set(allowed) else None


def _pattern(value: Any, pattern: re.Pattern[str]) -> str | None:
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def run_meta_doc(raw: Mapping[str, Any], *, scenario_ids: Iterable[str]) -> dict[str, Any]:
    """run 메타 → 고정 스키마(어휘 밖 잎은 null · 목록은 원소째 버림).

    문자열 잎은 전부 enum 또는 정규식(ISO 시각 · SHA 16진 · 시나리오 id · 버전 숫자)이다. 스키마
    `SCHEMA` = 1 — 칸: `run_id` · `mode` · `env` · `llm` · `status` · `exit_code` · `started_at` ·
    `ended_at` · `elapsed_sec` · `sha` · `dirty` · `scenarios{source, sha256, copied, copy}` ·
    `flags{probe, skip_heavy, only, smoke, limit, debug_raw, user, term_policy, placeholders,
    view_server_version, view_server_version_source}` · `bench_user` · `gate{passed,
    violations, rules}` · `safe_summary_written` · `quarantine` · `regated_at` · `regate_sha` ·
    `kit{checked, placeholders{present, absent, partial, excluded}, absent, partial,
    source_unmapped, source_mismatch, var{preexisting, created, new, changed}, root}`(실행 전 키트
    검사 · var 점검 · 실행 계정 — plans/164 §4.2 (d) · W4 · 자리표 이름은 허용 자리표 정규식만).
    `scenarios.copy`·`flags.smoke`·`*_source`·`bench_user`는 이후 모드(`--audit-digest` 등)가 run id
    만으로 값을 채우는 재료다(plans/164 W2b — 경로·사용자 이름은 싣지 않는다).
    """
    ids = set(scenario_ids)
    scen = _mapping(raw.get("scenarios"))
    flags = _mapping(raw.get("flags"))
    gate = _mapping(raw.get("gate"))

    def boolean(value: Any) -> bool:
        return value is True

    def integer(value: Any) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    rules = _mapping(gate.get("rules"))
    elapsed = raw.get("elapsed_sec")
    kit = _mapping(raw.get("kit"))
    counts = _mapping(kit.get("placeholders"))
    var = _mapping(kit.get("var"))

    def names(value: Any) -> list[str]:
        return ([n for n in value if isinstance(n, str) and is_allowed_placeholder(n)]
                if isinstance(value, list) else [])
    return {
        "schema": SCHEMA,
        "run_id": _pattern(raw.get("run_id"), RUN_ID_RE),
        "mode": "run",
        "env": _enum(raw.get("env"), ENVS),
        "llm": _enum(raw.get("llm"), LLMS),
        "status": _enum(raw.get("status"), STATUSES),
        "exit_code": integer(raw.get("exit_code")),
        "started_at": _pattern(raw.get("started_at"), ISO_RE),
        "ended_at": _pattern(raw.get("ended_at"), ISO_RE),
        "elapsed_sec": elapsed if isinstance(elapsed, (int, float))
        and not isinstance(elapsed, bool) else None,
        "sha": _pattern(raw.get("sha"), SHA_RE),
        "dirty": boolean(raw.get("dirty")),
        "scenarios": {"source": _enum(scen.get("source"), ("default", "explicit")),
                      "sha256": _pattern(scen.get("sha256"), SHA256_RE),
                      "copied": boolean(scen.get("copied")),
                      "copy": _enum(scen.get("copy"), (SCENARIO_COPY,))},
        "flags": {
            "probe": boolean(flags.get("probe")),
            "skip_heavy": boolean(flags.get("skip_heavy")),
            "only": ([i for i in flags["only"] if isinstance(i, str) and i in ids]
                     if isinstance(flags.get("only"), list) else None),
            "smoke": boolean(flags.get("smoke")),
            "limit": integer(flags.get("limit")),
            "debug_raw": boolean(flags.get("debug_raw")),
            "user": boolean(flags.get("user")),
            "term_policy": _enum(flags.get("term_policy"), FILE_SOURCES),
            "placeholders": _enum(flags.get("placeholders"), FILE_SOURCES),
            "view_server_version": _pattern(flags.get("view_server_version"), VERSION_RE),
            "view_server_version_source": _enum(flags.get("view_server_version_source"),
                                                VIEW_VERSION_SOURCES),
        },
        "bench_user": _enum(raw.get("bench_user"), BENCH_USERS),
        "gate": {"passed": gate.get("passed") if isinstance(gate.get("passed"), bool) else None,
                 "violations": integer(gate.get("violations")) or 0,
                 "rules": {k: v for k, v in rules.items()
                           if k in GATE_RULES and integer(v) is not None}},
        "safe_summary_written": boolean(raw.get("safe_summary_written")),
        "quarantine": _enum(raw.get("quarantine"), QUARANTINE) or "none",
        "regated_at": _pattern(raw.get("regated_at"), ISO_RE),
        "regate_sha": _pattern(raw.get("regate_sha"), SHA_RE),
        "kit": {
            "checked": boolean(kit.get("checked")),
            "placeholders": ({k: integer(counts.get(k)) for k in KIT_COUNTS}
                             if kit.get("placeholders") is not None else None),
            "absent": names(kit.get("absent")),
            "partial": names(kit.get("partial")),
            "source_unmapped": integer(kit.get("source_unmapped")),
            "source_mismatch": integer(kit.get("source_mismatch")),
            "var": ({"preexisting": boolean(var.get("preexisting")),
                     "created": boolean(var.get("created")),
                     "new": integer(var.get("new")), "changed": integer(var.get("changed"))}
                    if kit.get("var") is not None else None),
            "root": boolean(kit.get("root")),
        },
    }


def write_run_meta(run_dir: Path, raw: Mapping[str, Any], *,
                   scenario_ids: Iterable[str]) -> dict[str, Any]:
    """`run_meta.json`을 고정 스키마로 쓴다(임시 파일 → 교체) · 쓴 dict 를 돌려준다."""
    doc = run_meta_doc(raw, scenario_ids=scenario_ids)
    tmp = run_dir / f".{RUN_META_FILE}.tmp"
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(run_dir / RUN_META_FILE)
    return doc


def read_run_meta(run_dir: Path, *, scenario_ids: Iterable[str]) -> dict[str, Any] | None:
    """`run_meta.json` → 고정 스키마로 다시 거른 dict(없거나 못 읽으면 None)."""
    try:
        raw = json.loads((run_dir / RUN_META_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return run_meta_doc(raw, scenario_ids=scenario_ids) if isinstance(raw, Mapping) else None


__all__ = [
    "GATE_RULES",
    "ISO_RE",
    "RUN_ID_RE",
    "RUN_META_FILE",
    "SAFE_SUMMARY_FILE",
    "SCHEMA",
    "STATUSES",
    "SafeVocab",
    "build_safe_summary",
    "build_vocab",
    "check_safe_summary",
    "field_path",
    "read_run_meta",
    "render_console",
    "run_meta_doc",
    "skip_code",
    "violations",
    "write_run_meta",
]
