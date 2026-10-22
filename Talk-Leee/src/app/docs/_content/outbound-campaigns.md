## Outbound Campaigns

Campaigns control how Talk-Lee makes outbound calls, what the AI agent is trying to achieve, which voice it uses, and what actions it is allowed to perform.

### Open Campaigns

1. Select **Campaigns** from the sidebar.
2. The Campaign Performance page lists your available campaigns.
3. Use the available sorting/filtering controls to find a campaign.
4. Depending on campaign status, you can open, pause, resume, edit, or delete a campaign.

### Reading the campaign table

| Column | What it means |
| --- | --- |
| **Campaign** | The campaign name. Select the arrow beside it to expand the row for more detail. |
| **Status** | Whether the campaign is currently calling. See below. |
| **Completion** | How much of the contact list has been worked through. |
| **Success Rate** | The share of completed calls that met the campaign's objective. |
| **Leads** | How many leads the campaign has produced. |
| **Completed** | How many calls finished normally. |
| **Failed** | How many calls did not connect or ended in error. |

Select a column heading to sort by it.

### Campaign statuses

- **Active** — the campaign is calling, or ready to call within its schedule
- **Paused** — calling is stopped and can be resumed
- **Completed** — the contact list has been worked through
- **Draft** — the campaign is not finished and cannot call yet

Statuses available to you depend on your account configuration.

### Filter and search campaigns

The campaign list includes filters above the table:

- **Campaign name** — type to search
- **Status** — limit the list to a status such as active or paused
- **Success rate** — enter a minimum and maximum percentage

Use **Export** to download the filtered list, and **New Campaign** to start a new one.

### Move through a long list

Use the controls above the table to choose how many rows to show at once and to move between pages with **First**, **Prev**, **Next** and **Last**. The counter shows which rows you are viewing out of the total.

### Act on several campaigns at once

1. Tick the checkbox beside each campaign you want to act on, or the header checkbox to select them all.
2. A panel appears above the table showing how many are selected.
3. Choose an action:
   - **Pause Selected** — stop calling on those campaigns
   - **Resume Selected** — restart calling, where the campaigns are ready
   - **Export Selected** — open the export and reporting options
   - **Delete Selected** — remove the campaigns after confirming

**Resume Selected** is unavailable when the selected campaigns cannot currently place calls. When that happens, Talk-Lee explains why beside the buttons — for example that the selected telephone trunk is not ready. Select **Check again** to re-test, or open **Settings → Telephony** to review the trunk.

### Create a New Campaign

1. Open **Campaigns**.
2. Select **New Campaign** or the create-campaign action.
3. Complete the campaign setup sections shown in the wizard.

Your plan limits how many campaigns you can run. The pricing page lists the limit for each plan.

### Campaign Name

Enter a clear name that helps your team identify the campaign later.

Examples:

- Website Growth Audit
- Solar Lead Qualification
- Dojo Back-Book Upsell
- Appointment Confirmation

Avoid generic names such as "Campaign 1".

### Agent Name and Role

Choose the name used by the AI agent during the call and define the agent's role.

Examples:

- **Alex — Business Development Representative**
- **Sarah — Website Growth Consultant**
- **Sophie — Virtual Receptionist**

The role should describe what the agent is expected to do, not a long script.

### Define the First Useful Outcome

Describe what the agent should achieve early in the conversation.

A good first outcome is short and measurable.

Example:

> Confirm whether the person handles website or marketing decisions and understand whether the current website generates enquiries.

Do not place the entire sales script in this field.

### Approved Next Actions

Talk-Lee allows campaign creators to define which actions the AI agent may attempt.

Available actions can include:

- **Schedule callback** — offer an agreed follow-up time
- **Send follow-up email** — send information only after the caller confirms the address/content
- **Submit configured form** — use an available form integration
- **Transfer to a human** — transfer only when the transfer runtime and destination are available
- **End call politely** — close when the objective is complete or the caller declines

#### Important

Selecting an action gives the agent permission to attempt that action. The corresponding tool/integration must still be configured and return a successful result.

### Transfer Destination

If human transfer is enabled:

1. Enable **Transfer to a human**.
2. Enter or select the approved destination.
3. Save the campaign.
4. Test the transfer before using the campaign with real customers.

If transfer is not configured, use callback scheduling instead.

### Maximum Objection Attempts

Use this setting to control how many times the agent may attempt to handle a genuine objection before ending the sales conversation.

For normal outbound campaigns, **2 attempts** is a useful starting point.

The agent should distinguish between a soft objection such as "maybe later" and a clear rejection such as "I am not interested."

### Required Lead Fields

Use the required-fields section to define which contact details or lead information should be captured.

Typical fields include:

- Contact name
- Business name
- Phone number
- Email address
- Preferred callback time

Only mark fields as required when they are genuinely necessary for the campaign.

### Additional Campaign Guidance

Use **Additional campaign guidance** for campaign-specific behaviour that is not already represented by structured settings or the knowledge base.

Good guidance includes:

- How persuasive or consultative the agent should be
- What counts as a true rejection
- When to stop selling
- When to ask for an email
- Tone and conversation style
- Special compliance or handoff rules

Avoid duplicating large amounts of company information here. Business facts belong in the Knowledge Base.

### Calling Hours

Where the setting is available, define the hours and days during which the campaign may call, and the time zone those hours apply in. Calls are not placed outside that window.

Check the rules that apply in the markets you call into. Many have legally defined calling hours.

### Who Speaks First

Where the setting is available, choose whether the AI agent speaks first when the call connects, or waits for the person to speak. Agent-first suits outbound sales calls. Caller-first can suit confirmation or service calls.

### Select a Voice Provider and Voice

The campaign voice is selected per campaign.

1. Go to the **Voice** section.
2. Select a provider.
3. Review the available voices for that provider.
4. Use the play/preview button to hear a sample.
5. Select the voice that best fits the campaign persona.

Talk-Lee can expose multiple voice providers depending on your account configuration, including providers such as Google, Cartesia, Deepgram, or ElevenLabs. Google, Cartesia and Deepgram voices are included in the standard subscription. ElevenLabs is a premium option charged separately.

Choose a voice based on the campaign audience. A professional sales campaign should normally use a clear, natural voice rather than an overly theatrical persona.

### Edit an existing campaign

1. Open the campaign.
2. Select the edit action.
3. Change the sections you need.
4. Save.

Changes normally take effect on the next call rather than on calls already in progress. Pause the campaign first if a change must not apply mid-run.

### Export campaign data

Use **Export** on the campaign list, or **Export Selected** after selecting campaigns, to open the export and reporting options. Choose the data and format you need, then download the file.

Treat exported files as confidential. They can contain customer contact details.

## Campaign Knowledge Base

The Knowledge Base gives the AI agent approved business information to use during a call.

### What belongs in the Knowledge Base

Examples:

- Company information
- Products and services
- Pricing rules
- FAQs
- Qualification criteria
- Objection handling
- Policies
- Supported locations
- Approved claims
- Escalation instructions

### What should not be placed only in the Knowledge Base

Campaign permissions such as transfer, callback, email, and required fields should remain in the structured campaign configuration.

### Recommended structure

For larger campaigns, separate information logically:

- Company overview
- Services/products
- Pricing
- FAQs
- Objections
- Qualification
- Escalation
- Compliance

### Knowledge Base best practices

- Keep facts accurate and current.
- Do not include claims the agent is not allowed to make.
- Avoid conflicting versions of pricing or policies.
- Use simple headings and short sections.
- Test common customer questions after updating the knowledge base.

## Test the Agent Before Launch

Never launch a new campaign without testing it first.

### Recommended test process

1. Save the campaign.
2. Open the campaign and use the available **Test Agent** or preview function.
3. Test the opening.
4. Interrupt the agent while it is speaking.
5. Give a soft objection.
6. Give a clear rejection.
7. Ask a knowledge-base question.
8. Give an email address and check that it is captured correctly.
9. Test callback scheduling if enabled.
10. Test human transfer if enabled.
11. Check the resulting call summary/transcript.

### Launch criteria

The campaign should not go live until:

- the agent can hear the caller reliably
- interruption/barge-in works
- the agent does not repeat captured information
- approved actions work
- the knowledge base returns correct information
- the agent exits politely after a clear rejection

### Readiness check

Before a campaign can place calls, Talk-Lee checks that a telephone route is available. If the campaign shows a readiness warning, resolve it before launching. Select **Check again** after making a change in **Settings → Telephony**.
