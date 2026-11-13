# 前端镜像：Vite 构建产物由 Nginx 托管
FROM node:20-alpine AS build

WORKDIR /srv/app
COPY frontend/package.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM nginx:alpine
COPY --from=build /srv/app/dist /usr/share/nginx/html
EXPOSE 80
