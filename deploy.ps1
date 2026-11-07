# Deploys UrbanOS. Use this rather than `firebase deploy` directly.
#
# Firebase uploads .env as plain environment variables, and Cloud Run refuses a
# plain variable with the same name as a bound secret. .env is for local runs, so
# it is moved aside for the deploy and always put back.
#
# Importing main.py is also slower than the CLI's default 10 second limit.
param([string]$Only = "functions,hosting")

$ErrorActionPreference = "Stop"
$envFile = Join-Path $PSScriptRoot ".env"
$held = Join-Path $PSScriptRoot ".env.deploy-hold"
$env:FUNCTIONS_DISCOVERY_TIMEOUT = "120"

Push-Location $PSScriptRoot
if (Test-Path $envFile) { Move-Item $envFile $held }
try {
    cmd /c "firebase deploy --only $Only --project urbanos101 --force 2>&1"
    $code = $LASTEXITCODE
} finally {
    if (Test-Path $held) { Move-Item $held $envFile }
    Pop-Location
}
exit $code
