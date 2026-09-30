"""문서 RAG 접속 정보 반입 도구 (plans/126 W3 확장 · 교체 절차 docs/32).

**손으로 옮겨 적지 않기 위한 도구다.** 리트리벌 발급 건마다 엔드포인트·토큰·클라이언트 키·
자산 ID 4종이 모두 바뀌고(plans/126 §3.1), 토큰은 사람이 타이핑할 길이가 아니다. 이 스크립트는
붙여넣기·파일에서 읽어 `.env`에 **안전하게** 쓴다:

  - 4종을 **함께** 쓴다(부분 교체 금지 — §4.1a 1). 하나라도 비면 거부한다.
  - 붙여넣기에 섞인 개행·앞뒤 공백·따옴표를 제거한다(인증 실패의 흔한 원인).
  - `.env` 관례를 지킨다 — 값 뒤 인라인 주석 금지, 기존 줄은 제자리 갱신, 그 외 키 불변.
  - 원자적 저장(임시 파일 → 교체) + 저장 전 백업(`.env.bak-rag-<시각>`).
  - 화면·로그에 **토큰을 찍지 않는다**(끝 4자만).

사용 (저장소 루트에서):

    # 1) 대화형 — 플랫폼 화면에서 복사해 4번 붙여넣기(Enter로 구분)
    python scripts/rag_conn.py set hq_manual

    # 2) 파일 — 값 4줄 또는 `키=값` 형식(메모장에 붙여둔 뒤 그대로 읽힌다)
    python scripts/rag_conn.py set hq_manual --from-file C:\\temp\\hq.txt

    # 3) 확인 — 무엇이 채워졌는지(마스킹)
    python scripts/rag_conn.py show

지원 파일 형식(둘 다 인식):

    http://.../retrieval          |   endpoint=http://.../retrieval
    <토큰>                        |   token=<토큰>
    <클라이언트 키>                |   client_key=<클라이언트 키>
    20260928181958_9c84...        |   retrieval_id=20260928181958_9c84...
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.infrastructure.doc_sources import (  # noqa: E402
    CONNECTION_FIELD_MAP,
    load_collection_meta,
)

ENV_FILE = PROJECT_ROOT / ".env"

#: 필드 → (env 키 접미, 사람이 읽는 이름, 화면에 그대로 보여도 되는가)
FIELD_ORDER: tuple[tuple[str, str, bool], ...] = (
    ("endpoint", "엔드포인트 URL(전문 — 조립하지 말고 그대로)", True),
    ("token", "x-openapi-token(비밀)", False),
    ("client_key", "x-generative-ai-client(비밀)", False),
    ("retrieval_id", "자산 ID(예 20260928181958_9c84…)", True),
)

_ALIASES = {
    "endpoint": {"endpoint", "url", "endpoint_url", "주소"},
    "token": {"token", "openapi_token", "x-openapi-token", "apitoken", "api_token"},
    "client_key": {"client_key", "client", "x-generative-ai-client", "clientkey"},
    "retrieval_id": {"retrieval_id", "retrievalid", "asset_id", "자산id", "자산_id"},
}


def env_key(collection_id: str, field: str) -> str:
    """`RAG_<COLLECTION>_<FIELD>` — 정적 필드 이름과 1:1(§4.12)."""
    return f"RAG_{collection_id.upper()}_{field.upper()}"


def mask(value: str) -> str:
    if not value:
        return "(비어 있음)"
    return f"…{value[-4:]}" if len(value) > 4 else "…"


def clean(raw: str) -> str:
    """붙여넣기 오염 제거 — 개행·탭·앞뒤 공백·감싼 따옴표."""
    v = (raw or "").strip().strip('"').strip("'")
    return re.sub(r"\s+", "", v) if "\n" in v or "\t" in v else v.strip()


def looks_like_endpoint(v: str) -> bool:
    return v.startswith("http://") or v.startswith("https://")


def looks_like_asset_id(v: str) -> bool:
    return bool(re.match(r"^\d{14}_", v))


def parse_blob(text: str) -> dict[str, str]:
    """파일·붙여넣기 덩어리에서 4종을 뽑는다.

    `키=값`(또는 `키: 값`) 형식이 있으면 그것을 쓰고, 없으면 **줄 순서**(엔드포인트 → 토큰 →
    클라이언트 키 → 자산 ID)로 읽는다. 형태로 알 수 있는 둘(URL·자산 ID)은 순서가 어긋나도
    자리를 바로잡는다 — 사람이 순서를 헷갈리는 것이 흔하다.
    """
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    out: dict[str, str] = {}
    leftovers: list[str] = []

    for line in lines:
        m = re.match(r"^([A-Za-z_\-가-힣]+)\s*[=:]\s*(.+)$", line)
        if m:
            name = m.group(1).strip().lower().replace("-", "_")
            value = clean(m.group(2))
            # RAG_HQ_MANUAL_TOKEN=... 형태도 받는다
            for field, names in _ALIASES.items():
                if name in names or name.endswith(f"_{field}"):
                    out[field] = value
                    break
            else:
                leftovers.append(clean(line))
            continue
        leftovers.append(clean(line))

    # 형태로 확정할 수 있는 것부터 채운다.
    for value in list(leftovers):
        if "endpoint" not in out and looks_like_endpoint(value):
            out["endpoint"] = value
            leftovers.remove(value)
        elif "retrieval_id" not in out and looks_like_asset_id(value):
            out["retrieval_id"] = value
            leftovers.remove(value)

    # 남은 것은 순서대로 토큰 → 클라이언트 키.
    for field in ("token", "client_key"):
        if field not in out and leftovers:
            out[field] = leftovers.pop(0)
    return out


def read_env(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def current_values(lines: list[str], collection_id: str) -> dict[str, str]:
    """`.env`에 이미 있는 값(주석 처리된 줄은 «없음»으로 본다)."""
    found: dict[str, str] = {}
    for field, *_ in FIELD_ORDER:
        key = env_key(collection_id, field)
        for line in lines:
            if line.lstrip().startswith("#"):
                continue
            if line.split("=", 1)[0].strip() == key:
                found[field] = line.split("=", 1)[1].strip()
    return found


def apply_values(lines: list[str], collection_id: str, values: dict[str, str]) -> list[str]:
    """기존 줄은 제자리 갱신, 없으면 전용 절에 추가한다. 다른 키는 건드리지 않는다."""
    out = list(lines)
    pending: list[tuple[str, str]] = []
    for field, *_ in FIELD_ORDER:
        key = env_key(collection_id, field)
        value = values[field]
        replaced = False
        for i, line in enumerate(out):
            stripped = line.lstrip()
            name = stripped.lstrip("#").split("=", 1)[0].strip()
            if name != key:
                continue
            out[i] = f"{key}={value}"       # 주석 처리된 줄도 활성화한다
            replaced = True
            break
        if not replaced:
            pending.append((key, value))
    if pending:
        out.append("")
        out.append(f"# 문서 RAG 접속 정보 — {collection_id} (scripts/rag_conn.py · docs/32 §3)")
        out += [f"{k}={v}" for k, v in pending]
    return out


def write_env(path: Path, lines: list[str]) -> Path | None:
    """원자적 저장 + 백업 경로 반환(기존 파일이 없으면 None)."""
    backup = None
    if path.exists():
        backup = path.with_name(f".env.bak-rag-{time.strftime('%Y%m%d-%H%M%S')}")
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    tmp = path.with_name(path.name + ".tmp-rag")
    tmp.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    tmp.replace(path)
    return backup


def known_collections() -> list[str]:
    ids = [c.id for c in load_collection_meta()]
    return [cid for cid in ids if cid in CONNECTION_FIELD_MAP]


def cmd_show(_args: argparse.Namespace) -> int:
    lines = read_env(ENV_FILE)
    if not lines:
        print(f"`.env` 가 없습니다({ENV_FILE}). `set` 으로 만들 수 있습니다.")
    for cid in known_collections():
        cur = current_values(lines, cid)
        filled = [f for f, *_ in FIELD_ORDER if cur.get(f)]
        print(f"\n[{cid}] {len(filled)}/4 입력됨")
        for field, label, visible in FIELD_ORDER:
            value = cur.get(field, "")
            shown = (value or "(비어 있음)") if visible else mask(value)
            print(f"  {env_key(cid, field):34s} {shown}")
    print("\n참고: 4종이 모두 채워져야 그 문서군이 활성화됩니다(부분 입력은 비활성 + 사유).")
    return 0


def _collect_interactive(collection_id: str) -> dict[str, str]:
    print(f"[{collection_id}] 플랫폼 화면의 값을 붙여넣고 Enter 를 누르세요(4회).")
    print("  ※ 화면에 토큰은 표시되지 않습니다. 중단은 Ctrl+C.")
    values: dict[str, str] = {}
    for field, label, _ in FIELD_ORDER:
        while True:
            raw = input(f"  {label}: ")
            value = clean(raw)
            if value:
                values[field] = value
                break
            print("    빈 값은 받지 않습니다(4종을 함께 교체해야 합니다).")
    return values


def cmd_set(args: argparse.Namespace) -> int:
    collection_id = args.collection
    if collection_id not in CONNECTION_FIELD_MAP:
        print(f"모르는 문서군: {collection_id}", file=sys.stderr)
        print(f"  사용 가능: {', '.join(sorted(CONNECTION_FIELD_MAP))}", file=sys.stderr)
        print("  새 문서군은 정본 YAML + RagConfig 필드 + 이 표를 함께 추가합니다(docs/32 §5).",
              file=sys.stderr)
        return 2

    if args.from_file:
        blob = Path(args.from_file).read_text(encoding="utf-8")
        values = parse_blob(blob)
    elif not sys.stdin.isatty():
        values = parse_blob(sys.stdin.read())
    else:
        values = _collect_interactive(collection_id)

    missing = [f for f, *_ in FIELD_ORDER if not values.get(f)]
    if missing:
        print(f"값 4종이 모두 필요합니다 — 누락: {', '.join(missing)}", file=sys.stderr)
        print("  부분 교체는 «다른 자산을 가리키는 조합»이 되어 인증 실패나 오답을 만듭니다.",
              file=sys.stderr)
        return 2

    # 형태 경고(차단하지는 않는다 — 플랫폼 형식이 바뀔 수 있다)
    if not looks_like_endpoint(values["endpoint"]):
        print("경고: 엔드포인트가 http(s):// 로 시작하지 않습니다. 화면의 URL 전문을 확인하세요.")
    if not looks_like_asset_id(values["retrieval_id"]):
        print("경고: 자산 ID 형식이 `YYYYMMDDHHMMSS_...` 와 다릅니다. 순서가 바뀌지 않았는지 확인하세요.")

    lines = read_env(ENV_FILE)
    before = current_values(lines, collection_id)
    occupied = [f for f, *_ in FIELD_ORDER if before.get(f)]
    if occupied and not args.force:
        print(f"[{collection_id}] 이미 값이 있습니다({len(occupied)}/4). 덮어쓰려면 --force 를 붙이세요.")
        for field, _label, visible in FIELD_ORDER:
            old, new = before.get(field, ""), values[field]
            show_old = old if visible else mask(old)
            show_new = new if visible else mask(new)
            mark = "=" if old == new else "→"
            print(f"  {env_key(collection_id, field):34s} {show_old} {mark} {show_new}")
        return 3

    if args.dry_run:
        print(f"[{collection_id}] --dry-run — 쓰지 않았습니다. 적용될 값:")
        for field, _label, visible in FIELD_ORDER:
            v = values[field]
            print(f"  {env_key(collection_id, field):34s} {v if visible else mask(v)}")
        return 0

    backup = write_env(ENV_FILE, apply_values(lines, collection_id, values))
    print(f"[{collection_id}] `.env` 에 4종을 저장했습니다.")
    for field, _label, visible in FIELD_ORDER:
        v = values[field]
        print(f"  {env_key(collection_id, field):34s} {v if visible else mask(v)}")
    if backup:
        print(f"  백업: {backup.name}")
    print("\n다음: RAG_ENABLED=true 확인 후")
    print("  python scripts/rag_probe.py --list")
    print("  python scripts/rag_probe.py --collection %s --query \"…\" --search-only" % collection_id)
    print("  (서버가 돌고 있으면 관리자 화면에서 `설정 반영`으로 재기동 없이 적용됩니다)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="문서 RAG 접속 정보(엔드포인트·토큰·클라이언트 키·자산 ID)를 .env 에 안전하게 쓴다",
        epilog="교체 절차 전문: docs/32_rag_retrieval_runbook.md",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("set", help="문서군 하나의 접속 4종을 저장(붙여넣기·파일·표준입력)")
    s.add_argument("collection", help=f"문서군 id ({', '.join(sorted(CONNECTION_FIELD_MAP))})")
    s.add_argument("--from-file", help="값이 적힌 파일(4줄 또는 키=값)")
    s.add_argument("--force", action="store_true", help="기존 값 덮어쓰기")
    s.add_argument("--dry-run", action="store_true", help="쓰지 않고 적용될 값만 보여준다")
    s.set_defaults(func=cmd_set)

    sh = sub.add_parser("show", help="현재 입력 상태(토큰은 마스킹)")
    sh.set_defaults(func=cmd_show)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\n중단했습니다(쓰지 않았습니다).")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
