/**
 * 테마 부트스트랩 — 모든 화면의 <head>에서 **동기 로드**해야 한다.
 *
 * 적용 우선순위: 개인 선택(localStorage) > 전역 기본값(운영자 설정) > light.
 * 전역 기본값은 `GET /api/v1/ui/theme`로 읽지만, 응답을 기다리면 첫 페인트가
 * 잘못된 테마로 그려지므로(FOUC) 직전 값을 localStorage에 캐시해 두고 즉시 적용한 뒤
 * 응답이 오면 갱신한다. 개인 선택이 있으면 전역 기본값은 화면에 반영하지 않는다.
 */
(function () {
    "use strict";

    var PERSONAL_KEY = "ui.theme.personal";       // 사용자가 헤더 토글로 고른 값
    var DEFAULT_CACHE_KEY = "ui.theme.default";   // 전역 기본값의 마지막 조회 결과(캐시)
    var FALLBACK = "light";

    function read(key) {
        try {
            var v = window.localStorage.getItem(key);
            return v === "light" || v === "dark" ? v : null;
        } catch (e) {
            return null;   // 프라이빗 모드·저장소 차단 — 기본값 경로로 동작
        }
    }

    function write(key, value) {
        try {
            if (value === null) window.localStorage.removeItem(key);
            else window.localStorage.setItem(key, value);
        } catch (e) { /* 저장 실패는 표시에 영향 없음 */ }
    }

    function apply(theme) {
        document.documentElement.setAttribute("data-theme", theme);
    }

    function effective() {
        return read(PERSONAL_KEY) || read(DEFAULT_CACHE_KEY) || FALLBACK;
    }

    // 1) 첫 페인트 전 즉시 적용
    apply(effective());

    var listeners = [];

    function notify(theme) {
        for (var i = 0; i < listeners.length; i++) {
            try { listeners[i](theme); } catch (e) { /* 구독자 오류 격리 */ }
        }
    }

    // 2) 전역 기본값 갱신 — 개인 선택이 없을 때만 화면에 반영
    function refreshGlobalDefault() {
        return fetch("/api/v1/ui/theme", { credentials: "same-origin" })
            .then(function (res) { return res.ok ? res.json() : null; })
            .then(function (data) {
                if (!data) return null;
                var theme = data.default_theme === "dark" ? "dark" : "light";
                write(DEFAULT_CACHE_KEY, theme);
                if (!read(PERSONAL_KEY)) {
                    apply(theme);
                    notify(theme);
                }
                return theme;
            })
            .catch(function () { return null; });   // 오프라인·미배포 시 캐시/기본값 유지
    }

    window.AppTheme = {
        current: function () {
            return document.documentElement.getAttribute("data-theme") || FALLBACK;
        },
        /** 이 브라우저에만 적용되는 개인 선택을 저장한다. */
        setPersonal: function (theme) {
            var next = theme === "dark" ? "dark" : "light";
            write(PERSONAL_KEY, next);
            apply(next);
            notify(next);
            return next;
        },
        /** 개인 선택을 지우고 전역 기본값으로 되돌린다. */
        clearPersonal: function () {
            write(PERSONAL_KEY, null);
            var next = read(DEFAULT_CACHE_KEY) || FALLBACK;
            apply(next);
            notify(next);
            return next;
        },
        hasPersonal: function () { return read(PERSONAL_KEY) !== null; },
        /** 운영자가 전역 기본값을 바꿨을 때 캐시를 맞춰 둔다(자기 화면 반영은 별개). */
        cacheGlobalDefault: function (theme) {
            write(DEFAULT_CACHE_KEY, theme === "dark" ? "dark" : "light");
        },
        globalDefault: function () { return read(DEFAULT_CACHE_KEY) || FALLBACK; },
        refresh: refreshGlobalDefault,
        onChange: function (fn) { if (typeof fn === "function") listeners.push(fn); },
        toggleValue: function () { return this.current() === "dark" ? "light" : "dark"; }
    };

    refreshGlobalDefault();
})();
