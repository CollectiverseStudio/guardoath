# GuardOath website on AWS (guardoath.com)

One CloudFormation stack, `deploy/aws/guardoath-site.json`, in **us-east-1**. It is designed to cost
nothing: every part is either covered by the CloudFront flat-rate **Free** plan or stays inside an AWS
always-free allowance, and alarms stop the site before Lambda's free allowances run out. The cost table
below says where each figure comes from.

```
visitor --HTTPS--> CloudFront (Free plan) --> AWS WAF (1 rate rule, in the plan)
                        |  origin access control (SigV4)
                        v
                 Lambda function URL (AWS_IAM) --> guardoath-site (python3.13, 512 MB, 2 at a time)
                                                        |
                                                        v
                                               DynamoDB guardoath-tenants (provisioned 10/10)
alarms (invocations per hour/day, run time per hour) --> guardoath-site-shutoff (sets the site's concurrency to 0)
```

- **Caching:** `/assets/*` (page script, styles, logo, video parts) is cached at CloudFront. Everything
  else (the page, the API) is not cached and reaches the app with all viewer headers except Host.
- **Keys:** CloudFront replaces `Authorization` with its own signature, so the hosted API takes the key in
  `X-GuardOath-Key`. A POST must also send `x-amz-content-sha256` (hex SHA-256 of the body). The dashboard
  does both. See `docs/tenant-records.md`.
- **Video:** served in parts of at most 3 MiB, because a Lambda function URL response is limited to 6 MB.
- **Demo users:** created in the table on the first request; their keys come from the `DemoKeySecret`
  parameter (kept in the function's environment).

## Cost table (all figures checked against AWS pricing pages or the AWS price list before deploying)

| Part | What it is | Cost | Source |
|---|---|---|---|
| CloudFront distribution, WAF web ACL + 1 rule + requests, Route 53 zone + records + queries | Flat-rate **Free** plan (`AWS::PricingPlanManager::Subscription`, tier FREE) | $0. Allowance 1M requests / 100 GB a month; above it AWS may slow the site but never charges | CloudFront flat-rate plan docs ("Costs covered by your plan"); CFN resource reference (free subscriptions activate immediately) |
| Lambda requests and compute | Always-free: 1,000,000 requests and 400,000 GB-seconds a month, shared across the organization | $0 while under the allowance; the alarms stop the site first (1,300/hour, 30,000/day, 900 s of run time/hour at 0.5 GB) | AWS price list for Lambda (`_cost-price-lambda.json`); October organization usage: no Lambda |
| Lambda function URL | HTTPS endpoint for the function | No extra charge | Lambda function URL pricing docs |
| Data from Lambda to CloudFront | Origin fetches | $0 (AWS origin to CloudFront) | CloudFront pricing |
| DynamoDB table | Provisioned 10 read / 10 write units, under 1 GB | $0: the first 25 read and 25 write units (18,600 unit-hours each a month) and 25 GB are free | AWS price list for DynamoDB (`_cost-price-ddb.json`); October organization usage: no DynamoDB |
| CloudWatch alarms | 3 standard alarms on AWS/Lambda metrics | $0: 10 alarms are free; the organization used 2.45 in October | Free-tier usage report (`_cost-freetier-usage.json`) |
| CloudWatch Logs | 2 log groups, 7-day retention | $0 under 5 GB ingested a month (organization: 0.17 GB in October) | Same report |
| Custom metrics | None created (WAF metrics are off) | $0. The organization's 10 free custom metrics are already used, so the stack adds none | Same report |
| ACM certificate | guardoath.com + www | $0 (public certificates) | ACM pricing |
| CloudFormation, IAM, origin access control | | $0 | CloudFormation pricing (AWS::* resources), IAM |
| CloudFront invalidations | `/assets/*` after a code change (one path) | $0: the first 1,000 paths a month are free per account | CloudFront docs, "Pay for file invalidation" |
| Lambda code storage | ~40 MB zip, uploaded directly (no S3 bucket) | $0: the Lambda price list has no code-storage charge | `run_python_output_999.txt` (price-list usage types) |
| Already paid | Domain $16/year; hosted zone $0.50/month until it is attached to the Free plan | unchanged / then $0 | Route 53 billing |

**Small amounts that can happen once:**
- The web ACL exists for a few minutes before the Free plan covers it. WAF pay-as-you-go is $5 per web
  ACL-month plus $1 per rule-month, charged by the hour: about $0.008 per hour.
- If the Free plan subscription is refused (for example, AWS treats the account as "using AWS Free
  Tier", which makes it ineligible), CloudFormation rolls the whole stack back. The web ACL then exists
  for the time the rollback takes, at the same hourly rate.

## Before deploying

```cmd
cd /d C:\Users\bellgj\Documents\Personal\collectiverse\guardoath
aws lambda get-account-settings --region us-east-1 --profile collectiverse-prod > .guardoath-reports\lambda-account.json
type .guardoath-reports\lambda-account.json
```
Expect `"ConcurrentExecutions": 1000` and `"UnreservedConcurrentExecutions"` of at least 102 (Lambda keeps
100 unreserved; the site reserves 2).

## Deploy

1. Build the package (needs internet; about a minute). Expect the last lines to show the unzipped size
   under 262,144,000 bytes and the zip under 52,428,800 bytes:
   ```cmd
   cd /d C:\Users\bellgj\Documents\Personal\collectiverse\guardoath
   C:\Users\bellgj\AppData\Local\Programs\Python\Python313\python.exe deploy\aws\build_lambda.py
   ```
2. Make the demo key secret once and keep it (`.guardoath-reports` is not in git). Changing it later
   changes every demo key:
   ```cmd
   C:\Users\bellgj\AppData\Local\Programs\Python\Python313\python.exe -c "import secrets; print(secrets.token_urlsafe(48))" > .guardoath-reports\demo-key-secret.txt
   set /p GUARDOATH_SECRET=<.guardoath-reports\demo-key-secret.txt
   ```
3. Create the stack (10 to 30 minutes: the certificate is validated through DNS and the distribution is
   deployed worldwide). Expect `Successfully created/updated stack - guardoath-site`:
   ```cmd
   aws cloudformation deploy --region us-east-1 --profile collectiverse-prod --stack-name guardoath-site --template-file deploy\aws\guardoath-site.json --capabilities CAPABILITY_IAM --parameter-overrides DemoKeySecret=%GUARDOATH_SECRET% --no-fail-on-empty-changeset > .guardoath-reports\deploy-stack.txt 2>&1
   type .guardoath-reports\deploy-stack.txt
   ```
4. Upload the app (the stack starts with a placeholder that answers "being deployed"). Expect
   `Successful` and the code size:
   ```cmd
   aws lambda update-function-code --region us-east-1 --profile collectiverse-prod --function-name guardoath-site --zip-file fileb://deploy/aws/build/guardoath-lambda.zip --query "CodeSize" --output text
   aws lambda wait function-updated --region us-east-1 --profile collectiverse-prod --function-name guardoath-site
   aws lambda get-function --region us-east-1 --profile collectiverse-prod --function-name guardoath-site --query "Configuration.LastUpdateStatus" --output text
   ```
5. Check the site. Expect `ACTIVE` for the Free plan and `{"status":"ok",...}` from the site:
   ```cmd
   aws cloudformation describe-stacks --region us-east-1 --profile collectiverse-prod --stack-name guardoath-site --query "Stacks[0].Outputs" --output table > .guardoath-reports\stack-outputs.txt
   type .guardoath-reports\stack-outputs.txt
   curl -s https://guardoath.com/healthz
   ```

## After a code change

Run steps 1 and 4, then clear the cached assets (one invalidation path):
```cmd
aws cloudfront create-invalidation --profile collectiverse-prod --distribution-id <DistributionId from the outputs> --paths "/assets/*"
```

## If an alarm stopped the site

The shutoff sets the site's reserved concurrency to 0, so CloudFront gets an error for every request.
Find out why (CloudWatch alarm history), then turn the site back on:
```cmd
aws lambda put-function-concurrency --region us-east-1 --profile collectiverse-prod --function-name guardoath-site --reserved-concurrent-executions 2
```

## Removing the site

`aws cloudformation delete-stack --region us-east-1 --profile collectiverse-prod --stack-name guardoath-site`.
The table `guardoath-tenants` is kept (deletion protection and Retain); delete it separately if wanted.
