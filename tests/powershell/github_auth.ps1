# Execute the actual production GitHub reader. Only its HTTP transport is
# synthetic; no deployment top-level code, AWS call or network request runs.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    (Resolve-Path 'deploy/aws/Deploy-Verified.ps1').Path, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Deployment script cannot be parsed' }
$method = $ast.Find({ param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Invoke-GitHubRead'
}, $true)
if (-not $method) { throw 'Missing actual GitHub evidence transport' }
Invoke-Expression $method.Extent.Text
$script:calls = [Collections.Generic.List[object]]::new()
$script:transportFailure = $false
function Invoke-RestMethod {
    [CmdletBinding()]
    param([string]$Uri, [hashtable]$Headers, [string]$Method,
          [int]$MaximumRedirection, [int]$TimeoutSec)
    if (-not $PSBoundParameters.ContainsKey('MaximumRedirection') -or $MaximumRedirection -ne 0 -or
        $TimeoutSec -ne 15 -or $Method -cne 'Get' -or $PSBoundParameters['Verbose'] -ne $false -or
        $PSBoundParameters['Debug'] -ne $false -or $PSBoundParameters['ErrorAction'] -cne 'Stop') {
        throw 'Authenticated transport must suppress diagnostics, disable redirects and use a bounded GET'
    }
    if ($Uri -cnotmatch '\Ahttps://api\.github\.com/repos/CollectiverseStudio/guardoath/' -or
        $Headers['X-GitHub-Api-Version'] -cne '2022-11-28') {
        throw 'Incorrect fixed GitHub transport destination or API contract'
    }
    [void]$script:calls.Add(@{Uri=$Uri; Headers=$Headers.Clone()})
    if ($script:transportFailure) {
        throw ('Synthetic HTTP redirect/error body with secret ' + $Headers['Authorization'])
    }
    return [pscustomobject]@{synthetic_verified_read=$true}
}
function Expect-Refused([scriptblock]$Action, [string]$Phrase) {
    try { [void](& $Action) }
    catch {
        if (-not $_.Exception.Message.Contains($Phrase) -or
            $_.Exception.Message.Contains('synthetic-private')) {
            throw 'Expected sanitized release transport refusal'
        }
        return
    }
    throw 'Unsafe GitHub request accepted'
}
$oldGh = [Environment]::GetEnvironmentVariable('GH_TOKEN')
$oldGitHub = [Environment]::GetEnvironmentVariable('GITHUB_TOKEN')
try {
    [Environment]::SetEnvironmentVariable('GH_TOKEN', 'synthetic-private-primary')
    [Environment]::SetEnvironmentVariable('GITHUB_TOKEN', 'synthetic-private-fallback')
    $prefix = '/repos/CollectiverseStudio/guardoath/'
    $paths = @(($prefix + 'issues/30'), ($prefix + 'actions/runs/42/jobs?per_page=100'),
        ($prefix + 'actions/runs?head_sha=' + ('a' * 40) + '&per_page=100'))
    foreach ($path in $paths) {
        $result = Invoke-GitHubRead $path
        if (-not $result.synthetic_verified_read) { throw 'Read did not return actual transport evidence' }
    }
    foreach ($call in $script:calls) {
        if ($call.Headers['Authorization'] -cne 'Bearer synthetic-private-primary' -or
            $call.Uri.Contains('synthetic-private')) { throw 'Token preference or header-only authentication failed' }
    }
    [Environment]::SetEnvironmentVariable('GH_TOKEN', '')
    [void](Invoke-GitHubRead ($prefix + 'issues/30'))
    if ($script:calls[-1].Headers['Authorization'] -cne 'Bearer synthetic-private-fallback') {
        throw 'GITHUB_TOKEN fallback was not used'
    }
    [Environment]::SetEnvironmentVariable('GITHUB_TOKEN', '')
    [void](Invoke-GitHubRead ($prefix + 'issues/30'))
    if ($script:calls[-1].Headers.ContainsKey('Authorization')) { throw 'Anonymous reads gained a credential' }

    $before = $script:calls.Count
    foreach ($bad in @('synthetic-private bad', "synthetic-private`n", ('x' * 4097),
        ('synthetic-private' + [char]1), ('synthetic-private' + [char]233))) {
        [Environment]::SetEnvironmentVariable('GH_TOKEN', $bad)
        Expect-Refused { Invoke-GitHubRead ($prefix + 'issues/30') } 'Invalid operator GitHub authentication'
    }
    if ($script:calls.Count -ne $before) { throw 'Invalid credentials reached HTTP' }
    [Environment]::SetEnvironmentVariable('GH_TOKEN', 'synthetic-private-primary')
    foreach ($bad in @(('https://api.github.com/' + $prefix + 'issues/30'),
        '//example.invalid/steal', '/repos/Other/guardoath/issues/30', ($prefix + 'issues/30?token=secret'),
        ($prefix + 'actions/runs/0/jobs?per_page=100'), ($prefix + "issues/30`n"), ($prefix + '../issues/30'))) {
        Expect-Refused { Invoke-GitHubRead $bad } 'Untrusted GitHub release evidence path'
    }
    if ($script:calls.Count -ne $before) { throw 'Untrusted destination reached HTTP' }
    $script:transportFailure = $true
    Expect-Refused { Invoke-GitHubRead ($prefix + 'issues/30') } 'Actual GitHub release evidence unavailable'
    if ($script:calls.Count -ne $before + 1) { throw 'HTTP failure or redirect caused a retry' }
    Write-Host 'Authenticated fixed-host reads: token precedence, fallback, anonymous access, invalid inputs and sanitized no-retry failure passed'
} finally {
    [Environment]::SetEnvironmentVariable('GH_TOKEN', $oldGh)
    [Environment]::SetEnvironmentVariable('GITHUB_TOKEN', $oldGitHub)
}
