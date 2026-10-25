$ws = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath("Desktop")
$lnk = $ws.CreateShortcut((Join-Path $desktop "Academic Platform.lnk"))
$lnk.TargetPath = Join-Path $PSScriptRoot "start-silent.vbs"
$lnk.WorkingDirectory = $PSScriptRoot
$lnk.IconLocation = Join-Path $PSScriptRoot "icon.ico"
$lnk.Save()
Write-Host "Shortcut created on Desktop."
