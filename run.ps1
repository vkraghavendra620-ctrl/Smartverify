# PowerShell launcher for SmartVerify unified single project runner
param (
    [switch]$Dev,
    [switch]$Build,
    [switch]$Check,
    [switch]$Open,
    [string]$HostAddress = "0.0.0.0",
    [int]$Port = 8000
)

$PSScriptRoot = Split-Path -Parent -Path $MyInvocation.MyCommand.Definition
Set-Location $PSScriptRoot

$VenvPython = Join-Path $PSScriptRoot "backend\.venv\Scripts\python.exe"
$PythonExe = if (Test-Path $VenvPython) { $VenvPython } else { "python" }

$argsList = @("run.py")
if ($Dev) { $argsList += "--dev" }
if ($Build) { $argsList += "--build" }
if ($Check) { $argsList += "--check" }
if ($Open) { $argsList += "--open" }
if ($HostAddress -ne "127.0.0.1") { $argsList += "--host", $HostAddress }
if ($Port -ne 8000) { $argsList += "--port", $Port }

& $PythonExe $argsList
