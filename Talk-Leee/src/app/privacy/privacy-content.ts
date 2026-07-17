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
      `Call recordings — audio files stored encrypted on AWS S3 (WAV format)`,
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
      `We do not collect precise geolocation data`,
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
      `Running AI voice calling campaigns — STT via Deepgram, LLM via Groq or Gemini, TTS via ElevenLabs, Cartesia, Google TTS, or Deepgram`,
      `Routing calls through SIP telephony infrastructure (Asterisk, OpenSIPS, RTPEngine)`,
      `Booking appointments in Google Calendar during AI calls`,
      `Sending confirmation emails via Gmail (send only)`,
      `Sending SMS notifications via Vonage`,
      `Leaving voicemail messages when calls are unanswered`,
      `Syncing call outcomes to HubSpot CRM when connected`,
      `Storing call recordings and transcripts for operator review`,
      `Sending transactional emails via SendGrid, SMTP, or AWS SES`,
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

  { type: "h2", text: "4. Third-Party Sub-Processors" },
  { type: "h3", text: "4.1 Complete Sub-Processor List" },
  {
    type: "p",
    text: `The following companies process your data on our behalf. All are bound by Data Processing Agreements.`,
  },
  {
    type: "table",
    headers: ["Provider", "Purpose", "Data Processed", "Location", "Transfer Mechanism"],
    rows: [
      ["Deepgram", "Speech-to-text (STT) and TTS", "Call audio, transcripts", "United States", "Standard Contractual Clauses"],
      ["Groq", "LLM inference (primary)", "Conversation text, system prompts", "United States", "Standard Contractual Clauses"],
      ["Google (Gemini)", "LLM inference (secondary)", "Conversation text, system prompts", "United States", "Standard Contractual Clauses"],
      ["ElevenLabs", "Text-to-speech (primary)", "Text sent for voice synthesis", "United States", "Standard Contractual Clauses"],
      ["Cartesia", "Text-to-speech (secondary)", "Text sent for voice synthesis", "United States", "Standard Contractual Clauses"],
      ["Google (TTS)", "Text-to-speech (fallback)", "Text sent for voice synthesis", "United States", "Standard Contractual Clauses"],
      ["Stripe", "Payment processing", "Name, email, billing address, payment method", "United States", "Standard Contractual Clauses + DPA"],
      ["AWS (S3)", "File storage", "Call recordings, contact lists", "EU / Global", "Standard Contractual Clauses"],
      ["Supabase Inc.", "Database hosting", "All platform data", "United States / EU", "Standard Contractual Clauses"],
      ["Redis (self-hosted)", "Session cache", "Session tokens, temporary call state", "EU (our server)", "N/A — self-hosted"],
      ["Sentry", "Error monitoring", "Anonymised error logs, stack traces", "United States", "Standard Contractual Clauses"],
      ["Vonage", "SMS + telephony", "Phone numbers, call routing, SMS content", "United Kingdom / EU", "UK GDPR compliant"],
      ["BlazeDigitel", "SIP telephony trunk", "Phone numbers, call audio routing", "As contracted", "As contracted"],
      ["SendGrid", "Transactional email", "Email address, email content", "United States", "Standard Contractual Clauses"],
      ["AWS SES", "Transactional email (fallback)", "Email address, email content", "EU / Global", "Standard Contractual Clauses"],
      ["HubSpot", "CRM sync (optional)", "Contact data when user connects HubSpot", "United States", "Standard Contractual Clauses"],
      ["Google (Calendar/Gmail/Drive)", "Integrations (optional)", "Calendar events, Gmail metadata, Drive files", "United States", "Standard Contractual Clauses"],
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
    text: `All calls made through Talk-Lee may be recorded. Our AI agents are configured to disclose at the beginning of each call that the call may be recorded for quality assurance purposes. Recordings are stored encrypted on AWS S3 for 90 days by default (configurable by operator).`,
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
      `Data Processing Agreement available at talkleeai.com/dpa`,
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
    text: `When retention periods expire, data is permanently and irreversibly deleted from our database and from AWS S3. Deletion cannot be undone.`,
  },

  { type: "h2", text: "9. Data Security" },
  {
    type: "ul",
    items: [
      `All data in transit: TLS 1.2 or higher`,
      `All data at rest: AES-256 encryption`,
      `Call recordings: encrypted at rest on AWS S3 with server-side encryption`,
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
      `Standard Contractual Clauses (SCCs) — with all US-based sub-processors including Deepgram, Groq, Google, ElevenLabs, Cartesia, Stripe, Sentry, and SendGrid`,
      `UK International Data Transfer Agreements (IDTAs) — for UK data transfers post-Brexit`,
      `Adequacy decisions — where the European Commission has deemed a country adequate`,
    ],
  },
  {
    type: "p",
    text: `Our AI providers (Deepgram, Groq, Google Gemini, ElevenLabs) process call audio and text in the United States. This is necessary to provide the core AI functionality of the service. All transfers are governed by SCCs incorporated into our Data Processing Agreements with each provider.`,
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
