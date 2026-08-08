#!/bin/zsh

set -u
set -o pipefail
unsetopt BG_NICE

readonly AUTH_TIMEOUT=10
readonly RESPONSE_TIMEOUT=10
readonly -a REQUEST_TYPES=(6 1 16 0 20 27 10 8)
readonly script_name=${0:t}

usage() {
  print -u2 -- \
    "Использование: $script_name [--insecure] ws://адрес-контроллера/ws"
}

if ! command -v websocat >/dev/null 2>&1; then
  print -u2 -- "Не найден websocat. Установите его командой: brew install websocat"
  exit 1
fi

typeset -a websocat_args
websocat_args=(-t)

if [[ ${1:-} == "--insecure" ]]; then
  websocat_args+=(-k)
  shift
fi

if (( $# != 1 )); then
  usage
  exit 1
fi

readonly zont_url=$1
if [[ $zont_url != ws://* && $zont_url != wss://* ]]; then
  print -u2 -- "Адрес должен начинаться с ws:// или wss://"
  exit 1
fi

read "zont_username?Имя пользователя ZONT: "
read -s "zont_password?Пароль ZONT: "
print

json_escape() {
  local value=$1
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  value=${value//$'\n'/\\n}
  value=${value//$'\r'/\\r}
  value=${value//$'\t'/\\t}
  print -r -- "$value"
}

readonly escaped_username=$(json_escape "$zont_username")
readonly escaped_password=$(json_escape "$zont_password")
unset zont_username zont_password

zmodload zsh/datetime 2>/dev/null || true

timestamp() {
  if [[ -n ${EPOCHREALTIME:-} ]]; then
    printf '%.6f' "$EPOCHREALTIME"
  else
    date '+%H:%M:%S'
  fi
}

log_frame() {
  local direction=$1
  local payload=$2
  printf '%s %-5s %s\n' "$(timestamp)" "$direction" "$payload"
}

coproc websocat "${websocat_args[@]}" "$zont_url"
readonly websocat_pid=$!

cleanup() {
  trap - EXIT INT TERM
  if kill -0 "$websocat_pid" 2>/dev/null; then
    kill "$websocat_pid" 2>/dev/null || true
    wait "$websocat_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

readonly auth_payload="{\"user\":\"$escaped_username\",\"pass\":\"$escaped_password\"}"
log_frame "send:" '{"user":"<username>","pass":"<password>"}'
if ! print -r -p -- "$auth_payload"; then
  print -u2 -- "Не удалось отправить запрос авторизации"
  exit 1
fi

authenticated=0
while (( ! authenticated )); do
  if ! IFS= read -r -p -t "$AUTH_TIMEOUT" message; then
    print -u2 -- "Контроллер не ответил на авторизацию за ${AUTH_TIMEOUT} секунд"
    exit 1
  fi

  log_frame "recv:" "$message"
  compact_message=${message//[[:space:]]/}
  if [[ $compact_message == *'"auth":200'* ]]; then
    authenticated=1
  elif [[ $compact_message == *'"auth":401'* ]]; then
    print -u2 -- "Контроллер отклонил учётные данные"
    exit 1
  fi
done

print -- "Отправка ${#REQUEST_TYPES[@]} запросов без ожидания промежуточных ответов"
for type in "${REQUEST_TYPES[@]}"; do
  payload="{\"req_ids\":$type}"
  log_frame "send:" "$payload"
  if ! print -r -p -- "$payload"; then
    print -u2 -- "Не удалось отправить req_ids для type=$type"
    exit 1
  fi
done

response_index=0
while (( response_index < ${#REQUEST_TYPES[@]} )); do
  if ! IFS= read -r -p -t "$RESPONSE_TIMEOUT" message; then
    print -u2 -- \
      "Получено $response_index из ${#REQUEST_TYPES[@]} ответов ids за отведённое время"
    exit 1
  fi

  log_frame "recv:" "$message"
  compact_message=${message//[[:space:]]/}
  if [[ $compact_message != *'"ids":'* ]]; then
    print -- "  Независимое сообщение, не учитывается как ответ req_ids"
    continue
  fi

  (( response_index += 1 ))
  expected_type=${REQUEST_TYPES[$response_index]}
  print -- "  Ответ $response_index соответствует type=$expected_type, если контроллер соблюдает порядок"
done

print -- "Получены все ${#REQUEST_TYPES[@]} ответов ids; тестовое соединение закрывается"
