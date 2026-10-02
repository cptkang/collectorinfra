"""문서 코퍼스 정본 로더 (plans/126 §4.1 · W1).

`config/rag_collections.yaml`이 코퍼스의 **의미**(설명·형제 차이·표면어·답변 영역·민감 여부)를
소유하고, **접속 정보**(엔드포인트·토큰·클라이언트 키·자산 ID)는 설정(`RagConfig`)이 소유한다.
문서 추가·수정·재청킹·파라미터 변경마다 자산 ID가 회전하므로(plans/126 §3.1b) 둘을 같은 파일에
두지 않는다 — 회전을 파일 배포 없이 감당하기 위한 분리다.

규율:
- 검색 파라미터(top_k·threshold·rerank·hyde)는 플랫폼 전속이다(§3.3). 최상위에 그런 제어 키가
  있으면 **로드를 거부**한다. `platform_params_snapshot` 하위는 사람이 읽는 기록이라 예외이며,
  코드는 그 값을 필터·제어에 쓰지 않는다(로그·화면 표시 전용).
- 접속 4종 세트 중 하나라도 비면 그 컬렉션을 비활성으로 강등하고 **사유를 남긴다**(침묵 금지).
  부분 설정은 "켜졌다고 착각"의 주 원인이다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_COLLECTIONS_FILE = _PROJECT_ROOT / "config" / "rag_collections.yaml"

#: 최상위에 오면 거부하는 키 — 플랫폼 전속 파라미터의 "제어용 사본" 차단(§3.3 ①).
FORBIDDEN_CONTROL_KEYS: frozenset[str] = frozenset({
    "top_k", "topk", "threshold", "min_rank_score", "rank_threshold",
    "rerank", "rerank_threshold", "hyde", "vector_threshold", "candidates",
})

#: 접속 정보를 이루는 4종 — 항상 세트로 교체된다(§4.1a).
CONNECTION_FIELDS: tuple[str, ...] = ("endpoint", "token", "client_key", "retrieval_id")

#: 컬렉션 id → `RagConfig` 필드명(4종). **동적 `getattr` 을 쓰지 않는다** — 설정 소비가
#: 리터럴로 드러나야 카탈로그·소비 판정(`UNCONSUMED_KEYS`)과 오탈자 검출이 성립한다.
#: 새 코퍼스를 더할 때 `RagConfig` 필드 4개와 이 표를 함께 추가한다(정본 YAML + 설정 + 이 표).
CONNECTION_FIELD_MAP: dict[str, dict[str, str]] = {
    "hq_manual": {
        "endpoint": "hq_manual_endpoint",
        "token": "hq_manual_token",
        "client_key": "hq_manual_client_key",
        "retrieval_id": "hq_manual_retrieval_id",
    },
    "arch_docs": {
        "endpoint": "arch_docs_endpoint",
        "token": "arch_docs_token",
        "client_key": "arch_docs_client_key",
        "retrieval_id": "arch_docs_retrieval_id",
    },
}


@dataclass(frozen=True)
class DocCollection:
    """문서 코퍼스 1개의 의미 + 접속 정보 결합본."""

    id: str
    title: str
    description: str
    sibling_note: str = ""
    answer_domains: tuple[str, ...] = ()
    surface_terms: tuple[str, ...] = ()
    sensitive: bool = False
    enabled: bool = True
    platform_params_snapshot: Mapping[str, Any] = field(default_factory=dict)
    # 접속 4종 — 설정에서 주입된다(정본 YAML에는 없다).
    endpoint: str = ""
    token: str = ""
    client_key: str = ""
    retrieval_id: str = ""
    #: 비활성 사유(활성이면 빈 문자열). 사용자·관리자에게 그대로 보여줄 수 있는 문구다.
    disabled_reason: str = ""

    @property
    def usable(self) -> bool:
        """실제로 호출할 수 있는 상태인지."""
        return self.enabled and not self.disabled_reason

    @property
    def asset_recorded_at(self) -> str:
        """자산 ID 접두에서 읽은 발급 시각(`YYYYMMDDHHMMSS_...` 형식일 때만).

        형식 관찰에 근거한 **추정**이라(plans/126 §3.1b) 사용자 응답에는 쓰지 않고
        관리자 화면·로그에만 쓴다. 형식이 다르면 빈 문자열이다.
        """
        head = self.retrieval_id.split("_", 1)[0]
        if len(head) != 14 or not head.isdigit():
            return ""
        return f"{head[0:4]}-{head[4:6]}-{head[6:8]} {head[8:10]}:{head[10:12]}"

    def masked_connection(self) -> dict[str, str]:
        """로그·화면용 마스킹 사본 — 토큰·클라이언트 키는 끝 4자만 남긴다."""
        return {
            "endpoint": self.endpoint,
            "token": _mask(self.token),
            "client_key": _mask(self.client_key),
            "retrieval_id": self.retrieval_id,
        }


def _mask(secret: str) -> str:
    if not secret:
        return ""
    return f"…{secret[-4:]}" if len(secret) > 4 else "…"


class DocSourcesError(ValueError):
    """정본 파일이 계약을 위반했을 때 — 기동을 멈추게 하는 오류."""


def _as_tuple(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, Sequence):
        return tuple(str(x).strip() for x in raw if str(x).strip())
    return ()


def parse_collections(doc: Mapping[str, Any]) -> list[DocCollection]:
    """정본 매핑을 검증하며 `DocCollection` 목록으로 만든다(접속 정보는 아직 빈 값).

    Raises:
        DocSourcesError: `collections`가 없거나, `id`가 비었/중복이거나,
            최상위에 플랫폼 전속 제어 키가 있을 때.
    """
    raw_items = doc.get("collections")
    if not isinstance(raw_items, list) or not raw_items:
        raise DocSourcesError("collections 목록이 비어 있습니다")

    out: list[DocCollection] = []
    seen: set[str] = set()
    for item in raw_items:
        if not isinstance(item, Mapping):
            raise DocSourcesError(f"collections 항목이 매핑이 아닙니다: {item!r}")
        forbidden = sorted(FORBIDDEN_CONTROL_KEYS & {str(k).lower() for k in item})
        if forbidden:
            raise DocSourcesError(
                f"검색 파라미터는 플랫폼 전속입니다 — 정본에 둘 수 없는 키: {forbidden} "
                "(기록은 platform_params_snapshot 아래에만 · plans/126 §3.3)"
            )
        cid = str(item.get("id") or "").strip()
        if not cid:
            raise DocSourcesError("컬렉션 id가 비어 있습니다")
        if cid in seen:
            raise DocSourcesError(f"컬렉션 id가 중복입니다: {cid}")
        seen.add(cid)

        snapshot = item.get("platform_params_snapshot") or {}
        if not isinstance(snapshot, Mapping):
            raise DocSourcesError(f"{cid}: platform_params_snapshot이 매핑이 아닙니다")

        out.append(DocCollection(
            id=cid,
            title=str(item.get("title") or cid).strip(),
            description=str(item.get("description") or "").strip(),
            sibling_note=str(item.get("sibling_note") or "").strip(),
            answer_domains=_as_tuple(item.get("answer_domains")),
            surface_terms=_as_tuple(item.get("surface_terms")),
            sensitive=bool(item.get("sensitive", False)),
            enabled=bool(item.get("enabled", True)),
            platform_params_snapshot=dict(snapshot),
        ))
    return out


@lru_cache(maxsize=4)
def load_collection_meta(path: str | None = None) -> tuple[DocCollection, ...]:
    """정본 파일을 읽어 의미 정의만 돌려준다(접속 정보 미포함).

    파일이 없으면 빈 튜플이다 — 기능을 쓰지 않는 배포에서 기동을 막지 않는다.
    """
    file_path = Path(path) if path else DEFAULT_COLLECTIONS_FILE
    if not file_path.exists():
        logger.info("문서 코퍼스 정본 없음(%s) — 문서 검색 비활성", file_path)
        return ()
    doc = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
    if not isinstance(doc, Mapping):
        raise DocSourcesError(f"{file_path}: 최상위가 매핑이 아닙니다")
    return tuple(parse_collections(doc))


def _connection_of(rag_config: Any, collection_id: str) -> dict[str, str]:
    """설정에서 이 컬렉션의 접속 4종을 읽는다(`RAG_<ID>_*` 정적 필드).

    `CONNECTION_FIELD_MAP` 에 없는 컬렉션(정본에만 있고 설정 필드가 없는 신규 코퍼스)은 전부
    빈 값이다 — 호출부가 «접속 정보 없음»으로 강등하고 사유를 남긴다(조용히 켜지지 않는다).
    """
    fields = CONNECTION_FIELD_MAP.get(collection_id)
    if not fields:
        return {name: "" for name in CONNECTION_FIELDS}
    return {
        name: str(getattr(rag_config, attr, "") or "").strip()
        for name, attr in fields.items()
    }


def resolve_collections(
    rag_config: Any, *, path: str | None = None
) -> tuple[DocCollection, ...]:
    """정본 의미 + 설정 접속 정보를 결합한다. 불완전한 세트는 사유를 달아 강등한다.

    Args:
        rag_config: `AppConfig.rag`(또는 같은 속성을 가진 객체).
        path: 정본 파일 경로 override(테스트용).
    """
    if path is None:
        configured = str(getattr(rag_config, "collections_file", "") or "").strip()
        path = configured or None
    feature_on = bool(getattr(rag_config, "enabled", False))

    resolved: list[DocCollection] = []
    for meta in load_collection_meta(path):
        conn = _connection_of(rag_config, meta.id)
        missing = [k for k in CONNECTION_FIELDS if not conn[k]]
        # 사유는 **누적**한다 — 기능이 꺼져 있고 접속 정보도 비어 있으면 둘 다 조치 대상이다.
        # 하나만 알리면 켜자마자 다시 막히고, 그때 원인을 다시 찾아야 한다.
        reasons: list[str] = []
        if not feature_on:
            reasons.append("문서 검색 기능이 꺼져 있습니다(RAG_ENABLED=false)")
        if not meta.enabled:
            reasons.append("정본에서 비활성(enabled: false)")
        if missing:
            keys = " · ".join(f"RAG_{meta.id.upper()}_{k.upper()}" for k in missing)
            reasons.append(
                f"접속 정보 미입력 — {keys} "
                "(`python scripts/rag_conn.py set " + meta.id + "` · docs/32 §3)"
            )
        reason = " / ".join(reasons)
        item = DocCollection(
            id=meta.id, title=meta.title, description=meta.description,
            sibling_note=meta.sibling_note, answer_domains=meta.answer_domains,
            surface_terms=meta.surface_terms, sensitive=meta.sensitive,
            enabled=meta.enabled, platform_params_snapshot=meta.platform_params_snapshot,
            disabled_reason=reason, **conn,
        )
        if reason:
            # 침묵 금지 — 왜 안 되는지가 로그 한 줄로 판독돼야 한다.
            logger.warning("문서 코퍼스 '%s' 비활성: %s", meta.id, reason)
        resolved.append(item)
    return tuple(resolved)


def usable_collections(rag_config: Any, *, path: str | None = None) -> tuple[DocCollection, ...]:
    """호출 가능한 컬렉션만."""
    return tuple(c for c in resolve_collections(rag_config, path=path) if c.usable)


def routing_collections(rag_config: Any) -> tuple[DocCollection, ...]:
    """채팅 라우팅 보기로 오를 문서군(plans/127 §4.2 ②) — 정본 `enabled` ∧ **비민감**.

    **의미 정본(YAML)만 본다** — 접속 4종은 자산 회전마다 바뀌므로 여기 넣으면 회전이 분해
    프롬프트 바이트를 바꾼다(KV 캐시). 접속 미입력·폐기는 실행 시 엔진 status 로 드러난다.
    민감 문서군은 1차에서 채팅 보기에 올리지 않는다 — 분해 프롬프트는 전역(기동 시 1회)이라 권한
    밖 사용자의 계획에도 이름이 실린다(D-264 ②).
    """
    configured = str(getattr(rag_config, "collections_file", "") or "").strip()
    return tuple(
        c for c in load_collection_meta(configured or None) if c.enabled and not c.sensitive
    )


def routing_active(rag_config: Any) -> bool:
    """채팅 라우팅 활성 = `RAG_ENABLED` ∧ `RAG_CHAT_ROUTING_ENABLED` ∧ 보기로 오를 문서군 ≥ 1.

    ``is True``로 판정한다 — 설정 대역(MagicMock)의 속성이 참으로 평가돼 켜진 것처럼 동작하지
    않게 한다(비활성 = 바이트 불변).
    """
    if rag_config is None:
        return False
    if getattr(rag_config, "enabled", False) is not True:
        return False
    if getattr(rag_config, "chat_routing_enabled", False) is not True:
        return False
    return bool(routing_collections(rag_config))


def find_collection(
    rag_config: Any, collection_id: str, *, path: str | None = None
) -> DocCollection | None:
    """id로 컬렉션을 찾는다(비활성 포함 — 호출부가 사유를 보여줘야 하므로)."""
    for c in resolve_collections(rag_config, path=path):
        if c.id == collection_id:
            return c
    return None


def startup_summary(rag_config: Any, *, path: str | None = None) -> str:
    """기동·reload 시 1줄로 남길 요약(plans/126 §4.14)."""
    items = resolve_collections(rag_config, path=path)
    ok = [c for c in items if c.usable]
    bad = [c for c in items if not c.usable]
    parts = [f"문서 검색 컬렉션 {len(ok)}/{len(items)} 사용 가능"]
    if ok:
        parts.append(
            "활성: " + " · ".join(
                f"{c.id}({c.asset_recorded_at or 'ID 형식 미상'})" for c in ok
            )
        )
    if bad:
        parts.append("비활성: " + " · ".join(f"{c.id}({c.disabled_reason})" for c in bad))
    return " | ".join(parts)


def strip_surface_prefix(query: str, collections: Sequence[DocCollection]) -> str:
    """질의 머리에 붙은 컬렉션 표면어를 결정적으로 떼어낸다(plans/126 §4.5 질의 규칙 3).

    코퍼스는 `retrieval_id`로 이미 고르므로 "본부매뉴얼에서 …"의 앞부분은 lexical 점수만
    흐린다. **머리에 붙은 것만** 제거한다 — 문장 중간의 용어는 사용자가 의도한 검색어일 수
    있어 건드리지 않는다(용어 보존이 규칙 1이다).
    """
    text = (query or "").strip()
    terms = sorted(
        {t for c in collections for t in c.surface_terms if t},
        key=len, reverse=True,
    )
    changed = True
    while changed:
        changed = False
        for term in terms:
            if not text.startswith(term):
                continue
            rest = text[len(term):].lstrip()
            for particle in ("에서는", "에서", "에는", "의", "는", "은", "에"):
                if rest.startswith(particle):
                    rest = rest[len(particle):].lstrip()
                    break
            if rest:
                text = rest
                changed = True
                break
    return text or (query or "").strip()
