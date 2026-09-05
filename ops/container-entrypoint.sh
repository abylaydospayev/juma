#!/bin/sh
set -eu

read_secret() {
  file="$1"
  [ -r "$file" ] || return 0
  cat "$file"
}

[ -n "${OPENAI_API_KEY:-}" ] || OPENAI_API_KEY="$(read_secret /run/secrets/openai_api_key)"
[ -n "${JUMA_API_TOKEN:-}" ] || JUMA_API_TOKEN="$(read_secret /run/secrets/juma_api_token)"
export OPENAI_API_KEY JUMA_API_TOKEN
exec "$@"
