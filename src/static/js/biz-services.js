/**
 * 「업무 서비스 정의」(P1) 독립 페이지 (plans/215 §5.1 · §5.3 · D-368).
 *
 * ① 서비스 목록(검색·필터·정렬·CSV 내보내기·[새 서비스]) ② 오른쪽 상세 패널(기본 정보 · 구성원 3탭 ·
 * 구성 맵 · 연계 그래프 · 관리(병합·APM 대조·게이트웨이 업무 매핑 사본) · 변경 이력) ③ 검토함(후보 일괄 ·
 * 미소속 서버 일괄 지정) ④ 자동 계산(다시 계산 → 차이 → 적용 · 실행 이력 · 신호 표) ⑤ CSV 가져오기(W5b —
 * 파일 → 미리보기(행별 판정) → 적용 · 관리자에게만 영역을 만든다) ⑥ 연계 분석.
 *
 * - 조회는 로그인 사용자(`/api/v1/biz-services` — 존 권한 밖 구성원은 건수만), 편집은 관리자
 *   (`/api/v1/admin/biz-services`). 편집 버튼은 관리자일 때만 그린다 — 정본은 서버 403.
 * - 편집 충돌(409 `version_conflict`)은 「다른 사람이 먼저 고쳤습니다 · 다시 불러오기」만 보인다(덮어쓰기 없음).
 * - 옮기기·계층 지정·분할·병합·일괄 지정·후보 일괄은 미리보기 패널로 영향을 보인 뒤 확인 버튼으로 실행한다
 *   (브라우저 확인 대화상자를 쓰지 않는다).
 * - 주소 `/biz-services?service=<id>&tab=<탭>&section=<영역>`을 동기화한다(새로고침·링크 공유 복원).
 * - 화면 값은 모두 textContent로 넣는다(서버 값 이스케이프). 이 페이지는 새 탭을 열지 않는다.
 */
(function () {
    "use strict";

    var API = "/api/v1/biz-services";
    var ADMIN = "/api/v1/admin/biz-services";
    var PAGE_PATH = "/biz-services";
    var PAGE = 50;
    var MAX_BULK = 200;
    var CONFLICT_TEXT = "다른 사람이 먼저 고쳤습니다 · 다시 불러오기";
    var EMPTY_TEXT = "자동 계산 또는 가져오기로 서비스를 만드세요";
    var DETAIL_TABS = [["info", "기본 정보"], ["members", "구성원"], ["map", "구성 맵"], ["graph", "연계 그래프"],
        ["manage", "병합·대조", true], ["history", "변경 이력", true]];
    var KIND_TABS = [["sms_server", "SMS 서버"], ["apm", "APM"], ["apm_business", "제니퍼 업무"]];
    var LAYERS = [["web", "웹"], ["was", "WAS"], ["db", "DB"], ["batch", "배치"], ["etc", "기타"]];

    var L = {
        state: { active: "활성", ambiguous: "검토 필요", disabled: "비활성" },
        memberState: { active: "활성", excluded: "제외", target_gone: "끊김(대상 사라짐)" },
        origin: { auto: "자동", manual: "관리자" },
        confidence: { confirmed: "확정", inferred: "추정" },
        line: { confirmed: "확정", inferred: "추정", broken: "끊김" },
        candKind: { member: "구성원 후보", merge: "병합 후보", apm_unlinked: "APM 미연계" },
        review: { ambiguous: "모호", conflicts: "충돌", open_candidates: "열린 후보", target_gone: "끊김" },
        basis: {
            biz_suffix: "등록명 업무 꼬리", apm_domain: "APM 도메인", itam: "ITAM 업무", host_stem: "호스트 이름 계열",
            name_tokens: "이름 유사", host_prefix: "호스트 접두 코드", tree_group: "자원 트리 그룹",
            registered_batch: "같은 시각 등록", ip_subnet: "같은 대역", itam_contract: "같은 구매 계약",
            alarm_cooccurrence: "동시 알람", avail_dependency: "가용성 의존", apm_host_map: "APM·SMS 연계",
            manual: "관리자 지정", gateway_business: "게이트웨이 업무 해석",
        },
        action: {
            biz_service_create: "서비스 생성", biz_service_update: "기본 정보 수정", biz_service_monitored: "감시 대상 변경",
            biz_service_member_add: "구성원 추가", biz_service_member_state: "구성원 제외·제외 취소",
            biz_service_member_confirm: "구성원 확인·확인 취소", biz_service_member_move: "구성원 옮기기",
            biz_service_member_layer: "계층 지정", biz_service_merge: "병합", biz_service_split: "분할",
            biz_service_candidate_accept: "후보 수락", biz_service_candidate_reject: "후보 거절",
            biz_service_bulk_assign: "일괄 지정", biz_service_apm_compare: "APM 대조",
            biz_service_import: "CSV 가져오기",
        },
        outcome: { ok: "성공", denied: "거부", failed: "실패" },
    };

    var st = {
        isAdmin: false, section: "catalog", offset: 0, total: 0, rows: [], zones: {},
        detail: null, tab: "members", kind: "sms_server", selected: {}, services: [],
        cands: [], candSel: {}, unassigned: null, unSel: {}, preview: null,
    };

    // ── 공통 ────────────────────────────────────────────────────────────
    function el(id) { return document.getElementById(id); }

    function node(tag, text, cls) {
        var n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text !== undefined && text !== null) n.textContent = String(text);
        return n;
    }

    function clear(n) { if (n) n.textContent = ""; return n; }

    function button(text, cls, onClick) {
        var b = node("button", text, "btn " + (cls || "btn-secondary"));
        b.type = "button";
        if (onClick) b.addEventListener("click", onClick);
        return b;
    }

    function badge(text, tone) { return node("span", text, "bizsvc-badge bizsvc-badge--" + (tone || "muted")); }

    function td(tr, value, cls) { var c = node("td", value === null || value === undefined ? "—" : value, cls); tr.appendChild(c); return c; }

    function pct(v) { return v === null || v === undefined ? "—" : Math.round(Number(v) * 1000) / 10 + "%"; }

    function fmtTime(value) {
        var d = new Date(value);
        if (!value || isNaN(d.getTime())) return value ? String(value) : "—";
        return d.toLocaleString("ko-KR", { hour12: false });
    }

    function token() { return localStorage.getItem("user_token") || ""; }

    function headers(json) {
        var h = {};
        var t = token();
        if (t) h.Authorization = "Bearer " + t;
        if (json) h["Content-Type"] = "application/json";
        return h;
    }

    function toLogin() { window.location.href = "/login?next=" + encodeURIComponent(PAGE_PATH); }

    function failText(status, body) {
        var detail = body && body.detail;
        if (status === 401) return "로그인이 필요합니다.";
        if (status === 403) return "관리자 권한이 필요합니다.";
        if (detail && typeof detail === "object" && !Array.isArray(detail)) {
            if (detail.code === "version_conflict") return CONFLICT_TEXT;
            return String(detail.reason || detail.code || "HTTP " + status);
        }
        if (Array.isArray(detail)) return "입력값을 확인하세요(HTTP " + status + ").";
        if (status === 404) return "찾을 수 없습니다(기능이 꺼졌거나 권한 밖입니다).";
        return String(detail || "HTTP " + status);
    }

    function request(method, url, body) {
        var isForm = typeof FormData !== "undefined" && body instanceof FormData;
        var init = { method: method, headers: headers(body !== undefined && !isForm) };
        if (body !== undefined) init.body = isForm ? body : JSON.stringify(body);
        return fetch(url, init).then(function (resp) {
            return resp.json().catch(function () { return {}; }).then(function (data) {
                if (resp.status === 401) { toLogin(); }
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
            if (v === "" || v === null || v === undefined) return;
            parts.push(encodeURIComponent(k) + "=" + encodeURIComponent(String(v)));
        });
        return parts.length ? "?" + parts.join("&") : "";
    }

    function notify(text, kind) {
        var box = el("bsAlert");
        if (!box) return;
        box.hidden = !text;
        box.className = "bizsvc-alert" + (kind ? " bizsvc-alert--" + kind : "");
        box.textContent = text || "";
    }

    function fail(err) {
        if (err && err.code === "version_conflict") { showConflict(); return; }
        notify((err && err.message) || "요청이 실패했습니다.", "error");
    }

    function cacheNote(data) {
        if (data && data.cache_ok === false) notify("저장했습니다 — 사본(Redis)은 아직 갱신되지 않았습니다: " + (data.cache_reason || ""), "warn");
        else notify("저장했습니다.", "ok");
    }

    function syncUrl() {
        var p = {};
        if (st.section !== "catalog") p.section = st.section;
        if (st.detail) { p.service = st.detail.service.id; p.tab = st.tab; }
        history.replaceState(null, "", PAGE_PATH + qs(p));
    }

    // 미리보기 패널 — 영향 문장 목록 + [확인]·[취소](브라우저 확인 대화상자 대신)
    function previewPanel(host, title, lines, okText, onOk) {
        clear(host);
        var box = node("div", null, "bizsvc-preview");
        box.appendChild(node("strong", title));
        var ul = node("ul");
        lines.forEach(function (line) { ul.appendChild(node("li", line)); });
        box.appendChild(ul);
        var bar = node("div", null, "bizsvc-bar");
        var ok = button(okText || "확인", "btn-primary", function () {
            ok.disabled = true;
            Promise.resolve(onOk()).catch(fail).then(function () { clear(host); });
        });
        bar.appendChild(ok);
        bar.appendChild(button("취소", "btn-secondary", function () { clear(host); }));
        box.appendChild(bar);
        host.appendChild(box);
    }

    // ── 인증 · 시작 ─────────────────────────────────────────────────────
    function boot() {
        fetch("/api/v1/auth/status", { headers: headers(false) }).then(function (res) {
            if (res.status === 401) { toLogin(); return null; }
            return res.json();
        }).then(function (data) {
            if (!data) return;
            if (data.auth_enabled && !data.user) { toLogin(); return; }
            var user = data.user || {};
            st.isAdmin = !data.auth_enabled || user.role === "admin";
            el("bsUser").textContent = (user.username || user.user_id || "") + (st.isAdmin ? " · 관리자" : "");
            if (st.isAdmin) buildImportSection();
            document.querySelectorAll("[data-admin]").forEach(function (n) { n.hidden = !st.isAdmin; });
            wire();
            var params = new URLSearchParams(window.location.search);
            var section = params.get("section");
            if (section && st.isAdmin && ["review", "compute", "import", "analysis"].indexOf(section) >= 0) showSection(section);
            loadList().then(function () {
                var sid = parseInt(params.get("service") || "", 10);
                if (sid > 0) openDetail(sid, params.get("tab") || "members");
            });
            if (st.isAdmin) loadReviewBadge();
        }).catch(function () { notify("인증 상태를 확인하지 못했습니다.", "error"); });
    }

    function wire() {
        document.querySelectorAll("#bsSections .bizsvc-tab").forEach(function (b) {
            b.addEventListener("click", function () { showSection(b.getAttribute("data-section")); });
        });
        el("bsSearch").addEventListener("click", function () { st.offset = 0; loadList(); });
        el("bsQ").addEventListener("keydown", function (e) { if (e.key === "Enter") { st.offset = 0; loadList(); } });
        el("bsPrev").addEventListener("click", function () { st.offset = Math.max(0, st.offset - PAGE); loadList(); });
        el("bsNext").addEventListener("click", function () { if (st.offset + PAGE < st.total) { st.offset += PAGE; loadList(); } });
        el("bsExport").addEventListener("click", exportCsv);
        el("bsNew").addEventListener("click", showNewPanel);
        el("rvReload").addEventListener("click", loadCandidates);
        el("rvKind").addEventListener("change", loadCandidates);
        el("rvAll").addEventListener("change", function () {
            var on = el("rvAll").checked;
            st.candSel = {};
            if (on) st.cands.forEach(function (c) { st.candSel[c.id] = c; });
            renderCandidates();
        });
        el("rvAccept").addEventListener("click", function () { previewCandidates("accept"); });
        el("rvReject").addEventListener("click", function () { previewCandidates("reject"); });
        el("rvUnassignedLoad").addEventListener("click", loadUnassigned);
        el("rvAssign").addEventListener("click", previewAssign);
        el("cpRun").addEventListener("click", runPreview);
        el("cpApply").addEventListener("click", runApply);
    }

    function showSection(name) {
        st.section = name;
        document.querySelectorAll("#bsSections .bizsvc-tab").forEach(function (b) {
            b.classList.toggle("active", b.getAttribute("data-section") === name);
        });
        el("secCatalog").hidden = name !== "catalog";
        el("secReview").hidden = name !== "review";
        el("secCompute").hidden = name !== "compute";
        el("secAnalysis").hidden = name !== "analysis";
        if (imp.sec) imp.sec.hidden = name !== "import";
        if (name === "review") { loadCandidates(); fillServiceSelect(el("rvTarget")); }
        if (name === "compute") loadCompute();
        if (name === "analysis") loadAnalysis();
        syncUrl();
    }

    // ── ① 목록 ──────────────────────────────────────────────────────────
    function filters() {
        return {
            q: el("bsQ").value.trim(), zone: el("bsZone").value, tier: el("bsTier").value,
            status: el("bsStatus").value, monitored: el("bsMonitored").value,
            coverage_below: el("bsCoverage").value, sort: el("bsSort").value, order: el("bsOrder").value,
        };
    }

    function hasFilter(f) { return !!(f.q || f.zone || f.tier || f.status || f.monitored || f.coverage_below); }

    function loadList() {
        var f = filters();
        var p = Object.assign({ limit: PAGE, offset: st.offset }, f);
        return request("GET", API + qs(p)).then(function (data) {
            st.rows = data.rows || [];
            st.total = data.total || 0;
            if (data.is_admin === false && st.isAdmin) {
                // 서버 역할 재검증이 정본 — 화면 판정보다 우선한다
                st.isAdmin = false;
                document.querySelectorAll("[data-admin]").forEach(function (n) { n.hidden = true; });
            }
            renderList(data, f);
        }).catch(fail);
    }

    function zoneChips(map) {
        var wrap = node("span");
        Object.keys(map || {}).forEach(function (z) {
            st.zones[z] = true;
            wrap.appendChild(node("span", (z || "존 미상") + " " + map[z], "bizsvc-chip"));
        });
        return wrap;
    }

    function statusCell(row) {
        var c = node("td");
        if (row.state === "disabled") c.appendChild(badge("비활성", "muted"));
        else c.appendChild(row.verified ? badge("확정", "ok") : badge("추정", "info"));
        if (row.review_needed) {
            var b = badge("검토 필요", "warn");
            b.title = (row.review_reasons || []).map(function (r) { return L.review[r] || r; }).join(" · ");
            c.appendChild(b);
        }
        return c;
    }

    function renderList(data, f) {
        var tbody = clear(el("bsRows"));
        var note = [];
        if (data.hidden_service_count) note.push("존 권한 밖 서비스 " + data.hidden_service_count + "개는 이름 없이 건수만 셉니다.");
        note.push(data.analysis_at ? "APM 커버리지는 마지막 적용(" + fmtTime(data.analysis_at) + ") 기준입니다."
            : "APM 커버리지는 적용한 자동 계산이 있어야 보입니다.");
        el("bsListNote").textContent = note.join(" ");
        st.rows.forEach(function (row) {
            var tr = node("tr", null, "bizsvc-row");
            if (st.detail && st.detail.service.id === row.id) tr.classList.add("selected");
            var name = node("td");
            name.appendChild(node("div", row.display_name));
            name.appendChild(node("div", row.service_key, "bizsvc-muted"));
            tr.appendChild(name);
            td(tr, row.tier ? row.tier + "등급" : "미지정");
            td(tr, row.owner_org);
            var sms = node("td");
            sms.appendChild(node("span", String(row.sms_count) + " "));
            sms.appendChild(zoneChips(row.sms_by_zone));
            if (row.hidden_member_count) sms.appendChild(node("span", "권한 밖 " + row.hidden_member_count, "bizsvc-muted"));
            tr.appendChild(sms);
            td(tr, row.apm_count);
            td(tr, pct(row.apm_coverage));
            td(tr, pct(row.inferred_ratio));
            tr.appendChild(statusCell(row));
            td(tr, row.monitored ? "감시" : "—");
            td(tr, fmtTime(row.updated_at));
            tr.addEventListener("click", function () { openDetail(row.id, st.tab); });
            tbody.appendChild(tr);
        });
        var empty = el("bsEmpty");
        clear(empty);
        empty.hidden = st.rows.length > 0;
        if (!st.rows.length) {
            empty.appendChild(node("div", hasFilter(f) ? "조건에 맞는 서비스가 없습니다." : EMPTY_TEXT));
            if (!hasFilter(f) && st.isAdmin) {
                var bar = node("div", null, "bizsvc-bar");
                bar.style.justifyContent = "center";
                bar.appendChild(button("자동 계산으로", "btn-primary", function () { showSection("compute"); }));
                bar.appendChild(button("새 서비스", "btn-secondary", showNewPanel));
                empty.appendChild(bar);
            }
        }
        el("bsPage").textContent = st.total ? (st.offset + 1) + "–" + Math.min(st.offset + PAGE, st.total) + " / " + st.total : "0건";
        var zoneSel = el("bsZone");
        var cur = zoneSel.value;
        Object.keys(st.zones).sort().forEach(function (z) {
            if (!z || zoneSel.querySelector('option[value="' + CSS.escape(z) + '"]')) return;
            var o = node("option", z);
            o.value = z;
            zoneSel.appendChild(o);
        });
        zoneSel.value = cur;
    }

    function exportCsv() {
        var f = filters();
        fetch(API + "/export.csv" + qs(f), { headers: headers(false) }).then(function (resp) {
            if (resp.status === 401) { toLogin(); return null; }
            if (!resp.ok) return resp.json().catch(function () { return {}; }).then(function (b) { throw new Error(failText(resp.status, b)); });
            return resp.blob();
        }).then(function (blob) {
            if (!blob) return;
            var url = URL.createObjectURL(blob);
            var a = node("a");
            a.href = url;
            a.download = "biz-services.csv";
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
        }).catch(fail);
    }

    // 서비스 고르기 select(옮기기·병합·일괄 지정 대상) — 표시명 순 100개까지
    function fillServiceSelect(sel, exceptId) {
        if (!sel) return Promise.resolve();
        return request("GET", API + qs({ sort: "name", limit: 100 })).then(function (data) {
            st.services = data.rows || [];
            var keep = sel.value;
            while (sel.options.length > 1) sel.remove(1);
            st.services.forEach(function (s) {
                if (exceptId && s.id === exceptId) return;
                var o = node("option", s.display_name + " (" + s.service_key + ")");
                o.value = String(s.id);
                sel.appendChild(o);
            });
            sel.value = keep;
        }).catch(fail);
    }

    function serviceName(id) {
        var hit = st.services.concat(st.rows).filter(function (s) { return s.id === id; })[0];
        return hit ? hit.display_name : "서비스 #" + id;
    }

    function showNewPanel() {
        var host = el("bsNewPanel");
        clear(host);
        host.hidden = false;
        host.appendChild(node("h3", "새 서비스"));
        var form = node("div", null, "bizsvc-form");
        var fields = {};
        [["service_key", "서비스 키", 128], ["display_name", "표시명", 255], ["owner_org", "담당 부서", 60],
            ["description", "설명", 500]].forEach(function (f) {
            form.appendChild(node("label", f[1]));
            var input = node("input");
            input.maxLength = f[2];
            fields[f[0]] = input;
            form.appendChild(input);
        });
        form.appendChild(node("label", "등급"));
        fields.tier = tierSelect(null);
        form.appendChild(fields.tier);
        host.appendChild(form);
        host.appendChild(node("div", "담당 부서·설명에 개인 이름·연락처는 넣지 않습니다(저장 거부).", "bizsvc-muted"));
        var bar = node("div", null, "bizsvc-bar");
        bar.appendChild(button("만들기", "btn-primary", function () {
            var body = { service_key: fields.service_key.value.trim(), display_name: fields.display_name.value.trim() };
            if (fields.owner_org.value.trim()) body.owner_org = fields.owner_org.value.trim();
            if (fields.description.value.trim()) body.description = fields.description.value.trim();
            if (fields.tier.value) body.tier = parseInt(fields.tier.value, 10);
            request("POST", ADMIN, body).then(function (data) {
                host.hidden = true;
                cacheNote(data);
                return loadList().then(function () { openDetail(data.service.id, "members"); });
            }).catch(fail);
        }));
        bar.appendChild(button("취소", "btn-secondary", function () { host.hidden = true; }));
        host.appendChild(bar);
    }

    function tierSelect(value) {
        var sel = node("select");
        [["", "미지정"], ["1", "1등급"], ["2", "2등급"], ["3", "3등급"]].forEach(function (t) {
            var o = node("option", t[1]);
            o.value = t[0];
            sel.appendChild(o);
        });
        sel.value = value ? String(value) : "";
        return sel;
    }

    // ── ② 상세 ──────────────────────────────────────────────────────────
    function openDetail(id, tab) {
        st.selected = {};
        return request("GET", API + "/" + encodeURIComponent(id)).then(function (data) {
            st.detail = data;
            st.tab = DETAIL_TABS.some(function (t) { return t[0] === tab && (!t[2] || st.isAdmin); }) ? tab : "members";
            renderDetail();
            loadList();
            syncUrl();
        }).catch(function (err) {
            st.detail = null;
            el("bsDetail").hidden = true;
            fail(err);
            syncUrl();
        });
    }

    function reloadDetail() { return st.detail ? openDetail(st.detail.service.id, st.tab) : Promise.resolve(); }

    function showConflict() {
        notify(CONFLICT_TEXT, "warn");
        var box = el("bsAlert");
        box.appendChild(document.createTextNode(" "));
        box.appendChild(button("다시 불러오기", "btn-secondary", function () { notify(""); reloadDetail(); loadList(); }));
    }

    function renderDetail() {
        var host = clear(el("bsDetail"));
        host.hidden = false;
        var d = st.detail;
        var s = d.service;
        var head = node("div", null, "bizsvc-bar");
        var title = node("div");
        title.appendChild(node("strong", s.display_name));
        title.appendChild(node("div", s.service_key, "bizsvc-muted"));
        head.appendChild(title);
        head.appendChild(node("span", null, "bizsvc-spacer"));
        if (s.state === "disabled") head.appendChild(badge("비활성", "muted"));
        else head.appendChild(s.verified ? badge("확정", "ok") : badge("추정", "info"));
        if (s.state === "ambiguous") head.appendChild(badge("검토 필요", "warn"));
        if (s.monitored) head.appendChild(badge("감시 대상", "info"));
        head.appendChild(button("닫기", "btn-secondary", function () {
            st.detail = null; host.hidden = true; syncUrl(); loadList();
        }));
        host.appendChild(head);
        if (d.hidden_member_count) host.appendChild(node("div", "존 권한 밖 구성원 " + d.hidden_member_count + "개는 이름 없이 건수만 셉니다.", "bizsvc-note"));
        var tabs = node("div", null, "bizsvc-subtabs");
        DETAIL_TABS.forEach(function (t) {
            if (t[2] && !st.isAdmin) return;
            var b = node("button", t[1], "bizsvc-subtab" + (st.tab === t[0] ? " active" : ""));
            b.type = "button";
            b.addEventListener("click", function () { st.tab = t[0]; renderDetail(); syncUrl(); });
            tabs.appendChild(b);
        });
        host.appendChild(tabs);
        var body = node("div");
        host.appendChild(body);
        ({ info: renderInfo, members: renderMembers, map: renderLanes, graph: renderGraph, manage: renderManage,
            history: renderHistory }[st.tab] || renderMembers)(body);
    }

    function dl(host, pairs) {
        var list = node("dl", null, "bizsvc-dl");
        pairs.forEach(function (p) {
            list.appendChild(node("dt", p[0]));
            list.appendChild(node("dd", p[1] === null || p[1] === undefined || p[1] === "" ? "—" : p[1]));
        });
        host.appendChild(list);
    }

    function renderInfo(host) {
        var s = st.detail.service;
        dl(host, [["표시명", s.display_name], ["서비스 키", s.service_key], ["등급", s.tier ? s.tier + "등급" : "미지정"],
            ["담당 부서", s.owner_org], ["설명", s.description], ["감시 대상", s.monitored ? "예" : "아니오"],
            ["상태", L.state[s.state] || s.state], ["확인", s.verified ? "확정" : "추정"], ["출처", L.origin[s.origin] || s.origin],
            ["마지막 변경", fmtTime(s.updated_at) + (s.updated_by ? " · " + s.updated_by : "")], ["version", s.version]]);
        var m = st.detail.metrics;
        host.appendChild(node("h4", "연계 지표(마지막 적용 기준)"));
        if (!m) host.appendChild(node("div", "적용한 자동 계산이 없어 지표가 없습니다.", "bizsvc-note"));
        else dl(host, [["SMS 서버", m.sms_servers], ["확정 / 추정", m.confirmed + " / " + m.inferred],
            ["APM 연계 서버", m.apm_linked_servers], ["APM 없는 서버", m.apm_missing_servers],
            ["APM 인스턴스", m.apm_instances], ["SMS에 못 이은 인스턴스", m.apm_unlinked_instances],
            ["추정 비율", pct(m.inferred_ratio)], ["끊김", m.target_gone], ["충돌", m.conflicts], ["열린 후보", m.open_candidates]]);
        if (st.isAdmin) host.appendChild(button("기본 정보 편집", "btn-primary", function () { renderEdit(host); }));
    }

    function renderEdit(host) {
        var s = st.detail.service;
        clear(host);
        var form = node("div", null, "bizsvc-form");
        var f = {};
        function text(key, label, max) {
            form.appendChild(node("label", label));
            var i = node(key === "description" ? "textarea" : "input");
            i.maxLength = max;
            i.value = s[key] || "";
            f[key] = i;
            form.appendChild(i);
        }
        text("display_name", "표시명", 255);
        text("owner_org", "담당 부서", 60);
        text("description", "설명", 500);
        form.appendChild(node("label", "등급"));
        f.tier = tierSelect(s.tier);
        form.appendChild(f.tier);
        [["monitored", "감시 대상"], ["verified", "확정(확인됨)"], ["disabled", "비활성"]].forEach(function (c) {
            form.appendChild(node("label", c[1]));
            var i = node("input");
            i.type = "checkbox";
            i.checked = c[0] === "disabled" ? s.state === "disabled" : !!s[c[0]];
            f[c[0]] = i;
            form.appendChild(i);
        });
        host.appendChild(form);
        var bar = node("div", null, "bizsvc-bar");
        bar.appendChild(button("저장", "btn-primary", function () {
            var body = { version: s.version, display_name: f.display_name.value.trim(),
                owner_org: f.owner_org.value.trim() || null, description: f.description.value.trim() || null,
                tier: f.tier.value ? parseInt(f.tier.value, 10) : null, monitored: f.monitored.checked,
                verified: f.verified.checked, state: f.disabled.checked ? "disabled" : "active" };
            if (s.state === "ambiguous" && !f.disabled.checked) delete body.state;
            request("PATCH", ADMIN + "/" + s.id, body).then(function (data) { cacheNote(data); reloadDetail(); loadList(); }).catch(fail);
        }));
        bar.appendChild(button("취소", "btn-secondary", function () { renderDetail(); }));
        host.appendChild(bar);
    }

    // 구성원 — 종류 탭 3개 · 선택 · 관리자 동작
    function membersOf(kindTab) {
        return (st.detail.members || []).filter(function (m) {
            if (kindTab === "apm") return m.kind === "apm_instance" || m.kind === "apm_domain";
            return m.kind === kindTab;
        });
    }

    function selectedIds() { return Object.keys(st.selected).map(function (k) { return parseInt(k, 10); }); }

    function renderMembers(host) {
        var tabs = node("div", null, "bizsvc-subtabs");
        KIND_TABS.forEach(function (k) {
            var b = node("button", k[1] + " " + membersOf(k[0]).length, "bizsvc-subtab" + (st.kind === k[0] ? " active" : ""));
            b.type = "button";
            b.addEventListener("click", function () { st.kind = k[0]; st.selected = {}; renderDetail(); });
            tabs.appendChild(b);
        });
        host.appendChild(tabs);
        var previewHost = node("div");
        if (st.isAdmin) host.appendChild(memberActions(previewHost));
        host.appendChild(previewHost);
        var rows = membersOf(st.kind);
        var wrap = node("div", null, "bizsvc-tablewrap");
        var table = node("table", null, "bizsvc-table");
        var cols = st.kind === "sms_server"
            ? ["서버명", "hostname", "IP", "존 / DB", "업무코드", "계층", "근거", "확신도", "상태"]
            : st.kind === "apm" ? ["종류", "소스", "도메인", "인스턴스", "출처 매핑", "확신도", "상태"]
                : ["소스", "도메인", "업무 id", "확신도", "상태"];
        var hr = node("tr");
        if (st.isAdmin) hr.appendChild(node("th", "선택"));
        cols.forEach(function (c) { hr.appendChild(node("th", c)); });
        var thead = node("thead");
        thead.appendChild(hr);
        table.appendChild(thead);
        var tbody = node("tbody");
        rows.forEach(function (m) {
            var tr = node("tr");
            if (st.isAdmin) {
                var c = node("td");
                var cb = node("input");
                cb.type = "checkbox";
                cb.checked = !!st.selected[m.id];
                cb.addEventListener("change", function () { if (cb.checked) st.selected[m.id] = m; else delete st.selected[m.id]; });
                c.appendChild(cb);
                tr.appendChild(c);
            }
            if (st.kind === "sms_server") {
                var name = td(tr, m.server_name);
                if (m.owner_branch) name.appendChild(node("div", "담당 부점(ITAM 제안) " + m.owner_branch, "bizsvc-muted"));
                td(tr, m.hostname); td(tr, m.ip); td(tr, (m.zone || "존 미상") + " / " + (m.db_id || "—"));
                td(tr, m.prefix_code); td(tr, layerName(m.layer)); td(tr, L.basis[m.basis] || m.basis);
            } else if (st.kind === "apm") {
                td(tr, m.kind === "apm_domain" ? "도메인" : "인스턴스"); td(tr, m.source_id);
                td(tr, (m.domain_name || "") + (m.domain_id ? " (" + m.domain_id + ")" : ""));
                td(tr, m.instance_name); td(tr, m.apm_map_id ? "#" + m.apm_map_id : "—");
            } else {
                var parts = String(m.member_key || "").split("|");
                td(tr, m.source_id || parts[0]); td(tr, m.domain_id || parts[1]); td(tr, parts[2]);
            }
            var conf = node("td");
            conf.appendChild(m.confidence === "inferred" ? badge("추정", "info") : badge("확정", "ok"));
            tr.appendChild(conf);
            var stt = node("td");
            stt.appendChild(badge(L.memberState[m.state] || m.state, m.state === "active" ? "ok" : m.state === "excluded" ? "muted" : "err"));
            tr.appendChild(stt);
            tbody.appendChild(tr);
        });
        if (!rows.length) {
            var tr0 = node("tr");
            var c0 = node("td", "이 종류의 구성원이 없습니다.", "bizsvc-empty");
            c0.colSpan = cols.length + (st.isAdmin ? 1 : 0);
            tr0.appendChild(c0);
            tbody.appendChild(tr0);
        }
        table.appendChild(tbody);
        wrap.appendChild(table);
        host.appendChild(wrap);
        if (st.kind === "apm") host.appendChild(node("div", "APM·SMS 연계 매핑은 여기서 만들지 않습니다 — 관리자 화면 「APM·SMS 연계」 탭에서 수정하세요.", "bizsvc-note"));
    }

    function layerName(v) {
        var hit = LAYERS.filter(function (l) { return l[0] === v; })[0];
        return hit ? hit[1] : "미지정";
    }

    function memberActions(previewHost) {
        var s = st.detail.service;
        var bar = node("div", null, "bizsvc-bar");
        function needSel(fn) {
            return function () {
                var ids = selectedIds();
                if (!ids.length) { notify("구성원을 먼저 고르세요.", "warn"); return; }
                if (ids.length > MAX_BULK) { notify("한 번에 " + MAX_BULK + "개까지입니다.", "warn"); return; }
                fn(ids);
            };
        }
        function op(path, extra) {
            return needSel(function (ids) {
                request("POST", ADMIN + "/" + s.id + path, Object.assign({ version: s.version, member_ids: ids }, extra))
                    .then(function (data) { cacheNote(data); reloadDetail(); loadList(); }).catch(fail);
            });
        }
        bar.appendChild(button("확인", "btn-secondary", op("/members/confirm", { confirmed: true })));
        bar.appendChild(button("확인 취소", "btn-secondary", op("/members/confirm", { confirmed: false })));
        bar.appendChild(button("제외", "btn-secondary", op("/members/state", { state: "excluded" })));
        bar.appendChild(button("제외 취소", "btn-secondary", op("/members/state", { state: "active" })));
        bar.appendChild(button("다른 서비스로", "btn-secondary", needSel(function (ids) { moveForm(previewHost, ids); })));
        bar.appendChild(button("계층 지정", "btn-secondary", needSel(function (ids) { layerForm(previewHost, ids); })));
        bar.appendChild(button("분할", "btn-secondary", needSel(function (ids) { splitForm(previewHost, ids); })));
        if (st.kind === "sms_server") bar.appendChild(button("서버 찾아 추가", "btn-primary", function () { searchForm(previewHost, "sms"); }));
        if (st.kind === "apm") bar.appendChild(button("인스턴스 찾아 추가", "btn-primary", function () { searchForm(previewHost, "apm"); }));
        if (st.kind === "apm_business") bar.appendChild(button("제니퍼 업무 추가", "btn-primary", function () { businessForm(previewHost); }));
        return bar;
    }

    function moveForm(host, ids) {
        var s = st.detail.service;
        clear(host);
        var bar = node("div", null, "bizsvc-bar");
        var sel = node("select");
        sel.appendChild(node("option", "옮길 서비스 고르기"));
        sel.options[0].value = "";
        bar.appendChild(sel);
        var out = node("div");
        bar.appendChild(button("미리보기", "btn-primary", function () {
            var to = parseInt(sel.value, 10);
            if (!to) { notify("옮길 서비스를 고르세요.", "warn"); return; }
            request("GET", API + "/" + to).then(function (target) {
                previewPanel(out, "구성원 옮기기", [
                    "선택 구성원 " + ids.length + "개를 「" + s.display_name + "」에서 「" + target.service.display_name + "」로 옮깁니다.",
                    "옮긴 구성원은 관리자 지정(재계산이 되돌리지 않음)이 됩니다.",
                ], "옮기기", function () {
                    return request("POST", ADMIN + "/" + s.id + "/members/move", { version: s.version, member_ids: ids,
                        to_service_id: to, to_version: target.service.version })
                        .then(function (data) { cacheNote(data); reloadDetail(); loadList(); });
                });
            }).catch(fail);
        }));
        bar.appendChild(button("닫기", "btn-secondary", function () { clear(host); }));
        host.appendChild(bar);
        host.appendChild(out);
        fillServiceSelect(sel, s.id);
    }

    function layerForm(host, ids) {
        var s = st.detail.service;
        clear(host);
        var bar = node("div", null, "bizsvc-bar");
        var sel = node("select");
        LAYERS.concat([["", "미지정"]]).forEach(function (l) { var o = node("option", l[1]); o.value = l[0]; sel.appendChild(o); });
        bar.appendChild(sel);
        var out = node("div");
        bar.appendChild(button("미리보기", "btn-primary", function () {
            var layer = sel.value || null;
            var changing = ids.filter(function (id) { return (st.selected[id] || {}).layer !== layer; }).length;
            previewPanel(out, "계층 일괄 지정", [
                "선택 구성원 " + ids.length + "개의 계층을 「" + layerName(layer) + "」로 둡니다(바뀌는 구성원 " + changing + "개).",
                "관리자 지정 계층은 재계산이 덮지 않습니다.",
            ], "지정", function () {
                return request("PATCH", ADMIN + "/" + s.id + "/members/layer", { version: s.version, member_ids: ids, layer: layer })
                    .then(function (data) { cacheNote(data); reloadDetail(); });
            });
        }));
        bar.appendChild(button("닫기", "btn-secondary", function () { clear(host); }));
        host.appendChild(bar);
        host.appendChild(out);
    }

    function splitForm(host, ids) {
        var s = st.detail.service;
        clear(host);
        var form = node("div", null, "bizsvc-form");
        var key = node("input"); key.maxLength = 128;
        var name = node("input"); name.maxLength = 255;
        form.appendChild(node("label", "새 서비스 키")); form.appendChild(key);
        form.appendChild(node("label", "새 표시명")); form.appendChild(name);
        host.appendChild(form);
        var out = node("div");
        var bar = node("div", null, "bizsvc-bar");
        bar.appendChild(button("미리보기", "btn-primary", function () {
            if (!key.value.trim() || !name.value.trim()) { notify("새 서비스 키와 표시명을 넣으세요.", "warn"); return; }
            previewPanel(out, "분할", [
                "선택 구성원 " + ids.length + "개를 「" + s.display_name + "」에서 새 서비스 「" + name.value.trim() + "」(" + key.value.trim() + ")로 옮깁니다.",
                "「" + s.display_name + "」에는 구성원 " + Math.max(0, (st.detail.members || []).length - ids.length) + "개가 남습니다.",
            ], "분할", function () {
                return request("POST", ADMIN + "/" + s.id + "/split", { version: s.version, member_ids: ids,
                    service_key: key.value.trim(), display_name: name.value.trim() })
                    .then(function (data) { cacheNote(data); loadList(); return openDetail(data.service.id, "members"); });
            });
        }));
        bar.appendChild(button("닫기", "btn-secondary", function () { clear(host); }));
        host.appendChild(bar);
        host.appendChild(out);
    }

    function searchForm(host, kind) {
        var s = st.detail.service;
        clear(host);
        var bar = node("div", null, "bizsvc-bar");
        var q = node("input");
        q.type = "search";
        q.maxLength = 64;
        q.placeholder = kind === "sms" ? "서버명·hostname·IP (2자 이상)" : "도메인·인스턴스·IP (2자 이상)";
        bar.appendChild(q);
        var out = node("div");
        var preview = node("div");
        function find() {
            var text = q.value.trim();
            if (text.length < 2) { notify("검색어는 2자 이상입니다.", "warn"); return; }
            request("GET", ADMIN + "/inventory/" + kind + qs({ q: text })).then(function (data) {
                clear(out);
                if (data.reason) { out.appendChild(node("div", data.reason, "bizsvc-note")); return; }
                if (data.truncated) out.appendChild(node("div", "결과가 " + data.limit + "건을 넘어 앞부분만 보입니다 — 검색어를 좁히세요.", "bizsvc-note"));
                var table = node("table", null, "bizsvc-table");
                var tbody = node("tbody");
                (data.rows || []).forEach(function (r) {
                    var tr = node("tr");
                    if (kind === "sms") { td(tr, r.server_name); td(tr, r.hostname); td(tr, r.ip); td(tr, r.zone); }
                    else { td(tr, r.source_id); td(tr, (r.domain_name || "") + " (" + (r.domain_id || "") + ")"); td(tr, r.instance_name); td(tr, r.ip); }
                    var act = node("td");
                    var owner = r.owner;
                    if (owner && owner.service_id === s.id) act.appendChild(node("span", "이 서비스 구성원", "bizsvc-muted"));
                    else if (kind === "sms" && owner) {
                        act.appendChild(node("span", "「" + owner.service_name + "」 소속 ", "bizsvc-muted"));
                        act.appendChild(button("옮기기", "btn-secondary", function () {
                            previewPanel(preview, "서버 옮기기", ["「" + (r.server_name || r.resource_id) + "」을(를) 「" + owner.service_name + "」에서 「" + s.display_name + "」로 옮깁니다."],
                                "옮기기", function () { return assignTo(s.id, s.version, [{ db_id: r.db_id, resource_id: r.resource_id }]).then(function () { find(); }); });
                        }));
                    } else if (owner) act.appendChild(node("span", "「" + owner.service_name + "」 소속 — 그 서비스에서 「다른 서비스로」로 옮기세요", "bizsvc-muted"));
                    else if (!r.member_key) act.appendChild(node("span", "키를 만들 수 없는 행", "bizsvc-muted"));
                    else act.appendChild(button("추가", "btn-primary", function () { addMember(kind, r).then(find); }));
                    tr.appendChild(act);
                    tbody.appendChild(tr);
                });
                if (!(data.rows || []).length) { var e = node("tr"); e.appendChild(node("td", "일치하는 행이 없습니다.", "bizsvc-empty")); tbody.appendChild(e); }
                table.appendChild(tbody);
                out.appendChild(table);
            }).catch(fail);
        }
        q.addEventListener("keydown", function (e) { if (e.key === "Enter") find(); });
        bar.appendChild(button("찾기", "btn-primary", find));
        bar.appendChild(button("닫기", "btn-secondary", function () { clear(host); }));
        host.appendChild(bar);
        host.appendChild(preview);
        host.appendChild(out);
    }

    function addMember(kind, r) {
        var s = st.detail.service;
        var body = kind === "sms"
            ? { version: s.version, kind: "sms_server", db_id: r.db_id, resource_id: r.resource_id }
            : { version: s.version, kind: "apm_instance", source_id: r.source_id || "", domain_id: r.domain_id || "",
                scope: "instance", instance_name: r.instance_name || "", domain_name: r.domain_name || "" };
        return request("POST", ADMIN + "/" + s.id + "/members", body).then(function (data) {
            cacheNote(data);
            st.detail.service = data.service;
            return reloadDetail();
        }).catch(fail);
    }

    function businessForm(host) {
        var s = st.detail.service;
        clear(host);
        var form = node("div", null, "bizsvc-form");
        var f = {};
        [["source_id", "소스 id"], ["domain_id", "도메인 id"], ["business_id", "업무 id"]].forEach(function (c) {
            form.appendChild(node("label", c[1]));
            f[c[0]] = node("input");
            form.appendChild(f[c[0]]);
        });
        host.appendChild(form);
        var bar = node("div", null, "bizsvc-bar");
        bar.appendChild(button("추가", "btn-primary", function () {
            request("POST", ADMIN + "/" + s.id + "/members", { version: s.version, kind: "apm_business",
                source_id: f.source_id.value.trim(), domain_id: f.domain_id.value.trim(), business_id: f.business_id.value.trim() })
                .then(function (data) { cacheNote(data); reloadDetail(); }).catch(fail);
        }));
        bar.appendChild(button("닫기", "btn-secondary", function () { clear(host); }));
        host.appendChild(bar);
    }

    function assignTo(serviceId, version, servers) {
        return request("POST", ADMIN + "/bulk-assign", { service_id: serviceId, version: version, servers: servers })
            .then(function (data) {
                var c = data.counts || {};
                notify("지정했습니다 — 추가 " + (c.added || 0) + " · 옮김 " + (c.moved || 0) + " · 그대로 " + (c.kept || 0)
                    + (data.cache_ok === false ? " (사본 미갱신: " + (data.cache_reason || "") + ")" : ""), data.cache_ok === false ? "warn" : "ok");
                if (st.detail && st.detail.service.id === serviceId) reloadDetail();
                loadList();
                return data;
            });
    }

    // 구성 맵 — 계층 레인(활성 구성원)
    function renderLanes(host) {
        var lanes = node("div", null, "bizsvc-lanes");
        var active = (st.detail.members || []).filter(function (m) { return m.state === "active"; });
        LAYERS.concat([[null, "미지정"]]).forEach(function (l) {
            var lane = node("div", null, "bizsvc-lane");
            var items = active.filter(function (m) { return (m.layer || null) === l[0]; });
            lane.appendChild(node("h4", l[1] + " " + items.length));
            items.forEach(function (m) {
                var label = m.server_name || m.instance_name || m.domain_name || m.member_key;
                var chip = node("span", label + (m.confidence === "inferred" ? " · 추정" : ""), "bizsvc-chip");
                chip.title = m.kind;
                lane.appendChild(chip);
            });
            lanes.appendChild(lane);
        });
        host.appendChild(lanes);
        if (st.detail.hidden_member_count) host.appendChild(node("div", "권한 밖 구성원 " + st.detail.hidden_member_count + "개는 레인에 넣지 않았습니다.", "bizsvc-note"));
    }

    // 연계 그래프 — 서비스 → 도메인 → 인스턴스 → 서버(트리 표)
    function lineBadge(line) { return badge(L.line[line] || line, line === "confirmed" ? "ok" : line === "inferred" ? "info" : "err"); }

    function renderGraph(host) {
        var g = st.detail.link_graph || {};
        if (!g.available) { host.appendChild(node("div", g.reason || "연계 그래프를 그릴 수 없습니다.", "bizsvc-note")); return; }
        var table = node("table", null, "bizsvc-table bizsvc-tree");
        var thead = node("thead");
        var hr = node("tr");
        ["노드", "선", "비고"].forEach(function (h) { hr.appendChild(node("th", h)); });
        thead.appendChild(hr);
        table.appendChild(thead);
        var tbody = node("tbody");
        (g.domains || []).forEach(function (dom) {
            var tr = node("tr");
            td(tr, "도메인 " + (dom.domain_name || dom.domain_id) + " (" + dom.source_id + ")");
            td(tr, "");
            td(tr, dom.in_service ? "도메인 구성원" : "");
            tbody.appendChild(tr);
            (dom.instances || []).forEach(function (inst) {
                var ti = node("tr");
                td(ti, "인스턴스 " + (inst.instance_name || inst.member_key), "lvl1");
                var lc = node("td"); lc.appendChild(lineBadge(inst.line)); ti.appendChild(lc);
                td(ti, inst.map_id ? "연계 매핑 #" + inst.map_id : "연계 매핑 없음");
                tbody.appendChild(ti);
                (inst.servers || []).forEach(function (srv) {
                    var ts = node("tr");
                    td(ts, "서버 " + (srv.server_name || srv.resource_id) + " (" + srv.db_id + ")", "lvl2");
                    var sc = node("td"); sc.appendChild(lineBadge(srv.line)); ts.appendChild(sc);
                    td(ts, srv.in_service ? "이 서비스 구성원" : "구성원 아님");
                    tbody.appendChild(ts);
                });
            });
        });
        if (!(g.domains || []).length) { var e = node("tr"); e.appendChild(node("td", "APM 구성원이 없습니다.", "bizsvc-empty")); tbody.appendChild(e); }
        table.appendChild(tbody);
        var wrap = node("div", null, "bizsvc-tablewrap");
        wrap.appendChild(table);
        host.appendChild(wrap);
        var un = g.unlinked_servers || [];
        host.appendChild(node("div", "APM이 이어지지 않은 서버 " + un.length + "대" + (un.length ? ": " + un.slice(0, 20).map(function (u) { return u.server_name || u.db_id; }).join(", ") + (un.length > 20 ? " …" : "") : ""), "bizsvc-note"));
        if (g.hidden_server_count) host.appendChild(node("div", "권한 밖 서버 " + g.hidden_server_count + "대는 이름 없이 셉니다.", "bizsvc-note"));
    }

    // 관리 — 병합 · APM 대조 · 게이트웨이 업무 매핑 사본
    function renderManage(host) {
        var s = st.detail.service;
        var merge = node("div", null, "bizsvc-card");
        merge.appendChild(node("h3", "병합"));
        var bar = node("div", null, "bizsvc-bar");
        var sel = node("select");
        sel.appendChild(node("option", "합칠 대상 서비스 고르기"));
        sel.options[0].value = "";
        bar.appendChild(sel);
        var out = node("div");
        bar.appendChild(button("미리보기", "btn-primary", function () {
            var to = parseInt(sel.value, 10);
            if (!to) { notify("합칠 대상 서비스를 고르세요.", "warn"); return; }
            request("GET", API + "/" + to).then(function (target) {
                previewPanel(out, "병합", [
                    "「" + s.display_name + "」의 구성원 " + (st.detail.members || []).length + "개를 「" + target.service.display_name + "」로 옮깁니다.",
                    "「" + s.display_name + "」는 비활성이 됩니다(되돌리려면 분할·옮기기).",
                ], "병합", function () {
                    return request("POST", ADMIN + "/" + s.id + "/merge", { version: s.version, into_service_id: to, into_version: target.service.version })
                        .then(function (data) { cacheNote(data); loadList(); return openDetail(to, "members"); });
                });
            }).catch(fail);
        }));
        merge.appendChild(bar);
        merge.appendChild(out);
        host.appendChild(merge);
        fillServiceSelect(sel, s.id);

        var cmp = node("div", null, "bizsvc-card");
        cmp.appendChild(node("h3", "APM 대조"));
        cmp.appendChild(node("div", "서비스 표시명으로 게이트웨이 업무명 해석을 1회 부르고 APM 구성원과의 차이를 보입니다. 해석에만 있는 인스턴스는 후보로만 남깁니다.", "bizsvc-note"));
        var cmpOut = node("div");
        cmp.appendChild(button("대조 실행", "btn-primary", function () {
            clear(cmpOut).appendChild(node("div", "게이트웨이를 부르는 중…", "bizsvc-muted"));
            request("POST", ADMIN + "/" + s.id + "/apm-compare").then(function (data) {
                clear(cmpOut);
                function list(title, items) {
                    cmpOut.appendChild(node("div", title + " " + (items || []).length, "bizsvc-note"));
                    (items || []).slice(0, 50).forEach(function (it) {
                        cmpOut.appendChild(node("span", typeof it === "string" ? it : (it.instance_name || it.member_key || JSON.stringify(it)), "bizsvc-chip"));
                    });
                }
                list("일치", data.matched);
                list("해석에만 있음(후보로 남김)", data.only_in_gateway);
                list("구성원에만 있음", data.only_in_members);
                var c = data.candidates || {};
                cmpOut.appendChild(node("div", "후보 새로 " + (c.inserted || 0) + " · 이미 있음 " + (c.existing || 0) + (c.truncated ? " · 상한으로 잘림" : ""), "bizsvc-muted"));
            }).catch(function (err) { clear(cmpOut); fail(err); });
        }));
        cmp.appendChild(cmpOut);
        host.appendChild(cmp);

        var gw = node("div", null, "bizsvc-card");
        gw.appendChild(node("h3", "게이트웨이 업무 매핑 사본(YAML)"));
        gw.appendChild(node("div", "확정 APM 인스턴스 구성원으로 만든 사본입니다. 본체는 게이트웨이 파일을 쓰지 않습니다 — 붙여 넣을지는 관리자가 정합니다.", "bizsvc-note"));
        var gwOut = node("div");
        gw.appendChild(button("사본 만들기", "btn-secondary", function () {
            request("GET", ADMIN + "/" + s.id + "/gateway-business-map").then(function (data) {
                clear(gwOut);
                var ta = node("textarea", null, "bizsvc-yaml");
                ta.readOnly = true;
                ta.value = data.yaml || "";
                gwOut.appendChild(ta);
                gwOut.appendChild(button("복사", "btn-secondary", function () {
                    if (navigator.clipboard) navigator.clipboard.writeText(ta.value).then(function () { notify("복사했습니다.", "ok"); }, function () { notify("복사하지 못했습니다 — 직접 선택해 복사하세요.", "warn"); });
                    else { ta.select(); notify("자동 복사를 쓸 수 없습니다 — 선택된 내용을 복사하세요.", "warn"); }
                }));
                gwOut.appendChild(node("div", "인스턴스 " + (data.instance_count || 0) + "개" + ((data.skipped || []).length ? " · 뺀 구성원 " + data.skipped.length + "개" : ""), "bizsvc-muted"));
            }).catch(fail);
        }));
        gw.appendChild(gwOut);
        host.appendChild(gw);
    }

    function renderHistory(host) {
        var s = st.detail.service;
        host.appendChild(node("div", "불러오는 중…", "bizsvc-muted"));
        request("GET", ADMIN + "/" + s.id + "/history").then(function (data) {
            clear(host);
            if (data.reason) { host.appendChild(node("div", data.reason, "bizsvc-note")); return; }
            host.appendChild(node("div", "최근 " + (data.rows || []).length + "건(감사 기록 " + data.scanned + "건 안에서 찾음)", "bizsvc-muted"));
            var table = node("table", null, "bizsvc-table");
            var tbody = node("tbody");
            (data.rows || []).forEach(function (r) {
                var tr = node("tr");
                td(tr, fmtTime(r.at)); td(tr, r.user_id); td(tr, L.action[r.action] || r.action);
                var oc = node("td");
                oc.appendChild(badge(L.outcome[r.outcome] || r.outcome, r.outcome === "ok" ? "ok" : "err"));
                if (r.reason) oc.appendChild(node("span", r.reason, "bizsvc-muted"));
                tr.appendChild(oc);
                var ba = node("td");
                ba.style.whiteSpace = "normal";
                ba.appendChild(node("pre", "전: " + JSON.stringify(r.before) + "\n후: " + JSON.stringify(r.after), "bizsvc-pre"));
                if (r.batch_id) ba.appendChild(node("div", "묶음 " + String(r.batch_id).slice(0, 8), "bizsvc-muted"));
                tr.appendChild(ba);
                tbody.appendChild(tr);
            });
            if (!(data.rows || []).length) { var e = node("tr"); e.appendChild(node("td", "변경 이력이 없습니다.", "bizsvc-empty")); tbody.appendChild(e); }
            table.appendChild(tbody);
            host.appendChild(table);
        }).catch(function (err) { clear(host); fail(err); });
    }

    // ── ③ 검토함 ────────────────────────────────────────────────────────
    function loadReviewBadge() {
        request("GET", ADMIN + "/candidates" + qs({ status: "open", limit: 1 })).then(function (data) {
            var b = el("bsReviewBadge");
            b.hidden = !data.total;
            b.textContent = String(data.total || 0);
        }).catch(function () { /* 배지는 보조 표시 — 검토함을 열면 사유가 보인다 */ });
    }

    function loadCandidates() {
        st.candSel = {};
        el("rvAll").checked = false;
        return request("GET", ADMIN + "/candidates" + qs({ status: "open", kind: el("rvKind").value, limit: MAX_BULK })).then(function (data) {
            st.cands = data.rows || [];
            el("rvCount").textContent = "열린 " + (data.total || 0) + "건" + (data.total > st.cands.length ? " (앞 " + st.cands.length + "건 표시)" : "");
            renderCandidates();
            loadReviewBadge();
        }).catch(fail);
    }

    function signalChips(signals) {
        var wrap = node("span");
        (signals || []).slice().sort(function (a, b) { return (b.anchors || 0) - (a.anchors || 0); }).forEach(function (sig) {
            var chip = node("span", (L.basis[sig.basis] || sig.basis) + (sig.anchors ? " " + sig.anchors + "대" : ""), "bizsvc-chip");
            chip.title = (sig.bundle_key ? "묶음 " + sig.bundle_key : "") + (sig.purity !== null && sig.purity !== undefined ? " · 순도 " + pct(sig.purity) : "");
            wrap.appendChild(chip);
        });
        return wrap;
    }

    function renderCandidates() {
        var tbody = clear(el("rvCandRows"));
        st.cands.forEach(function (c) {
            var tr = node("tr");
            var cc = node("td");
            var cb = node("input");
            cb.type = "checkbox";
            cb.checked = !!st.candSel[c.id];
            cb.addEventListener("change", function () { if (cb.checked) st.candSel[c.id] = c; else delete st.candSel[c.id]; });
            cc.appendChild(cb);
            tr.appendChild(cc);
            td(tr, L.candKind[c.kind] || c.kind);
            td(tr, c.service_id ? serviceName(c.service_id) : (c.evidence && c.evidence.service_key) || "(새 서비스)");
            td(tr, c.kind === "merge" ? "← " + (c.other_service_id ? serviceName(c.other_service_id) : "") : ((c.evidence && c.evidence.display && (c.evidence.display.server_name || c.evidence.display.instance_name)) || c.member_key));
            var sc = node("td");
            sc.appendChild(signalChips(c.signals));
            tr.appendChild(sc);
            td(tr, fmtTime(c.updated_at));
            tbody.appendChild(tr);
        });
        if (!st.cands.length) { var e = node("tr"); var c0 = node("td", "열린 후보가 없습니다.", "bizsvc-empty"); c0.colSpan = 6; e.appendChild(c0); tbody.appendChild(e); }
    }

    function previewCandidates(action) {
        var picked = Object.keys(st.candSel).map(function (k) { return st.candSel[k]; });
        if (!picked.length) { notify("후보를 먼저 고르세요.", "warn"); return; }
        if (picked.length > MAX_BULK) { notify("한 번에 " + MAX_BULK + "건까지입니다.", "warn"); return; }
        var by = {};
        picked.forEach(function (c) { by[c.kind] = (by[c.kind] || 0) + 1; });
        var lines = Object.keys(by).map(function (k) { return (L.candKind[k] || k) + " " + by[k] + "건"; });
        if (action === "accept") {
            lines.push("구성원 후보는 그 서비스 구성원(관리자 지정 · 확정)이 되고, 병합 후보는 두 서비스를 합칩니다.");
            if (by.apm_unlinked) lines.push("APM 미연계 " + by.apm_unlinked + "건은 수락할 수 없습니다 — 「APM·SMS 연계」 탭에서 매핑을 만드세요.");
        } else lines.push("거절은 기억합니다 — 재계산이 같은 후보를 다시 내지 않습니다.");
        previewPanel(el("rvCandPreview"), action === "accept" ? "후보 수락" : "후보 거절", lines, action === "accept" ? "수락" : "거절", function () {
            return request("POST", ADMIN + "/candidates/bulk", { action: action, candidate_ids: picked.map(function (c) { return c.id; }) })
                .then(function (data) {
                    var c = data.counts || {};
                    notify("처리했습니다 — 성공 " + (c.ok || 0) + " · 거부 " + (c.denied || 0) + " · 실패 " + (c.failed || 0), c.denied || c.failed ? "warn" : "ok");
                    loadCandidates();
                    loadList();
                });
        });
    }

    function loadUnassigned() {
        st.unSel = {};
        return request("GET", ADMIN + "/unassigned").then(function (data) {
            st.unassigned = data;
            renderUnassigned();
        }).catch(fail);
    }

    function renderUnassigned() {
        var host = clear(el("rvUnassigned"));
        var data = st.unassigned || {};
        if (data.reason) { host.appendChild(node("div", data.reason, "bizsvc-note")); return; }
        el("rvUnassignedCount").textContent = "미소속 " + (data.total || 0) + "대" + (data.truncated ? " (앞 " + data.limit + "대 표시)" : "");
        (data.groups || []).forEach(function (g) {
            var card = node("div", null, "bizsvc-card");
            var head = node("div", null, "bizsvc-bar");
            var all = node("input");
            all.type = "checkbox";
            all.title = "묶음 전체 선택";
            head.appendChild(all);
            head.appendChild(node("strong", (g.zone || "존 미상") + " · " + (g.prefix_code ? "접두 " + g.prefix_code : "코드 없음") + " · " + g.count + "대"));
            card.appendChild(head);
            var boxes = [];
            var list = node("div");
            (g.servers || []).forEach(function (srv) {
                var line = node("label", null, "bizsvc-chip");
                var cb = node("input");
                cb.type = "checkbox";
                cb.checked = !!st.unSel[srv.member_key];
                cb.addEventListener("change", function () { if (cb.checked) st.unSel[srv.member_key] = srv; else delete st.unSel[srv.member_key]; });
                boxes.push([cb, srv]);
                line.appendChild(cb);
                line.appendChild(document.createTextNode(" " + (srv.server_name || srv.resource_id) + (srv.excluded_from ? " (「" + srv.excluded_from.service_name + "」에서 제외됨)" : "")));
                list.appendChild(line);
            });
            all.addEventListener("change", function () {
                boxes.forEach(function (p) { p[0].checked = all.checked; if (all.checked) st.unSel[p[1].member_key] = p[1]; else delete st.unSel[p[1].member_key]; });
            });
            card.appendChild(list);
            host.appendChild(card);
        });
        if (!(data.groups || []).length) host.appendChild(node("div", "미소속 서버가 없습니다.", "bizsvc-note"));
    }

    function previewAssign() {
        var picked = Object.keys(st.unSel).map(function (k) { return st.unSel[k]; });
        var to = parseInt(el("rvTarget").value, 10);
        if (!picked.length) { notify("서버를 먼저 고르세요.", "warn"); return; }
        if (picked.length > MAX_BULK) { notify("한 번에 " + MAX_BULK + "대까지입니다.", "warn"); return; }
        if (!to) { notify("지정할 서비스를 고르세요.", "warn"); return; }
        request("GET", API + "/" + to).then(function (target) {
            var moving = picked.filter(function (p) { return p.excluded_from && p.excluded_from.service_id !== to; }).length;
            previewPanel(el("rvAssignPreview"), "서비스로 지정", [
                picked.length + "대를 「" + target.service.display_name + "」 구성원(관리자 지정 · 확정)으로 둡니다.",
                moving ? "그중 " + moving + "대는 다른 서비스에서 제외된 구성원이라 옮겨집니다." : "다른 서비스에서 옮겨지는 서버는 없습니다.",
                "감사 기록은 서버마다 1줄(같은 묶음 id)입니다.",
            ], "지정", function () {
                return assignTo(to, target.service.version, picked.map(function (p) { return { db_id: p.db_id, resource_id: p.resource_id }; }))
                    .then(function () { loadUnassigned(); });
            });
        }).catch(fail);
    }

    // ── ④ 자동 계산 ─────────────────────────────────────────────────────
    function loadCompute() {
        request("GET", ADMIN + qs({ limit: 1 })).then(function (data) {
            el("cpReason").textContent = data.compute_available ? "" : (data.compute_reason || "계산을 쓸 수 없습니다.");
            el("cpRun").disabled = !data.compute_available;
        }).catch(fail);
        loadRuns();
    }

    function loadRuns() {
        request("GET", ADMIN + "/runs" + qs({ limit: 20 })).then(function (data) {
            var tbody = clear(el("cpRuns"));
            (data.runs || []).forEach(function (r) {
                var tr = node("tr");
                td(tr, String(r.run_id || "").slice(0, 8)); td(tr, r.mode === "apply" ? "적용" : "미리보기");
                var sc = node("td");
                sc.appendChild(badge(r.status, r.status === "done" ? "ok" : r.status === "running" ? "info" : "err"));
                tr.appendChild(sc);
                td(tr, fmtTime(r.started_at)); td(tr, fmtTime(r.finished_at)); td(tr, r.requested_by);
                var diff = r.summary && r.summary.diff;
                td(tr, diff && diff.total_changes !== undefined ? diff.total_changes : "—");
                tbody.appendChild(tr);
            });
        }).catch(fail);
    }

    function countLine(title, obj) {
        var parts = Object.keys(obj || {}).map(function (k) { return k + " " + obj[k]; });
        return title + ": " + (parts.length ? parts.join(" · ") : "없음");
    }

    function runPreview() {
        var out = clear(el("cpResult"));
        el("cpApply").disabled = true;
        st.preview = null;
        el("cpStatus").textContent = "계산 중…";
        request("POST", ADMIN + "/preview").then(function (data) {
            st.preview = data;
            el("cpStatus").textContent = "미리보기 " + String(data.run_id || "").slice(0, 8) + " · 기준 스캔 " + (data.scan_run_id || "—");
            var sv = data.services || {};
            var lines = [
                "변경 합계 " + (data.total_changes || 0) + "건",
                "서비스: 신규 " + (sv.new || []).length + " · 변경 " + (sv.changed || []).length + " · 삭제 " + (sv.removed || []).length,
                countLine("구성원", (data.members || {}).by_action),
                countLine("후보", (data.candidates || {}).by_kind),
                "스캔 제안 " + (data.scan_suggests || []).length + "건 · 충돌 " + (data.conflicts || 0) + "건",
            ];
            lines.forEach(function (l) { out.appendChild(node("div", l, "bizsvc-note")); });
            (data.warnings || []).forEach(function (w) { out.appendChild(node("div", "설정 경고: " + w, "bizsvc-alert bizsvc-alert--warn")); });
            (data.attr_failures || []).forEach(function (f) { out.appendChild(node("div", "보강 읽기 실패: " + (L.basis[f.signal] || f.signal) + " · " + (f.zone || "") + " · " + f.reason, "bizsvc-alert bizsvc-alert--warn")); });
            out.appendChild(signalTable(data.signals || []));
            el("cpApply").disabled = false;
        }).catch(function (err) {
            el("cpStatus").textContent = "";
            if (err.code === "scan_disabled") el("cpReason").textContent = err.message;
            fail(err);
        });
    }

    function signalTable(signals) {
        var wrap = node("div", null, "bizsvc-tablewrap");
        var table = node("table", null, "bizsvc-table");
        var thead = node("thead");
        var hr = node("tr");
        ["존", "신호", "켜짐", "순도", "평가 / 순수", "끈 사유"].forEach(function (h) { hr.appendChild(node("th", h)); });
        thead.appendChild(hr);
        table.appendChild(thead);
        var tbody = node("tbody");
        signals.forEach(function (sg) {
            var tr = node("tr");
            td(tr, sg.zone || "전체"); td(tr, L.basis[sg.signal] || sg.signal);
            var on = node("td"); on.appendChild(sg.enabled ? badge("켜짐", "ok") : badge("꺼짐", "muted")); tr.appendChild(on);
            td(tr, pct(sg.purity)); td(tr, (sg.evaluated || 0) + " / " + (sg.pure || 0)); td(tr, sg.reason_text || sg.reason);
            tbody.appendChild(tr);
        });
        if (!signals.length) { var e = node("tr"); var c0 = node("td", "신호 상태가 없습니다.", "bizsvc-empty"); c0.colSpan = 6; e.appendChild(c0); tbody.appendChild(e); }
        table.appendChild(tbody);
        wrap.appendChild(table);
        return wrap;
    }

    function runApply() {
        if (!st.preview) return;
        var p = st.preview;
        previewPanel(el("cpResult"), "적용", ["미리보기 " + String(p.run_id).slice(0, 8) + "의 변경 " + (p.total_changes || 0) + "건을 저장합니다.",
            "관리자가 지정·확인한 구성원은 바꾸지 않습니다."], "적용", function () {
            el("cpApply").disabled = true;
            return request("POST", ADMIN + "/apply", { preview_run_id: p.run_id }).then(function (data) {
                notify("적용했습니다 — " + countLine("건수", data.applied), data.cache && data.cache.ok === false ? "warn" : "ok");
                st.preview = null;
                loadRuns();
                loadList();
            }).catch(function (err) {
                if (err.code === "snapshot_changed") notify("스냅샷이 바뀌었습니다 — 다시 계산하세요.", "warn");
                else fail(err);
            });
        });
    }

    // ── ⑥ 연계 분석 ─────────────────────────────────────────────────────
    function loadAnalysis() {
        request("GET", ADMIN + "/analysis").then(function (data) {
            el("anReason").textContent = data.reason || ("적용 " + fmtTime(data.finished_at) + " 기준");
            var host = clear(el("anOverall"));
            var summary = data.summary || {};
            var o = summary.overall || {};
            if (data.summary) {
                dl(host, [["서비스", o.services], ["SMS 서버", o.sms_servers], ["소속 서버", o.assigned_servers],
                    ["미소속 서버", o.unassigned_servers], ["APM 연계 서버", o.apm_linked_servers], ["미소속 인스턴스", o.unassigned_instances],
                    ["추정 비율", pct(o.inferred_ratio)], ["끊김", o.target_gone], ["충돌", o.conflicts],
                    ["열린 후보", JSON.stringify(o.open_candidates || {})]]);
            }
            renderCoverage(summary.services || {});
        }).catch(fail);
        request("GET", ADMIN + "/runs" + qs({ mode: "apply", limit: 20 })).then(function (data) {
            var tbody = clear(el("anTrend"));
            (data.runs || []).slice().reverse().forEach(function (r) {
                if (r.status !== "done" || !r.summary || !r.summary.overall) return;
                var o = r.summary.overall;
                var tr = node("tr");
                td(tr, fmtTime(r.finished_at)); td(tr, o.services); td(tr, o.sms_servers); td(tr, o.assigned_servers);
                td(tr, o.unassigned_servers); td(tr, o.apm_linked_servers); td(tr, o.unassigned_instances); td(tr, pct(o.inferred_ratio));
                tbody.appendChild(tr);
            });
        }).catch(fail);
    }

    function renderCoverage(services) {
        var rows = Object.keys(services).map(function (k) {
            var m = services[k];
            return { key: k, m: m, cov: m.sms_servers ? (m.apm_linked_servers || 0) / m.sms_servers : null };
        }).filter(function (r) { return r.cov !== null; }).sort(function (a, b) { return a.cov - b.cov || (a.key < b.key ? -1 : 1); });
        var tbody = clear(el("anCoverage"));
        rows.slice(0, 50).forEach(function (r) {
            var tr = node("tr");
            td(tr, r.key); td(tr, r.m.sms_servers); td(tr, r.m.apm_linked_servers); td(tr, r.m.apm_missing_servers);
            td(tr, pct(r.cov)); td(tr, r.m.apm_unlinked_instances);
            tbody.appendChild(tr);
        });
        if (!rows.length) { var e = node("tr"); var c0 = node("td", "커버리지를 셀 서비스가 없습니다.", "bizsvc-empty"); c0.colSpan = 6; e.appendChild(c0); tbody.appendChild(e); }
    }

    // ── ⑤ CSV 가져오기(관리자 · W5b) ──────────────────────────────────────
    // HTML 정적 영역이 아니라 관리자일 때만 탭·영역을 만든다(일반 사용자 화면에는 없다 — 정본은 서버 403).
    var IMPORT_MAX_BYTES = 1048576;
    var IMPORT_ROWS_SHOWN = 500;
    var IMPORT_TONE = {
        add: "ok", move: "info", keep: "muted", service_only: "muted", not_found: "warn", ambiguous: "warn",
        unknown_db: "err", file_conflict: "err", duplicate: "muted", pii_rejected: "err", invalid: "err",
    };
    var imp = { sec: null, file: null, applyBtn: null, status: null, panel: null, result: null, preview: null };

    function buildImportSection() {
        var tab = node("button", "가져오기", "bizsvc-tab");
        tab.type = "button";
        tab.setAttribute("data-section", "import");
        el("bsSections").appendChild(tab);
        var sec = node("section");
        sec.id = "secImport";
        sec.hidden = true;
        var card = node("div", null, "bizsvc-card");
        card.appendChild(node("h3", "CSV 가져오기"));
        card.appendChild(node("div", "형식: service_key,display_name,tier,owner_org,db_id,server_name,hostname,layer"
            + " (CSV 내보내기와 같습니다) · UTF-8(BOM 허용) · 1MB · 5,000행까지.", "bizsvc-note"));
        card.appendChild(node("div", "현재 스냅샷에서 1대만 일치하는 서버만 구성원(관리자 지정 · 확정)으로 둡니다 — 2대 이상 일치하면"
            + " 고르지 않습니다. 기존 서비스의 표시명·등급·담당 부서는 바꾸지 않습니다.", "bizsvc-note"));
        var bar = node("div", null, "bizsvc-bar");
        imp.file = node("input");
        imp.file.type = "file";
        imp.file.accept = ".csv,text/csv";
        bar.appendChild(imp.file);
        bar.appendChild(button("미리보기", "btn-primary", runImportPreview));
        imp.applyBtn = button("적용", "btn-secondary", previewImportApply);
        imp.applyBtn.disabled = true;
        bar.appendChild(imp.applyBtn);
        imp.status = node("span", "", "bizsvc-muted");
        bar.appendChild(imp.status);
        card.appendChild(bar);
        imp.panel = node("div");
        card.appendChild(imp.panel);
        imp.result = node("div");
        card.appendChild(imp.result);
        sec.appendChild(card);
        document.querySelector(".bizsvc-main").appendChild(sec);
        imp.sec = sec;
    }

    function resetImport() {
        imp.preview = null;
        imp.applyBtn.disabled = true;
        clear(imp.panel);
    }

    function runImportPreview() {
        var f = imp.file.files && imp.file.files[0];
        resetImport();
        clear(imp.result);
        if (!f) { notify("CSV 파일을 고르세요.", "warn"); return; }
        if (f.size > IMPORT_MAX_BYTES) { notify("파일은 1MB까지입니다.", "warn"); return; }
        var form = new FormData();
        form.append("file", f);
        imp.status.textContent = "대조 중…";
        request("POST", ADMIN + "/import/preview", form).then(function (data) {
            imp.preview = data;
            imp.status.textContent = "미리보기 " + String(data.preview_id || "").slice(0, 8) + " · 기준 스캔 " + (data.snapshot_run_id || "—");
            renderImportPreview(data);
            imp.applyBtn.disabled = !(data.applicable || data.creates);
        }).catch(function (err) { imp.status.textContent = ""; fail(err); });
    }

    function importCountLine(data) {
        var labels = data.labels || {};
        var parts = Object.keys(data.counts || {}).filter(function (k) { return data.counts[k]; })
            .map(function (k) { return (labels[k] || k) + " " + data.counts[k]; });
        return "행 " + (data.rows || []).length + "개 — " + (parts.join(" · ") || "판정 없음");
    }

    function renderImportPreview(data) {
        var out = clear(imp.result);
        out.appendChild(node("div", importCountLine(data), "bizsvc-note"));
        out.appendChild(node("div", "적용할 구성원 " + (data.applicable || 0) + "건 · 새 서비스 " + (data.creates || 0) + "개", "bizsvc-note"));
        (data.moves || []).forEach(function (m) {
            out.appendChild(node("div", "옮김: 「" + (m.from_service_name || m.from_service_id) + "」 → " + m.to_service_key + " " + m.count + "대", "bizsvc-note"));
        });
        out.appendChild(importServiceTable(data.services || []));
        var bar = node("div", null, "bizsvc-bar");
        var sel = node("select");
        sel.title = "판정";
        var all = node("option", "전체 판정");
        all.value = "";
        sel.appendChild(all);
        Object.keys(data.counts || {}).forEach(function (k) {
            if (!data.counts[k]) return;
            var o = node("option", ((data.labels || {})[k] || k) + " " + data.counts[k]);
            o.value = k;
            sel.appendChild(o);
        });
        bar.appendChild(sel);
        out.appendChild(bar);
        var host = node("div");
        out.appendChild(host);
        sel.addEventListener("change", function () { importRowTable(host, data.rows || [], sel.value); });
        importRowTable(host, data.rows || [], "");
    }

    function importServiceTable(services) {
        var wrap = node("div", null, "bizsvc-tablewrap");
        var table = node("table", null, "bizsvc-table");
        var hr = node("tr");
        ["서비스 키", "표시명", "구분", "추가", "옮겨 옴", "이미 소속", "비고"].forEach(function (h) { hr.appendChild(node("th", h)); });
        var thead = node("thead");
        thead.appendChild(hr);
        table.appendChild(thead);
        var tbody = node("tbody");
        services.forEach(function (s) {
            var tr = node("tr");
            td(tr, s.service_key); td(tr, s.display_name);
            var kind = node("td");
            kind.appendChild(s.is_new ? badge("새 서비스", "info") : badge(L.state[s.state] || "기존", s.state === "active" ? "muted" : "warn"));
            tr.appendChild(kind);
            td(tr, s.add); td(tr, s.move_in); td(tr, s.keep); td(tr, s.attr_note);
            tbody.appendChild(tr);
        });
        if (!services.length) { var e = node("tr"); var c0 = node("td", "서비스가 없습니다.", "bizsvc-empty"); c0.colSpan = 7; e.appendChild(c0); tbody.appendChild(e); }
        table.appendChild(tbody);
        wrap.appendChild(table);
        return wrap;
    }

    function importRowTable(host, rows, status) {
        clear(host);
        var picked = rows.filter(function (r) { return !status || r.status === status; });
        var wrap = node("div", null, "bizsvc-tablewrap");
        var table = node("table", null, "bizsvc-table");
        var hr = node("tr");
        ["줄", "판정", "서비스 키", "db_id", "서버명", "hostname", "계층", "일치 서버", "사유"].forEach(function (h) { hr.appendChild(node("th", h)); });
        var thead = node("thead");
        thead.appendChild(hr);
        table.appendChild(thead);
        var tbody = node("tbody");
        picked.slice(0, IMPORT_ROWS_SHOWN).forEach(function (r) {
            var tr = node("tr");
            td(tr, r.line);
            var sc = node("td");
            sc.appendChild(badge(r.label || r.status, IMPORT_TONE[r.status] || "muted"));
            tr.appendChild(sc);
            td(tr, r.service_key || "—"); td(tr, r.db_id || "—"); td(tr, r.server_name || "—"); td(tr, r.hostname || "—");
            td(tr, r.layer ? layerName(r.layer) : "—");
            td(tr, r.match ? (r.match.server_name || r.match.hostname || r.match.resource_id) : (r.match_count > 1 ? r.match_count + "대" : "—"));
            td(tr, r.reason || "—");
            tbody.appendChild(tr);
        });
        if (!picked.length) { var e = node("tr"); var c0 = node("td", "해당 행이 없습니다.", "bizsvc-empty"); c0.colSpan = 9; e.appendChild(c0); tbody.appendChild(e); }
        table.appendChild(tbody);
        wrap.appendChild(table);
        host.appendChild(wrap);
        if (picked.length > IMPORT_ROWS_SHOWN) host.appendChild(node("div", "앞 " + IMPORT_ROWS_SHOWN + "행만 보입니다(전체 " + picked.length + "행) — 판정으로 걸러 보세요.", "bizsvc-muted"));
    }

    function previewImportApply() {
        var p = imp.preview;
        if (!p) return;
        var counts = p.counts || {};
        var skipped = (p.rows || []).length - (counts.add || 0) - (counts.move || 0) - (counts.keep || 0) - (counts.service_only || 0);
        var lines = [
            "새 서비스 " + (p.creates || 0) + "개를 만듭니다(관리자 생성).",
            "구성원 추가 " + (counts.add || 0) + "대 · 옮김 " + (counts.move || 0) + "대 · 이미 소속 " + (counts.keep || 0) + "대를 관리자 지정 · 확정으로 둡니다.",
        ];
        (p.moves || []).forEach(function (m) {
            lines.push("「" + (m.from_service_name || m.from_service_id) + "」 → " + m.to_service_key + ": " + m.count + "대가 옮겨집니다.");
        });
        lines.push("적용하지 않는 행 " + Math.max(0, skipped) + "개(스냅샷에 없음 · 고르지 않음 · 등록되지 않은 DB · 파일 안 충돌 등).");
        lines.push("미리보기 뒤 스냅샷이나 대상 서비스가 바뀌었으면 저장하지 않습니다. 감사 기록은 행마다 1줄(같은 묶음 id)입니다.");
        previewPanel(imp.panel, "가져오기 적용", lines, "적용", function () {
            imp.applyBtn.disabled = true;
            return request("POST", ADMIN + "/import/apply", { preview_id: p.preview_id }).then(function (data) {
                var c = data.counts || {};
                cacheNote(data);
                imp.status.textContent = "적용했습니다 — 새 서비스 " + (data.created || []).length + "개 · 추가 " + (c.added || 0)
                    + " · 옮김 " + (c.moved || 0) + " · 유지 " + (c.kept || 0) + " · 묶음 " + String(data.batch_id || "").slice(0, 8);
                imp.preview = null;
                loadList();
            }).catch(function (err) {
                imp.preview = null;
                if (err.code === "snapshot_changed") imp.status.textContent = "미리보기 뒤 스냅샷이 바뀌었습니다 — 다시 미리보기 하세요.";
                else if (err.code === "version_conflict" || err.code === "exists") imp.status.textContent = "다른 사람이 먼저 고쳤습니다 — 다시 미리보기 하세요.";
                else if (err.code === "preview_not_found") imp.status.textContent = "미리보기가 만료됐습니다 — 다시 미리보기 하세요.";
                else { fail(err); return; }
                notify(imp.status.textContent, "warn");
            });
        });
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
    else boot();
})();
