<# Install the package and configure ATC with the same Windows Python. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$RepoUrl,
    [string]$Subdirectory,
    [string]$Python = 'python',
    [string[]]$SetupArguments = @()
)
$ErrorActionPreference = 'Stop'
$spec = if ($RepoUrl.StartsWith('git+')) { $RepoUrl } else { 'git+' + $RepoUrl }
if ($Subdirectory) { $spec += '#subdirectory=' + $Subdirectory }

& $Python -m pip install $spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
# Passing the module to this same interpreter avoids depending on Scripts/PATH.
& $Python -m agent_atc setup --force --enable --now @SetupArguments
exit $LASTEXITCODE
