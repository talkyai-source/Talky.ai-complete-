## Troubleshooting Quick Guide

### The agent is not behaving as expected

Check:

- Campaign guidance
- Knowledge-base content
- Approved actions
- Required fields
- AI Options
- Recent call transcript

### The agent asks for information twice

Check that the campaign is set to capture the field. Review the transcript to see whether the caller's information was understood correctly.

### A campaign will not start, or Resume is unavailable

Talk-Lee shows the reason beside the campaign controls. The most common cause is that the selected telephone trunk is not ready. Select **Check again**, then open **Settings → Telephony** and review the line's live status.

![A campaign page showing a telephone readiness warning beside the campaign controls](/docs/outbound-campaigns-04-readiness-warning.webp)

### A campaign is running but no calls are going out

Check the calling hours and their time zone, that contacts remain in the list, that your concurrency limit is not already used by another campaign, and that minutes remain on the account.

### A connector is expired

Open **Connectors** and use **Reconnect**.

### A voice sounds wrong

Preview another voice or provider, save the change, and run a test call.

### Calls fail or audio quality is poor

Pause the campaign if customers are affected. Check the line's status under **Settings → Telephony**, run a test call, and contact support (see Getting Help) before you continue.

### Minutes have run out

Buy a top-up bundle from **Billing**, or change plan. Calls are blocked once the allowance is exhausted.

### A plan change has not appeared

Returning from the payment provider does not activate the subscription on its own. Wait briefly and refresh the Billing page. Do not pay twice. Contact support if it does not appear.

### Ask AI will not connect

Check that your browser has microphone permission, that no other application is using the microphone, and that you are not on a restricted network. Reload the page and try again.

### A contact import failed

Select **Download a sample file** and compare your file against it. Talk-Lee lists the rows it could not import and why.

### A recording is missing

Open **Settings → Recording**. A saved **Do not record calls** means no call is recorded; with no saved policy, calls are recorded with a spoken notice. For an incoming call, check that the inbound campaign's **Record inbound calls** switch was on. Recordings are not deleted automatically in this release.

### You were signed out unexpectedly

Sign in again. Sessions end after inactivity and when permissions change. Save long forms as you go.

### You cannot find a page you used before

Confirm your role still has access, and that the page has not moved. Use the sidebar rather than a saved link.

## Getting Help

Use the contact details supplied by your account owner, the contact form on the website, or support@talkleeai.com. The in-app support assistant and ticket desk are optional deployment features and are not promised by this guide. If your account shows a working Help control, follow its on-screen instructions; otherwise use the contact fallback. Do not assume a ticket was created until you receive confirmation.

Before contacting support, have the following ready:

- What you were trying to do
- Which page or campaign it happened on
- The time it happened
- The phone number or call reference, for a call problem
- Any message shown on screen, copied exactly

Do not include passwords, API keys or recordings in a support message unless you are asked to.
