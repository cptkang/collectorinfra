"""plans/166 W1 M6 — APM 게이트웨이 소비자 계약(소비측 대조).

게이트웨이 오류 코드·봉투·작업 도구 이름의 정본은 `apm_gateway`에 있고, 소비자(`src`·`noise_gate`·
`sre_agent`)는 import 없이 문자열 사본을 쓴다(D-139·D-274). 공유 라이브러리 대신 계약 스냅샷
`apm_gateway/tests/snapshots/plan166_w1/apm_contract.json`을 **파일 경로로 읽어** 소비측 사본이
그 집합 안에 있는지 본다(정본 쪽 대조는 `apm_gateway/tests/test_plan166_w1_contract_snapshot.py`).

사본은 소스 파일을 AST·텍스트로만 읽는다 — `sre_agent`는 별도 venv라 import하지 않는다.
동음이의(같은 철자의 본체 어휘)는 대상이 아니다: `src/utils/prior_dependency.py`
`NOTE_SOURCE_UNAVAILABLE`·`src/domain/disclosure.py`의 노트 종류 · `apm_query._refusal(…,
"source_unavailable", …)`의 강등 사유(`DEGRADED_KEY`) · `intent_planner`의
`task_frame_contract_violation`.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
CONTRACT: dict[str, Any] = json.loads(
    (REPO / "apm_gateway/tests/snapshots/plan166_w1/apm_contract.json").read_text(encoding="utf-8")
)
CODES = frozenset(CONTRACT["error_codes"])


def _assigned_strings(rel: str, name: str) -> list[str]:
    """모듈 최상위 `name = …`(주석 대입 포함) 값 안의 문자열 상수 전부."""
    tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            assert node.value is not None
            return [
                n.value
                for n in ast.walk(node.value)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            ]
    raise AssertionError(f"{rel}에 {name} 대입이 없다")


# (파일, 최상위 이름) — 값이 게이트웨이 오류 코드의 사본(부분집합)
CODE_COPIES = [
    ("src/orchestration/apm_query.py", "_GUIDANCE_CODES"),
    ("src/orchestration/apm_jobs.py", "JOB_NOT_FOUND"),
    ("src/orchestration/apm_jobs.py", "JOB_NOT_READY"),
    ("noise_gate/scripts/page_audit_sanitize.py", "APP_IMPACT_INSTANCE_UNRESOLVED"),
    ("sre_agent/sre_agent/domain/investigation_limits.py", "APM_UNAVAILABLE_CODES"),
]

# (파일, 코드 안 원문 조각) — 코드에 박힌 오류 코드 리터럴
_APM_QUERY = "src/orchestration/apm_query.py"
_NOTIFY_GATE = "noise_gate/application/nodes/notification_gate.py"
_WORLD = "sre_agent/scripts/investigation_bench/world_server.py"
INLINE_COPIES = [
    (_APM_QUERY, '"instance_unresolved" not in call.error', "instance_unresolved"),
    (_APM_QUERY, '"instance_unresolved" in call.error', "instance_unresolved"),
    (_NOTIFY_GATE, '"invalid_argument — ', "invalid_argument"),
    (_NOTIFY_GATE, '"contract_violation — ', "contract_violation"),
    (_WORLD, '"error": "instance_unresolved"', "instance_unresolved"),
    (_WORLD, '"apm_api_error"', "apm_api_error"),
]


@pytest.mark.parametrize(("rel", "name"), CODE_COPIES)
def test_code_copy_within_contract(rel: str, name: str) -> None:
    values = _assigned_strings(rel, name)
    assert values, f"{rel}:{name} 값이 비었다"
    assert set(values) <= CODES, f"{rel}:{name} 계약 밖 코드: {sorted(set(values) - CODES)}"


def test_full_code_copy_equals_contract() -> None:
    """`page_audit_sanitize.GATEWAY_ERROR_CODES`는 정본 `ERROR_CODES` 전체의 사본이다."""
    rel = "noise_gate/scripts/page_audit_sanitize.py"
    assert set(_assigned_strings(rel, "GATEWAY_ERROR_CODES")) == CODES


@pytest.mark.parametrize(("rel", "snippet", "code"), INLINE_COPIES)
def test_inline_code_within_contract(rel: str, snippet: str, code: str) -> None:
    assert snippet in (REPO / rel).read_text(encoding="utf-8"), f"{rel}에서 {snippet!r}이 사라졌다"
    assert code in CODES


def test_job_tool_names_within_contract() -> None:
    rel = "src/orchestration/apm_jobs.py"
    names = {
        v
        for n in ("STATUS_TOOL", "CANCEL_TOOL", "READ_TOOL")
        for v in _assigned_strings(rel, n)
    }
    assert names == set(CONTRACT["job_tools"])


def test_envelope_copies_match_contract() -> None:
    kind, source = CONTRACT["source_kind"], CONTRACT["source"]
    assert _assigned_strings(
        "sre_agent/sre_agent/domain/severity_signatures.py", "APM_SOURCE_KIND"
    ) == [kind]
    tree = ast.parse((REPO / _WORLD).read_text(encoding="utf-8"))
    envelope = next(
        ast.literal_eval(n.value)
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_APM_ENVELOPE" for t in n.targets)
    )
    assert envelope == {"source_kind": kind, "source": source}
