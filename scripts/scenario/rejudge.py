"""J-4 오프라인 재판정기 (plans/122 J-4 · G-17 · D-276 ③).

과거 run 의 `raw.jsonl` 행을 관측치(`Observation`)로 되살려 **현 판정기**로 다시 판정한다. 판정 계약
(카탈로그)이 바뀐 전후 run 을 같은 계약으로 맞춰 **제품 변화와 계약 변화를 가른다** - `plans/120`
§2.2 가 사본에서 손으로 한 보정을 도구로 만든 것이다. LLM·서버·DB 호출 0 이다.

    python -m scripts.scenario.rejudge <run_dir>                     # 현 작업 트리 카탈로그
    python -m scripts.scenario.rejudge <run_dir> --catalog-ref <ref> # git ref(checkout 없음)
    python -m scripts.scenario.rejudge <run_dir> --catalog-dir <dir> # 시나리오 YAML 디렉터리
    python -m scripts.scenario.rejudge <run_dir> --identity          # run 커밋 카탈로그로 항등 대조
    python -m scripts.scenario.rejudge <run_dir> --identity --catalog-ref <ref>  # 커밋 대체 ref

**되살리지 못하는 것**(행에 없다 - 차이 원인으로 분류한다):
  - 결과 행(H-1)·오라클(O) - 과거 run 은 수집하지 않았다. 해당 단언은 보류로 남는다
  - `http_status`·`status` - 옛 행에 칸이 없으면 **추정**한다
    (`_restore_http_status`·`_restore_status` 의 규칙). 새 행(칸 있음)은 그대로 쓴다
  - 역질문 선택지 원본 - 행에는 표시 이름(`clarification_options.options` · 50개 상한)만 있다.
    `db_id` 같은 다른 값·폼필 후보는 없다
  - 응답 본문 - 4,000자에서 잘렸을 수 있다(`response_truncated`)
  - `retries_partial`(하한 표지)·`hang`(대응 등급이 `hang` 이 아니면)

**칸 자체가 없는 행은 그 칸을 보는 단언을 판정하지 않는다**
(「복원 불가」 보류 · plans/122 J-4 보강 · `ABSENT_COLUMN_HOLDS`).
  - 칸이 **있는데 비어 있는** 것(빈 응답 · 빈 목록)은 관측 0 이라 판정한다.
  - 칸이 없으면 그 칸을 적재하기 전 러너가 만든 행이다 - 빈 값으로 채워 판정하면 거짓 불합격이
    난다(run `20260914-185540` 6,567행은 `response_text`·`clarification_options`·`db_ids` 칸이
    전부 없다).
  - `executed_sqls` 는 러너가 SQL 이 있을 때만 싣는 칸이라 행 하나로는 가르지 못한다.
    **run 의 어느 행에도** `executed_sqls`·`row_counts_by_db`(감사 로그 수집과 함께 생긴 칸) 칸이
    없으면 SQL 수집 이전 run 으로 보고 SQL 단언을 보류한다(`sql_collected`).
  - 보류는 `manual`(출처 `unobservable`)이고 문구는 `REJUDGE_HOLD_PREFIX` 로 시작한다.

**송신 계약 변경**(plans/122 H-6 — 턴 `auth` · 시나리오 `upload_generate`)은 비교에서 뺀다
(`send_contract_gap`). 러너는 송신 방식을 행에 싣지 않으므로 이렇게 가른다:
  1. run 메타의 판정 계약 지문(`judgement_contract.catalog_digest`)이 재판정 카탈로그 지문과
     같으면 같은 카탈로그로 보냈다 - 비교한다
  2. 아니면 행의 HTTP 상태(`_restore_http_status`)로 가른다. 새 송신 방식은 그래프 진입 전에
     거절되는 경로다 - `auth: none` 이면 401/403, `upload_generate` 면 4xx(확장자·크기 검사).
     그 상태가 아니면 옛 방식(러너 토큰 · 파일 없는 질의)으로 보낸 행으로 보고
     「송신 계약 변경 — 재판정 불가」로 분류한다(판정은 `manual` 보류 · 비교 표본 밖)
  한계: 새 방식으로 보냈는데 제품이 거절하지 않은 회귀(예 `AUTH_ENABLED=false` 로 200)는 2에서
  이 분류로 가려진다 - 그래서 제외 행은 `rejudge.md` 에 건수와 행 목록으로 싣는다(조용히 빼지
  않는다).

산출(`--out`, 기본 `<run_dir>/rejudge/<카탈로그 표지>/`):
  - `raw.jsonl`(재판정 행) · `run.json`(판정 계약 = 재판정 카탈로그 지문)
  - `rejudge_diff.jsonl`(행별 전후) · `rejudge.md`(요약)
  - `report.md`·`summary.json`(재판정 행의 J-1 리포트)
**원 run 디렉터리의 파일은 덮어쓰지 않는다.**
"""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

from . import REPO_ROOT, clarify, utf8_open
from .assertions import (
    AUTH_FAILURE_STATUSES,
    INVALID_VERDICT,
    Observation,
    Verdict,
    evaluate_turn,
    judge_digest,
    primary_manual_source,
    row_db_ids,
    row_is_invalid,
)
from .catalog import (
    DB_REGISTRY_PATH,
    PROFILES_PATH,
    SCENARIO_DIR,
    Catalog,
    CatalogError,
    Group,
    Scenario,
    Turn,
    judgement_digest,
    load_catalog,
)
from .report import (
    arm_breakdown,
    load_rows,
    load_run_meta,
    note_sources,
    row_bundle_of,
    run_catalog_digest,
    unevaluated_reason,
    write_report,
)
from .runner import BUNDLE_TURN_STRIDE, _as_member, _hold_for_env


class RejudgeError(RuntimeError):
    """재판정을 시작할 수 없다(카탈로그·run 을 찾지 못함)."""


# --- 카탈로그 선택 -----------------------------------------------------------------------

#: `git archive` 로 풀어 읽는 카탈로그 경로(저장소 루트 기준).
#: 프로파일은 카탈로그 교차 검증에 필요하다.
_CATALOG_PATHS = ("testdata/scenarios", "config/scenarios/profiles.yaml")


def _git(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", "-C", str(REPO_ROOT), *args], capture_output=True, check=False)


def resolve_commit(ref: str) -> str | None:
    """ref 를 커밋 sha 로 푼다. 로컬 저장소에 없으면 None."""
    done = _git("rev-parse", "--verify", "-q", f"{ref}^{{commit}}")
    if done.returncode != 0:
        return None
    return done.stdout.decode().strip() or None


def load_catalog_at_ref(ref: str, workdir: Path) -> tuple[Catalog, str]:
    """git ref 의 카탈로그를 **checkout 없이** 임시 디렉터리에 풀어 읽는다 → (카탈로그, sha)."""
    sha = resolve_commit(ref)
    if not sha:
        raise RejudgeError(f"ref `{ref}` 가 로컬 저장소에 없다")
    for path in _CATALOG_PATHS:
        done = _git("archive", "--format=tar", sha, "--", path)
        if done.returncode != 0:
            raise RejudgeError(f"`git archive {sha[:12]} {path}` 실패: "
                               f"{done.stderr.decode(errors='replace').strip()}")
        with tarfile.open(fileobj=io.BytesIO(done.stdout)) as archive:
            archive.extractall(workdir, filter="data")
    catalog = load_catalog(workdir / "testdata" / "scenarios",
                           workdir / "config" / "scenarios" / "profiles.yaml", DB_REGISTRY_PATH)
    return catalog, sha


# --- 관측치 복원 -------------------------------------------------------------------------

#: 대응 등급 근거 문구(`classify_mode`)의 `http_status=NNN`·`http=NNN` · 오류 문구의 `http NNN`.
_HTTP_IN_EVIDENCE = re.compile(r"\bhttp(?:_status)?=(\d{3})\b")
_HTTP_IN_ERROR = re.compile(r"^http (\d{3})\b")
_STATUS_IN_EVIDENCE = re.compile(r"\bstatus=(\w+)")
#: 전송 계층 예외(`httpx.ReadTimeout: …`) - 응답을 받기 전에 끊긴 턴이다.
_TRANSPORT_ERROR = re.compile(r"^[A-Za-z_]\w*(?:Error|Exception|Timeout)\b")


def _failed_actual(row: dict[str, Any], key: str) -> Any:
    """원 판정기가 그 단언에서 본 **관측값**(`failed_assertions[].actual`). 없으면 None."""
    return next((f.get("actual") for f in row.get("failed_assertions") or []
                 if f.get("key") == key), None)


def _restore_http_status(row: dict[str, Any]) -> tuple[int, bool]:
    """(http_status, 추정 여부). 칸 → 원 실패 단언의 관측값 → 대응 등급 근거 → 오류 문구 → 추정 순.

    추정 규칙: 전송 예외 문구이고 SSE 이벤트가 하나도 없으면 0(응답 전 끊김), 그 밖은 200
    (스트림은 200 을 받은 뒤 `error` 이벤트로 끝난다 - `client._post_stream`).
    """
    if isinstance(row.get("http_status"), int):
        return int(row["http_status"]), False
    if isinstance(_failed_actual(row, "http_status"), int):
        return int(_failed_actual(row, "http_status")), False
    found = (_HTTP_IN_EVIDENCE.search(str(row.get("mode_evidence") or ""))
             or _HTTP_IN_ERROR.match(str(row.get("error") or "")))
    if found:
        return int(found.group(1)), False
    if (row.get("error") and not row.get("sse_events")
            and _TRANSPORT_ERROR.match(str(row.get("error")))):
        return 0, True
    return 200, True


def _restore_status(row: dict[str, Any]) -> tuple[str, bool]:
    """(status, 추정 여부). 칸 → 원 실패 단언의 관측값 → 대응 등급 근거(`status=…`) → 추정 순.

    추정 규칙: 대응 등급 `clarify` → `clarification` · 오류 문구가 있으면 `error` ·
    그 밖 `completed`.
    (폼필 역질문은 `status` 가 `completed` 여도 대응 등급이 `clarify` 다 - 이 추정이 틀리는 자리다.)
    """
    if row.get("status"):
        return str(row["status"]), False
    for key in ("status", "status_any"):
        if isinstance(_failed_actual(row, key), str):
            return str(_failed_actual(row, key)), False
    found = _STATUS_IN_EVIDENCE.search(str(row.get("mode_evidence") or ""))
    if found:
        return found.group(1), False
    if row.get("response_mode") == "clarify":
        return "clarification", True
    return ("error" if row.get("error") else "completed"), True


def _local_artifact(path: str, run_dir: Path) -> Path | None:
    """원 기계의 절대 경로(Windows·Linux)를 이 run 디렉터리의 `artifacts/<파일명>` 으로 옮긴다."""
    name = re.split(r"[\\/]", str(path))[-1]
    local = run_dir / "artifacts" / name
    return local if name and local.exists() else None


def restore_observation(row: dict[str, Any], run_dir: Path) -> tuple[Observation, list[str]]:
    """행 1개 → 관측치. 두 번째 값은 **추정·복원 불가 칸** 목록(차이 원인 분류의 재료)."""
    estimated: list[str] = []
    obs = Observation()
    obs.http_status, guessed = _restore_http_status(row)
    if guessed:
        estimated.append("http_status")
    obs.status, guessed = _restore_status(row)
    if guessed:
        estimated.append("status")

    obs.response = str(row.get("response_text") or "")
    if row.get("response_truncated"):
        estimated.append("response")
    obs.executed_sql = row.get("executed_sql")
    obs.executed_sqls = [
        str(entry.get("sql")) if isinstance(entry, dict) else str(entry)
        for entry in row.get("executed_sqls") or []
        if (entry.get("sql") if isinstance(entry, dict) else entry)
    ]
    obs.row_count = row.get("row_count")
    obs.row_counts_by_db = dict(row.get("row_counts_by_db") or {})
    obs.db_ids, obs.db_ids_source = row_db_ids(row)

    snapshot = row.get("clarification_options")
    if isinstance(snapshot, dict):
        labels = [str(label) for label in snapshot.get("options") or []]
        question = str(snapshot.get("question") or "")
        if snapshot.get("kind") == "form_fill":
            obs.form_fill_clarification = {"question": question,
                                           "fields": [{"name": label} for label in labels]}
        else:
            obs.clarification = {"kind": snapshot.get("kind"), "question": question,
                                 "options": [{"label": label} for label in labels]}
        # 표시 이름만 적재됐다 - db_id 등 원본 값·폼필 후보·50개 넘는 선택지는 없다.
        estimated.append("clarification")

    artifacts = [str(path) for path in row.get("artifacts") or []]
    local = [_local_artifact(path, run_dir) for path in artifacts]
    obs.artifacts = [str(found) if found else original for found, original in zip(local, artifacts)]
    if any(found is None for found in local):
        estimated.append("artifacts")
    failed_has_file = next((f for f in row.get("failed_assertions") or []
                            if f.get("key") == "has_file"), None)
    if artifacts:
        obs.has_file = True
    elif failed_has_file is not None:
        obs.has_file = bool(failed_has_file.get("actual"))   # 원 판정기가 본 관측값
    else:
        obs.has_file = False
        estimated.append("has_file")
    obs.file_name = re.split(r"[\\/]", artifacts[0])[-1] if artifacts else None

    mode = row.get("response_mode")
    obs.hang = mode == "hang" or row.get("forbidden_mode") == "hang"
    if mode == "crash":
        estimated.append("hang")          # crash 가 hang 보다 먼저 분류된다 - hang 여부를 모른다
    obs.max_event_gap_ms = row.get("max_event_gap_ms")
    obs.error = row.get("error")
    obs.auth_retried = bool(row.get("auth_retried"))

    obs.processing_time_ms = row.get("processing_time_ms")
    obs.wall_ms = float(row.get("wall_ms") or 0.0)
    obs.ttfb_ms = row.get("ttfb_ms")
    obs.ttft_ms = row.get("ttft_ms")
    obs.timeline = row.get("timeline") if isinstance(row.get("timeline"), dict) else None
    summary = row.get("plan_summary")
    obs.plan_summary = summary if isinstance(summary, dict) else None
    obs.node_elapsed_ms = dict(row.get("node_elapsed_ms") or {})
    obs.node_calls = dict(row.get("node_calls") or {})
    obs.node_path = [str(node) for node in row.get("node_path") or []]
    obs.sse_events = [str(event) for event in row.get("sse_events") or []]
    obs.llm_calls, obs.tokens = row.get("llm_calls"), row.get("tokens")
    obs.retries, obs.node_count = row.get("retries"), row.get("node_count")
    obs.retries_partial = bool(row.get("retries_partial"))
    if "retries_partial" not in row:
        estimated.append("retries_partial")
    obs.column_mapping = dict(row.get("column_mapping") or {})
    obs.rewrite_traces = [t for t in row.get("rewrite_trace") or [] if isinstance(t, dict)]
    obs.anchor_at = row.get("anchor_at")
    obs.dependency_notes = [n for n in row.get("dependency_notes") or [] if isinstance(n, dict)]
    if isinstance(row.get("form_memory_panel"), dict):
        obs.form_memory_panel = row["form_memory_panel"]
    else:
        estimated.append("form_memory_panel")
    # 결과 행(H-1)·오라클(O)은 과거 run 에 없다 - None 이면 판정기가 보류로 남긴다.
    return obs, estimated


# --- 재판정 ------------------------------------------------------------------------------

def resolve_turn(
    row: dict[str, Any], catalog: Catalog
) -> tuple[tuple[Scenario, int, Turn, Group] | None, str | None]:
    """행이 가리키는 (판정 시나리오, 턴 번호, 턴, 군). 없으면 (None, 사유)."""
    scenario_id = str(row.get("scenario_id"))
    turn_no = int(row.get("turn") or 0)
    ref_id = row_bundle_of(row)
    if ref_id:
        # 부하 묶음(K군): 행은 묶음 id 로 적재되고 판정은 참조 시나리오의 단언으로 했다
        # (`runner._as_member`).
        bundle, ref = catalog.by_id(scenario_id), catalog.by_id(ref_id)
        if bundle is None or ref is None:
            return None, "catalog_missing"
        scenario = _as_member(bundle, ref)
        index = (turn_no - 1) % BUNDLE_TURN_STRIDE + 1
    else:
        found = catalog.by_id(scenario_id)
        if found is None:
            return None, "catalog_missing"
        scenario, index = found, turn_no
    if not 1 <= index <= len(scenario.turns):
        return None, "turn_missing"
    group = catalog.groups.get(scenario.group)
    if group is None:
        return None, "catalog_missing"
    return (scenario, index, scenario.turns[index - 1], group), None


# --- 복원 불가 보류 · 송신 계약 (plans/122 J-4 보강) ------------------------------------------

#: 「복원 불가」 보류 문구의 머리 - 차이 원인 분류가 카탈로그 문구와 가른다.
REJUDGE_HOLD_PREFIX = "재판정 복원 불가 - "

#: 행에 칸이 **없으면** 판정하지 않는 단언: (칸 이름, 보류 단언 키, 칸이 없는 이유).
#: 러너가 무조건 싣는 칸이라(`runner._row`) 칸이 없으면 그 칸 도입 전 run 이다.
#: - `db_ids` 는 `executed_sqls` 도 없을 때만 보류한다 - 있으면 실행 DB 로 되살린다(`row_db_ids`).
#: - `form_memory_panel` 은 러너가 행에 싣지 않는다(2026-09-29 실측) - 재판정에서는 늘 보류다.
ABSENT_COLUMN_HOLDS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("response_text",
     ("response_must_contain", "response_must_contain_any", "response_must_not_contain"),
     "응답 본문 적재 이전 run"),
    ("clarification_options", ("clarification",), "역질문 선택지 적재 이전 run"),
    ("db_ids", ("db_ids",), "라우팅 DB 적재 이전 run"),
    ("form_memory_panel", ("form_memory_panel",), "러너가 행에 싣지 않는 관측"),
)
#: SQL 수집(감사 로그 `query_executed`) 이전 run 에서 판정하지 않는 단언 - 실행 SQL 을 보는
#: 단언이다.
#: `column_must_not_map` 도 실행 SQL(과 매핑 산출물)을 보는 부정 단언이라 같다.
SQL_HOLD_KEYS: tuple[str, ...] = (
    "sql_must_match", "sql_must_not_match", "sql_executed", "period_covers", "column_must_not_map",
)
#: SQL 수집 여부를 가르는 칸 - 감사 로그 수집과 함께 생겼다
#: (`executed_sqls` 는 SQL 이 있을 때만 싣는다).
_SQL_COLLECTION_COLUMNS = ("executed_sqls", "row_counts_by_db")


def run_sql_collected(rows: list[dict[str, Any]]) -> bool:
    """run 이 SQL 을 수집했는가 - 어느 행에든 `executed_sqls`·`row_counts_by_db` 칸이 있다."""
    return any(column in row for row in rows for column in _SQL_COLLECTION_COLUMNS)


def absent_column_holds(
    row: dict[str, Any], expect: dict[str, Any], *, sql_collected: bool,
) -> dict[str, list[str]]:
    """이 행에서 판정하지 않을 단언 {보류 사유 표지: [단언 키…]} - 턴에 선언된 키만 싣는다."""
    held: dict[str, list[str]] = {}
    for column, keys, _why in ABSENT_COLUMN_HOLDS:
        if column in row or (column == "db_ids" and row.get("executed_sqls")):
            continue
        found = [key for key in keys if key in expect]
        if found:
            held[column] = found
    if not sql_collected:
        found = [key for key in SQL_HOLD_KEYS if key in expect]
        if found:
            held["executed_sqls"] = found
    return held


def _hold_notes(held: dict[str, list[str]]) -> list[str]:
    """보류 표지 → 보류 문구(`REJUDGE_HOLD_PREFIX` 로 시작)."""
    why = {column: reason for column, _keys, reason in ABSENT_COLUMN_HOLDS}
    notes = []
    for column, keys in held.items():
        if column == "executed_sqls":
            head = ("SQL 수집 이전 run 이다"
                    "(어느 행에도 `executed_sqls`·`row_counts_by_db` 칸이 없다)")
        else:
            head = f"행에 `{column}` 칸이 없다({why[column]})"
        notes.append(f"{REJUDGE_HOLD_PREFIX}{head} - {' · '.join(keys)} 판정 보류")
    return notes


#: 송신 계약 변경 행의 사유 머리(plans/122 J-4 보강 · 원인 `send_contract`).
SEND_CONTRACT_NOTE = "송신 계약 변경 — 재판정 불가"


def send_contract_gap(
    row: dict[str, Any], scenario: Scenario, turn: Turn, meta: dict[str, Any],
    catalog_digest: str | None,
) -> str | None:
    """재판정 카탈로그의 송신 계약(턴 `auth` · 시나리오 `upload_generate`)으로 보냈다고 볼 수
    없으면 그 사유, 볼 수 있으면 None.

    판별 규칙은 모듈 독스트링 「송신 계약 변경」 - 같은 카탈로그 지문이면 비교하고, 아니면 행의 HTTP
    상태가 새 방식의 거절 상태(`auth: none` → 401/403 · `upload_generate` → 4xx)인지 본다.
    """
    if turn.auth is None and not scenario.upload_generate:
        return None
    if catalog_digest and run_catalog_digest(meta) == catalog_digest:
        return None
    status, _guessed = _restore_http_status(row)
    missing = []
    if turn.auth is not None and not (turn.auth == "none" and status in AUTH_FAILURE_STATUSES):
        missing.append(f"턴 auth={turn.auth} 인데 HTTP {status}(401/403 아님)")
    if scenario.upload_generate and not 400 <= status < 500:
        missing.append(f"upload_generate 인데 HTTP {status}(4xx 아님)")
    if not missing:
        return None
    return f"{SEND_CONTRACT_NOTE} - {' · '.join(missing)} - 과거 run 은 이 방식으로 보내지 않았다"


def _excluded_row(row: dict[str, Any], turn: Turn, note: str) -> dict[str, Any]:
    """비교에서 뺀 행의 재판정 행 - 판정은 `manual` 보류.

    다른 요청의 관측을 새 기대로 재단하지 않는다.
    """
    new_row = dict(row)
    new_row.update({
        "func_verdict": "manual", "invalid_reason": None, "forbidden_mode": None,
        "failed_assertions": [], "manual_notes": [note],
        "manual_sources": ["unobservable"], "manual_source": "unobservable",
        "rejudge_excluded": "send_contract",
    })
    new_row["unevaluated_reason"] = unevaluated_reason(
        new_row, expected_question=clarify.expects_question(turn.expect))
    return new_row


#: 추정·복원 불가 칸 → 그 칸이 움직일 수 있는 단언 키(접두). `mode` 는 대응 등급 변화다.
_FIELD_KEYS: dict[str, tuple[str, ...]] = {
    "http_status": ("http_status", "mode", "response_modes"),
    "status": ("status", "status_any", "sql_must_match", "sql_executed", "mode", "response_modes"),
    "clarification": ("clarification", "mode", "response_modes"),
    "artifacts": ("file", "has_file"),
    "has_file": ("has_file", "file"),
    "hang": ("mode", "response_modes"),
    "retries_partial": ("retries",),
    "form_memory_panel": ("form_memory_panel",),
    "response": ("response_must_contain", "response_must_contain_any", "response_must_not_contain",
                 "mode", "response_modes"),
}
#: 과거 run 에 없는 관측(H-1 결과 행 · O 오라클)의 보류 문구 머리.
_NOT_COLLECTED_NOTES = ("result 를 확인하지 못했다", "oracle")

#: 차이 원인 - 우선순위 순(요약의 대표 원인은 앞선 것).
CAUSES: dict[str, str] = {
    "catalog_missing": "카탈로그에 시나리오·턴이 없다",
    "uncommitted_catalog": ("미커밋 카탈로그 - dirty run 의 카탈로그가 커밋과 달랐다"
                            "(원 판정 근거가 커밋 카탈로그에 없다)"),
    "catalog_change": "카탈로그 변경 - 원 판정 근거(단언 키·수동 문구)가 이 카탈로그에 없다",
    "column_absent": ("복원 불가 보류 - 행에 칸 자체가 없다(옛 run · SQL 수집 이전) - 그 칸을 보는 "
                      "단언을 판정하지 않았다"),
    "send_contract": f"{SEND_CONTRACT_NOTE} - 과거 run 이 새 송신 방식(auth·upload_generate)으로 "
                     "보내지 않았다(비교 제외)",
    "not_collected": "과거 run 에 없는 관측 - 결과 행(H-1)·오라클(O) 보류",
    "response_truncated": "응답 절단 - 4,000자 뒤를 못 본다",
    "unrestorable": "복원 불가 칸 - 행에 없는 값을 추정했다",
    "judge_change": ("판정기 변경(추정) - 같은 입력·카탈로그에서 판정이 달라졌다"
                     "(run 이후 판정기 수정분)"),
    "contract_change": ("판정 계약 변경(카탈로그 값·판정기 - 구분 불가) - 같은 run 을 "
                        "`--identity` 로도 재판정해 그 차이(판정기 몫)를 빼면 "
                        "카탈로그 몫이 남는다"),
}


def _key_hit(key: str, prefixes: tuple[str, ...]) -> bool:
    return any(key == prefix or key.startswith(prefix + ".") for prefix in prefixes)


def _canon(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _expectation_gone(failure: dict[str, Any], expect: dict[str, Any]) -> bool:
    """원 실패 단언의 기대값이 이 카탈로그 턴에 더는 없는가 - 단언 키가 없거나 값이 바뀌었다."""
    root = str(failure.get("key")).split(".")[0]
    if root == "response_modes":
        return False                  # 시나리오 칸이다 - 턴 기대값으로 대조하지 않는다
    if root not in expect:
        return True
    return _canon(failure.get("expected")) not in _canon(expect[root])


def _diff_causes(
    row: dict[str, Any], verdict: Verdict, turn: Turn,
    estimated: list[str], *, identity: bool, dirty: bool,
    held: dict[str, list[str]] | None = None,
) -> list[str]:
    """행 1개의 전후 차이 원인(우선순위 순 · 중복 없음).

    `held` 는 「복원 불가」 보류 {칸: [단언 키…]} 다(`absent_column_holds`). 그 보류 문구는
    `note_sources` 가 카탈로그 문구로 읽으므로(판정기가 쓰는 문구가 아니다) 카탈로그 변경
    판별에서 뺀다.
    """
    held = held or {}
    before_keys = Counter(str(f.get("key")) for f in row.get("failed_assertions") or [])
    after_keys = Counter(f.key for f in verdict.failures)
    changed_keys = set((before_keys - after_keys) | (after_keys - before_keys))
    if row.get("response_mode") != verdict.response_mode:
        changed_keys.add("mode")
    before_notes, after_notes = list(row.get("manual_notes") or []), list(verdict.manual_notes)
    only_before = [n for n in before_notes if n not in after_notes]
    only_after = [n for n in after_notes if n not in before_notes]

    causes: list[str] = []
    gone = set(before_keys - after_keys)
    stale_key = any(_expectation_gone(f, turn.expect)
                    for f in row.get("failed_assertions") or [] if str(f.get("key")) in gone)
    stale_note = any("catalog" in note_sources(str(note)) for note in only_before + only_after
                     if not str(note).startswith(REJUDGE_HOLD_PREFIX))
    # 팬아웃 보류(Y-4)는 턴에 `row_count` 가 있을 때만 생긴다 - 사라졌으면 카탈로그가 바뀐 것이다.
    stale_fanout = "row_count" not in turn.expect and any(
        "fanout" in note_sources(str(note)) for note in only_before)
    if stale_key or stale_note or stale_fanout:
        causes.append("uncommitted_catalog" if identity and dirty else "catalog_change")
    held_keys = tuple(key for keys in held.values() for key in keys)
    # 응답 본문 칸이 없으면 대응 등급도 빈 응답으로 다시 고른 것이다(`classify_mode`).
    mode_unrestorable = ("response_text" not in row
                         and bool(changed_keys & {"mode", "response_modes"}))
    if mode_unrestorable or any(_key_hit(key, held_keys) for key in changed_keys) or any(
        str(note).startswith(REJUDGE_HOLD_PREFIX) for note in only_after
    ):
        causes.append("column_absent")
    if any(str(note).startswith(_NOT_COLLECTED_NOTES) for note in only_after):
        causes.append("not_collected")
    if "response" in estimated and changed_keys & set(_FIELD_KEYS["response"]):
        causes.append("response_truncated")
    if any(field != "response"
           and any(_key_hit(key, _FIELD_KEYS.get(field, ())) for key in changed_keys)
           for field in estimated):
        causes.append("unrestorable")
    if not causes:
        # 같은 카탈로그(항등)면 남는 것은 판정기 몫이다. 다른 카탈로그면 값만 바뀐 단언과
        # 판정기 수정이 구별되지 않는다 - 지어내지 않고 「계약 변경」으로 둔다.
        causes.append("judge_change" if identity else "contract_change")
    return causes


def rejudge_row(
    row: dict[str, Any], catalog: Catalog, meta: dict[str, Any], run_dir: Path,
    *, identity: bool = False, sql_collected: bool | None = None,
    catalog_digest: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """행 1개를 재판정한다 → (재판정 행, 차이 기록). 무효 행은 그대로 둔다(측정 미성립).

    `sql_collected` 는 run 단위 판별(`run_sql_collected`)이다 - 없으면 이 행의 칸으로만 가른다.
    `catalog_digest` 는 재판정 카탈로그 지문 - 없으면 송신 계약을 선언한 턴에서만 계산한다.
    송신 계약 변경 행은 상태 `send_contract` 로 비교에서 빠진다(`changed`·`detail_changed` 거짓).
    """
    base: dict[str, Any] = {"profile": row.get("profile"), "arm": row.get("arm"),
            "scenario_id": row.get("scenario_id"), "turn": row.get("turn"),
            "repeat": row.get("repeat"),
            "before": {"func": row.get("func_verdict"),
                       "failed": sorted(str(f.get("key"))
                                        for f in row.get("failed_assertions") or []),
                       "manual_notes": list(row.get("manual_notes") or []),
                       "response_mode": row.get("response_mode")}}
    if row_is_invalid(row):
        return dict(row), {**base, "status": "invalid_kept", "after": base["before"],
                           "changed": False, "detail_changed": False, "causes": []}
    resolved, missing = resolve_turn(row, catalog)
    if resolved is None:
        return dict(row), {**base, "status": missing, "after": None, "changed": True,
                           "detail_changed": True, "causes": ["catalog_missing"]}
    scenario, index, turn, group = resolved
    if turn.auth is not None or scenario.upload_generate:
        digest = catalog_digest or judgement_digest(catalog)
        gap = send_contract_gap(row, scenario, turn, meta, digest)
        if gap:
            return _excluded_row(row, turn, gap), {
                **base, "status": "send_contract", "after": None, "changed": False,
                "detail_changed": False, "causes": ["send_contract"], "reason": gap}
    obs, estimated = restore_observation(row, run_dir)
    run_env = meta.get("env")
    mock = str(row.get("mode") or meta.get("mode")) == "mock"
    env_mismatch = not mock and scenario.env not in ("both", run_env)
    judged = _hold_for_env(turn, scenario, run_env) if env_mismatch else turn
    if sql_collected is None:
        sql_collected = run_sql_collected([row])
    held = absent_column_holds(row, judged.expect, sql_collected=sql_collected)
    if held:
        dropped = {key for keys in held.values() for key in keys}
        judged = replace(judged, expect={key: value for key, value in judged.expect.items()
                                         if key not in dropped})
    verdict = evaluate_turn(scenario, index, judged, obs, group, mock=mock)
    if held and verdict.func != INVALID_VERDICT:
        # 보류는 합격을 막는다(`manual`) - 판정기의 `manual` 규칙과 같다(불합격·오류는 그대로).
        verdict.manual_notes = [*verdict.manual_notes, *_hold_notes(held)]
        if "unobservable" not in verdict.manual_sources:
            verdict.manual_sources = [*verdict.manual_sources, "unobservable"]
        if verdict.func == "pass":
            verdict.func = "manual"

    new_row = dict(row)
    new_row.update({
        "func_verdict": verdict.func, "invalid_reason": verdict.invalid_reason,
        "perf_verdict": verdict.perf, "response_mode": verdict.response_mode,
        "forbidden_mode": verdict.forbidden_mode, "mode_evidence": verdict.mode_evidence,
        "failed_assertions": [f.as_dict() for f in verdict.failures],
        "manual_notes": verdict.manual_notes, "manual_sources": verdict.manual_sources,
        "manual_source": primary_manual_source(verdict.manual_sources),
    })
    if env_mismatch:
        new_row["env_mismatch"] = {"scenario_env": scenario.env, "run_env": run_env}
    else:
        new_row.pop("env_mismatch", None)
    new_row["unevaluated_reason"] = unevaluated_reason(
        new_row, expected_question=clarify.expects_question(turn.expect))

    after = {"func": verdict.func, "failed": sorted(f.key for f in verdict.failures),
             "manual_notes": list(verdict.manual_notes), "response_mode": verdict.response_mode}
    changed = after["func"] != base["before"]["func"]
    detail_changed = changed or any(after[k] != base["before"][k]
                                    for k in ("failed", "manual_notes", "response_mode"))
    dirty = bool(meta.get("dirty"))
    causes = (_diff_causes(row, verdict, turn, estimated, identity=identity, dirty=dirty,
                           held=held)
              if detail_changed else [])
    return new_row, {**base, "status": "rejudged", "after": after, "changed": changed,
                     "detail_changed": detail_changed, "causes": causes,
                     "estimated": estimated, "held": held,
                     "env_mismatch_changed": bool(row.get("env_mismatch")) != env_mismatch}


def rejudge_run(
    run_dir: Path, catalog: Catalog, *, identity: bool = False
) -> dict[str, Any]:
    """run 전체를 재판정한다 → {rows(재판정 행), diffs, meta}."""
    rows = load_rows(run_dir)
    if not rows:
        raise RejudgeError(f"{run_dir / 'raw.jsonl'} 가 없거나 비었다")
    run = load_run_meta(run_dir)
    meta = run.get("meta") or {}
    sql_collected = run_sql_collected(rows)
    digest = judgement_digest(catalog)
    new_rows, diffs = [], []
    for row in rows:
        new_row, diff = rejudge_row(row, catalog, meta, run_dir, identity=identity,
                                    sql_collected=sql_collected, catalog_digest=digest)
        new_rows.append(new_row)
        diffs.append(diff)
    return {"run": run, "rows": rows, "new_rows": new_rows, "diffs": diffs,
            "sql_collected": sql_collected}


# --- 산출 --------------------------------------------------------------------------------

def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _table(header: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    lines += ["| " + " | ".join("" if c is None else str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    """전후 판정 요약 - 전이 행렬 · 일치 · 원인 · arm별 J-1 전후."""
    diffs = result["diffs"]
    judged = [d for d in diffs if d["status"] == "rejudged"]
    transitions = Counter(f"{d['before']['func']} → {d['after']['func']}"
                          for d in judged if d["changed"])
    primary = Counter(d["causes"][0] for d in diffs if d["detail_changed"] and d["causes"])
    func_causes = Counter(cause for d in diffs if d["changed"] for cause in d["causes"])
    run = result["run"]
    # 「복원 불가」 보류(J-4 보강) - 칸별 행 수와 보류 단언 키(중복 없음 · 처음 본 순서).
    held_rows: Counter[str] = Counter()
    held_keys: dict[str, list[str]] = {}
    for d in judged:
        for column, keys in (d.get("held") or {}).items():
            held_rows[column] += 1
            known = held_keys.setdefault(column, [])
            known.extend(key for key in keys if key not in known)
    return {
        "rows": len(diffs),
        "rejudged": len(judged),
        "invalid_kept": sum(1 for d in diffs if d["status"] == "invalid_kept"),
        "missing": sum(1 for d in diffs if d["status"] in ("catalog_missing", "turn_missing")),
        "send_contract": sum(1 for d in diffs if d["status"] == "send_contract"),
        "held_rows": sum(1 for d in judged if d.get("held")),
        "held_by_column": {column: {"rows": count, "keys": held_keys[column]}
                           for column, count in held_rows.most_common()},
        "sql_collected": result.get("sql_collected"),
        "func_equal": sum(1 for d in judged if not d["changed"]),
        "detail_equal": sum(1 for d in judged if not d["detail_changed"]),
        "func_changed": sum(1 for d in diffs if d["changed"]),
        "transitions": dict(transitions.most_common()),
        "primary_causes": dict(primary.most_common()),
        "func_change_causes": dict(func_causes.most_common()),
        "env_mismatch_changed": sum(1 for d in judged if d.get("env_mismatch_changed")),
        "arms_before": arm_breakdown(result["rows"], run),
        "arms_after": arm_breakdown(result["new_rows"], run),
    }


def render_markdown(result: dict[str, Any], summary: dict[str, Any], source: dict[str, Any]) -> str:
    meta = result["run"].get("meta") or {}
    out = [f"# 오프라인 재판정 - {meta.get('run_id', '?')} (plans/122 J-4)", ""]
    out.append(_table(["항목", "값"], [
        ["원 run", f"`{meta.get('run_id')}` · 커밋 `{str(meta.get('commit') or '-')[:12]}` · "
                  f"dirty {meta.get('dirty')}"],
        ["재판정 카탈로그", source["label"]],
        ["재판정 카탈로그 지문", f"`{source['digest']}`"],
        ["원 run 판정 계약 지문", f"`{run_catalog_digest(meta)}`" if run_catalog_digest(meta)
         else "미기록(옛 run)"],
        ["항등 대조", "예 - 원 판정과 행 단위 대조" if source["identity"] else "아니오"],
    ]))
    out.append("")
    if source.get("note"):
        out += [f"> {source['note']}", ""]
    out.append("결과 행(H-1)·오라클(O)은 과거 run 에 없어 보류로 남는다. "
               "추정 규칙은 모듈 독스트링 참조.")
    out.append("")
    out.append("## 행 대조")
    out.append("")
    out.append(_table(["지표", "값"], [
        ["전체 행", summary["rows"]],
        ["재판정 행", summary["rejudged"]],
        ["무효 행(그대로 둠)", summary["invalid_kept"]],
        ["카탈로그에 없는 행", summary["missing"]],
        ["판정(func) 일치", f"{summary['func_equal']}/{summary['rejudged']}"],
        ["판정·실패 키·수동 문구·대응 등급 전부 일치",
         f"{summary['detail_equal']}/{summary['rejudged']}"],
        ["판정이 바뀐 행", summary["func_changed"]],
        ["환경 불일치 판별이 바뀐 행", summary["env_mismatch_changed"]],
        [f"{SEND_CONTRACT_NOTE}(비교 제외 · `manual` 보류)", summary["send_contract"]],
        ["복원 불가 보류 행(칸 없음 - 판정은 나머지 단언으로)", summary["held_rows"]],
        ["SQL 수집 run", "예" if summary["sql_collected"] else
         "아니오 - SQL 단언은 복원 불가 보류"],
    ]))
    out.append("")
    if summary["held_by_column"]:
        out.append("## 복원 불가 보류 - 칸이 없어 판정하지 않은 단언 (plans/122 J-4 보강)")
        out.append("")
        out.append(_table(["없는 칸", "보류 단언", "행"], [
            ["SQL 수집(`executed_sqls`·`row_counts_by_db`)" if column == "executed_sqls"
             else f"`{column}`", " · ".join(info["keys"]), info["rows"]]
            for column, info in summary["held_by_column"].items()]))
        out.append("")
    excluded = [d for d in result["diffs"] if d["status"] == "send_contract"]
    if excluded:
        out.append(f"## {SEND_CONTRACT_NOTE} ({len(excluded)}건 · 비교 제외)")
        out.append("")
        out.append("새 송신 방식으로 보냈는데 제품이 거절하지 않은 회귀도 여기로 온다"
                   " - 행을 확인할 것"
                   "(판별 규칙은 모듈 독스트링).")
        out.append("")
        out.append(_table(["시나리오", "턴", "프로파일", "원 판정", "사유"], [
            [d["scenario_id"], d["turn"], d["profile"], d["before"]["func"], d.get("reason")]
            for d in excluded[:200]]))
        out.append("")
    out.append("## 판정 전이")
    out.append("")
    out.append(_table(["전 → 후", "행"], [[k, v] for k, v in summary["transitions"].items()])
               if summary["transitions"] else "판정이 바뀐 행 없음.")
    out.append("")
    out.append("## 차이 원인 (대표 원인 · 세부 차이 포함)")
    out.append("")
    out.append(_table(
        ["원인", "뜻", "대표 원인 행", "판정 변화 행(중복 포함)"],
        [[cause, CAUSES[cause], summary["primary_causes"].get(cause, 0),
          summary["func_change_causes"].get(cause, 0)]
         for cause in CAUSES
         if summary["primary_causes"].get(cause) or summary["func_change_causes"].get(cause)],
    ) if summary["primary_causes"] else "차이 행 없음.")
    out.append("")
    out.append("## arm별 기능 합격률 전후 (D-241 ③ 정의 - plans/122 J-1)")
    out.append("")
    body = []
    for before, after in zip(summary["arms_before"], summary["arms_after"]):
        label = (before.get("arm") or "(arm 없음)") + (" (기준)" if before.get("reference") else "")
        body.append([label, before.get("tier") or "-",
                     f"{_pct(before['func_pass_rate'])} ({before['pass']}/{before['judged']})",
                     f"{_pct(after['func_pass_rate'])} ({after['pass']}/{after['judged']})",
                     f"{_pct(before['coverage'])} ({before['judged']}/{before['scored']})",
                     f"{_pct(after['coverage'])} ({after['judged']}/{after['scored']})"])
    out.append(_table(["arm", "사다리 단", "기능 합격률 전", "기능 합격률 후",
                       "판정 커버리지 전", "판정 커버리지 후"], body))
    out.append("")
    changed = [d for d in result["diffs"] if d["detail_changed"]]
    out.append(f"## 차이 행 ({len(changed)}건 · 판정 변화 먼저 · 상위 200)")
    out.append("")
    changed.sort(key=lambda d: (not d["changed"], str(d["scenario_id"]), int(d["turn"] or 0)))
    out.append(_table(
        ["시나리오", "턴", "프로파일", "전", "후", "원인", "실패 키 전 → 후"],
        [[d["scenario_id"], d["turn"], d["profile"], d["before"]["func"],
          (d["after"] or {}).get("func", "-"), " · ".join(d["causes"]),
          f"{','.join(d['before']['failed']) or '-'} → "
          f"{','.join((d['after'] or {}).get('failed') or []) or '-'}"]
         for d in changed[:200]],
    ) if changed else "없음.")
    out.append("")
    return "\n".join(out) + "\n"


def write_outputs(result: dict[str, Any], out_dir: Path, source: dict[str, Any],
                  catalog: Catalog) -> dict[str, Path]:
    """재판정 산출을 `out_dir` 에 쓴다. 원 run 디렉터리의 파일은 건드리지 않는다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with utf8_open(out_dir / "raw.jsonl", "w") as handle:
        for row in result["new_rows"]:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    run = json.loads(json.dumps(result["run"], ensure_ascii=False))   # 깊은 사본
    meta = run.setdefault("meta", {})
    original = {"run_id": meta.get("run_id"), "judgement_contract": meta.get("judgement_contract"),
                "catalog_digest": run_catalog_digest(meta)}
    meta["run_id"] = f"{meta.get('run_id')}~rejudge"
    # 재판정 행의 판정 계약 = 재판정 카탈로그 지문(G-17 - 같은 지문의 run 끼리만 비교한다).
    meta["catalog_digest"] = source["digest"]
    # 판정기 지문은 이번 재판정에 쓴 판정기의 것이다(원 run 의 판정기가 아니다 - J-1 ⑤ 보강).
    meta["judgement_contract"] = {"catalog_digest": source["digest"], "judge_digest": judge_digest()}
    meta["rejudge"] = {"source": source["label"], "identity": source["identity"],
                       "original": original}
    run["raw_path"] = str(out_dir / "raw.jsonl")
    run["out_dir"] = str(out_dir)
    with utf8_open(out_dir / "run.json", "w") as handle:
        json.dump(run, handle, ensure_ascii=False, indent=2)
    with utf8_open(out_dir / "rejudge_diff.jsonl", "w") as handle:
        for diff in result["diffs"]:
            handle.write(json.dumps(diff, ensure_ascii=False) + "\n")
    summary = summarize(result)
    with utf8_open(out_dir / "rejudge.md", "w") as handle:
        handle.write(render_markdown(result, summary, source))
    paths = write_report(out_dir, catalog)
    return {"raw": out_dir / "raw.jsonl", "diff": out_dir / "rejudge_diff.jsonl",
            "rejudge": out_dir / "rejudge.md", **paths}


# --- CLI ---------------------------------------------------------------------------------

def _resolve_run_dir(value: str) -> Path:
    path = Path(value)
    if path.is_dir():
        return path
    candidate = REPO_ROOT / "results" / "scenario" / value
    if candidate.is_dir():
        return candidate
    raise RejudgeError(f"run 디렉터리가 없다: {value}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.scenario.rejudge",
        description="과거 run 의 raw.jsonl 을 현 판정기로 다시 판정한다(plans/122 J-4 · LLM 0).")
    parser.add_argument("run_dir", help="run 디렉터리(또는 results/scenario 아래 run id)")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--catalog-ref",
                        help="이 git ref 의 카탈로그로 판정(checkout 없이 git archive)")
    source.add_argument("--catalog-dir", help="시나리오 YAML 디렉터리(프로파일은 현 작업 트리)")
    parser.add_argument("--out", help="산출 디렉터리(기본 <run_dir>/rejudge/<카탈로그 표지>)")
    parser.add_argument("--identity", action="store_true",
                        help="run 메타 커밋의 카탈로그로 재판정해 원 판정과 행 단위 대조"
                             "(커밋이 로컬에 없으면 --catalog-ref 로 대체 ref 를 준다)")
    args = parser.parse_args(argv)

    try:
        run_dir = _resolve_run_dir(args.run_dir)
        meta = (load_run_meta(run_dir).get("meta") or {})
        with tempfile.TemporaryDirectory(prefix="rejudge-") as workdir:
            note = None
            if args.catalog_dir:
                catalog = load_catalog(Path(args.catalog_dir), PROFILES_PATH, DB_REGISTRY_PATH)
                label, tag = f"디렉터리 `{args.catalog_dir}`", "dir"
            elif args.catalog_ref or args.identity:
                ref = args.catalog_ref or str(meta.get("commit") or "")
                if not ref:
                    raise RejudgeError("run 메타에 커밋이 없다 - --catalog-ref 로 ref 를 준다")
                if args.identity and not args.catalog_ref and not resolve_commit(ref):
                    raise RejudgeError(
                        f"run 커밋 `{ref[:12]}` 가 로컬 저장소에 없다(폐쇄망 쪽 커밋) - "
                        "같은 날 카탈로그의 커밋을 --catalog-ref 로 대신 준다"
                        "(항등 대조의 대체 ref)")
                catalog, sha = load_catalog_at_ref(ref, Path(workdir))
                label, tag = f"git `{ref}` (`{sha[:12]}`)", sha[:12]
                if args.identity and args.catalog_ref:
                    note = (f"run 커밋 `{str(meta.get('commit'))[:12]}` 대신 `{sha[:12]}` 의 "
                            "카탈로그로 항등 대조했다 - 두 커밋의 카탈로그가 다르면 "
                            "그 차이도 섞인다.")
            else:
                catalog = load_catalog(SCENARIO_DIR, PROFILES_PATH, DB_REGISTRY_PATH)
                label, tag = "현 작업 트리", "worktree"
            if args.identity and meta.get("dirty"):
                dirty_note = ("원 run 은 dirty 였다 - 미커밋 카탈로그 변경은 커밋에 없어 "
                              "불일치로 나올 수 있다(원인 `uncommitted_catalog`).")
                note = f"{note} {dirty_note}" if note else dirty_note
            source_info = {"label": label, "digest": judgement_digest(catalog),
                           "identity": bool(args.identity), "note": note}
            result = rejudge_run(run_dir, catalog, identity=bool(args.identity))
            out_dir = Path(args.out) if args.out else run_dir / "rejudge" / tag
            paths = write_outputs(result, out_dir, source_info, catalog)
    except (RejudgeError, CatalogError) as exc:
        print(f"[rejudge] 실패: {exc}", file=sys.stderr)
        return 2
    summary = summarize(result)
    print(f"[rejudge] {run_dir.name} · 카탈로그 {label} · 행 {summary['rows']} · "
          f"판정 일치 {summary['func_equal']}/{summary['rejudged']} · "
          f"전부 일치 {summary['detail_equal']}/{summary['rejudged']} · "
          f"복원 불가 보류 {summary['held_rows']} · 송신 계약 변경 {summary['send_contract']} · "
          f"대표 원인 {summary['primary_causes'] or '-'}")
    for name, path in paths.items():
        print(f"  {name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
