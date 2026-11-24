# Execute production rollback/evidence functions. Only the AWS CLI boundary is
# scripted; archive hashing and the Python backup verifier are the real code.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$scriptPath = (Resolve-Path 'deploy/aws/Deploy-Verified.ps1').Path
$ast = [System.Management.Automation.Language.Parser]::ParseFile($scriptPath,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'Deployment script cannot be parsed' }
foreach ($name in @('Invoke-Checked','Assert-RollbackArchive','Get-ArchiveCodeSha256',
    'ConvertTo-LambdaCodeEvidence','Assert-AppliedLambdaCode','Restore-VerifiedLambdaBackup')) {
    $method = $ast.Find({ param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name
    },$true)
    if (-not $method) { throw "Missing production function $name" }
    Invoke-Expression $method.Extent.Text
}

$script:responses = [Collections.Generic.Queue[object]]::new()
$script:calls = [Collections.Generic.List[object]]::new()
function Invoke-Aws([string[]]$Arguments) {
    [void]$script:calls.Add($Arguments)
    if (-not $script:responses.Count) { throw 'Unexpected AWS call: no scripted response' }
    $expected = $script:responses.Dequeue()
    if (($Arguments[0..1] -join ' ') -cne $expected.Command) { throw 'Unexpected AWS operation ordering' }
    if ($expected.Error) { throw $expected.Error }
    return $expected.Json
}
function Reply([string]$Command, [string]$Hash='', [string]$Revision='', [string]$Status='Successful') {
    return @{Command=$Command; Error=$null; Json=(@{CodeSha256=$Hash; RevisionId=$Revision;
        LastUpdateStatus=$Status} | ConvertTo-Json -Compress)}
}
function BrokenReply([string]$Command, [string]$Message) {
    return @{Command=$Command; Error=$Message; Json=''}
}
function Scenario([object[]]$Replies) {
    $script:responses.Clear(); $script:calls.Clear()
    foreach ($item in $Replies) { $script:responses.Enqueue($item) }
}
function Assert-Calls([int]$Total, [int]$Writes) {
    if ($script:responses.Count -ne 0 -or $script:calls.Count -ne $Total) { throw 'Incomplete AWS boundary assertions' }
    $updates = [Collections.Generic.List[object]]::new()
    foreach ($call in $script:calls) {
        if ($call[0] -ceq 'lambda' -and $call[1] -ceq 'update-function-code') { [void]$updates.Add($call) }
    }
    if ($updates.Count -ne $Writes) { throw "Expected $Writes conditional code writes, observed $($updates.Count)" }
    foreach ($call in $updates) {
        $revisionIndex = [Array]::IndexOf($call, '--revision-id')
        $fileIndex = [Array]::IndexOf($call, '--zip-file')
        if ($revisionIndex -lt 0 -or $call[$revisionIndex+1] -cne 'owned-revision' -or
            $fileIndex -lt 0 -or $call[$fileIndex+1] -cne "fileb://$script:backup") {
            throw 'Restoration must use the owned revision and exact preserved archive'
        }
    }
}
function Expect-Refused([scriptblock]$Action, [string]$Phrase) {
    try { [void](& $Action) }
    catch {
        if (-not $_.Exception.Message.Contains($Phrase)) {
            throw "Wrong refusal; expected '$Phrase', received: $($_.Exception.Message)"
        }
        return
    }
    throw "Unsafe action accepted; expected refusal containing '$Phrase'"
}
function Archive([string]$Path, [string]$Sha) {
    $stream = [IO.File]::Create($Path)
    $zip = [IO.Compression.ZipArchive]::new($stream,[IO.Compression.ZipArchiveMode]::Create)
    try {
        $entry = $zip.CreateEntry('guardoath/_release_source.json')
        $writer = [IO.StreamWriter]::new($entry.Open(),[Text.UTF8Encoding]::new($false))
        try { $writer.Write((@{source_sha=$Sha} | ConvertTo-Json -Compress)) }
        finally { $writer.Dispose() }
    } finally { $zip.Dispose(); $stream.Dispose() }
}

$directory = Join-Path ([IO.Path]::GetTempPath()) ('guardoath-owned-rollback-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $directory | Out-Null
try {
    $script:backup = Join-Path $directory 'backup.zip'
    $candidate = Join-Path $directory 'candidate.zip'
    Archive $backup ('b' * 40)
    Archive $candidate ('a' * 40)
    $backupHash = Get-ArchiveCodeSha256 $backup
    $candidateHash = Get-ArchiveCodeSha256 $candidate
    $thirdHash = [Convert]::ToBase64String([byte[]](1..32))
    $get = 'lambda get-function-configuration'
    $update = 'lambda update-function-code'
    $wait = 'lambda wait'
    $restore = { Restore-VerifiedLambdaBackup 'synthetic-function' $backup $backupHash $candidateHash 'owned-revision' }

    # Both terminal outcomes retain ownership, unlike unresolved InProgress.
    foreach ($status in @('Successful','Failed')) {
        Scenario @((Reply $get $candidateHash 'owned-revision' $status),
            (Reply $update $backupHash 'restored-revision' 'InProgress'),
            (Reply $wait), (Reply $get $backupHash 'restored-revision'))
        if ((& $restore) -cne 'restored') { throw 'Owned revision was not restored and verified' }
        Assert-Calls 4 1
    }

    Scenario @((Reply $get $backupHash 'original-revision'))
    $observed = Restore-VerifiedLambdaBackup 'synthetic-function' $backup $backupHash $candidateHash ''
    if ($observed -cne 'backup_observed') { throw 'Already-present backup must be observation only' }
    Assert-Calls 1 0

    Scenario @((Reply $get $thirdHash 'other-revision'))
    Expect-Refused $restore 'belongs to another update'
    Assert-Calls 1 0
    Scenario @((Reply $get $candidateHash 'other-revision'))
    Expect-Refused $restore 'belongs to another update'
    Assert-Calls 1 0
    Scenario @((Reply $get $candidateHash 'owned-revision'))
    Expect-Refused { Restore-VerifiedLambdaBackup 'synthetic-function' $backup $backupHash $candidateHash '' } 'no owned revision receipt'
    Assert-Calls 1 0
    foreach ($hash in @($candidateHash,$backupHash)) {
        Scenario @((Reply $get $hash 'owned-revision' 'InProgress'))
        Expect-Refused $restore 'remains in progress'
        Assert-Calls 1 0
    }

    # A conflicting writer after our read must not cause a retry with its new revision.
    Scenario @((Reply $get $candidateHash 'owned-revision'), (BrokenReply $update 'Synthetic revision conflict'))
    Expect-Refused $restore 'Synthetic revision conflict'
    Assert-Calls 2 1
    Scenario @((Reply $get $candidateHash 'owned-revision'), (BrokenReply $update 'Synthetic lost restoration response'))
    Expect-Refused $restore 'Synthetic lost restoration response'
    Assert-Calls 2 1

    Scenario @((Reply $get $candidateHash 'owned-revision'), (Reply $update $thirdHash 'restored-revision'))
    Expect-Refused $restore 'did not verify the backup'
    Assert-Calls 2 1
    Scenario @((Reply $get $candidateHash 'owned-revision'), (Reply $update $backupHash 'owned-revision'))
    Expect-Refused $restore 'did not verify the backup'
    Assert-Calls 2 1
    foreach ($final in @((Reply $get $thirdHash 'restored-revision'),
        (Reply $get $backupHash 'other-revision'),
        (Reply $get $backupHash 'restored-revision' 'Failed'),
        (Reply $get $backupHash 'restored-revision' 'InProgress'))) {
        Scenario @((Reply $get $candidateHash 'owned-revision'),
            (Reply $update $backupHash 'restored-revision' 'InProgress'), (Reply $wait), $final)
        Expect-Refused $restore 'not verified at the expected revision'
        Assert-Calls 4 1
    }

    # Hash/revision comparisons must be ordinal: base64 is case-sensitive.
    $caseHash = 'A' * 43 + '='
    $differentCaseHash = 'a' + ('A' * 42) + '='
    Scenario @((Reply $get $differentCaseHash 'owned-revision'))
    Expect-Refused { Assert-AppliedLambdaCode 'synthetic-function' $caseHash 'owned-revision' } 'not verified at the expected revision'
    Assert-Calls 1 0
    Scenario @((Reply $get $candidateHash 'OWNED-revision'))
    Expect-Refused $restore 'belongs to another update'
    Assert-Calls 1 0

    # Candidate acceptance also checks the exact retained revision after waiting.
    Scenario @((Reply $get $candidateHash 'owned-revision'))
    Assert-AppliedLambdaCode 'synthetic-function' $candidateHash 'owned-revision'
    Assert-Calls 1 0

    # Invalid configuration evidence is sanitized; environment values never escape.
    foreach ($bad in @('[]', '"private-provider-body"', '{"Environment":"private-provider-body"',
        '{"Environment":"private-provider-body"}',
        (@{CodeSha256='bad'; RevisionId='r'; LastUpdateStatus='Successful'} | ConvertTo-Json -Compress),
        (@{CodeSha256=$candidateHash; RevisionId=''; LastUpdateStatus='Successful'} | ConvertTo-Json -Compress),
        (@{CodeSha256=$candidateHash; RevisionId='r'; LastUpdateStatus='Unknown'} | ConvertTo-Json -Compress))) {
        try { [void](ConvertTo-LambdaCodeEvidence $bad); throw 'Malformed code evidence accepted' }
        catch {
            if ($_.Exception.Message -ceq 'Malformed code evidence accepted' -or
                $_.Exception.Message.Contains('private-provider-body')) { throw }
        }
    }
    $safe = ConvertTo-LambdaCodeEvidence (@{CodeSha256=$candidateHash;RevisionId='owned-revision';
        LastUpdateStatus='Successful';Environment=@{Variables=@{SECRET='private-provider-body'}}} | ConvertTo-Json -Depth 5 -Compress)
    if ((($safe.PSObject.Properties.Name | Sort-Object) -join ',') -cne 'CodeSha256,LastUpdateStatus,RevisionId') {
        throw 'Only bounded evidence fields may leave the provider response decoder'
    }

    # The original digest check remains immediately before any restore write.
    [IO.File]::AppendAllText($backup,'tampered after initial acceptance')
    Scenario @((Reply $get $candidateHash 'owned-revision'))
    Expect-Refused $restore 'does not match deployed CodeSha256'
    Assert-Calls 1 0
    Write-Host 'Actual rollback ownership functions: owned revision restored; conflicts, ambiguity, malformed evidence and tampered backups refused'
} finally {
    Remove-Item $directory -Recurse -Force
}
# Intentional native verifier refusal must not override completed assertions.
exit 0
