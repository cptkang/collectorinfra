package com.collectorinfra.jennifer.adapter;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.LinkedBlockingDeque;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;

/**
 * 비동기 TCP 줄 송신기 — 제니퍼 이벤트 스레드를 막지 않는다.
 *
 * <ul>
 *   <li>{@link #offer}는 대기열에 넣기만 한다(즉시 반환). 가득 차면 가장 오래된 건을 버린다(최신 이벤트 우선).</li>
 *   <li>데몬 스레드 1개가 연결을 유지하며 보낸다. 실패하면 그 건을 대기열 앞에 되돌리고 지수 백오프로 재연결한다.</li>
 *   <li>전달 보장은 "최대 1회에 가까운 최선 노력"이다 — 쓰기 직후 연결이 끊긴 건은 유실될 수 있다.
 *       누락분은 게이트웨이 폴러(Open API)가 메운다(멱등 키가 같아 중복 발행되지 않는다).</li>
 * </ul>
 */
final class TcpLineSender {

    private static final long STATS_LOG_INTERVAL_MS = 300_000L;

    private final AdapterOptions opts;
    private final LinkedBlockingDeque<String> queue;
    private final Thread worker;
    private volatile boolean running = true;

    private final AtomicLong enqueued = new AtomicLong();
    private final AtomicLong sent = new AtomicLong();
    private final AtomicLong dropped = new AtomicLong();
    private final AtomicLong failures = new AtomicLong();

    private Socket socket;
    private OutputStream out;

    TcpLineSender(AdapterOptions opts) {
        this.opts = opts;
        this.queue = new LinkedBlockingDeque<String>(opts.queueCapacity);
        this.worker = new Thread(new Runnable() {
            @Override
            public void run() {
                loop();
            }
        }, "collectorinfra-jennifer-sender");
        this.worker.setDaemon(true);
        this.worker.start();
    }

    AdapterOptions options() {
        return opts;
    }

    void offer(String line) {
        enqueued.incrementAndGet();
        while (!queue.offerLast(line)) {
            if (queue.pollFirst() != null) {
                long n = dropped.incrementAndGet();
                if (n == 1 || n % 1000 == 0) {
                    Log.warn("대기열 가득 참 - 오래된 이벤트 폐기 누적 " + n + "건(목적지 " + opts.endpoint() + ")");
                }
            }
        }
    }

    /** 송신기를 멈춘다. 대기열에 남은 건은 버린다(옵션 변경으로 교체될 때 호출). */
    void shutdown() {
        running = false;
        worker.interrupt();
        closeQuietly();
        int left = queue.size();
        if (left > 0) {
            Log.warn("송신기 교체 - 미송신 " + left + "건 폐기(목적지 " + opts.endpoint() + ")");
        }
    }

    private void loop() {
        long backoff = 500L;
        long consecutive = 0;
        long lastStats = System.currentTimeMillis();
        while (running) {
            String line;
            try {
                line = queue.pollFirst(1, TimeUnit.SECONDS);
            } catch (InterruptedException e) {
                break;
            }
            long now = System.currentTimeMillis();
            if (now - lastStats >= STATS_LOG_INTERVAL_MS) {
                lastStats = now;
                Log.info(stats());
            }
            if (line == null) {
                continue;
            }
            try {
                ensureConnected();
                out.write(line.getBytes(StandardCharsets.UTF_8));
                out.flush();
                sent.incrementAndGet();
                if (consecutive > 0) {
                    Log.info("송신 재개(" + opts.endpoint() + ") - 연속 실패 " + consecutive + "회 뒤 복구");
                    consecutive = 0;
                }
                backoff = 500L;
            } catch (IOException e) {
                long n = failures.incrementAndGet();
                closeQuietly();
                if (!queue.offerFirst(line)) {
                    dropped.incrementAndGet();
                }
                consecutive++;
                // 연속 실패 로그는 1·2·3회와 이후 20회마다만 남긴다(수신부 장기 중단 시 로그 폭주 방지).
                if (consecutive <= 3 || consecutive % 20 == 0) {
                    Log.warn("송신 실패(" + opts.endpoint() + ") 연속 " + consecutive + "회 · 누적 " + n + "회 - "
                            + e.getClass().getSimpleName() + ": " + e.getMessage() + " · " + backoff + "ms 후 재시도"
                            + " · 대기 " + queue.size() + "건");
                }
                try {
                    Thread.sleep(backoff);
                } catch (InterruptedException ie) {
                    break;
                }
                backoff = Math.min(backoff * 2, opts.maxBackoffMs);
            }
        }
        closeQuietly();
    }

    private void ensureConnected() throws IOException {
        if (socket != null && socket.isConnected() && !socket.isClosed()) {
            return;
        }
        Socket s = new Socket();
        s.setKeepAlive(true);
        s.setTcpNoDelay(true);
        s.setSoTimeout(opts.connectTimeoutMs);
        s.connect(new InetSocketAddress(opts.targetHost, opts.targetPort), opts.connectTimeoutMs);
        socket = s;
        out = s.getOutputStream();
        Log.info("연결 수립: " + opts.endpoint());
    }

    private void closeQuietly() {
        if (socket != null) {
            try {
                socket.close();
            } catch (IOException ignored) {
                // 닫기 실패는 무시한다
            }
        }
        socket = null;
        out = null;
    }

    String stats() {
        return "통계 - 목적지 " + opts.endpoint() + " · 접수 " + enqueued.get() + " · 송신 " + sent.get()
                + " · 폐기 " + dropped.get() + " · 실패 " + failures.get() + " · 대기 " + queue.size();
    }
}
