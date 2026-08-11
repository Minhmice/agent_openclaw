[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $SshArgument
)

$ErrorActionPreference = 'Stop'

$hostName = $env:OPENCLAW_SSH_HOST
$userName = $env:OPENCLAW_SSH_USER
$port = if ([string]::IsNullOrWhiteSpace($env:OPENCLAW_SSH_PORT)) { '22' } else { $env:OPENCLAW_SSH_PORT }

if ([string]::IsNullOrWhiteSpace($hostName)) {
    Write-Error 'OPENCLAW_SSH_HOST is not set.'
    exit 2
}

if ([string]::IsNullOrWhiteSpace($userName)) {
    Write-Error 'OPENCLAW_SSH_USER is not set.'
    exit 2
}

if ($port -notmatch '^[0-9]+$') {
    Write-Error 'OPENCLAW_SSH_PORT must be a numeric TCP port.'
    exit 2
}

$sshArgs = @('-p', $port)

if (-not [string]::IsNullOrWhiteSpace($env:OPENCLAW_SSH_KEY)) {
    $keyPath = [Environment]::ExpandEnvironmentVariables($env:OPENCLAW_SSH_KEY)
    if (-not (Test-Path -LiteralPath $keyPath -PathType Leaf)) {
        Write-Error "OPENCLAW_SSH_KEY does not point to a file: $keyPath"
        exit 2
    }
    $sshArgs += @('-i', $keyPath)
}

$sshArgs += "$userName@$hostName"
if ($null -ne $SshArgument) {
    $sshArgs += $SshArgument
}

# Passwords are intentionally not read or passed as process arguments.
# Native OpenSSH will prompt interactively when no key/agent is available.
& ssh @sshArgs
exit $LASTEXITCODE
