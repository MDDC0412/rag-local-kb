# 前端镜像：多阶段构建。
#
# 阶段一（build）在 Node 镜像内安装依赖并构建静态文件，阶段二（nginx）
# 只保留构建产物，运行镜像不含 Node。
#
# 部署侧有两种情况：
#
#   1. 部署机可访问 npm 源
#        docker compose up -d --build
#      依赖安装在构建镜像时完成，宿主机不需要 Node。
#
#   2. 部署机不可访问 npm 源（内网或离线服务器）
#      在任意可上网的机器上：
#        docker compose build frontend
#        docker save rag-frontend:latest | gzip > rag-frontend.tar.gz
#      上传后到部署机：
#        docker load < rag-frontend.tar.gz
#        docker compose up -d --no-build
#      部署机不参与构建，也不需要 Node 与 npm 源。
#
# 若仓库内已存在 frontend/dist（由 pnpm build 生成），构建阶段会跳过
# 依赖安装与编译，直接打包该产物。用于完全离线、但已拿到 dist 的场景。

FROM node:22-alpine AS build

WORKDIR /app

# 允许构建期覆盖 npm 源与代理
ARG NPM_REGISTRY=https://registry.npmjs.org
ARG HTTP_PROXY=
ARG HTTPS_PROXY=
ARG NO_PROXY=

ENV HTTP_PROXY=${HTTP_PROXY} \
    HTTPS_PROXY=${HTTPS_PROXY} \
    NO_PROXY=${NO_PROXY}

# 依赖安装与编译合并为一步：预置了 dist 时整段跳过。
# 依赖清单与源码的哈希参与缓存键，源码变化会自动重新安装并重建。
COPY frontend/ ./

RUN if [ -f dist/index.html ]; then \
        echo '[build] 检测到预置的 frontend/dist，跳过依赖安装与构建'; \
    else \
        npm config set registry "${NPM_REGISTRY}" \
        && npm install --no-audit --no-fund \
        && npx vite build; \
    fi

# ---- 运行阶段 ----
FROM nginx:1.27-alpine

ENV TZ=Asia/Shanghai

COPY --from=build /app/dist /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
