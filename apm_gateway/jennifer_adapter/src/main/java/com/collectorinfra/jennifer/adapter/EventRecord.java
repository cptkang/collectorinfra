package com.collectorinfra.jennifer.adapter;

import java.util.List;

/**
 * 제니퍼 EventData의 값 사본 — 제니퍼 클래스에 의존하지 않는 중립 레코드.
 *
 * <p>EventData 필드를 읽는 곳은 {@link AlarmEventAdapter#toRecord}뿐이다. 송신 스레드는 이 사본만 다루므로
 * 제니퍼가 배열을 재사용해도 안전하고, {@link SendTestEvent}는 제니퍼 jar 없이 이 레코드로 시험한다.
 */
final class EventRecord {
    // 기본 14필드 — extension 1.3.0 · 1.5.8 공통
    short domainId;
    String domainName;
    int instanceId;
    String instanceName;
    long time;
    String errorType;
    String metricsName;
    String eventLevel;
    String message;
    double value;
    String otype;
    String detailMessage;
    String serviceName;
    long txid;

    // extension 1.5.8+ (없으면 null)
    String domainDescription;
    List<String> domainGroupHierarchy;
    String instanceDescription;
    String businessName;
    String customMessage;

    // instanceData — 1.5.8+ (instanceData 자체가 null인 빌드도 있다 → hasInstance=false)
    boolean hasInstance;
    String hostName;
    String ipAddress;
    String platform;
    String agentVersion;
    String instanceDataDescription;

    // instanceData.k8s — 쿠버네티스 환경에서만
    boolean hasK8s;
    String k8sPodUid;
    String k8sContainerName;
    String k8sNodeName;
    String k8sContainerIdHint;
}
