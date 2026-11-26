# 前端镜像：构建静态文件 → 用 nginx 提供，并把 /api 反代到后端
FROM node:22-alpine AS build

WORKDIR /app

# pnpm 用 corepack 启用，避免额外装一层
RUN corepack enable

# 先只拷依赖清单
COPY frontend/package.json frontend/pnpm-lock.yaml* ./
RUN pnpm install --frozen-lockfile || pnpm install

COPY frontend/ ./

# 构建时不需要后端地址：nginx 会反代 /api
RUN pnpm build

# ---- 运行阶段 ----
FROM nginx:1.27-alpine

ENV TZ=Asia/Shanghai

COPY --from=build /app/dist /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]