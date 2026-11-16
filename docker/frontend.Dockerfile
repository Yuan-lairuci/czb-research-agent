# 前端镜像：Vite 构建产物由 Nginx 托管，并反代 /api 到 backend 服务
# VITE_API_BASE 留空 = 同源请求（推荐，走 Nginx 反代）
# 构建参数（国内网络加速，评委默认官方源即可）：REGISTRY / NPM_REGISTRY
ARG REGISTRY=
FROM ${REGISTRY}node:20-alpine AS build

WORKDIR /srv/app
COPY frontend/package.json frontend/package-lock.json* ./
ARG NPM_REGISTRY=https://registry.npmjs.org
RUN npm install --registry=${NPM_REGISTRY}
COPY frontend/ ./

ARG VITE_API_BASE=""
ENV VITE_API_BASE=${VITE_API_BASE}
RUN npm run build

FROM ${REGISTRY}nginx:alpine
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /srv/app/dist /usr/share/nginx/html
EXPOSE 80
