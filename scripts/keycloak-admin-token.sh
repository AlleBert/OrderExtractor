#!/usr/bin/env bash
set -euo pipefail

issuer=${1:-}
if [[ ! "$issuer" =~ ^https://[a-zA-Z0-9.-]+(:[0-9]+)?/realms/[a-zA-Z0-9_-]+$ ]]; then
    echo 'Indica l’issuer HTTPS del realm Keycloak.' >&2
    exit 2
fi

protocol="$issuer/protocol/openid-connect"
client_id=order-extractor-console
device=$(curl --fail --silent --show-error --max-time 15 \
    --data-urlencode "client_id=$client_id" --data-urlencode 'scope=openid' \
    "$protocol/auth/device")
device_code=$(jq -er '.device_code | strings | select(length > 0)' <<< "$device")
expires=$(jq -er '.expires_in | numbers | select(. >= 1 and . <= 1800 and . == floor)' <<< "$device")
interval=$(jq -er '(.interval // 5) | numbers | select(. >= 1 and . <= 60 and . == floor)' <<< "$device")
url=$(jq -er '.verification_uri | strings | select(startswith("https://"))' <<< "$device")
if [[ "$url" != "${issuer%%/realms/*}/"* ]]; then
    echo 'L’indirizzo di verifica non corrisponde a Keycloak.' >&2
    exit 1
fi
code=$(jq -er '.user_code | strings | select(length > 0)' <<< "$device")
printf 'Apri %s dal PC connesso a Tailscale e inserisci il codice %s.\n' "$url" "$code" >&2

deadline=$((SECONDS + expires))
while (( SECONDS + interval < deadline )); do
    sleep "$interval"
    reply=$(printf '%s' "$device_code" | curl --silent --show-error --max-time 15 \
        --data-urlencode "client_id=$client_id" \
        --data-urlencode 'grant_type=urn:ietf:params:oauth:grant-type:device_code' \
        --data-urlencode 'device_code@-' "$protocol/token")
    if token=$(jq -er '.access_token | strings | select(length > 0)' <<< "$reply"); then
        printf '%s\n' "$token"
        exit 0
    fi
    case "$(jq -r '.error // "risposta_non_valida"' <<< "$reply")" in
        authorization_pending) ;;
        slow_down) interval=$((interval + 5)) ;;
        *) echo 'Accesso Keycloak rifiutato o scaduto.' >&2; exit 1 ;;
    esac
done
echo 'Il codice di accesso è scaduto. Ripeti il comando.' >&2
exit 1
