# GuardOath test-mode Stripe setup (does not enable live payments).
# Run from the repository root: .\deploy\aws\Configure-Stripe-Test.ps1
# Never paste or commit Stripe secret keys. This script preserves all existing Lambda environment variables.
[CmdletBinding()]
param(
    [string]$Profile = "collectiverse-prod",
    [string]$Region = "us-east-1",
    [string]$FunctionName = "guardoath-site",
    [string]$PriceId = "price_1UajKvCcAkYW08dPZubVXO6j",
    [ValidateRange(0, 1000000000)][int]$MonthlyAllowance = 0
)
$ErrorActionPreference = "Stop"
if ($PriceId -notmatch '^price_[A-Za-z0-9_]+$') { throw "Invalid Stripe price ID" }
$secret = Read-Host "Paste the Stripe TEST secret key (input hidden)" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
try {
    $key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
}
if (-not $key.StartsWith("sk_test_") -or $key.Length -lt 16) {
    throw "Only Stripe test-mode secret keys are accepted"
}
$raw = & aws lambda get-function-configuration --function-name $FunctionName --region $Region --profile $Profile --output json
if ($LASTEXITCODE -ne 0) { throw "Cannot read current Lambda configuration" }
$config = $raw | ConvertFrom-Json
if ($config.LastUpdateStatus -ne "Successful") { throw "Lambda is not ready for configuration update" }
$vars = @{}
if ($config.Environment -and $config.Environment.Variables) {
    foreach ($p in $config.Environment.Variables.PSObject.Properties) { $vars[$p.Name] = $p.Value }
}
$vars["GUARDOATH_STRIPE_SECRET_KEY"] = $key
$vars["GUARDOATH_STRIPE_PRICE_ID"] = $PriceId
# Explicit plan quota only. Zero means no verified paid use is enabled.
# Do not invent a product allowance; the operator must choose it.
if ($MonthlyAllowance -gt 0) {
    $vars["GUARDOATH_PAID_MONTHLY_ALLOWANCE"] = [string]$MonthlyAllowance
}
# Avoid exposing secrets in process arguments or writing them to a tracked repository path.
$temp = Join-Path $env:TEMP ("guardoath-stripe-" + [guid]::NewGuid().ToString("N") + ".json")
try {
    $payload = @{ Variables = $vars } | ConvertTo-Json -Depth 8 -Compress
    [IO.File]::WriteAllText($temp, $payload, [Text.UTF8Encoding]::new($false))
    $key = $null
    $payload = $null
    & aws lambda update-function-configuration --function-name $FunctionName --region $Region --profile $Profile --environment "file://$temp" --revision-id $config.RevisionId --query '{LastUpdateStatus:LastUpdateStatus}' --output json
    if ($LASTEXITCODE -ne 0) { throw "Lambda configuration update failed" }
} finally {
    if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force }
}
& aws lambda wait function-updated --function-name $FunctionName --region $Region --profile $Profile
if ($LASTEXITCODE -ne 0) { throw "Lambda configuration update did not complete" }
$check = & aws lambda get-function-configuration --function-name $FunctionName --region $Region --profile $Profile --query 'LastUpdateStatus' --output text
if ($LASTEXITCODE -ne 0 -or $check.Trim() -ne "Successful") { throw "Lambda configuration not successful" }
Write-Host "Test-mode Stripe settings saved. No secret values printed."
Write-Host "Verify: Invoke-WebRequest https://guardoath.com/v1/billing/price"
if ($MonthlyAllowance -eq 0) {
    Write-Warning "GUARDOATH_PAID_MONTHLY_ALLOWANCE was not configured. Verified subscriptions cannot activate until the plan quota is explicitly set."
} else {
    Write-Host "Paid plan monthly checks configured: $MonthlyAllowance (Stripe TEST mode only)."
}
