#!/usr/bin/env bash
set -euo pipefail
umask 077

issuer=${1:-}
tailnet_ip=${2:-}
if [[ ! -f infra/tailscale-keycloak.compose.yaml ||
      ! "$issuer" =~ ^https://([A-Za-z0-9.-]+)(:[0-9]+)?/realms/[A-Za-z0-9_-]+$ ]]; then
    echo 'Avvia dalla radice del repository indicando l’issuer HTTPS di Keycloak.' >&2
    exit 2
fi
host=${BASH_REMATCH[1]}
if [[ ! "$tailnet_ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
    echo 'Indica l’IPv4 Tailscale del server come secondo argomento.' >&2
    exit 2
fi
if [[ -e .env || -L .env ]]; then
    echo 'Il file .env esiste già. Nessun file è stato sostituito.' >&2
    exit 1
fi

subject=$(python3 -c '
import base64, json, sys, uuid

try:
    segment = sys.stdin.read().strip().split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
    audience = claims["aud"]
    if isinstance(audience, str):
        audience = [audience]
    if (claims["iss"] != sys.argv[1] or "order-extractor-console" not in audience
            or "mfa" not in claims["amr"] or "owner" not in claims["roles"]):
        raise ValueError
    subject = claims["sub"]
    if str(uuid.UUID(subject)) != subject:
        raise ValueError
except (AttributeError, KeyError, IndexError, ValueError, TypeError):
    sys.exit("Serve il token Keycloak con issuer, audience, MFA e proprietario già controllati.")
print(subject)
' "$issuer")

keys="$PWD/.license-keys"
if ! mkdir -m 700 -- "$keys"; then
    echo 'La cartella delle chiavi non è stata modificata.' >&2
    exit 1
fi
private="$keys/lease-signing.pem"
public="$keys/lease-public.pem"
pending="$keys/.setup-env"
initialized=false
cleanup() {
    rm -f -- "$pending"
    if [[ "$initialized" == false ]]; then
        rm -f -- "$private" "$public"
        rmdir -- "$keys" || true
    fi
}
trap cleanup EXIT

openssl genpkey -algorithm ED25519 -out "$private"
openssl pkey -in "$private" -pubout -out "$public"
password=$(openssl rand -hex 32)
pepper=$(openssl rand -base64 48 | tr '+/' '-_' | tr -d '\n')
cat > "$pending" <<ENV
POSTGRES_PASSWORD=$password
OE_DATABASE=postgresql+psycopg://order:$password@postgres/order
OE_PEPPER=$pepper
OE_HOST_KEY_FILE='$private'
OE_HOSTNAME=http://:8080
OE_OIDC_ISSUER=$issuer
OE_OIDC_AUDIENCE=order-extractor-console
OE_OIDC_JWKS=$issuer/protocol/openid-connect/certs
OE_ADMIN_SUBJECT=$subject
OE_IDENTITY_TAILNET_HOST=$host
OE_IDENTITY_TAILNET_IP=$tailnet_ip
ENV
sudo chown 10001:10001 "$private"
ln -T -- "$pending" .env
initialized=true
printf 'Configurazione iniziale creata. La chiave pubblica per il client è %s.\n' "$public"
