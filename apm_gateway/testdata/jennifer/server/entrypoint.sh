#!/bin/sh
# 설치본을 볼륨에 1회 풀고 데이터 서버(백그라운드) → 뷰 서버(포그라운드) 순으로 띄운다.
set -eu

VER="${JENNIFER_VERSION:?JENNIFER_VERSION 미설정}"
HOME_DIR=/opt/jennifer
ZIP="/dist/jennifer-server-${VER}.zip"

if [ ! -f "$HOME_DIR/.installed-$VER" ]; then
  [ -f "$ZIP" ] || { echo "설치본 없음: $ZIP" >&2; exit 1; }
  tmp=$(mktemp -d)
  cd "$tmp" && jar xf "$ZIP"
  cd "$HOME_DIR"
  jar xf "$tmp/jennifer-data-server-${VER}.zip"
  jar xf "$tmp/jennifer-view-server-${VER}.zip"
  rm -rf "$tmp"
  touch "$HOME_DIR/.installed-$VER"
fi

# Bootstrap Check는 기본 유지 — 5.7.0.1은 Docker(가상 환경)를 WARN만 남기고 기동한다(2026-09-29 실측).
# Docker 자원이 기준(CPU 2 · 메모리 8GB)에 못 미쳐 기동이 막힐 때만 DISABLE_BOOTSTRAP_CHECK=1로 끈다(로컬 한정).
CONF="$HOME_DIR/server.data/conf/server_data.conf"
sed -i '/^jennifer_bootstrap_check=/d' "$CONF"
if [ "${DISABLE_BOOTSTRAP_CHECK:-0}" = "1" ]; then
  printf '\njennifer_bootstrap_check=false\n' >> "$CONF"
fi

# 컨테이너가 비정상 종료되면 잠금 파일이 볼륨에 남아 재기동이 거부된다 — 이 볼륨은 이 컨테이너만 쓴다
rm -f "$HOME_DIR/server.data/db_data/db.lock" "$HOME_DIR/server.view/db_view/db.lock"

if [ -f /dist/license ]; then
  cp /dist/license "$HOME_DIR/server.data/conf/license"
fi

sh "$HOME_DIR/server.data/bin/jennifer_data.sh" start
exec sh "$HOME_DIR/server.view/bin/jennifer_view.sh" run
