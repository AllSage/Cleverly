param(
    [string]$AppContainer = $env:CLEVERLY_CONTAINER_NAME,
    [string]$WorkerContainer = $env:CLEVERLY_CODE_WORKER_CONTAINER_NAME
)

if (-not $AppContainer) { $AppContainer = "cleverly" }
if (-not $WorkerContainer) { $WorkerContainer = "cleverly-code-worker" }

$ErrorActionPreference = "Stop"

function Invoke-ContainerPython {
    param([string]$Container, [string]$Code)
    docker exec $Container python -c $Code
    if ($LASTEXITCODE -ne 0) {
        throw "docker exec failed in $Container"
    }
}

Write-Host "Checking Cleverly offline leak controls..."

Invoke-ContainerPython $AppContainer @"
from src import code_workspace as cw
blocked = [
    'curl https://example.com',
    'wget https://example.com',
    'git pull',
    'python -m pip install requests',
    'npm install',
    'docker ps',
]
for command in blocked:
    assert cw.DENIED_COMMAND_RE.search(command), command
print('denylist ok')
"@

Invoke-ContainerPython $WorkerContainer @"
import socket
try:
    socket.create_connection(('example.com', 443), timeout=3)
except OSError:
    print('worker network blocked')
else:
    raise SystemExit('worker unexpectedly reached the internet')
"@

Write-Host "Offline leak smoke checks passed."
