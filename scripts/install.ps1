<# Download a prebuilt Windows x64 binary from official GitHub Releases. #>
param(
  [ValidateSet('agent', 'server')][string]$Component = 'agent'
)
$ErrorActionPreference = 'Stop'
if ($Component -ne 'agent') { throw 'On Windows use the agent; deploy the server with Docker or on Linux.' }
# PROCESSOR_ARCHITECTURE is available in Windows PowerShell 5.1 and PowerShell 7.
# PROCESSOR_ARCHITEW6432 covers a 32-bit PowerShell process on 64-bit Windows.
$CpuArchitecture = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
if ($CpuArchitecture -ne 'AMD64') {
  throw "Windows x64 is required for this build (detected: $CpuArchitecture)."
}
# Public GitHub Release download does not require a GitHub login.
$UseGh = $false
if (Get-Command gh -ErrorAction SilentlyContinue) {
  & gh auth status 2>$null
  if ($LASTEXITCODE -eq 0) { $UseGh = $true }
}

$Repo = 'Doradux/remoteDoradoCommander'
$Asset = 'remote-dorado-agent-windows-x64.zip'
$Dir = Join-Path ([IO.Path]::GetTempPath()) ('remote-dorado-' + [guid]::NewGuid().ToString('N'))
New-Item -Path $Dir -ItemType Directory -Force | Out-Null
try {
  if ($UseGh) {
    & gh release download --repo $Repo --pattern $Asset --pattern SHA256SUMS --dir $Dir
    if ($LASTEXITCODE -ne 0) { throw 'Failed to download release assets from GitHub.' }
  } else {
    $BaseUrl = "https://github.com/$Repo/releases/latest/download"
    Invoke-WebRequest -Uri "$BaseUrl/$Asset" -OutFile (Join-Path $Dir $Asset)
    Invoke-WebRequest -Uri "$BaseUrl/SHA256SUMS" -OutFile (Join-Path $Dir 'SHA256SUMS')
  }
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
  # Create explicit Start Menu shortcuts. Never auto-start or embed private IPs/tokens.
  $Menu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Dorado Relay'
  New-Item -Path $Menu -ItemType Directory -Force | Out-Null
  $Shell = New-Object -ComObject WScript.Shell
  $StartShortcut = $Shell.CreateShortcut((Join-Path $Menu 'Dorado Relay.lnk'))
  $StartShortcut.TargetPath = $Dest
  $StartShortcut.WorkingDirectory = $InstallDir
  $StartShortcut.Description = 'Connect this computer to your Dorado MCP relay'
  $StartShortcut.Save()
  $SetupShortcut = $Shell.CreateShortcut((Join-Path $Menu 'Configure Dorado Relay.lnk'))
  $SetupShortcut.TargetPath = $Dest
  $SetupShortcut.Arguments = '--setup'
  $SetupShortcut.WorkingDirectory = $InstallDir
  $SetupShortcut.Description = 'Pair or rename this computer'
  $SetupShortcut.Save()
  Write-Host 'Dorado Relay is available in the Start Menu (launch it only when you want to).'
  Write-Host 'On first run, name your PC and approve browser pairing. No IP is needed in the launcher.'
}
finally {
  Remove-Item -LiteralPath $Dir -Recurse -Force -ErrorAction SilentlyContinue
}
