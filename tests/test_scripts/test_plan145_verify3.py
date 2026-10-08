"""plans/145 독립 검증 3차(교정 2차 뒤) — 실제 `run_turn` 클로저 → `finish_turns` → 관문.

- 정규화 불일치 전수: 2차에서만 강등되는 셀(앞 턴 일반 열 + 뒤 턴에서 모은 사람 값)을
  숫자 반올림·앞 0·지수·소수 0·대소문자·비ASCII 대소문자·내부 공백/탭·앞뒤 공백/탭/NBSP·
  NFD·전각 꼴로 넣고, 2차에서 처음 보는 원값(발급 시점에 등록 원값 집합에 없는 키)이
  0 이고 관문을 통과하는지 본다.
- 결함 재현: 결과에 `_source_db` 열이 있으면(per_db) 오라클 쪽 키 태그(DB id)가 `originals()`에
  없어 2차에서 처음 보는 원값이 된다 — 앞서 낸 가짜 값과 같으면 관문 ①이 run 을 막는다.
- 전각 점 IPv4 는 기록 파일에 원문으로 남지 않는다(닫힌 쪽).

실 LLM·DB 0. 가짜 값은 난수라 성질로 단언한다(결정적 재현만 `_draw`를 바꿔 끼운다).
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.itam_bench import POLICY_PATH
from scripts.itam_bench import __main__ as cli
from scripts.itam_bench import catalog as cat
from scripts.itam_bench import judge as jd
from scripts.itam_bench import redact as rd
from scripts.itam_bench import substitute as sb
from scripts.itam_bench.substitute import FakeValues

_USER = {"login_id": "5488923"}
_CATALOG: dict[str, Any] = {
    "tables": {
        "TCDMSIF80": {
            "columns": [{"name": n} for n in ("sevrHostName", "rspblPsnEmnm", "zzzMemo")],
            "relations": [],
            "key": [],
        }
    },
    "same_key_groups": [],
}


@pytest.fixture(scope="module")
def policy() -> cat.ColumnPolicy:
    return cat.load_policy(POLICY_PATH)


class _Tail:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self._items = items

    def mark(self) -> int:
        return 0

    def collect(self, _mark: int, _thread_id: str) -> list[dict[str, Any]]:
        return self._items


def _turn(index: int) -> SimpleNamespace:
    return SimpleNamespace(
        index=index, send={"query": ""}, oracle=None, expect={}, observe=None,
        reply_to=None, query="",
    )  # fmt: skip


def _run(
    policy: cat.ColumnPolicy,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cells: list[Any],
    *,
    per_db: bool = False,
    pin: str | None = None,
    tag: str = "polestar",
    oracle_db: str = "itam",
    column: str = "sevrHostName",
    sql: str = "SELECT sevrHostName FROM TCDMSIF80",
) -> dict[str, Any]:
    """앞 턴: 일반 키 열(`sevrHostName`)에 `cells` + keyset 오라클.

    뒤 턴: 사람 열에서 `박서준` 수집.

    `cells`에 `박서준`을 넣으면 앞 턴 열은 2차에서만 강등된다(1차에는 vault 가 아직 비어 있다).
    """
    vault, fakes, run_policy = cli.start_substitution(
        policy=policy, catalog_doc=_CATALOG, user_values=_USER, p1_draft=None
    )
    first_seen: list[str] = []
    real_issue = FakeValues._issue

    def issue(self: FakeValues, key: str, text: str) -> str | None:
        if sb._fold(key) not in self._seen_equal:
            first_seen.append(key)
        return real_issue(self, key, text)

    monkeypatch.setattr(FakeValues, "_issue", issue)
    if pin is not None:
        draws = iter([pin])
        real_draw = sb._draw
        monkeypatch.setattr(sb, "_draw", lambda segment: next(draws, None) or real_draw(segment))
    outcome = {"status": "ok", "rows_by_db": {oracle_db: [{"sevrHostName": "zz9q"}]}}
    ctx = cli.RunContext(
        run_id="20261008-000000", tier=None, policy=run_policy,
        catalog=jd.CatalogFacts.from_catalog(_CATALOG), vault=vault,
        oracle=lambda *_a: outcome,
    )  # fmt: skip
    first, second = _turn(1), _turn(2)
    first.oracle = {"id": "o1", "compare": "keyset", "key": ["sevrHostName"], "match": "equal"}
    scenario = SimpleNamespace(
        id="S1", category="c", traps=[], turns=[first, second],
        key_columns_for=lambda t: ["sevrHostName"] if t is first else [],
        gold_tables_for=lambda _t: [],
    )  # fmt: skip
    columns = [column, "_source_db"] if per_db else [column]
    rows = [{column: c, **({"_source_db": tag} if per_db else {})} for c in cells]
    results = {
        "q0": {"status": "ok", "columns": columns, "rows": rows,
               "total_rows": len(rows), "truncated": False},
        "q1": {"status": "ok", "columns": ["rspblPsnEmnm"], "rows": [{"rspblPsnEmnm": "박서준"}],
               "total_rows": 1, "truncated": False},
    }  # fmt: skip
    sqls = [sql, "SELECT rspblPsnEmnm FROM TCDMSIF80"]
    pending = []
    for i, (turn, query_id) in enumerate(((first, "q0"), (second, "q1"))):
        obs = SimpleNamespace(
            status="ok", query_id=query_id, response="", db_ids=["itam"], error=None
        )
        client = SimpleNamespace(
            send=lambda *_a, obs=obs: obs, download_csv=lambda qid: results[qid]
        )
        audit = _Tail(
            [{"sql": sqls[i], "source": "itam", "success": True, "row_count": 1,
              "retry_attempt": 0, "error": None}]
        )  # fmt: skip
        pending.append(
            cli.run_turn(
                scenario, turn, 0, "tid", client=client, audit=audit, capture=_Tail([]), ctx=ctx
            )
        )
    records = cli.finish_turns(pending, ctx)
    staged, gate = cli.stage_gated(
        run_meta={"run_id": ctx.run_id, "substitution_note": rd.SUBSTITUTION_NOTE},
        catalog_doc={"tables": {}}, records=records, policy=policy, vault=ctx.vault,
        user_values=_USER, p1_draft=None,
    )  # fmt: skip
    ok, violations = rd.write_gated(tmp_path / "run", staged, gate)
    return {
        "ok": ok, "violations": violations, "collisions": fakes.collisions(),
        "first_seen": first_seen, "record": records[0], "staged": staged,
        "counters": ctx.counters,
    }  # fmt: skip


_NFD = unicodedata.normalize("NFD", "가나다라")


@pytest.mark.parametrize(
    "cell",
    [
        " ab1x", "ab1x\t", " ab1x",  # 앞뒤 공백·탭·NBSP
        "3.14159265", 3.14159265, "00714", "1e3", "1500.0", 12345,  # 수 정규화(`_key_cell`)
        "AbCx", "ÄbcX",  # 대소문자
        "ab  1x", "ab\t1x",  # 내부 공백
        _NFD, "ＡＢ１Ｘ",  # 유니코드 정규화
    ],
)  # fmt: skip
def test_demoted_in_phase_two_has_no_first_seen_original(
    policy: cat.ColumnPolicy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cell: Any
) -> None:
    out = _run(policy, tmp_path, monkeypatch, [cell, "박서준"])
    column = out["record"]["result"]["columns"][0]
    assert column.get("demoted") == "pii_value_match"  # 2차에서 강등됐다(시험 성립)
    assert out["record"]["oracle"]["keys_recorded"] is False
    assert out["first_seen"] == []
    assert out["collisions"] == 0 and out["ok"], out["violations"]


def test_per_db_oracle_tag_is_first_seen_in_phase_two(
    policy: cat.ColumnPolicy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """결함 재현(실패가 정상) — 오라클 쪽 키 태그 `itam`(rows_by_db 의 DB id)은
    결과 셀·오라클 행 셀이 아니라 `originals()`에 없다. 2차에서만 키가 치환되면 처음 보는
    원값이 되고, 앞서 낸 가짜 값이 `itam`이면 관문 ①이 run 을 막는다.
    권고: `originals()`가 `rows_by_db`의 키(DB id)도 내보내거나,
    per_db 태그 자리는 치환하지 않는다(DB id 는 값이 아니다)."""
    out = _run(policy, tmp_path, monkeypatch, ["ab1x", "박서준"], per_db=True, pin="itam")
    assert out["first_seen"] == []
    assert out["collisions"] == 0 and out["ok"], out["violations"]


def test_fullwidth_dot_ip_is_never_written(
    policy: cat.ColumnPolicy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """전각 점 IPv4 는 `ip_key`가 ASCII 점으로 접어 가림·치환한다(교정 3b).

    ASCII IP 와 같은 가짜 값이 되고 run 은 막히지 않는다 — 원문 기록 0.
    """
    raw = "172．31．45．67"
    out = _run(policy, tmp_path, monkeypatch, [raw, "plainx"])
    # 교정 3b 설계 변경: 일반 열 전각 점 IP 는 막지 않고 `ip_key`로 접어 가림·치환한다(팀 리드 지시)
    assert out["ok"], out["violations"]
    assert not (tmp_path / "run").exists() or raw not in "".join(
        p.read_text(encoding="utf-8") for p in (tmp_path / "run").rglob("*") if p.is_file()
    )


# --- 교정 3·3b·3c 재확인 -----------------------------------------------------------------------


def _written(tmp_path: Path) -> str:
    root = tmp_path / "run"
    if not root.exists():
        return ""
    return "".join(p.read_text(encoding="utf-8") for p in root.rglob("*") if p.is_file())


@pytest.mark.parametrize("pin", [None, "srv-host-01"])
def test_unregistered_source_db_alias_is_substituted(
    policy: cat.ColumnPolicy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pin: str | None
) -> None:
    """3c — 생성 SQL 이 호스트명을 `_source_db`로 별칭한 결과: 등록 밖 태그는 치환 칸이고 원문 0."""
    out = _run(
        policy, tmp_path, monkeypatch, ["ab1x", "박서준"], per_db=True, tag="srv-host-01", pin=pin
    )
    assert out["first_seen"] == []
    assert out["collisions"] == 0 and out["ok"], out["violations"]
    detail = out["record"]["oracle"]["detail"]
    assert all(key[0] != "srv-host-01" for key in detail["extra"])
    assert "srv-host-01" not in _written(tmp_path)


@pytest.mark.parametrize("tag", ["polestar", "itam"])
def test_registered_tag_kept_and_gate_passes(
    policy: cat.ColumnPolicy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tag: str
) -> None:
    """Minor-A 해소 — 등록 DB id 태그는 그대로 남고 2차 처음 보는 원값이 없다(고정 추첨 `itam`)."""
    out = _run(policy, tmp_path, monkeypatch, ["ab1x", "박서준"], per_db=True, tag=tag, pin="itam")
    assert out["first_seen"] == []
    assert out["collisions"] == 0 and out["ok"], out["violations"]
    detail = out["record"]["oracle"]["detail"]
    assert [key[0] for key in detail["missing"]] == ["itam"]
    assert {key[0] for key in detail["extra"]} == {tag}


def test_fullwidth_and_ascii_ip_share_one_fake(policy: cat.ColumnPolicy) -> None:
    """A2 조인 — 전각 점 IP 와 ASCII IP 는 같은 가짜 값 · 같은 /24 형제는 접두를 공유한다."""
    vault, _fakes, _policy = cli.start_substitution(
        policy=policy, catalog_doc=_CATALOG, user_values=_USER, p1_draft=None
    )
    ascii_fake = vault.mask_ip("172.31.45.67")
    assert vault.mask_ip("172\uff0e31\uff0e45\uff0e67") == ascii_fake
    assert vault.mask_ip("172\uff0e31.45\u300267") == ascii_fake
    sibling = vault.mask_ip("172.31.45.68")
    assert sibling.rsplit(".", 1)[0] == ascii_fake.rsplit(".", 1)[0] and sibling != ascii_fake


def test_short_compare_number_keeps_join_with_result_sample(
    policy: cat.ColumnPolicy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """결함 재현(실패가 정상 · A2) — Minor-C 로 SQL 비교 자리 `= 12`는 원문으로 남는데 같은 run 의
    사람 열 표본은 `12`를 가짜 값으로 싣는다. 한 레코드에서 조인이 깨지고(SQL `12` ↔ 표본 가짜 값)
    원값↔가짜 값 짝이 드러난다. 권고: 짧은 수 유지는 미분류·근거 없는 조각에만 두거나, 생성기가 짧은
    수를 표본에서도 원문으로 둔다."""
    sql = (
        "SELECT rspblPsnEmpid FROM TCDMSIF80 "
        "WHERE rspblPsnEmpid = 12 OR rspblPsnEmpid = 1234567"
    )
    out = _run(
        policy, tmp_path, monkeypatch, ["12", "1234567"], column="rspblPsnEmpid", sql=sql
    )
    assert out["ok"], out["violations"]
    written_sql = out["record"]["executed_sqls"][0]["sql"]
    shown = out["record"]["result"]["columns"][0]["sample"]
    literals = re.findall(r"rspblPsnEmpid = (\w+)", written_sql)
    assert "1234567" not in literals and literals[1] in shown  # 긴 값은 조인 보존
    assert literals[0] in shown, (literals, shown)


def test_registry_failure_substitutes_registered_tag(
    policy: cat.ColumnPolicy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """3d — 레지스트리를 못 읽으면 `itam` 밖 태그(`polestar`)도 치환 칸이 되고 관문을 통과하며
    run 카운터 `registry_fallback`이 선다(원문 `polestar` 기록 0)."""
    import src.routing.registry as registry

    def broken() -> Any:
        raise registry.RegistryError("probe")

    jd.registered_db_ids_cache_clear()
    monkeypatch.setattr(registry, "get_registry", broken)
    try:
        out = _run(policy, tmp_path, monkeypatch, ["ab1x", "박서준"], per_db=True, tag="polestar")
    finally:
        monkeypatch.undo()
        jd.registered_db_ids_cache_clear()
    assert out["first_seen"] == []
    assert out["collisions"] == 0 and out["ok"], out["violations"]
    assert out["counters"]["registry_fallback"] == 1
    detail = out["record"]["oracle"]["detail"]
    assert all(key[0] != "polestar" for key in detail["extra"])
    assert [key[0] for key in detail["missing"]] == ["itam"]
    assert '"polestar"' not in _written(tmp_path)
