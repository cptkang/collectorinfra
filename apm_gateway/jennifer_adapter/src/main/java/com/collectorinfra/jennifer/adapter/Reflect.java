package com.collectorinfra.jennifer.adapter;

import java.lang.reflect.Field;
import java.util.List;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 선택 필드 읽기 — 확장 라이브러리 버전에 따라 있을 수도 없을 수도 있는 공개 필드를 리플렉션으로 읽는다.
 *
 * <p>extension 1.3.0(구 튜토리얼)의 EventData는 기본 14필드뿐이고, 1.5.8(제니퍼 5.5+)에서 businessName·
 * customMessage·instanceData(hostName·ipAddress·k8s …) 등이 추가됐다. 기본 14필드만 직접 참조하고 나머지는 여기로 읽어,
 * 같은 소스가 어느 버전의 extension jar로도 컴파일·실행되게 한다. 없는 필드는 null.
 */
final class Reflect {

    private static final Field MISSING;
    private static final ConcurrentHashMap<String, Field> CACHE = new ConcurrentHashMap<String, Field>();

    static {
        try {
            MISSING = Reflect.class.getDeclaredField("MISSING");
        } catch (NoSuchFieldException e) {
            throw new ExceptionInInitializerError(e);
        }
    }

    private Reflect() {
    }

    static Object get(Object target, String name) {
        if (target == null) {
            return null;
        }
        Class<?> cls = target.getClass();
        String key = cls.getName() + '#' + name;
        Field f = CACHE.get(key);
        if (f == null) {
            try {
                f = cls.getField(name);
            } catch (NoSuchFieldException e) {
                f = MISSING;
            } catch (SecurityException e) {
                f = MISSING;
            }
            CACHE.put(key, f);
        }
        if (f == MISSING) {
            return null;
        }
        try {
            return f.get(target);
        } catch (IllegalAccessException e) {
            return null;
        }
    }

    static String str(Object target, String name) {
        Object v = get(target, name);
        return v == null ? null : String.valueOf(v);
    }

    @SuppressWarnings("unchecked")
    static List<String> strList(Object target, String name) {
        Object v = get(target, name);
        if (v instanceof List) {
            return (List<String>) v;
        }
        return null;
    }
}
