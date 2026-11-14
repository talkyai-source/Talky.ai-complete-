import type { LegalBlock } from "@/components/legal/legal-document";

export const privacyIntro: string[] = [
  `Talk-Lee Limited ("Talk-Lee", "we", "us", "our") operates the AI voice agent platform available at talkleeai.com. This Privacy Policy explains exactly what personal data we collect, how we use it, who we share it with, how we protect it, and your rights regarding it.`,
  `This policy applies to: business customers who register accounts ("Operators"), individuals whose calls are handled by Talk-Lee AI agents ("Callers"), visitors to talkleeai.com, and developers who use our API.`,
];

export const privacyBlocks: LegalBlock[] = [
  { type: "h2", text: "1. Who We Are" },
  {
    type: "p",
    text: `Talk-Lee Limited is the data controller for personal data processed through the Talk-Lee platform.`,
  },
  {
    type: "ul",
    items: [
      `Email: privacy@talkleeai.com`,
      `Support: support@talkleeai.com`,
      `Security: security@talkleeai.com`,
      `Website: https://talkleeai.com`,
    ],
  },
  {
    type: "p",
    text: `We will respond to all data protection requests within 30 days as required by applicable law.`,
  },

  { type: "h2", text: "2. Data We Collect and Store" },
  { type: "h3", text: "2.1 Account and Identity Data" },
  {
    type: "ul",
    items: [
      `Full name and email address — for account creation, login, and communications`,
      `Company name and business details — for account setup and billing`,
      `Role within organisation — for access control (user, admin, partner_admin)`,
    ],
  },
  { type: "h3", text: "2.2 Contact and Lead Data" },
  {
    type: "ul",
    items: [
      `Phone numbers — of contacts uploaded for outbound campaigns`,
      `First name, last name, email — of contacts in uploaded lists`,
      `Custom fields (JSONB) — any additional data operators choose to include in their contact lists. Talk-Lee processes this data as a data processor on behalf of the operator`,
      `Tags and priority scores — campaign management metadata`,
      `Call attempt history — dates, outcomes, and retry counts per contact`,
    ],
  },
  { type: "h3", text: "2.3 Call Data" },
  {
    type: "ul",
    items: [
      `Call recordings — audio files stored encrypted on our own servers (WAV format)`,
      `Full call transcripts — text stored in the transcripts table`,
      `Conversation history — complete message-by-message exchange stored in conversations.messages JSONB`,
      `Call summary — AI-generated summary of call outcome`,
      `Call metadata — phone number, duration, status, outcome, timestamps, cost per call`,
      `Goal achievement status — whether the AI achieved the campaign objective`,
      `Sentiment score — automated positive/neutral/negative classification`,
    ],
  },
  { type: "h3", text: "2.4 Billing and Financial Data" },
  {
    type: "ul",
    items: [
      `Stripe customer ID and subscription ID — stored in our database for billing reference`,
      `Subscription status, plan, billing period — account management`,
      `Invoice history — invoice IDs, amounts, dates, status`,
      `Per-call cost — monetary cost stored per call for usage billing`,
      `Payment method details — processed by Stripe only; we never store card numbers`,
    ],
  },
  { type: "h3", text: "2.5 Security and Audit Data" },
  {
    type: "ul",
    items: [
      `IP addresses — stored in audit logs (2-year retention) and rate limit event logs (30-day retention). Under GDPR, IP addresses are treated as personal data`,
      `Audit log entries — every significant action with actor, timestamp, outcome, and IP address`,
      `Rate limit events — IP address, endpoint, action taken, timestamp`,
      `Abuse detection events — fraud scoring, velocity anomaly details, severity, resolution notes`,
      `Call guard decisions — every pre-call security evaluation including phone number, decision, and checks performed`,
      `Session tokens — stored in Redis with 24-hour expiry`,
    ],
  },
  { type: "h3", text: "2.6 Authentication Data" },
  {
    type: "ul",
    items: [
      `Email and hashed password — bcrypt hash with work factor 12; plaintext never stored`,
      `Multi-Factor Authentication (MFA) tokens — TOTP secrets stored encrypted`,
      `Passkey credentials (WebAuthn/FIDO2) — credential public key, device type, authenticator AAGUID, sign count, transports, backup state. These are cryptographic keys tied to your physical device. No biometric data is transmitted to or stored by Talk-Lee`,
    ],
  },
  { type: "h3", text: "2.7 Integration and Connector Data" },
  {
    type: "ul",
    items: [
      `Google OAuth tokens — access and refresh tokens, encrypted using Fernet (AES-128-CBC + HMAC-SHA256), stored in database`,
      `HubSpot OAuth tokens — encrypted, stored for CRM sync`,
      `SIP trunk credentials — auth username and encrypted password per tenant for telephony configuration`,
      `Webhook HMAC secrets — stored for signature verification of outbound webhook deliveries`,
      `API keys issued to developers — hashed; only the prefix is stored in plaintext`,
    ],
  },
  { type: "h3", text: "2.8 Automatically Collected Data" },
  {
    type: "ul",
    items: [
      `Browser and device information — type, version, operating system`,
      `Usage data — pages visited, features used, session duration`,
      `Error logs — technical errors sent to Sentry (anonymised, 30-day retention)`,
      `API request logs — endpoint, timestamp, HTTP status (no request bodies logged)`,
    ],
  },
  { type: "h3", text: "2.9 Data We Do Not Collect" },
  {
    type: "ul",
    items: [
      `We do not collect Social Security numbers or national identity documents`,
      `We do not collect biometric data — passkey public keys are cryptographic, not biometric`,
      `We do not collect precise geolocation data. To show prices and amounts in your local currency, the website uses only the country your connection appears to come from, and does not store it`,
      `We do not read email body content (only metadata: sender, subject, date)`,
      `We do not access Google Drive files other than those created by Talk-Lee`,
      `We do not collect data from children under 13 (US/COPPA) or 16 (EU/UK GDPR)`,
    ],
  },

  { type: "h2", text: "3. How We Use Your Data" },
  { type: "h3", text: "3.1 To Provide the Service" },
  {
    type: "ul",
    items: [
      `Running AI voice calling campaigns — speech-to-text via Deepgram or AssemblyAI, language models via Groq, Google Gemini, OpenAI, Cerebras, or DeepSeek, and text-to-speech via Cartesia, ElevenLabs, Google, or Deepgram (see Section 4.1)`,
      `Routing calls through SIP telephony infrastructure (Asterisk, OpenSIPS, RTPEngine)`,
      `Booking appointments in Google Calendar during AI calls`,
      `Sending confirmation emails via Gmail (send only)`,
      `Sending SMS notifications via Vonage`,
      `Leaving voicemail messages when calls are unanswered`,
      `Syncing call outcomes to HubSpot CRM when connected`,
      `Storing call recordings and transcripts for operator review`,
      `Sending transactional emails via our email provider, Microsoft 365 (SMTP)`,
    ],
  },
  { type: "h3", text: "3.2 For Security and Fraud Prevention" },
  {
    type: "ul",
    items: [
      `Detecting velocity anomalies, toll fraud, IRSF, sequential dialing, and other fraud patterns`,
      `Enforcing per-tenant call rate limits and concurrency limits`,
      `Evaluating every outbound call through our CallGuard security system`,
      `Storing call guard decisions for audit and dispute resolution`,
      `Monitoring and blocking abuse events`,
      `IP-based rate limiting to prevent API abuse`,
    ],
  },
  { type: "h3", text: "3.3 For Compliance" },
  {
    type: "ul",
    items: [
      `Enforcing calling hours based on recipient's local time zone (TCPA, PECR, Australian DNC)`,
      `Maintaining and enforcing Do Not Call lists per tenant`,
      `Blocking calls to expired DNC entries`,
      `Storing audit logs for compliance and legal obligations`,
    ],
  },
  { type: "h3", text: "3.4 For Billing and Administration" },
  {
    type: "ul",
    items: [
      `Processing payments through Stripe`,
      `Tracking per-call costs for usage billing`,
      `Sending billing notifications and suspension emails`,
      `Quota management — tracking minutes used vs allocated`,
    ],
  },
  { type: "h3", text: "3.5 Legal Bases (GDPR / UK GDPR)" },
  {
    type: "ul",
    items: [
      `Contract — processing necessary to deliver the services you signed up for`,
      `Legitimate interests — fraud prevention, security monitoring, service improvement, audit logging`,
      `Legal obligation — tax records, compliance with applicable laws`,
      `Consent — Google, HubSpot, and other OAuth integrations (withdrawable at any time)`,
    ],
  },

  { type: "h2", text: "4. Who Processes Your Data" },
  { type: "h3", text: "4.1 Third-Party Sub-Processors" },
  {
    type: "p",
    text: `The following companies process your data on our behalf. All are bound by Data Processing Agreements. Which of them handle your data depends on how you use Talk-Lee. The speech, language-model and voice providers are chosen in AI Options (or in a campaign's settings), and the default ones are used unless you choose another. If the provider you chose does not respond during a call, Talk-Lee can switch that call to a backup provider of the same kind. Phone carriers are chosen in Telephony settings. A row that says "Only if" applies only when you connect or choose that service.`,
  },
  {
    type: "table",
    headers: ["Service", "What it does for us", "Personal data it receives"],
    rows: [
      ["Hetzner", "Hosts our servers: the Talk-Lee application, its database, the session cache (Redis) and call recordings. Always used.", "All platform data: account details, contact lists, call recordings, transcripts and call logs"],
      ["Vercel", "Hosts and delivers the Talk-Lee website and dashboard. Always used.", "Your IP address, browser details, and the pages and requests you make to the website and dashboard"],
      ["Deepgram", "Speech-to-text for call audio (the default engine) and for the in-app assistant's voice mode. Also text-to-speech if you pick a Deepgram voice, and transcription of voice notes left about a call.", "Call audio, microphone audio in the assistant's voice mode, voice notes, text sent for voice synthesis"],
      ["AssemblyAI", "Speech-to-text for call audio, as an alternative to Deepgram.", "Call audio"],
      ["Groq", "Language model that writes the agent's replies (the default), the call summaries and, by default, the in-app assistant's answers.", "Conversation text and the agent's instructions, which can include contact details such as a name; call transcripts for summaries"],
      ["Google (Gemini)", "Language model that writes the agent's replies, as an alternative to Groq.", "Conversation text and the agent's instructions"],
      ["OpenAI", "Language model that writes the agent's replies, and the realtime voice model that talks on the call itself, as alternatives to Groq.", "Conversation text and the agent's instructions; for the realtime voice model, call audio as well"],
      ["Cerebras", "Language model that writes the agent's replies or the in-app assistant's answers, as an alternative to Groq.", "Conversation text and the agent's instructions"],
      ["DeepSeek", "Language model that writes the agent's replies, as an alternative to Groq.", "Conversation text and the agent's instructions"],
      ["Cartesia", "Text-to-speech: turns the agent's replies, and the assistant's voice-mode replies, into voice (the default voice engine).", "The text the agent speaks, which can include the contact's name"],
      ["ElevenLabs", "Text-to-speech and cloned voices, as an alternative to Cartesia.", "The text the agent speaks; the voice samples you upload to create a cloned voice"],
      ["Google (Cloud Text-to-Speech)", "Text-to-speech, as an alternative to Cartesia.", "The text the agent speaks"],
      ["BlazeDigitel", "Phone carrier (SIP trunk) for the platform's default calling line. It places and receives calls unless you bring your own carrier.", "Phone numbers and call audio carried over the phone network"],
      ["Vonage", "Sends SMS messages (appointment confirmations and call transfer notifications). Also a phone carrier option if you choose Vonage for calls.", "Phone numbers and the text of SMS messages; for calls, call audio"],
      ["Twilio", "Phone carrier option. Only if you choose Twilio for calls.", "Phone numbers and call audio"],
      ["Microsoft 365 (account email)", "Sends the emails Talk-Lee itself sends: sign-up codes, password resets, team invitations, and account, billing and security notices.", "The recipient's email address and the content of the email"],
      ["Stripe", "Takes card payments and manages subscriptions.", "Name, email, billing address, payment method (card details are entered on Stripe's own page; we never store card numbers)"],
      ["Google (Calendar, Gmail, Drive)", "Books appointments in your Google Calendar, sends emails from your Gmail account and stores files in your Drive. Only if you connect Google.", "Calendar events with attendee names and email addresses, emails sent from your Gmail account, files Talk-Lee creates in your Drive"],
      ["Microsoft (Outlook Calendar)", "Books appointments in your Outlook calendar. Only if you connect Microsoft.", "Calendar events with attendee names and email addresses"],
      ["HubSpot", "Syncs contacts and call records to your HubSpot CRM. Only if you connect HubSpot.", "Contact details (name, email, phone number) and call notes, outcome and duration"],
      ["Salesforce", "Syncs contacts and call records to your Salesforce CRM. Only if you connect Salesforce.", "Contact details (name, email, phone number) and call notes, outcome and duration"],
      ["Sentry", "Error monitoring: receives technical error reports so we can find and fix faults. Only if switched on for the service.", "Anonymised error logs, stack traces"],
    ],
  },
  {
    type: "p",
    text: `Two services work directly with your browser rather than on our behalf:`,
  },
  {
    type: "ul",
    items: [
      `Google Calendar booking page — the Book a Demo page shows Google's appointment booking form inside our page. What you type there, such as your name, email address and any notes, goes to Google and to the Talk-Lee calendar the booking is made in. Google's own privacy policy applies to that form. The short form above it on the same page (your name, email address, business name and your answers to two questions about your business) is sent to Talk-Lee, not to Google, and is kept with the enquiries we receive so that we can prepare for your demo`,
      `Pwned Passwords (Have I Been Pwned) — when you enter a password on the sign-in form, your browser sends the first five characters of a one-way hash of it to api.pwnedpasswords.com to check whether it has appeared in a known data breach. The password itself is never sent, and no account details are attached to the request`,
    ],
  },
  { type: "h3", text: "4.2 We Do Not Sell Your Data" },
  {
    type: "p",
    text: `We do not sell, rent, license, or trade your personal data to any third party for any commercial purpose. This applies to all data including contact lists, call recordings, transcripts, and conversation content.`,
  },
  { type: "h3", text: "4.3 Legal Disclosure" },
  {
    type: "p",
    text: `We may disclose your data if required by a valid court order, law enforcement request, or applicable law. Where permitted, we will notify you before disclosure.`,
  },
  { type: "h3", text: "4.4 Business Transfers" },
  {
    type: "p",
    text: `In the event of a merger, acquisition, or sale of all or substantially all of our assets, your data may be transferred. We will notify you 30 days before your data is subject to a different privacy policy.`,
  },

  { type: "h2", text: "5. Google Integration" },
  { type: "h3", text: "5.1 What We Access" },
  {
    type: "ul",
    items: [
      `Google Calendar — create and manage appointments booked by AI during calls`,
      `Gmail (send only) — send confirmation emails from your Gmail account after bookings`,
      `Gmail metadata — sender, subject, and date only; never email body content`,
      `Google Drive (files created by Talk-Lee only) — store call recordings and transcripts`,
    ],
  },
  { type: "h3", text: "5.2 What We Do Not Access" },
  {
    type: "ul",
    items: [
      `Email body content`,
      `Your contacts list`,
      `Files you did not create through Talk-Lee`,
      `Google Docs, Sheets, Slides, or Photos`,
      `Your search history or any other Google service`,
    ],
  },
  { type: "h3", text: "5.3 Google API Limited Use Statement" },
  {
    type: "p",
    text: `Talk-Lee's use and transfer of information received from Google APIs adheres to the Google API Services User Data Policy, including the Limited Use requirements. We do not use Google user data to serve advertisements, to train AI models, or for any purpose other than providing the Talk-Lee service features visible to the user.`,
  },
  { type: "h3", text: "5.4 Revoking Google Access" },
  {
    type: "p",
    text: `You can disconnect your Google account at any time from Talk-Lee Settings or from your Google account at myaccount.google.com/permissions. Revocation does not delete data already stored.`,
  },

  { type: "h2", text: "6. AI Voice Calls, Recordings, and Automated Systems" },
  { type: "h3", text: "6.1 Call Recording Notice" },
  {
    type: "p",
    text: `All calls made through Talk-Lee may be recorded. Our AI agents are configured to disclose at the beginning of each call that the call may be recorded for quality assurance purposes. Recordings are stored encrypted on our own servers for 90 days by default (configurable by operator).`,
  },
  { type: "h3", text: "6.2 Voicemail Messages" },
  {
    type: "p",
    text: `When calls are unanswered, our AI agents may leave a voicemail message. Voicemail messages contain only information provided by the operator for that campaign. We track that a voicemail was left per contact for retry logic purposes.`,
  },
  { type: "h3", text: "6.3 Automated Decision Making and Profiling" },
  {
    type: "p",
    text: `Talk-Lee uses automated systems for the following purposes under GDPR Article 22:`,
  },
  {
    type: "ul",
    items: [
      `Fraud scoring — each call and tenant is automatically scored for fraud risk. This may result in calls being blocked or accounts being suspended automatically. You have the right to request human review of any automated suspension`,
      `Call guard decisions — every outbound call is automatically evaluated and may be blocked without human intervention`,
      `Sentiment analysis — calls are automatically classified as positive, neutral, or negative`,
      `Voicemail detection — calls are automatically classified as answered or voicemail`,
    ],
  },
  { type: "h3", text: "6.4 SMS Messages" },
  {
    type: "p",
    text: `Talk-Lee sends SMS messages for appointment confirmations and call transfer notifications via Vonage. SMS messages contain only information relevant to the call or appointment. Operators must ensure they have valid consent to send SMS messages in all applicable jurisdictions.`,
  },

  { type: "h2", text: "7. Legal Compliance by Jurisdiction" },
  { type: "h3", text: "7.1 United States — TCPA and FCC" },
  {
    type: "ul",
    items: [
      `Calling hours enforced based on recipient's phone number time zone (8:00 AM to 9:00 PM local)`,
      `Time zone determined by area code — not the operator's location — per FCC TCPA requirements`,
      `DNC list management per tenant — operators must scrub against national DNC registry`,
      `AI voice agents may be subject to TCPA automated calling restrictions — operators are responsible for compliance`,
      `Voicemail left by AI agents may be subject to FCC ringless voicemail guidance`,
      `SMS messages sent by Talk-Lee may be subject to TCPA — operators must obtain valid consent`,
      `FTC Act Section 5 — we do not engage in deceptive practices regarding AI disclosure`,
    ],
  },
  { type: "h3", text: "7.2 United Kingdom — ICO and PECR" },
  {
    type: "ul",
    items: [
      `Automated calls to UK numbers require prior consent under PECR Regulation 19`,
      `SMS messages to UK numbers require prior consent under PECR Regulation 22`,
      `UK data is processed under UK GDPR with the ICO as supervisory authority`,
      `Operators must hold valid PECR consent before using Talk-Lee to call UK numbers`,
    ],
  },
  { type: "h3", text: "7.3 European Union — GDPR" },
  {
    type: "ul",
    items: [
      `Legal basis for all processing is documented in Section 3.5`,
      `Data subject rights are detailed in Section 9`,
      `International transfers are covered in Section 10`,
      `Data Processing Agreement available on request from privacy@talkleeai.com`,
      `Supervisory authority: your national data protection authority`,
    ],
  },
  { type: "h3", text: "7.4 Canada — CASL" },
  {
    type: "ul",
    items: [
      `Canadian Anti-Spam Legislation (CASL) requires express or implied consent for commercial electronic messages`,
      `Operators calling or messaging Canadian recipients must hold valid CASL consent`,
      `CASL applies to SMS messages sent by Talk-Lee to Canadian numbers`,
      `Consent records must be maintained by operators`,
    ],
  },
  { type: "h3", text: "7.5 Australia — Do Not Call Register Act 2006" },
  {
    type: "ul",
    items: [
      `Calls to Australian numbers comply with the Do Not Call Register Act 2006`,
      `Calling hours restrictions apply under Australian Consumer Law`,
      `Operators must ensure Australian numbers are not on the national DNC register`,
    ],
  },
  { type: "h3", text: "7.6 California — CCPA/CPRA" },
  {
    type: "ul",
    items: [
      `California residents have rights under the California Consumer Privacy Act (CCPA) and California Privacy Rights Act (CPRA)`,
      `Right to know what personal information is collected, used, disclosed, or sold`,
      `Right to delete personal information`,
      `Right to opt-out of sale of personal information (we do not sell personal information)`,
      `Right to non-discrimination for exercising CCPA rights`,
      `To exercise California rights: privacy@talkleeai.com with subject 'CCPA Request'`,
    ],
  },

  { type: "h2", text: "8. Data Retention" },
  {
    type: "table",
    headers: ["Data Type", "Retention Period", "Reason"],
    rows: [
      ["Account data (name, email, company)", "Until account deletion + 30 days grace period", "Service provision"],
      ["Call recordings (WAV files)", "90 days default, configurable by operator", "Quality assurance, disputes"],
      ["Call transcripts", "90 days default, configurable by operator", "Quality assurance, disputes"],
      ["Conversation message history", "90 days default, configurable by operator", "Service provision"],
      ["Lead/contact data", "Until campaign or account deleted", "Service provision"],
      ["Billing records (invoices, subscriptions)", "7 years from invoice date", "Legal and tax obligation"],
      ["Audit logs", "2 years", "Security, compliance, legal obligation"],
      ["Abuse detection events", "2 years", "Security, fraud prevention"],
      ["Call guard decisions", "90 days", "Security audit trail"],
      ["Rate limit events (IP addresses)", "30 days", "Security monitoring"],
      ["API request logs", "30 days", "Technical debugging"],
      ["Error logs (Sentry)", "30 days", "Technical debugging"],
      ["Session tokens (Redis)", "24 hours", "Security"],
      ["Google OAuth tokens", "Until revoked by user", "Service provision"],
      ["HubSpot OAuth tokens", "Until revoked by user", "Service provision"],
      ["SIP credentials (encrypted)", "Until tenant deleted", "Service provision"],
      ["Webhook HMAC secrets", "Until webhook deleted", "Service provision"],
      ["Passkey credentials", "Until user deletes passkey", "Authentication"],
      ["DNC entries", "Until expired or account deleted", "Compliance"],
    ],
  },
  {
    type: "p",
    text: `When retention periods expire, data is permanently and irreversibly deleted from our database and from our recording storage. Deletion cannot be undone.`,
  },

  { type: "h2", text: "9. Data Security" },
  {
    type: "ul",
    items: [
      `All data in transit: TLS 1.2 or higher`,
      `All data at rest: AES-256 encryption`,
      `Call recordings: encrypted at rest on our own servers`,
      `OAuth tokens: Fernet encryption (AES-128-CBC + HMAC-SHA256) before database storage`,
      `Passwords: bcrypt hash with work factor 12 — plaintext never stored`,
      `SIP credentials: encrypted before storage in tenant_sip_trunks table`,
      `API keys: hashed — only prefix stored in plaintext for identification`,
      `Webhook secrets: encrypted at rest`,
      `Passkey credentials: stored as COSE-encoded public keys — no private keys ever transmitted to us`,
      `Row-Level Security (RLS): database enforces tenant isolation at query level`,
      `Multi-Factor Authentication: available for all accounts`,
      `Passkeys (FIDO2/WebAuthn): available as passwordless authentication`,
      `API rate limiting: IP-based and tenant-based to prevent abuse`,
    ],
  },
  {
    type: "callout",
    tone: "warning",
    title: "Security Incident Notification",
    text: `If we become aware of a personal data breach that is likely to result in a high risk to your rights and freedoms, we will notify affected users within 72 hours and report to the relevant supervisory authority as required by GDPR and UK GDPR.`,
  },

  { type: "h2", text: "10. Your Rights" },
  { type: "h3", text: "10.1 Universal Rights" },
  {
    type: "ul",
    items: [
      `Access — request a copy of all personal data we hold about you`,
      `Correction — request correction of inaccurate or incomplete data`,
      `Deletion — request deletion of your personal data (right to erasure / right to be forgotten)`,
      `Portability — receive your data in machine-readable format (JSON)`,
      `Withdraw consent — for optional integrations (Google, HubSpot) at any time`,
    ],
  },
  { type: "h3", text: "10.2 Additional Rights Under GDPR and UK GDPR" },
  {
    type: "ul",
    items: [
      `Restrict processing — request we limit how we process your data in certain circumstances`,
      `Object to processing — object to processing based on legitimate interests`,
      `Human review — request human review of any automated decision that significantly affects you (Article 22)`,
      `Lodge a complaint — with your national data protection supervisory authority`,
    ],
  },
  { type: "h3", text: "10.3 California Rights (CCPA/CPRA)" },
  {
    type: "ul",
    items: [
      `Know — right to know what personal information is collected and how it is used`,
      `Delete — right to request deletion of personal information`,
      `Correct — right to correct inaccurate personal information`,
      `Opt-out — right to opt out of sale of personal information (we do not sell personal information)`,
      `Non-discrimination — we will not discriminate against you for exercising your CCPA rights`,
    ],
  },
  { type: "h3", text: "10.4 How to Exercise Your Rights" },
  {
    type: "p",
    text: `Contact privacy@talkleeai.com with your request. We will respond within 30 days (GDPR/UK GDPR) or 45 days (CCPA). We may verify your identity before processing requests. Requests are free of charge.`,
  },
  { type: "h3", text: "10.5 Supervisory Authorities" },
  {
    type: "ul",
    items: [
      `United Kingdom: Information Commissioner's Office (ICO) — ico.org.uk — 0303 123 1113`,
      `European Union: Your national data protection authority — edpb.europa.eu/about-edpb/board/members`,
      `United States (FTC): ftc.gov/about-ftc/contact`,
      `California: California Privacy Protection Agency — cppa.ca.gov`,
      `Canada: Office of the Privacy Commissioner — priv.gc.ca`,
      `Australia: Office of the Australian Information Commissioner — oaic.gov.au`,
    ],
  },

  { type: "h2", text: "11. International Data Transfers" },
  {
    type: "p",
    text: `Our primary servers are located in the European Union. When we transfer data to countries outside the EEA or UK we use the following safeguards:`,
  },
  {
    type: "ul",
    items: [
      `Standard Contractual Clauses (SCCs) — with all US-based sub-processors including Deepgram, Groq, Google, ElevenLabs, Cartesia, Stripe, and Sentry`,
      `UK International Data Transfer Agreements (IDTAs) — for UK data transfers post-Brexit`,
      `Adequacy decisions — where the European Commission has deemed a country adequate`,
    ],
  },
  {
    type: "p",
    text: `Our AI providers (Deepgram, AssemblyAI, Groq, Google Gemini, OpenAI, Cerebras, DeepSeek, ElevenLabs, Cartesia) process call audio and text on their own servers, mainly in the United States. This is necessary to provide the core AI functionality of the service. All transfers are governed by SCCs incorporated into our Data Processing Agreements with each provider.`,
  },

  { type: "h2", text: "12. Cookies and Tracking" },
  {
    type: "ul",
    items: [
      `Session cookies — keep you logged in (essential, cannot be disabled)`,
      `CSRF protection cookies — prevent cross-site request forgery (essential)`,
      `Preference cookies — remember your settings (can be disabled)`,
    ],
  },
  {
    type: "p",
    text: `We do not use advertising cookies, third-party analytics that share data with advertisers, tracking pixels, or session recording tools. We do not use Google Analytics.`,
  },

  { type: "h2", text: "13. Children's Privacy" },
  {
    type: "p",
    text: `Talk-Lee is a business-to-business platform. We do not knowingly collect data from individuals under 13 (COPPA, US) or 16 (GDPR, EU/UK). If you believe we have inadvertently collected such data, contact privacy@talkleeai.com immediately for deletion.`,
  },

  { type: "h2", text: "14. Changes to This Policy" },
  {
    type: "p",
    text: `We will notify you of material changes by: updating the Effective Date, emailing all account holders, and displaying a 30-day notice in the dashboard. Continued use after changes constitutes acceptance. If you do not agree, you may delete your account.`,
  },

  { type: "h2", text: "15. Contact" },
  {
    type: "ul",
    items: [
      `Privacy: privacy@talkleeai.com`,
      `Security: security@talkleeai.com`,
      `Support: support@talkleeai.com`,
      `Legal: legal@talkleeai.com`,
      `Website: https://talkleeai.com/privacy`,
    ],
  },

  { type: "p", text: `© 2026 Talk-Lee Limited. All rights reserved.` },
];
