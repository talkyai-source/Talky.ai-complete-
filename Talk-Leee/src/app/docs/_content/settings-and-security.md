## Security

The Security area covers your own account's protection, separate from the administrative Security Center.

Where enabled, it can include:

- Recovery codes for your account
- Current and recent sign-in sessions
- Trusted devices

### Recovery codes

1. Select **Security** from the sidebar.
2. Generate recovery codes if you have not already.
3. Store them somewhere safe and private — a password manager, not a note on your desk.
4. Each code can be used once, to sign in if you lose access to your MFA device.
5. Generate a new set if you use them up or believe they have been seen by someone else.

Generating a new set invalidates the old one.

### Sessions and devices

Review where your account is signed in. End any session you do not recognise, then change your password and review your MFA settings.

## Settings

Settings contains account and telephony configuration.

### Profile

Use the Profile tab to review or update information such as:

- Name
- Business/organization name
- Account/tenant information

Save changes after editing.

### Account Security

Where enabled, Security settings can include:

- MFA setup
- MFA removal
- Recovery information
- Passkeys
- Trusted devices/session controls

Use MFA for administrator accounts wherever possible.

#### Set up MFA

1. Open **Settings → Account Security**.
2. Start the MFA setup.
3. Scan the code with your authenticator app, or enter the key manually.
4. Enter the six-digit code from the app to confirm.
5. Store the recovery codes somewhere safe. Each one can be used once if you lose access to the app.

#### Add a passkey

Where passkeys are available, follow the on-screen prompts to register your device. A passkey lets you sign in using your device's own unlock method instead of a password.

#### Remove MFA

Removing MFA weakens your account. Only do it when you are replacing the method, and re-enable it immediately afterwards.

### Telephony

The Telephony tab is used to configure supported calling providers and related settings.

1. Open **Settings → Telephony**.
2. Review the existing SIP lines and their live status.
3. Select **Connect your PBX** to add a connection, or **Edit** to change one.
4. Enter the details supplied by your provider.
5. Save, then use the test action to confirm the line works.

Do not change production telephony configuration without testing, because incorrect SIP/provider settings can interrupt live calling.

A campaign cannot place calls while its telephone route is not ready. If a campaign reports that the selected trunk is not ready, check its live status here.

#### Concurrent calls

Your plan sets how many calls can run at once. Campaigns queue beyond that limit rather than failing. The pricing page lists the concurrency allowance for each plan.

## Security Center and Developer Hub

These areas are available only to authorized administrative roles.

### Security Center may include

- **Audit logs** — a record of who did what and when
- **Voice security** — controls related to voice handling
- **Abuse detection** — monitoring for misuse

### Developer Hub may include

- **API keys** — create, review and revoke keys
- **Webhooks** — register endpoints to receive events
- **Rate limiting** — review and adjust request limits
- **Secrets** — manage stored credentials

### Administrative billing

Administrators may also see account-wide billing and per-tenant billing views.

### Administrative reviews

Administrators may see a separate reviews area covering conversations across the whole account, including prompt versions, rather than only their own campaigns.

These areas should normally be managed by administrators or technical teams rather than everyday campaign operators.

#### Working with API keys

1. Open **Developer Hub → API keys**.
2. Create a key and record it immediately — it is shown only once.
3. Revoke any key that is no longer needed or may have been exposed.

Never place an API key in a public document, a support message, or client-side code.

#### Working with webhooks

1. Open **Developer Hub → Webhooks**.
2. Register the endpoint that should receive events.
3. Select the events you want delivered.
4. Verify that your endpoint responds successfully.

Your endpoint should verify that each request genuinely came from Talk-Lee before acting on it.
