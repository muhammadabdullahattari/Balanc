[CmdletBinding()]
param([switch]$ConfirmReset)

if (-not $ConfirmReset) {
    throw 'This removes the disposable local PostgreSQL volume. Re-run with -ConfirmReset only when you intend to erase local development data.'
}

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $repositoryRoot

$services = @(docker compose config --services)
if ($LASTEXITCODE -ne 0 -or $services.Count -ne 1 -or $services[0] -ne 'postgres') {
    throw 'Compose services have changed. Review the compose file and reset the local database manually.'
}

docker compose down --volumes
if ($LASTEXITCODE -ne 0) { throw 'Could not stop and remove the local PostgreSQL volume.' }
docker compose up -d postgres
if ($LASTEXITCODE -ne 0) { throw 'Could not restart local PostgreSQL.' }

docker compose ps



