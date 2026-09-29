#!/bin/sh
# 에이전트를 뷰 서버에서 1회 받아 필수 설정만 덮어쓰고 Tomcat에 붙여 띄운다.
set -eu

AGENT_ROOT=/opt/agent
AGENT="$AGENT_ROOT/agent.java"

if [ ! -f "$AGENT/jennifer.jar" ]; then
  i=0
  until curl -fsS -o /tmp/agent.zip "$AGENT_DOWNLOAD_URL/download/agent/java/latest"; do
    i=$((i + 1))
    [ "$i" -ge 60 ] && { echo "에이전트 다운로드 실패: $AGENT_DOWNLOAD_URL" >&2; exit 1; }
    sleep 5
  done
  cd "$AGENT_ROOT" && jar xf /tmp/agent.zip
fi

CONF="$AGENT/conf/jennifer.conf"
sed -i -E '/^(server_address|server_port|domain_id|inst_name)[[:space:]]*=/d' "$CONF"
printf '\n' >> "$CONF"
cat >> "$CONF" <<EOF
server_address = ${AGENT_SERVER_ADDRESS}
server_port = 5000
domain_id = ${AGENT_DOMAIN_ID}
inst_name = ${AGENT_INST_NAME}
EOF

export CATALINA_OPTS="${CATALINA_OPTS:-} -javaagent:$AGENT/jennifer.jar -Djennifer.config=$CONF"
exec catalina.sh run
