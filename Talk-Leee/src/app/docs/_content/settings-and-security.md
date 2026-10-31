## Security

The Security area covers your account protection, active sessions, and — for admins — tenant-wide keys and audit activity.

![Account security, with password change and passkey options](/docs/security-01-password-passkeys.webp)

### Password and account

Change your password here, or sign in without one using a passkey. Changing the password signs out all other sessions, each within 15 minutes.

### Passkeys

Add a passkey from this section. A passkey lets you sign in with your device's own unlock instead of a password.

### Two-factor authentication

A second step at sign-in, from an authenticator app. Enable it from this page.

### Sessions and devices

**Active sessions** lists every device the account is signed in on, with its last-active time, under **Active Devices**. Revoking an entry does not sign that device out; changing your password does.

![Active sessions, listing each signed-in device](/docs/security-03-sessions.webp)

### Tenant-wide security

Administrators also see **Tenant-wide security**: **Audit activity** (who changed what, and when), **Audit and access** (security events and access review), **API keys and tokens** and **Voice security**. The last two currently state that they are not available here.

### Data retention and recordings

Confirm your account's recording and transcript retention policy with your administrator. Open **Recordings** to review stored audio.

### IP allow-list

Not available. No IP allow-list is enforced anywhere in the platform today; it is listed so that its absence is explicit rather than assumed.

## Settings

Settings contains account and telephony configuration.

### Profile

Use the Profile tab to review or update information such as:

- Name
- Business/organization name
- Account/tenant information

![The profile settings, showing name and business details](/docs/settings-01-profile.webp)

Save changes after editing.

### Account Security

Account security controls live on the **Security** page, opened from the sidebar. The **Security** tab under **Settings → Account & Security** only shows a **Go to Security** link. The Security page offers:

- Two-factor authentication setup and removal
- Recovery codes, shown once two-factor authentication is on
- Passkeys
- Active sessions, with sign-out of other devices

Use MFA for administrator accounts wherever possible.

#### Set up MFA

1. Select **Security** from the sidebar.
2. Under **Two-factor authentication**, select **Enable two-factor authentication**.
3. On the **QR Code** step, scan the code with your authenticator app, or enter the key shown beneath it manually.
4. On the **Verify** step, enter the six-digit code from the app to confirm.
5. On the **Recovery** step, select **Download** to save your recovery codes, then **Complete Setup**. Once two-factor authentication is on, the panel shows **Recovery codes remaining** and a **Regenerate recovery codes** button.

![The two-factor authentication panel, showing its current state](/docs/security-02-two-factor.webp)

#### Add a passkey

On the **Security** page, under **Password and account**, select **Add a passkey** and follow the on-screen prompts to register your device. A passkey lets you sign in using your device's own unlock method instead of a password.

#### Remove MFA

Removing MFA weakens your account. Only do it when you are replacing the method, and re-enable it immediately afterwards. On the **Security** page, under **Two-factor authentication**, select **Turn off**, confirm your password, then select **Turn off two-factor authentication**.

### Telephony

The Telephony tab is used to configure supported calling providers and related settings.

1. Open **Settings → Telephony**.
2. Review the existing SIP lines and their live status.

   ![The telephony settings, listing configured SIP lines and their status](/docs/settings-02-telephony.webp)
3. Select **Connect PBX** to add a connection, or **Edit** to change one.

   ![The dialog for connecting a PBX, with fields for the provider details](/docs/settings-03-pbx-dialog.webp)
4. Enter the details supplied by your provider.
5. Save, then use the test action to confirm the line works.

Do not change production telephony configuration without testing, because incorrect SIP/provider settings can interrupt live calling.

A campaign cannot place calls while its telephone route is not ready. If a campaign reports that the selected trunk is not ready, check its live status here.

#### Concurrent calls

Your plan sets how many calls can run at once. Campaigns queue beyond that limit rather than failing. The pricing page lists the concurrency allowance for each plan.

## Security Center and Developer Hub

These areas are available only to authorized administrative roles.

![Tenant-wide security, listing audit activity, access review, API keys and voice security](/docs/security-04-tenant-wide.webp)

### Security Center may include

- **Audit logs** — a record of who did what and when
- **Voice security** — listed, but not available in the application today
- **Abuse detection** — monitoring for misuse

### Developer Hub may include

- **API keys** — listed, but not available in the application today
- **Webhooks** — register endpoints to receive events
- **Rate limiting** — review and adjust request limits
- **Secrets** — manage stored credentials

### Administrative billing

Administrators may also see account-wide billing and per-tenant billing views.

### Administrative reviews

Administrators may see a separate reviews area covering conversations across the whole account, including prompt versions, rather than only their own campaigns.

These areas should normally be managed by administrators or technical teams rather than everyday campaign operators.

#### API keys and voice security

API keys and voice security are listed in the navigation but are not available in the application today. No key management API is served.

#### Working with webhooks

Webhooks are available where your account provides them.

1. Open **Developer Hub → Webhooks**.
2. Register the endpoint that should receive events.
3. Select the events you want delivered.
4. Verify that your endpoint responds successfully.

Your endpoint should verify that each request genuinely came from Talk-Lee before acting on it.
