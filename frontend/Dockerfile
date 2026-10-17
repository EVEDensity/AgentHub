# ── AgentHub Frontend ───────────────────────────────────────────────
# Multi-stage build: install → build → production runtime
# ────────────────────────────────────────────────────────────────────

FROM node:24-alpine AS deps
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
COPY frontend/scripts ./scripts
COPY frontend/licenses ./licenses
RUN npm ci --omit=dev

FROM node:24-alpine AS builder
WORKDIR /app
ARG API_BACKEND=legacy
ARG API_BACKEND_URL=http://host.docker.internal:8000
ARG GO_GATEWAY_URL=http://gateway-service:8081
ARG MISSION_CONTROL_URL=
ENV API_BACKEND=${API_BACKEND}
ENV API_BACKEND_URL=${API_BACKEND_URL}
ENV GO_GATEWAY_URL=${GO_GATEWAY_URL}
ENV MISSION_CONTROL_URL=${MISSION_CONTROL_URL}
COPY frontend/package.json frontend/package-lock.json ./
COPY frontend/scripts ./scripts
COPY frontend/licenses ./licenses
RUN npm ci
COPY frontend/. .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:24-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/package.json ./
COPY --from=builder /app/next.config.js ./
EXPOSE 3000
CMD ["npx", "next", "start", "-p", "3000"]
