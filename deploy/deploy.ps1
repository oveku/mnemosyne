#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Deploy Mnemosyne to a remote server via SSH
.DESCRIPTION
    Transfers server files and builds Docker containers natively on the target host.
    Deploys Neo4j + Mnemosyne MCP server as Docker containers.
.PARAMETER SshHost
    SSH host alias or address (default: from MNEMOSYNE_SSH_HOST env or "mnemosyne-host")
.PARAMETER RemoteDir
    Remote deployment directory (default: from MNEMOSYNE_REMOTE_DIR env or "/opt/mnemosyne")
.PARAMETER SkipBuild
    Skip Docker build, only restart containers
.PARAMETER Backup
    Backup existing Neo4j data before deploying
.EXAMPLE
    .\deploy.ps1
    .\deploy.ps1 -SshHost pi@192.168.1.100 -RemoteDir /opt/mnemosyne
    .\deploy.ps1 -Backup
#>
param(
    [string]$SshHost = $env:MNEMOSYNE_SSH_HOST,
    [string]$RemoteDir = $env:MNEMOSYNE_REMOTE_DIR,
    [switch]$SkipBuild,
    [switch]$Backup
)

$ErrorActionPreference = "Stop"

if (-not $SshHost) { $SshHost = "mnemosyne-host" }
if (-not $RemoteDir) { $RemoteDir = "/opt/mnemosyne" }

$LOCAL_SERVER = Join-Path (Join-Path $PSScriptRoot "..") "server"

function Write-Status {
    param([string]$Message, [string]$Color = "White")
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$timestamp] $Message" -ForegroundColor $Color
}

# Verify SSH connectivity
Write-Status "Verifying SSH connection to $SshHost..." "Cyan"
$sshTest = ssh $SshHost "echo ok" 2>&1
if ($sshTest -ne "ok") {
    Write-Status "Cannot connect via SSH ($SshHost)" "Red"
    exit 1
}
Write-Status "SSH connection OK" "Green"

# Backup existing data if requested
if ($Backup) {
    Write-Status "Backing up Neo4j data..." "Yellow"
    $backupDir = Join-Path (Join-Path (Join-Path $PSScriptRoot "..") "backups") (Get-Date -Format "yyyy-MM-dd_HHmmss")
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null

    ssh $SshHost "docker exec mnemosyne-neo4j neo4j-admin database dump neo4j --to-stdout 2>/dev/null" > "$backupDir/neo4j-dump.dump" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Status "Neo4j backup saved to $backupDir" "Green"
    }
}

# Create remote directory structure
Write-Status "Creating remote directory structure..." "Cyan"
ssh $SshHost "mkdir -p $RemoteDir/app/storage"

# Transfer server files
Write-Status "Transferring server files..." "Cyan"
scp "$LOCAL_SERVER/Dockerfile" "${SshHost}:${RemoteDir}/Dockerfile"
scp "$LOCAL_SERVER/docker-compose.yml" "${SshHost}:${RemoteDir}/docker-compose.yml"
scp "$LOCAL_SERVER/app/requirements.txt" "${SshHost}:${RemoteDir}/app/requirements.txt"
scp "$LOCAL_SERVER/app/server.py" "${SshHost}:${RemoteDir}/app/server.py"
scp "$LOCAL_SERVER/app/storage/__init__.py" "${SshHost}:${RemoteDir}/app/storage/__init__.py"
scp "$LOCAL_SERVER/app/storage/base.py" "${SshHost}:${RemoteDir}/app/storage/base.py"
scp "$LOCAL_SERVER/app/storage/neo4j_storage.py" "${SshHost}:${RemoteDir}/app/storage/neo4j_storage.py"

# Transfer .env if it exists
$envFile = Join-Path (Join-Path $PSScriptRoot "..") "server" | Join-Path -ChildPath ".env"
if (Test-Path $envFile) {
    scp "$envFile" "${SshHost}:${RemoteDir}/.env"
    Write-Status ".env file transferred" "Green"
}

Write-Status "Files transferred" "Green"

if (-not $SkipBuild) {
    Write-Status "Building and starting Docker containers..." "Cyan"
    ssh $SshHost "cd $RemoteDir && docker compose up -d --build"
    Write-Status "Containers started" "Green"

    # Wait for health check
    Write-Status "Waiting for services to be healthy..." "Cyan"
    Start-Sleep -Seconds 10

    # Verify MCP server is responding
    $healthCheck = ssh $SshHost "curl -s -o /dev/null -w '%{http_code}' -X POST http://localhost:8010/mcp -H 'Content-Type: application/json' -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"ping\"}'" 2>&1
    if ($healthCheck -eq "200") {
        Write-Status "Mnemosyne MCP server is healthy" "Green"
    } else {
        Write-Status "Health check returned: $healthCheck" "Yellow"
        Write-Status "Check logs: ssh $SshHost 'docker logs mnemosyne-mcp'" "Yellow"
    }
} else {
    Write-Status "Skipping build, restarting containers..." "Yellow"
    ssh $SshHost "cd $RemoteDir && docker compose restart"
}

Write-Status "" "White"
Write-Status "Deployment complete" "Green"
Write-Status "  MCP Server: http://${SshHost}:8010/mcp" "Cyan"
Write-Status "  Neo4j Browser: http://${SshHost}:7474" "Cyan"
Write-Status "  Neo4j Bolt: bolt://${SshHost}:7687" "Cyan"
Write-Status "  Logs: ssh $SshHost 'docker logs -f mnemosyne-mcp'" "Cyan"
