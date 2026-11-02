"""사건 기억 — 결정적 지문 유사 조사 · KEDB 부트스트랩 (plans/198 W7 I-8 · D-350 ⑤).

조사 저장소 레코드(조사 id별 최신 행)와 운영 「실제 원인」 라벨(W0 — 조사 id → 최신 라벨)만 입력으로
받는다. 임베딩·LLM을 쓰지 않고, 표준 라이브러리만 의존한다(저장소 읽기는 호출부가 한다).

- **지문**: 레코드마다 토큰 집합 = 조사 종류(`kind`) · 플레이북 id · 성립한 판독 enum 묶음
  (`이름=값` — 판정 불가 `n/a`·빈 값 제외) · 지지 가설 id(플레이북 가설 규칙 id) · 호스트군
  (범위 `host`의 끝 숫자·구분자 제거)/서비스군(범위 `targets[].instance` — 없으면
  `alarm_instance` — 를 같은 방식으로 정규화). 같은 입력이면 같은 지문이다.
- **유사 조사**: 지문 토큰 집합의 Jaccard 유사도로 상위 3건(동점은 최신 · 조사 id 순). 완료
  조사만 후보이고 자기 자신은 뺀다. 조사 종류 외 토큰이 없는 지문(플레이북 없는 조사 등)은
  비교하지 않고, 조사 종류 외 공통 토큰이 1개 이상인 건만 후보다(plans/198 교정 — 종류 토큰
  하나만으로 무관한 조사가 1.0이 되는 축퇴 방지).
- **결론 노출**: 라벨이 붙은 조사만 결론을 보인다. 라벨이 없으면 결론 없이 「확정 전」이다
  (오염 자기강화 방지 — 조사 엔진 결론을 확정 원인처럼 되풀이하지 않는다).
- **KEDB 부트스트랩**: 같은 지문 + 같은 확정 원인(라벨이 가리킨 가설 · 목록에 없음이면 자유
  서술)이 N회 쌓이면 초안 후보 1건을 낸다. 초안 상태·승격은 호출부의 기존 KB 초안 흐름(`wip` ·
  승격은 사람)을 그대로 쓴다.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from typing import Any

from noise_gate.domain.investigation_accuracy import (
    CAUSE_VERDICTS,
    JUDGED_VERDICTS,
    OUTCOME_DONE,
    VERDICT_NOT_LISTED,
    label_rank,
    outcome_of,
)

#: 유사 조사 노출 건수.
SIMILAR_LIMIT = 3
#: 유사 조사로 보는 Jaccard 하한 [제안 값] — 조사 종류 토큰 하나만 겹치는 건을 거른다.
MIN_JACCARD = 0.3
#: KEDB 부트스트랩 문턱 기본값(같은 지문 + 같은 확정 원인 횟수 · plans/198 I-8 ③).
KEDB_MIN_COUNT_DEFAULT = 3

#: 지지 가설 상태(플레이북 가설 규칙 충족).
_SUPPORTED = "supported"
#: 판정 불가 enum — 성립한 판독이 아니다.
_NOT_APPLICABLE = "n/a"
_HOST_TAIL = re.compile(r"[\d\-_.]+$")
_SPACES = re.compile(r"\s+")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _norm(text: str) -> str:
    return _SPACES.sub(" ", text).strip().lower()


def _playbook(rec: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(_mapping(rec.get("briefing")).get("playbook"))


def _readings(playbook: Mapping[str, Any]) -> Mapping[str, Any]:
    """판독 평탄 맵 — 브리핑 계약(이름 → enum)과 `values` 하위 맵 모양을 함께 받는다."""
    readings = _mapping(playbook.get("readings"))
    values = readings.get("values")
    return values if isinstance(values, Mapping) else readings


def host_group(host: str) -> str:
    """호스트군 — 소문자 · 끝 숫자·구분자 제거(`web01` → `web`). 다 지워지면 원래 소문자."""
    lowered = _text(host).lower()
    return _HOST_TAIL.sub("", lowered) or lowered


def _hypotheses(rec: Mapping[str, Any]) -> list[tuple[int, Mapping[str, Any]]]:
    """`(순위, 가설)` 목록 — 순위가 없으면 목록 위치(1부터)."""
    raw = _mapping(rec.get("briefing")).get("root_cause_hypotheses")
    out: list[tuple[int, Mapping[str, Any]]] = []
    for pos, item in enumerate(raw if isinstance(raw, list) else [], start=1):
        hyp = _mapping(item)
        if not hyp:
            continue
        rank = hyp.get("rank")
        if isinstance(rank, int) and not isinstance(rank, bool) and rank >= 1:
            pos = rank
        out.append((pos, hyp))
    return out


def _hypothesis_id(rec: Mapping[str, Any], hyp: Mapping[str, Any]) -> str:
    """가설 id — 플레이북 가설은 규칙 id(`reasoning` 자리), 그 밖은 원인 문장 정규화."""
    if _text(_playbook(rec).get("id")) and _text(hyp.get("reasoning")):
        return _text(hyp.get("reasoning"))
    return _norm(_text(hyp.get("cause")))


def fingerprint(rec: Mapping[str, Any]) -> frozenset[str]:
    """조사 레코드의 결정적 지문(토큰 집합 — 모듈 독스트링의 5요소)."""
    tokens = {f"kind:{_text(rec.get('kind')) or 'alarm'}"}
    pb = _playbook(rec)
    playbook_id = _text(pb.get("id"))
    if playbook_id:
        tokens.add(f"pb:{playbook_id}")
    for name, value in _readings(pb).items():
        if isinstance(value, str) and value and value != _NOT_APPLICABLE:
            tokens.add(f"rd:{name}={value}")
    if playbook_id:
        for _rank, hyp in _hypotheses(rec):
            if hyp.get("status") == _SUPPORTED and _text(hyp.get("reasoning")):
                tokens.add(f"hyp:{_text(hyp.get('reasoning'))}")
    scope = _mapping(pb.get("scope"))
    group = host_group(_text(scope.get("host")))
    if group:
        tokens.add(f"hg:{group}")
    for instance in _instances(scope):
        tokens.add(f"svc:{host_group(instance)}")
    return frozenset(tokens)


def _instances(scope: Mapping[str, Any]) -> list[str]:
    """범위의 대상 인스턴스명 — `targets[].instance`, 없으면 `alarm_instance`.

    sre_agent 범위 계약(`RunScope.to_dict`) 모양이다.
    """
    raw = scope.get("targets")
    names = [
        _text(_mapping(t).get("instance"))
        for t in (raw if isinstance(raw, list) else [])
    ]
    names = [n for n in names if n]
    if not names and _text(scope.get("alarm_instance")):
        names = [_text(scope.get("alarm_instance"))]
    return names


def _distinctive(tokens: frozenset[str]) -> frozenset[str]:
    """조사 종류 토큰을 뺀 지문 — 유사 판단의 실질 토큰."""
    return frozenset(t for t in tokens if not t.startswith("kind:"))


def fingerprint_id(tokens: Iterable[str]) -> str:
    """지문 토큰 집합의 고정 id(정렬 후 sha256 앞 16자)."""
    return hashlib.sha256("\n".join(sorted(tokens)).encode("utf-8")).hexdigest()[:16]


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """집합 유사도 |A∩B| / |A∪B| (둘 다 비면 0)."""
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _confirmed(
    rec: Mapping[str, Any], label: Mapping[str, Any] | None
) -> tuple[str, str] | None:
    """라벨이 확정한 원인 `(키, 문장)` — 판정 라벨이 원인을 가리킬 때만.

    1순위·k순위 맞음은 그 순위 가설(키 = 가설 id), 목록에 없음은 자유 서술(키 = 정규화 서술 ·
    서술이 없으면 None). 판단 불가·라벨 없음·순위 가설 부재는 None.
    """
    if label is None or label.get("verdict") not in JUDGED_VERDICTS:
        return None
    if label.get("verdict") == VERDICT_NOT_LISTED:
        note = _text(label.get("note"))
        return (f"note:{_norm(note)}", note) if note else None
    rank = label_rank(label)
    for hyp_rank, hyp in _hypotheses(rec):
        if hyp_rank == rank:
            key = _hypothesis_id(rec, hyp)
            return (f"hyp:{key}", _text(hyp.get("cause")) or key) if key else None
    return None


def confirmed_cause(
    rec: Mapping[str, Any], label: Mapping[str, Any] | None
) -> str | None:
    """라벨이 확정한 원인 문장(없으면 None — 화면은 결론을 보이지 않는다)."""
    found = _confirmed(rec, label)
    return found[1] if found else None


def similar_investigations(
    target: Mapping[str, Any],
    records: Iterable[Mapping[str, Any]],
    labels: Mapping[str, Mapping[str, Any]],
    *,
    limit: int | None = SIMILAR_LIMIT,
) -> list[dict[str, Any]]:
    """대상 조사와 지문이 비슷한 완료 조사(점수 내림차순 · 동점은 최신 먼저 · 조사 id 순).

    항목: `{investigation_id, kind, ts, score, labeled, verdict, rank, conclusion}`. 라벨이 없으면
    `labeled=False`이고 `verdict`·`rank`·`conclusion`은 None이다(결론 비노출). 판단 불가 라벨은
    `labeled=True`·결론 None이다. `limit=None`이면 전부(호출부가 인가로 거른 뒤 자른다).
    """
    target_id = str(target.get("investigation_id") or "")
    base = fingerprint(target)
    base_core = _distinctive(base)
    if not base_core:
        return []
    scored: list[tuple[float, str, str, Mapping[str, Any]]] = []
    for rec in records:
        rid = str(rec.get("investigation_id") or "")
        if not rid or rid == target_id or outcome_of(rec) != OUTCOME_DONE:
            continue
        tokens = fingerprint(rec)
        if not base_core & _distinctive(tokens):
            continue
        score = jaccard(base, tokens)
        if score < MIN_JACCARD:
            continue
        scored.append((score, str(rec.get("ts") or ""), rid, rec))
    scored.sort(key=lambda s: s[2])
    scored.sort(key=lambda s: s[1], reverse=True)  # 안정 정렬 — ISO 시각 사전순 = 시간순
    scored.sort(key=lambda s: s[0], reverse=True)
    out: list[dict[str, Any]] = []
    for score, ts, rid, rec in scored if limit is None else scored[:limit]:
        label = labels.get(rid)
        labeled = label is not None and label.get("verdict") in CAUSE_VERDICTS
        out.append({
            "investigation_id": rid,
            "kind": _text(rec.get("kind")) or "alarm",
            "ts": ts or None,
            "score": round(score, 4),
            "labeled": labeled,
            "verdict": label.get("verdict") if labeled and label else None,
            "rank": label_rank(label) if labeled and label else None,
            "conclusion": confirmed_cause(rec, label) if labeled else None,
        })
    return out


def kedb_candidate(
    target: Mapping[str, Any],
    records: Iterable[Mapping[str, Any]],
    labels: Mapping[str, Mapping[str, Any]],
    *,
    min_count: int = KEDB_MIN_COUNT_DEFAULT,
) -> dict[str, Any] | None:
    """KEDB 부트스트랩 후보 — 같은 지문 + 같은 확정 원인인 완료 조사가 `min_count`건 이상일 때.

    반환: `{key, fingerprint_id, cause_key, cause, count, investigation_ids}`(조사 id 정렬). `key`는
    (지문, 원인)의 고정 id라 호출부가 중복 초안을 막는 데 쓴다. 대상에 확정 원인이 없거나 문턱
    미달이면 None.
    """
    target_id = str(target.get("investigation_id") or "")
    found = _confirmed(target, labels.get(target_id))
    if found is None:
        return None
    cause_key, cause = found
    tokens = fingerprint(target)
    ids: set[str] = set()
    for rec in records:
        rid = str(rec.get("investigation_id") or "")
        if not rid or outcome_of(rec) != OUTCOME_DONE or fingerprint(rec) != tokens:
            continue
        other = _confirmed(rec, labels.get(rid))
        if other is not None and other[0] == cause_key:
            ids.add(rid)
    if len(ids) < max(1, int(min_count)):
        return None
    fp_id = fingerprint_id(tokens)
    return {
        "key": fingerprint_id((fp_id, cause_key)),
        "fingerprint_id": fp_id,
        "cause_key": cause_key,
        "cause": cause,
        "count": len(ids),
        "investigation_ids": sorted(ids),
    }


__all__ = [
    "KEDB_MIN_COUNT_DEFAULT",
    "MIN_JACCARD",
    "SIMILAR_LIMIT",
    "confirmed_cause",
    "fingerprint",
    "fingerprint_id",
    "host_group",
    "jaccard",
    "kedb_candidate",
    "similar_investigations",
]
