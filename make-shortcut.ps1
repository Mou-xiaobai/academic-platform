# Creates a desktop shortcut for Academic Platform.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File make-shortcut.ps1
$ws = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath("Desktop")
$lnk = $ws.CreateShortcut((Join-Path $desktop "Academic Platform.lnk"))
$lnk.TargetPath = Join-Path $PSScriptRoot "start-silent.vbs"
$lnk.WorkingDirectory = $PSScriptRoot
$icon = Join-Path $PSScriptRoot "icon.ico"
if (Test-Path $icon) { $lnk.IconLocation = $icon }
$lnk.Description = "Academic Research Platform"
$lnk.Save()
Write-Host "Shortcut created on Desktop."
