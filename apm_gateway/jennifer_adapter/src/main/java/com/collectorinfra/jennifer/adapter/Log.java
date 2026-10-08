package com.collectorinfra.jennifer.adapter;

import java.lang.reflect.Method;
import java.text.SimpleDateFormat;
import java.util.Date;

/**
 * 로그 — 제니퍼 {@code com.aries.extension.util.LogUtil}(extension 1.5.8+ · 뷰서버 로그 {@code jennifer.log})이 있으면 그것을,
 * 없으면(구버전) {@code System.out}을 쓴다. 리플렉션으로 찾으므로 어느 버전의 extension jar로도 컴파일된다.
 * 모든 줄에 {@code [collectorinfra-adapter]} 접두를 붙인다 — {@code grep collectorinfra-adapter}로 모아 본다.
 */
final class Log {

    private static final String TAG = "[collectorinfra-adapter] ";
    private static final Method INFO;
    private static final Method WARN;

    static {
        Method info = null;
        Method warn = null;
        try {
            Class<?> c = Class.forName("com.aries.extension.util.LogUtil");
            info = c.getMethod("info", String.class);
            warn = c.getMethod("warn", String.class);
        } catch (Throwable ignored) {
            // 구버전 extension — System.out 폴백
        }
        INFO = info;
        WARN = warn;
    }

    private Log() {
    }

    static void info(String msg) {
        write(INFO, "INFO", msg);
    }

    static void warn(String msg) {
        write(WARN, "WARN", msg);
    }

    private static void write(Method m, String level, String msg) {
        if (m != null) {
            try {
                m.invoke(null, TAG + msg);
                return;
            } catch (Throwable ignored) {
                // LogUtil 호출 실패 — System.out 폴백
            }
        }
        System.out.println(new SimpleDateFormat("yyyy-MM-dd HH:mm:ss").format(new Date())
                + " " + TAG + level + " " + msg);
    }
}
