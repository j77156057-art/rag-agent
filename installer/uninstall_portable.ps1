param([string]$InstallDir = (Split-Path -Parent $PSScriptRoot))
Get-Process DocMind -ErrorAction SilentlyContinue | Stop-Process -Force
if (-not (Test-Path -LiteralPath $InstallDir)) { Write-Host '安装目录不存在'; exit 0 }
$confirm = Read-Host "确认删除 $InstallDir 及其全部文件？输入 YES"
if ($confirm -ne 'YES') { Write-Host '已取消'; exit 1 }
Remove-Item -LiteralPath $InstallDir -Recurse -Force
Write-Host 'DocMind 已卸载'
