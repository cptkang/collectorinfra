"""plans/120 — 판정 신뢰성(V-1 · V-4)과 단언 교정(U-1~U-7).

근거 run `20260923-140539`(`ladder-1` · 226턴). SQL·응답은 그 run 의 `raw.jsonl` 에서 옮겼다
(주석·공백만 줄였다). 서버도 Redis 도 부르지 않는다 — 폼필 확인 이력은 모의 Redis 로 대신한다.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from scripts.scenario import REPO_ROOT
from scripts.scenario import runner as runner_mod
from scripts.scenario.assertions import (
    INVALID_VERDICT,
    Observation,
    evaluate_turn,
    resolve_db_ids,
    row_db_ids,
)
from scripts.scenario.catalog import CatalogError, load_catalog
from scripts.scenario.client import _apply_done
from scripts.scenario.runner import RawLog, RunConfig

from .conftest import write

OWNER_FORM = REPO_ROOT / "testdata" / "scenarios" / "fixtures" / "adhoc_owner_column.xlsx"
META = {"run_id": "r", "env": "closed", "mode": "run"}
GROUP_HEAD = "version: 1\ngroup: {id: T, name: \"t\", latency_target_ms: 10000}\nscenarios:\n"


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


def _eval(catalog, scenario_id: str, turn_index: int, **obs: Any):
    scenario = catalog.by_id(scenario_id)
    turn = scenario.turns[turn_index - 1]
    return evaluate_turn(scenario, turn_index, turn, Observation(**obs),
                         catalog.groups[scenario.group])


def _keys(verdict) -> list[str]:
    return [f.key for f in verdict.failures]


def _rows(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


class _Client:
    def __init__(self, respond=None) -> None:
        self.sent: list[tuple[str, dict]] = []
        self._respond = respond or (
            lambda _e, _p: Observation(http_status=200, status="completed")
        )

    def send(self, endpoint: str, payload: dict,
             upload: Optional[Path] = None) -> Observation:
        self.sent.append((endpoint, dict(payload)))
        return self._respond(endpoint, payload)

    def download(self, *_a: Any, **_k: Any) -> None:
        return None


def _run(catalog, scenario_id: str, client: _Client, tmp_path: Path, *, mode: str = "run",
         meta: Optional[dict] = None, skipped: Optional[list] = None) -> int:
    return runner_mod._run_once(
        catalog, RunConfig(mode=mode), meta if meta is not None else dict(META), "baseline",
        catalog.by_id(scenario_id), 0, client, RawLog(tmp_path / "raw.jsonl"), tmp_path,
        skipped if skipped is not None else [], preference=[],
    )


# ══════════════════════════════════════════════════════════════════════
# V-1 — `db_ids` 관측 폴백
# ══════════════════════════════════════════════════════════════════════

def test_V1_스코프가_있으면_스코프가_이긴다() -> None:
    assert resolve_db_ids(["polestar_cm_gp"], ["polestar_b0"]) == (["polestar_cm_gp"], "scope")


def test_V1_스코프가_비면_실행_DB_집합으로_본다() -> None:
    sources = ["polestar_cm_yd", "polestar_cm_gp", None, "polestar_cm_yd"]
    assert resolve_db_ids([], sources) == (["polestar_cm_gp", "polestar_cm_yd"], "executed")


def test_V1_둘_다_없으면_출처도_없다() -> None:
    assert resolve_db_ids([], []) == ([], None)


def test_V1_done_스코프는_출처를_scope로_남긴다() -> None:
    obs = Observation()
    _apply_done(obs, {"response": "x", "db_scope": {"db_ids": ["polestar_cm_gp"]}})
    assert (obs.db_ids, obs.db_ids_source) == (["polestar_cm_gp"], "scope")
    empty = Observation()
    _apply_done(empty, {"response": "x", "db_scope": {"db_ids": []}})
    assert (empty.db_ids, empty.db_ids_source) == ([], None)


def _audit(source: str, row_count: Optional[int], success: bool = True) -> dict:
    return {"sql": "SELECT 1", "source": source, "row_count": row_count, "success": success,
            "retry_attempt": 0}


def test_V1_감사_로그로_db_ids를_채우고_단언이_통과한다(catalog) -> None:
    """3단 A-01 — `db_scope` 가 비었지만 SQL 은 여의도(`polestar_cm_yd`)로 나갔다(723행)."""
    obs = Observation(http_status=200, status="completed", row_count=723)
    runner_mod._apply_sql_audit(obs, [_audit("polestar_cm_yd", 723)])
    assert (obs.db_ids, obs.db_ids_source) == (["polestar_cm_yd"], "executed")
    scenario = catalog.by_id("A-01")
    verdict = evaluate_turn(scenario, 1, scenario.turns[0], obs, catalog.groups[scenario.group])
    assert "db_ids" not in _keys(verdict)


def test_V1_실패한_실행도_라우팅으로_센다() -> None:
    """2단 D-08 턴 2 — 은행존 SQL 이 실패해 행 수는 비었지만 보낸 곳은 은행존이다."""
    obs = Observation()
    runner_mod._apply_sql_audit(obs, [_audit("polestar_b0", None, success=False)])
    assert obs.row_counts_by_db == {}
    assert (obs.db_ids, obs.db_ids_source) == (["polestar_b0"], "executed")


def test_V1_스코프가_있으면_감사_로그가_덮지_않는다() -> None:
    obs = Observation(db_ids=["polestar_cm_gp"], db_ids_source="scope")
    runner_mod._apply_sql_audit(obs, [_audit("polestar_b0", 1)])
    assert (obs.db_ids, obs.db_ids_source) == (["polestar_cm_gp"], "scope")


def test_V1_행에_출처_칸을_싣는다(catalog) -> None:
    obs = Observation(db_ids=["polestar_cm_yd"], db_ids_source="executed")
    row = runner_mod._row(dict(META), "baseline", catalog.by_id("A-01"), 1, 0, obs,
                          runner_mod.Verdict())
    assert (row["db_ids"], row["db_ids_source"]) == (["polestar_cm_yd"], "executed")


def test_V1_옛_run_행을_재판정한다() -> None:
    """옛 행에는 `db_ids_source` 칸이 없다 — 러너가 싣는 `executed_sqls[].source` 로 푼다."""
    old = {"db_ids": [], "executed_sqls": [
        {"sql": "SELECT a", "source": "polestar_cm_gp", "row_count": 1578, "success": True},
        {"sql": "SELECT b", "source": "polestar_cm_yd", "row_count": 723, "success": True},
    ]}
    assert row_db_ids(old) == (["polestar_cm_gp", "polestar_cm_yd"], "executed")
    assert row_db_ids({"db_ids": ["polestar_b0"]}) == (["polestar_b0"], "scope")
    already = {"db_ids": ["polestar_cm_gp"], "db_ids_source": "executed", "executed_sqls": []}
    assert row_db_ids(already) == (["polestar_cm_gp"], "executed"), "폴백을 거친 행은 그대로 둔다"


def test_V1_러너_1회전에서_감사_로그로_라우팅을_판정한다(tmp_path: Path, catalog) -> None:
    log = tmp_path / "server.log"
    log.write_text("", encoding="utf-8")

    def respond(_endpoint: str, payload: dict) -> Observation:
        record = {"event": "query_executed", "thread_id": payload["thread_id"], "sql": "SELECT 1",
                  "source_name": "polestar_cm_yd", "row_count": 723, "success": True,
                  "retry_attempt": 0}
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
        return Observation(http_status=200, status="completed", row_count=723)

    runner_mod._run_once(catalog, RunConfig(mode="run"), dict(META), "baseline",
                         catalog.by_id("A-01"), 0, _Client(respond),
                         RawLog(tmp_path / "raw.jsonl"), tmp_path, [], preference=[],
                         sql_tail=runner_mod.SqlAuditTail(log, settle_sec=0.0))
    (row,) = _rows(tmp_path / "raw.jsonl")
    assert (row["db_ids"], row["db_ids_source"]) == (["polestar_cm_yd"], "executed")
    assert "db_ids" not in [f["key"] for f in row["failed_assertions"]]


# ══════════════════════════════════════════════════════════════════════
# V-4 — 폼필 확인 이력 격리 (108·CU-B3)
# ══════════════════════════════════════════════════════════════════════

class FakeFormRedis:
    """`RedisSchemaCache` 의 폼필 이력 메서드 대역. TTL 은 남은 초로 흉내 낸다."""

    def __init__(self) -> None:
        self.data: dict[str, dict] = {}
        self.ttl: dict[str, int] = {}
        self.saves: list[tuple[str, int]] = []

    async def load_form_memory(self, signature: str) -> Optional[dict]:
        return copy.deepcopy(self.data.get(signature))

    async def save_form_memory(self, signature: str, data: dict, ttl_seconds: int) -> bool:
        self.data[signature] = copy.deepcopy(data)
        self.ttl[signature] = ttl_seconds
        self.saves.append((signature, ttl_seconds))
        return True

    async def delete_form_memory(self, signature: str) -> bool:
        self.ttl.pop(signature, None)
        return self.data.pop(signature, None) is not None


FAKE_CONFIG = SimpleNamespace(query=SimpleNamespace(form_memory_ttl_days=7))


@pytest.fixture()
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeFormRedis:
    """러너의 이력 API 호출을 모의 Redis 로 돌린다 — 제품 API(`form_memory`)는 그대로 탄다."""
    from src.schema_cache import form_memory

    fake = FakeFormRedis()

    async def get_redis(_config: Any) -> FakeFormRedis:
        return fake

    monkeypatch.setattr(form_memory, "_get_redis", get_redis)
    monkeypatch.setattr(runner_mod, "_with_form_memory",
                        lambda work: asyncio.run(work(FAKE_CONFIG)))
    return fake


def _entry(value: str = "인프라팀") -> dict:
    return {"action": "literal", "value": value, "confirmed_at": "2026-09-20T10:00:00"}


def _memory(use_count: int, **fields: dict) -> dict:
    return {"display_name": "d", "use_count": use_count, "fields": dict(fields)}


def _asked(*names: str) -> Observation:
    """폼필 역질문 응답 — `names` 를 되물었다."""
    return Observation(http_status=200, status="clarification",
                       form_fill_clarification={"fields": [{"name": n} for n in names]})


def _owner_signature() -> str:
    signature = runner_mod.form_signature_of(OWNER_FORM)
    assert signature
    return signature


def test_V4_양식_시그니처는_서버와_같은_파서로_뜬다() -> None:
    """서버가 저장한 키와 같아야 한다 — 표시 이름이 run 로그의 이력 이름과 같다."""
    from src.document.excel_parser import parse_excel_template
    from src.schema_cache.form_memory import _display_name
    from src.utils.schema_utils import form_signature

    template = parse_excel_template(OWNER_FORM.read_bytes())
    assert runner_mod.form_signature_of(OWNER_FORM) == form_signature(template)
    # run 20260923-140539 server-baseline.log:5107 `'서버명, 호스트명, IP 외 4개 양식' … 사용 42회`
    assert _display_name(template) == "서버명, 호스트명, IP 외 4개 양식"
    csv = REPO_ROOT / "testdata" / "scenarios" / "fixtures" / "not_a_form.csv"
    assert runner_mod.form_signature_of(csv) is None


def test_V4_조회는_TTL과_사용_횟수를_바꾸지_않는다(fake_redis: FakeFormRedis) -> None:
    signature = _owner_signature()
    fake_redis.data[signature] = _memory(42, 담당자=_entry())
    fake_redis.ttl[signature] = 1234

    assert runner_mod.snapshot_form_memory(signature) == ["담당자"]
    assert fake_redis.saves == [], "touch=False 가 아니면 조회가 TTL 을 되돌린다(sliding)"
    assert fake_redis.ttl[signature] == 1234
    assert fake_redis.data[signature]["use_count"] == 42


def test_V4_이_실행이_더한_선언_필드만_지우고_나머지는_남긴다(fake_redis: FakeFormRedis) -> None:
    from src.document.excel_parser import parse_excel_template
    from src.schema_cache.form_memory import save_form_memory_entries

    signature = _owner_signature()
    fake_redis.data[signature] = _memory(5, 비고=_entry("x"))
    before = runner_mod.snapshot_form_memory(signature)
    assert before == ["비고"]

    # 턴 2 — 서버가 `form_fill_remember` 로 `담당자` 를 저장한다(제품 API 그대로).
    template = parse_excel_template(OWNER_FORM.read_bytes())
    answer = {"담당자": {"action": "literal", "value": "인프라팀"}}
    asyncio.run(save_form_memory_entries(template, answer, "q", FAKE_CONFIG))
    # 같은 시각 다른 주체가 같은 양식에 `용도` 를 기억시켰다.
    fake_redis.data[signature]["fields"]["용도"] = _entry("y")

    log = runner_mod.forget_form_memory_additions(signature, before, ["담당자"])

    assert sorted(fake_redis.data[signature]["fields"]) == ["비고", "용도"], \
        "원래 있던 필드·남의 필드는 남는다"
    assert fake_redis.data[signature]["use_count"] == 5
    assert log == [f"삭제 {signature} ['담당자']: 완료",
                   f"남김 {signature} ['용도']: 선언한 필드가 아니다"]


def test_V4_원래_있던_선언_필드는_지우지_않는다(fake_redis: FakeFormRedis) -> None:
    """기준선에 이미 있던 `담당자` 는 이 실행이 더한 것이 아니다 — 선적재 오염이고 사람이 지운다."""
    signature = _owner_signature()
    fake_redis.data[signature] = _memory(1, 담당자=_entry())
    before = runner_mod.snapshot_form_memory(signature)
    log = runner_mod.forget_form_memory_additions(signature, before, ["담당자"])
    assert "담당자" in fake_redis.data[signature]["fields"]
    assert log == [f"대상 없음 {signature}: 기준선 이후 더해진 필드가 없다"]


def test_V4_선적재_이력이_있으면_그_실행의_턴은_무효다(
    tmp_path: Path, fake_redis: FakeFormRedis, catalog
) -> None:
    """I-01 — 이력이 `담당자` 를 채워 역질문 후보가 `['비고']` 뿐이었다(114 진단 · 120 §2.5)."""
    signature = _owner_signature()
    fake_redis.data[signature] = _memory(42, 담당자=_entry())
    fake_redis.ttl[signature] = 999

    client = _Client(lambda _e, _p: _asked("비고"))
    _run(catalog, "I-01", client, tmp_path)

    # 턴을 보내지 않는다 — 보내면 서버 업로드 턴이 이력을 `touch=True` 로 읽어 TTL 을 늘린다
    # (코드 리뷰 2026-09-28 · 벤치가 오염 이력의 수명을 스스로 늘리지 않게).
    assert client.sent == []
    (row,) = _rows(tmp_path / "raw.jsonl")
    assert row["func_verdict"] == INVALID_VERDICT
    assert row["invalid_reason"].startswith(runner_mod.FORM_MEMORY_PRELOAD_REASON)
    assert "['담당자']" in row["invalid_reason"] and signature in row["invalid_reason"]
    assert "턴을 보내지 않았다" in row["invalid_reason"]
    assert row["unevaluated_reason"] == "invalid"
    assert row["failed_assertions"] == []
    assert row["form_memory_preload"] == {"signature": signature, "fields": ["담당자"],
                                          "invalidated": True}
    assert fake_redis.saves == [] and fake_redis.ttl[signature] == 999, "탐지는 읽기 전용이다"


def test_V4_선적재_무효는_뒤_턴을_건너뛰고_사유를_남긴다(
    tmp_path: Path, fake_redis: FakeFormRedis, catalog
) -> None:
    fake_redis.data[_owner_signature()] = _memory(1, 담당자=_entry())
    skipped: list[dict] = []
    client = _Client(lambda _e, _p: _asked("비고"))
    _run(catalog, "I-03", client, tmp_path, skipped=skipped)
    assert client.sent == []
    reasons = [s["reason"] for s in skipped if s.get("turn")]
    assert reasons and all("폼필 확인 이력 선적재" in r for r in reasons)


def test_V4_이력에_의존하지_않는_업로드_시나리오는_무효로_돌리지_않는다(
    tmp_path: Path, fake_redis: FakeFormRedis, catalog
) -> None:
    """키에 사용자 스코프가 없어 실사용자가 표준 양식에 기억시킨 답도 선적재로 보인다 — H군까지
    무효로 돌리면 표적 재측정(H-10~H-15)이 성립하지 않는다(코드 리뷰 2026-09-28). 사실만 남긴다."""
    scenario = catalog.by_id("H-10")
    assert not runner_mod.form_memory_dependent(scenario)
    signature = runner_mod.form_signature_of(REPO_ROOT / scenario.upload)
    assert signature
    fake_redis.data[signature] = _memory(3, 담당자=_entry())
    client = _Client(lambda _e, _p: Observation(http_status=200, status="completed",
                                               has_file=True))
    _run(catalog, "H-10", client, tmp_path)
    assert len(client.sent) >= 1
    rows = _rows(tmp_path / "raw.jsonl")
    assert rows and all(r["func_verdict"] != INVALID_VERDICT for r in rows)
    assert rows[0]["form_memory_preload"] == {"signature": signature, "fields": ["담당자"],
                                              "invalidated": False}


def test_V4_이력_의존_판정은_폼필_역질문_답변_기억_선언만_본다(catalog) -> None:
    dependent = {s.id for s in catalog.scenarios if runner_mod.form_memory_dependent(s)}
    assert {"I-01", "I-02", "I-03", "I-04", "I-05", "I-06"} <= dependent
    assert not any(sid.startswith("H-") for sid in dependent), sorted(dependent)
    # 존 역질문(kind: zone_select)은 이력과 무관하다.
    assert not any(sid.startswith("F-") for sid in dependent), sorted(dependent)


def test_V4_저장_시나리오는_끝나면_자기가_더한_필드를_지운다(
    tmp_path: Path, fake_redis: FakeFormRedis, catalog
) -> None:
    """I-02 턴 2 가 `form_fill_remember` 로 `담당자` 를 저장한다 — 이번 run 이 오염을 안 남긴다."""
    signature = _owner_signature()

    def respond(_endpoint: str, payload: dict) -> Observation:
        if payload.get("form_fill_remember"):
            fake_redis.data[signature] = _memory(0, 담당자=_entry())
            return Observation(http_status=200, status="completed", has_file=True)
        return _asked("담당자", "비고")

    meta = dict(META)
    _run(catalog, "I-02", _Client(respond), tmp_path, meta=meta)

    assert signature not in fake_redis.data, "선언 필드뿐이던 이력은 통째로 지워진다"
    assert meta["teardown_log"] == [{"scenario_id": "I-02", "repeat": 0,
                                     "forget_form_memory": [f"삭제 {signature} ['담당자']: 완료"]}]
    rows = _rows(tmp_path / "raw.jsonl")
    assert rows[0]["func_verdict"] != INVALID_VERDICT
    assert all("teardown_unsupported" not in r for r in rows)


def test_V4_기준선을_못_뜨면_저장_시나리오를_실행하지_않는다(tmp_path: Path, catalog) -> None:
    """conftest 가 Redis 를 「닿지 못함」으로 고정한다."""
    client, skipped = _Client(), []
    assert _run(catalog, "I-02", client, tmp_path, skipped=skipped) == 0
    assert client.sent == []
    assert "폼필 확인 이력 기준선을 뜨지 못해" in skipped[0]["reason"]


def test_V4_Redis에_닿지_못하면_판정을_바꾸지_않는다(tmp_path: Path, catalog) -> None:
    _run(catalog, "I-01", _Client(lambda _e, _p: _asked("담당자", "비고")), tmp_path)
    (row,) = _rows(tmp_path / "raw.jsonl")
    assert row["func_verdict"] != INVALID_VERDICT
    assert row["form_memory_check"].startswith("미확인 - RuntimeError")


def test_V4_모의_실행은_이력을_건드리지_않는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, catalog
) -> None:
    monkeypatch.setattr(runner_mod, "_with_form_memory",
                        lambda _work: pytest.fail("모의 실행이 Redis 를 쓰면 안 된다"))
    _run(catalog, "I-02", _Client(), tmp_path, mode="mock")
    assert len(_rows(tmp_path / "raw.jsonl")) == 2


def test_V4_카탈로그의_저장_시나리오는_전부_격리를_선언한다(catalog) -> None:
    """`form_fill_remember: true` 를 보내는 시나리오는 자기가 저장한 필드를 지워야 한다."""
    writers = {s.id for s in catalog.scenarios
               if any(t.send.get("form_fill_remember") for t in s.turns)}
    assert writers == {"I-02", "I-03", "I-04", "I-05", "I-06"}
    for scenario_id in sorted(writers):
        scenario = catalog.by_id(scenario_id)
        assert "forget_form_memory" in scenario.teardown, scenario_id
        remembered = {name for t in scenario.turns if t.send.get("form_fill_remember")
                      for name in t.send.get("form_fill_answers") or {}}
        assert remembered <= set(scenario.forget_form_fields), scenario_id
        assert runner_mod._teardown(scenario) == [], f"{scenario_id} 가 미지원 teardown 을 남겼다"


@pytest.mark.parametrize("body,message", [
    ("    teardown: [drop_thread, forget_form_memory]\n    upload: \"a.xlsx\"\n",
     "T-01: teardown forget_form_memory 에는 지울 필드 목록 forget_form_fields 가 필요하다"),
    ("    forget_form_fields: [\"담당자\"]\n",
     "T-01: forget_form_fields 는 teardown forget_form_memory 와 함께만 쓴다"),
    ("    teardown: [forget_form_memory]\n    forget_form_fields: [\"담당자\"]\n",
     "T-01: teardown forget_form_memory 는 upload(양식 파일)가 있어야 한다"),
])
def test_V4_선언_없는_forget_form_memory는_로더가_거부한다(
    scenario_dir: Path, profiles_path: Path, body: str, message: str
) -> None:
    write(scenario_dir / "t.yaml", GROUP_HEAD
          + "  - id: T-01\n    plans: [120]\n    title: \"t\"\n" + body
          + "    turns: [{send: {query: \"a\"}, expect: {}}]\n")
    with pytest.raises(CatalogError) as exc:
        load_catalog(scenario_dir, profiles_path)
    assert any(error.startswith(message) for error in exc.value.errors), exc.value.errors


# ══════════════════════════════════════════════════════════════════════
# U-1 ~ U-7 — 단언·카탈로그 교정 (run 실측 SQL·응답으로 검증)
# ══════════════════════════════════════════════════════════════════════

#: B-03 김포·여의도 SQL(2단) — HAVING 피벗의 장비명 필터 · 1행.
B03_NAME_SQL = (
    "-- 서버명 'cocm-hdkapp01'\nSELECT MAX(CASE WHEN c.resource_type = 'server.Server' "
    "THEN c.hostname END) AS hostname,\n  MAX(CASE WHEN c.resource_type = 'server.Server' "
    "AND cc.name = 'OSType' THEN cc.stringvalue_short END) AS os_type\n"
    "FROM polestar.cmm_resource c LEFT JOIN polestar.core_config_prop cc "
    "ON c.resource_conf_id = cc.configuration_id\nWHERE c.dtime IS NULL\n"
    "GROUP BY COALESCE(c.platform_resource_id, c.id)\n"
    "HAVING MAX(CASE WHEN c.resource_type = 'server.Server' THEN c.name END) = 'cocm-hdkapp01'\n"
    "LIMIT 10000;"
)
#: 같은 턴의 은행존 SQL(2단) — 호스트명 필터 · 0행. EAV `cc.name = 'OSType'` 이 함께 있다.
B03_HOSTNAME_HAVING_SQL = B03_NAME_SQL.replace(
    "THEN c.name END) = 'cocm-hdkapp01'", "THEN c.hostname END) = 'cocm-hdkapp01'")
#: G-01 턴 1 SQL — 김포·여의도는 `r.name =`, 은행존은 `r.hostname =`(0행).
G01_NAME_SQL = (
    "SELECT COALESCE(r.name, r.hostname) AS server_name, cc.stringvalue_short AS os_type\n"
    "FROM polestar.cmm_resource r LEFT JOIN polestar.core_config_prop cc\n"
    "  ON r.resource_conf_id = cc.configuration_id AND cc.name = 'OSType'\n"
    "WHERE r.resource_type = 'server.Server' AND r.dtime IS NULL\n"
    "  AND r.name = 'cocm-hdkapp01'   -- 장비명 필터\nLIMIT 10000;"
)
G01_HOSTNAME_SQL = (
    "SELECT r.hostname, cc.stringvalue_short AS os_type FROM POLESTAR.cmm_resource r\n"
    "LEFT JOIN POLESTAR.core_config_prop cc ON r.resource_conf_id = cc.configuration_id "
    "AND cc.name = 'OSType'\nWHERE r.resource_type = 'server.Server'\n"
    "  AND r.hostname = 'cocm-hdkapp01' AND r.dtime IS NULL\nFETCH FIRST 10000 ROWS ONLY;"
)
FANOUT = {"polestar_b0": 0, "polestar_cm_gp": 1, "polestar_cm_yd": 0}
DONE = {"http_status": 200, "status": "completed"}


@pytest.mark.parametrize("scenario_id,name_sql,hostname_sql", [
    ("B-03", B03_NAME_SQL, B03_HOSTNAME_HAVING_SQL),
    ("G-01", G01_NAME_SQL, G01_HOSTNAME_SQL),
])
def test_U1_장비명_필터는_통과하고_호스트명_필터만으로는_불합격이다(
    catalog, scenario_id: str, name_sql: str, hostname_sql: str
) -> None:
    passed = _eval(catalog, scenario_id, 1, **DONE, executed_sqls=[hostname_sql, name_sql],
                   row_count=1, row_counts_by_db=FANOUT)
    assert passed.failures == []
    assert not any("row_count 는 단일 DB 턴 전용" in note for note in passed.manual_notes), \
        "팬아웃 합계는 row_count_total 로 본다"
    failed = _eval(catalog, scenario_id, 1, **DONE, executed_sqls=[hostname_sql],
                   row_count=0, row_counts_by_db={"polestar_b0": 0})
    assert "sql_must_match" in _keys(failed) and "row_count_total.eq" in _keys(failed)


def test_U1_EAV_속성명_name_필터에_걸리지_않는다(catalog) -> None:
    """`\\bname\\s*=` 만 보면 `cc.name = 'OSType'` 이 거짓 통과시킨다 — 값까지 묶는다."""
    verdict = _eval(catalog, "B-03", 1, **DONE, executed_sqls=[B03_HOSTNAME_HAVING_SQL],
                    row_count=1)
    assert "sql_must_match" in _keys(verdict)


#: B-09 2단 김포 SQL — LOB 우선 COALESCE · 1행.
B09_LOB_FIRST = "COALESCE(cc.stringvalue, cc.stringvalue_short)"
B09_COALESCE_SQL = (
    f"SELECT r.name AS server_name, {B09_LOB_FIRST} AS os_parameter\n"
    "FROM polestar.cmm_resource r JOIN polestar.core_config_prop cc\n"
    "  ON r.resource_conf_id = cc.configuration_id AND cc.name = 'OSParameter'\n"
    "WHERE r.resource_type = 'server.Server' AND r.name = 'cocm-xgzapp09'\n"
    "  AND r.dtime IS NULL\nLIMIT 10000;"
)


def test_U2_LOB_우선_COALESCE는_통과한다(catalog) -> None:
    verdict = _eval(catalog, "B-09", 1, **DONE, executed_sqls=[B09_COALESCE_SQL], row_count=1)
    assert verdict.failures == [] and verdict.forbidden_mode is None


@pytest.mark.parametrize("value_expr,key", [
    ("cc.stringvalue_short", "sql_must_match"),
    ("COALESCE(cc.stringvalue_short, cc.stringvalue)", "sql_must_not_match"),
])
def test_U2_짧은_값만_또는_먼저_읽으면_불합격이다(catalog, value_expr: str, key: str) -> None:
    sql = B09_COALESCE_SQL.replace(B09_LOB_FIRST, value_expr)
    verdict = _eval(catalog, "B-09", 1, **DONE, executed_sqls=[sql], row_count=1)
    assert key in _keys(verdict)


#: C-11 2단 SQL — CTE 로 전체 평균(475행).
C11_CTE_SQL = (
    "WITH overall_cpu AS (\n  /* 전체 서버 평균 */\n"
    "  SELECT ROUND(AVG(s.avg_val)::numeric, 2) AS avg_cpu\n"
    "  FROM polestar.cmm_metric_stat_m s JOIN polestar.cmm_resource r ON s.resource_id = r.id\n"
    "  WHERE s.stat_date = '202606'\n)\n"
    "SELECT svr.name, ROUND(AVG(s.avg_val)::numeric, 2) AS cpu_avg\n"
    "FROM polestar.cmm_resource r LEFT JOIN polestar.cmm_metric_stat_m s\n"
    "  ON r.id = s.resource_id AND s.stat_date = '202606'\n"
    "CROSS JOIN overall_cpu\nGROUP BY svr.name, overall_cpu.avg_cpu\n"
    "HAVING ROUND(AVG(s.avg_val)::numeric, 2) > overall_cpu.avg_cpu\nLIMIT 10000;"
)
#: C-11 3단 SQL — 파생 테이블(476행).
C11_SUBQUERY_SQL = (
    "SELECT svr.name, ROUND(s.avg_val::numeric, 2) AS cpu FROM polestar.cmm_resource cpu\n"
    "JOIN polestar.cmm_metric_stat_m s ON cpu.id = s.resource_id AND s.stat_date = '202606'\n"
    "LEFT JOIN (\n  SELECT AVG(s2.avg_val) AS avg_cpu FROM polestar.cmm_metric_stat_m s2\n"
    "  WHERE s2.stat_date = '202606'\n) overall ON TRUE\n"
    "WHERE s.avg_val > overall.avg_cpu\nLIMIT 10000;"
)


@pytest.mark.parametrize("sql", [C11_CTE_SQL, C11_SUBQUERY_SQL], ids=["CTE", "서브쿼리"])
def test_U3_CTE와_서브쿼리_둘_다_통과한다(catalog, sql: str) -> None:
    verdict = _eval(catalog, "C-11", 1, **DONE, executed_sqls=[sql], row_count=475)
    assert verdict.failures == []


def test_U3_전체_평균을_따로_계산하지_않으면_불합격이다(catalog) -> None:
    flat = ("SELECT svr.name, AVG(s.avg_val) FROM polestar.cmm_metric_stat_m s "
            "WHERE s.stat_date = '202606' GROUP BY svr.name HAVING AVG(s.avg_val) > 5 LIMIT 10000;")
    verdict = _eval(catalog, "C-11", 1, **DONE, executed_sqls=[flat], row_count=10)
    assert "sql_must_match" in _keys(verdict)


#: H-06 2단 응답 꼬리 — 자동응답 "전 필드 공란"(D-216) 뒤의 결정적 덧붙임.
H06_TABLE = "**조회 결과**\n\n총 1690건이 조회되었습니다.\n\n| 호스트명 |\n|---|\n| cob0-peawao01 |"
H06_ANSWERED = (
    H06_TABLE + "\n\n---\n**[사용자 답변 적용 내역]**\n  - TPMC: 공란 유지 적용\n"
    "  - 도입일자: 공란 유지 적용\n  - 용도: 공란 유지 적용"
)
H06_UNANSWERED = (
    H06_TABLE + "\n\n---\n**[미작성 항목]** 다음 칼럼은 수집 데이터에 해당 항목이 없어 "
    "비워두었습니다(임의 기재 금지):\n  - TPMC\n  - 도입일자\n  - 용도"
)


def _eval_h06_response(catalog, **obs: Any):
    """H-06 1턴을 **`file` 단언을 뺀** 실제 카탈로그 기대값으로 판정한다(U-4 전용).

    U-4 가 고정하는 것은 응답 문구 선택지(`response_must_contain_any`) 판정이다.
    plans/122 H-4 가 H-06 에 `file.empty_columns`(3열 공란)를 더했는데, 이 테스트의 관측은 산출물
    없는 합성 관측이라 `file` 이 「산출물 없음」 불합격이 된다 — U-4 의 관심 밖이다.
    픽스처 산출물을 만들어 넣으면 이 테스트가 xlsx 판정까지 떠안게 되므로
    (그 판정은 tests/test_scenario/test_plan122_judge_file.py 소관) 판정 전에 `file` 키만 뺐다.
    나머지 기대값(status·has_file·선택지)은 카탈로그 그대로라 선택지가 바뀌면 여전히 깨진다.
    """
    scenario = catalog.by_id("H-06")
    turn = scenario.turns[0]
    # 뺄 것이 실제로 있다(카탈로그가 바뀌면 이 전제를 다시 본다).
    assert "file" in turn.expect
    judged = dataclasses.replace(
        turn, expect={key: value for key, value in turn.expect.items() if key != "file"})
    return evaluate_turn(scenario, 1, judged, Observation(**obs), catalog.groups[scenario.group])


@pytest.mark.parametrize("response", [H06_ANSWERED, H06_UNANSWERED], ids=["답변_적용", "미작성"])
def test_U4_미작성_또는_3열_공란_적용_내역이면_통과한다(catalog, response: str) -> None:
    verdict = _eval_h06_response(catalog, **DONE, has_file=True, response=response)
    assert verdict.failures == []


@pytest.mark.parametrize("response", [
    H06_ANSWERED.replace("  - 용도: 공란 유지 적용", "  - 용도: 직접 입력('x') 적용"),
    H06_TABLE,
], ids=["한_열이_공란이_아님", "고지_없음"])
def test_U4_고지가_모자라면_불합격이다(catalog, response: str) -> None:
    verdict = _eval_h06_response(catalog, **DONE, has_file=True, response=response)
    assert _keys(verdict) == ["response_must_contain_any"]


def test_U4_선택지_모양이_틀리면_로더가_거부한다(scenario_dir: Path, profiles_path: Path) -> None:
    expect = "{response_must_contain_any: \"[미작성 항목]\"}"
    write(scenario_dir / "t.yaml", GROUP_HEAD
          + "  - id: T-01\n    plans: [120]\n    title: \"t\"\n"
          + f"    turns: [{{send: {{query: \"a\"}}, expect: {expect}}}]\n")
    with pytest.raises(CatalogError) as exc:
        load_catalog(scenario_dir, profiles_path)
    assert any("response_must_contain_any" in error for error in exc.value.errors)


def test_U5_F03은_존_그룹_상호배타_옵트인_프로파일에서_잰다(catalog) -> None:
    assert catalog.by_id("F-03").profile == "optin_zone_exclusive"
    assert catalog.profiles["optin_zone_exclusive"] == {"ZONE_GROUP_EXCLUSIVE": "true"}
    assert "ZONE_GROUP_EXCLUSIVE" not in catalog.profiles["baseline"], \
        "기본 프로파일은 코드 기본값(false)"
    assert [s.id for s in catalog.scenarios if s.profile == "optin_zone_exclusive"] == ["F-03"]


#: D-03 2단 SQL(알람 결정적 조립)과 3단 SQL(서버 수를 셌다).
D03_ALARM_SQL = (
    "SELECT COUNT(*) AS alarm_count FROM polestar.cmm_alarm a JOIN polestar.cmm_resource res "
    "ON a.resource_id = res.id WHERE res.dtime IS NULL AND a.alarmseverity = 3 "
    "AND a.ctime >= TIMESTAMP '2026-07-01 00:00:00' "
    "AND a.ctime < TIMESTAMP '2026-08-01 00:00:00' LIMIT 1"
)
D03_SERVER_COUNT_SQL = (
    "-- 알람·이벤트 데이터가 스키마에 존재하지 않으므로 전체 서버 수만 반환\n"
    "SELECT COUNT(*) AS server_count FROM polestar.cmm_resource r\n"
    "WHERE r.resource_type = 'server.Server' AND r.dtime IS NULL\nLIMIT 10000;"
)


def test_U6_알람_테이블을_세면_통과하고_서버_수를_세면_불합격이다(catalog) -> None:
    per_db = {"polestar_cm_gp": 1}
    ok = _eval(catalog, "D-03", 1, **DONE, executed_sqls=[D03_ALARM_SQL], row_count=1,
               row_counts_by_db=per_db)
    assert ok.failures == []
    wrong = _eval(catalog, "D-03", 1, **DONE, executed_sqls=[D03_SERVER_COUNT_SQL], row_count=1,
                  row_counts_by_db=per_db)
    assert any(f.key == "sql_must_match" and "cmm_alarm" in str(f.expected)
               for f in wrong.failures)


def test_U7_C06은_G3_확인_전까지_수동_판정이다(catalog) -> None:
    """3단 SQL(일간 통계 집계 · 1,689행)이 불합격이 아니라 보류로 남는다."""
    turn = catalog.by_id("C-06").turns[0]
    assert "sql_must_match" not in turn.expect
    assert "G-3" in turn.expect["manual_review"]
    stat_d = ("SELECT srv.name, ROUND(AVG(st.avg_val)::numeric, 2) FROM polestar.cmm_resource srv "
              "LEFT JOIN polestar.cmm_metric_stat_d st ON st.resource_id = srv.id LIMIT 10000;")
    verdict = _eval(catalog, "C-06", 1, **DONE, executed_sqls=[stat_d], row_count=1689)
    assert verdict.func == "manual"
