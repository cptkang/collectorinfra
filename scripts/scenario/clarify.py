"""역질문 자동 응답 (plans/94 §0.3-2 · D-216).

사람이 답하던 역질문을 **스크립트가 결정적으로 답해** 테스트가 끝까지 진행되게 한다.
LLM 을 쓰지 않는다 - 답은 전부 구조화 필드이고(D-143 `selected_db_ids` · D-151
`form_fill_answers`), 무엇을 골랐는지는 원시 로그 `auto_answers` 에 남는다.

| 역질문 | 오는 곳 | 답 |
|---|---|---|
| 존 선택 `zone_select`(D-143) · 범위 선택 `scope_select`(D-176 후속4) | done `clarification` | `selected_db_ids` - YAML 명시값, 없으면 `auto_answer.yaml` 우선 존, 없으면 첫 선택지 |
| 폼필 미해결 필드(D-151) | done `form_fill_clarification` | 전 필드 `blank` - 후보를 임의로 고르면 오답을 채운다 |
| SQL/구조 승인(D-130) | done `awaiting_approval` | 승인 어휘 `승인`(결정적 판정 `_APPROVE_WORDS`) |

**턴이 역질문 자체를 기대하면 답하지 않는다**(`status: clarification` 등) - 그 시나리오는
다음 턴에서 직접 답한다(F-01). 턴 단위로 `auto_answer: false` 를 두면 역질문을 그대로
판정한다(승계 회귀를 잡는 F-06 3턴처럼 "물으면 안 되는" 턴).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

from . import REPO_ROOT
from .assertions import Observation

AUTO_ANSWER_PATH = REPO_ROOT / "config" / "scenarios" / "auto_answer.yaml"

#: 한 턴에서 연속으로 답할 수 있는 역질문 수. 존 -> 범위 -> 승인이 한 줄로 이어져도 3회다.
MAX_AUTO_ANSWERS = 3

#: 승인 대기 답. 서버의 결정적 승인 어휘(query.py `_APPROVE_WORDS`)에 있어 LLM 보조 없이 확정된다.
APPROVAL_REPLY = "승인"

#: 폼필 답변 턴의 질의 문구. 웹 UI 가 보내는 것과 같다(app.js `executeStreamingQuery`).
FORM_FILL_REPLY_QUERY = "[양식 미해결 항목 답변]"

_ZONE_KINDS = frozenset({"zone_select", "scope_select"})
_FILE_ENDPOINTS = {"file": "plain", "file_stream": "stream"}


@dataclass(frozen=True)
class Question:
    """응답을 기다리는 역질문 1건."""

    kind: str                     # zone | form_fill | approval
    payload: dict[str, Any]

    @property
    def signature(self) -> str:
        """같은 질문이 되풀이됐는지 가리는 키. 답이 먹지 않으면 무한 왕복을 멈춘다."""
        return self.kind + json.dumps(self.payload, ensure_ascii=False, sort_keys=True, default=str)


def load_zone_preference(path: Path = AUTO_ANSWER_PATH) -> list[str]:
    """존 자동 선택 우선순위를 읽는다. 파일이 없으면 빈 목록(첫 선택지를 고른다)."""
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [str(item) for item in (raw.get("zone_preference") or [])]


def expects_question(expect: dict[str, Any]) -> bool:
    """턴이 역질문을 **기대하는지**. 기대하면 자동으로 답하지 않고 그대로 판정한다."""
    return (
        expect.get("status") in ("clarification", "awaiting_approval")
        or "clarification" in expect
    )


def pending_question(obs: Observation) -> Optional[Question]:
    """관측치에서 답을 기다리는 역질문을 찾는다. 없으면 None."""
    clarification = obs.clarification
    if isinstance(clarification, dict) and clarification.get("kind") in _ZONE_KINDS:
        return Question("zone", clarification)
    if isinstance(obs.form_fill_clarification, dict) and obs.form_fill_clarification.get("fields"):
        return Question("form_fill", obs.form_fill_clarification)
    if obs.status == "awaiting_approval":
        return Question("approval", {"response": obs.response[:200]})
    return None


def offered_db_ids(clarification: dict[str, Any]) -> list[str]:
    """선택지가 제시한 DB 를 표시 순서대로 모은다. zone_select 는 `db_id`, scope_select 는 `db_ids`."""
    offered: list[str] = []
    for option in clarification.get("options") or []:
        if not isinstance(option, dict):
            continue
        ids = [option["db_id"]] if option.get("db_id") else list(option.get("db_ids") or [])
        for db_id in ids:
            if db_id and str(db_id) not in offered:
                offered.append(str(db_id))
    return offered


def choose_db_ids(
    clarification: dict[str, Any], preference: list[str], override: Optional[list[str]] = None
) -> list[str]:
    """고를 존. YAML 명시값 -> 우선순위에서 제시된 첫 존 -> 첫 선택지 순이다(사용자 확정 2026-09-15)."""
    if override:
        return [str(db_id) for db_id in override]
    offered = offered_db_ids(clarification)
    for db_id in preference:
        if db_id in offered:
            return [db_id]
    return offered[:1]


def build_answer(
    question: Question,
    *,
    override: dict[str, Any],
    preference: list[str],
    last_query: str,
) -> Optional[dict[str, Any]]:
    """역질문에 보낼 요청 본문(thread_id 제외)을 만든다. 답을 만들 수 없으면 None."""
    if question.kind == "zone":
        db_ids = choose_db_ids(question.payload, preference, override.get("selected_db_ids"))
        if not db_ids:
            return None
        # 원문 재전송은 웹 UI 와 같다 - 서버는 selected_db_ids 로 라우팅을 고정한다(D-143).
        return {
            "query": str(question.payload.get("original_query") or last_query),
            "selected_db_ids": db_ids,
        }
    if question.kind == "form_fill":
        answers = override.get("form_fill_answers") or {
            str(field["name"]): {"action": "blank", "value": None}
            for field in question.payload.get("fields") or []
            if isinstance(field, dict) and field.get("name")
        }
        if not answers:
            return None
        return {"query": FORM_FILL_REPLY_QUERY, "form_fill_answers": answers}
    if question.kind == "approval":
        return {"query": str(override.get("approval") or APPROVAL_REPLY)}
    return None


def answer_endpoint(endpoint: str, question: Question) -> tuple[str, bool]:
    """답을 보낼 엔드포인트와 파일 재전송 여부.

    존 선택이 파일 업로드에서 왔으면(`has_file`) 파일을 다시 올린다 - 웹 UI 도 보관한 파일을
    `selected_db_ids` 와 함께 재전송한다. 폼필 답변·승인은 JSON 경로다 - `/query/file` 은
    `form_fill_answers` 를 Form 으로 받지 않는다(catalog.Turn 주석).
    """
    if question.kind == "zone" and question.payload.get("has_file"):
        return endpoint, True
    return _FILE_ENDPOINTS.get(endpoint, endpoint), False


def complete_payload(
    payload: dict[str, Any], last_obs: Optional[Observation], last_query: str
) -> dict[str, Any]:
    """구조화 필드만 적은 턴을 서버가 받는 모양으로 채운다. 바꿀 것이 없으면 원본을 돌려준다.

    - `query`: 서버는 필수로 받는다(`QueryRequest.query` min_length=1). `selected_db_ids` 만 보내는
      답변 턴(F-01 2턴)은 채우지 않으면 422 로 끝난다 - 웹 UI 가 보내는 문구와 같게 채운다.
    - `form_memory_delete.signature`: 서버는 세션이 조회한 양식 시그니처와 같아야 지운다(D-187).
      직전 응답의 저장 값 패널(`form_memory_panel`)에서 가져온다 - 웹 UI 도 패널의 값을 보낸다(I-06).
    """
    filled = payload
    delete = payload.get("form_memory_delete")
    panel = (last_obs.form_memory_panel if last_obs else None) or {}
    if isinstance(delete, dict) and not delete.get("signature") and panel.get("signature"):
        filled = {**payload, "form_memory_delete": {**delete, "signature": panel["signature"]}}
    if str(filled.get("query") or "").strip():
        return filled
    filled = dict(filled)
    if filled.get("selected_db_ids"):
        clarification = (last_obs.clarification if last_obs else None) or {}
        filled["query"] = str(clarification.get("original_query") or last_query)
    elif filled.get("form_fill_answers"):
        filled["query"] = FORM_FILL_REPLY_QUERY
    elif last_query:
        filled["query"] = last_query
    return filled


def summarize_answer(question: Question, body: dict[str, Any]) -> dict[str, Any]:
    """원시 로그에 남길 응답 요약. 무엇을 골랐는지가 판정을 검증하는 재료다."""
    if question.kind == "zone":
        return {"kind": question.payload.get("kind"), "selected_db_ids": body.get("selected_db_ids")}
    if question.kind == "form_fill":
        return {"kind": "form_fill", "fields": sorted((body.get("form_fill_answers") or {}).keys())}
    return {"kind": question.kind, "query": body.get("query")}
