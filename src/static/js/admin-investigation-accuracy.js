/**
 * 관리자 「조사 정확도」 탭 (plans/198 W0 I-1 · D-350 ②).
 *
 * 조사 기록 × 「실제 원인」 라벨의 결정적 집계(서버 계산)를 주간 표 1장으로 읽기만 한다 — 주별 행 아래에
 * 창 전체의 엔진별·알람 종류별 행을 잇는다. 「후보 목록 내려받기」는 154 세계 후보 메타 JSON이다.
 * 라우터는 `NOISE_INVESTIGATION_LABEL_ENABLED`가 켜진 채 기동했을 때만 등록된다 → 첫 조회가 404면
 * 탭 버튼을 숨긴 채로 둔다(꺼져 있으면 화면이 종전과 같다). 화면 값은 모두 textContent로 넣는다.
 */
(function () {
    "use strict";

    var API = "/api/v1/admin/investigation/accuracy";
    var TOKEN_KEYS = ["admin_token", "user_token"];
    var ENGINE_LABELS = { playbook: "플레이북", holmesgpt: "HolmesGPT", parallel: "병렬(두 엔진)", none: "엔진 없음(스텁 등)" };
    var KIND_LABELS = { alarm: "알람", episode: "사건", diagnosis: "추가 조사", manual: "알람 다시 분석" };

    function authHeaders() {
        var token = "";
        for (var i = 0; i < TOKEN_KEYS.length && !token; i++) {
            token = localStorage.getItem(TOKEN_KEYS[i]) || "";
        }
        return token ? { Authorization: "Bearer " + token } : {};
    }

    function el(id) { return document.getElementById(id); }

    function node(tag, text, cls) {
        var n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text !== undefined && text !== null) n.textContent = String(text);
        return n;
    }

    function pct(value) {
        return typeof value === "number" ? Math.round(value * 1000) / 10 + "%" : "-";
    }

    function failText(status, body) {
        var detail = body && body.detail;
        if (status === 401) return "로그인이 필요합니다(인증 토큰 없음 또는 만료).";
        if (status === 403) return "관리자 권한이 필요합니다.";
        if (detail && typeof detail === "object") return String(detail.reason || detail.code || "HTTP " + status);
        return String(detail || "HTTP " + status);
    }

    function setNotice(text, kind) {
        var box = el("invAccuracyNotice");
        if (!box) return;
        box.style.display = text ? "block" : "none";
        box.className = "incident-kb-notice" + (kind ? " incident-kb-notice--" + kind : "");
        box.textContent = text || "";
    }

    function getJSON(path) {
        return fetch(API + path, { headers: authHeaders() }).then(function (resp) {
            return resp.json().catch(function () { return {}; }).then(function (body) {
                return { ok: resp.ok, status: resp.status, body: body || {} };
            });
        });
    }

    function row(tbody, label, m, cls) {
        var tr = document.createElement("tr");
        if (cls) tr.className = cls;
        m = m || {};
        [label, m.investigations || 0, m.done || 0, pct(m.label_rate), m.judged || 0,
            pct(m.candidate_recall), pct(m.rc_at_1), pct(m.rc_at_3),
            pct(m.incomplete_rate), pct(m.unavailable_rate)].forEach(function (v) {
            tr.appendChild(node("td", v));
        });
        tbody.appendChild(tr);
    }

    function head(tbody, text) {
        var tr = document.createElement("tr");
        var td = node("td", text, "incident-kb-sub");
        td.colSpan = 10;
        tr.appendChild(td);
        tbody.appendChild(tr);
    }

    function render(data) {
        var tbody = el("invAccuracyBody");
        var win = el("invAccuracyWindow");
        if (!tbody) return;
        tbody.textContent = "";
        var w = data.window || {};
        if (win) {
            win.textContent = "집계 창: " + (w.start || "-") + " ~ " + (w.end || "-") + " (" + (w.weeks || 0) + "주) · 조사 기록 끝 " +
                (data.scan_max_lines || "-") + "줄 · 집계 " + String(data.generated_at || "");
        }
        (data.weeks || []).forEach(function (r) { row(tbody, r.week + " (" + r.start + "~)", r); });
        row(tbody, "창 전체", data.total, "inv-accuracy-total");
        head(tbody, "엔진별(창 전체)");
        Object.keys(data.by_engine || {}).forEach(function (k) { row(tbody, ENGINE_LABELS[k] || k, data.by_engine[k]); });
        head(tbody, "알람 종류별(창 전체)");
        Object.keys(data.by_kind || {}).forEach(function (k) { row(tbody, KIND_LABELS[k] || k, data.by_kind[k]); });
    }

    function load(first) {
        if (!first) setNotice("불러오는 중…");
        return getJSON("?weeks=8").then(function (res) {
            if (res.status === 404 && first) return false;
            var tab = document.querySelector('.tab[data-tab="invaccuracy"]');
            if (tab) tab.hidden = false;
            if (!res.ok) { setNotice(failText(res.status, res.body), "warn"); return true; }
            setNotice("");
            render(res.body);
            return true;
        }).catch(function (err) {
            if (!first) setNotice(String((err && err.message) || err), "warn");
            return false;
        });
    }

    function exportCandidates() {
        getJSON("/candidates").then(function (res) {
            if (!res.ok) { setNotice(failText(res.status, res.body), "warn"); return; }
            var blob = new Blob([JSON.stringify(res.body, null, 2)], { type: "application/json" });
            var a = document.createElement("a");
            a.href = URL.createObjectURL(blob);
            a.download = "investigation_label_candidates.json";
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(a.href);
            setNotice("후보 " + (res.body.count || 0) + "건을 내려받았습니다 — " + (res.body.notice || ""));
        }).catch(function (err) {
            setNotice(String((err && err.message) || err), "warn");
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        var refresh = el("refreshInvAccuracyBtn");
        if (refresh) refresh.addEventListener("click", function () { load(false); });
        var exp = el("invAccuracyExportBtn");
        if (exp) exp.addEventListener("click", exportCandidates);
        document.querySelectorAll('.tab[data-tab="invaccuracy"]').forEach(function (tab) {
            tab.addEventListener("click", function () { load(false); });
        });
        load(true);
    });
})();
