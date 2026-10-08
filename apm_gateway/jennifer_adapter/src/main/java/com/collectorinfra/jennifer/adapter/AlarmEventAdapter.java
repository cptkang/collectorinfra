package com.collectorinfra.jennifer.adapter;

import com.aries.extension.data.EventData;
import com.aries.extension.handler.EventHandler;

/**
 * 제니퍼 이벤트 알림 어댑터 — 이벤트를 collectorinfra APM 게이트웨이로 push한다.
 *
 * <p>제니퍼 「관리 > 이벤트 규칙」에서 외부 연동이 켜진 규칙의 이벤트가 {@link #on}으로 들어온다.
 * 이 클래스는 EventData를 값 사본({@link EventRecord})으로 옮겨 대기열에 넣기만 하고 즉시 반환한다.
 * 송신은 {@link TcpLineSender}의 데몬 스레드가 한다 — 수신측이 멈춰도 제니퍼 이벤트 처리는 막히지 않는다.
 *
 * <p>등록: 유형 = 이벤트, 어댑터 ID = {@code collectorinfra_event}(코드 고정 — {@link AdapterOptions}), 클래스 =
 * {@code com.collectorinfra.jennifer.adapter.AlarmEventAdapter}, 경로 = jar 절대 경로.
 * 뷰서버 5.6.5.10+는 {@code server_view.conf}에 {@code extension_allowed_packages = com.collectorinfra.jennifer.*}가 있어야 로드된다.
 */
public class AlarmEventAdapter implements EventHandler {

    private static final Object LOCK = new Object();
    /** 뷰서버가 어댑터 인스턴스를 몇 번 만들든 송신 스레드는 하나만 둔다. */
    private static TcpLineSender sender;
    private static boolean warnedInactive;

    /** 산출물 버전 — 재기동 직후 로드 확인 로그에 찍힌다. */
    static final String VERSION = "0.2.0";

    private static boolean announced;

    /**
     * 뷰서버가 어댑터를 생성할 때 1회 로드 확인 로그를 남긴다 — 재기동 뒤 이 줄로 로드 성공을 판정한다
     * (송신은 첫 이벤트에서 시작되므로 이 줄이 없으면 로드 실패다).
     */
    public AlarmEventAdapter() {
        synchronized (LOCK) {
            if (announced) {
                return;
            }
            announced = true;
        }
        try {
            AdapterOptions o = AdapterOptions.load();
            Log.info("어댑터 로드됨 v" + VERSION + " · 어댑터 ID " + AdapterOptions.adapterId()
                    + " · enabled=" + o.enabled + " · target=" + (o.targetHost.isEmpty() ? "(미설정)" : o.endpoint())
                    + " · source_id=" + o.sourceId);
        } catch (Throwable t) {
            Log.warn("어댑터 로드됨 v" + VERSION + " - 옵션 읽기 실패: " + t);
        }
    }

    @Override
    public void on(EventData[] events) {
        if (events == null || events.length == 0) {
            return;
        }
        try {
            AdapterOptions opts = AdapterOptions.load();
            TcpLineSender s = senderFor(opts);
            if (s == null) {
                return;
            }
            String adapterId = AdapterOptions.adapterId();
            long now = System.currentTimeMillis();
            for (EventData data : events) {
                if (data == null) {
                    continue;
                }
                try {
                    EventRecord rec = toRecord(data);
                    if (!LevelFilter.passes(rec.eventLevel, opts.minLevel)) {
                        continue;
                    }
                    s.offer(EventJson.toLine(rec, opts.sourceId, adapterId, now));
                } catch (RuntimeException e) {
                    Log.warn("이벤트 변환 실패 - 건너뜀: " + e);
                }
            }
        } catch (Throwable t) {
            // 어댑터 예외가 제니퍼 이벤트 처리로 번지지 않게 모두 삼킨다(사유는 로그로 남긴다).
            Log.warn("이벤트 묶음 처리 실패: " + t);
        }
    }

    /**
     * EventData → 값 사본. 제니퍼 클래스 필드를 읽는 유일한 곳이다.
     * 기본 14필드는 직접, 1.5.8 추가 필드는 {@link Reflect}로 읽는다(구버전 extension에서도 컴파일·실행).
     */
    static EventRecord toRecord(EventData d) {
        EventRecord r = new EventRecord();
        r.domainId = d.domainId;
        r.domainName = d.domainName;
        r.instanceId = d.instanceId;
        r.instanceName = d.instanceName;
        r.time = d.time;
        r.errorType = d.errorType;
        r.metricsName = d.metricsName;
        r.eventLevel = d.eventLevel;
        r.message = d.message;
        r.value = d.value;
        r.otype = d.otype;
        r.detailMessage = d.detailMessage;
        r.serviceName = d.serviceName;
        r.txid = d.txid;

        r.domainDescription = Reflect.str(d, "domainDescription");
        r.domainGroupHierarchy = Reflect.strList(d, "domainGroupHierarchy");
        r.instanceDescription = Reflect.str(d, "instanceDescription");
        r.businessName = Reflect.str(d, "businessName");
        r.customMessage = Reflect.str(d, "customMessage");

        Object inst = Reflect.get(d, "instanceData");
        if (inst != null) {
            r.hasInstance = true;
            r.hostName = Reflect.str(inst, "hostName");
            r.ipAddress = Reflect.str(inst, "ipAddress");
            r.platform = Reflect.str(inst, "platform");
            r.agentVersion = Reflect.str(inst, "version");
            r.instanceDataDescription = Reflect.str(inst, "description");
            Object k8s = Reflect.get(inst, "k8s");
            if (k8s != null) {
                r.hasK8s = true;
                r.k8sPodUid = Reflect.str(k8s, "podUid");
                r.k8sContainerName = Reflect.str(k8s, "containerName");
                r.k8sNodeName = Reflect.str(k8s, "nodeName");
                r.k8sContainerIdHint = Reflect.str(k8s, "containerIdHint");
            }
        }
        return r;
    }

    private static TcpLineSender senderFor(AdapterOptions opts) {
        synchronized (LOCK) {
            if (!opts.enabled || opts.targetHost.isEmpty()) {
                if (!warnedInactive) {
                    warnedInactive = true;
                    Log.warn(!opts.enabled
                            ? "옵션 enabled=false - 이벤트를 보내지 않는다"
                            : "옵션 target_host 미설정 - 이벤트를 보내지 않는다(어댑터 ID " + AdapterOptions.adapterId()
                              + " · 사용자 정의 속성이 이 ID로 저장됐는지 확인)");
                }
                if (sender != null) {
                    sender.shutdown();
                    sender = null;
                }
                return null;
            }
            warnedInactive = false;
            if (sender != null && sender.options().sameEndpoint(opts)) {
                return sender;
            }
            if (sender != null) {
                sender.shutdown();
            }
            sender = new TcpLineSender(opts);
            Log.info("송신기 시작: " + opts.endpoint() + " · source_id=" + opts.sourceId
                    + " · queue=" + opts.queueCapacity + " · min_level=" + (opts.minLevel.isEmpty() ? "(전부)" : opts.minLevel));
            return sender;
        }
    }
}
