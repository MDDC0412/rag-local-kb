# 一键启动前后端（开发模式）
# 用法：powershell -ExecutionPolicy Bypass -File scripts/dev.ps1

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$py = 'D:\MiniConda\minconda\envs\rag311\python.exe'

if (-not (Test-Path $py)) {
    Write-Host "[错误] 找不到 Python 环境：$py" -ForegroundColor Red
    Write-Host "请先执行：conda create -n rag311 python=3.11 -y" -ForegroundColor Yellow
    Write-Host "再执行：  pip install -r backend/requirements.txt" -ForegroundColor Yellow
    exit 1
}

Write-Host "启动后端 http://127.0.0.1:8000 ..." -ForegroundColor Cyan
$backend = Start-Process -FilePath $py `
    -ArgumentList '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000', '--reload' `
    -WorkingDirectory (Join-Path $root 'backend') -PassThru -WindowStyle Minimized

Start-Sleep -Seconds 3

Write-Host "启动前端 http://127.0.0.1:5173 ..." -ForegroundColor Cyan
$frontend = Start-Process -FilePath 'pnpm' `
    -ArgumentList 'dev' `
    -WorkingDirectory (Join-Path $root 'frontend') -PassThru -WindowStyle Minimized

Write-Host ""
Write-Host "已启动。按任意键停止两个服务。" -ForegroundColor Green
Write-Host "  前端：http://127.0.0.1:5173" -ForegroundColor Gray
Write-Host "  文档：http://127.0.0.1:8000/docs" -ForegroundColor Gray
$null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')

foreach ($p in @($backend, $frontend)) {
    if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
}
Write-Host "已停止。" -ForegroundColor Yellow