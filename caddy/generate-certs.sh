#!/usr/bin/env bash
# Generate a long-lived local TLS certificate for Caddy (ADR-0014).
#
# mkcert provides no way to set the validity period (it hardcodes ~2y3m). To get
# a quasi-infinite certificate that browsers still TRUST, we sign a long-lived
# leaf with mkcert's already-installed root CA, using openssl. Browsers do not
# enforce the public ~398-day cap for certs chaining to a locally-installed root.
# (Note: some iOS/Safari versions may still cap server-cert lifetime — if the
#  tablet rejects it, lower CERT_DAYS.)
#
# One certificate covers HOST_IP (tablet) and localhost/127.0.0.1 (same-machine).
# HOST_IP is read from the repo-root .env; override inline with HOST_IP=... .
# CERT_DAYS defaults to 36500 (~100 years); override with CERT_DAYS=... .
#
# Prerequisites: mkcert + openssl installed, and the mkcert CA trusted
# (`mkcert -install`, once per machine / per browsing device).
# Output (gitignored): caddy/certs/livekit.crt and caddy/certs/livekit.key
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"
CERT_DIR="$ROOT_DIR/caddy/certs"
CERT_DAYS="${CERT_DAYS:-36500}" # ~100 years (quasi-infinite)

# Read HOST_IP from .env unless it is already set in the environment (override).
if [ -z "${HOST_IP:-}" ]; then
  if [ ! -f "$ENV_FILE" ]; then
    echo "error: $ENV_FILE not found (copy .env.example to .env first)" >&2
    exit 1
  fi
  HOST_IP="$(grep -E '^[[:space:]]*HOST_IP[[:space:]]*=' "$ENV_FILE" | tail -n1 | cut -d= -f2- | tr -d '"' | xargs)"
fi
: "${HOST_IP:?HOST_IP is empty (set it in $ENV_FILE or pass HOST_IP=... inline)}"

command -v mkcert >/dev/null 2>&1 || { echo "error: mkcert not found" >&2; exit 1; }
command -v openssl >/dev/null 2>&1 || { echo "error: openssl not found" >&2; exit 1; }

CAROOT="$(mkcert -CAROOT)"
ROOT_CA="$CAROOT/rootCA.pem"
ROOT_CA_KEY="$CAROOT/rootCA-key.pem"
if [ ! -f "$ROOT_CA" ] || [ ! -f "$ROOT_CA_KEY" ]; then
  echo "error: mkcert root CA not found in $CAROOT — run 'mkcert -install' first" >&2
  exit 1
fi

mkdir -p "$CERT_DIR"

# Add HOST_IP to the SANs as an IP entry if it looks like an IPv4 address, else DNS.
if [[ "$HOST_IP" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
  HOST_SAN="IP.2 = $HOST_IP"
else
  HOST_SAN="DNS.2 = $HOST_IP"
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cat > "$TMP/cert.cnf" <<EOF
[req]
distinguished_name = dn
prompt = no
[dn]
CN = realtime-calib
[v3]
subjectAltName = @alt
extendedKeyUsage = serverAuth
keyUsage = critical, digitalSignature, keyEncipherment
[alt]
DNS.1 = localhost
IP.1 = 127.0.0.1
$HOST_SAN
EOF

openssl genrsa -out "$CERT_DIR/livekit.key" 2048
openssl req -new -key "$CERT_DIR/livekit.key" -out "$TMP/csr.pem" -config "$TMP/cert.cnf"
openssl x509 -req -in "$TMP/csr.pem" \
  -CA "$ROOT_CA" -CAkey "$ROOT_CA_KEY" -CAcreateserial -CAserial "$TMP/ca.srl" \
  -out "$CERT_DIR/livekit.crt" -days "$CERT_DAYS" -sha256 \
  -extensions v3 -extfile "$TMP/cert.cnf"

echo "Certificate written to $CERT_DIR (valid $CERT_DAYS days) for HOST_IP=$HOST_IP."
