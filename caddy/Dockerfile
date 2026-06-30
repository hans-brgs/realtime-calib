# syntax=docker/dockerfile:1.7

# -----------------------------------------------------------------------------
# Stage 1 — install webapp dependencies (yarn 4 via corepack).
# Build context is the repo root (see docker-compose.yml).
# -----------------------------------------------------------------------------
FROM node:24-alpine AS deps
WORKDIR /app
RUN corepack enable
COPY ./calibration-webapp/package.json ./calibration-webapp/yarn.lock ./calibration-webapp/.yarnrc.yml ./
RUN yarn install --immutable

# -----------------------------------------------------------------------------
# Stage 2 — build the React webapp.
# -----------------------------------------------------------------------------
FROM node:24-alpine AS builder
WORKDIR /app
RUN corepack enable
COPY --from=deps /app/node_modules ./node_modules
COPY ./calibration-webapp/. .

# Host IP injected by docker-compose (build args) from the root .env HOST_IP.
# Vite inlines it into the VITE_* URLs at build time.
ARG VITE_HOST_IP
ENV VITE_HOST_IP=${VITE_HOST_IP}
ENV VITE_API_URL=https://${VITE_HOST_IP}/api
ENV VITE_LIVEKIT_URL=wss://${VITE_HOST_IP}/livekit
ENV VITE_TOKEN_URL=https://${VITE_HOST_IP}/token
RUN yarn build

# -----------------------------------------------------------------------------
# Stage 3 — Caddy serving the built webapp (Caddyfile + certs mounted at runtime).
# -----------------------------------------------------------------------------
FROM caddy:2.10.2-alpine
COPY --from=builder /app/dist /srv/webapp
