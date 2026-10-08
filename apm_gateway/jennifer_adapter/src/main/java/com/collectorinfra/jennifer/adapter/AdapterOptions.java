package com.collectorinfra.jennifer.adapter;

import java.lang.reflect.Method;

/**
 * 어댑터 옵션 — 제니퍼 관리 화면 어댑터 [옵션] 팝업의 「사용자 정의 속성」(Key/Value)에서 읽는다.
 *
 * <p>옵션은 화면에서 바뀔 수 있으므로 이벤트 묶음마다 다시 읽는다 — 옵션 변경은 뷰서버 재기동 없이 반영된다.
 *
 * <p>어댑터 ID: extension API(1.3.0 · 1.5.8 실측)에는 등록 ID를 어댑터에 알려 주는 콜백이 없다
 * (EventHandler는 {@code on(EventData[])} 하나뿐). 그래서 ID를 코드 상수 {@link #DEFAULT_ADAPTER_ID}로 고정한다 —
 * 관리 화면의 어댑터 ID를 반드시 이 값으로 입력한다. 바꿔야 하면 뷰서버 JVM 옵션
 * {@code -Dcollectorinfra.adapter.id=<ID>}를 쓴다(재기동 필요).
 *
 * <p>PropertyUtil은 리플렉션으로 부른다 — 컴파일용 API jar {@code extension-ide-x.y.z.jar}에는 util 패키지
 * (PropertyUtil·LogUtil)가 없다(1.5.7·1.6.0 실측). 그래서 extension·extension-ide 어느 쪽으로도 빌드되고,
 * 실행 시에는 뷰서버가 가진 실물을 쓴다. 실물이 없으면 모든 옵션이 기본값 → target_host 미설정 → 송신 안 함(로그 1회).
 */
final class AdapterOptions {

    static final String DEFAULT_ADAPTER_ID = "collectorinfra_event";

    /** {@code PropertyUtil.getValue(String, String, String)} — 없으면 null(옵션 전부 기본값). */
    private static final Method GET_VALUE = findGetValue();

    private static Method findGetValue() {
        try {
            return Class.forName("com.aries.extension.util.PropertyUtil")
                    .getMethod("getValue", String.class, String.class, String.class);
        } catch (Throwable e) {
            Log.warn("PropertyUtil 없음 - 어댑터 옵션을 읽을 수 없어 기본값으로 동작(송신 안 함): " + e);
            return null;
        }
    }

    /** 사용 여부 — false면 송신기를 멈추고 이벤트를 버린다(재기동 없이 끄는 스위치). */
    final boolean enabled;

    /** 수신 게이트웨이 호스트(필수 — 비면 송신하지 않는다). */
    final String targetHost;
    /** 수신 게이트웨이 TCP 포트. */
    final int targetPort;
    /** 다중 제니퍼 구분자 — 게이트웨이 소스 id(`default` = 단일 소스). */
    final String sourceId;
    /** 연결 타임아웃(ms). */
    final int connectTimeoutMs;
    /** 송신 대기열 상한(건) — 넘치면 가장 오래된 건부터 버린다. */
    final int queueCapacity;
    /** 이 레벨 미만은 보내지 않는다(빈 값 = 전부). 해소(recovery·clear)는 항상 보낸다. */
    final String minLevel;
    /** 재연결 대기 상한(ms). */
    final int maxBackoffMs;

    private AdapterOptions(boolean enabled, String targetHost, int targetPort, String sourceId, int connectTimeoutMs,
                           int queueCapacity, String minLevel, int maxBackoffMs) {
        this.enabled = enabled;
        this.targetHost = targetHost;
        this.targetPort = targetPort;
        this.sourceId = sourceId;
        this.connectTimeoutMs = connectTimeoutMs;
        this.queueCapacity = queueCapacity;
        this.minLevel = minLevel;
        this.maxBackoffMs = maxBackoffMs;
    }

    static String adapterId() {
        String id = System.getProperty("collectorinfra.adapter.id");
        return (id == null || id.trim().isEmpty()) ? DEFAULT_ADAPTER_ID : id.trim();
    }

    static AdapterOptions load() {
        String id = adapterId();
        return new AdapterOptions(
                !"false".equalsIgnoreCase(str(id, "enabled", "true")),
                str(id, "target_host", ""),
                intVal(id, "target_port", 9110),
                str(id, "source_id", "default"),
                intVal(id, "connect_timeout_ms", 3000),
                intVal(id, "queue_capacity", 10000),
                str(id, "min_level", "").toLowerCase(),
                intVal(id, "max_backoff_ms", 30000));
    }

    /** 송신기 재생성이 필요한 옵션(목적지·대기열 크기)이 같은지. */
    boolean sameEndpoint(AdapterOptions other) {
        return other != null
                && targetHost.equals(other.targetHost)
                && targetPort == other.targetPort
                && connectTimeoutMs == other.connectTimeoutMs
                && queueCapacity == other.queueCapacity
                && maxBackoffMs == other.maxBackoffMs;
    }

    String endpoint() {
        return targetHost + ":" + targetPort;
    }

    private static String str(String id, String key, String def) {
        if (GET_VALUE == null) {
            return def;
        }
        try {
            Object v = GET_VALUE.invoke(null, id, key, def);
            return v == null ? def : String.valueOf(v).trim();
        } catch (Throwable e) {
            return def;
        }
    }

    private static int intVal(String id, String key, int def) {
        String v = str(id, key, String.valueOf(def));
        try {
            int n = Integer.parseInt(v);
            return n > 0 ? n : def;
        } catch (NumberFormatException e) {
            return def;
        }
    }
}
