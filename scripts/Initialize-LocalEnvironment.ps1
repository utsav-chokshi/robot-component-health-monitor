$ErrorActionPreference='Stop'
$repoRoot=Split-Path -Parent $PSScriptRoot
$environmentPath=Join-Path $repoRoot '.env'
if(Test-Path -LiteralPath $environmentPath){Write-Output 'Existing local environment preserved.';return}
$randomBytes=New-Object byte[] 24
$generator=[System.Security.Cryptography.RandomNumberGenerator]::Create()
try{$generator.GetBytes($randomBytes)}finally{$generator.Dispose()}
$password=[BitConverter]::ToString($randomBytes).Replace('-','').ToLowerInvariant()
$lines=@('POSTGRES_USER=rchm_local',('POSTGRES_PASSWORD='+$password),'POSTGRES_DB=component_health','API_PORT=8020')
[System.IO.File]::WriteAllLines($environmentPath,$lines,[System.Text.UTF8Encoding]::new($false))
Write-Output 'Created local environment with a random password; contents were not displayed.'
