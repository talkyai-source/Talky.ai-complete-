Inbound allows Talk-Lee to answer incoming calls with an AI agent when inbound calling is enabled for your account.

![The inbound campaigns list](/docs/inbound-01-list.webp)

## Typical setup flow

1. Open **Inbound Campaigns**.
2. Create or select an inbound campaign.
3. Choose the AI role, such as receptionist or customer support.
4. Configure the greeting and objective.
5. Select the voice.
6. Add the relevant knowledge base.
7. Configure the approved actions: on an incoming call the agent can take a message, note a callback request for your team or send a follow-up email, and it can transfer the call when it is set up.
8. Connect the required telephone number or SIP line.
9. Run test calls before real callers use the number.

![An inbound campaign's overview, showing its number, name, AI campaign, SIP trunk, opening mode and timezone, with the server readiness checks and the after-hours and recording policy](/docs/inbound-02-setup.webp)

## Managing inbound campaigns

The inbound list works like the outbound campaign list. You can open an inbound campaign to review it, edit its configuration, or create a new one.

## What the agent does with callbacks, bookings and transfers

On an incoming call the agent does not schedule a callback and does not book appointments or meetings into a calendar. It only notes a callback request for your team. Scheduled callbacks, where the AI agent calls the person back at the agreed time, exist on outgoing campaigns only.

On an incoming campaign, a transfer works only when the transfer option is on (**Transfer to a person**), the destination is on the approved list (**Approved transfer destinations**), the phone line carries calls both ways, and **Transfer to a human** is also ticked in the campaign's agent settings with the same number. When the account has no transfer available, an incoming campaign's option reads **Transfer to a person is unavailable**. The system knows that a transfer was requested; it does not guarantee that someone answers.

## Who speaks first

Each inbound campaign has an opening mode.

- **Agent speaks first**: the agent greets the caller as soon as the call is answered, with the greeting you saved.
- **Caller speaks first**: the agent waits for the caller. If the caller says nothing for about two seconds, the agent greets them, so a caller never sits in silence. It uses your saved greeting. Without one it says: "Hello, thank you for calling", your company name, then "This is", the agent's name, "an AI assistant. How can I help you?"

On a recorded call the recording notice comes first, and the two seconds are counted after it.

The first thing a caller hears always says that the agent is an AI. If the greeting you saved does not say so, the agent adds a short sentence to it, such as "This is Sarah, an AI assistant." A greeting that already says it is left exactly as you wrote it. When the caller speaks first, the agent first says who it is and that it is an AI assistant, then answers.

## Recording incoming calls

Each inbound campaign has its own **Record inbound calls** switch and a recording notice.

- The agent says the notice first on every call. The recording starts after the notice.
- If the service cannot confirm that the notice played in full, the recording is still kept and the call is marked "notice not confirmed".
- Calls are not recorded if your account's recording policy is **Do not record calls**.
- The notice cannot offer callers a way to opt out, such as pressing a key. The service cannot do that during a call.
- A new inbound campaign starts with recording switched on. You can switch it off before you activate the campaign.

You are responsible for making sure your use of call recording follows the law where your callers are.

## Recommended inbound testing

Test:

- greeting
- caller interruption
- silence
- unclear speech
- frequently asked questions
- a callback request, which the agent notes for your team
- an incoming-call transfer, if one is set up for the campaign
- emergency language where applicable

## Rejected inbound calls

Where available, review the rejected-calls panel to understand why incoming calls were not answered — for example an unrecognised number or a phone line that was not ready.
