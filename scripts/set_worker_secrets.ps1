param(
  [string]$EnvFile = ".env"
)

$ErrorActionPreference = "Stop"

function Read-DotEnv($Path) {
  $map = @{}
  if (-not (Test-Path $Path)) {
    throw "Env file not found: $Path"
  }
  foreach ($line in Get-Content $Path) {
    if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
    $parts = $line -split '=', 2
    $key = $parts[0].Trim()
    $value = $parts[1].Trim().Trim('"').Trim("'")
    if ($key) { $map[$key] = $value }
  }
  return $map
}

function Put-Secret($Name, $Value) {
  if ([string]::IsNullOrWhiteSpace($Value)) {
    Write-Host "skip $Name (missing)"
    return
  }
  Write-Host "set $Name"
  $Value | npm.cmd exec wrangler -- secret put $Name
}

$envMap = Read-DotEnv $EnvFile

Put-Secret "LINE_CHANNEL_ACCESS_TOKEN" $envMap["LINE_CHANNEL_ACCESS_TOKEN"]
Put-Secret "LINE_CHANNEL_SECRET" $envMap["LINE_CHANNEL_SECRET"]
Put-Secret "SUPABASE_URL" $envMap["SUPABASE_URL"]
Put-Secret "SUPABASE_SERVICE_ROLE_KEY" $envMap["SUPABASE_SERVICE_ROLE_KEY"]
Put-Secret "SUPABASE_ANON_KEY" $envMap["SUPABASE_ANON_KEY"]
Put-Secret "OCR_SPACE_API_KEY" $envMap["OCR_SPACE_API_KEY"]

$cronSecret = $envMap["CRON_SECRET"]
if ([string]::IsNullOrWhiteSpace($cronSecret)) {
  $bytes = New-Object byte[] 32
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  try {
    $rng.GetBytes($bytes)
  } finally {
    $rng.Dispose()
  }
  $cronSecret = [Convert]::ToBase64String($bytes)
}
Put-Secret "CRON_SECRET" $cronSecret

if ($envMap["GITHUB_TOKEN"]) { Put-Secret "GITHUB_TOKEN" $envMap["GITHUB_TOKEN"] }
if ($envMap["GITHUB_REPO"]) { Put-Secret "GITHUB_REPO" $envMap["GITHUB_REPO"] }
