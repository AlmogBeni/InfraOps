#!/bin/sh
# Enable HTTPS in the bundled nginx when a certificate is mounted.
#
#   /etc/infraops/tls/tls.crt   server certificate (+ intermediates), PEM
#   /etc/infraops/tls/tls.key   private key, PEM
#
# With both present, port 443 serves the application and port 80 only answers
# the health check and redirects to HTTPS. Without them, port 80 serves the
# application as before (for deployments behind a TLS reverse proxy).
set -eu

TLS_DIR=/etc/infraops/tls
CERT="$TLS_DIR/tls.crt"
KEY="$TLS_DIR/tls.key"
HTTP_MODE=/etc/nginx/infraops/http-mode.conf
HTTPS_CONF=/etc/nginx/conf.d/infraops-https.conf

if [ ! -e "$CERT" ] && [ ! -e "$KEY" ]; then
    echo "infraops: no TLS certificate mounted; serving HTTP on port 80"
    rm -f "$HTTPS_CONF"
    exit 0
fi
if [ ! -r "$CERT" ] || [ ! -r "$KEY" ]; then
    echo "infraops: both $CERT and $KEY must be present and readable" >&2
    exit 1
fi

# Public HTTPS port for the redirect (the host port mapped to 443).
case "${FRONTEND_HTTPS_PORT:-443}" in
    443) PORT_SUFFIX="" ;;
    *) PORT_SUFFIX=":${FRONTEND_HTTPS_PORT}" ;;
esac

cat > "$HTTP_MODE" <<CONF
# TLS certificate mounted: HTTP only answers the health check and redirects.
location = /frontend-health {
    access_log off;
    default_type text/plain;
    return 200 "ok\n";
}
location / {
    return 308 https://\$host${PORT_SUFFIX}\$request_uri;
}
CONF

cat > "$HTTPS_CONF" <<CONF
server {
    listen 443 ssl;
    http2 on;
    server_name _;

    ssl_certificate     $CERT;
    ssl_certificate_key $KEY;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:InfraOpsTLS:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;

    add_header Strict-Transport-Security "max-age=31536000" always;
    include /etc/nginx/infraops/app.conf;
}
CONF

echo "infraops: HTTPS enabled on port 443 (public port ${FRONTEND_HTTPS_PORT:-443}); HTTP redirects"
