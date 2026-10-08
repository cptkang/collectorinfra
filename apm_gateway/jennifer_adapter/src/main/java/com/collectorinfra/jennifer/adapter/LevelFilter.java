package com.collectorinfra.jennifer.adapter;

/**
 * 최소 레벨 필터 — 게이트웨이 `domain/events.py`의 passes_min_level과 같은 순위(fatal·critical 3 · warning 2 ·
 * normal 1 · 미지 = 2). 단 extension 1.5.8 문서의 레벨 값(FATAL·CRITICAL·WARNING·INFO) 중 INFO는 1로 본다
 * (게이트웨이 표에는 아직 info가 없다 — 미지 = 2로 처리됨). 해소 레벨(recovery·clear)은 필터와 무관하게 통과시킨다(알람이 해소되지 않는 일을 막는다).
 *
 * <p>게이트웨이도 같은 필터를 다시 적용하므로 이것은 송신량을 줄이는 1차 필터일 뿐이다. 기본(빈 값)은 전부 보낸다.
 */
final class LevelFilter {

    private LevelFilter() {
    }

    static boolean passes(String level, String minLevel) {
        if (minLevel == null || minLevel.isEmpty()) {
            return true;
        }
        String key = level == null ? "" : level.trim().toLowerCase();
        if (key.equals("recovery") || key.equals("clear")) {
            return true;
        }
        return rank(key) >= rank(minLevel);
    }

    static int rank(String level) {
        String k = level == null ? "" : level.trim().toLowerCase();
        if (k.equals("fatal") || k.equals("critical")) {
            return 3;
        }
        if (k.equals("warning")) {
            return 2;
        }
        if (k.equals("normal") || k.equals("info")) {
            return 1;
        }
        return 2;
    }
}
