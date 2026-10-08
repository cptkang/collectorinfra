package com.collectorinfra.jennifer.adapter;

/**
 * 송신 형식 — 단일 행 JSON(줄 끝 '\n') 1건 = 이벤트 1건.
 *
 * <pre>
 * {"kind":"jennifer.event","v":1,"sourceId":"default","adapterId":"collectorinfra_event",
 *  "sentAt":1760000000000,
 *  "event":{"domainId":1000,"domainName":"..","instanceId":10011,"instanceName":"..","time":1760000000000,
 *           "errorType":"..","metricsName":"..","eventLevel":"FATAL","message":"..","value":0.0,
 *           "otype":"..","detailMessage":"..","serviceName":"..","txid":"123",
 *           "domainDescription":"..","domainGroupHierarchy":[".."],"instanceDescription":"..",
 *           "businessName":"..","customMessage":".."},
 *  "instance":{"hostName":"..","ipAddress":"..","platform":"..","version":"..","description":"..","k8s":null}}
 * </pre>
 *
 * <p>extension 1.5.8 미만이라 없는 필드는 null, instanceData가 없으면 {@code "instance":null}.
 * {@code instance.hostName}은 게이트웨이 hostname 정합 ①순위(APM `hostName` 직접 대조) 재료다.
 *
 * <p>{@code event} 안의 키 이름은 제니퍼 Open API 이벤트 응답과 같게 둔다 — 수신측(게이트웨이)이 폴러와 같은
 * 파서(`adapters/jennifer/fields.py`의 parse_event)로 받아 hostname 정합·마스킹·멱등 키를 그대로 적용한다.
 * 어댑터는 정규화·심각도 매핑을 하지 않는다(D-195 G-4b — 수신은 게이트웨이).
 *
 * <p>{@code txid}는 문자열로 보내고 0(트랜잭션 없음)은 빈 문자열로 둔다 — 폴러 경로의 멱등 키(txid 칸)와 맞추기 위해서다.
 * {@code value}가 NaN·무한이면 null로 둔다(JSON 규격 밖).
 */
final class EventJson {

    private EventJson() {
    }

    static String toLine(EventRecord e, String sourceId, String adapterId, long sentAt) {
        StringBuilder sb = new StringBuilder(512);
        sb.append('{');
        str(sb, "kind", "jennifer.event").append(',');
        sb.append("\"v\":1,");
        str(sb, "sourceId", sourceId).append(',');
        str(sb, "adapterId", adapterId).append(',');
        sb.append("\"sentAt\":").append(sentAt).append(',');
        sb.append("\"event\":{");
        sb.append("\"domainId\":").append(e.domainId).append(',');
        str(sb, "domainName", e.domainName).append(',');
        sb.append("\"instanceId\":").append(e.instanceId).append(',');
        str(sb, "instanceName", e.instanceName).append(',');
        sb.append("\"time\":").append(e.time).append(',');
        str(sb, "errorType", e.errorType).append(',');
        str(sb, "metricsName", e.metricsName).append(',');
        str(sb, "eventLevel", e.eventLevel).append(',');
        str(sb, "message", e.message).append(',');
        sb.append("\"value\":");
        if (Double.isNaN(e.value) || Double.isInfinite(e.value)) {
            sb.append("null");
        } else {
            sb.append(e.value);
        }
        sb.append(',');
        str(sb, "otype", e.otype).append(',');
        str(sb, "detailMessage", e.detailMessage).append(',');
        str(sb, "serviceName", e.serviceName).append(',');
        str(sb, "txid", e.txid == 0L ? "" : String.valueOf(e.txid)).append(',');
        str(sb, "domainDescription", e.domainDescription).append(',');
        strList(sb, "domainGroupHierarchy", e.domainGroupHierarchy).append(',');
        str(sb, "instanceDescription", e.instanceDescription).append(',');
        str(sb, "businessName", e.businessName).append(',');
        str(sb, "customMessage", e.customMessage);
        sb.append("},");
        sb.append("\"instance\":");
        if (!e.hasInstance) {
            sb.append("null");
        } else {
            sb.append('{');
            str(sb, "hostName", e.hostName).append(',');
            str(sb, "ipAddress", e.ipAddress).append(',');
            str(sb, "platform", e.platform).append(',');
            str(sb, "version", e.agentVersion).append(',');
            str(sb, "description", e.instanceDataDescription).append(',');
            sb.append("\"k8s\":");
            if (!e.hasK8s) {
                sb.append("null");
            } else {
                sb.append('{');
                str(sb, "podUid", e.k8sPodUid).append(',');
                str(sb, "containerName", e.k8sContainerName).append(',');
                str(sb, "nodeName", e.k8sNodeName).append(',');
                str(sb, "containerIdHint", e.k8sContainerIdHint);
                sb.append('}');
            }
            sb.append('}');
        }
        sb.append("}\n");
        return sb.toString();
    }

    private static StringBuilder strList(StringBuilder sb, String key, java.util.List<String> values) {
        sb.append('"').append(key).append("\":");
        if (values == null) {
            return sb.append("null");
        }
        sb.append('[');
        boolean first = true;
        for (String v : values) {
            if (!first) {
                sb.append(',');
            }
            first = false;
            if (v == null) {
                sb.append("null");
            } else {
                sb.append('"');
                escape(sb, v);
                sb.append('"');
            }
        }
        return sb.append(']');
    }

    private static StringBuilder str(StringBuilder sb, String key, String value) {
        sb.append('"').append(key).append("\":");
        if (value == null) {
            return sb.append("null");
        }
        sb.append('"');
        escape(sb, value);
        return sb.append('"');
    }

    /** JSON 문자열 이스케이프 — 제어 문자·따옴표·역슬래시. 줄바꿈이 남으면 줄 단위 수신이 깨지므로 반드시 거친다. */
    static void escape(StringBuilder sb, String s) {
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"':
                    sb.append("\\\"");
                    break;
                case '\\':
                    sb.append("\\\\");
                    break;
                case '\n':
                    sb.append("\\n");
                    break;
                case '\r':
                    sb.append("\\r");
                    break;
                case '\t':
                    sb.append("\\t");
                    break;
                case '\b':
                    sb.append("\\b");
                    break;
                case '\f':
                    sb.append("\\f");
                    break;
                default:
                    if (c < 0x20 || c == '\u2028' || c == '\u2029') {
                        sb.append(String.format("\\u%04x", (int) c));
                    } else {
                        sb.append(c);
                    }
            }
        }
    }
}
