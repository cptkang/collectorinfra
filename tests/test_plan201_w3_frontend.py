"""plans/201 W3 — 알람 화면 담당 강조 프론트(`app.js`): 순수 함수 node 실행 단언 + 정적 계약.

순수 함수(`normalizeApmEventType`·`dutyAreasOf`·`dutyCauseHintsOf`·`dutyBadgesOf`·`dutyFirstOrder`·
`reorderPromptsForDuty`)는 app.js에서 잘라 node로 실제 실행한다(plans/204 `test_ui_alarm_view.py` 패턴).
픽스처는 실 SSE payload 형태(한글 표시 이름 resource_type · `apm_event_type` 원값 · `top_process_kinds`)다.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_plan201_w3_duty_focus import NORMALIZE_CASES

_ROOT = Path(__file__).resolve().parents[1]
_APP_JS = (_ROOT / "src" / "static" / "js" / "app.js").read_text(encoding="utf-8")

#: `/auth/me` duty_alarm_focus 형태(서버 `duty_alarm_focus_meta`)를 실 정본에서 만든다 — 사본을 두지 않는다.
def _real_meta() -> dict:
    from src.infrastructure.duty_area_vocabulary import read_duty_area_vocabulary

    vocab = read_duty_area_vocabulary(_ROOT / "config" / "duty_areas.yaml")
    return {
        "areas": [{"code": a.code, "resource_types": list(a.resource_types)} for a in vocab.areas],
        "cause_hints": vocab.cause_hints.to_meta(),
    }


def _run(expr_lines: list[str]) -> object:
    if shutil.which("node") is None:
        pytest.skip("node 미설치")
    start = _APP_JS.index("var ALARM_PROMPT_AXES = {")
    end = _APP_JS.index("// ─── plans/201 W3 순수 함수 끝 ───")
    script = "\n".join([_APP_JS[start:end], *expr_lines])
    done = subprocess.run(["node", "-e", script], capture_output=True, timeout=30, check=False)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    return json.loads(done.stdout.decode("utf-8"))


def _call(fn: str, *args: object) -> object:
    js_args = ", ".join(json.dumps(a, ensure_ascii=False) for a in args)
    return _run([f"process.stdout.write(JSON.stringify({fn}({js_args})));"])


def test_normalize_same_rule_as_server() -> None:
    inputs = [raw for raw, _ in NORMALIZE_CASES]
    out = _run([f"var X = {json.dumps(inputs, ensure_ascii=False)};",
                "process.stdout.write(JSON.stringify(X.map(normalizeApmEventType)));"])
    assert out == [expected for _, expected in NORMALIZE_CASES]


def test_duty_areas_of_display_names_case_insensitive() -> None:
    meta = _real_meta()
    cases = [
        {"resource_type": "CPU"}, {"resource_type": "cpu"}, {"resource_type": "개별 파일시스템"},
        {"resource_type": "server.Cpus"}, {"resource_type": "WebURL 감시"}, {"resource_type": "weburl 감시"},
        {"resource_type": "apm.Instance"}, {"resource_type": "mysql.Process"},
        {"resource_type": "모르는 유형"}, {"resource_type": ""}, {},
    ]
    out = _run([f"var M = {json.dumps(meta, ensure_ascii=False)};",
                f"var A = {json.dumps(cases, ensure_ascii=False)};",
                "process.stdout.write(JSON.stringify(A.map(function (a) { return dutyAreasOf(a, M); })));"])
    assert out == [["server_os"], ["server_os"], ["server_os"], ["server_os"], ["was_mw"], ["was_mw"],
                   ["was_mw"], ["db"], [], [], []]
    assert _call("dutyAreasOf", {"resource_type": "CPU"}, None) == []


def test_cause_hints_from_apm_type_and_process_kinds() -> None:
    meta = _real_meta()
    alarm = {"resource_type": "apm.Instance", "apm_event_type": "BAD_RESPONSE_TIME_SQL"}
    assert _call("dutyCauseHintsOf", alarm, meta) == [
        {"area": "db", "label": "SQL 응답 지연", "source": "apm", "evidence": "BAD_RESPONSE_TIME_SQL"}]
    both = _call("dutyCauseHintsOf", {"apm_event_type": "sql_toomany_fetch"}, meta)
    assert [h["area"] for h in both] == ["was_mw", "db"]
    assert both[0]["evidence"] == "sql_toomany_fetch"                     # 근거는 받은 원값
    ext = _call("dutyCauseHintsOf", {"apm_event_type": "SOCKET_EXCEPTION"}, meta)
    assert ext == [{"area": "was_mw", "label": "외부 연계 — 상대 시스템 확인 필요", "source": "apm",
                    "evidence": "SOCKET_EXCEPTION"}]
    proc = _call("dutyCauseHintsOf",
                 {"resource_type": "CPU", "top_process_kinds": ["tomcat", "redis", "DB2", "tomcat"]}, meta)
    assert proc == [
        {"area": "was_mw", "label": "WAS·웹서버 프로세스", "source": "process", "evidence": "tomcat"},
        {"area": "db", "label": "이 서버에서 DB 엔진 실행 중", "source": "process", "evidence": "DB2"},
    ]


@pytest.mark.parametrize("alarm", [
    {"apm_event_type": "BAD_RESPONSE_TIME_APPLICATION"},   # 알려진 미매핑
    {"apm_event_type": "METRICS EVENT"},
    {"apm_event_type": "SERVICE_EXCEPTION"},
    {"apm_event_type": ""},
    {"apm_event_type": 3},
    {"top_process_kinds": ["redis", "kafka"]},
    {"top_process_kinds": "tomcat"},
    {"resource_type": "CPU"},
    {},
])
def test_no_hint_means_nothing(alarm) -> None:
    assert _call("dutyCauseHintsOf", alarm, _real_meta()) == []


def test_badges_overlap_with_user_areas() -> None:
    meta = _real_meta()
    db_user = ["db"]
    sql = {"resource_type": "apm.Instance", "apm_event_type": "BAD_RESPONSE_TIME_SQL"}
    b = _call("dutyBadgesOf", sql, meta, db_user)
    assert b["occurrence"] is False and b["hint"] is True and len(b["hints"]) == 1
    b = _call("dutyBadgesOf", sql, meta, ["was_mw"])
    assert b["occurrence"] is True and b["hint"] is False
    cpu = {"resource_type": "CPU", "top_process_kinds": ["postgresql"]}
    b = _call("dutyBadgesOf", cpu, meta, ["server_os", "db"])
    assert b["occurrence"] is True and b["hint"] is True
    b = _call("dutyBadgesOf", cpu, meta, [])
    assert b["occurrence"] is False and b["hint"] is False and len(b["hints"]) == 1


def test_duty_first_order_keeps_tier_slots_and_count() -> None:
    items = [
        {"tier": "PAGE", "duty": False}, {"tier": "DASHBOARD", "duty": False},
        {"tier": "PAGE", "duty": True}, {"tier": "DASHBOARD", "duty": True},
        {"tier": "TICKET", "duty": False}, {"tier": "PAGE", "duty": True},
        {"tier": "DASHBOARD", "duty": True}, {"tier": "", "duty": True},
    ]
    order = _call("dutyFirstOrder", items)
    assert sorted(order) == list(range(len(items)))                        # 숨김 0 · 중복 0
    assert [items[i]["tier"] for i in order] == [it["tier"] for it in items]   # 자리별 티어 불변
    # 같은 티어 안에서만 담당 먼저 · 안정 정렬
    assert order == [2, 3, 5, 6, 4, 0, 1, 7]
    assert _call("dutyFirstOrder", []) == []
    plain = [{"tier": "PAGE", "duty": False}, {"tier": "PAGE", "duty": False}]
    assert _call("dutyFirstOrder", plain) == [0, 1]


def test_prompt_reorder_keeps_set_and_count() -> None:
    meta = _real_meta()
    prompts = [{"label": "a", "axis": "server.Cpus"}, {"label": "b", "axis": "apm.Instance"},
               {"label": "c", "axis": "server.Cpus"}, {"label": "d", "axis": "weburl.WebURLMonitor"}]
    out = _call("reorderPromptsForDuty", prompts, meta, ["was_mw"])
    assert [p["label"] for p in out] == ["b", "d", "a", "c"]
    assert _call("reorderPromptsForDuty", prompts, meta, []) == prompts
    # 현행 buildAlarmPrompts: 한 알람의 추천은 축이 하나뿐이라 재정렬은 순서를 바꾸지 않는다(실측 고정)
    real = _run([
        f"var M = {json.dumps(meta, ensure_ascii=False)};",
        "var P = buildAlarmPrompts({hostname: 'web01', resource_type: 'CPU', pattern_type: '급증'});",
        "process.stdout.write(JSON.stringify([P, reorderPromptsForDuty(P, M, ['db']),"
        " reorderPromptsForDuty(P, M, ['server_os'])]));",
    ])
    base, as_db, as_os = real
    assert base and as_db == base and as_os == base
    assert len({p["axis"] for p in base}) == 1


# ─── 정적 계약 ────────────────────────────────────────────────────────────────


def _block(start: str, end: str) -> str:
    i = _APP_JS.index(start)
    return _APP_JS[i:_APP_JS.index(end, i)]


def test_dom_block_uses_text_content_only() -> None:
    block = _block("// ─── plans/201 W3 알람 화면 담당 강조", "function renderAlarmMessage(data) {")
    assert "innerHTML" not in block and "insertAdjacentHTML" not in block and "outerHTML" not in block
    assert block.count("textContent") >= 5
    pure = _block("// ─── plans/201 W3 담당 강조 순수 함수", "// ─── plans/201 W3 순수 함수 끝 ───")
    assert "document" not in pure and "localStorage" not in pure and "fetch(" not in pure


def test_entry_gated_on_server_meta_key() -> None:
    block = _block("function loadDutyAlarmFocus()", "function dutyFirstEnabled()")
    assert 'dutyHasOwn(me, "duty_alarm_focus")' in block
    render = _block("function renderAlarmMessage(data) {", "// 보고 있지 않은 동안 도착한 알람만")
    assert "if (dutyAlarmFocus) alarmPrompts = reorderPromptsForDuty(" in render
    assert "if (dutyAlarmFocus) decorateDutyAlarmCard(el, data);" in render
    # 토글은 담당 지정 사용자에게만
    toggle = _block("function setupDutyFirstToggle()", "function applyDutyAlarmOrder()")
    assert "!dutyUserAreas.length" in toggle and 'DUTY_FIRST_STORAGE_KEY' in toggle


def test_fixed_copy_and_no_vocab_copy() -> None:
    assert '"단서 · 알람 정보만 봄(조사 전)"' in _APP_JS
    assert '"내 담당 · 발생"' in _APP_JS and '"내 담당 · 단서"' in _APP_JS
    # 어휘 사본 금지 — 유형 리터럴·영역 코드가 app.js에 없다
    for literal in ("BAD_RESPONSE_TIME_SQL", "SQL_TOOMANY_FETCH", '"was_mw"', '"server_os"'):
        assert literal not in _APP_JS, literal


def test_auth_me_fetched_once_shared() -> None:
    assert len(re.findall(r'fetch\("/api/v1/auth/me"', _APP_JS)) == 1
    assert "fetchAuthMeOnce()" in _block("function loadDutyAreaNotice(userId)", "function checkAuthOnLoad()")


def test_cache_versions() -> None:
    index = (_ROOT / "src/static/index.html").read_text(encoding="utf-8")
    assert 'src="/static/js/app.js?v=53"' in index
    dashboard = (_ROOT / "src/static/admin/dashboard.html").read_text(encoding="utf-8")
    assert 'src="/static/js/admin.js?v=18"' in dashboard


def test_admin_known_unmapped_text_only() -> None:
    admin = (_ROOT / "src/static/js/admin.js").read_text(encoding="utf-8")
    i = admin.index("// (plans/201 W3) 알려진 미매핑")
    seg = admin[i:admin.index("card.appendChild(box);", i)]
    assert "innerHTML" not in seg and "textContent" in seg and "<details" not in seg
    assert 'createElement("details")' in seg
