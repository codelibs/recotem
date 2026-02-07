# Multi-stage build: frontend SPA + nginx reverse proxy
# Serves static frontend assets and proxies API/WebSocket to backend

FROM node:22-slim AS frontend-build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

FROM nginx:1.27.3-alpine

# Support non-root execution: nginx needs writable dirs for pid/cache
RUN chown -R nginx:nginx /var/cache/nginx /var/log/nginx \
    && touch /var/run/nginx.pid \
    && chown nginx:nginx /var/run/nginx.pid

COPY --from=frontend-build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Validate nginx configuration at build time
RUN nginx -t

USER nginx

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -qO /dev/null http://127.0.0.1:8000/api/ping/ || exit 1

EXPOSE 8000
