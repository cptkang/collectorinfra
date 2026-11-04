/**
 * 관리자 「APM·SMS 연계」 탭 (plans/210 §3.10 · D-362).
 *
 * ① 매핑 목록·필터·편집(대상·상태·메모 · 확인 · 다시 찾기 미리보기/적용 · 삭제) · 수동 추가
 * ② 사전 스캔(「지금 스캔」 · 진행 폴링 · 스캐너별 결과 · 노드 주소 소스별 상태 · DPM 「미연동」 · 이력)
 * ③ 인벤토리(마지막 스캔 스냅샷 검색 · 「매핑 없음만」 · 수동 추가 선택 · APM 행의 현재 연계 표시)
 *
 * 라우터는 `NOISE_APM_HOST_MAP_ENABLED`가 켜진 채 기동했을 때만 있다 — 첫 조회가 404면 탭을 숨긴 채 둔다
 * (플래그 off 화면 그대로). 토큰은 운영자(admin_token)·일반 로그인(user_token) 둘 다 읽는다.
 * 화면 값은 모두 textContent로 넣는다(서버 값 이스케이프).
 */
(function () {
    "use strict";

    var API = "/api/v1/admin/apm-host-map";
    var TOKEN_KEYS = ["admin_token", "user_token"];
    var PAGE = 50;
    var POLL_MS = 3000;
    var OFF_TEXT = "APM·SMS 연계 기능이 꺼져 있습니다 — NOISE_APM_HOST_MAP_ENABLED를 켜고 본체를 재기동해야 이 탭이 동작합니다.";
    var CHANGED_TEXT = "목록을 본 뒤 다른 변경이 먼저 저장됐습니다 — 새로 고친 뒤 다시 확인하세요";
    var SCAN_OFF_TEXT = "자동 생성(NOISE_APM_HOST_MAP_AUTO_ENABLED)이 꺼져 있어 스캔할 수 없습니다 — 수동 매핑만 관리합니다.";
    var INV_LINK_TITLE = "인스턴스 → 워크로드 → 도메인 기본값 순으로 처음 있는 연계(표시용 — 진단 순서와 다를 수 있음)";
    var INV_EDIT_TITLE = "이 인스턴스의 연계 수정 — 매핑 목록의 [수정]과 같은 편집 칸";

    var L = {
        state: { active: "사용", ambiguous: "모호", failed: "실패", disabled: "연계 없음" },
        origin: { auto: "자동", manual: "관리자" },
        basis: { node_address: "노드 주소", vm_exact: "VM 일치", itam: "ITAM 업무 추정", manual: "관리자 지정", name_candidate: "이름 후보" },
        scope: { instance: "인스턴스", workload: "워크로드", domain: "도메인 기본값" },
        review: { ambiguous: "모호", failed: "실패", changed: "변경됨", scan_suggests: "스캔 제안 있음", target_gone: "대상 사라짐" },
        run: { running: "실행 중", ok: "정상", partial: "일부 실패", failed: "실패", aborted: "중단" },
        scanner: { apm_inventory: "APM 인벤토리", node_address: "APM 노드 주소", sms: "SMS(폴스타 서버)", itam: "ITAM", dpm: "DPM" },
        scannerStatus: { ok: "정상", partial: "일부 실패", failed: "실패", disabled: "꺼짐", not_integrated: "미연동" },
        action: { insert: "새로 만듦", update: "갱신(변경됨)", same: "결과 같음", suggest: "스캔 제안으로만 남김", skip: "바꾸지 않음" },
        reason: {
            apm_map_no_inventory: "사전 스캔 스냅샷이 없습니다 — 「지금 스캔」을 먼저 실행하세요.",
            not_in_inventory: "현재 스냅샷에 이 인스턴스·도메인이 없습니다.",
            no_match: "현재 스냅샷으로는 잇는 폴스타 서버를 찾지 못했습니다.",
            itam_config_invalid: "ITAM 조회 설정이 잘못되어 ITAM 업무 추정을 할 수 없습니다.",
        },
        summary: [["new", "신규"], ["changed", "변경"], ["ambiguous", "모호"], ["failed", "실패"], ["gone", "사라짐"],
            ["kept_manual", "관리자 행 유지"], ["errors", "오류"]],
    };
    var SCANNER_ORDER = ["apm_inventory", "node_address", "sms", "itam", "dpm"];

    var st = {
        offset: 0, total: 0, rows: [], autoEnabled: false, scanAvailable: false,
        editing: null, targets: [], previewRow: null,
        invOffset: 0, invHasMore: false, polling: null, opened: false,
    };

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

    function val(id) { var n = el(id); return n ? String(n.value || "").trim() : ""; }

    function setVal(id, v) { var n = el(id); if (n) n.value = v === undefined || v === null ? "" : String(v); }

    function fmtTime(value) {
        var d = new Date(value);
        if (!value || isNaN(d.getTime())) return String(value || "");
        return d.toLocaleString("ko-KR", { hour12: false });
    }

    function seconds(a, b) {
        var s = new Date(a).getTime(), e = b ? new Date(b).getTime() : Date.now();
        if (isNaN(s) || isNaN(e)) return "";
        var sec = Math.max(0, Math.round((e - s) / 1000));
        return sec >= 60 ? Math.floor(sec / 60) + "분 " + (sec % 60) + "초" : sec + "초";
    }

    function failText(status, body) {
        var detail = body && body.detail;
        if (status === 404 && !(detail && typeof detail === "object")) return OFF_TEXT;
        if (status === 401) return "로그인이 필요합니다(인증 토큰 없음 또는 만료).";
        if (status === 403) return "관리자 권한이 필요합니다.";
        if (detail && typeof detail === "object") return String(detail.reason || detail.code || "HTTP " + status);
        if (Array.isArray(detail)) return "입력값을 확인하세요(HTTP " + status + ").";
        return String(detail || "HTTP " + status);
    }

    function setNotice(id, text, kind) {
        var box = el(id);
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

    function qs(params) {
        var parts = [];
        Object.keys(params).forEach(function (k) {
            var v = params[k];
            if (v === "" || v === null || v === undefined || v === false) return;
            parts.push(encodeURIComponent(k) + "=" + encodeURIComponent(String(v)));
        });
        return parts.length ? "?" + parts.join("&") : "";
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
        return td;
    }

    function stack(lines) {
        var box = document.createElement("div");
        lines.filter(function (l) { return l; }).forEach(function (l, i) {
            box.appendChild(node("div", l, i ? "ahm-sub" : null));
        });
        return box;
    }

    function tag(text, kind) { return node("span", text, "ahm-tag" + (kind ? " ahm-tag--" + kind : "")); }

    function button(text, onClick) {
        var b = node("button", text, "btn btn-secondary ahm-btn");
        b.type = "button";
        b.addEventListener("click", function () { onClick(b); });
        return b;
    }

    function targetText(t) {
        var name = String(t.server_name || t.hostname || "-");
        var meta = [t.hostname && t.hostname !== t.server_name ? t.hostname : "", t.ip, t.db_id].filter(Boolean).join(" · ");
        return name + (meta ? " (" + meta + ")" : "");
    }

    function targetsNode(targets) {
        var list = node("ul", null, "ahm-targets");
        (targets || []).forEach(function (t) {
            var li = node("li", targetText(t));
            if (t.target_gone) li.appendChild(tag("사라짐", "warn"));
            if (t.kind && t.kind !== "sms") li.appendChild(tag(String(t.kind)));
            list.appendChild(li);
        });
        if (!list.children.length) list.appendChild(node("li", "-"));
        return list;
    }

    // ── ① 매핑 목록 ──
    function mapQuery() {
        return qs({
            source_id: val("apmMapFilterSource"), state: val("apmMapFilterState"), origin: val("apmMapFilterOrigin"),
            basis: val("apmMapFilterBasis"), q: val("apmMapFilterQuery"),
            review: el("apmMapFilterReview") && el("apmMapFilterReview").checked ? "true" : "",
            limit: PAGE, offset: st.offset,
        });
    }

    function revealTab() {
        var tab = document.querySelector('.tab[data-tab="apmhostmap"]');
        if (tab) tab.hidden = false;
    }

    // first = 페이지를 연 직후 탐색 — 404(기능 꺼짐)면 탭을 숨긴 채 둔다
    function loadMaps(notice, kind, first) {
        return request("GET", mapQuery()).then(function (body) {
            revealTab();
            st.rows = Array.isArray(body.rows) ? body.rows : [];
            st.total = Number(body.total) || 0;
            st.autoEnabled = body.auto_enabled === true;
            st.scanAvailable = body.scan_available === true;
            setNotice("apmMapNotice", typeof notice === "string" ? notice : "", kind);
            renderMaps();
            return true;
        }).catch(function (err) {
            if (first && err.status === 404) return false;
            revealTab();
            setNotice("apmMapNotice", err.message, "warn");
            if (el("apmMapBody")) el("apmMapBody").textContent = "";
            return true;
        });
    }

    function renderMaps() {
        var tbody = el("apmMapBody");
        if (!tbody) return;
        tbody.textContent = "";
        if (!st.rows.length) emptyRow(tbody, 7, "연계 매핑이 없습니다 — 「지금 스캔」이나 알람 진단이 쌓으면 생기고, 「수동 추가」로 직접 넣을 수 있습니다.");
        st.rows.forEach(function (row) {
            var tr = document.createElement("tr");
            var stateBox = document.createElement("div");
            stateBox.appendChild(tag(L.state[row.state] || String(row.state || "-"), row.state === "active" ? null : "warn"));
            if (row.verified) stateBox.appendChild(tag("관리자 확인"));
            (row.review_reasons || []).forEach(function (r) {
                if (r !== row.state) stateBox.appendChild(tag(L.review[r] || r, "warn"));
            });
            cell(tr, stateBox);
            cell(tr, stack([row.source_id || "-", [row.domain_name, row.domain_id ? "도메인 " + row.domain_id : ""].filter(Boolean).join(" · ")]));
            cell(tr, stack([L.scope[row.scope] || row.scope || "-", row.instance_example || row.match_key || ""]));
            cell(tr, targetsNode(row.targets));
            var basisBox = document.createElement("div");
            basisBox.appendChild(node("div", (L.origin[row.origin] || row.origin || "-") + " · " + (L.basis[row.basis] || row.basis || "-")));
            if (row.origin === "auto" && !row.verified) basisBox.appendChild(tag("자동 · 추정", "auto"));
            if (row.note) basisBox.appendChild(node("div", "메모 " + row.note, "ahm-sub"));
            cell(tr, basisBox);
            cell(tr, stack([[row.updated_by, fmtTime(row.updated_at)].filter(Boolean).join(" · ") || "-", "v" + (row.version || 0)]));
            var actions = document.createElement("div");
            actions.appendChild(button("수정", function () { openEditor(row); }));
            if (row.origin === "auto" && !row.verified) {
                actions.appendChild(button("확인", function (b) { act(b, "POST", "/" + row.id + "/verify", { version: row.version }, "확인했습니다 — 이후 자동 결과가 덮지 않습니다."); }));
            }
            actions.appendChild(button("다시 찾기", function (b) { preview(row, b); }));
            actions.appendChild(button("삭제", function (b) {
                if (!window.confirm("이 연계를 삭제할까요? 다음 알람부터 다시 찾습니다.")) return;
                act(b, "DELETE", "/" + row.id, { version: row.version }, "삭제했습니다.");
            }));
            cell(tr, actions);
            tbody.appendChild(tr);
        });
        var info = el("apmMapPageInfo");
        if (info) info.textContent = st.total ? (st.offset + 1) + "–" + Math.min(st.offset + PAGE, st.total) + " / " + st.total + "건" : "";
        if (el("apmMapPrevBtn")) el("apmMapPrevBtn").disabled = st.offset <= 0;
        if (el("apmMapNextBtn")) el("apmMapNextBtn").disabled = st.offset + PAGE >= st.total;
    }

    function act(btn, method, path, body, done) {
        btn.disabled = true;
        request(method, path, body).then(function () {
            closeEditor();
            closePreview();
            loadMaps(done);
            if (st.opened) loadInv();  // 인벤토리 연계 칸·버튼도 새로 — 같은 인스턴스 재추가(409 exists) 방지
        }).catch(function (err) {
            btn.disabled = false;
            if (err.status === 409 && err.code === "version_conflict") { loadMaps(CHANGED_TEXT, "warn"); return; }
            setNotice("apmMapNotice", err.message, "warn");
        });
    }

    // ── 편집 · 수동 추가 ──
    function openEditor(row, preset) {
        var box = el("apmMapEditor");
        if (!box) return;
        closePreview();
        st.editing = row || { create: true };
        st.targets = row ? (row.targets || []).map(function (t) {
            return { db_id: t.db_id || "", server_name: t.server_name || "", hostname: t.hostname || "", ip: t.ip || "", target_gone: !!t.target_gone };
        }) : [];
        var p = preset || {};
        el("apmMapEditTitle").textContent = row
            ? "연계 수정 — " + [row.source_id, row.domain_name || row.domain_id, L.scope[row.scope] || row.scope, row.instance_example || row.match_key].filter(Boolean).join(" · ")
            : "수동 추가";
        el("apmMapEditKeyFields").hidden = !!row;
        setVal("apmMapEditSource", p.source_id);
        setVal("apmMapEditDomain", p.domain_id);
        setVal("apmMapEditDomainName", p.domain_name);
        setVal("apmMapEditScope", p.scope || "instance");
        setVal("apmMapEditInstance", p.instance_name);
        setVal("apmMapEditState", row && row.state === "disabled" ? "disabled" : "active");
        setVal("apmMapEditNote", row ? row.note : "");
        setVal("apmMapEditTargetDb", "");
        setVal("apmMapEditTargetServer", "");
        box.hidden = false;
        renderEditorTargets();
        if (box.scrollIntoView) box.scrollIntoView({ block: "nearest" });
    }

    function closeEditor() {
        st.editing = null;
        st.targets = [];
        if (el("apmMapEditor")) el("apmMapEditor").hidden = true;
    }

    function renderEditorTargets() {
        var list = el("apmMapEditTargets");
        if (!list) return;
        list.textContent = "";
        if (!st.targets.length) list.appendChild(node("li", "대상 없음 — 「연계 없음」이면 비워 둘 수 있습니다.", "ahm-sub"));
        st.targets.forEach(function (t, i) {
            var li = node("li", targetText(t));
            if (t.target_gone) li.appendChild(tag("사라짐", "warn"));
            li.appendChild(button("빼기", function () { st.targets.splice(i, 1); renderEditorTargets(); }));
            list.appendChild(li);
        });
    }

    function addTarget(t) {
        if (!st.editing) { setNotice("apmInvNotice", "먼저 매핑의 [수정]이나 「수동 추가」로 편집 칸을 여세요.", "warn"); return; }
        var key = (t.db_id || "") + "|" + String(t.server_name || "").toLowerCase();
        var dup = st.targets.some(function (x) { return (x.db_id || "") + "|" + String(x.server_name || "").toLowerCase() === key; });
        if (!dup) st.targets.push(t);
        renderEditorTargets();
    }

    function saveEditor(btn) {
        if (!st.editing) return;
        var targets = st.targets.map(function (t) { return { db_id: t.db_id || "", server_name: t.server_name || "", hostname: t.hostname || "" }; });
        var state = val("apmMapEditState") || "active";
        var note = el("apmMapEditNote") ? String(el("apmMapEditNote").value || "") : "";
        if (st.editing.create) {
            act(btn, "POST", "", {
                source_id: val("apmMapEditSource"), domain_id: val("apmMapEditDomain"),
                domain_name: val("apmMapEditDomainName"), scope: val("apmMapEditScope") || "instance",
                instance_name: val("apmMapEditInstance"), targets: targets, state: state, note: note,
            }, "추가했습니다.");
            return;
        }
        act(btn, "PUT", "/" + st.editing.id, { version: st.editing.version, targets: targets, state: state, note: note },
            "저장했습니다 — 관리자 행이 되어 자동 결과가 덮지 않습니다.");
    }

    // ── 다시 찾기 ──
    function closePreview() {
        st.previewRow = null;
        if (el("apmMapPreview")) el("apmMapPreview").hidden = true;
    }

    function preview(row, btn) {
        btn.disabled = true;
        request("POST", "/" + row.id + "/preview").then(function (res) {
            btn.disabled = false;
            closeEditor();
            renderPreview(row, res);
        }).catch(function (err) {
            btn.disabled = false;
            setNotice("apmMapNotice", err.message, "warn");
        });
    }

    function renderPreview(row, res) {
        var box = el("apmMapPreview"), body = el("apmMapPreviewBody"), apply = el("apmMapPreviewApplyBtn");
        if (!box || !body) return;
        st.previewRow = row;
        body.textContent = "";
        var p = res && res.proposal;
        body.appendChild(node("div", "현재: " + (L.state[row.state] || row.state) + " · " + (L.basis[row.basis] || row.basis)));
        body.appendChild(targetsNode(row.targets));
        if (!p) {
            body.appendChild(node("div", L.reason[res && res.reason] || ("제안 없음(" + String((res && res.reason) || "-") + ")"), "incident-kb-notice incident-kb-notice--warn"));
        } else {
            body.appendChild(node("div", "제안: " + (L.state[p.state] || p.state) + " · " + (L.basis[p.basis] || p.basis) +
                " — " + (L.action[res.action] || res.action || "")));
            body.appendChild(targetsNode(p.targets));
            var why = p.evidence && (p.evidence.reason || p.evidence.stage);
            if (why) body.appendChild(node("div", "근거 " + String(why), "ahm-sub"));
        }
        var canApply = !!p && !res.preview_only && st.autoEnabled;
        if (res && res.preview_only) body.appendChild(node("div", "관리자 행(관리자 등록·확인·연계 없음)은 미리보기만 합니다 — 바꾸려면 [수정]하세요.", "ahm-sub"));
        else if (p && !st.autoEnabled) body.appendChild(node("div", "자동 생성(NOISE_APM_HOST_MAP_AUTO_ENABLED)이 꺼져 있어 적용할 수 없습니다.", "ahm-sub"));
        if (apply) apply.hidden = !canApply;
        box.hidden = false;
    }

    function applyPreview(btn) {
        var row = st.previewRow;
        if (!row) return;
        act(btn, "POST", "/" + row.id + "/resolve", { apply: true, version: row.version }, "다시 찾기 결과를 적용했습니다.");
    }

    // ── ② 스캔 ──
    function scannerDetail(name, rec) {
        var parts = [];
        if (rec.error_kind) parts.push(String(rec.error_kind));
        if (rec.unchecked_zones && rec.unchecked_zones.length) parts.push("확인 못 한 존 " + rec.unchecked_zones.join(", "));
        if (name === "node_address" && Array.isArray(rec.sources)) parts.push("소스 " + rec.sources.length + "개");
        return parts.join(" · ") || "-";
    }

    function summaryText(sum) {
        if (!sum || typeof sum !== "object") return "";
        var parts = L.summary.filter(function (p) { return sum[p[0]] !== undefined; }).map(function (p) { return p[1] + " " + sum[p[0]]; });
        if (Array.isArray(sum.skipped) && sum.skipped.length) parts.push("건너뛴 규칙 " + sum.skipped.map(function (s) { return L.basis[s] || s; }).join(", "));
        return parts.join(" · ");
    }

    function renderRun(run) {
        var latest = el("apmScanLatest"), sBody = el("apmScanScannerBody"), nBody = el("apmScanNodeSourcesBody");
        if (latest) {
            latest.textContent = "";
            if (!run) latest.textContent = "스캔 이력이 없습니다.";
            else {
                latest.appendChild(node("div", "마지막 스캔: " + (L.run[run.status] || run.status) + " · 시작 " + fmtTime(run.started_at) +
                    " · 소요 " + seconds(run.started_at, run.finished_at) + (run.status === "running" ? "(진행 중)" : "")));
                var s = summaryText(run.link_summary);
                if (s) latest.appendChild(node("div", "연계 요약: " + s, "ahm-sub"));
            }
        }
        var scanners = run && run.scanners && typeof run.scanners === "object" ? run.scanners : {};
        if (sBody) {
            sBody.textContent = "";
            var names = SCANNER_ORDER.concat(Object.keys(scanners).filter(function (k) { return SCANNER_ORDER.indexOf(k) < 0; }));
            var any = false;
            names.forEach(function (name) {
                var rec = scanners[name];
                if (!rec) return;
                any = true;
                var tr = document.createElement("tr");
                cell(tr, L.scanner[name] || name);
                cell(tr, L.scannerStatus[rec.status] || rec.status || "-");
                cell(tr, rec.status === "not_integrated" ? "-" : (rec.rows || 0));
                cell(tr, rec.status === "not_integrated" ? "-" : (rec.seconds || 0) + "초");
                cell(tr, scannerDetail(name, rec));
                sBody.appendChild(tr);
            });
            if (!any) emptyRow(sBody, 5, run && run.status === "running" ? "스캐너 결과를 기다리는 중…" : "스캐너 결과가 없습니다.");
        }
        if (nBody) {
            nBody.textContent = "";
            var sources = scanners.node_address && Array.isArray(scanners.node_address.sources) ? scanners.node_address.sources : [];
            sources.forEach(function (src) {
                var tr = document.createElement("tr");
                cell(tr, src.source_id || "-");
                cell(tr, src.status || "-");
                cell(tr, src.reason || "-");
                nBody.appendChild(tr);
            });
            if (!sources.length) emptyRow(nBody, 3, "노드 주소 소스별 결과가 없습니다.");
        }
    }

    function renderRuns(runs) {
        var tbody = el("apmScanRunsBody");
        if (!tbody) return;
        tbody.textContent = "";
        if (!runs.length) emptyRow(tbody, 5, "스캔 이력이 없습니다.");
        runs.forEach(function (run) {
            var tr = document.createElement("tr");
            cell(tr, fmtTime(run.started_at));
            cell(tr, L.run[run.status] || run.status || "-");
            cell(tr, seconds(run.started_at, run.finished_at));
            cell(tr, run.requested_by || "-");
            cell(tr, summaryText(run.link_summary) || "-");
            tbody.appendChild(tr);
        });
    }

    function setScanButton() {
        var btn = el("apmScanStartBtn");
        if (btn) btn.disabled = !st.scanAvailable || !!st.polling;
    }

    function loadScan(notice, kind) {
        return request("GET", "/scan/runs?limit=10").then(function (body) {
            var runs = Array.isArray(body.runs) ? body.runs : [];
            st.scanAvailable = body.scan_available === true;
            setNotice("apmScanNotice", typeof notice === "string" ? notice : (st.scanAvailable ? "" : SCAN_OFF_TEXT), kind);
            renderRun(runs[0] || null);
            renderRuns(runs);
            if (runs[0] && runs[0].status === "running" && !st.polling) poll(runs[0].run_id);
            setScanButton();
        }).catch(function (err) {
            setNotice("apmScanNotice", err.message, "warn");
        });
    }

    function poll(runId) {
        st.polling = runId;
        setScanButton();
        request("GET", "/scan/runs/" + encodeURIComponent(runId)).then(function (body) {
            var run = body && body.run;
            renderRun(run || null);
            if (run && run.status === "running") { setTimeout(function () { poll(runId); }, POLL_MS); return; }
            st.polling = null;
            loadScan("스캔이 끝났습니다 — " + (L.run[run && run.status] || (run && run.status) || ""));
            loadMaps();
        }).catch(function (err) {
            st.polling = null;
            setScanButton();
            setNotice("apmScanNotice", err.message, "warn");
        });
    }

    function startScan(btn) {
        btn.disabled = true;
        request("POST", "/scan").then(function (body) {
            setNotice("apmScanNotice", "스캔을 시작했습니다 — 진행 상황을 자동으로 새로 고칩니다.");
            poll(body.run_id);
        }).catch(function (err) {
            setScanButton();
            if (err.status === 409 && err.code === "scan_running") { setNotice("apmScanNotice", err.message, "warn"); loadScan(err.message, "warn"); return; }
            setNotice("apmScanNotice", err.message, "warn");
        });
    }

    // ── ③ 인벤토리 ──
    var INV_COLS = {
        apm: ["제니퍼 소스 · 도메인", "인스턴스", "IP · 호스트", "노드 주소", "연계", ""],
        sms: ["폴스타 소스", "서버명", "hostname", "IP", ""],
        itam: ["업무", "hostname", "IP", "사용", ""],
    };

    function renderInvHead(kind) {
        var head = el("apmInvHead");
        if (!head) return;
        head.textContent = "";
        var tr = document.createElement("tr");
        INV_COLS[kind].forEach(function (h) { tr.appendChild(node("th", h)); });
        head.appendChild(tr);
    }

    // APM 행의 「연계」 칸 — 서버가 붙인 `mapping`(instance → workload → domain 첫 행 · 없으면 null)
    function invLinkNode(m) {
        if (!m) return node("span", "없음", "ahm-sub");
        var box = document.createElement("div");
        box.title = INV_LINK_TITLE;
        box.appendChild(tag(L.scope[m.scope] || String(m.scope || "-")));
        box.appendChild(tag(L.state[m.state] || String(m.state || "-"), m.state === "active" ? null : "warn"));
        box.appendChild(tag(L.basis[m.basis] || String(m.basis || "-")));
        var targets = Array.isArray(m.targets) ? m.targets : [];
        var t = targets[0];
        box.appendChild(node("div", t
            ? [t.db_id, t.server_name || t.hostname].filter(Boolean).join(" / ") + (targets.length > 1 ? " 외 " + (targets.length - 1) + "대" : "")
            : "대상 없음", "ahm-sub"));
        return box;
    }

    function invRow(kind, r) {
        var tr = document.createElement("tr");
        if (kind === "apm") {
            cell(tr, stack([r.source_id || "-", [r.domain_name, r.domain_id ? "도메인 " + r.domain_id : ""].filter(Boolean).join(" · ")]));
            cell(tr, r.instance_name || "-");
            cell(tr, [r.ip, r.host_name].filter(Boolean).join(" · ") || "-");
            cell(tr, [r.host_address, r.host_address_status].filter(Boolean).join(" · ") || "-");
            var m = r.mapping || null;
            cell(tr, invLinkNode(m));
            if (m && m.scope === "instance") {
                var edit = button("수정", function () { openEditor(m); });
                edit.title = INV_EDIT_TITLE;
                cell(tr, edit);
            } else {
                // instance 매핑이 없을 때만 — workload·domain 연계는 칸에 표시만 하고 instance 행 추가는 허용
                cell(tr, button("이 인스턴스 연계 추가", function () {
                    openEditor(null, { source_id: r.source_id, domain_id: r.domain_id, domain_name: r.domain_name, scope: "instance", instance_name: r.instance_name });
                }));
            }
        } else if (kind === "sms") {
            cell(tr, r.db_id || "-");
            cell(tr, r.server_name || "-");
            cell(tr, r.hostname || "-");
            cell(tr, [r.ip].concat(Array.isArray(r.nic_ips) ? r.nic_ips : []).filter(Boolean).join(", ") || "-");
            cell(tr, button("대상으로", function () {
                addTarget({ db_id: r.db_id || "", server_name: r.server_name || "", hostname: r.hostname || "", ip: r.ip || "" });
            }));
        } else {
            cell(tr, stack([r.app_name || "-", r.app_code || ""]));
            cell(tr, r.hostname || "-");
            cell(tr, Array.isArray(r.ips) ? r.ips.join(", ") : String(r.ips || "-"));
            cell(tr, r.active === false ? "아니오" : "예");
            cell(tr, "");
        }
        return tr;
    }

    function loadInv(notice) {
        var kind = val("apmInvKind") || "apm";
        var path = "/inventory/" + kind + qs({
            q: val("apmInvQuery"), unmapped: el("apmInvUnmapped") && el("apmInvUnmapped").checked ? "true" : "",
            limit: PAGE, offset: st.invOffset,
        });
        return request("GET", path).then(function (body) {
            var tbody = el("apmInvBody");
            renderInvHead(kind);
            st.invHasMore = body.has_more === true;
            setNotice("apmInvNotice", typeof notice === "string" ? notice : (body.run_id ? "" : "스냅샷이 없습니다 — 「지금 스캔」을 먼저 실행하세요."), body.run_id ? null : "warn");
            if (!tbody) return;
            tbody.textContent = "";
            var rows = Array.isArray(body.rows) ? body.rows : [];
            if (!rows.length) emptyRow(tbody, INV_COLS[kind].length, "행이 없습니다.");
            rows.forEach(function (r) { tbody.appendChild(invRow(kind, r)); });
            var info = el("apmInvPageInfo");
            if (info) info.textContent = rows.length ? (st.invOffset + 1) + "–" + (st.invOffset + rows.length) + "건" : "";
            if (el("apmInvPrevBtn")) el("apmInvPrevBtn").disabled = st.invOffset <= 0;
            if (el("apmInvNextBtn")) el("apmInvNextBtn").disabled = !st.invHasMore;
        }).catch(function (err) {
            setNotice("apmInvNotice", err.message, "warn");
        });
    }

    // ── 배선 ──
    function on(id, fn) { var n = el(id); if (n) n.addEventListener("click", function () { fn(n); }); }

    function openAll() {
        loadMaps();
        loadScan();
        if (!st.opened) { st.opened = true; loadInv(); }
    }

    document.addEventListener("DOMContentLoaded", function () {
        on("apmMapRefreshBtn", function () { loadMaps(); });
        on("apmMapSearchBtn", function () { st.offset = 0; loadMaps(); });
        on("apmMapPrevBtn", function () { st.offset = Math.max(0, st.offset - PAGE); loadMaps(); });
        on("apmMapNextBtn", function () { st.offset += PAGE; loadMaps(); });
        on("apmMapAddBtn", function () { openEditor(null); });
        on("apmMapEditCancelBtn", function () { closeEditor(); });
        on("apmMapEditSaveBtn", saveEditor);
        on("apmMapEditTargetAddBtn", function () {
            var server = val("apmMapEditTargetServer");
            if (!server) return;
            addTarget({ db_id: val("apmMapEditTargetDb"), server_name: server, hostname: "", ip: "" });
            setVal("apmMapEditTargetServer", "");
        });
        on("apmMapPreviewCloseBtn", function () { closePreview(); });
        on("apmMapPreviewApplyBtn", applyPreview);
        on("apmScanStartBtn", startScan);
        on("apmScanRefreshBtn", function () { loadScan(); });
        on("apmInvSearchBtn", function () { st.invOffset = 0; loadInv(); });
        on("apmInvPrevBtn", function () { st.invOffset = Math.max(0, st.invOffset - PAGE); loadInv(); });
        on("apmInvNextBtn", function () { st.invOffset += PAGE; loadInv(); });
        document.querySelectorAll('.tab[data-tab="apmhostmap"]').forEach(function (tab) {
            tab.addEventListener("click", openAll);
        });
        // 진단 화면 「연계 정보 수정」 링크 — `/admin?apm_map_q=…#apmhostmap`
        var search = (window.location && window.location.search) || "";
        var m = /[?&]apm_map_q=([^&]*)/.exec(search);
        if (m) {
            // 깨진 퍼센트 인코딩(URIError)이 초기화를 멈추지 않게 — 검색어만 버린다
            try {
                setVal("apmMapFilterQuery", decodeURIComponent(m[1].replace(/\+/g, " ")));
            } catch (e) { /* 무시 */ }
        }
        loadMaps(undefined, undefined, true).then(function (enabled) {
            if (!enabled) return;
            if (window.location && window.location.hash === "#apmhostmap") {
                var tab = document.querySelector('.tab[data-tab="apmhostmap"]');
                if (tab && tab.click) tab.click();
            }
        });
    });
})();
