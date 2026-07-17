import type { LegalBlock } from "@/components/legal/legal-document";

export const termsBlocks: LegalBlock[] = [
  { type: "h2", text: "1. Parties and Agreement" },
  {
    type: "p",
    text: `These Terms of Service ("Terms") govern your access to and use of the Talk-Lee AI voice agent platform including talkleeai.com, our API, mobile applications, and all related services ("Service").`,
  },
  {
    type: "p",
    text: `"Talk-Lee Limited" ("we", "us", "our") is the provider. "You", "Customer", or "Operator" is the individual or organisation accessing the Service.`,
  },
  {
    type: "p",
    text: `By registering, you confirm you are aged 18 or over (or the age of majority in your jurisdiction), have authority to bind your organisation to these Terms, and agree to all provisions herein.`,
  },

  { type: "h2", text: "2. Description of Service" },
  { type: "p", text: `Talk-Lee provides an AI-powered voice agent platform enabling businesses to:` },
  {
    type: "ul",
    items: [
      `Deploy autonomous AI voice agents for outbound calling campaigns`,
      `Handle inbound calls with AI receptionists and customer support agents`,
      `Leave voicemail messages when calls are unanswered`,
      `Send SMS appointment confirmations and transfer notifications via Vonage`,
      `Automatically book appointments in Google Calendar during live AI calls`,
      `Send confirmation emails from your Gmail account after bookings`,
      `Manage contact lists, campaigns, analytics, call recordings, and transcripts`,
      `Integrate with HubSpot CRM for contact and deal management`,
      `Issue API keys for developers to build on top of Talk-Lee`,
      `Provide partner/reseller accounts for agencies and platform partners`,
    ],
  },
  {
    type: "p",
    text: `The Service uses multiple AI providers including Deepgram (STT), Groq and Google Gemini (LLM), ElevenLabs, Cartesia, Google TTS, and Deepgram (TTS), and telephony infrastructure including Asterisk, OpenSIPS, RTPEngine, Vonage, and BlazeDigitel.`,
  },

  { type: "h2", text: "3. Account Registration and Security" },
  { type: "h3", text: "3.1 Account Requirements" },
  {
    type: "ul",
    items: [
      `Provide accurate, current, and complete information at registration`,
      `Maintain and promptly update your account information`,
      `Keep your credentials confidential — do not share passwords or API keys`,
      `Enable Multi-Factor Authentication (MFA) — strongly recommended`,
      `Notify us immediately of any unauthorised access at security@talkleeai.com`,
    ],
  },
  { type: "h3", text: "3.2 API Keys" },
  {
    type: "p",
    text: `API keys issued through our platform grant programmatic access to the Service. You are responsible for all activity under your API keys. Treat API keys as passwords — do not embed them in client-side code or public repositories. Compromised keys must be revoked immediately.`,
  },
  { type: "h3", text: "3.3 Account Responsibility" },
  {
    type: "p",
    text: `You are responsible for all activity under your account including calls made, messages sent, data uploaded, and actions taken by anyone you give access to, including developers using your API keys.`,
  },

  { type: "h2", text: "4. Acceptable Use" },
  { type: "h3", text: "4.1 Permitted Use" },
  { type: "p", text: `You may use Talk-Lee only for lawful business purposes and in full compliance with:` },
  {
    type: "ul",
    items: [
      `The Telephone Consumer Protection Act (TCPA) — United States`,
      `FCC regulations on automated calling, including voicemail and ringless voicemail`,
      `Privacy and Electronic Communications Regulations (PECR) — United Kingdom`,
      `General Data Protection Regulation (GDPR) — European Union`,
      `UK GDPR — United Kingdom`,
      `Canadian Anti-Spam Legislation (CASL) — Canada`,
      `Do Not Call Register Act 2006 — Australia`,
      `California Consumer Privacy Act (CCPA/CPRA) — California, USA`,
      `FTC Act Section 5 — prohibition on deceptive practices`,
      `All other applicable telemarketing, data protection, and consumer protection laws in all jurisdictions where calls are made or received`,
    ],
  },
  { type: "h3", text: "4.2 Prohibited Use" },
  { type: "p", text: `You must not use Talk-Lee to:` },
  {
    type: "ul",
    items: [
      `Call numbers without lawful consent as required by applicable law`,
      `Call numbers on national or state Do Not Call registries without a lawful exemption`,
      `Send SMS messages without valid consent under TCPA, PECR, CASL, or applicable law`,
      `Leave voicemail messages in violation of FCC ringless voicemail guidance`,
      `Call outside legally permitted hours in the recipient's jurisdiction`,
      `Conduct fraudulent, deceptive, or misleading calling campaigns`,
      `Impersonate government agencies, emergency services, financial institutions, or regulated professionals`,
      `Harass, threaten, abuse, or intimidate call recipients`,
      `Transmit spam, malware, or malicious content via SMS or email`,
      `Collect data in violation of applicable privacy laws`,
      `Use the service for illegal gambling, adult content, controlled substances, or weapons`,
      `Resell or sublicense the Service without our prior written consent`,
      `Reverse engineer, decompile, or attempt to extract our source code or AI models`,
      `Use the Service to train competing AI models or products`,
      `Circumvent our security systems, rate limits, or usage controls`,
      `Create multiple accounts to circumvent usage limits`,
      `Use the API in a manner that exceeds fair use or constitutes abuse`,
    ],
  },
  { type: "h3", text: "4.3 Your Compliance Obligations" },
  { type: "p", text: `You are solely and exclusively responsible for:` },
  {
    type: "ul",
    items: [
      `Obtaining all required consents from individuals before calling, SMS messaging, or emailing them`,
      `Maintaining your own Do Not Call lists and honouring all opt-out requests immediately`,
      `Scrubbing your contact lists against national and state DNC registries before uploading`,
      `Ensuring your contact lists were lawfully obtained and lawfully processed`,
      `Complying with all AI disclosure requirements in applicable jurisdictions`,
      `Complying with all call recording laws in all jurisdictions where calls are made and received`,
      `Ensuring your use of voicemail and SMS messaging complies with applicable law`,
      `Obtaining valid CASL consent before messaging Canadian recipients`,
      `Complying with Australian DNC Register requirements for Australian recipients`,
    ],
  },
  {
    type: "callout",
    tone: "warning",
    title: "Critical Compliance Notice",
    text: `Telemarketing, automated calling, SMS, and AI disclosure laws vary significantly by country, state, province, and industry. Talk-Lee provides technical compliance tools (calling hours enforcement, DNC management, consent recording) but does not provide legal advice and does not guarantee your legal compliance. You must obtain independent legal advice before running calling campaigns in any jurisdiction. Non-compliance may result in fines of up to $1,500 per call or message under TCPA.`,
  },

  { type: "h2", text: "5. AI Voice Calls — Special Terms" },
  { type: "h3", text: "5.1 Nature of AI Agents" },
  {
    type: "ul",
    items: [
      `AI agents are powered by large language models and may produce unexpected or inaccurate responses`,
      `AI agents are not a substitute for human judgment in sensitive, medical, legal, or financial situations`,
      `You are responsible for configuring AI agents appropriately for your use case and target audience`,
      `AI agents will identify themselves as calling from your business as configured`,
    ],
  },
  { type: "h3", text: "5.2 Call Recording" },
  {
    type: "p",
    text: `All calls are recorded by default. You must ensure that your use of call recording complies with all applicable laws in all jurisdictions where calls are made and received. Many jurisdictions require all-party consent to recording. You are solely responsible for obtaining required recording consent from all parties.`,
  },
  { type: "h3", text: "5.3 Voicemail Messages" },
  {
    type: "p",
    text: `Talk-Lee AI agents may leave automated voicemail messages when calls are unanswered. Automated voicemail messages may be regulated under TCPA and FCC guidance in the United States, and equivalent laws in other jurisdictions. You are solely responsible for ensuring voicemail messages comply with all applicable laws including FCC guidance on ringless voicemails.`,
  },
  { type: "h3", text: "5.4 SMS Messages" },
  {
    type: "p",
    text: `Talk-Lee sends SMS messages on your behalf via Vonage. SMS messages are subject to TCPA (US), PECR (UK), CASL (Canada), and equivalent laws. You must obtain valid prior consent for all SMS recipients. You are solely responsible for TCPA, PECR, and CASL compliance for all SMS messages sent through Talk-Lee.`,
  },
  { type: "h3", text: "5.5 Calling Hours" },
  {
    type: "p",
    text: `Our platform automatically enforces calling hours (8:00 AM to 9:00 PM) based on the recipient's area code time zone for US numbers, as required by TCPA. However, more restrictive state laws may apply in some US states (e.g. Florida, New York). Calling hour laws in other countries vary. You are responsible for ensuring compliance with all applicable calling hour restrictions.`,
  },
  { type: "h3", text: "5.6 Do Not Call Compliance" },
  {
    type: "p",
    text: `Our platform maintains per-tenant DNC lists and enforces them before every call. However, we do not have access to national DNC registries. You must scrub your contact lists against the US National Do Not Call Registry, UK TPS, and equivalent registries before uploading them to Talk-Lee. We are not liable for calls made to numbers that should have been removed before upload.`,
  },

  { type: "h2", text: "6. Billing and Payment" },
  { type: "h3", text: "6.1 Pricing" },
  {
    type: "p",
    text: `Subscription pricing is available at talkleeai.com/pricing. We reserve the right to modify pricing with 30 days notice to existing customers. Price changes do not apply to the current billing period.`,
  },
  { type: "h3", text: "6.2 Billing" },
  {
    type: "ul",
    items: [
      `Subscriptions are billed monthly in advance unless otherwise agreed`,
      `Usage-based charges (call minutes, API requests) are billed monthly in arrears`,
      `All payments processed by Stripe — we never store your payment card details`,
      `You authorise us to charge your payment method on file for all applicable fees`,
      `All prices are exclusive of applicable taxes`,
    ],
  },
  { type: "h3", text: "6.3 Failed Payments" },
  {
    type: "p",
    text: `If payment fails, we will retry 3 times over 7 days and notify you by email. After 3 failed attempts your account may be suspended. You will receive a suspension notification email. Service resumes upon successful payment.`,
  },
  { type: "h3", text: "6.4 Refunds" },
  {
    type: "p",
    text: `Monthly subscription fees are non-refundable once a billing period has commenced. Usage fees are non-refundable. We may provide refunds at our discretion for verified billing errors. Contact billing@talkleeai.com for billing disputes within 30 days of the invoice date.`,
  },
  { type: "h3", text: "6.5 Free Trials" },
  {
    type: "p",
    text: `If we offer a free trial, your subscription will automatically begin at the end of the trial period unless you cancel before it ends. We will notify you before any trial ends.`,
  },

  { type: "h2", text: "7. Partner and Reseller Accounts" },
  {
    type: "p",
    text: `Talk-Lee offers partner accounts for agencies and resellers who wish to provide Talk-Lee services to their own clients. Partner accounts are subject to:`,
  },
  {
    type: "ul",
    items: [
      `Additional Partner Agreement terms provided at partner signup`,
      `Revenue share arrangements as agreed in the Partner Agreement`,
      `Aggregate call limits and tenant limits per your partner tier`,
      `Responsibility for all sub-tenants' compliance with these Terms and applicable law`,
      `Partners are jointly responsible with their sub-tenants for TCPA, GDPR, and other regulatory compliance`,
    ],
  },
  {
    type: "p",
    text: `Partners may not grant sub-tenants more permissions than partners themselves hold. Partners are responsible for ensuring all sub-tenants comply with these Terms.`,
  },

  { type: "h2", text: "8. Developer API" },
  { type: "p", text: `Developers who use the Talk-Lee API agree to:` },
  {
    type: "ul",
    items: [
      `Use the API only for lawful purposes consistent with these Terms`,
      `Not exceed documented rate limits or fair use thresholds`,
      `Not resell API access without written consent`,
      `Not use API access to scrape, index, or copy our data`,
      `Secure API keys and rotate them if compromised`,
      `Comply with our API documentation and any usage policies`,
    ],
  },
  {
    type: "p",
    text: `We reserve the right to suspend API access for any account that abuses the API or violates these Terms.`,
  },

  { type: "h2", text: "9. Data, Privacy, and Processing" },
  {
    type: "p",
    text: `Your use of Talk-Lee is subject to our Privacy Policy at talkleeai.com/privacy. A Data Processing Agreement (DPA) is available at talkleeai.com/dpa and is incorporated into these Terms for GDPR and UK GDPR compliance.`,
  },
  { type: "h3", text: "9.1 Your Data" },
  {
    type: "p",
    text: `You retain all rights to data you upload including contact lists, recordings, and transcripts. You grant us a limited licence to process this data solely to provide the Service.`,
  },
  { type: "h3", text: "9.2 Data Controller / Processor" },
  {
    type: "p",
    text: `For personal data of individuals you call: you are the data controller and Talk-Lee is your data processor. You must have a lawful basis to process their data and to contact them. Our DPA governs this relationship.`,
  },
  { type: "h3", text: "9.3 Data Deletion" },
  {
    type: "p",
    text: `Upon account termination, we delete your data within 30 days except where retention is required by law (billing records: 7 years). Recording and transcript deletion can be requested earlier via privacy@talkleeai.com.`,
  },

  { type: "h2", text: "10. Intellectual Property" },
  { type: "h3", text: "10.1 Our IP" },
  {
    type: "p",
    text: `Talk-Lee and all related software, algorithms, AI models, designs, trademarks, and content are owned by Talk-Lee Limited and protected by applicable intellectual property laws.`,
  },
  { type: "h3", text: "10.2 Your IP" },
  {
    type: "p",
    text: `You retain all rights to your content including scripts, contact lists, and configurations. You grant us a limited, non-exclusive, royalty-free licence to use your content solely to provide the Service.`,
  },
  { type: "h3", text: "10.3 Restrictions" },
  {
    type: "ul",
    items: [
      `You may not use Talk-Lee outputs to train competing AI systems`,
      `You may not reproduce or distribute our software, documentation, or AI models`,
      `You may not use our trademarks without prior written permission`,
    ],
  },
  { type: "h3", text: "10.4 Feedback" },
  {
    type: "p",
    text: `Feedback and suggestions you provide may be used by us without restriction or compensation.`,
  },

  { type: "h2", text: "11. Disclaimers and Warranties" },
  {
    type: "p",
    text: `THE SERVICE IS PROVIDED "AS IS" AND "AS AVAILABLE" WITHOUT ANY WARRANTIES, EXPRESS OR IMPLIED, INCLUDING WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, NON-INFRINGEMENT, OR UNINTERRUPTED SERVICE.`,
  },
  {
    type: "p",
    text: `WE DO NOT WARRANT THAT: (A) THE SERVICE WILL MEET YOUR REQUIREMENTS; (B) THE SERVICE WILL BE UNINTERRUPTED, TIMELY, OR ERROR-FREE; (C) AI AGENT RESPONSES WILL BE ACCURATE OR APPROPRIATE; (D) ANY ERRORS WILL BE CORRECTED.`,
  },
  {
    type: "p",
    text: `Some jurisdictions do not allow exclusion of implied warranties. In such jurisdictions, our warranties are limited to the minimum extent permitted by law.`,
  },

  { type: "h2", text: "12. Limitation of Liability" },
  {
    type: "p",
    text: `TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW, TALK-LEE LIMITED SHALL NOT BE LIABLE FOR ANY INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, EXEMPLARY, OR PUNITIVE DAMAGES INCLUDING BUT NOT LIMITED TO LOSS OF PROFITS, DATA, BUSINESS OPPORTUNITIES, OR GOODWILL, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGES.`,
  },
  {
    type: "p",
    text: `OUR TOTAL AGGREGATE LIABILITY TO YOU FOR ALL CLAIMS ARISING FROM OR RELATED TO THESE TERMS OR YOUR USE OF THE SERVICE SHALL NOT EXCEED THE GREATER OF: (A) THE TOTAL FEES PAID BY YOU IN THE 12 MONTHS PRECEDING THE CLAIM; OR (B) £100 GBP.`,
  },
  {
    type: "p",
    text: `SOME JURISDICTIONS DO NOT ALLOW LIMITATION OF CERTAIN DAMAGES. IN SUCH JURISDICTIONS OUR LIABILITY IS LIMITED TO THE MAXIMUM EXTENT PERMITTED BY LAW. NOTHING IN THESE TERMS LIMITS LIABILITY FOR DEATH OR PERSONAL INJURY CAUSED BY NEGLIGENCE, FRAUD, OR ANY LIABILITY THAT CANNOT BE LIMITED BY LAW.`,
  },

  { type: "h2", text: "13. Indemnification" },
  {
    type: "p",
    text: `You agree to indemnify, defend, and hold harmless Talk-Lee Limited, its directors, officers, employees, agents, and licensors from and against any and all claims, damages, losses, liabilities, costs, and expenses (including reasonable legal fees) arising from or related to:`,
  },
  {
    type: "ul",
    items: [
      `Your use or misuse of the Service`,
      `Your violation of these Terms`,
      `Your violation of any applicable law including TCPA, PECR, GDPR, CASL, CCPA, or Australian DNC Act`,
      `Your calling campaigns, voicemail messages, or SMS messages`,
      `Content you upload, generate, or transmit through the Service`,
      `Any claim by a third party arising from your use of AI voice agents`,
      `Infringement of any third party's intellectual property or privacy rights`,
      `Any regulatory fine or penalty arising from your use of the Service`,
    ],
  },

  { type: "h2", text: "14. Service Availability" },
  { type: "h3", text: "14.1 Uptime Target" },
  {
    type: "p",
    text: `We aim to maintain 99.9% monthly uptime excluding scheduled maintenance. Scheduled maintenance will be communicated with reasonable advance notice. We do not guarantee uninterrupted service and are not liable for downtime.`,
  },
  { type: "h3", text: "14.2 Modifications" },
  {
    type: "p",
    text: `We may modify, suspend, or discontinue any feature with reasonable notice. Material changes will be communicated by email and dashboard notification.`,
  },
  { type: "h3", text: "14.3 Suspension" },
  {
    type: "p",
    text: `We may suspend your account immediately for: material breach of these Terms, fraudulent or abusive activity, failure to pay, legal requirement, or protection of other users or the platform.`,
  },

  { type: "h2", text: "15. Termination" },
  { type: "h3", text: "15.1 By You" },
  {
    type: "p",
    text: `Cancel anytime from account settings or by emailing support@talkleeai.com. Cancellation takes effect at the end of the current billing period. No refund for the current period.`,
  },
  { type: "h3", text: "15.2 By Us" },
  {
    type: "p",
    text: `We may terminate your account with 30 days notice for any reason, or immediately for material breach, illegal activity, or repeated non-payment.`,
  },
  { type: "h3", text: "15.3 Effect of Termination" },
  {
    type: "ul",
    items: [
      `Your access to the Service ends immediately upon termination`,
      `Data is deleted within 30 days (except legally required retention)`,
      `Outstanding fees become immediately due`,
      `Provisions that by their nature survive termination will survive: IP, indemnification, limitation of liability, governing law`,
    ],
  },

  { type: "h2", text: "16. Governing Law and Dispute Resolution" },
  { type: "h3", text: "16.1 Governing Law" },
  {
    type: "p",
    text: `These Terms are governed by the laws of England and Wales for UK and EU customers, and the laws of the State of Delaware for US customers, without regard to conflict of law provisions.`,
  },
  { type: "h3", text: "16.2 Dispute Resolution" },
  { type: "p", text: `We encourage informal resolution first — contact legal@talkleeai.com. If unresolved within 30 days:` },
  {
    type: "ul",
    items: [
      `UK/EU customers: courts of England and Wales have exclusive jurisdiction`,
      `US customers: binding arbitration under AAA Commercial Arbitration Rules in Delaware`,
      `Class action waiver: you waive the right to participate in class actions to the extent permitted by law`,
    ],
  },
  { type: "h3", text: "16.3 Consumer Rights" },
  {
    type: "p",
    text: `Nothing in these Terms limits statutory consumer rights you may have under applicable law. Consumers in the EU have rights under EU consumer protection legislation that cannot be waived by contract.`,
  },

  { type: "h2", text: "17. General" },
  {
    type: "ul",
    items: [
      `Entire Agreement — these Terms, Privacy Policy, and DPA constitute the entire agreement`,
      `Severability — if any provision is unenforceable, the remainder continues in full effect`,
      `Waiver — failure to enforce any right is not a waiver of that right`,
      `Assignment — you may not assign these Terms without prior written consent. We may assign in connection with merger or acquisition`,
      `Force Majeure — neither party is liable for failures caused by events beyond reasonable control including natural disasters, government actions, or third-party infrastructure failures`,
      `Notices — legal notices to us must be sent to legal@talkleeai.com`,
      `Language — English is the governing language of these Terms`,
      `Relationship — the parties are independent contractors; no partnership, agency, or employment relationship is created`,
    ],
  },

  { type: "h2", text: "18. Contact" },
  {
    type: "ul",
    items: [
      `Legal: legal@talkleeai.com`,
      `Privacy: privacy@talkleeai.com`,
      `Billing: billing@talkleeai.com`,
      `Support: support@talkleeai.com`,
      `Website: https://talkleeai.com/terms`,
    ],
  },

  { type: "p", text: `© 2026 Talk-Lee Limited. All rights reserved.` },
];
