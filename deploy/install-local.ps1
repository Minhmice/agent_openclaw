[CmdletBinding()]
param(
    [string]$Python = "python",
    [switch]$InstallChromium
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venv = Join-Path $repo ".venv"

& $Python -m venv $venv
$venvPython = Join-Path $venv "Scripts\python.exe"
& $venvPython -m pip install -e "$repo[dev]"
if ($InstallChromium) {
    & $venvPython -m playwright install chromium
}
Write-Output "Local environment ready at $venv"
