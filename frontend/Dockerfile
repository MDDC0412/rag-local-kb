# 前端镜像：构建静态文件 → 用 nginx 提供，并把 /api 反代到后端
#
# 构建期只需要 Docker Hub（基础镜像），不再访问 npm registry：
# node:22-alpine 已内置 npm 与 npx，直接用它安装依赖并执行 vite 构建。
# 这样在没有外网 DNS 的服务器上也能构建成功。

FROM node:22-alpine AS build

WORKDIR /app

# 允许构建期覆盖 npm 镜像源（仅当目标机器能访问该源时才有意义）
ARG NPM_REGISTRY=

# 先只拷依赖清单：改代码不会让这一层失效
COPY frontend/package.json frontend/pnpm-lock.yaml* ./

# 有 lockfile 时按锁文件内容安装（不需要 pnpm 本体），否则退回普通安装
RUN if [ -f pnpm-lock.yaml ]; then \
        if [ -n "${NPM_REGISTRY}" ]; then npm config set registry "${NPM_REGISTRY}"; fi; \
        npm exec --yes --package=pnpm@11 -- pnpm install --frozen-lockfile --prefer-offline \
          || npm install --no-audit --no-fund; \
    else \
        if [ -n "${NPM_REGISTRY}" ]; then npm config set registry "${NPM_REGISTRY}"; fi; \
        npm install --no-audit --no-fund; \
    fi

COPY frontend/ ./

# 构建时不需要后端地址：nginx 会反代 /api
RUN npx --no-install vite build

# ---- 运行阶段 ----
FROM nginx:1.27-alpine

ENV TZ=Asia/Shanghai

COPY --from=build /app/dist /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
