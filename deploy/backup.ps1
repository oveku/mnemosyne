#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Backup Mnemosyne Neo4j data from remote server
.DESCRIPTION
    Creates local backups of Neo4j graph data via SSH.
.PARAMETER SshHost
    SSH host alias or address (default: from MNEMOSYNE_SSH_HOST env or "mnemosyne-host")
.EXAMPLE
    .\backup.ps1
    .\backup.ps1 -SshHost pi@192.168.1.100
#>
param(
    [string]$SshHost = $env:MNEMOSYNE_SSH_HOST
)

$ErrorActionPreference = "Stop"

if (-not $SshHost) { $SshHost = "mnemosyne-host" }

$BACKUP_ROOT = Join-Path $PSScriptRoot ".." "backups"
$BACKUP_DIR = Join-Path $BACKUP_ROOT (Get-Date -Format "yyyy-MM-dd_HHmmss")

function Write-Status {
    param([string]$Message, [string]$Color = "White")
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$timestamp] $Message" -ForegroundColor $Color
}

New-Item -ItemType Directory -Path $BACKUP_DIR -Force | Out-Null
Write-Status "Backup directory: $BACKUP_DIR" "Cyan"

# Backup Neo4j
Write-Status "Backing up Neo4j database..." "Cyan"
$neo4jRunning = ssh $SshHost "docker inspect mnemosyne-neo4j --format='{{.State.Running}}' 2>/dev/null || echo false" 2>&1
if ($neo4jRunning -eq "true") {
    ssh $SshHost "docker exec mnemosyne-neo4j cypher-shell -u neo4j -p mnemosyne 'MATCH (n) RETURN n' --format plain" > "$BACKUP_DIR/neo4j-nodes.txt" 2>$null
    ssh $SshHost "docker exec mnemosyne-neo4j cypher-shell -u neo4j -p mnemosyne 'MATCH ()-[r]->() RETURN r' --format plain" > "$BACKUP_DIR/neo4j-relationships.txt" 2>$null
    Write-Status "Neo4j data exported" "Green"
} else {
    Write-Status "Neo4j container not running" "Yellow"
}

Write-Status "Backup complete: $BACKUP_DIR" "Green"
