<# Download a prebuilt Windows x64 binary from private GitHub Releases. #>
param(
  [ValidateSet('agent', 'server')][string]$Component = 'agent',
  [switch]$NoSetup
)
$ErrorActionPreference = 'Stop'
if ($Component -ne 'agent') { throw 'On Windows use the agent; deploy the server with Docker or on Linux.' }
if ([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne [System.Runtime.InteropServices.Architecture]::X64) {
  throw 'Windows x64 only (Windows ARM64 has no standalone build yet).'
}
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { throw 'Install GitHub CLI: https://cli.github.com/ and run gh auth login.' }
& gh auth status 2>$null
if ($LASTEXITCODE -ne 0) { throw 'Run gh auth login first to access this private repo.' }

$Repo = 'Doradux/remoteDoradoCommander'
$Asset = 'remote-dorado-agent-windows-x64.zip'
$Dir = Join-Path ([IO.Path]::GetTempPath()) ('remote-dorado-' + [guid]::NewGuid().ToString('N'))
New-Item -Path $Dir -ItemType Directory -Force | Out-Null
try {
  & gh release download --repo $Repo --pattern $Asset --pattern SHA256SUMS --dir $Dir
  if ($LASTEXITCODE -ne 0) { throw 'Failed to download release assets from GitHub.' }
  $Lines = Get-Content -LiteralPath (Join-Path $Dir 'SHA256SUMS')
  $Expected = $null
  foreach ($Line in $Lines) {
    if ($Line -match '^([0-9a-f]{64})\s+remote-dorado-agent-windows-x64\.zip$') { $Expected = $Matches[1]; break }
  }
  if (-not $Expected) { throw 'Missing SHA256 checksum in release.' }
  $Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $Dir $Asset)).Hash
  if ($Actual -ine $Expected) { throw 'SHA256 checksum mismatch. Refusing to install.' }
  Expand-Archive -LiteralPath (Join-Path $Dir $Asset) -DestinationPath (Join-Path $Dir 'unpacked') -Force
  $Source = Join-Path (Join-Path $Dir 'unpacked') 'remote-dorado-agent.exe'
  if (-not (Test-Path -LiteralPath $Source)) { throw 'Missing executable in release zip.' }
  $InstallDir = Join-Path $env:LOCALAPPDATA 'RemoteDoradoCommander\bin'
  New-Item -Path $InstallDir -ItemType Directory -Force | Out-Null
  $Dest = Join-Path $InstallDir 'remote-dorado-agent.exe'
  Copy-Item -LiteralPath $Source -Destination $Dest -Force
  Write-Host "Installed: $Dest"
  $UserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
  $Segments = @($UserPath -split ';' | Where-Object { $_ })
  if ($Segments -notcontains $InstallDir) {
    [Environment]::SetEnvironmentVariable('Path', ($Segments + $InstallDir -join ';'), 'User')
    Write-Host 'Added to the user PATH (new terminals will pick it up).'
  }
  $env:Path += ';' + $InstallDir
  if (-not $NoSetup) { & $Dest --setup; if ($LASTEXITCODE -ne 0) { throw 'Agent setup failed' } }
  Write-Host "Start the agent: remote-dorado-agent --auto-shell"
}
finally {
  Remove-Item -LiteralPath $Dir -Recurse -Force -ErrorAction SilentlyContinue
}
