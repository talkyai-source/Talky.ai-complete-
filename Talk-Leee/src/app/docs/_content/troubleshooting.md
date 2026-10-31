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

Verify that the campaign captures the field and that the latest call-state/captured-facts logic is active. Review the transcript to determine whether the caller's information was understood correctly.

### A campaign will not start, or Resume is unavailable

Talk-Lee shows the reason beside the campaign controls. The most common cause is that the selected telephone trunk is not ready. Select **Check again**, then open **Settings → Telephony** and review the line's live status.

![The bulk-action panel showing a readiness warning, with Resume unavailable and a Check again link](/docs/outbound-campaigns-04-readiness-warning.webp)

### A campaign is active but no calls are going out

Check the calling hours and their time zone, that contacts remain in the list, that your concurrency limit is not already used by another campaign, and that minutes remain on the account.

### A connector is expired

Open **Connectors** and use **Reconnect**.

### A voice sounds wrong

Preview another voice or provider, save the change, and run a test call.

### Calls fail or audio quality is poor

Pause the campaign if customers are affected and ask your Talk-Lee administrator/technical team to review telephony and voice-service health before continuing.

### Minutes have run out

Buy a top-up bundle from **Billing**, or change plan. Calls are blocked once the allowance is exhausted.

### A plan change has not appeared

Returning from the payment provider does not activate the subscription on its own. Wait briefly and refresh the Billing page. Do not pay twice. Contact support if it does not appear.

### Ask AI will not connect

Check that your browser has microphone permission, that no other application is using the microphone, and that you are not on a restricted network. Reload the page and try again.

### A contact import failed

Download the template and compare your file against it. Talk-Lee lists the rows it could not import and why.

### A recording is missing

Check the retention period for your plan. Recordings older than that are removed. Check also that recording was enabled for the campaign at the time of the call.

### You were signed out unexpectedly

Sign in again. Sessions end after inactivity and when permissions change. Save long forms as you go.

### You cannot find a page you used before

Confirm your role still has access, and that the page has not moved. Use the sidebar rather than a saved link.

## Getting Help

Select the **Help** button at the bottom right of any page. The support assistant answers first, from the help articles and read-only checks of your own account, and hands over to a person when it cannot solve the problem, when you ask for one with **Talk to a person**, or when the topic always needs a person (billing disputes, data deletion, account ownership, legal matters, security reports). The hand-over creates a support ticket with the conversation already filled in; the ticket number appears once it is saved.

Follow up under **Support** in the sidebar: read replies, add messages and files, mark a ticket resolved or reopen it within seven days, and rate the help you received. The team also replies by email. [Getting Support](/docs/support) describes the whole flow.

If the Help button and the Support entry are not shown, support is not switched on for your account yet. Use the contact details you already have, or the contact form on the website.

Before contacting support, have the following ready:

- What you were trying to do
- Which page or campaign it happened on
- The time it happened
- The phone number or call reference, for a call problem
- Any message shown on screen, copied exactly

Do not include passwords, API keys or recordings in a support message unless you are asked to.
