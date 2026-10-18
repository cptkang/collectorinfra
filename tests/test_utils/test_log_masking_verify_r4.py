"""plans/167 W1 fix4 재확인 — structlog 가림의 팬아웃 순환·repr 예외 격리·seen 사전 수명."""

from __future__ import annotations

import copy
import gc
import json
import time
from collections.abc import Mapping

import pytest

from src.utils import log_masking as lm
from src.utils.log_masking import mask_event_dict


class _BadRepr:
    def __repr__(self) -> str:
        raise RuntimeError("boom")


def _dump(ed) -> str:
    return json.dumps(ed, default=repr, ensure_ascii=False)


def _run(ed):
    t = time.perf_counter()
    out = mask_event_dict(None, "info", ed)
    return out, time.perf_counter() - t


def test_fanout2_self_list_fast_and_masked():
    lst = ["password=x"]
    lst.extend([lst, lst])
    out, dt = _run({"l": lst})
    assert dt < 0.5
    assert "password=x" not in repr(out["l"]) and "password=***" in repr(out["l"])
    assert lst[0] == "password=x" and lst[1] is lst  # 호출자 객체 불변


def test_fanout3_and_nested_cycle():
    a = ["token=abc"]
    b = [a, "secret=s1"]
    a.extend([a, b, a])
    b.append(b)
    out, dt = _run({"a": a, "b": b})
    assert dt < 0.5
    text = repr(out)
    assert "abc" not in text and "s1" not in text


def test_doubly_linked_dict_chain():
    nodes = [{"i": i, "password": f"pw{i}", "msg": f"api_key=k{i}"} for i in range(50)]
    for i, n in enumerate(nodes):
        n["prev"] = nodes[i - 1] if i else None
        n["next"] = nodes[i + 1] if i < 49 else None
    snap = [(n["password"], n["msg"]) for n in nodes]
    out, dt = _run({"head": nodes[0], "mid": nodes[25]})
    assert dt < 0.5
    text = repr(out)
    for i in range(50):
        assert f"pw{i}'" not in text and f"k{i}'" not in text and f"k{i}," not in text
    assert [(n["password"], n["msg"]) for n in nodes] == snap
    assert nodes[1]["prev"] is nodes[0]


def test_bad_repr_sibling_token_masked_no_raise():
    bad = _BadRepr()
    ed = {"payload": {"token": "abc", "x": bad, "deep": {"m": "password=zz", "y": bad}}}
    out, _ = _run(ed)
    p = out["payload"]
    assert p["token"] == lm.MASK
    assert p["x"] is bad and p["deep"]["y"] is bad
    assert p["deep"]["m"] == "password=***"


def test_shared_object_reuse_does_not_mutate_and_is_consistent():
    shared = {"msg": "password=x"}
    orig = copy.deepcopy(shared)
    ed = {"a": shared, "b": [shared, shared], "password": shared}
    out, _ = _run(ed)
    assert shared == orig
    assert out["a"] == {"msg": "password=***"}
    assert out["b"][0] is out["a"] and out["b"][1] is out["a"]
    assert out["password"] == lm.MASK


def test_no_state_leak_across_events():
    shared = ["password=x"]
    out1, _ = _run({"a": shared})
    shared[0] = "plain"  # 같은 객체(같은 id)를 바꿔 다음 이벤트에 넣는다
    out2, _ = _run({"a": shared})
    assert out1["a"] == ["password=***"]
    assert out2["a"] is shared and shared == ["plain"]
    # 모듈 전역에 id 캐시가 없어야 한다
    id_caches = [
        n
        for n, v in vars(lm).items()
        if isinstance(v, dict) and v and all(isinstance(k, int) for k in v)
    ]
    assert not id_caches


class _GenMapping(Mapping):
    """값을 접근할 때마다 새로 만드는 Mapping — 처리 후 해제돼 id가 재사용될 수 있다."""

    def __init__(self, n):
        self._n = n

    def __getitem__(self, k):
        return [f"plain{k}"] if k % 2 else [f"password=s{k}"]

    def __iter__(self):
        return iter(range(self._n))

    def __len__(self):
        return self._n


def test_id_reuse_of_transient_values_within_event():
    gc.collect()
    out, _ = _run({"m": _GenMapping(40)})
    m = out["m"]
    for k in range(40):
        exp = [f"plain{k}"] if k % 2 else ["password=***"]
        assert list(m[k]) == exp, (k, m[k])


def test_real_structlog_jsonrenderer():
    structlog = pytest.importorskip("structlog")
    lst = ["password=x"]
    lst.extend([lst, lst])
    node = {"token": "tk1", "msg": "Authorization: Bearer abc.def"}
    node["self"] = node
    ed = {
        "event": "login dsn=postgresql://u:pw9@h/db",
        "l": lst,
        "n": node,
        "bad": {"api_key": "k9", "o": _BadRepr()},
    }
    ed = mask_event_dict(None, "info", ed)
    try:
        text = structlog.processors.JSONRenderer()(None, "info", ed)
    except Exception as exc:  # noqa: BLE001
        # _BadRepr는 렌더러가 repr 시 실패 — 가림 결과 자체를 확인한다
        text = repr({k: v for k, v in ed.items() if k != "bad"}) + repr(ed["bad"].get("api_key"))
        assert isinstance(exc, (RuntimeError, ValueError, TypeError))
    for s in ("pw9", "tk1", "abc.def", "password=x", "k9"):
        assert s not in text, s


class _GenTupleMapping(Mapping):
    """접근마다 새 튜플을 만드는 Mapping — 가린 뒤 원 튜플이 해제돼 다음 튜플이 같은 id를 받는다."""

    def __getitem__(self, k):
        return (f"secret=q{k}", k)

    def __iter__(self):
        return iter(range(30))

    def __len__(self):
        return 30


@pytest.mark.xfail(
    strict=True, reason="seen이 원 객체를 붙잡지 않아 해제 뒤 재사용된 id에 다른 결과를 돌려준다"
)
def test_id_reuse_transient_tuples_get_wrong_result():
    out, _ = _run({"m": _GenTupleMapping()})
    m = out["m"]
    assert all(m[k][1] == k for k in range(30))
