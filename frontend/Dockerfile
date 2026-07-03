# syntax=docker/dockerfile:1.4
# ─────────────────────────────────────────────────────────────────────────────
# Frontend — three-stage image
#
# Stage 1 (editor-builder): builds the runbook graph editor from /runbook-editor.
#   Output lands in /editor/dist and is served at /editor/ by the runtime nginx.
#
# Stage 2 (builder): installs dependencies and runs `vite build` for the main
#   React frontend from /frontend.
#   BuildKit cache mount on /root/.npm keeps the npm download cache across
#   rebuilds so that unchanged packages are not re-fetched from the registry,
#   even when package-lock.json changes.
#
# Stage 3 (runtime): nginx:alpine — serves /app/dist (main app) and
#   /app/editor-dist (graph editor) as static files.
#   Worker processes run as the built-in 'nginx' user automatically.
#
# NOTE: build context must be the project root (.) so both ./frontend and
#   ./runbook-editor are accessible.  docker-compose.yml sets context: .
#   and dockerfile: frontend/Dockerfile accordingly.
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: build runbook graph editor ───────────────────────────────────────
FROM node:20-alpine AS editor-builder

WORKDIR /editor

# Manifests first — layer cached independently of source changes
COPY runbook-editor/package*.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --prefer-offline

# Build the editor (vite.config sets base: '/editor/')
COPY runbook-editor/ .
RUN npm run build

# ── Stage 2: build main frontend ──────────────────────────────────────────────
FROM node:20-alpine AS builder

WORKDIR /app

COPY frontend/package*.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --prefer-offline

COPY frontend/ .
RUN npm run build

# ── Stage 3: runtime ──────────────────────────────────────────────────────────
FROM nginx:alpine

# Upgrade all base packages so the image ships with latest security patches
RUN apk update && apk upgrade --no-cache

WORKDIR /app

# Copy nginx configuration
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

# Copy main app build
COPY --from=builder /app/dist ./dist

# Copy editor build — served at /editor/ by nginx
COPY --from=editor-builder /editor/dist ./editor-dist

# Ensure nginx workers own the served files
RUN chown -R nginx:nginx /app/dist /app/editor-dist /etc/nginx/conf.d

EXPOSE 3000

CMD ["nginx", "-g", "daemon off;"]
