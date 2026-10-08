"""치환 코드값 `code_samples.yaml` — 6번째 반출 파일 (plans/140 W2-4 · D-311 ② · G-2·G-4).

P1 이 코드로 판정한 컬럼(`assets.code_values`)과 공통코드 테이블 라벨 쌍(`labels_from:
code_table:…`)을 **형식 보존 치환**해 싣는다. 외부망 쿼리 자동 생성 테스트 자료 전용이다 —
내부망으로 가는 설정 파일에 넣지 않는다(실제 코드와 달라 조건 조회가 틀린다).

- 대상에서 빼는 컬럼: 정책 등급 `pii`·`free_text`·`amount`·`network` · 사람 정보 휴리스틱
  (`pii_suggestion` — D-301 ② 이름 토큰·주석 단어). 사유별 **수만** 싣는다(컬럼 이름도 없음).
- P1 `flag`가 있는 컬럼은 `substitution: flag`(값 없음 — 카탈로그 `profile.flag`가 대신한다).
- 치환: run 의 형식 보존 가짜 값 생성기(`substitute.FakeValues` — `secrets` 난수 · 원값
  내용에서 파생하지 않음 · 대응은 메모리에만)를 쓴다. 숫자→숫자(여러 자리 수의 첫 자리가 0이
  아니면 0이 아니게) · A-Z→A-Z · a-z→a-z · 한글 음절→한글 음절 · 그 밖 문자 유지 · 길이 동일.
  같은 원값(ASCII 대소문자·끝 공백 차이 포함)은 run 안 어디서나 같은 치환값이다 — 같은 생성기를
  받으면 SQL·결과에 나온 코드값과 이 파일의 치환값이 같다. 치환값은 원 코드·라벨 전체 집합과
  casefold 기준으로 다르고(3자 이상 원값을 부분 문자열로 품지도 않는다) 키가 다른 원값끼리 겹치지
  않으며 누출 관문 규칙에 걸리지 않는다 — 어기면
  재추첨(`MAX_DRAWS`). 재추첨이 실패하거나 바꿀 글자가 없는 값이 있으면 그 컬럼은
  `substitution: exhausted`(값·라벨 없이 `distinct`만). 관문은 잎을 줄바꿈으로 이어 붙인 글도
  보므로, 컬럼 값을 이어 붙인 글이 관문 규칙에 걸려도 그 컬럼은 `exhausted`다(관문 완화 금지).
- **짧은 코드 여집합 차단(감사 M-1)** — 값마다 치환 공간(그 값의 길이·문자 군 서열로 만들 수 있는
  값 수)에서 전역 원 코드 집합에 속한 값을 뺀 남은 공간이 그 컬럼 고유값 수의
  `MIN_SPACE_FACTOR`배 미만이면 그 컬럼 전체를 `exhausted`로 낸다 — 공간이 작으면 「치환값이 아닌
  나머지 = 원값」이 되어 치환값의 여집합이 원 집합을 드러낸다(예: 한 자리 숫자 5개).
- 치환 후보가 카탈로그 식별자(테이블·컬럼 이름 — `comments` 키)와 같으면 재추첨한다(빌더 차단이
  식별자를 치환값으로 오검출하지 않게).
- 생성기·원값 집합은 피클을 거부한다(대응 메모·원값 직렬화 차단 · 감사 L-1).
- 빈도·원값·해시는 싣지 않는다. 대응표는 없다(되돌릴 수 없음 — 난수라 run 마다 다르다).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Mapping
from typing import Any

from .catalog import ColumnPolicy, pii_suggestion
from .redact import (
    CODE_SAMPLES_EXCLUDE_REASONS,
    CODE_SAMPLES_EXCLUDED_GRADES,
    CODE_SAMPLES_NOTE,
    CodeOriginals,
)
from .substitute import MIN_SPACE_FACTOR, FakeValues, _pattern, _space

#: 파일 머리 고정 문구(관문이 값 형태로 검증 — 단일 출처는 `redact`)
NOTE = CODE_SAMPLES_NOTE
#: 치환 대상에서 빼는 정책 등급.
EXCLUDED_GRADES: tuple[str, ...] = CODE_SAMPLES_EXCLUDED_GRADES
EXCLUDE_REASONS: tuple[str, ...] = CODE_SAMPLES_EXCLUDE_REASONS


def _space_exhausted(texts: list[str], distinct: int, originals: CodeOriginals) -> bool:
    """한 컬럼의 치환 대상 글 중 하나라도 남은 치환 공간이 `MIN_SPACE_FACTOR × distinct` 미만이면
    True.

    남은 공간 = 그 값의 치환 공간 − 그 공간에 드는 전역 원값 수. 원값 수를 세지 않고도 충분한
    공간(공간 − 원값 전체 수 ≥ 하한)이면 세지 않는다.
    """
    need = MIN_SPACE_FACTOR * max(distinct, 1)
    counted: dict[str, int] = {}
    for text in texts:
        size = _space(text)
        if size - len(originals) >= need:
            continue
        pattern = _pattern(text)
        if pattern not in counted:
            counted[pattern] = originals.count_matching(re.compile(pattern))
        if size - counted[pattern] < need:
            return True
    return False


def original_values(draft: Mapping[str, Any] | None) -> list[str]:
    """P1 초안의 원 코드값·라벨 전체(제외 컬럼 포함) — `CodeOriginals` 재료(메모리 전용)."""
    assets = (draft or {}).get("assets") or {}
    out: list[str] = []
    for values in (assets.get("code_values") or {}).values():
        out += [str(v) for v in values or []]
    for labels in (assets.get("code_labels") or {}).values():
        if isinstance(labels, Mapping):
            out += [str(k) for k in labels] + [str(v) for v in labels.values()]
    return out


def _evidence_index(draft: Mapping[str, Any], section: str) -> dict[str, Mapping[str, Any]]:
    rows = ((draft.get("evidence") or {}).get(section)) or []
    return {str(r.get("key")): r for r in rows if isinstance(r, Mapping) and r.get("key")}


def _exclusion(key: str, policy: ColumnPolicy, comments: Mapping[str, str]) -> str | None:
    table, _, column = key.rpartition(".")
    grade = policy.grade(column, table or None)
    if grade in EXCLUDED_GRADES:
        return grade
    if pii_suggestion(column, comments.get(key) or ""):
        return "person_hint"
    return None


def build_code_samples(
    draft: Mapping[str, Any],
    policy: ColumnPolicy,
    *,
    db_id: str,
    run_id: str,
    comments: Mapping[str, str],
    originals: CodeOriginals,
    reject: Callable[[str], bool],
    fakes: FakeValues | None = None,
) -> dict[str, Any]:
    """`code_samples.yaml` 본문.

    Args:
        draft: P1 자산 초안(메모리 — 원값 포함)
        policy: 컬럼 기록 정책(제외 등급 판정)
        comments: `"table.column"` → 컬럼 의미(사람 정보 휴리스틱의 주석 단어 재료 · 키의
            테이블·컬럼 이름은 치환 후보 재추첨 기준 — 식별자와 같은 치환값을 내지 않는다)
        originals: 원 코드값·라벨 전체 집합(`original_values(draft)`)
        reject: 누출 관문 규칙(코드값 대조 제외) — True 면 그 후보는 재추첨 · 이어 붙인 글 검사
        fakes: run 의 가짜 값 생성기(SQL·결과와 같은 치환값을 내려면 같은 인스턴스). None 이면
            `originals`·`comments` 식별자·`reject`로 이 호출 전용 생성기를 만든다
    """
    assets = draft.get("assets") or {}
    code_values: Mapping[str, Any] = assets.get("code_values") or {}
    code_labels: Mapping[str, Any] = assets.get("code_labels") or {}
    column_evidence = _evidence_index(draft, "columns")
    code_evidence = _evidence_index(draft, "code_columns")

    excluded: Counter[str] = Counter()
    plan: dict[str, tuple[str, list[str], list[tuple[str, str]]]] = {}
    for key in sorted(code_values):
        reason = _exclusion(key, policy, comments)
        if reason is not None:
            excluded[reason] += 1
            continue
        values = sorted({str(v) for v in code_values[key] or []})
        if (column_evidence.get(key) or {}).get("flag"):
            plan[key] = ("flag", values, [])
            continue
        labels_from = str((code_evidence.get(key) or {}).get("labels_from") or "")
        pairs: list[tuple[str, str]] = []
        if labels_from.startswith("code_table:") and isinstance(code_labels.get(key), Mapping):
            pairs = sorted((str(c), str(lb)) for c, lb in code_labels[key].items())
        plan[key] = ("substitute", values, pairs)

    if fakes is None:
        fakes = FakeValues(
            originals=originals,
            identifiers=(part for key in comments for part in str(key).rsplit(".", 1) if part),
            reject=reject,
        )
    targets: set[str] = set()
    for mode, values, pairs in plan.values():
        if mode == "substitute":
            targets.update(values)
            targets.update(text for pair in pairs for text in pair)
    for value in sorted(targets):  # 처리 순서 고정(공간 하한 판정이 입력 순서에 매이지 않게)
        fakes.fake_or_none(value)

    columns: dict[str, Any] = {}
    counts: Counter[str] = Counter()
    for key, (mode, values, pairs) in plan.items():
        entry: dict[str, Any] = {"distinct": len(values)}
        if mode == "flag":
            entry["substitution"] = "flag"
        else:
            new_values = [fakes.fake_or_none(v) for v in values]
            new_pairs = [(fakes.fake_or_none(c), fakes.fake_or_none(lb)) for c, lb in pairs]
            ok = all(v is not None for v in new_values) and all(
                c is not None and lb is not None for c, lb in new_pairs
            )
            sorted_values = sorted(v for v in new_values if v is not None)
            label_rows = sorted([str(c), str(lb)] for c, lb in new_pairs)
            # 치환 공간이 작으면 치환값의 여집합이 원 집합을 드러낸다 — 값 없이 낸다(M-1)
            if ok and _space_exhausted(
                [*values, *(text for pair in pairs for text in pair)], len(values), originals
            ):
                ok = False
            # 관문은 잎을 줄바꿈으로 이어 붙여서도 본다 — 이어 붙인 글이 걸리면 값 없이 낸다
            if ok and (
                reject("\n".join(sorted_values))
                or (label_rows and reject("\n".join(t for row in label_rows for t in row)))
            ):
                ok = False
            if ok:
                entry["substitution"] = "ok"
                entry["values"] = sorted_values
                if label_rows:
                    entry["labels"] = label_rows
            else:
                entry["substitution"] = "exhausted"
        counts[entry["substitution"]] += 1
        columns[key] = entry

    return {
        "db_id": db_id,
        "run_id": run_id,
        "p1_draft_id": draft.get("draft_id"),
        "note": NOTE,
        "summary": {
            "columns": len(code_values),
            "substituted": counts.get("ok", 0),
            "exhausted": counts.get("exhausted", 0),
            "flag": counts.get("flag", 0),
            "excluded": {reason: excluded.get(reason, 0) for reason in EXCLUDE_REASONS},
        },
        "columns": columns,
    }


def column_comments(catalog: Mapping[str, Any]) -> dict[str, str]:
    """카탈로그 → `"table.column"` → 컬럼 의미(없으면 빈 글)."""
    return {
        f"{table}.{col.get('name')}": str(col.get("meaning") or "")
        for table, spec in (catalog.get("tables") or {}).items()
        for col in spec.get("columns") or []
    }


def summary_line(doc: Mapping[str, Any] | None) -> str:
    """리포트 한 줄 요약(수만)."""
    if not doc:
        return "없음 — P1 근거 없음"
    s = doc.get("summary") or {}
    excluded = s.get("excluded") or {}
    reasons = " · ".join(f"{k} {v}" for k, v in excluded.items() if v) or "없음"
    return (
        f"컬럼 {s.get('columns', 0)} · 치환 {s.get('substituted', 0)} · "
        f"고갈 {s.get('exhausted', 0)} · 플래그 {s.get('flag', 0)} · 제외 {reasons}"
    )
