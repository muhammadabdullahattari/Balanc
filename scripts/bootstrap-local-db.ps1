[CmdletBinding()]
param()

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $repositoryRoot

& (Join-Path $repositoryRoot 'backend/.venv/Scripts/python.exe') -m ledgerpilot.local_setup check
if ($LASTEXITCODE -ne 0) {
    throw 'Local credential validation failed; run the credential preparation command in README.md.'
}

$services = @(docker compose config --services)
if ($LASTEXITCODE -ne 0 -or $services.Count -ne 1 -or $services[0] -ne 'postgres') {
    throw 'Compose services have changed. Review the compose file before bootstrapping local database roles.'
}

$status = docker compose ps --status running --services
if ($LASTEXITCODE -ne 0 -or $status -notcontains 'postgres') {
    throw 'Start the local PostgreSQL service before bootstrapping its roles.'
}

# Read current Compose values into the service before rerunning bootstrap. This
# recreates the service only when its configuration changed; data stays in volume.
docker compose up -d --wait --wait-timeout 90 postgres
if ($LASTEXITCODE -ne 0) { throw 'Could not apply the current local database service configuration.' }

docker compose exec --no-TTY postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /docker-entrypoint-initdb.d/01-roles.sql'
if ($LASTEXITCODE -ne 0) {
    throw 'Local database role setup failed; inspect PostgreSQL output without sharing secrets.'
}

Write-Output 'Local application and migration roles are configured.'
