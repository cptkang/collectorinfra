"""plans/201 W3 독립 검증(verifier) — 구현자 테스트가 덮지 않은 경계.

- 프로세스 kind 판정을 **실제 스냅샷 경로의 args**(`mask_args` 120자 절단)로 본다.
- DB 엔진 시그니처 오탐·미탐 표(args 맨 앞 토큰 · 이름만 · 경로 접두).
- 프론트 「내 담당 먼저」를 가짜 DOM 위에서 실제 app.js 코드로 돌린다(도착 · 토글 on/off · 메타 이전 카드).
- W3 신규 어휘(`alarm_focus`·`cause_hints`·`top_process_kinds`·`apm_event_type`·`process_kind_classifier`)
  의 참조 모듈 전수(W1 허용 목록 테스트는 `duty_area` 부분 문자열만 본다).

결함을 드러내는 단언은 `xfail(strict=True)`로 둔다 — 고치면 XPASS로 실패해 표지를 떼게 한다.
"""

from __future__ import annotations

import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from noise_gate.domain.process_rank import mask_args
from src.domain.middleware import classify_process_kind

_ROOT = Path(__file__).resolve().parents[1]
_APP_JS = (_ROOT / "src" / "static" / "js" / "app.js").read_text(encoding="utf-8")

_TOMCAT_CMDLINE = (
    "/usr/lib/jvm/jre/bin/java "
    "-Djava.util.logging.config.file=/opt/tomcat/conf/logging.properties "
    "-Djava.util.logging.manager=org.apache.juli.ClassLoaderLogManager -Xms2g -Xmx2g "
    "-Dcatalina.base=/opt/tomcat -Dcatalina.home=/opt/tomcat "
    "-classpath /opt/tomcat/bin/bootstrap.jar org.apache.catalina.startup.Bootstrap start"
)


# ─── 7. 프로세스 kind 판정 ────────────────────────────────────────────────────


def test_tomcat_full_cmdline_is_tomcat() -> None:
    assert classify_process_kind("java", _TOMCAT_CMDLINE) == "tomcat"


def test_tomcat_kind_survives_snapshot_truncation() -> None:
    """(W3 교정 2) 스냅샷 args는 mask_args로 120자 절단돼 WAS 단서(②)가 사라진다 — kind 판정은 같은 정렬로
    고른 **원 항목**(마스킹 전)으로 하므로 절단 뒤에도 tomcat 단서가 남는다.

    입력 변경: 종전엔 `classify_process_kind("java", mask_args(cmdline))`를 직접 단언했다(설계상 실패가 맞다 —
    마스킹 값으로 판정하지 않는 것이 수정이다). 이제 원 프로세스 항목 → 스냅샷(`select_top_processes`)과
    kind(`rank_raw_processes`+`classify_top_process_kinds`) 두 경로를 같은 입력으로 돌린다.
    """
    from noise_gate.domain.process_rank import (
        classify_top_process_kinds,
        rank_raw_processes,
        select_top_processes,
    )

    raw = [{"name": "java", "pid": 7, "p100cpu": 90.0, "args": _TOMCAT_CMDLINE}]
    top, _ = select_top_processes(raw, "cpu", 5)
    assert top[0].args == mask_args(_TOMCAT_CMDLINE)                       # 스냅샷은 절단된 값
    assert classify_process_kind(top[0].name, top[0].args) != "tomcat"      # 절단 값으로는 못 잡는다(전제)
    raw_top, _ = rank_raw_processes(raw, "cpu", 5)
    assert classify_top_process_kinds(raw_top, classify_process_kind) == ["tomcat"]


@pytest.mark.parametrize(
    ("name", "args", "expected"),
    [
        # 정탐 — args 맨 앞이 실행 파일 · 하위 프로세스 표시 · args에 실행 파일이 없고 이름만 있는 경우
        ("postgres", "/usr/pgsql-15/bin/postgres -D /var/lib/pgsql/15/data", "postgresql"),
        ("postgres", "postgres: checkpointer", "postgresql"),
        ("postgres", "-D /var/lib/pgsql/15/data", "postgresql"),
        ("postgres", "", "postgresql"),
        ("mysqld", "/usr/sbin/mysqld --basedir=/usr", "mysql"),
        ("mysqld", "--basedir=/usr --datadir=/var/lib/mysql", "mysql"),
        ("mariadbd", "", "mariadb"),
        ("db2sysc", "db2sysc 0", "db2"),
        ("oracle", "oracleORCL (LOCAL=NO)", "oracle"),
        ("ora_pmon_ORCL", "ora_pmon_ORCL", "oracle"),
        ("tbsvr", "tbsvr_FGWP000 -t NORMAL", "tibero"),
        # 오탐 방지 — 수집기·래퍼·클라이언트·조사 명령·JDBC jar·oracle 접두 경로
        ("postgres_export", "/usr/local/bin/postgres_exporter --web.listen-address=:9187", None),
        ("mysqld_exporter", "/usr/local/bin/mysqld_exporter", None),
        ("mysqld_safe", "/bin/sh /usr/bin/mysqld_safe --datadir=/var/lib/mysql", None),
        ("psql", "psql -U postgres", None),
        ("grep", "grep postgres", None),
        ("bash", "bash -c postgres", None),
        ("java", "/opt/oracle/product/19c/bin/java -jar x.jar", None),
        ("java", "/home/postgres/bin/java -jar a.jar", None),
        ("java", "-classpath /opt/tomcat/lib/postgresql-42.6.0.jar com.example.Main", None),
        ("sh", "sh /home/oracle/start.sh", None),
    ],
)
def test_db_engine_table(name: str, args: str, expected: str | None) -> None:
    assert classify_process_kind(name, args) == expected
    # 스냅샷 경로(마스킹·절단) 뒤에도 같다
    assert classify_process_kind(name, mask_args(args)) == expected


def test_oracleasm_is_not_db_engine() -> None:
    assert classify_process_kind("oracleasm", "/usr/sbin/oracleasm scandisks") is None


def test_known_false_negatives_documented() -> None:
    """미탐(정보) — 고치라는 단언이 아니라 현재 동작 고정: Oracle 백그라운드(pmon 외)·Windows 실행 파일."""
    assert classify_process_kind("ora_dbw0_ORCL", "ora_dbw0_ORCL") is None
    assert classify_process_kind("postgres.exe", r'"C:\Program Files\PostgreSQL\15\bin\postgres.exe" -D d') is None


def test_existing_kinds_unchanged_for_representative_cmdlines() -> None:
    """새 DB 규칙은 뒤에 붙었다 — 기존 13종 대표 cmdline 판정은 그대로."""
    cases = {
        "tomcat": _TOMCAT_CMDLINE,
        "jboss-wildfly": "java -D[Standalone] org.jboss.as.standalone",
        "weblogic": "java -Dweblogic.Name=AdminServer weblogic.Server",
        "websphere": "java com.ibm.ws.runtime.WsServer cfg cell node server1",
        "jeus": "java -Djeus.home=/jeus jeus.server.ServerBootstrapper",
        "nginx": "nginx: master process /usr/sbin/nginx",
        "apache-httpd": "/usr/sbin/httpd -DFOREGROUND",
        "webtob": "wsm -d /webtob",
        "redis": "redis-server *:6379",
        "kafka": "java kafka.Kafka /etc/kafka/server.properties",
        "rabbitmq": "beam.smp -- rabbit@host",
        "zookeeper": "java org.apache.zookeeper.server.quorum.QuorumPeerMain zoo.cfg",
        "tmax": "tmboot -d /tmax",
    }
    for kind, cmdline in cases.items():
        assert classify_process_kind("x", cmdline) == kind, kind


# ─── 4. 인가 비참조 — W3 신규 어휘 전수 ──────────────────────────────────────

_W3_TOKENS = ("alarm_focus", "cause_hints", "top_process_kinds", "apm_event_type", "process_kind_classifier")
_W3_ALLOWED = {
    "src/api/duty_area_support.py",
    "src/api/routes/duty_areas.py",
    "src/api/routes/user_auth.py",
    "src/api/schemas.py",
    "src/api/server.py",
    "src/config/services.py",
    "src/domain/duty_areas.py",
    "src/domain/middleware.py",
    "noise_gate/application/alarm_worker.py",
    "noise_gate/application/nodes/alarm_notifier.py",
    # (W3 교정 2) kind 판정을 마스킹 전 원 항목으로 옮김 — enricher(판정기 호출)·상태 선언·순수 헬퍼.
    # 인가 판정이 아니다(SSE 표시 키 산출 전용).
    "noise_gate/application/nodes/alarm_context_enricher.py",
    "noise_gate/orchestration/alarm_graph.py",
    "noise_gate/domain/process_rank.py",
}


def test_w3_tokens_only_in_allowlisted_modules() -> None:
    found: dict[str, list[str]] = {}
    for pkg in ("src", "noise_gate"):
        for p in (_ROOT / pkg).rglob("*.py"):
            rel = p.relative_to(_ROOT).as_posix()
            if "/tests/" in rel:
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
            hits = [t for t in _W3_TOKENS if t in text]
            if hits:
                found[rel] = hits
    assert set(found) <= _W3_ALLOWED, {k: v for k, v in found.items() if k not in _W3_ALLOWED}


# ─── 2·3. 프론트 — 가짜 DOM에서 실제 app.js 코드 실행 ─────────────────────────

_FAKE_DOM = r"""
class El {
  constructor(tag) { this.tagName = tag; this.children = []; this.dataset = {}; this.style = {};
    this.parentNode = null; this.className = ""; this.textContent = ""; this.id = ""; this._l = {}; this._bubble = null; }
  get firstChild() { return this.children[0] || null; }
  get nextSibling() { if (!this.parentNode) return null; var s = this.parentNode.children;
    return s[s.indexOf(this) + 1] || null; }
  _detach(c) { if (c.parentNode) { var s = c.parentNode.children; s.splice(s.indexOf(c), 1); } }
  appendChild(c) { this._detach(c); this.children.push(c); c.parentNode = this; return c; }
  insertBefore(c, ref) { this._detach(c); var i = ref ? this.children.indexOf(ref) : -1;
    if (i < 0) this.children.push(c); else this.children.splice(i, 0, c); c.parentNode = this; return c; }
  addEventListener(ev, fn) { this._l[ev] = fn; }
  querySelector(sel) { return sel === ".message-bubble" ? this._bubble : null; }
}
function findId(root, id) { if (!root) return null; if (root.id === id) return root;
  for (var i = 0; i < root.children.length; i++) { var f = findId(root.children[i], id); if (f) return f; } return null; }
var alarmList = new El("div"); var alarmTools = new El("div");
var document = { createElement: function (t) { return new El(t); },
  getElementById: function (id) { return findId(alarmTools, id) || findId(alarmList, id); } };
var _store = {};
var localStorage = { getItem: function (k) { return Object.prototype.hasOwnProperty.call(_store, k) ? _store[k] : null; },
  setItem: function (k, v) { _store[k] = String(v); } };
var ME = null;
function fetchAuthMeOnce() { return Promise.resolve(ME); }
function makeCard(id) { var el = new El("div"); el.dataset.id = id; el._bubble = new El("div");
  el._bubble.appendChild(new El("div")); el.appendChild(el._bubble); return el; }
// renderAlarmMessage 꼬리와 같은 순서(장식 → 맨 위 삽입 → 토글 켜졌으면 재정렬)
function arrive(data) { var el = makeCard(data.alarm_id);
  if (dutyAlarmFocus) decorateDutyAlarmCard(el, data);
  alarmList.insertBefore(el, alarmList.firstChild);
  if (dutyAlarmFocus && dutyFirstEnabled() && document.getElementById("alarmDutyFirst")) applyDutyAlarmOrder(); }
function ids() { return alarmList.children.map(function (c) { return c.dataset.id; }); }
"""


def _js_blocks() -> str:
    pure_start = _APP_JS.index("var ALARM_PROMPT_AXES = {")
    pure_end = _APP_JS.index("// ─── plans/201 W3 순수 함수 끝 ───")
    dom_start = _APP_JS.index("// ─── plans/201 W3 알람 화면 담당 강조")
    dom_end = _APP_JS.index("function renderAlarmMessage(data) {")
    return _APP_JS[pure_start:pure_end] + "\n" + _APP_JS[dom_start:dom_end]


def _real_meta() -> dict:
    from src.infrastructure.duty_area_vocabulary import read_duty_area_vocabulary

    vocab = read_duty_area_vocabulary(_ROOT / "config" / "duty_areas.yaml")
    return {
        "areas": [{"code": a.code, "resource_types": list(a.resource_types)} for a in vocab.areas],
        "cause_hints": vocab.cause_hints.to_meta(),
    }


def _node(body: str) -> object:
    if shutil.which("node") is None:
        pytest.skip("node 미설치")
    script = _FAKE_DOM + _js_blocks() + "\n(async function(){\n" + body + "\n})().catch(function(e){" \
        "process.stderr.write(String(e && e.stack || e)); process.exit(1);});"
    done = subprocess.run(["node", "-e", script], capture_output=True, timeout=30, check=False)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    return json.loads(done.stdout.decode("utf-8"))


def _alarms(n: int, seed: int) -> list[dict]:
    rnd = random.Random(seed)
    tiers = ["PAGE", "TICKET", "DASHBOARD"]
    rts = ["CPU", "apm.Instance", "개별 파일시스템", "WebURL 감시", "server.Process"]
    out = []
    for i in range(n):
        a = {"alarm_id": f"a{i}", "tier": rnd.choice(tiers), "resource_type": rnd.choice(rts)}
        if rnd.random() < 0.3:
            a["apm_event_type"] = rnd.choice(["BAD_RESPONSE_TIME_SQL", "METRICS EVENT", "SOCKET_EXCEPTION"])
        out.append(a)
    return out


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_duty_first_toggle_on_off_with_arrivals(seed: int) -> None:
    alarms = _alarms(14, seed)
    out = _node(
        f"""
        ME = {{duty_areas: ["db"], duty_area_options: [{{code: "db", label: "DB"}}],
               duty_alarm_focus: {json.dumps(_real_meta(), ensure_ascii=False)}}};
        var A = {json.dumps(alarms, ensure_ascii=False)};
        var tierOf = {{}}; A.forEach(function (a) {{ tierOf[a.alarm_id] = a.tier; }});
        // 메타 이전 도착 2건(장식 없음)
        arrive(A[0]); arrive(A[1]);
        _store["alarm_duty_first"] = "1";
        loadDutyAlarmFocus(); await Promise.resolve(); await Promise.resolve();
        var snaps = [];
        for (var i = 2; i < A.length; i++) {{
          arrive(A[i]);
          snaps.push({{ids: ids(), mine: alarmList.children.map(function (c) {{ return c.dataset.dutyMine || ""; }})}});
        }}
        var chk = document.getElementById("alarmDutyFirst");
        chk.checked = false; chk._l.change();
        var offIds = ids();
        chk.checked = true; chk._l.change();
        var onIds = ids();
        process.stdout.write(JSON.stringify({{snaps: snaps, offIds: offIds, onIds: onIds, tierOf: tierOf,
          toggles: alarmTools.children.length, stored: _store["alarm_duty_first"]}}));
        """
    )
    tier_of = out["tierOf"]
    newest_first = [a["alarm_id"] for a in reversed(alarms)]
    # 끄면 도착 역순(현행)으로 정확히 복원
    assert out["offIds"] == newest_first
    assert out["stored"] == "1" and out["toggles"] == 1
    for k, snap in enumerate(out["snaps"]):
        arrived = [a["alarm_id"] for a in reversed(alarms[: k + 3])]
        got = snap["ids"]
        assert sorted(got) == sorted(arrived)               # 숨김 0 · 카드 수 동일
        assert [tier_of[i] for i in got] == [tier_of[i] for i in arrived]   # 자리별 티어 불변
        # 같은 티어 안: 담당 먼저 · 각 그룹 안 최신순(안정)
        for tier in {tier_of[i] for i in arrived}:
            g_got = [i for i in got if tier_of[i] == tier]
            mine_flags = {i: m for i, m in zip(got, snap["mine"])}
            g_base = [i for i in arrived if tier_of[i] == tier]
            expect = [i for i in g_base if mine_flags[i] == "1"] + [i for i in g_base if mine_flags[i] != "1"]
            assert g_got == expect, (tier, g_got, expect)
    assert out["onIds"] == out["snaps"][-1]["ids"]


def test_no_meta_means_no_toggle_and_plain_prepend() -> None:
    out = _node(
        """
        ME = {duty_areas: ["db"], duty_area_options: [{code: "db", label: "DB"}]};  // W3 메타 키 없음
        loadDutyAlarmFocus(); await Promise.resolve(); await Promise.resolve();
        _store["alarm_duty_first"] = "1";
        arrive({alarm_id: "a", tier: "PAGE", resource_type: "CPU"});
        arrive({alarm_id: "b", tier: "TICKET", resource_type: "CPU", apm_event_type: "BAD_RESPONSE_TIME_SQL"});
        process.stdout.write(JSON.stringify({focus: dutyAlarmFocus, tools: alarmTools.children.length, ids: ids(),
          ds: alarmList.children.map(function (c) { return Object.keys(c.dataset).sort(); }),
          bubbleKids: alarmList.children.map(function (c) { return c._bubble.children.length; })}));
        """
    )
    assert out["focus"] is None and out["tools"] == 0
    assert out["ids"] == ["b", "a"]
    assert out["ds"] == [["id"], ["id"]]          # data-tier·dutySeq·dutyMine 없음(현행 DOM)
    assert out["bubbleKids"] == [1, 1]            # 담당 섹션 미삽입


def test_hint_line_text_only_and_external_value_not_parsed() -> None:
    """외부 유입 원값(`apm_event_type`)이 HTML로 해석될 경로가 없는지 — textContent에 그대로 실린다."""
    evil = "<img src=x onerror=alert(1)> BAD_RESPONSE_TIME_SQL"
    out = _node(
        f"""
        var meta = {json.dumps(_real_meta(), ensure_ascii=False)};
        meta.cause_hints.apm_event_types["<IMG_SRC=X_ONERROR=ALERT(1)>_BAD_RESPONSE_TIME_SQL"] =
            {{areas: ["db"], label: "<b>x</b>"}};
        ME = {{duty_areas: ["db"], duty_area_options: [{{code: "db", label: "DB"}}], duty_alarm_focus: meta}};
        loadDutyAlarmFocus(); await Promise.resolve(); await Promise.resolve();
        arrive({{alarm_id: "a", tier: "PAGE", resource_type: "CPU", apm_event_type: {json.dumps(evil)}}});
        var box = alarmList.children[0]._bubble.children[1];
        var line = box.children[box.children.length - 1];
        process.stdout.write(JSON.stringify({{cap: line.children[0].textContent, body: line.children[1].textContent,
          html: Object.prototype.hasOwnProperty.call(line.children[1], "innerHTML")}}));
        """
    )
    assert out["cap"] == "단서 · 알람 정보만 봄(조사 전)"
    assert "<img src=x onerror=alert(1)>" in out["body"] and "<b>x</b>" in out["body"]
    assert out["html"] is False


@pytest.mark.parametrize("seed", range(5))
def test_prompt_reorder_is_stable_permutation(seed: int) -> None:
    rnd = random.Random(seed)
    axes = ["server.Cpus", "apm.Instance", "server.FileSystem", "mysql.Process", "unknown.Axis", None]
    prompts = [{"axis": rnd.choice(axes), "text": f"p{i}"} for i in range(rnd.randint(0, 9))]
    areas = rnd.choice([[], ["db"], ["was_mw"], ["server_os", "db"]])
    out = _node(
        f"""
        var r = reorderPromptsForDuty({json.dumps(prompts)}, {json.dumps(_real_meta(), ensure_ascii=False)},
                                      {json.dumps(areas)});
        process.stdout.write(JSON.stringify(r.map(function (p) {{ return p.text; }})));
        """
    )
    texts = [p["text"] for p in prompts]
    assert sorted(out) == sorted(texts) and len(out) == len(texts)
    # 안정성: 각 그룹(앞=담당 · 뒤=비담당) 안의 상대 순서는 원래 순서
    idx = [texts.index(t) for t in out]
    split = next((k for k in range(1, len(idx)) if idx[k] < idx[k - 1]), len(idx))
    assert idx[:split] == sorted(idx[:split]) and idx[split:] == sorted(idx[split:])


# ─── 기타 — 설정 env 이름 ──────────────────────────────────────────────────────


def test_bare_alarm_focus_env_does_not_enable(monkeypatch) -> None:
    from src.config.services import DutyAreaConfig

    monkeypatch.delenv("DUTY_ALARM_FOCUS_ENABLED", raising=False)
    monkeypatch.setenv("DUTY_AREA_ENABLED", "true")
    monkeypatch.setenv("ALARM_FOCUS_ENABLED", "true")
    assert DutyAreaConfig(_env_file=None).alarm_focus_effective is False
