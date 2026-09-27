/* 매뉴얼 페이지 동작 (plans/116 §4.2) — 목차 찾기·현재 절 표시·복사·그림 확대·테마·좁은 화면 목차. */
(function () {
    "use strict";

    // 목차 찾기: 절 제목·ID 에 입력어가 없으면 목차에서 숨긴다(본문은 그대로)
    var filter = document.querySelector(".toc-filter");
    if (filter) {
        filter.addEventListener("input", function () {
            var q = filter.value.trim().toLowerCase();
            document.querySelectorAll(".toc li li").forEach(function (li) {
                li.classList.toggle("hidden", q !== "" && li.textContent.toLowerCase().indexOf(q) < 0);
            });
        });
        filter.addEventListener("keydown", function (e) {
            if (e.key !== "Enter") return;
            var first = document.querySelector(".toc li li:not(.hidden) a");
            if (first) { location.hash = first.getAttribute("href"); }
        });
    }

    // 현재 보고 있는 절을 목차에 표시
    var links = {};
    document.querySelectorAll(".toc li li a").forEach(function (a) { links[a.getAttribute("href").slice(1)] = a; });
    if ("IntersectionObserver" in window) {
        var io = new IntersectionObserver(function (entries) {
            entries.forEach(function (en) {
                if (!en.isIntersecting) return;
                Object.keys(links).forEach(function (k) { links[k].classList.remove("active"); });
                var a = links[en.target.id];
                if (a) { a.classList.add("active"); a.scrollIntoView({ block: "nearest" }); }
            });
        }, { rootMargin: "-20% 0px -70% 0px" });
        document.querySelectorAll(".feature").forEach(function (s) { io.observe(s); });
    }

    // 사례 입력 복사
    document.addEventListener("click", function (e) {
        var btn = e.target.closest(".copy-btn");
        if (!btn) return;
        var text = btn.getAttribute("data-copy") || "";
        function done() { btn.textContent = "복사됨"; btn.classList.add("done");
            setTimeout(function () { btn.textContent = "복사"; btn.classList.remove("done"); }, 1500); }
        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(text).then(done, fallback);
        } else { fallback(); }
        function fallback() {
            var ta = document.createElement("textarea");
            ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
            document.body.appendChild(ta); ta.select();
            try { document.execCommand("copy"); done(); } catch (err) { /* 복사 불가 — 사용자가 직접 선택 */ }
            document.body.removeChild(ta);
        }
    });

    // 테마 — 앱과 같은 개인 선택 키를 쓴다(매뉴얼에서 바꾸면 앱도 따라간다)
    var themeBtn = document.querySelector(".theme-btn");
    if (themeBtn) {
        themeBtn.addEventListener("click", function () {
            var next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
            document.documentElement.setAttribute("data-theme", next);
            try { localStorage.setItem("ui.theme.personal", next); } catch (err) { /* 저장 불가 — 이번 화면만 */ }
        });
    }

    // 그림 확대(라이트박스): 원본 크기로 본다. JS 가 없으면 링크가 새 탭에서 이미지를 연다
    var box = null;
    function closeBox() { if (box) { box.remove(); box = null; } }
    document.addEventListener("click", function (e) {
        var a = e.target.closest("a.zoom");
        if (!a || e.ctrlKey || e.metaKey || e.shiftKey) return;
        e.preventDefault();
        closeBox();
        box = document.createElement("div");
        box.className = "lightbox";
        box.setAttribute("role", "dialog");
        box.setAttribute("aria-label", "그림 원본 보기 — 누르거나 Esc 로 닫기");
        var img = document.createElement("img");
        img.src = a.getAttribute("href");
        img.alt = (a.querySelector("img") || {}).alt || "";
        box.appendChild(img);
        box.addEventListener("click", closeBox);
        document.body.appendChild(box);
    });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeBox(); });

    // 자세히 보기(plans/116 §11.2): 소제목·항목마다 따로 접힘 · 처음엔 전부 접힘 · 펼친 상태는 저장하지 않는다
    var details = Array.prototype.slice.call(document.querySelectorAll(".slot-detail details"))
        .filter(function (d) { return !d.closest(".detail-store"); });  // 본문에 연결돼 숨긴 항목은 빼고
    var allBtn = document.querySelector(".detail-all");
    function syncAllBtn() {
        if (!allBtn) return;
        var allOpen = details.length > 0 && details.every(function (d) { return d.open; });
        allBtn.textContent = allOpen ? "모두 접기" : "모두 펼치기";
        allBtn.setAttribute("aria-pressed", allOpen ? "true" : "false");
    }
    if (allBtn) {
        if (!details.length) { allBtn.hidden = true; }
        allBtn.addEventListener("click", function () {
            var open = allBtn.getAttribute("aria-pressed") !== "true";
            details.forEach(function (d) { d.open = open; });
            syncAllBtn();
        });
    }
    details.forEach(function (d) { d.addEventListener("toggle", syncAllBtn); });

    // 주소 해시가 상세 안(#u-11-d-2 소제목 · #u-11-d-2-3 항목)을 가리키면 그 항목과 바깥 소제목을 열고 그 자리로 간다
    function openHashTarget() {
        var id = decodeURIComponent(location.hash.slice(1));
        var el = id && document.getElementById(id);
        if (!el || !el.closest(".slot-detail")) return;
        if (el.closest(".detail-store")) {  // 본문에 연결된 항목 — 본문 링크를 열어 그 자리에서 보여 준다
            var link = document.querySelector('a.term[data-term="' + id + '"]');
            if (link) {
                if (link.getAttribute("aria-expanded") !== "true") link.click();
                link.scrollIntoView({ block: "center" });
            }
            return;
        }
        for (var d = el.closest("details"); d; d = d.parentElement && d.parentElement.closest("details")) {
            d.open = true;
        }
        el.scrollIntoView();
    }
    window.addEventListener("hashchange", openHashTarget);

    // 인쇄(PDF 저장)는 펼칠 수 없으므로 전부 펼쳐 찍고 원래대로 돌린다
    var beforePrint = null;
    window.addEventListener("beforeprint", function () {
        beforePrint = details.map(function (d) { return d.open; });
        details.forEach(function (d) { d.open = true; });
    });
    window.addEventListener("afterprint", function () {
        if (!beforePrint) return;
        details.forEach(function (d, i) { d.open = beforePrint[i]; });
        beforePrint = null;
    });

    // 본문 속 항목 이름(a.term)을 누르면 그 줄 바로 아래(표면 그 행 아래)에 항목 설명을 펼친다 — 다시 누르면 닫힌다.
    // 표에 이미 그 항목의 일반 설명이 채워져 있으면(span.term-lead) 자세한 설명만 펼친다.
    // JS 가 없거나 Ctrl/⌘ 클릭이면 링크 그대로 상세 설명 항목으로 이동한다.
    function termPanel(term, item, skipLead) {
        var panel = document.createElement("div");
        panel.className = "term-panel";
        panel.setAttribute("data-term", item.id);
        var lead = item.querySelector(".detail-item-lead");
        var more = item.querySelector(".detail-more-body");
        var html = "";
        if (lead && (!skipLead || !more)) html += '<div class="term-panel-lead">' + lead.innerHTML + "</div>";
        if (more) html += '<div class="term-panel-more">' + more.innerHTML + "</div>";
        if (!item.closest(".detail-store")) {
            html += '<a class="term-jump" href="#' + item.id + '">상세 설명에서 보기 ↓</a>';
        }
        panel.innerHTML = html;
        return panel;
    }
    document.addEventListener("click", function (e) {
        var term = e.target.closest("a.term");
        if (!term || e.ctrlKey || e.metaKey || e.shiftKey) return;
        var id = term.getAttribute("data-term");
        var item = document.getElementById(id);
        if (!item) return;
        e.preventDefault();
        var open = term.getAttribute("aria-expanded") === "true";
        var tr = term.closest("td") && term.closest("tr");
        if (tr) {
            var next = tr.nextElementSibling;
            if (next && next.classList.contains("term-row") && next.getAttribute("data-term") === id) {
                next.remove();
            } else {
                var row = document.createElement("tr");
                row.className = "term-row";
                row.setAttribute("data-term", id);
                var cell = document.createElement("td");
                cell.colSpan = tr.children.length;
                cell.appendChild(termPanel(term, item, !!tr.querySelector('.term-lead[data-term="' + id + '"]')));
                row.appendChild(cell);
                tr.parentNode.insertBefore(row, tr.nextSibling);
            }
        } else {
            var host = term.closest("li, p") || term.parentElement;
            var existing = null;
            var sib = host.tagName === "LI" ? host.lastElementChild : host.nextElementSibling;
            if (sib && sib.classList.contains("term-panel") && sib.getAttribute("data-term") === id) existing = sib;
            if (existing) {
                existing.remove();
            } else if (host.tagName === "LI") {
                host.appendChild(termPanel(term, item, false));
            } else {
                host.parentNode.insertBefore(termPanel(term, item, false), host.nextSibling);
            }
        }
        term.setAttribute("aria-expanded", open ? "false" : "true");
    });

    // 첫 해시 처리는 클릭 처리기(본문 항목 펼침)를 등록한 뒤에 한다 — 숨긴 항목은 본문 링크를 눌러 연다
    openHashTarget();

    // 좁은 화면: 목차 열고 닫기
    var tocBtn = document.querySelector(".toc-toggle");
    if (tocBtn) {
        tocBtn.addEventListener("click", function () { document.body.classList.toggle("toc-open"); });
        document.querySelectorAll(".toc a").forEach(function (a) {
            a.addEventListener("click", function () { document.body.classList.remove("toc-open"); });
        });
    }
})();
