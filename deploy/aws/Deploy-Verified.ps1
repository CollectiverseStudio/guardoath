# GuardOath production deployment. Run from a clean repository checkout on Windows.
# Default is read-only preflight. -Deploy requires exact-source GitHub gates and
# closed reliability blocker #30; it does not override the 15 live acceptance checks.
[CmdletBinding()]
param(
    [switch]$Deploy,
    [string]$Profile = "collectiverse-prod",
    [string]$Region = "us-east-1",
    [string]$FunctionName = "guardoath-site",
    [string]$StackName = "guardoath-site"
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Repo = "CollectiverseStudio/guardoath"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
Set-Location $Root

function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    $lines = @(& $Program @Arguments 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "$Program failed with exit $($LASTEXITCODE): $($lines -join ' ')"
    }
    return ($lines -join "`n").Trim()
}
function Invoke-Aws([string[]]$Arguments) {
    return Invoke-Checked "aws" ($Arguments + @("--region", $Region, "--profile", $Profile, "--output", "json"))
}
function Assert-Run([object[]]$Runs, [string]$Name, [string]$Sha) {
    $matches = @($Runs | Where-Object { $_.name -eq $Name -and $_.head_sha -eq $Sha })
    if (-not $matches.Count) { throw "No exact-SHA GitHub Actions run for $Name at $Sha. Dispatch the workflow and recheck." }
    $latest = $matches | Sort-Object created_at -Descending | Select-Object -First 1
    if ($latest.status -ne "completed" -or $latest.conclusion -ne "success") {
        throw "$Name not green for $($Sha): status=$($latest.status) conclusion=$($latest.conclusion) run=$($latest.html_url)"
    }
    Write-Host "$Name passed at $Sha ($($latest.html_url))"
}

$Sha = Invoke-Checked "git" @("rev-parse", "--verify", "HEAD")
if ($Sha -notmatch '^[a-f0-9]{40}$') { throw "Invalid full Git SHA" }
$dirty = Invoke-Checked "git" @("status", "--porcelain", "--untracked-files=all")
if ($dirty) { throw "Working tree not clean. Do not package or deploy uncommitted code." }
$origin = Invoke-Checked "git" @("ls-remote", "origin", "refs/heads/main")
$remoteSha = ($origin -split '\s+')[0]
if ($remoteSha -ne $Sha) { throw "Local HEAD $Sha is not current origin/main $remoteSha. Fetch and fast-forward first." }
Write-Host "Clean current main source: $Sha"

$Headers = @{ "User-Agent" = "GuardOath-Verified-Deployment"; "Accept" = "application/vnd.github+json" }
$RunsResponse = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/actions/runs?head_sha=$Sha&per_page=100" -Headers $Headers -Method Get
$Runs = @($RunsResponse.workflow_runs)
foreach ($required in @("Tests", "Push on main", "Field Comparisons Evidence", "Trusted Reference Acceptance", "PII Reliability Gate")) {
    Assert-Run $Runs $required $Sha
}
$OpenRisk = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/issues/30" -Headers $Headers -Method Get
if ($OpenRisk.state -ne "closed" -or $OpenRisk.state_reason -eq "not_planned") {
    throw "Issue #30 remains an unresolved 500-ms PII/redaction release blocker. Fix and verify it before production deployment."
}

# The account identity and deployed stack must be readable before any write.
$Identity = (Invoke-Aws @("sts", "get-caller-identity")) | ConvertFrom-Json
if (-not $Identity.Account) { throw "AWS caller account not identified" }
$Stack = (Invoke-Aws @("cloudformation", "describe-stacks", "--stack-name", $StackName)) | ConvertFrom-Json
if ($Stack.Stacks[0].StackStatus -notin @("CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE")) {
    throw "Stack not stable: $($Stack.Stacks[0].StackStatus)"
}
$Outputs = @{}
foreach ($output in $Stack.Stacks[0].Outputs) { $Outputs[$output.OutputKey] = $output.OutputValue }
if (-not $Outputs.DistributionId -or $Outputs.FunctionName -ne $FunctionName) {
    throw "CloudFormation outputs do not match the expected website function/distribution"
}
$Function = (Invoke-Aws @("lambda", "get-function", "--function-name", $FunctionName)) | ConvertFrom-Json
if ($Function.Configuration.LastUpdateStatus -ne "Successful" -or -not $Function.Configuration.RevisionId) {
    throw "Lambda has a pending/failed update; do not overwrite it."
}
Write-Host "AWS account $($Identity.Account) | stable stack | Lambda revision $($Function.Configuration.RevisionId)"
if (-not $Deploy) {
    Write-Host "Preflight complete. No production changes made. Re-run with -Deploy only after completing pre-deploy release acceptance."
    exit 0
}

$BuildPython = Invoke-Checked "python" @("-c", "import sys; print(sys.version_info.major, sys.version_info.minor)")
if ($BuildPython -ne "3 13") { throw "Python 3.13 is required for Lambda build, got $BuildPython" }
$BackupDir = Join-Path $Root ".guardoath-reports"
New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
$BackupFile = Join-Path $BackupDir ("lambda-before-" + $Sha.Substring(0,12) + ".zip")
if (Test-Path $BackupFile) { throw "Backup filename already exists; avoid overwriting the previous rollback copy." }
$Uploaded = $false
try {
    Invoke-WebRequest -Uri $Function.Code.Location -OutFile $BackupFile -UseBasicParsing
    if ((Get-Item $BackupFile).Length -lt 1000) { throw "Lambda rollback package is unexpectedly small" }
    Write-Host "Original Lambda package preserved at $BackupFile"
    [void](Invoke-Checked "python" @("deploy/aws/build_lambda.py"))
    $Zip = Join-Path $Root "deploy/aws/build/guardoath-lambda.zip"
    if (-not (Test-Path $Zip)) { throw "Release archive missing" }
    $CurrentHead = Invoke-Checked "git" @("rev-parse", "HEAD")
    if ($CurrentHead -ne $Sha) { throw "Git HEAD changed during build" }
    $Uploaded = $true # AWS may apply the update even if the client loses its response.
    [void](Invoke-Aws @("lambda", "update-function-code", "--function-name", $FunctionName,
        "--revision-id", $Function.Configuration.RevisionId, "--zip-file", "fileb://$Zip"))
    [void](Invoke-Aws @("lambda", "wait", "function-updated", "--function-name", $FunctionName))
    [void](Invoke-Aws @("cloudfront", "create-invalidation", "--distribution-id", $Outputs.DistributionId,
        "--paths", "/assets/*"))
    [void](Invoke-Checked "python" @("-m", "tools.verify_live_dashboard",
        "--url", "https://guardoath.com", "--expected-sha", $Sha))
    Write-Host "Source parity verified. Production is NOT accepted until all 15 live safety checks have passed."
} catch {
    Write-Error "Deployment or parity verification failed: $_"
    if ($Uploaded -and (Test-Path $BackupFile)) {
        Write-Warning "Attempting to restore the previously downloaded Lambda code (tenant table and configuration unchanged)."
        $Now = (Invoke-Aws @("lambda", "get-function-configuration", "--function-name", $FunctionName)) | ConvertFrom-Json
        [void](Invoke-Aws @("lambda", "update-function-code", "--function-name", $FunctionName,
            "--revision-id", $Now.RevisionId, "--zip-file", "fileb://$BackupFile"))
        [void](Invoke-Aws @("lambda", "wait", "function-updated", "--function-name", $FunctionName))
        [void](Invoke-Aws @("cloudfront", "create-invalidation", "--distribution-id", $Outputs.DistributionId,
            "--paths", "/assets/*"))
        Write-Warning "Restored backup Lambda archive. Verify old /healthz, dashboard and all production checks."
    }
    throw
}
