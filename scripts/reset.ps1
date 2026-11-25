# 清空运行时数据（重建数据库与向量库）
# 用法：powershell -ExecutionPolicy Bypass -File scripts/reset.ps1
# 注意：会删除所有文档、分块、会话与供应商配置。

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$data = Join-Path $root 'data'

if (-not (Test-Path $data)) {
    Write-Host "data 目录不存在，无需清理。" -ForegroundColor Yellow
    exit 0
}

Write-Host "将删除以下内容：" -ForegroundColor Yellow
Get-ChildItem $data -Force | ForEach-Object { Write-Host "  $($_.Name)" }

$answer = Read-Host "确认删除？(y/N)"
if ($answer -ne 'y') {
    Write-Host "已取消。" -ForegroundColor Gray
    exit 0
}

Get-ChildItem $data -Force | Remove-Item -Recurse -Force
Write-Host "已清空。下次启动会自动重建数据库并写入种子数据。" -ForegroundColor Green