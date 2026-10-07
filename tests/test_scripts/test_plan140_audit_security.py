"""plans/140 감사(검증 + 독립 보안 감사 1라운드) — 치환·누출 관문·빌더 차단 (D-311 ②③).

W2 구현 테스트(`test_plan140_w2_export.py`)와 겹치지 않게 **성질(property)·우회 경로·종단
카나리아**를
본다. 감사가 찾은 결함(M-1·L-1·L-2·L-3·L-5·L-6)의 재현은 교정 뒤 일반 통과 테스트로 남겼다.

실 Redis·DB·LLM 0. 픽스처 값은 전부 합성이다(반출 원본을 읽지 않는다).
"""

from __future__ import annotations

import json
import pickle
import random
import string
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.itam_bench import __main__ as cli
from scripts.itam_bench import build_assets as ba
from scripts.itam_bench import catalog as cat
from scripts.itam_bench import code_samples as cs
from scripts.itam_bench import redact as rd

# --- 공통 -----------------------------------------------------------------------------


def _policy(**tables: dict[str, str]) -> cat.ColumnPolicy:
    return cat.ColumnPolicy(
        db_id="itam", scope="closed", tables=tables, canary_literals=("감사카나리아",)
    )


def _never(_text: str) -> bool:
    return False


def _draft(code_values: dict[str, list[str]], **extra: Any) -> dict[str, Any]:
    evidence: dict[str, Any] = {"columns": [], "code_columns": []}
    evidence.update(extra.pop("evidence", {}))
    assets: dict[str, Any] = {"code_values": code_values, "code_labels": {}}
    assets.update(extra.pop("assets", {}))
    return {"draft_id": "audit0000001", "assets": assets, "evidence": evidence, **extra}


def _build(draft: dict[str, Any], policy: cat.ColumnPolicy | None = None) -> dict[str, Any]:
    return cs.build_code_samples(
        draft,
        policy or _policy(),
        db_id="itam",
        run_id="audit-run",
        comments={},
        originals=rd.CodeOriginals(cs.original_values(draft)),
        reject=_never,
    )


def _char_class(ch: str) -> str:
    if ch.isdigit():
        return "d"
    if "A" <= ch <= "Z":
        return "U"
    if "a" <= ch <= "z":
        return "l"
    if 0xAC00 <= ord(ch) <= 0xD7A3:
        return "H"
    return ch


# --- 1. 치환기 성질 -------------------------------------------------------------------


class TestSubstitutionProperties:
    """형식 보존 · 원값 불일치(casefold) · 3자 이상 원값 부분 문자열 불포함 · 단사 · 전역 일관 ·
    정렬."""

    @staticmethod
    def _random_codes(rng: random.Random, n: int) -> list[str]:
        alphabet = string.ascii_uppercase + string.digits + "가나다라마바사" + "-_"
        out = set()
        while len(out) < n:
            length = rng.randint(3, 8)
            out.add("".join(rng.choice(alphabet) for _ in range(length)))
        return sorted(out)

    def test_properties_hold_over_random_codes(self) -> None:
        rng = random.Random(140)
        a = self._random_codes(rng, 40)
        b = a[:10] + self._random_codes(rng, 15)  # 앞 10개는 두 컬럼에 같이 있다
        draft = _draft({"t1.상태코드": a, "t2.구분": b})
        doc = _build(draft)
        originals = {v.casefold() for v in a + b}
        long_originals = {o for o in originals if len(o) >= 3}
        for key, values in (("t1.상태코드", a), ("t2.구분", b)):
            entry = doc["columns"][key]
            assert entry["substitution"] == "ok"
            assert entry["values"] == sorted(entry["values"])  # 원값 순서가 새지 않는다
            assert entry["distinct"] == len(values)
            assert "labels" not in entry
        # 치환값마다 형식 보존(문자 군·길이) — 원값과 짝을 몰라도 군 서열 다중집합이 같아야 한다
        for key, values in (("t1.상태코드", a), ("t2.구분", b)):
            got = sorted("".join(_char_class(c) for c in v) for v in doc["columns"][key]["values"])
            want = sorted("".join(_char_class(c) for c in v) for v in values)
            assert got == want
        everything = doc["columns"]["t1.상태코드"]["values"] + doc["columns"]["t2.구분"]["values"]
        for value in everything:
            folded = value.casefold()
            assert folded not in originals
            assert not any(o in folded for o in long_originals)
        # 단사(casefold) — 두 컬럼에 같이 있던 원값 10개는 같은 치환값이라 합집합 크기 = 원값 합집합
        assert len({v.casefold() for v in everything}) == len(set(a) | set(b))
        first = set(doc["columns"]["t1.상태코드"]["values"])
        shared = first & set(doc["columns"]["t2.구분"]["values"])
        assert len(shared) == len(set(a) & set(b))  # 같은 원값 → 같은 치환값(전역)

    def test_multi_digit_leading_digit_stays_nonzero(self) -> None:
        values = [f"{n}" for n in range(1000, 1040)]
        doc = _build(_draft({"t.지역코드": values}))
        entry = doc["columns"]["t.지역코드"]
        assert entry["substitution"] == "ok"
        assert all(v[0] != "0" and len(v) == 4 and v.isdigit() for v in entry["values"])

    def test_new_key_every_run(self) -> None:
        values = [f"K{n:03d}" for n in range(30)]
        draft = _draft({"t.유형": values})
        first = _build(draft)["columns"]["t.유형"]["values"]
        second = _build(draft)["columns"]["t.유형"]["values"]
        assert first != second  # 30개가 run 두 번에 같게 나올 확률은 무시할 만큼 작다

    def test_repr_and_slots_hide_key(self) -> None:
        sub = cs._Substituter(rd.CodeOriginals(["ABC"]), _never)
        assert repr(sub) == "<_Substituter>"
        assert not hasattr(sub, "__dict__")
        assert sub._key.hex() not in repr(sub)
        assert "ABC" not in repr(rd.CodeOriginals(["ABC"]))

    def test_secret_holders_refuse_pickle(self) -> None:
        """감사 L-1 — reject 가 피클 가능한 함수여도 비밀키·대응 메모·원값은 직렬화되지 않는다."""
        sub = cs._Substituter(rd.CodeOriginals(["ABC1"]), bool)
        sub.substitute("XYZ9")
        for protocol in range(pickle.HIGHEST_PROTOCOL + 1):
            with pytest.raises(TypeError):
                pickle.dumps(sub, protocol=protocol)
            with pytest.raises(TypeError):
                pickle.dumps(rd.CodeOriginals(["ABC1"]), protocol=protocol)


# --- 2. 짧은 코드 ----------------------------------------------------------------------


class TestShortCodes:
    def test_full_single_digit_set_is_exhausted(self) -> None:
        doc = _build(_draft({"t.등급": [str(d) for d in range(10)]}))
        entry = doc["columns"]["t.등급"]
        assert entry == {"distinct": 10, "substitution": "exhausted"}

    def test_single_digit_half_set_does_not_reveal_originals(self) -> None:
        """감사 M-1 — 한 자리 숫자 5개: 남은 공간 5 < 2 × 5 → 값 없이 고갈(여집합 노출 차단)."""
        originals = {"1", "2", "3", "4", "5"}
        draft = _draft({"t.등급": sorted(originals)})
        for _ in range(5):  # 확률이 아니라 공간 판정이다 — 매번 같다
            entry = _build(draft)["columns"]["t.등급"]
            assert entry == {"distinct": 5, "substitution": "exhausted"}

    def test_space_counts_global_originals(self) -> None:
        """남은 공간은 **전역** 원 집합으로 센다 — 다른 컬럼의 1자 코드도 공간을 줄인다."""
        draft = _draft({"t.등급": ["1", "2"], "u.구분": ["3", "4", "5", "6", "7"]})
        doc = _build(draft)
        # 공간 10 − 전역 원값 7 = 3 < 2 × 2 → 고갈(컬럼 자기 원값 2개만 보면 8 ≥ 4)
        assert doc["columns"]["t.등급"] == {"distinct": 2, "substitution": "exhausted"}
        assert doc["columns"]["u.구분"] == {"distinct": 5, "substitution": "exhausted"}

    def test_one_small_value_exhausts_whole_column(self) -> None:
        """한 값이라도 공간이 모자라면 컬럼 전체가 고갈이다(긴 값이 섞여도)."""
        draft = _draft({"t.유형": ["7", "8", "9", "K001", "K002"]})
        entry = _build(draft)["columns"]["t.유형"]
        assert entry == {"distinct": 5, "substitution": "exhausted"}

    def test_single_letter_with_room_is_substituted(self) -> None:
        """1자 영문 3개: 남은 공간 26 − 3 = 23 ≥ 2 × 3 → 치환(상수 경계 밖)."""
        assert cs.MIN_SPACE_FACTOR == 2
        entry = _build(_draft({"t.등급": ["A", "B", "C"]}))["columns"]["t.등급"]
        assert entry["substitution"] == "ok" and len(entry["values"]) == 3

    def test_three_char_codes_unaffected(self) -> None:
        """3자 이상 일반 코드는 공간이 넉넉해 고갈되지 않는다."""
        values = [f"S{n:02d}" for n in range(40)] + [f"{n}" for n in range(100, 140)]
        doc = _build(_draft({"t.상태": values[:40], "u.지역": values[40:]}))
        assert doc["columns"]["t.상태"]["substitution"] == "ok"
        assert doc["columns"]["u.지역"]["substitution"] == "ok"
        assert doc["summary"]["exhausted"] == 0

    def test_candidate_equal_to_identifier_redrawn(self) -> None:
        """치환 후보가 카탈로그 식별자(테이블·컬럼 이름)와 같으면 재추첨한다."""
        seen: list[str] = []

        def spy(text: str) -> bool:
            seen.append(text)
            return False

        draft = _draft({"t.등급": ["AB"]})
        comments = {f"tbl.{a}{b}": "" for a in string.ascii_uppercase for b in "ABCDEFGHIJKLM"}
        doc = cs.build_code_samples(
            draft, _policy(), db_id="itam", run_id="audit-run", comments=comments,
            originals=rd.CodeOriginals(cs.original_values(draft)), reject=spy,
        )
        entry = doc["columns"]["t.등급"]
        if entry["substitution"] == "ok":  # 공간의 절반이 식별자 — 32회 안에 나머지를 뽑았을 때
            assert entry["values"][0][1] in "NOPQRSTUVWXYZ"
        identifiers = {k.split(".")[1] for k in comments}
        assert not any(t in identifiers for t in seen)  # 식별자 후보는 관문까지 가지도 않는다


# --- 3. 관문 `code_original` ----------------------------------------------------------


def _gate(originals: list[str]) -> rd.LeakGate:
    policy = _policy()
    return rd.LeakGate(
        policy=policy,
        vault=rd.PiiVault.from_policy(policy),
        user_values={},
        code_originals=rd.CodeOriginals(originals),
    )


def _samples_text(entry: dict[str, Any], **top: Any) -> str:
    doc = {
        "db_id": "itam", "run_id": "r", "note": cs.NOTE, "summary": {}, "columns": {"t.c": entry}
    }
    doc.update(top)
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)


class TestCodeOriginalGate:
    @pytest.mark.parametrize(
        "leaf",
        ["ABC1", "abc1", "xxABC1yy", "가동중", "7"],
    )
    def test_value_and_case_and_substring_caught(self, leaf: str) -> None:
        gate = _gate(["ABC1", "가동중", "7"])
        text = _samples_text({"distinct": 1, "substitution": "ok", "values": [leaf]})
        found = gate.check({rd.CODE_SAMPLES_FILE: text})
        assert any(v["rule"] == "code_original" for v in found)
        assert leaf not in json.dumps(found, ensure_ascii=False)

    def test_label_side_caught(self) -> None:
        gate = _gate(["ABC1", "가동중"])
        text = _samples_text(
            {
                "distinct": 1, "substitution": "ok", "values": ["QQQ9"],
                "labels": [["QQQ9", "가동중"]],
            }
        )
        assert gate.check({rd.CODE_SAMPLES_FILE: text})

    @pytest.mark.parametrize(
        "case",
        ["key", "summary", "structural", "fullwidth"],
    )
    def test_bypass_paths_caught(self, case: str) -> None:
        """감사 L-2 — 매핑 키 · summary 하위 · 구조 칸 · 전각 문자도 걸린다."""
        gate = _gate(["ABC1"])
        if case == "key":
            text = _samples_text({"distinct": 1, "substitution": "ok", "labels_map": {"ABC1": "Q"}})
        elif case == "summary":
            text = _samples_text({"distinct": 1, "substitution": "ok"}, summary={"x": "ABC1"})
        elif case == "structural":
            text = _samples_text({"distinct": 1, "substitution": "ABC1"})
        else:
            text = _samples_text({"distinct": 1, "substitution": "ok", "values": ["ＡＢＣ1"]})
        found = gate.check({rd.CODE_SAMPLES_FILE: text})
        assert any(v["rule"] == "code_original" for v in found)
        assert "ABC1" not in json.dumps(found, ensure_ascii=False)  # 위치에 키(원값)를 싣지 않는다

    @pytest.mark.parametrize(
        ("patch", "ok"),
        [
            ({}, True),
            ({"summary": {"columns": 1, "excluded": {"pii": 0}}}, True),
            ({"summary": {"columns": "1"}}, False),
            ({"summary": {"columns": True}}, False),
            ({"summary": {"excluded": {"unknown": 1}}}, False),
            ({"note": "다른 문구"}, False),
            ({"run_id": "한글 run"}, False),
            ({"p1_draft_id": None}, True),
        ],
    )
    def test_structural_fields_shape_checked(self, patch: dict[str, Any], ok: bool) -> None:
        """구조 칸은 원값 대조 대신 값 형태(정수 · 고정 열거 · 고정 문구 · 식별자 형식)를 본다."""
        gate = _gate(["ABC1"])
        text = _samples_text({"distinct": 1, "substitution": "ok", "values": ["QQQ9"]}, **patch)
        assert (gate.check({rd.CODE_SAMPLES_FILE: text}) == []) is ok

    @pytest.mark.parametrize(
        ("column_key", "ok"),
        [("t_srv.stat_cd", True), ("자산.상태코드", True), ("ABC1 값", False)],
    )
    def test_column_key_identifier_or_compared(self, column_key: str, ok: bool) -> None:
        """컬럼 키는 `table.column` 식별자 형식이면 통과, 아니면 원값 대조."""
        gate = _gate(["ABC1", "stat"])
        doc = {
            "db_id": "itam", "run_id": "r", "note": cs.NOTE, "summary": {},
            "columns": {column_key: {"distinct": 1, "substitution": "exhausted"}},
        }
        text = yaml.safe_dump(doc, allow_unicode=True)
        assert (gate.check({rd.CODE_SAMPLES_FILE: text}) == []) is ok

    def test_generator_output_passes_shape_check(self) -> None:
        """생성기 산출(요약·제외 사유·판정 열거)은 형태 검증을 그대로 지난다."""
        draft = _draft({"t.상태": ["S01", "S02"], "t.등급": ["1"]})
        doc = _build(draft, _policy(t={"memo": "pii"}))
        gate = _gate(cs.original_values(draft))
        text = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)
        assert gate.check({rd.CODE_SAMPLES_FILE: text}) == []

    def test_write_gated_is_all_or_nothing(self, tmp_path: Path) -> None:
        gate = _gate(["ABC1"])
        staged = {
            "run.json": "{}\n",
            "schema_catalog.yaml": "tables: {}\n",
            "trace.jsonl": "",
            "report.md": "# r\n",
            rd.CODE_SAMPLES_FILE: _samples_text(
                {"distinct": 1, "substitution": "ok", "values": ["ABC1"]}
            ),
        }
        ok, violations = rd.write_gated(tmp_path / "run", staged, gate)
        assert not ok and violations
        assert sorted(p.name for p in (tmp_path / "run").iterdir()) == ["leak_check.json"]
        check = (tmp_path / "run" / "leak_check.json").read_text(encoding="utf-8")
        assert "ABC1" not in check and "t.c" not in check

    def test_other_files_keep_five_rules(self) -> None:
        """다른 파일의 관문 기준은 그대로다 — `code_original`은 치환 파일에만 붙는다."""
        gate = _gate(["ABC1"])
        assert gate.check({"report.md": "ABC1\n"}) == []
        assert rd.GATE_RULES[:5] == ("canary", "pii_value", "pii_regex", "schema_form", "user_info")
        assert any(
            v["rule"] == "canary" for v in gate.check({"report.md": "감사카나리아\n"})
        )


# --- 4. 6파일 종단 카나리아 ------------------------------------------------------------

_CANARY_CODES = ["QAZ0001", "QAZ0002", "QAZ0003"]
_CANARY_LABELS = {"QAZ0001": "카나리가동", "QAZ0002": "카나리폐기", "QAZ0003": "카나리수리"}
_CANARY_EXCLUDED = ["PIIVAL001", "AMTVAL001"]
_CANARY_ERROR = "OperationalError: SELECT x FROM t WHERE c='ERRVAL987'"
_CANARY_SKIPPED = "distinct:t_srv.SKIPNAME42"


class _Store:
    def __init__(self, snapshot: dict[str, Any], draft: dict[str, Any]) -> None:
        self._snapshot, self._draft = snapshot, draft

    async def load_snapshot(self, db_id: str) -> dict[str, Any]:
        return self._snapshot

    async def load_ddl_comments(self, db_id: str) -> dict[str, str]:
        return {}

    async def list_asset_drafts(self, db_id: str) -> list[dict[str, Any]]:
        return [self._draft]

    async def get_description_draft(self, db_id: str, draft_id: str) -> None:
        return None


def _canary_store() -> _Store:
    snapshot = {
        "hash": "h1",
        "snapshot": {
            "tables": {
                "t_srv": {
                    "columns": {
                        "srv_id": {"type": "varchar", "nullable": False, "primary_key": True},
                        "stat_cd": {"type": "char", "nullable": True},
                        "owner_cd": {"type": "char", "nullable": True},
                        "amt_cd": {"type": "char", "nullable": True},
                        "use_yn": {"type": "char", "nullable": True},
                    },
                    "foreign_keys": [],
                }
            }
        },
    }
    col = {
        "candidate": "code", "code": True, "distinct": 3, "truncated": False, "total": 3,
        "date8": 0.0, "datetime14": 0.0, "ipv4": 0.0, "hostname": 0.0, "multi_value": 0.0,
        "mixed_case": False, "flag": [], "entity_key": None, "error": None,
    }
    draft = {
        "draft_id": "audit0000002", "kind": "profile", "status": "pending",
        "created_at": "2026-10-07T00:00:00", "engine": "mariadb", "snapshot_hash": "h1",
        "assets": {
            "code_values": {
                "t_srv.stat_cd": list(_CANARY_CODES),
                "t_srv.owner_cd": [_CANARY_EXCLUDED[0]],
                "t_srv.amt_cd": [_CANARY_EXCLUDED[1]],
                "t_srv.use_yn": ["N", "Y"],
            },
            "code_labels": {"t_srv.stat_cd": dict(_CANARY_LABELS)},
        },
        "evidence": {
            "columns": [
                {**col, "key": "t_srv.stat_cd"},
                {**col, "key": "t_srv.owner_cd", "error": _CANARY_ERROR},
                {**col, "key": "t_srv.use_yn", "flag": ["N", "Y"]},
            ],
            "code_columns": [
                {"key": "t_srv.stat_cd", "distinct": 3, "labels": 3,
                 "labels_from": "code_table:t_code"},
            ],
            "relationships": [
                {"child": "t_srv", "parent": None, "child_columns": ["srv_id"],
                 "parent_columns": [], "origin": "name_match", "overlap": None,
                 "sampled": None, "accepted": False, "error": _CANARY_ERROR,
                 "unique_parent": None},
            ],
            "allowed_tables": [{"table": "t_srv", "rows": 10, "comment": None}],
            "budget": {"limit": 400, "used": 9, "skipped": 1,
                       "skipped_sample": [_CANARY_SKIPPED], "requested": 9, "cap": 2000},
        },
    }
    return _Store(snapshot, draft)


def test_six_files_carry_no_p1_originals(tmp_path: Path) -> None:
    policy = _policy(t_srv={"owner_cd": "pii", "amt_cd": "amount"})
    schema = cat.load_schema_source("structure_store", store=_canary_store())
    draft = schema["_p1_draft"]
    catalog = cat.build_schema_catalog(
        schema, policy, assets={"p1": cat.p1_asset(draft)},
        profile={"source": "manual", "allowed_tables": ["t_srv"]}, repo_root=tmp_path,
    )
    run = {"run_id": "audit-run", "env": "closed", "assets": catalog["assets"]}
    staged, gate = cli.stage_gated(
        run_meta=run, catalog_doc=catalog, records=[], policy=policy,
        vault=rd.PiiVault.from_policy(policy), user_values={}, p1_draft=draft,
    )
    ok, violations = rd.write_gated(tmp_path / "run", staged, gate)
    assert ok, violations
    files = {p.name: p.read_text(encoding="utf-8") for p in (tmp_path / "run").iterdir()}
    assert len(files) == 6
    blob = "\n".join(files.values())
    canaries = [
        *_CANARY_CODES, *_CANARY_LABELS.values(), *_CANARY_EXCLUDED,
        "ERRVAL987", "SKIPNAME42", "SELECT x FROM",
    ]
    for canary in canaries:
        assert canary not in blob, canary
    # 제외 컬럼은 이름도 없다(수만)
    samples = yaml.safe_load(files[rd.CODE_SAMPLES_FILE])
    assert set(samples["columns"]) == {"t_srv.stat_cd", "t_srv.use_yn"}
    assert "owner_cd" not in files[rd.CODE_SAMPLES_FILE]
    assert "amt_cd" not in files[rd.CODE_SAMPLES_FILE]
    assert samples["summary"]["excluded"]["pii"] == 1
    assert samples["summary"]["excluded"]["amount"] == 1
    assert samples["columns"]["t_srv.use_yn"] == {"distinct": 2, "substitution": "flag"}
    # 오류는 범주만
    profile = catalog["tables"]["t_srv"]["columns"]
    owner = next(c for c in profile if c["name"] == "owner_cd")
    assert owner["profile"]["error"] == "조회 실패:OperationalError"


def test_definition_notes_with_original_code_withheld(tmp_path: Path) -> None:
    """감사 L-6 — 사람이 쓴 정의 글 칸에 P1 원 코드값이 있으면 그 행을 보류하고 수만 센다."""
    policy = _policy()
    schema = cat.load_schema_source("structure_store", store=_canary_store())
    draft = schema["_p1_draft"]
    profile = {
        "source": "manual",
        "allowed_tables": ["t_srv"],
        "table_definitions": {
            "t_srv": {"kind": "현행", "manages": "서버 원장",
                      "notes": f"stat_cd 가 {_CANARY_CODES[0]} 이면 가동", "origin": "manual"},
        },
    }
    catalog = cat.build_schema_catalog(
        schema, policy, assets={"p1": cat.p1_asset(draft)}, profile=profile, repo_root=tmp_path
    )
    staged, gate = cli.stage_gated(
        run_meta={"run_id": "audit-run", "env": "closed", "assets": catalog["assets"]},
        catalog_doc=catalog, records=[], policy=policy,
        vault=rd.PiiVault.from_policy(policy), user_values={}, p1_draft=draft,
    )
    ok, violations = rd.write_gated(tmp_path / "run", staged, gate)
    assert ok, violations
    blob = "\n".join(p.read_text(encoding="utf-8") for p in (tmp_path / "run").iterdir())
    assert _CANARY_CODES[0] not in blob
    structure = catalog["approved_profile"]
    assert structure["table_definitions"] == {} and structure["table_definitions_withheld"] == 1
    # 보류한 정의의 `manages`는 테이블 의미로도 싣지 않는다
    assert catalog["tables"]["t_srv"]["meaning_source"] != "table_definitions"


def test_definition_manages_with_original_code_not_table_meaning(tmp_path: Path) -> None:
    """`manages`에 원 라벨이 있으면 정의와 테이블 의미 둘 다 싣지 않는다 · 고정 열거·식별자 칸은
    대조하지 않는다."""
    schema = cat.load_schema_source("structure_store", store=_canary_store())
    draft = schema["_p1_draft"]
    label = _CANARY_LABELS[_CANARY_CODES[0]]
    profile = {
        "source": "manual", "allowed_tables": ["t_srv"],
        "table_definitions": {
            "t_srv": {"kind": "현행", "manages": f"{label} 서버 원장", "origin": "manual"},
        },
    }
    catalog = cat.build_schema_catalog(
        schema, _policy(), assets={"p1": cat.p1_asset(draft)}, profile=profile, repo_root=tmp_path
    )
    assert catalog["approved_profile"]["table_definitions_withheld"] == 1
    assert label not in yaml.safe_dump(catalog, allow_unicode=True)
    # 원 코드값이 `kind`(고정 열거)·`key_columns`(식별자)와 같아도 행은 남는다
    originals = rd.CodeOriginals(["현행", "srv_id"])
    gated = cat.gate_table_definitions(
        {"approved_profile": {"table_definitions": {
            "t_srv": {"kind": "현행", "manages": "서버 원장", "key_columns": ["srv_id"],
                      "origin": "manual"},
        }}},
        lambda _t: False, originals=originals,
    )
    assert list(gated["approved_profile"]["table_definitions"]) == ["t_srv"]


# --- 5. 빌더 치환값 차단 ---------------------------------------------------------------

_VALUES = {"4821", "QZ7K", "뷁퀗"}


def _hits(data: dict[str, Any]) -> list[str]:
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    return ba.substitution_hits({"config/db_profiles/itam.yaml": (text, data)}, _VALUES)


class TestBuilderSubstitutionHits:
    @pytest.mark.parametrize(
        "rule",
        ["`t.c` = 'QZ7K' 인 행", '`t.c` = "QZ7K" 인 행'],
    )
    def test_quoted_literals_caught_without_value(self, rule: str) -> None:
        hits = _hits({"query_rules": [rule]})
        assert hits and not any(v in h for h in hits for v in _VALUES)

    def test_value_key_scalar_and_json_caught(self) -> None:
        assert _hits({"code_values": {"t.c": ["4821"]}})
        assert ba.substitution_hits({"x.json": ('{"a": "QZ7K"}', None)}, _VALUES)

    @pytest.mark.parametrize(
        "data",
        [
            {"query_rules": ["`t.c` = 4821 인 행만 본다"]},
            {"query_rules": ["`t.c` IN (4821, 1234) 조건"]},
            {"query_rules": ["`t.c`는 `QZ7K` 값이다"]},
            {"column_synonyms": {"t.c": ["뷁퀗"]}},
            {"table_definitions": {"t": {"manages": "자산", "notes": "뷁퀗 상태만"}}},
        ],
        ids=["numeric", "in_list", "backtick", "synonym_plain", "definition_notes"],
    )
    def test_unquoted_forms_caught(self, data: dict[str, Any]) -> None:
        """감사 L-3 — 숫자 리터럴 · IN 목록 · 백틱 · 칸 한정 없는 YAML 평문 스칼라."""
        hits = _hits(data)
        assert hits and not any(v in h for h in hits for v in _VALUES)

    @pytest.mark.parametrize(
        "data",
        [
            {"query_rules": ["`t.c48210` 은 무관", "`t.c` = 48211 은 다른 수"]},
            {"query_rules": ["2026-10-07T09:04:21 생성 · 비율 0.4821"]},
            {"query_rules": ["뷁퀗퀗 은 다른 낱말"]},
            {"entity_keys": {"keys": [{"priority": 4821, "overlap": 4821.0}]}},
        ],
        ids=["latin_word", "date_decimal", "hangul_word", "numeric_scalar"],
    )
    def test_token_boundaries_avoid_false_hits(self, data: dict[str, Any]) -> None:
        """토큰 경계 밖(낱말 일부 · 날짜·소수 조각 · 빌더 지표 수)은 걸지 않는다."""
        assert _hits(data) == []

    def test_text_only_file_scanned_by_line(self) -> None:
        hits = ba.substitution_hits({"s.yaml": ("a: 1\nb: x IN (4821)\n", None)}, _VALUES)
        assert hits == ["s.yaml:2 값 토큰"]

    def test_identifier_equal_values_skipped_and_counted(self) -> None:
        """치환값이 카탈로그 식별자와 같으면 대조에서 빼고 수를 센다(식별자 오검출 방지)."""
        catalog = {"tables": {"QZ7K": {"columns": [{"name": "srv_id"}, {"name": "뷁퀗"}]}}}
        samples = {"columns": {"t.c": {"values": ["qz7k", "4821"], "labels": [["4821", "뷁퀗"]]}}}
        values, skipped = ba.blocked_values(samples, catalog)
        assert values == {"4821"} and skipped == 2

    def test_identifier_inner_token_values_skipped(self) -> None:
        """한글·라틴이 섞인 이름 안의 토큰(`주소7`의 `7` · `IP주소`의 `IP`)과 같은 치환값도 뺀다."""
        catalog = {"tables": {"t": {"columns": [{"name": "주소7"}, {"name": "IP주소"}]}}}
        samples = {"columns": {"t.c": {"values": ["7", "IP", "4821", "IPX"]}}}
        values, skipped = ba.blocked_values(samples, catalog)
        assert values == {"4821", "IPX"} and skipped == 2

    def test_catalog_text_tokens_skipped(self) -> None:
        """반출 카탈로그 글(내부망 정의·주석)에 이미 토큰으로 있는 치환값은 우연 일치라 뺀다 —
        카탈로그에 없는 치환값은 그대로 대조한다."""
        catalog = {"tables": {"t": {"meaning": "등급 X9 이상 · 4821건",
                                    "columns": [{"name": "c"}]}}}
        samples = {"columns": {"t.c": {"values": ["QZ7K", "4821", "X9"]}}}
        values, skipped = ba.blocked_values(samples, catalog, ba.catalog_texts(catalog))
        assert values == {"QZ7K"} and skipped == 2
        # 부분 문자열(토큰 경계 밖)은 우연 일치로 보지 않는다
        values, skipped = ba.blocked_values(samples, catalog, ["AQZ7KB"])
        assert values == {"QZ7K", "4821", "X9"} and skipped == 0

    def test_hit_location_never_echoes_value(self) -> None:
        """감사 L-5 — 값이 키인 칸에서도 위치에 치환값을 싣지 않는다(`[#순번]`)."""
        hits = _hits({"code_labels": {"t.c": {"QZ7K": "뷁퀗"}}})
        assert hits
        assert not any(v in h for h in hits for v in _VALUES)
