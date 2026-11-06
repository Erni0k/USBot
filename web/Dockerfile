# Obraz strony WWW: build statyczny (Vite) serwowany przez Caddy z TLS i proxy /api.
# Budowany z katalogu głównego repozytorium (potrzebuje infra/Caddyfile):
#
#   docker build -f web/Dockerfile -t usbot-web .

FROM node:24-alpine AS build
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
# Wartości VITE_* są wkompilowywane w JavaScript strony (to nie sekrety). Puste
# VITE_API_BASE_URL = ten sam origin: /api obsługuje Caddy.
ARG VITE_API_BASE_URL=
ARG VITE_FEEDBACK_ENABLED=false
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL} VITE_FEEDBACK_ENABLED=${VITE_FEEDBACK_ENABLED}
RUN npm run build

FROM caddy:2.11.9-alpine
COPY infra/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /web/dist /srv
RUN caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
