param([Parameter(Mandatory=$true)][string]$Package)
$target = Split-Path -Parent $PSScriptRoot
$source = (Resolve-Path $Package).Path
Get-Process DocMind -ErrorAction SilentlyContinue | Stop-Process -Force
robocopy $source $target /E /XO /R:2 /W:1 /XF '*.log' | Out-Null
if ($LASTEXITCODE -gt 7) { throw "复制发布包失败：$LASTEXITCODE" }
Write-Host "DocMind 便携版升级完成：$target"
