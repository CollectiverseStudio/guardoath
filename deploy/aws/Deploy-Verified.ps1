# GuardOath production deployment. Run from a clean checkout with PowerShell 7.
# Set POWERSHELL_TELEMETRY_OPTOUT=1 before starting PowerShell.
# Default is read-only preflight. -Deploy requires exact-source GitHub gates.
# Issue #30 stays visible as a warning; the 15 live acceptance checks still apply.
# Release stabilization: preserve this exact candidate while its CI/deployment completes.
[CmdletBinding()]
param(
    [switch]$Deploy,
    [string]$Profile = "collectiverse-prod",
    [string]$Region = "us-east-1",
    [string]$FunctionName = "guardoath-site",
    [string]$StackName = "guardoath-site",
    [string]$ReceiptPath = "",
    [string]$ReceiptInvocationId = ""
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
function Invoke-GitHubRead([string]$Path) {
    # Never place credentials in a URL, child-process argument or diagnostic.
    # These are the only three read endpoints used by the release contract.
    if ($Path -cnotmatch '\A/repos/CollectiverseStudio/guardoath/(actions/runs\?head_sha=[a-f0-9]{40}&per_page=100|actions/runs/[1-9][0-9]*/jobs\?per_page=100|issues/30)\z') {
        throw 'Untrusted GitHub release evidence path'
    }
    $headers = @{ 'User-Agent' = 'GuardOath-Verified-Deployment';
        'Accept' = 'application/vnd.github+json'; 'X-GitHub-Api-Version' = '2022-11-28' }
    $token = [Environment]::GetEnvironmentVariable('GH_TOKEN')
    if ([string]::IsNullOrEmpty($token)) {
        $token = [Environment]::GetEnvironmentVariable('GITHUB_TOKEN')
    }
    if (-not [string]::IsNullOrEmpty($token)) {
        if ($token -cnotmatch '\A[\x21-\x7e]{1,4096}\z') {
            throw 'Invalid operator GitHub authentication'
        }
        $headers['Authorization'] = 'Bearer ' + $token
    }
    try {
        # A redirect is an error, including a redirect to another GitHub URL.
        # Suppress transport diagnostics, which can contain provider data.
        return Invoke-RestMethod -Uri ('https://api.github.com' + $Path) -Headers $headers -Method Get `
            -MaximumRedirection 0 -TimeoutSec 15 -Verbose:$false -Debug:$false -ErrorAction Stop
    } catch {
        throw 'Actual GitHub release evidence unavailable; verify operator authentication and rate limits'
    } finally {
        [void]$headers.Remove('Authorization')
        $token = $null
    }
}
function Invoke-DeploymentEvidence([string]$Operation, [object]$Value) {
    # Configuration contains secrets. Pass it only through private stdin to the
    # local validator, never arguments, files, normal stdout or error details.
    $payload = $Value | ConvertTo-Json -Depth 100 -Compress
    $lines = @($payload | & python deploy/aws/deployment_evidence.py $Operation 2>&1)
    if ($LASTEXITCODE -ne 0) { throw "Deployment evidence refused for $Operation" }
    $document = $null
    try {
        # ConvertFrom-Json in PowerShell 7.4 converts timestamp strings to
        # DateTime and changes their spelling. Keep the bounded flat evidence
        # exactly as the validator returned it, without exposing provider data.
        $document = [System.Text.Json.JsonDocument]::Parse(($lines -join "`n"))
        $values = @{}
        foreach ($property in $document.RootElement.EnumerateObject()) {
            $values[$property.Name] = switch ($property.Value.ValueKind.ToString()) {
                'String' { $property.Value.GetString() }
                'Number' { $property.Value.GetInt64() }
                'True' { $true }
                'False' { $false }
                'Null' { $null }
                default { throw "Unexpected evidence structure" }
            }
        }
        return [pscustomobject]$values
    } catch { throw "Deployment evidence response invalid" }
    finally { if ($null -ne $document) { $document.Dispose() } }
}
function Assert-CompletedLambdaCode([string]$Name, [string]$CodeSha256,
                                    [object]$Acknowledged, [string]$ConfigurationSha256) {
    $json = Invoke-Aws @('lambda', 'get-function-configuration', '--function-name', $Name)
    [void](ConvertTo-LambdaCodeEvidence $json) # Sanitize malformed provider evidence before decoding.
    return Invoke-DeploymentEvidence 'complete' @{
        acknowledged=$Acknowledged; observed=$json; expected_code_sha256=$CodeSha256;
        original_configuration_sha256=$ConfigurationSha256
    }
}
# Exact job membership is part of the release contract. A top-level "success"
# is not enough: a matrix job may have been removed, skipped or left unexamined.
$RequiredJobs = @{
    "Tests" = @(
        "test (3.11)", "test (3.12)", "test (3.13)", "test (3.14)", "test (3.15)",
        "local-pii (3.12)", "local-pii (3.13)", "local-pii (3.14)",
        "windows-launcher (3.12)", "windows-launcher (3.13)",
        "dashboard", "optional-aws-adapter", "release-wheel"
    )
    "Push on main" = @(
        "Analyze (python)", "Analyze (actions)", "Analyze (javascript-typescript)"
    )
    "Field Comparisons Evidence" = @(
        "strict-portfolio-and-scale", "customer-browser-acceptance"
    )
    "Trusted Reference Acceptance" = @("signed-reference")
    "PII Reliability Gate" = @(
        "real-pii-tail (ubuntu-latest, py3.12)",
        "real-pii-tail (ubuntu-latest, py3.13)",
        "real-pii-tail (windows-latest, py3.12)"
    )
    "Stripe Reconciliation Acceptance" = @("owner-billing-api-and-stores", "authenticated-billing-browser")
    "Deploy Script Acceptance" = @("powershell-parse-and-guardrails")
}

function Assert-RollbackCompatibility([string]$Candidate, [string]$Backup, [string]$Sha, [string]$CodeSha256) {
    return ((Invoke-Checked "python" @("deploy/aws/verify_rollback.py", "--candidate", $Candidate,
        "--backup", $Backup, "--expected-sha", $Sha, "--backup-code-sha256", $CodeSha256)) | ConvertFrom-Json)
}
function Assert-RollbackArchive([string]$Backup, [string]$CodeSha256) {
    [void](Invoke-Checked "python" @("deploy/aws/verify_rollback.py", "--backup", $Backup,
        "--backup-code-sha256", $CodeSha256, "--verify-backup-only"))
}
function Get-ArchiveCodeSha256([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { return [Convert]::ToBase64String($hasher.ComputeHash($stream)) }
    finally { $hasher.Dispose(); $stream.Dispose() }
}
function ConvertTo-LambdaCodeEvidence([string]$Json) {
    # AWS configuration responses can contain environment secrets. Keep only
    # the three required fields and never echo a malformed response body.
    if (-not $Json.TrimStart().StartsWith('{')) { throw "Lambda code evidence is not a configuration object" }
    try { $value = $Json | ConvertFrom-Json }
    catch { throw "Lambda code evidence is not valid JSON" }
    if ($null -eq $value -or $value -isnot [pscustomobject]) { throw "Lambda code evidence is not a configuration object" }
    foreach ($name in @('CodeSha256', 'RevisionId', 'LastUpdateStatus')) {
        if ($null -eq $value.PSObject.Properties[$name] -or $value.$name -isnot [string]) {
            throw "Lambda code evidence is missing required fields"
        }
    }
    try { $decoded = [Convert]::FromBase64String($value.CodeSha256) }
    catch { throw "Lambda code evidence has an invalid digest" }
    if ($decoded.Length -ne 32 -or [Convert]::ToBase64String($decoded) -cne $value.CodeSha256) {
        throw "Lambda code evidence has an invalid digest"
    }
    if ($value.RevisionId -cnotmatch '^[A-Za-z0-9_-]{1,128}$' -or
        $value.LastUpdateStatus -cnotin @('InProgress', 'Successful', 'Failed')) {
        throw "Lambda code evidence has an invalid revision or update state"
    }
    return [pscustomobject]@{CodeSha256=$value.CodeSha256; RevisionId=$value.RevisionId;
        LastUpdateStatus=$value.LastUpdateStatus}
}
function Assert-AppliedLambdaCode([string]$Name, [string]$CodeSha256, [string]$RevisionId) {
    $current = ConvertTo-LambdaCodeEvidence (Invoke-Aws @('lambda', 'get-function-configuration', '--function-name', $Name))
    if ($current.LastUpdateStatus -cne 'Successful' -or $current.CodeSha256 -cne $CodeSha256 -or
        $current.RevisionId -cne $RevisionId) {
        throw "Lambda code and successful update are not verified at the expected revision"
    }
}
function Restore-VerifiedLambdaBackup([string]$Name, [string]$Backup, [string]$BackupCodeSha256,
                                     [string]$CandidateCodeSha256, [string]$AppliedRevisionId) {
    $currentJson = Invoke-Aws @('lambda', 'get-function-configuration', '--function-name', $Name)
    $current = ConvertTo-LambdaCodeEvidence $currentJson
    if ($current.LastUpdateStatus -ceq 'InProgress') {
        throw "Automatic rollback refused while the Lambda update remains in progress"
    }
    if ($current.CodeSha256 -ceq $BackupCodeSha256) {
        # Observation only: this process did not perform a restoration.
        return 'backup_observed'
    }
    if ([string]::IsNullOrEmpty($AppliedRevisionId)) {
        throw "Automatic rollback refused: original update outcome is uncertain; no owned revision receipt"
    }
    if ($current.CodeSha256 -cne $CandidateCodeSha256 -or $current.RevisionId -cne $AppliedRevisionId) {
        throw "Automatic rollback refused: current code or revision belongs to another update"
    }
    # Never adopt a freshly observed revision as rollback authority. Reuse the
    # revision returned by OUR successful update, so a race is rejected by AWS.
    Assert-RollbackArchive $Backup $BackupCodeSha256
    $currentEvidence = Invoke-DeploymentEvidence 'inspect' $currentJson
    $restoredJson = Invoke-Aws @('lambda', 'update-function-code', '--function-name', $Name,
        '--revision-id', $AppliedRevisionId, '--zip-file', "fileb://$Backup")
    $restored = ConvertTo-LambdaCodeEvidence $restoredJson
    if ($restored.CodeSha256 -cne $BackupCodeSha256 -or $restored.RevisionId -ceq $AppliedRevisionId) {
        throw "Rollback response did not verify the backup at a new revision"
    }
    $restorationAcknowledged = Invoke-DeploymentEvidence 'inspect' $restoredJson
    [void](Invoke-Aws @('lambda', 'wait', 'function-updated', '--function-name', $Name))
    [void](Assert-CompletedLambdaCode $Name $BackupCodeSha256 $restorationAcknowledged $currentEvidence.configuration_sha256)
    return 'restored'
}
function Assert-Run([object[]]$Runs, [string]$Name, [string]$Sha) {
    $matches = @($Runs | Where-Object { $_.name -eq $Name -and $_.head_sha -eq $Sha })
    if (-not $matches.Count) {
        throw "No exact-SHA GitHub Actions run for $Name at $Sha. Dispatch the workflow and recheck."
    }
    $latest = $matches | Sort-Object created_at -Descending | Select-Object -First 1
    if ($latest.status -ne "completed" -or $latest.conclusion -ne "success") {
        throw "$Name not green for $($Sha): status=$($latest.status) conclusion=$($latest.conclusion) run=$($latest.html_url)"
    }
    if (-not $latest.id) { throw "$Name has no GitHub Actions run ID; cannot inspect individual jobs." }
    $jobResponse = Invoke-GitHubRead "/repos/$Repo/actions/runs/$($latest.id)/jobs?per_page=100"
    $jobs = @($jobResponse.jobs)
    if (-not $jobs.Count -or $jobResponse.total_count -ne $jobs.Count) {
        throw "$Name run $($latest.html_url) returned only $($jobs.Count) of $($jobResponse.total_count) jobs; fail closed on incomplete job evidence."
    }
    $expected = @($RequiredJobs[$Name])
    if (-not $expected.Count) { throw "No individual-job release contract is defined for $Name." }
    foreach ($jobName in $expected) {
        if (@($jobs | Where-Object { $_.name -eq $jobName }).Count -ne 1) {
            throw "$Name run $($latest.html_url) does not contain exactly one required job '$jobName'. Do not release."
        }
    }
    $failed = @($jobs | Where-Object { $_.status -ne "completed" -or $_.conclusion -ne "success" })
    if ($failed.Count) {
        $details = @($failed | ForEach-Object {
            "$($_.name): status=$($_.status), conclusion=$($_.conclusion), job=$($_.html_url)"
        }) -join "; "
        throw "$Name has $($failed.Count) non-successful individual job(s): $details"
    }
    Write-Host "$Name passed at ${Sha}: $($jobs.Count)/$($jobs.Count) individual jobs SUCCESS ($($latest.html_url))"
}

if ($ReceiptPath -or $ReceiptInvocationId) {
    if (-not $Deploy -or -not $ReceiptPath -or $ReceiptInvocationId -cnotmatch '^[a-f0-9]{64}$') {
        throw "A deployment receipt requires -Deploy, an explicit private path and a 64-hex invocation ID"
    }
    [void](Invoke-DeploymentEvidence 'path' @{path=$ReceiptPath; checkout=$Root})
}

$Sha = Invoke-Checked "git" @("rev-parse", "--verify", "HEAD")
if ($Sha -notmatch '^[a-f0-9]{40}$') { throw "Invalid full Git SHA" }
$dirty = Invoke-Checked "git" @("status", "--porcelain", "--untracked-files=all")
if ($dirty) { throw "Working tree not clean. Do not package or deploy uncommitted code." }
$origin = Invoke-Checked "git" @("ls-remote", "origin", "refs/heads/main")
$remoteSha = ($origin -split '\s+')[0]
if ($remoteSha -ne $Sha) { throw "Local HEAD $Sha is not current origin/main $remoteSha. Fetch and fast-forward first." }
Write-Host "Clean current main source: $Sha"

$RunsResponse = Invoke-GitHubRead "/repos/$Repo/actions/runs?head_sha=$Sha&per_page=100"
$Runs = @($RunsResponse.workflow_runs)
foreach ($required in @("Tests", "Push on main", "Field Comparisons Evidence", "Trusted Reference Acceptance", "PII Reliability Gate", "Stripe Reconciliation Acceptance", "Deploy Script Acceptance")) {
    Assert-Run $Runs $required $Sha
}
$OpenRisk = Invoke-GitHubRead "/repos/$Repo/issues/30"
if ($OpenRisk.state -ne "closed" -or $OpenRisk.state_reason -eq "not_planned") {
    # Greg explicitly authorized deployment without waiting for issue closure.
    # Exact-SHA PII jobs and runtime fail-closed deadlines remain mandatory.
    Write-Warning "Issue #30 remains tracked: intermittent 500-ms privacy latency is not proven resolved. Exact-SHA PII reliability jobs passed; late or incomplete privacy checks still Block. Issue status alone does not stop this authorized deployment."
}

# The account identity and deployed stack must be readable before any write.
$Identity = (Invoke-Aws @("sts", "get-caller-identity")) | ConvertFrom-Json
if ($Identity.Account -cne '839000214942') { throw "AWS caller is not the authorized production account" }
$Stack = (Invoke-Aws @("cloudformation", "describe-stacks", "--stack-name", $StackName)) | ConvertFrom-Json
if ($Stack.Stacks[0].StackStatus -notin @("CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE")) {
    throw "Stack not stable: $($Stack.Stacks[0].StackStatus)"
}
$Outputs = @{}
foreach ($output in $Stack.Stacks[0].Outputs) { $Outputs[$output.OutputKey] = $output.OutputValue }
if (-not $Outputs.DistributionId -or $Outputs.FunctionName -ne $FunctionName) {
    throw "CloudFormation outputs do not match the expected website function/distribution"
}
$FunctionJson = Invoke-Aws @("lambda", "get-function", "--function-name", $FunctionName)
$Function = $FunctionJson | ConvertFrom-Json
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
$BackupCodeSha256 = $Function.Configuration.CodeSha256
$OriginalEvidence = Invoke-DeploymentEvidence 'inspect' $FunctionJson
$Uploaded = $false
$CandidateCodeSha256 = $null
$AppliedRevisionId = $null
try {
    Invoke-WebRequest -Uri $Function.Code.Location -OutFile $BackupFile -UseBasicParsing
    if ((Get-Item $BackupFile).Length -lt 1000) { throw "Lambda rollback package is unexpectedly small" }
    Write-Host "Original Lambda package preserved at $BackupFile"
    [void](Invoke-Checked "python" @("deploy/aws/build_lambda.py"))
    $Zip = Join-Path $Root "deploy/aws/build/guardoath-lambda.zip"
    if (-not (Test-Path $Zip)) { throw "Release archive missing" }
    $CurrentHead = Invoke-Checked "git" @("rev-parse", "HEAD")
    if ($CurrentHead -ne $Sha) { throw "Git HEAD changed during build" }
    $RollbackEvidence = Assert-RollbackCompatibility $Zip $BackupFile $Sha $BackupCodeSha256
    $CandidateCodeSha256 = Get-ArchiveCodeSha256 $Zip
    $Uploaded = $true # AWS may apply the update even if the client loses its response.
    $AppliedJson = Invoke-Aws @("lambda", "update-function-code", "--function-name", $FunctionName,
        "--revision-id", $Function.Configuration.RevisionId, "--zip-file", "fileb://$Zip")
    $Applied = ConvertTo-LambdaCodeEvidence $AppliedJson
    if ($Applied.CodeSha256 -cne $CandidateCodeSha256) { throw "Update response did not verify candidate archive bytes" }
    $AppliedRevisionId = $Applied.RevisionId
    $Acknowledged = Invoke-DeploymentEvidence 'inspect' $AppliedJson
    Write-Host "Lambda upload acknowledged: code $CandidateCodeSha256 | revision $AppliedRevisionId"
    [void](Invoke-Aws @("lambda", "wait", "function-updated", "--function-name", $FunctionName))
    # AWS changes revision for asynchronous state transitions. Bind completed
    # code to our acknowledgement's timestamp and unchanged complete settings.
    # Never replace AppliedRevisionId with this observed completion revision.
    $Completed = Assert-CompletedLambdaCode $FunctionName $CandidateCodeSha256 $Acknowledged $OriginalEvidence.configuration_sha256
    $Invalidation = (Invoke-Aws @("cloudfront", "create-invalidation", "--distribution-id", $Outputs.DistributionId,
        "--paths", "/assets/*")) | ConvertFrom-Json
    $Parity = (Invoke-Checked "python" @("-m", "tools.verify_live_dashboard",
        "--url", "https://guardoath.com", "--expected-sha", $Sha)) | ConvertFrom-Json
    if ($ReceiptPath) {
        $receipt = @{
            format='guardoath.catalog-deployment-receipt.v1'; invocation_id=$ReceiptInvocationId;
            source_sha=$Sha; code_sha256=$CandidateCodeSha256; backup_code_sha256=$BackupCodeSha256;
            backup_source_sha=$RollbackEvidence.rollback_source_sha;
            original_revision_id=$Function.Configuration.RevisionId; upload_revision_id=$AppliedRevisionId;
            verified_revision_id=$Completed.revision_id; last_modified=$Completed.last_modified;
            configuration_sha256=$Completed.configuration_sha256; account_id=$Identity.Account;
            region=$Region; function_name=$FunctionName; stack_name=$StackName;
            distribution_id=$Outputs.DistributionId; invalidation_id=$Invalidation.Invalidation.Id;
            observed_at=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds();
            automatic_rollback_owned=$Completed.automatic_rollback_owned; last_update_status='Successful';
            release_source_parity_verified=$Parity.release_source_parity_verified;
            dashboard_ui_matches_local_source=$Parity.dashboard_ui_matches_local_source;
            expected_source_sha=$Parity.expected_source_sha; live_source_sha=$Parity.live_source_sha;
            verified_asset_count=$Parity.verified_asset_count; expected_asset_count=$Parity.expected_asset_count;
            production_live_acceptance=$false
        }
        [void](Invoke-DeploymentEvidence 'write' @{receipt=$receipt; path=$ReceiptPath; checkout=$Root})
    }
    Write-Host "Source parity verified. Production is NOT accepted until all 15 live safety checks have passed."
} catch {
    $DeploymentFailure = $_
    Write-Warning "Deployment or parity verification failed: $DeploymentFailure"
    if ($Uploaded -and (Test-Path $BackupFile)) {
        try {
            $Recovery = Restore-VerifiedLambdaBackup $FunctionName $BackupFile $BackupCodeSha256 $CandidateCodeSha256 $AppliedRevisionId
            if ($Recovery -ceq 'restored') {
                [void](Invoke-Aws @("cloudfront", "create-invalidation", "--distribution-id", $Outputs.DistributionId,
                    "--paths", "/assets/*"))
                Write-Warning "Restored backup Lambda archive and verified its code/revision. All 15 live production checks remain required."
            } elseif ($Recovery -ceq 'backup_observed') {
                Write-Warning "Previously recorded backup code digest is already present; no restoration was performed. Live acceptance remains unverified."
            }
        } catch {
            Write-Warning "Automatic rollback was not verified: $_. Preserved backup: $BackupFile. No retry with another revision is permitted."
        }
    }
    throw $DeploymentFailure
}
