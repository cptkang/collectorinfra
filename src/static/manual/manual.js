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

    // 좁은 화면: 목차 열고 닫기
    var tocBtn = document.querySelector(".toc-toggle");
    if (tocBtn) {
        tocBtn.addEventListener("click", function () { document.body.classList.toggle("toc-open"); });
        document.querySelectorAll(".toc a").forEach(function (a) {
            a.addEventListener("click", function () { document.body.classList.remove("toc-open"); });
        });
    }
})();
