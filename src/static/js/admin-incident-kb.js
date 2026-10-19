/**
 * 관리자 「조사 지식」 탭 (plans/187 FE-2 · W9 §5.7.5).
 *
 * KB 초안 목록·초안 YAML 보기/복사/내려받기 · 검토 대상(「아님」 연속 등) · 엔진 선택 통계를 읽기만 한다.
 * 저장소(sre_agent) 반영은 사람이 검토해 PR로 한다 — 이 화면은 아무것도 쓰지 않는다.
 * 라우터는 `NOISE_INVESTIGATION_KB_ENABLED`가 켜진 채 기동했을 때만 등록된다 → 404면 안내만 보인다.
 * 화면 값은 모두 textContent로 넣는다(서버 값 이스케이프).
 */
(function () {
    "use strict";

    var API = "/api/v1/admin/incident-kb";
    var TOKEN_KEYS = ["admin_token", "user_token"];
    var KIND_LABELS = {
        runbook_stale: "런북 검토 기한 경과",
        ke_review_overdue: "알려진 문제 검토 기한 경과",
        ke_incorrect_streak: "「아님」 연속",
        ke_unmatched: "일치 0건",
    };
    var OFF_TEXT = "조사 지식 기능이 꺼져 있습니다 — NOISE_INVESTIGATION_KB_ENABLED를 켜고 본체를 재기동해야 이 탭이 동작합니다.";
    var current = { draftId: "", yaml: "" };

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

    function fmtTime(value) {
        var d = new Date(value);
        if (!value || isNaN(d.getTime())) return String(value || "");
        return d.toLocaleString("ko-KR", { hour12: false });
    }

    // 실패 사유 — 404(기능 꺼짐) · 503(저장소 미설정·접근 불가) · 401/403
    function failText(status, body) {
        var detail = body && body.detail;
        if (status === 404 && !(detail && typeof detail === "object")) return OFF_TEXT;
        if (status === 401) return "로그인이 필요합니다(인증 토큰 없음 또는 만료).";
        if (status === 403) return "관리자 권한이 필요합니다.";
        if (detail && typeof detail === "object") return String(detail.reason || detail.code || "HTTP " + status);
        return String(detail || "HTTP " + status);
    }

    function setNotice(text, kind) {
        var box = el("incidentKbNotice");
        if (!box) return;
        box.style.display = text ? "block" : "none";
        box.className = "incident-kb-notice" + (kind ? " incident-kb-notice--" + kind : "");
        box.textContent = text || "";
    }

    function getJSON(path) {
        return fetch(API + path, { headers: authHeaders() }).then(function (resp) {
            return resp.json().catch(function () { return {}; }).then(function (body) {
                if (!resp.ok) throw new Error(failText(resp.status, body));
                return body;
            });
        });
    }

    function emptyRow(tbody, cols, text) {
        var tr = document.createElement("tr");
        var td = node("td", text, "incident-kb-empty");
        td.colSpan = cols;
        tr.appendChild(td);
        tbody.appendChild(tr);
    }

    function cells(tr, values) {
        values.forEach(function (v) { tr.appendChild(v instanceof Node ? wrap(v) : node("td", v)); });
    }

    function wrap(child) {
        var td = document.createElement("td");
        td.appendChild(child);
        return td;
    }

    function renderDrafts(items) {
        var tbody = el("incidentKbDraftBody");
        if (!tbody) return;
        tbody.textContent = "";
        if (!items.length) { emptyRow(tbody, 6, "초안이 없습니다 — 조사 대화의 「KB 초안 만들기」로 만듭니다."); return; }
        items.forEach(function (d) {
            var tr = document.createElement("tr");
            var head = document.createElement("div");
            head.appendChild(node("div", d.title || "(제목 없음)"));
            if (d.cause) head.appendChild(node("div", "원인(미검증): " + d.cause, "incident-kb-sub"));
            var btn = node("button", "보기", "btn btn-secondary incident-kb-view");
            btn.type = "button";
            btn.addEventListener("click", function () { showYaml(d.draft_id); });
            cells(tr, [
                node("span", d.draft_id || "", "incident-kb-mono"),
                head,
                (d.playbook_id || "-") + " · 일치 후보 " + (d.match_candidates || 0),
                d.engine_choice || "-",
                (d.created_by || "-") + " · " + fmtTime(d.created_at),
                btn,
            ]);
            tbody.appendChild(tr);
        });
    }

    function showYaml(draftId) {
        var box = el("incidentKbYamlBox");
        var text = el("incidentKbYamlText");
        var msg = el("incidentKbYamlMsg");
        if (!box || !text) return;
        box.style.display = "block";
        el("incidentKbYamlTitle").textContent = "초안 YAML — " + draftId;
        text.textContent = "불러오는 중…";
        if (msg) msg.textContent = "";
        current = { draftId: "", yaml: "" };
        fetch(API + "/drafts/" + encodeURIComponent(draftId) + "/yaml", { headers: authHeaders() }).then(function (resp) {
            if (!resp.ok) {
                return resp.json().catch(function () { return {}; }).then(function (body) {
                    throw new Error(failText(resp.status, body));
                });
            }
            return resp.text();
        }).then(function (yaml) {
            current = { draftId: draftId, yaml: yaml };
            text.textContent = yaml;
            if (msg) msg.textContent = "검토 전(wip) 초안입니다 — 저장소 반영은 사람이 검토해 PR로 합니다.";
        }).catch(function (err) {
            text.textContent = "";
            if (msg) msg.textContent = "YAML을 불러오지 못했습니다 — " + err.message;
        });
    }

    function copyYaml() {
        var msg = el("incidentKbYamlMsg");
        if (!current.yaml) return;
        var clip = navigator.clipboard;
        Promise.resolve().then(function () {
            if (!clip || !clip.writeText) throw new Error("clipboard");
            return clip.writeText(current.yaml);
        }).then(function () {
            if (msg) msg.textContent = "복사했습니다.";
        }, function () {
            if (msg) msg.textContent = "복사하지 못했습니다 — 내용을 직접 선택해 복사하세요.";
        });
    }

    function downloadYaml() {
        if (!current.yaml) return;
        var url = URL.createObjectURL(new Blob([current.yaml], { type: "application/yaml;charset=utf-8" }));
        var a = document.createElement("a");
        a.href = url;
        a.download = current.draftId + ".yaml";
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(function () { URL.revokeObjectURL(url); }, 0);
    }

    function renderReview(targets) {
        var tbody = el("incidentKbReviewBody");
        if (!tbody) return;
        tbody.textContent = "";
        var items = Array.isArray(targets && targets.items) ? targets.items : [];
        if (!items.length) emptyRow(tbody, 4, "검토 대상이 없습니다.");
        items.forEach(function (t) {
            var tr = document.createElement("tr");
            cells(tr, [KIND_LABELS[t.kind] || t.kind || "", node("span", t.id || "", "incident-kb-mono"), t.reason || "", t.owner || "-"]);
            tbody.appendChild(tr);
        });
        var um = (targets && targets.unmatched_check) || {};
        var box = el("incidentKbUnmatched");
        if (box) {
            box.textContent = um.judgeable === false
                ? "「일치 0건」 판정 보류 — " + (um.reason || "조회 창이 판정 기간보다 짧습니다.")
                : (um.days ? "「일치 0건」은 최근 " + um.days + "일 기준입니다." : "");
        }
    }

    function renderEngine(stats) {
        var tbody = el("incidentKbEngineBody");
        var note = el("incidentKbEngineNote");
        if (!tbody) return;
        tbody.textContent = "";
        stats = stats || {};
        if (note) {
            note.textContent = (stats.notice || "") +
                (stats.min_samples ? " · 표본 " + stats.min_samples + "건 이상 · 플레이북 비율 " + stats.switch_ratio + " 이상이면 권고" : "");
        }
        var items = Array.isArray(stats.items) ? stats.items : [];
        if (!items.length) { emptyRow(tbody, 8, "엔진 선택 기록이 없습니다."); return; }
        items.forEach(function (it) {
            var c = it.counts || {};
            var ratio = typeof it.playbook_ratio === "number" ? Math.round(it.playbook_ratio * 100) + "%" : "-";
            var tr = document.createElement("tr");
            cells(tr, [it.playbook_id || "-", it.samples || 0, c.playbook || 0, c.holmesgpt || 0, c.both || 0, c.neither || 0,
                ratio, it.recommend_switch ? "플레이북 단독 권고" : "-"]);
            tbody.appendChild(tr);
        });
    }

    function renderWindow(summary) {
        var box = el("incidentKbWindow");
        if (!box) return;
        var w = summary.window || {};
        box.textContent = "집계 창: 조사 기록 " + (w.rows || 0) + "건(끝 " + (w.scan_max_lines || "-") + "줄)" +
            (w.oldest ? " · " + fmtTime(w.oldest) + " ~ " + fmtTime(w.newest) : "") +
            " · 초안 " + (summary.drafts || 0) + "건 · 집계 " + fmtTime(summary.generated_at);
    }

    function load() {
        setNotice("불러오는 중…");
        Promise.all([getJSON("/summary"), getJSON("/drafts")]).then(function (res) {
            setNotice("");
            renderWindow(res[0]);
            renderReview(res[0].review_targets);
            renderEngine(res[0].engine_stats);
            renderDrafts(Array.isArray(res[1].items) ? res[1].items : []);
        }).catch(function (err) {
            setNotice(err.message, "warn");
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        var refresh = el("refreshIncidentKbBtn");
        if (refresh) refresh.addEventListener("click", load);
        var copy = el("incidentKbYamlCopyBtn");
        if (copy) copy.addEventListener("click", copyYaml);
        var dl = el("incidentKbYamlDownloadBtn");
        if (dl) dl.addEventListener("click", downloadYaml);
        document.querySelectorAll('.tab[data-tab="incidentkb"]').forEach(function (tab) {
            tab.addEventListener("click", load);
        });
    });
})();
