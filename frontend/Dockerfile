# 前端镜像：仅打包构建产物，构建期不安装任何 npm 依赖。
#
# 为什么这样设计：
#   部署目标服务器可能没有公网 DNS（npm registry 不可达），任何在构建期
#   下载 npm 包的做法都会失败。因此依赖安装与前端构建在开发机完成，
#   产物随发布包分发（scripts/package_release.ps1），本镜像只负责拷入 nginx。
#
# 前置条件：仓库根目录下存在 frontend/dist（由 pnpm build 生成）。
#   若不存在，构建会在下面 COPY 这一步失败，并提示先执行打包脚本。

FROM nginx:1.27-alpine

ENV TZ=Asia/Shanghai

# 静态资源；目录不存在时构建失败，避免打出一个空白页面
COPY frontend/dist /usr/share/nginx/html

COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
