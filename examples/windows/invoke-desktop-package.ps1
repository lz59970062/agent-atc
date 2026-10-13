<# Compatibility entry point for the package-owned Windows PowerShell facade. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PackageFamilyName,
    [Parameter(Mandatory = $true)][string]$Python,
    [Parameter(Mandatory = $true)][string]$Helper,
    [Parameter(Mandatory = $true)][string]$RequestFile
)
$ErrorActionPreference = 'Stop'
$resource = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) '..\..\src\agent_atc\desktop_windows_assets\invoke-desktop-package.ps1'
if (-not (Test-Path -LiteralPath $resource -PathType Leaf)) {
    throw 'The checkout package helper is missing. Use the installed atc desktop launch command.'
}
& $resource @PSBoundParameters
