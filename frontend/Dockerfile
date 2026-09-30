# ── Build stage ─────────────────────────────────────────────────────────────
FROM node:22-bookworm-slim AS build

WORKDIR /app

ARG VITE_API_BASE=/api/v1
ARG VITE_ENVIRONMENT_LABEL=DEV
ENV VITE_API_BASE=$VITE_API_BASE
ENV VITE_ENVIRONMENT_LABEL=$VITE_ENVIRONMENT_LABEL
ENV CI=true

COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY . .
RUN npm run typecheck
RUN npm run bundle

# ── Runtime stage ───────────────────────────────────────────────────────────
FROM nginx:1.27-alpine AS runtime

RUN apk add --no-cache curl

# The CIDR of the host TLS reverse proxy as seen from inside this container
# (the Docker bridge gateway by default). Override at runtime to narrow it.
ENV TRUSTED_PROXY_CIDR=172.16.0.0/12
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY nginx/app.conf nginx/http-mode.conf /etc/nginx/infraops/
# Runs before nginx starts: enables HTTPS when /etc/infraops/tls/tls.{crt,key} exist.
COPY --chmod=0755 nginx/40-infraops-tls.sh /docker-entrypoint.d/40-infraops-tls.sh
RUN rm -f /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80 443

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
  CMD curl --fail --silent --show-error --max-time 2 http://127.0.0.1/frontend-health >/dev/null || exit 1

CMD ["nginx", "-g", "daemon off;"]
