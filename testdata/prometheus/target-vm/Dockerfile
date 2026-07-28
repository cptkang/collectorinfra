# VM 유사 대상 인프라 (§8.1 승격) — ubuntu 베이스에 node_exporter를 "설치"한 컨테이너.
# 빌드 시 공식 이미지에서 바이너리를 반입하므로 기동 시 인터넷 불요(결정적).
# compose에서 hostname=svr-web-01 을 부여해 node_uname_info{nodename="svr-web-01"}
# 실측 경로(sre-agent/06 §5-1 수집 측 표준화)가 픽스처에서 검증 가능해진다.
FROM prom/node-exporter:v1.8.1 AS node_exporter_dist

FROM ubuntu:24.04
COPY --from=node_exporter_dist /bin/node_exporter /usr/local/bin/node_exporter
EXPOSE 9100
CMD ["/usr/local/bin/node_exporter"]
