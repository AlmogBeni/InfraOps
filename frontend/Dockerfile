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

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
  CMD curl --fail --silent --show-error --max-time 2 http://127.0.0.1/frontend-health >/dev/null || exit 1

CMD ["nginx", "-g", "daemon off;"]
