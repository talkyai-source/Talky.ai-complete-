## Security

The Security area covers your account protection, passkeys and active sessions.

![Account security, with password change and passkey options](/docs/security-01-password-passkeys.webp)

### Password and account

Change your password here, or sign in without one using a passkey. Changing your password signs out your other sessions when they next refresh or make a request.

### Passkeys

Add a passkey from this section. A passkey lets you sign in with your device's own unlock instead of a password.

### Two-factor authentication

A second step at sign-in, from an authenticator app. Enable it from this page.

### Sessions and devices

**Active sessions** lists every device the account is signed in on, with its last-active time, under **Active Devices**. Revoking an entry signs that device out: its next request or token refresh is refused, and it must sign in again.

![Active sessions, listing each signed-in device](/docs/security-03-sessions.webp)

### Tenant-wide security

**Tenant-wide security**, with its **Audit activity** and **Audit and access** cards, is not shown in a customer account.

### Data retention and recordings

Your recording policy is under **Settings → Recording**. Open **Recordings** to review stored audio.

### IP allow-list

Not available. No IP allow-list is enforced anywhere in the platform today; it is listed so that its absence is explicit rather than assumed.

## Settings

Settings has five tabs: Profile, Security, Devices, Telephony and Recording.

### Profile

Use the Profile tab to review or update information such as:

- Name
- Business/organization name
- Timezone — the zone used for dates on the Dashboard and in Analytics; it does not change campaign calling hours. Only an account administrator can change it.
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

### Devices

The **Devices** tab shows **Active Devices**: the devices your account is signed in on. It is the same list as **Active sessions** on the Security page. **Sign out from all other devices** ends every session except the one you are using.

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

#### Buy Now

Use **Buy Now** to request a phone number. A request is not instant: you upload the documents asked for, staff review each request, and staff contact you about the next steps. You can also connect your own SIP trunk under **Connect PBX**. Each destination has its own regulations, so the window lists the documents needed for business use or personal use and lets you upload them.

The last card on the Telephony tab is shown to every signed-in user. Account admins see it after the rest of the Telephony settings. Other users see it after the note that phone lines are managed by their account admin.

The card reads:

- Minute charges vary by destination. Local conditions apply.
- Info: Each destination has its own regulations, which may vary from region to region.

Telephone (PSTN / SIP) usage is not included in AI minutes. Its cost depends on the phone line you connect.

1. Select **Buy Now**. A window titled **Documentation is required:** opens.
2. The window has two cards, each listing its documents:
   - **1. Business Use**
     - A) Business registration
     - B) Photo ID matching the business registration
   - **2. Personal Use**
     - A) Utility bill (proof of address)
     - B) Photo ID matching the utility bill
3. Under each document is an upload box that reads **Click to upload or drag and drop**. Select the box to choose a file, or drag a file onto it. Each box takes one file. The file name is then shown, with **Choose another** to replace the file and **Remove** to empty the box.
4. Select **Submit** on the card you filled in. Each card has its own **Submit**, which works only when both of that card's boxes have a file.
5. The window closes and the message **Thank you. Your documents have been submitted.** is shown. Staff review the request and contact you about the next steps.

To close the window without submitting, select **Cancel**, press Escape or select outside the window. Files you chose are not kept when the window closes.

#### Concurrent calls

Your plan sets how many calls can run at once. Campaigns queue beyond that limit rather than failing. The pricing page lists the concurrency allowance for each plan.

### Recording

The **Recording** tab holds the **Call recording policy**. If no policy has been saved, calls are recorded and the agent speaks a recording notice first. The box at the top of the card shows which rule applies. Only an account administrator can change the policy; other users see it without the **Save policy** button.

- **Consent mode** — **Do not record calls**, **Record without a spoken notice (one-party consent)** or **Record with a spoken notice (two-party consent)**. One-party recording is lawful only where you and the people you call are in one-party jurisdictions, or you call your own numbers. Even in one-party mode, the agent still speaks the notice to callers in two-party countries, or when the caller's country is unknown. With a spoken notice, the agent speaks a recording notice as the first thing on the call.
- **Spoken notice (two-party)** and **Countries needing the notice** — the notice text, and the countries where it is spoken. An empty list means everywhere.
- **Opt-out key** has no effect in this release. Callers cannot opt out of recording by pressing a key, and an opt-out offer in your notice is left out when the notice is spoken.
- **Keep for (days)** — the retention period (90 days by default). In this release recordings are not deleted automatically.

Select **Save policy** to apply a change. You are responsible for making sure your use of call recording follows the law where your callers are.

## Administrative controls

Some screens depend on your role. **Security Center** and **Agent Quality** are not shown in a customer account.

Use the account Security and Settings controls described above for your own account. Changes that affect other users or the whole account belong with an authorised administrator.
