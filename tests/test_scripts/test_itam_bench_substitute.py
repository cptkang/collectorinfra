"""plans/145 W1 — 형식 보존 가짜 값 생성기 `FakeValues` · 관계 등가류 유효 정책 (D-321 · D-311 ②).

난수 생성기라 결정성 대신 **성질**을 단언한다(A2·A4·A5·A8 · 날짜 · IP · 등가류). 실 DB·LLM 0 ·
값은 전부 합성이다.
"""

from __future__ import annotations

import ast
import copy
import inspect
import itertools
import pickle
import re
import string
from datetime import date, datetime
from typing import Any

import pytest

from scripts.itam_bench import catalog as cat
from scripts.itam_bench import code_samples as cs
from scripts.itam_bench import redact as rd
from scripts.itam_bench import substitute as sb


def _class(ch: str) -> str:
    if "0" <= ch <= "9":
        return "d"
    if "A" <= ch <= "Z":
        return "U"
    if "a" <= ch <= "z":
        return "l"
    if 0xAC00 <= ord(ch) <= 0xD7A3:
        return "H"
    return ch


# --- A2 메모 키 · 대소문자 · 끝 공백 ------------------------------------------------------


class TestMemoKey:
    def test_case_and_trailing_space_share_one_fake(self) -> None:
        fv = sb.FakeValues()
        upper = fv.fake("SRV-01가")
        assert upper != rd.MASK and upper != "SRV-01가"
        assert [_class(c) for c in upper] == [_class(c) for c in "SRV-01가"]
        assert fv.fake("srv-01가") == upper.lower()
        assert fv.fake("Srv-01가") == upper[0] + upper[1:].lower()
        padded = fv.fake("SRV-01가  ")
        assert padded == upper + "  "
        assert fv.is_fake("srv-01가") is False  # 원값은 가짜 값 등록부에 없다
        assert fv.is_fake(upper) and fv.is_fake(upper.lower() + " ")

    def test_non_ascii_letters_not_folded(self) -> None:
        """ASCII 밖 글자는 접지 않는다(키 길이 불변) — 한글 음절은 문자 군만 유지."""
        fv = sb.FakeValues()
        out = fv.fake("가나A")
        assert len(out) == 3 and out[2].isupper() and out[:2] != "가나"

    def test_multi_digit_lead_nonzero(self) -> None:
        fv = sb.FakeValues()
        for value in ("1023", "90-7", "x12"):
            out = fv.fake(value)
            assert [_class(c) for c in out] == [_class(c) for c in value]
        assert all(fv.fake(f"{n}")[0] != "0" for n in range(1000, 1020))


# --- A4 난수 · 직렬화 거부 · 값 비노출 ------------------------------------------------------


class TestNoDerivationNoLeak:
    def test_two_instances_differ_on_long_value(self) -> None:
        value = "ASSET-SERVER-0001-가나다라"
        assert sb.FakeValues().fake(value) != sb.FakeValues().fake(value)

    def test_pickle_and_copy_refused(self) -> None:
        fv = sb.FakeValues(originals=rd.CodeOriginals(["ABC1"]))
        fv.fake("XYZ9")
        for protocol in range(pickle.HIGHEST_PROTOCOL + 1):
            with pytest.raises(TypeError):
                pickle.dumps(fv, protocol=protocol)
        with pytest.raises(TypeError):
            copy.deepcopy(fv)
        assert not hasattr(fv, "__dict__")

    def test_repr_has_counts_only(self) -> None:
        fv = sb.FakeValues()
        fake = fv.fake("SECRET-VALUE-1")
        text = repr(fv)
        assert "SECRET" not in text and "secret" not in text and fake not in text
        assert re.fullmatch(r"<FakeValues 가짜 1건 · 원값 1건>", text)

    @pytest.mark.parametrize("module", [sb, cs])
    def test_module_has_no_file_log_hash_calls(self, module: Any) -> None:
        tree = ast.parse(inspect.getsource(module))
        banned_imports = {"logging", "hashlib", "hmac", "random", "pickle"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not {a.name.split(".")[0] for a in node.names} & banned_imports
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in banned_imports
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    assert func.id not in {"open", "print"}
                elif isinstance(func, ast.Attribute):
                    assert func.attr not in {
                        "write", "write_text", "write_bytes", "open", "dump", "dumps",
                        "debug", "info", "warning", "error", "exception",
                    }


# --- A5 후보 거절 --------------------------------------------------------------------------


class TestCandidateRejection:
    def test_never_equals_own_original(self) -> None:
        for _ in range(40):
            fv = sb.FakeValues()
            out = fv.fake("Q")
            assert out.casefold() != "q"

    def test_avoids_code_originals(self) -> None:
        pairs = ["".join(p) for p in itertools.product(string.ascii_lowercase, repeat=2)]
        taken = set(pairs[:200])
        fv = sb.FakeValues(originals=rd.CodeOriginals(taken))
        outs = [fv.fake_or_none(v) for v in pairs[:20]]
        assert any(o is not None for o in outs)
        assert all(o is None or o.casefold() not in taken for o in outs)

    def test_avoids_originals_seen_and_substrings(self) -> None:
        fv = sb.FakeValues()
        fv.fake("qwe")
        assert fv._is_known_original("aQWEb")  # 3자 이상 원값을 부분 문자열로 품음
        assert fv._is_known_original("QWE")
        assert not fv._is_known_original("qw")
        fv.fake("x" * (sb.SUBSTRING_MAX + 1))
        assert fv._is_known_original("X" * (sb.SUBSTRING_MAX + 1))  # 긴 원값은 같음만
        assert not fv._is_known_original("a" + "x" * (sb.SUBSTRING_MAX + 1))

    def test_identifiers_rejected_before_gate(self) -> None:
        pairs = ["".join(p) for p in itertools.product(string.ascii_uppercase, repeat=2)]
        identifiers = set(pairs[: len(pairs) // 2])
        seen: list[str] = []

        def spy(text: str) -> bool:
            seen.append(text)
            return False

        fv = sb.FakeValues(identifiers=identifiers, reject=spy)
        outs = [fv.fake_or_none(v) for v in ("ZZ", "YY", "XX")]
        folded = {i.casefold() for i in identifiers}
        assert all(o is None or o.casefold() not in folded for o in outs)
        assert not any(t.casefold() in folded for t in seen)

    def test_reject_and_known_redraw(self) -> None:
        fv = sb.FakeValues(
            reject=lambda t: t.casefold().startswith("a"), known=lambda t: t.endswith("9")
        )
        for n in range(30):
            out = fv.fake(f"K{n:03d}")
            assert out == rd.MASK or not (out.casefold().startswith("a") or out.endswith("9"))
        fv.set_reject(lambda _t: True)
        assert fv.fake("NEWVALUE") == rd.MASK
        assert fv.fallback_counts()["draws"] >= 1

    def test_set_known_applies_to_later_values(self) -> None:
        fv = sb.FakeValues()
        fv.set_known(lambda _t: True)
        assert fv.fake_or_none("ABCDEF") is None

    def test_fakes_never_collide_with_each_other(self) -> None:
        values = [f"C{n:02d}" for n in range(100)]
        # 원값을 먼저 싣는다 — 나중에 들어온 원값과의 우연 일치는 `collisions()`(관문)가 잡는다
        fv = sb.FakeValues(originals=rd.CodeOriginals(values))
        outs = [fv.fake(v) for v in values]
        assert rd.MASK not in outs
        assert len({o.casefold() for o in outs}) == len(values)
        assert not {o.casefold() for o in outs} & {v.casefold() for v in values}

    def test_collisions_count_later_originals(self) -> None:
        fv = sb.FakeValues()
        fake = fv.fake("ABCD")
        assert fv.collisions() == 0
        fv.fake(fake.lower())  # 앞서 낸 가짜 값과 같은 원값이 나중에 들어온다
        assert fv.collisions() == 1
        short = sb.FakeValues()
        short.fake("7")  # 길이 하한 미만은 세지 않는다
        assert short.collisions() == 0

    def test_registry_views(self) -> None:
        fv = sb.FakeValues()
        a = fv.fake("Alpha1")
        b = fv.fake("alpha1")
        c = fv.fake("Beta22  ")
        assert fv.fakes() == sorted({a, b, c.rstrip(" ")})
        assert set(fv.fallback_counts()) == set(sb.FALLBACK_REASONS)
        assert fv.fake("--") == rd.MASK and fv.fake_or_none("--") is None
        assert fv.fallback_counts()["no_change"] == 1  # 서로 다른 원값 기준


# --- A8 작은 공간 --------------------------------------------------------------------------


class TestSmallSpace:
    def test_single_digits_fall_back_to_mask(self) -> None:
        fv = sb.FakeValues()
        outs = [fv.fake(str(d)) for d in range(10)]
        assert outs.count(rd.MASK) >= 6  # 공간 10 − 원값 k < 2 × (낸 수 + 1)
        assert fv.fallback_counts()["space"] >= 6
        assert all(o == rd.MASK or (len(o) == 1 and o.isdigit()) for o in outs)

    def test_code_originals_shrink_space(self) -> None:
        fv = sb.FakeValues(originals=rd.CodeOriginals([str(d) for d in range(9)]))
        assert fv.fake("9") == rd.MASK  # 10 − 원값 9(중복 없이) − 자기 1 → 0

    def test_large_space_unaffected(self) -> None:
        fv = sb.FakeValues(originals=rd.CodeOriginals([f"{n:03d}" for n in range(500)]))
        assert fv.fake("ABC123") != rd.MASK


# --- 날짜 · IP ----------------------------------------------------------------------------


class TestDateAndIp:
    @pytest.mark.parametrize(
        "value",
        ["2024-03-15", "2024-03-15 10:20", "2024-03-15T10:20:30", "2024-03-15 10:20:30.123456"],
    )
    def test_date_shape_valid_and_in_range(self, value: str) -> None:
        out = sb.FakeValues().fake(value)
        assert out != value and len(out) == len(value)
        assert [_class(c) for c in out] == [_class(c) for c in value]
        parsed = datetime.fromisoformat(out)
        assert date(2000, 1, 1) <= parsed.date() <= date(2030, 12, 31)

    def test_ip_hierarchical_fake(self) -> None:
        """IP 전체를 가짜로 — 같은 원 접두는 같은 가짜 접두(/8·/16·/24 계층 일관 · 교정 1차 3)."""
        fv = sb.FakeValues()
        out = fv.fake_ip("a 10.0.1.4 b 10.0.1.4 c 10.0.2.9 d 10.7.1.4 e 11.0.1.4")
        found = re.findall(r"\d+\.\d+\.\d+\.\d+", out)
        octets = [ip.split(".") for ip in found]
        assert found[0] == found[1]
        assert octets[0][:2] == octets[2][:2] and octets[0][2] != octets[2][2]  # 같은 /16
        assert octets[0][0] == octets[3][0] and octets[0][1] != octets[3][1]  # 같은 /8
        assert octets[0][0] != octets[4][0]  # 다른 /8
        assert not {"10.0.1.4", "10.0.2.9", "10.7.1.4", "11.0.1.4"} & set(found)
        for ip in found:
            first, *_middle, last = (int(o) for o in ip.split("."))
            assert 1 <= first <= 223 and first != 127 and 1 <= last <= 254
        assert fv.is_fake(found[0]) and found[0] in fv.fakes()
        assert "10.0.1.4" not in repr(fv)

    def test_ip_small_space_falls_back(self) -> None:
        """/24 하나에 원 IP 256개 — 끝 옥텟 공간(1~254)을 넘는 만큼만 표지(최후 폴백 · 수만)."""
        fv = sb.FakeValues()
        outs = [fv.fake_ip(f"10.9.9.{n}") for n in range(256)]
        assert outs.count(rd.MASK) == 2 and fv.fallback_counts()["space"] == 2
        fakes = [o for o in outs if o != rd.MASK]
        assert len(set(fakes)) == len(fakes)
        assert len({o.rsplit(".", 1)[0] for o in fakes}) == 1  # 같은 가짜 /24 아래


# --- 코드값 파일 통합 -------------------------------------------------------------------


def test_code_samples_use_shared_generator() -> None:
    draft = {
        "draft_id": "subst0000001",
        "assets": {"code_values": {"t.상태": ["A01", "B02", "C03"]}, "code_labels": {}},
        "evidence": {"columns": [], "code_columns": []},
    }
    policy = cat.ColumnPolicy(db_id="itam", scope="closed", tables={})
    originals = rd.CodeOriginals(cs.original_values(draft))
    fv = sb.FakeValues(originals=originals)
    from_sql = fv.fake("a01")  # 같은 run 의 SQL 리터럴(소문자)
    doc = cs.build_code_samples(
        draft, policy, db_id="itam", run_id="r1", comments={},
        originals=originals, reject=lambda _t: False, fakes=fv,
    )
    values = doc["columns"]["t.상태"]["values"]
    assert from_sql.upper() in values
    assert sorted(values) == sorted(fv.fake(v) for v in ("A01", "B02", "C03"))


# --- 관계 등가류 ------------------------------------------------------------------------


def _catalog(relations: dict[str, list[dict[str, Any]]], **extra: Any) -> dict[str, Any]:
    tables = {
        name: {"key": extra.get("keys", {}).get(name, []), "relations": rels, "columns": []}
        for name, rels in relations.items()
    }
    return {"tables": tables, "same_key_groups": extra.get("groups", [])}


class TestEffectivePolicy:
    def test_general_raised_to_pii_original_untouched(self) -> None:
        policy = cat.ColumnPolicy(
            db_id="itam",
            scope="closed",
            tables={"자산": {"담당자사번": "general"}, "사원": {"사번": "pii", "부서": "general"}},
        )
        doc = _catalog(
            {
                "자산": [
                    {"from": "자산", "to": "사원", "columns": [["담당자사번", "사번"]],
                     "kind": "declared"}
                ],
                "사원": [],
            }
        )
        eff = sb.effective_policy(policy, doc)
        assert eff.grade("담당자사번", "자산") == "pii"
        assert eff.grade("부서", "사원") == "general"
        assert policy.grade("담당자사번", "자산") == "general"  # 원본 불변
        assert eff is not policy

    def test_general_raised_by_out_of_policy_partner(self) -> None:
        policy = cat.ColumnPolicy(
            db_id="itam", scope="closed", tables={"자산": {"deptCd": "general"}}
        )
        doc = _catalog(
            {
                "자산": [
                    {"from": "자산", "to": "부서", "columns": [["DEPTCD", "dept_cd"]], "kind": "p1"}
                ],
                "부서": [],
            }
        )
        eff = sb.effective_policy(policy, doc)
        assert eff.grade("deptCd", "자산") == "unclassified"
        assert eff.grade("dept_cd", "부서") == "unclassified"
        assert list(eff.tables["자산"]) == ["deptCd"]  # 원래 표기 유지

    def test_same_key_group_and_no_lowering(self) -> None:
        policy = cat.ColumnPolicy(
            db_id="itam",
            scope="closed",
            tables={
                "X": {"assetNo": "general"},
                "Y": {"ASSETNO": "pii"},
                "Z": {"code": "pii"},
                "W": {"code": "general"},
            },
        )
        doc = _catalog(
            {
                "X": [],
                "Y": [],
                "Z": [{"from": "Z", "to": "W", "columns": [["code", "code"]], "kind": "inferred"}],
                "W": [],
                "V": [{"from": "V", "to": None, "columns": [["a", None]], "kind": "p1"}],
            },
            keys={"X": ["assetNo"], "Y": ["ASSETNO"]},
            groups=[["X", "Y"]],
        )
        eff = sb.effective_policy(policy, doc)
        assert eff.grade("assetNo", "X") == "pii"  # Y.ASSETNO(pii)와 같은 기본키 군
        assert eff.grade("code", "Z") == "pii" and eff.grade("code", "W") == "pii"
        classes = sb.relation_classes(doc)
        assert sorted(len(c) for c in classes) == [2, 2]
