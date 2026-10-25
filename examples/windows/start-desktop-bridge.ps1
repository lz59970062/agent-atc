<# Compatibility entry point; launcher resources ship inside the Reins package. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Python,
    [string]$CodexHome,
    [string]$CodexBin,
    [string]$CoreWrapper,
    [string]$DesktopExe,
    [string]$BridgeFile,
    [switch]$PackageContext,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$resource = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) '..\..\src\harness_reins\harness\codex\desktop\windows_assets\start-desktop-bridge.ps1'
if (-not (Test-Path -LiteralPath $resource -PathType Leaf)) {
    throw 'The checkout launcher resource is missing. Use the installed reins desktop launch command.'
}
& $resource @PSBoundParameters
