/**
 * 관리자 「호스트 연결」 탭 (plans/196 §1.1 ③ · §5.5).
 *
 * 장애 진단 화면에서 사람이 고른 제니퍼 대상 → 폴스타 서버 연결(확정·승인 대기·부정)과
 * 확정 연결에서 뽑은 유사 패턴(적용 건수)을 본다. 승인 대기 연결은 [승인], 모든 연결은 [삭제]할 수 있다.
 * 라우터는 `NOISE_HOST_LINK_ENABLED`가 켜진 채 기동했을 때만 있다 → 404면 안내만 보인다.
 * 화면 값은 모두 textContent로 넣는다(서버 값 이스케이프).
 */
(function () {
    "use strict";

    var API = "/api/v1/admin/host-links";
    var TOKEN_KEYS = ["admin_token", "user_token"];
    var OFF_TEXT = "호스트 연결 기능이 꺼져 있습니다 — NOISE_HOST_LINK_ENABLED를 켜고 본체를 재기동해야 이 탭이 동작합니다.";
    var STATE_LABELS = { confirmed: "확정", pending: "승인 대기" };
    // 승인 409 `link_changed` — 목록을 읽은 뒤 그 연결이 바뀌었다(서버가 행 값으로 대조한다)
    var CHANGED_TEXT = "목록이 바뀌었습니다 — 새로 고친 뒤 다시 확인하세요";

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

    // 실패 사유 — 404(기능 꺼짐) · 401/403 · 그 밖은 서버 사유
    function failText(status, body) {
        var detail = body && body.detail;
        if (status === 404 && !(detail && typeof detail === "object")) return OFF_TEXT;
        if (status === 401) return "로그인이 필요합니다(인증 토큰 없음 또는 만료).";
        if (status === 403) return "관리자 권한이 필요합니다.";
        if (detail && typeof detail === "object") return String(detail.reason || detail.code || "HTTP " + status);
        return String(detail || "HTTP " + status);
    }

    function setNotice(text, kind) {
        var box = el("hostLinksNotice");
        if (!box) return;
        box.style.display = text ? "block" : "none";
        box.className = "incident-kb-notice" + (kind ? " incident-kb-notice--" + kind : "");
        box.textContent = text || "";
    }

    function request(method, path, body) {
        var init = { method: method, headers: authHeaders() };
        if (body !== undefined) {
            init.headers = Object.assign({ "Content-Type": "application/json" }, init.headers);
            init.body = JSON.stringify(body);
        }
        return fetch(API + path, init).then(function (resp) {
            return resp.json().catch(function () { return {}; }).then(function (data) {
                if (!resp.ok) {
                    var err = new Error(failText(resp.status, data));
                    err.status = resp.status;
                    err.code = data && data.detail && typeof data.detail === "object" ? data.detail.code : "";
                    throw err;
                }
                return data;
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

    function cell(tr, child) {
        var td = document.createElement("td");
        if (child instanceof Node) td.appendChild(child);
        else td.textContent = String(child === undefined || child === null ? "" : child);
        tr.appendChild(td);
    }

    function stack(lines) {
        var box = document.createElement("div");
        lines.filter(function (l) { return l && l[0]; }).forEach(function (l, i) {
            box.appendChild(node("div", l[0], i ? "incident-kb-sub" : l[1] || null));
        });
        return box;
    }

    function stateText(link) {
        if (link.kind === "absent") return "부정(해당 서버 없음)";
        return STATE_LABELS[link.state] || String(link.state || "-");
    }

    function button(text, onClick) {
        var b = node("button", text, "btn btn-secondary");
        b.type = "button";
        b.style.cssText = "padding: 4px 10px; font-size: 0.75rem; margin-right: 4px;";
        b.addEventListener("click", function () { onClick(b); });
        return b;
    }

    function keyOf(link) { return { source_id: link.source_id, hostname: link.hostname }; }

    // 승인은 목록에서 본 행 값을 함께 보낸다 — 그 사이 연결이 바뀌었으면 서버가 409 `link_changed`로 거부한다
    function approveBody(link) {
        return { source_id: link.source_id, hostname: link.hostname, db_id: link.db_id,
            server_name: link.server_name, at: link.at };
    }

    function act(btn, method, path, body, done) {
        btn.disabled = true;
        request(method, path, body).then(function () {
            load(done);
        }).catch(function (err) {
            btn.disabled = false;
            if (err.status === 409 && err.code === "link_changed") { load(CHANGED_TEXT, "warn"); return; }
            setNotice(err.message, "warn");
        });
    }

    function renderLinks(items) {
        var tbody = el("hostLinksBody");
        if (!tbody) return;
        tbody.textContent = "";
        if (!items.length) { emptyRow(tbody, 6, "연결이 없습니다 — 장애 진단 화면에서 폴스타 서버를 고르면 생깁니다."); return; }
        items.forEach(function (link) {
            var tr = document.createElement("tr");
            cell(tr, stateText(link));
            cell(tr, stack([[link.source_id || "-"], [link.hostname || ""]]));
            cell(tr, link.kind === "absent" ? "-" : stack([
                [link.server_name || link.polestar_hostname || "-"],
                [[link.polestar_hostname && link.polestar_hostname !== link.server_name ? link.polestar_hostname : "", link.db_id]
                    .filter(Boolean).join(" · ")],
            ]));
            cell(tr, link.grade || "-");
            cell(tr, stack([
                [[link.by, link.by_role, link.at ? fmtTime(link.at) : ""].filter(Boolean).join(" · ") || "-"],
                [link.approved_by ? "승인 " + link.approved_by + " · " + fmtTime(link.approved_at) : ""],
                [link.expires_at ? "만료 " + fmtTime(link.expires_at) : ""],
                [link.investigation_id ? "조사 " + link.investigation_id : ""],
            ]));
            var actions = document.createElement("div");
            if (link.state === "pending") {
                actions.appendChild(button("승인", function (b) { act(b, "POST", "/approve", approveBody(link), "승인했습니다."); }));
            }
            actions.appendChild(button("삭제", function (b) {
                if (!window.confirm("이 연결을 삭제할까요? 다음 알람부터 다시 후보를 고르게 됩니다.")) return;
                act(b, "DELETE", "", keyOf(link), "삭제했습니다.");
            }));
            cell(tr, actions);
            tbody.appendChild(tr);
        });
    }

    // 패턴 — 목록 `[{key, kind, from, to, label, count}]` 또는 해시 그대로 `{"kind|from|to": count}`(§5.4)
    function patternRows(patterns) {
        if (Array.isArray(patterns)) return patterns;
        if (!patterns || typeof patterns !== "object") return [];
        return Object.keys(patterns).map(function (k) {
            var p = k.split("|");
            return { kind: p[0], from: p[1], to: p.slice(2).join("|"), count: patterns[k] };
        });
    }

    function renderPatterns(patterns) {
        var tbody = el("hostLinksPatternBody");
        if (!tbody) return;
        tbody.textContent = "";
        var rows = patternRows(patterns).slice().sort(function (a, b) { return (Number(b.count) || 0) - (Number(a.count) || 0); });
        if (!rows.length) { emptyRow(tbody, 3, "패턴이 없습니다 — 확정 연결이 쌓이면 생깁니다."); return; }
        rows.forEach(function (p) {
            var tr = document.createElement("tr");
            cell(tr, p.label || p.kind || "-");
            cell(tr, (p.from || "") + " → " + (p.to || ""));
            cell(tr, Number(p.count) || 0);
            tbody.appendChild(tr);
        });
    }

    // notice = 직전 동작(승인·삭제) 결과 문구 — 다시 읽은 뒤에도 남긴다(kind = 안내 색)
    function load(notice, kind) {
        request("GET", "").then(function (body) {
            setNotice(typeof notice === "string" ? notice : "", kind);
            // 응답은 `{links[], patterns[]}` — 옛 키 `items`는 대체로만 읽는다
            renderLinks(Array.isArray(body.links) ? body.links : Array.isArray(body.items) ? body.items : []);
            renderPatterns(body.patterns);
        }).catch(function (err) {
            setNotice(err.message, "warn");
            ["hostLinksBody", "hostLinksPatternBody"].forEach(function (id) { if (el(id)) el(id).textContent = ""; });
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        var refresh = el("refreshHostLinksBtn");
        if (refresh) refresh.addEventListener("click", function () { load(); });
        document.querySelectorAll('.tab[data-tab="hostlinks"]').forEach(function (tab) {
            tab.addEventListener("click", function () { load(); });
        });
    });
})();
