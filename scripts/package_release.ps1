<#
.SYNOPSIS
    生成离线部署所需的发布包。

.DESCRIPTION
    开发机完成前端构建后，把后端源码与前端构建产物分别打包上传，
    供无法访问公网 npm 源的服务器部署。服务器端只需 docker compose up -d --build。

    产出（release 目录）：
      rag-backend-src.tar.gz    后端源码、前端的 Dockerfile 与 nginx 配置、compose、脚本
      rag-frontend-dist.tar.gz  前端 dist、前端 Dockerfile、nginx 配置
      SHA256SUMS.txt            校验和

    解压顺序：先解后端包，再解前端包。前端包覆盖 frontend 目录下的 Dockerfile、nginx.conf 与 dist。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts/package_release.ps1
#>

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$release = Join-Path $root 'release'
$dist = Join-Path $root 'frontend\dist'
$backendStage = Join-Path $release 'stage-backend'
$frontendStage = Join-Path $release 'stage-frontend'

function Assert-Exists([string]$path, [string]$hint) {
    if (-not (Test-Path $path)) { throw "缺少 $path。$hint" }
}

Write-Host '[1/5] 构建前端 ...' -ForegroundColor Cyan
Push-Location (Join-Path $root 'frontend')
try {
    pnpm build
    if ($LASTEXITCODE -ne 0) { throw 'pnpm build 失败' }
} finally {
    Pop-Location
}
Assert-Exists (Join-Path $dist 'index.html') '构建产物不完整，请检查前端构建日志。'

Write-Host '[2/5] 准备暂存目录 ...' -ForegroundColor Cyan
if (Test-Path $release) { Remove-Item $release -Recurse -Force }
New-Item -ItemType Directory -Path $backendStage, $frontendStage | Out-Null

# 后端包：保持仓库相对结构，服务器上可直接 docker compose build
Copy-Item (Join-Path $root 'backend') (Join-Path $backendStage 'backend') -Recurse -Force
New-Item -ItemType Directory -Path (Join-Path $backendStage 'frontend') | Out-Null
Copy-Item (Join-Path $root 'frontend\Dockerfile') (Join-Path $backendStage 'frontend\Dockerfile') -Force
Copy-Item (Join-Path $root 'frontend\nginx.conf') (Join-Path $backendStage 'frontend\nginx.conf') -Force
foreach ($name in @('docker-compose.yml', '.env.example', '.dockerignore', 'README.md', 'LICENSE')) {
    Copy-Item (Join-Path $root $name) $backendStage -Force
}
Copy-Item (Join-Path $root 'scripts') (Join-Path $backendStage 'scripts') -Recurse -Force
Copy-Item (Join-Path $root 'samples') (Join-Path $backendStage 'samples') -Recurse -Force

# 前端包：dist、Dockerfile、nginx 配置，与仓库相对路径一致
$frontendOut = Join-Path $frontendStage 'frontend'
New-Item -ItemType Directory -Path $frontendOut | Out-Null
Copy-Item $dist (Join-Path $frontendOut 'dist') -Recurse -Force
Copy-Item (Join-Path $root 'frontend\Dockerfile') $frontendOut -Force
Copy-Item (Join-Path $root 'frontend\nginx.conf') $frontendOut -Force

Write-Host '[3/5] 生成压缩包 ...' -ForegroundColor Cyan
$backendTar = Join-Path $release 'rag-backend-src.tar.gz'
$frontendTar = Join-Path $release 'rag-frontend-dist.tar.gz'

tar -czf $backendTar -C $backendStage .
if ($LASTEXITCODE -ne 0) { throw '后端打包失败' }
tar -czf $frontendTar -C $frontendStage .
if ($LASTEXITCODE -ne 0) { throw '前端打包失败' }

Write-Host '[4/5] 清理暂存目录 ...' -ForegroundColor Cyan
Remove-Item $backendStage, $frontendStage -Recurse -Force

Write-Host '[5/5] checksum and report ...' -ForegroundColor Cyan
$hashLines = @()
$hashLines += ((Get-FileHash $backendTar -Algorithm SHA256).Hash.ToLower() + '  rag-backend-src.tar.gz')
$hashLines += ((Get-FileHash $frontendTar -Algorithm SHA256).Hash.ToLower() + '  rag-frontend-dist.tar.gz')
Set-Content -Path (Join-Path $release 'SHA256SUMS.txt') -Value $hashLines -Encoding utf8

Write-Host ''
Write-Host ('发布包已生成：' + $release) -ForegroundColor Green
Get-ChildItem $release | Select-Object Name, Length | Format-Table -AutoSize
Get-Content (Join-Path $release 'SHA256SUMS.txt')
