## Outbound Campaigns

Campaigns control how Talk-Lee makes outbound calls, what the AI agent is trying to achieve, which voice it uses, and what actions it is allowed to perform.

### Open Campaigns

1. Select **Campaigns** from the sidebar.
2. The Campaigns page lists your available campaigns.
3. Use the available sorting/filtering controls to find a campaign.
4. Depending on campaign status, you can open, pause, resume, edit, or delete a campaign.

![The campaigns list, showing the filters above a table of three campaigns](/docs/outbound-campaigns-01-list.webp)

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

![The campaign table, with columns for status, completion, success rate, leads, completed and failed](/docs/outbound-campaigns-02-columns.webp)

Select a column heading to sort by it.

### Campaign statuses

- **Running** — the campaign is calling, or ready to call within its schedule
- **Paused** — calling is stopped and can be resumed
- **Completed** — the contact list has been worked through
- **Draft** — the campaign is not finished and cannot call yet
- **Stopped** — calling has ended; the campaign can be started again
- **Preparing** — the campaign is getting ready to call after **Start** or **Resume**
- **Stopping** — a stop request is in progress

Statuses available to you depend on your account configuration.

### Filter and search campaigns

The campaign list includes filters above the table:

- **Campaign name** — type to search
- **Status** — limit the list to a status such as running or paused
- **Success rate** — enter a minimum and maximum percentage

Use **Export** to download the filtered list, and **Create campaign** to start a new one.

### Move through a long list

Use the controls above the table to choose how many rows to show at once and to move between pages with **First**, **Prev**, **Next** and **Last**. The counter shows which rows you are viewing out of the total.

### Act on several campaigns at once

1. Tick the checkbox beside each campaign you want to act on, or the header checkbox to select them all.
2. A panel appears above the table showing how many are selected.

   ![The bulk-action panel with Pause, Resume, Export and Delete, shown after selecting a campaign](/docs/outbound-campaigns-03-bulk-actions.webp)
3. Choose an action:
   - **Pause Selected** — stop calling on those campaigns
   - **Resume Selected** — opens the **Start campaign** window for the selected campaigns; any readiness warning is shown there
   - **Export Selected** — open the export and reporting options
   - **Delete Selected** — remove the campaigns after confirming

### Create a New Campaign

1. Open **Campaigns**.
2. Select **Create campaign** or the create-campaign action.
3. Complete the campaign setup sections shown in the wizard.

![The top of the campaign form, showing the name, brand, persona options and representative name](/docs/outbound-campaigns-05-identity.webp)

The pricing page lists the number of campaigns for each plan.

### Campaign Name

Enter a clear name that helps your team identify the campaign later.

Examples:

- Website Growth Audit
- Solar Lead Qualification
- Dojo Back-Book Upsell
- Appointment Confirmation

Avoid generic names such as "Campaign 1".

### Brand / company name

Enter the business the agent represents. The field sits beside the campaign name, and the campaign brief takes its brand from here.

### Persona and Representative names

Choose the **Persona** that matches the campaign. There are three:

- **Lead Generation** — for outgoing calls: qualify leads and capture their details
- **Customer Support** — for incoming calls: resolve issues and note requests for your team
- **AI Receptionist** — for incoming calls: answer and take messages

The form's persona descriptions mention booking, routing and escalation, but a caller is put through to a person only when a transfer is set up for the campaign (outgoing or incoming). The agent does not book appointments or meetings into a calendar. **What you book** is the next step the agent proposes; your team arranges it. On an outgoing campaign it can schedule a callback; on an incoming call it only notes a callback request.

Then enter the **Representative names**: one to three names, comma-separated. The agent uses one of them on each call. Each name has a **Male / Female** selector so that the name matches the gender of the voice you choose.

### Voice engine

Choose the engine from the **Voice engine** dropdown. The current option reads **Traditional · separate speech and language providers**, which uses the speech-to-text, language model and voice settings described in this guide.

### Opening objective

The **Campaign brief** is the live prompt layer: structured instructions used by the live agent. Brand and representative come from the identity fields above; everything here is saved and appears in prompt preview.

**Opening objective** is required and limited to 500 characters. Describe the first useful outcome, not a full script.

![The campaign brief, with fields for the opening objective and the decision-maker role](/docs/outbound-campaigns-06-campaign-brief.webp)

A good opening objective is short and measurable.

Example:

> Confirm whether the person handles website or marketing decisions and understand whether the current website generates enquiries.

Do not place the entire sales script in this field.

### Decision-maker role

Enter the role the agent should try to reach, such as Head of Operations. Leave blank when any informed contact can progress the call.

### Approved Next Actions

Talk-Lee allows campaign creators to define which actions the AI agent may attempt.

Available actions can include:

- **Schedule callback** — the AI agent calls the person back at the agreed time; scheduled callbacks exist on outgoing campaigns only
- **Send follow-up email** — needs Gmail connected under **Connectors** and the **Subject** and **Message** filled in; the email is sent only after the caller confirms the address and the content
- **Submit configured form** — emails the form you configured to the destination address; needs Gmail connected under **Connectors**
- **Transfer to a human** — on an outgoing campaign, a transfer works only when the campaign lists transfer among its approved actions and names the destination numbers; the system knows that a transfer was requested, and it does not guarantee that someone answers
- **End call politely** — close when the objective is complete or the caller declines

![The approved next actions, with transfer destination and objection-attempt settings beneath](/docs/outbound-campaigns-07-approved-actions.webp)

#### Important

Selecting an action gives the agent permission to attempt that action. The action must still be set up (for example Gmail connected for the email) and return a successful result.

### Transfer Destination

On an outgoing campaign, a transfer works only when the campaign lists transfer among its approved actions and names the destination numbers:

1. Enable **Transfer to a human**.
2. Enter or select the approved destination.
3. Save the campaign.
4. On an outgoing campaign, a transfer set up for the campaign cannot be tested in the browser test, because nothing is dialled; check its destination number carefully before using the campaign with real customers.

Without that set-up, an outgoing campaign cannot transfer a call; use a scheduled callback instead (outgoing campaigns only).

### Maximum Objection Attempts

Use this setting to control how many times the agent may attempt to handle a genuine objection before ending the sales conversation.

For normal outbound campaigns, **2 attempts** is a useful starting point.

The agent should distinguish between a soft objection such as "maybe later" and a clear rejection such as "I am not interested."

### Required Lead Fields

The **Required lead fields** line in the campaign brief is a summary. The fields themselves are chosen in **Contact details the agent may capture**, further down the form.

Choose only information this campaign needs. "Required" means a missing answer remains visibly incomplete; the agent must never invent it.

The ten fields are:

- Phone number
- Mobile
- Business number
- First name
- Last name
- Email
- Company
- Job title
- Best time to call
- Calling notes

![The contact-details picker, listing the fields the agent may capture](/docs/outbound-campaigns-11-contact-details.webp)

Only mark fields as required when they are genuinely necessary for the campaign.

### Additional Campaign Guidance

Use **Additional campaign guidance** for campaign-specific behaviour that is not already represented by structured settings or the knowledge base.

Good guidance includes:

- How persuasive or consultative the agent should be
- What counts as a true rejection
- When to stop selling
- When to ask for an email
- Tone and conversation style
- Special compliance rules

Avoid duplicating large amounts of company information here. Business facts belong in the Knowledge Base.

Guidance shares a 12,000 character limit with the structured brief. The counter includes the structured brief and this freeform layer. Nothing is trimmed automatically.

![The additional guidance field, with its character counter](/docs/outbound-campaigns-08-guidance.webp)

### Calling Hours

Set the **Timezone**, the **Start** and **End** times, and the days the campaign may call using the **Mon** to **Sun** toggles. Calls are not placed outside that window.

Tick **Call anytime (ignore these hours)** to allow calls outside the window above. Calling outside these hours depends on platform settings and is not guaranteed.

![The calling hours settings, with timezone, start and end times, day selection and a call-anytime option](/docs/outbound-campaigns-10-calling-hours.webp)

Check the rules that apply in the markets you call into. Many have legally defined calling hours.

### Select a Voice Provider and Voice

The campaign voice is selected per campaign.

1. Go to the **Voice** section.
2. Select a provider.
3. Review the available voices for that provider.
4. Use the play/preview button to hear a sample.
5. Select the voice that best fits the campaign persona.

![The voice selection, showing the provider list and the available voices](/docs/outbound-campaigns-09-voice.webp)

Talk-Lee can expose multiple voice providers depending on your account configuration, including providers such as Google, Cartesia, Deepgram, or ElevenLabs. Google, Cartesia and Deepgram voices are included in the standard subscription. ElevenLabs is a premium option charged separately.

Choose a voice based on the campaign audience. A professional sales campaign should normally use a clear, natural voice rather than an overly theatrical persona.

### Edit an existing campaign

1. Open the campaign.
2. Select the edit action.
3. Change the sections you need.
4. Save.

Changes normally take effect on the next call rather than on calls already in progress. Pause the campaign first if a change must not apply mid-run.

### Duplicate

1. Open the campaign.
2. Select **Duplicate**.

![The campaign page header with the Duplicate button](/docs/outbound-campaigns-13-duplicate.webp)

The copy is a draft with the same settings, knowledge files and lead fields. Contacts, calls and results are not copied.

### Export campaign data

Use **Export** on the campaign list, or **Export Selected** after selecting campaigns, to open the export and reporting options. Choose the data and format you need, then download the file.

![Export & Reporting](/docs/outbound-campaigns-15-export.webp)

PDF opens a print view; save as PDF from your browser.

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

### What should not be placed only in the Knowledge Base

The approved next actions and the required fields should remain in the structured campaign configuration.

### Recommended structure

For larger campaigns, separate information logically:

- Company overview
- Services/products
- Pricing
- FAQs
- Objections
- Qualification
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
2. Open the campaign and use the available **Test agent** or preview function.

   ![The test dialog, offering a choice of who opens the conversation and a headphones option](/docs/outbound-campaigns-12-test-dialog.webp)
3. Test the opening.
4. Interrupt the agent while it is speaking.
5. Give a soft objection.
6. Give a clear rejection.
7. Ask a knowledge-base question.
8. Give an email address and check that it is captured correctly.
9. Test a scheduled callback, if the campaign has that action (outgoing campaigns only).
10. On an outgoing campaign, a transfer set up for the campaign cannot be tested in the browser test, because nothing is dialled; check its destination number carefully. A test does not guarantee that someone will answer a live transfer.
11. Check the resulting call summary/transcript.

### Choose who speaks first

When you start a test, the **Test this campaign's agent** dialog asks who opens the conversation. This choice applies to the test only. For live calls, the choice is made in the **Start campaign** window.

- **AI agent speaks first** — The agent greets you the moment the session connects. Same as an agent-first outbound call.
- **You say hello first** — The agent waits for your "hello", then introduces itself and why it is calling. Still an outbound call — it will not answer like a receptionist.
- **I'm wearing headphones — allow interruption** — Lets you talk over the agent, like on a real phone line. On speakers the microphone hears the agent's own voice and it will interrupt itself — leave this off, and don't judge interruption quality from a speaker test.

Your microphone is used only for the test. Nothing is dialled and no plan minutes are used.

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

### Start campaign

1. Open the campaign.
2. Select **Start**.

![Start campaign](/docs/outbound-campaigns-14-start-campaign.webp)

**Start campaign** — "Pick who opens the conversation when the callee answers. The call pipeline pre-warms either way — this only controls whether the agent speaks an opening line or waits for the callee." — offers two options: **AI agent speaks first** and **Callee speaks first**.

- **AI agent speaks first** — Agent plays a pre-synthesized greeting the instant the callee picks up. Best for cold outreach.
- **Callee speaks first** — Agent waits for the callee to say “hello” before responding. Feels more natural on warm lists.

When you start a single campaign, the window also offers:

- **Calls at a time (batch size)** — how many calls dial at once; a new call starts only when an earlier one finishes
- **Wait between calls** — the minimum gap before the next call is dialed; set it to 0 for no wait

While a campaign is running, select **Pause** to stop calling temporarily, then **Resume** to continue; **Resume** opens the same window. Select **Stop** to end calling; a stopped campaign can be started again.
