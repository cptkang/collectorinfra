package com.aries.extension.util;

/**
 * 로컬 시험 전용 대체 클래스 — 실물 PropertyUtil은 뷰서버 밖에서 옵션을 읽지 못한다.
 * 클래스패스에서 실물 extension jar보다 **앞에** 두면 시스템 프로퍼티 {@code -D<어댑터ID>.<키>=<값>}로 옵션을 흉내 낸다.
 * 산출 jar에는 넣지 않는다(build.sh는 src/main/java만 컴파일한다).
 */
public class PropertyUtil {
    public static String getValue(String adapterId, String key, String defaultValue) {
        return System.getProperty(adapterId + "." + key, defaultValue);
    }

    public static String getValue(String adapterId, String key) {
        return System.getProperty(adapterId + "." + key);
    }
}
