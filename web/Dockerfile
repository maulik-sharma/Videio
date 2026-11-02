# syntax=docker/dockerfile:1
# Build context is the repository root (the edge config lives in infra/nginx).

# ---- build the SPA ----
FROM node:22-alpine AS build
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY web/ ./
RUN npm run build

# ---- edge: nginx serving the SPA and routing to the API ----
FROM nginx:1.27-alpine AS edge
RUN rm -f /etc/nginx/conf.d/default.conf
COPY infra/nginx/nginx.conf /etc/nginx/nginx.conf
COPY --from=build /web/dist /usr/share/nginx/html
USER nginx
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
    CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/"]
