package com.collectorinfra.jennifer.adapter;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;

/**
 * 연결 시험 — 제니퍼 jar 없이 시험 이벤트 1건을 보낸다(뷰서버에서 방화벽·수신부 확인용).
 *
 * <pre>
 * java -cp collectorinfra-jennifer-adapter.jar com.collectorinfra.jennifer.adapter.SendTestEvent &lt;host&gt; &lt;port&gt; [sourceId] [--print]
 * </pre>
 * {@code --print}는 보내지 않고 송신할 줄만 출력한다.
 */
public final class SendTestEvent {

    private SendTestEvent() {
    }

    public static void main(String[] args) throws IOException {
        if (args.length < 2) {
            System.err.println("사용법: SendTestEvent <host> <port> [sourceId] [--print]");
            System.exit(2);
        }
        String host = args[0];
        int port = Integer.parseInt(args[1]);
        String sourceId = args.length >= 3 && !args[2].startsWith("--") ? args[2] : "default";
        boolean printOnly = args[args.length - 1].equals("--print");

        EventRecord r = new EventRecord();
        r.domainId = 1000;
        r.domainName = "TEST_DOMAIN";
        r.instanceId = 10001;
        r.instanceName = "test-was-01";
        r.time = System.currentTimeMillis();
        r.errorType = "TEST_EVENT";
        r.metricsName = "";
        r.eventLevel = "WARNING";
        r.message = "collectorinfra 어댑터 연결 시험 \"따옴표\"\n줄바꿈";
        r.value = 1.0;
        r.otype = "INSTANCE";
        r.detailMessage = "";
        r.serviceName = "/test";
        r.txid = 0L;

        String line = EventJson.toLine(r, sourceId, AdapterOptions.adapterId(), System.currentTimeMillis());
        System.out.print(line);
        if (printOnly) {
            return;
        }
        Socket s = new Socket();
        try {
            s.connect(new InetSocketAddress(host, port), 3000);
            OutputStream out = s.getOutputStream();
            out.write(line.getBytes(StandardCharsets.UTF_8));
            out.flush();
            System.out.println("송신 완료: " + host + ":" + port);
        } finally {
            s.close();
        }
    }
}
