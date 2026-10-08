#!/bin/sh
# 제니퍼 뷰서버(또는 같은 JDK가 있는 장비)에서 빌드한다 — Maven·인터넷 불필요. JDK(javac·jar)와 extension jar만 있으면 된다.
#
#   EXT_JAR=/path/to/extension-x.y.z.jar ./build.sh
#
# EXT_JAR를 비우면 JENNIFER_HOME 아래에서 extension*.jar를 찾는다(예: JENNIFER_HOME=/opt/jennifer).
# 산출물: dist/collectorinfra-jennifer-adapter-<버전>.jar (extension jar는 넣지 않는다 — 뷰서버가 이미 가지고 있다)
set -eu
cd "$(dirname "$0")"

VERSION=$(sed -n 's/.*String VERSION = "\([^"]*\)".*/\1/p' src/main/java/com/collectorinfra/jennifer/adapter/AlarmEventAdapter.java)
OUT="dist/collectorinfra-jennifer-adapter-${VERSION}.jar"

if [ -z "${EXT_JAR:-}" ]; then
  if [ -z "${JENNIFER_HOME:-}" ]; then
    echo "EXT_JAR 또는 JENNIFER_HOME을 지정하라. 찾기: find / -name 'extension*.jar' 2>/dev/null" >&2
    exit 2
  fi
  n=$(find "$JENNIFER_HOME" -name 'extension*.jar' 2>/dev/null | wc -l)
  if [ "$n" -ne 1 ]; then
    echo "$JENNIFER_HOME 아래 extension*.jar가 ${n}개 — EXT_JAR로 뷰서버(server.view) 쪽 파일을 직접 지정하라:" >&2
    find "$JENNIFER_HOME" -name 'extension*.jar' 2>/dev/null >&2
    exit 2
  fi
  EXT_JAR=$(find "$JENNIFER_HOME" -name 'extension*.jar' 2>/dev/null)
fi
[ -f "$EXT_JAR" ] || { echo "EXT_JAR 파일 없음: $EXT_JAR" >&2; exit 2; }
echo "extension jar : $EXT_JAR"
echo "javac         : $(javac -version 2>&1)"

# Java 8 바이트코드 — 뷰서버 JVM이 8이든 17·21이든 로드된다(extension 1.3.0·1.5.8 jar 자체도 Java 8 바이트코드).
# JDK 9+는 --release 8, JDK 8은 -source/-target.
if javac --release 8 -version >/dev/null 2>&1; then
  TARGET="--release 8"
else
  TARGET="-source 1.8 -target 1.8"
fi

rm -rf build dist
mkdir -p build/classes dist
# shellcheck disable=SC2086
javac $TARGET -encoding UTF-8 -nowarn -cp "$EXT_JAR" -d build/classes \
  $(find src/main/java -name '*.java')
jar cf "$OUT" -C build/classes .

# 검증 출력 — 작업 기록에 남긴다
echo "--- 산출물 검증"
echo "클래스 수     : $(jar tf "$OUT" | grep -c '\.class$') (기대 10)"
jar tf "$OUT" | grep -q 'com/collectorinfra/jennifer/adapter/AlarmEventAdapter.class' \
  && echo "등록 클래스   : com.collectorinfra.jennifer.adapter.AlarmEventAdapter (포함)" \
  || { echo "등록 클래스 누락" >&2; exit 1; }
if jar tf "$OUT" | grep -q '^com/aries/'; then echo "com/aries 클래스가 섞였다 — 중단" >&2; exit 1; fi
echo "바이트코드    : $(javap -v -cp "$OUT" com.collectorinfra.jennifer.adapter.AlarmEventAdapter | grep 'major version' | tr -s ' ') (52 = Java 8)"
if command -v sha256sum >/dev/null 2>&1; then echo "sha256        : $(sha256sum "$OUT" | cut -d' ' -f1)"; fi
echo "완료          : $(pwd)/$OUT"
