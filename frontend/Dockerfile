# ── AgentHub Frontend ───────────────────────────────────────────────
# Multi-stage build: install → build → production runtime
# ────────────────────────────────────────────────────────────────────

FROM node:20-alpine AS deps
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm install --omit=dev --registry=https://registry.npmmirror.com

FROM node:20-alpine AS builder
WORKDIR /app
ARG API_BACKEND=legacy
ARG API_BACKEND_URL=http://host.docker.internal:8000
ARG GO_GATEWAY_URL=http://gateway-service:8081
ENV API_BACKEND=${API_BACKEND}
ENV API_BACKEND_URL=${API_BACKEND_URL}
ENV GO_GATEWAY_URL=${GO_GATEWAY_URL}
COPY frontend/package.json frontend/package-lock.json ./
RUN npm install --registry=https://registry.npmmirror.com
COPY frontend/. .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:20-alpine AS runner
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
