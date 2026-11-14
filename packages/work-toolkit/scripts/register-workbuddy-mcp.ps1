# register-workbuddy-mcp.ps1
# Thin wrapper. The real logic is in tools/register_mcp.py (stdlib only), which
# picks an interpreter that actually has mcp + python-docx + openpyxl.
# Keeping one implementation avoids the old bug: this script used to test only
# docx/openpyxl and could register a Python without the MCP SDK.
param(
    [switch]$Check,
    [switch]$DryRun,
    [string]$Python
)
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$entry = Join-Path $root 'tools\register_mcp.py'
if (-not (Test-Path -LiteralPath $entry)) { throw "register_mcp.py not found: $entry" }

$exe = 'python'
if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { $exe = 'py' }

$argv = @($entry)
if ($Check)  { $argv += '--check' }
if ($DryRun) { $argv += '--dry-run' }
if ($Python) { $argv += @('--python', $Python) }

& $exe @argv
if ($LASTEXITCODE -ne 0) { throw "register_mcp.py failed with exit code $LASTEXITCODE" }
