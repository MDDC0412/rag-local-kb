# 前端镜像：构建静态文件 → 用 nginx 提供，并把 /api 反代到后端
FROM node:22-alpine AS build

WORKDIR /app

# 固定 pnpm 版本：不写死的话 corepack 会去 registry 查 "pnpm/latest"，
# 内网或 registry 不可达时构建会直接失败（DNS EAI_AGAIN）。
# pnpm 11 依赖 Node >= 22.12，node:22-alpine 满足。
ARG PNPM_VERSION=11.18.0

# 允许构建期覆盖 npm 镜像源，例如私有 registry 或内网镜像
ARG NPM_REGISTRY=https://registry.npmjs.org

RUN npm config set registry "${NPM_REGISTRY}" \
    && npm install -g "pnpm@${PNPM_VERSION}" \
    && npm cache clean --force

# 先只拷依赖清单；有 lockfile 时按 lockfile 精确安装
COPY frontend/package.json frontend/pnpm-lock.yaml* ./
RUN if [ -f pnpm-lock.yaml ]; then \
        pnpm install --frozen-lockfile; \
    else \
        pnpm install; \
    fi

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
