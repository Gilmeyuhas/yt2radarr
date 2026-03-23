#!/bin/sh
set -eu

APP_USER="appuser"

mkdir -p /config

# If running as root → support PUID/PGID
if [ "$(id -u)" = "0" ]; then
  if [ -n "${PGID:-}" ]; then
    groupmod -o -g "$PGID" "$APP_USER" 2>/dev/null || true
  fi

  if [ -n "${PUID:-}" ]; then
    usermod -o -u "$PUID" "$APP_USER" 2>/dev/null || true
  fi

  chown -R "$APP_USER":"$APP_USER" /config /app 2>/dev/null || true

  exec gosu "$APP_USER" "$@"
fi

# If user used docker -u → respect it
exec "$@"
