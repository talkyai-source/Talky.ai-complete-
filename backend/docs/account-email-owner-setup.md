# Account email sender and domain verification

Item 21 uses the existing Microsoft 365 account SMTP sender for signup,
verification, password reset, support, billing and security notifications.
No live message, DNS change or tenant administration was performed by this fix.
Provider acceptance is not proof of inbox delivery or DMARC alignment.

## Application settings

Set these in the owner's existing protected deployment configuration; do not
paste the password into a command line, log or issue:

```dotenv
BRAND_NAME=Talk-Lee
FROM_EMAIL=accounts@talkleeai.com
FRONTEND_URL=https://talkleeai.com
```

`accounts@talkleeai.com` is a proposed address: the owner must provision it or
choose another real mailbox/alias on the **exact** `talkleeai.com` domain.
Production startup refuses a missing, display-name-formatted or foreign-domain
`FROM_EMAIL`. `EMAIL_USER` and `EMAIL_PASS` remain the existing Microsoft 365
SMTP login, separate from the visible sender. If the addresses differ, grant
the login the appropriate Send As permission. SMTP uses
`smtp.office365.com:587`, STARTTLS, a 10-second operation timeout and a
20-second overall send bound. Confirm the tenant/mailbox permits the existing
SMTP authentication method; this change does not introduce OAuth or weaken
Microsoft tenant policy. Do not repoint `EMAIL_PROVIDER` or `SMTP_*` to activate
a second account mail transport: active notifications delegate to the account
sender. Tenant-connected email remains a separate customer connector.

`FRONTEND_URL` must be the deployed HTTPS frontend origin. Customer links use
`/auth/verify-email`, `/auth/forgot-password`, `/support`, `/billing` and
`/security`; support ticket links use `/support/tickets/{id}`. Meeting invitation
join links remain validated provider meeting links. Restart the backend and
notification workers after changing settings so cached sender instances reload.

## Proposed DNS records and owner verification

The QA report observed Google-only SPF and Google DKIM, with DMARC
`p=quarantine`. Those historical observations have **not** been rechecked here.
The owner must inspect current DNS and retain every still-authorized sender.

If Google remains authorized and no other mechanisms were added since QA,
replace the existing SPF TXT value at `talkleeai.com` (`@` in most DNS consoles)
with this **single** SPF record, TTL 3600:

```text
v=spf1 include:_spf.google.com include:spf.protection.outlook.com ~all
```

Do not publish a second SPF record. If current SPF differs, merge the Microsoft
include into that record and validate its DNS lookup limit. Preserve the
existing Google DKIM record and current DMARC policy; do not relax DMARC to make
failed messages appear delivered. No MX change is needed for this sender fix.

In an owner-approved Exchange Online PowerShell session, obtain the exact
Microsoft tenant-specific DKIM destinations:

```powershell
Connect-ExchangeOnline
Get-DkimSigningConfig -Identity talkleeai.com |
    Format-List Domain,Enabled,Status,Selector1CNAME,Selector2CNAME
```

If the domain has no DKIM signing configuration, create it disabled first, then
repeat the read command:

```powershell
New-DkimSigningConfig -DomainName talkleeai.com -Enabled $false
Get-DkimSigningConfig -Identity talkleeai.com |
    Format-List Domain,Enabled,Status,Selector1CNAME,Selector2CNAME
```

Publish exactly these two CNAMEs, TTL 3600. Values must be copied from that
domain's returned configuration; they cannot be inferred from the public domain.

| DNS name | Type | Destination |
| --- | --- | --- |
| `selector1._domainkey.talkleeai.com` | CNAME | Exact `Selector1CNAME` value |
| `selector2._domainkey.talkleeai.com` | CNAME | Exact `Selector2CNAME` value |

After DNS propagation, the owner verifies the records and enables signing:

```powershell
Resolve-DnsName talkleeai.com -Type TXT
Resolve-DnsName selector1._domainkey.talkleeai.com -Type CNAME
Resolve-DnsName selector2._domainkey.talkleeai.com -Type CNAME
Resolve-DnsName _dmarc.talkleeai.com -Type TXT
Set-DkimSigningConfig -Identity talkleeai.com -Enabled $true
Get-DkimSigningConfig -Identity talkleeai.com |
    Format-List Domain,Enabled,Status,Selector1CNAME,Selector2CNAME
```

The owner sends one intentional signup, verification, reset, support, billing
and security notification to a controlled mailbox. Retain sanitized recipient
Authentication-Results showing SPF/DKIM/DMARC pass, an aligned `talkleeai.com`
header From, correct visible brand, working links and delivery location. Do not
retain codes, verification tokens, passwords or message bodies in shared QA
evidence. A failed or timed-out send is not automatically retried; billing
outcomes remain unknown pending reconciliation when acceptance was unconfirmed.

## Rollout and rollback

Record current non-secret sender/brand settings and existing DNS record values
before the owner applies changes. Apply the SPF addition and Microsoft DKIM
configuration before routing production notifications through Microsoft 365.
Configure the sender, restart services using the deployment runbook, then
perform the controlled verification above. Keep this operational gate open
until that evidence exists.

For rollback, restore the previous application release and its matching
non-secret settings using the deployment runbook. Do not remove Microsoft's
SPF/DKIM authorization while any released or queued account message still uses
that sender; mail may be delayed. Restore DNS only after the owner confirms no
active sender depends on the records. Never replay a billing notification with
an unknown acceptance outcome merely because the release was rolled back.
Existing CRM activities keep their legacy identity marker readable. Existing
Drive folders are not renamed or deleted; new folder lookup uses the configured
brand, so the owner may see both historical and newly branded roots.
