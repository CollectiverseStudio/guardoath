# Execute the actual deployment compatibility functions with synthetic ZIPs.
# This test imports no deployment top-level code and never invokes AWS.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$scriptPath = (Resolve-Path 'deploy/aws/Deploy-Verified.ps1').Path
$ast = [System.Management.Automation.Language.Parser]::ParseFile($scriptPath,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'Deployment script cannot be parsed' }
foreach ($name in @('Invoke-Checked','Assert-RollbackCompatibility','Assert-RollbackArchive')) {
    $method = $ast.Find({ param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name
    },$true)
    if (-not $method) { throw "Missing actual deployment function $name" }
    Invoke-Expression $method.Extent.Text
}
function Invoke-Aws { throw 'No AWS call permitted in offline rollback acceptance' }
function Expect-Refused([scriptblock]$Action, [string]$Phrase) {
    try { & $Action }
    catch {
        if ($_.Exception.Message.Contains($Phrase)) { return }
        throw "Wrong rollback refusal; expected '$Phrase', received: $($_.Exception.Message)"
    }
    throw "Incompatible rollback was accepted; expected '$Phrase'"
}
function New-TestArchive([string]$Path,[string]$Metadata) {
    $stream = [IO.File]::Create($Path)
    $zip = [IO.Compression.ZipArchive]::new($stream,[IO.Compression.ZipArchiveMode]::Create)
    try {
        $entry = $zip.CreateEntry('guardoath/_release_source.json')
        $writer = [IO.StreamWriter]::new($entry.Open(),[Text.UTF8Encoding]::new($false))
        try { $writer.Write($Metadata) } finally { $writer.Dispose() }
    } finally { $zip.Dispose(); $stream.Dispose() }
}
function Digest([string]$Path) {
    return [Convert]::ToBase64String([Security.Cryptography.SHA256]::HashData([IO.File]::ReadAllBytes($Path)))
}
$directory = Join-Path ([IO.Path]::GetTempPath()) ('guardoath-rollback-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $directory | Out-Null
try {
    $sha = 'a' * 40
    $oldSha = 'b' * 40
    $old = Join-Path $directory 'old.zip'
    $baseline = Join-Path $directory 'baseline.zip'
    $writer = Join-Path $directory 'writer.zip'
    $contract = 'subscription-cap-provenance-v1'
    New-TestArchive $old (@{source_sha=$oldSha} | ConvertTo-Json -Compress)
    New-TestArchive $baseline (@{source_sha=$sha;release_contract_version=1;
        supported_state_contracts=@($contract);introduced_state_contracts=@()} | ConvertTo-Json -Compress)
    New-TestArchive $writer (@{source_sha=$sha;release_contract_version=1;
        supported_state_contracts=@($contract);introduced_state_contracts=@($contract)} | ConvertTo-Json -Compress)
    Assert-RollbackCompatibility $baseline $old $sha (Digest $old)
    Expect-Refused { Assert-RollbackCompatibility $writer $old $sha (Digest $old) } 'cannot preserve new persistent state'
    Assert-RollbackCompatibility $writer $baseline $sha (Digest $baseline)
    Expect-Refused { Assert-RollbackCompatibility $writer $baseline $oldSha (Digest $baseline) } 'Candidate source differs from accepted exact SHA'
    $acceptedDigest = Digest $baseline
    Assert-RollbackArchive $baseline $acceptedDigest
    [IO.File]::AppendAllText($baseline,'changed after deployment approval')
    Expect-Refused { Assert-RollbackArchive $baseline $acceptedDigest } 'does not match deployed CodeSha256'

    # Verify the checked functions remain on the actual write/restore path.
    $source = Get-Content $scriptPath -Raw
    $gateCall = $source.IndexOf('Assert-RollbackCompatibility $Zip $BackupFile $Sha $BackupCodeSha256')
    $updateMark = $source.IndexOf('    $Uploaded = $true')
    if ($gateCall -lt 0 -or $gateCall -ge $updateMark) { throw 'Compatibility must be checked before Lambda may change' }
    $restoreMethod = $ast.Find({ param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Restore-VerifiedLambdaBackup'
    },$true)
    if (-not $restoreMethod -or -not $source.Contains('Restore-VerifiedLambdaBackup $FunctionName $BackupFile')) {
        throw 'Production failure path must use the tested rollback helper'
    }
    $restoreCheck = $restoreMethod.Extent.Text.IndexOf('    Assert-RollbackArchive $Backup $BackupCodeSha256')
    $restoreWrite = $restoreMethod.Extent.Text.IndexOf('"fileb://$Backup"')
    if ($restoreCheck -lt 0 -or $restoreCheck -ge $restoreWrite) { throw 'Reverify backup bytes before restoration' }
    Write-Host 'Actual rollback functions: compatible pairs accepted, incompatible or changed archives refused before writes'
} finally {
    Remove-Item $directory -Recurse -Force
}
# The last negative case deliberately makes Python exit 1. PowerShell's GitHub
# runner propagates LASTEXITCODE even after Expect-Refused verified that failure.
# Reach success only after every assertion and cleanup completed; an uncaught
# refusal/assertion still terminates above with ErrorActionPreference=Stop.
exit 0
