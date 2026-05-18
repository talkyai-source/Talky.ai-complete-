--
-- PostgreSQL database dump
--

\restrict 3rryg8z1y9nXSl2pSZ6wOn85wRq4r1hBRYr6QFOhxNb2XDNqfuQLkISkLsLgKPH

-- Dumped from database version 16.13 (Ubuntu 16.13-0ubuntu0.24.04.1)
-- Dumped by pg_dump version 16.13 (Ubuntu 16.13-0ubuntu0.24.04.1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Data for Name: plans; Type: TABLE DATA; Schema: public; Owner: -
--

SET SESSION AUTHORIZATION DEFAULT;

ALTER TABLE public.plans DISABLE TRIGGER ALL;

COPY public.plans (id, name, price, description, minutes, agents, concurrent_calls, features, not_included, popular, stripe_price_id, stripe_product_id, billing_period, created_at, updated_at) FROM stdin;
basic	Basic	29.00	Perfect for startups.	300	1	1	["300 minutes/month", "1 AI agent", "Basic analytics", "Email support"]	["API access", "Custom voices", "Priority support"]	f	\N	\N	monthly	2026-02-18 16:33:58.25665+05	2026-02-18 16:33:58.25665+05
professional	Professional	79.00	Ideal for growing businesses.	1500	3	3	["1500 minutes/month", "3 AI agents", "Advanced analytics", "Priority support", "Custom voices"]	["API access", "White-label"]	t	\N	\N	monthly	2026-02-18 16:33:58.25665+05	2026-02-18 16:33:58.25665+05
enterprise	Enterprise	199.00	For large scale operations.	5000	10	10	["5000 minutes/month", "10 AI agents", "Full analytics", "24/7 support", "API access", "White-label"]	[]	f	\N	\N	monthly	2026-02-18 16:33:58.25665+05	2026-02-18 16:33:58.25665+05
free	Free Trial	0.00	Temporary 15-day promotional tier — expires 2026-05-15.	30	1	1	["30 minutes", "1 AI agent", "Trial only — no production support"]	["All paid features"]	f	\N	\N	monthly	2026-04-30 19:38:33.975897+05	2026-04-30 19:38:33.975897+05
\.


ALTER TABLE public.plans ENABLE TRIGGER ALL;

--
-- Data for Name: tenants; Type: TABLE DATA; Schema: public; Owner: -
--

ALTER TABLE public.tenants DISABLE TRIGGER ALL;

COPY public.tenants (id, business_name, plan_id, minutes_allocated, minutes_used, calling_rules, stripe_customer_id, stripe_subscription_id, subscription_status, created_at, updated_at, partner_id, is_partner, status, suspended_at, suspended_by, suspension_reason, white_label_partner_id) FROM stdin;
76171092-ceac-4f7a-baab-af6d1bf4db68	DEVAI	basic	300	0	{"skip_dnc": true, "timezone": "America/New_York", "allowed_days": [0, 1, 2, 3, 4], "time_window_end": "19:00", "time_window_start": "09:00", "max_retry_attempts": 3, "retry_delay_seconds": 7200, "max_concurrent_calls": 10, "high_priority_threshold": 8, "min_hours_between_calls": 2, "enable_priority_override": true}	\N	\N	inactive	2026-02-19 21:08:46.336936+05	2026-02-19 21:08:46.336936+05	\N	f	active	\N	\N	\N	\N
cf2bea16-dc16-4145-97a6-8d4bc2366a7b	talk-lee	basic	300	0	{"skip_dnc": true, "timezone": "America/New_York", "allowed_days": [0, 1, 2, 3, 4], "time_window_end": "19:00", "time_window_start": "09:00", "max_retry_attempts": 3, "retry_delay_seconds": 7200, "max_concurrent_calls": 10, "high_priority_threshold": 8, "min_hours_between_calls": 2, "enable_priority_override": true}	\N	\N	inactive	2026-03-07 22:13:26.497114+05	2026-03-07 22:13:26.497114+05	\N	f	active	\N	\N	\N	\N
f3d331de-c3e8-4990-a217-a9958cd49056	GOLF	basic	300	0	{"skip_dnc": true, "timezone": "America/New_York", "allowed_days": [0, 1, 2, 3, 4], "time_window_end": "19:00", "time_window_start": "09:00", "max_retry_attempts": 3, "retry_delay_seconds": 7200, "max_concurrent_calls": 10, "high_priority_threshold": 8, "min_hours_between_calls": 2, "enable_priority_override": true}	\N	\N	inactive	2026-03-30 18:00:25.755945+05	2026-03-30 18:00:25.755945+05	\N	f	active	\N	\N	\N	\N
95f2c96b-efce-49a7-957a-55ee7ff64e7a	GOLF	basic	300	0	{"skip_dnc": true, "timezone": "America/New_York", "allowed_days": [0, 1, 2, 3, 4], "time_window_end": "19:00", "time_window_start": "09:00", "max_retry_attempts": 3, "retry_delay_seconds": 7200, "max_concurrent_calls": 10, "high_priority_threshold": 8, "min_hours_between_calls": 2, "enable_priority_override": true}	\N	\N	inactive	2026-03-30 18:03:29.34644+05	2026-03-30 18:03:29.34644+05	\N	f	active	\N	\N	\N	\N
370b3a41-9987-4cfb-b491-2ea352966299	TEST	basic	300	0	{"skip_dnc": true, "timezone": "America/New_York", "allowed_days": [0, 1, 2, 3, 4], "time_window_end": "19:00", "time_window_start": "09:00", "max_retry_attempts": 3, "retry_delay_seconds": 7200, "max_concurrent_calls": 10, "high_priority_threshold": 8, "min_hours_between_calls": 2, "enable_priority_override": true}	\N	\N	inactive	2026-03-31 16:46:17.467013+05	2026-03-31 16:46:17.467013+05	\N	f	active	\N	\N	\N	\N
0d1c717a-e887-4d0d-b528-cc2f702dff8d	TEST	basic	300	0	{"skip_dnc": true, "timezone": "America/New_York", "allowed_days": [0, 1, 2, 3, 4], "time_window_end": "19:00", "time_window_start": "09:00", "max_retry_attempts": 3, "retry_delay_seconds": 7200, "max_concurrent_calls": 10, "high_priority_threshold": 8, "min_hours_between_calls": 2, "enable_priority_override": true}	\N	\N	inactive	2026-03-31 17:43:08.954195+05	2026-03-31 17:43:08.954195+05	\N	f	active	\N	\N	\N	\N
532b5db0-2374-4579-a7ac-87bd2893016b	GOLF	basic	300	0	{"skip_dnc": true, "timezone": "UTC", "caller_id": null, "allowed_days": [0, 1, 2, 3, 4, 5, 6], "time_window_end": "23:59", "time_window_start": "00:00", "max_retry_attempts": 3, "retry_delay_seconds": 7200, "max_concurrent_calls": 10, "high_priority_threshold": 8, "min_hours_between_calls": 0, "enable_priority_override": true}	\N	\N	inactive	2026-03-30 18:06:53.071197+05	2026-04-02 15:45:38.625796+05	\N	f	active	\N	\N	\N	\N
87a98f0d-f45d-486b-9c52-86b7856e92da	Test	basic	300	0	{"skip_dnc": true, "timezone": "America/New_York", "allowed_days": [0, 1, 2, 3, 4], "time_window_end": "19:00", "time_window_start": "09:00", "max_retry_attempts": 3, "retry_delay_seconds": 7200, "max_concurrent_calls": 10, "high_priority_threshold": 8, "min_hours_between_calls": 2, "enable_priority_override": true}	\N	\N	inactive	2026-04-27 20:29:08.411956+05	2026-04-27 20:29:08.411956+05	\N	f	active	\N	\N	\N	\N
4f051fc1-14e7-4abe-9060-c753199076bf	Test	basic	300	0	{"skip_dnc": true, "timezone": "America/New_York", "allowed_days": [0, 1, 2, 3, 4], "time_window_end": "19:00", "time_window_start": "09:00", "max_retry_attempts": 3, "retry_delay_seconds": 7200, "max_concurrent_calls": 10, "high_priority_threshold": 8, "min_hours_between_calls": 2, "enable_priority_override": true}	\N	\N	inactive	2026-04-27 20:29:23.876325+05	2026-04-27 20:29:23.876325+05	\N	f	active	\N	\N	\N	\N
\.


ALTER TABLE public.tenants ENABLE TRIGGER ALL;

--
-- Data for Name: abuse_detection_rules; Type: TABLE DATA; Schema: public; Owner: -
--

ALTER TABLE public.abuse_detection_rules DISABLE TRIGGER ALL;

COPY public.abuse_detection_rules (id, tenant_id, rule_name, rule_type, parameters, warn_threshold, block_threshold, action_on_trigger, analysis_window_minutes, is_active, priority, created_at, updated_at, created_by, updated_by) FROM stdin;
202b8e41-f3de-4a64-8edc-b04296f9a6b3	\N	Global Velocity Spike Detection	velocity_spike	{"spike_multiplier": 3.0, "min_baseline_calls": 10, "comparison_window_hours": 24}	2	5	throttle	60	t	10	2026-04-27 20:17:38.152889+05	2026-04-27 20:17:38.152889+05	\N	\N
9c3a8299-8abb-4760-ab5f-1384cf06a509	\N	Global Short Duration Pattern	short_duration_pattern	{"window_minutes": 60, "min_calls_in_window": 10, "duration_threshold_seconds": 10}	5	10	block	60	t	20	2026-04-27 20:17:38.152889+05	2026-04-27 20:17:38.152889+05	\N	\N
989a542e-0748-471f-b320-b09f60c76049	\N	Global Repeat Number Detection	repeat_number	{"window_minutes": 60, "max_calls_per_number": 3}	2	3	block	60	t	30	2026-04-27 20:17:38.152889+05	2026-04-27 20:17:38.152889+05	\N	\N
d3a4b19c-f8c7-45ce-905b-60d3d2f3e123	\N	Global Sequential Dialing Detection	sequential_dialing	{"window_minutes": 30, "sequence_length": 5, "digit_variance_threshold": 2}	1	2	block	30	t	40	2026-04-27 20:17:38.152889+05	2026-04-27 20:17:38.152889+05	\N	\N
b0524041-b175-48ba-a4ec-b14aa538c00b	\N	Global Premium Rate Protection	premium_rate	{"alert_on_first": true, "blocked_prefixes": ["+1900", "+4487", "+339", "+809"]}	1	3	block	1440	t	50	2026-04-27 20:17:38.152889+05	2026-04-27 20:17:38.152889+05	\N	\N
e7075583-72af-4564-ba32-e1fd4f1d285a	\N	Global International Spike	international_spike	{"spike_multiplier": 5.0, "high_risk_countries": ["PK", "BD", "NG", "VN", "ID"], "comparison_window_hours": 24}	3	5	throttle	60	t	60	2026-04-27 20:17:38.152889+05	2026-04-27 20:17:38.152889+05	\N	\N
e6ba46af-332e-47f4-8290-badb18d74798	\N	Global After Hours Detection	after_hours	{"allow_emergency": true}	10	20	warn	1440	t	100	2026-04-27 20:17:38.152889+05	2026-04-27 20:17:38.152889+05	\N	\N
8e3af136-ecae-416d-8d6c-2a4a06a88e84	\N	Global Toll Fraud Protection	toll_fraud	{"known_fraud_patterns": ["wangiri", "irs_fraud"], "block_high_risk_destinations": true}	1	2	block	60	t	5	2026-04-27 20:17:38.152889+05	2026-04-27 20:17:38.152889+05	\N	\N
\.


ALTER TABLE public.abuse_detection_rules ENABLE TRIGGER ALL;

--
-- Data for Name: user_profiles; Type: TABLE DATA; Schema: public; Owner: -
--

ALTER TABLE public.user_profiles DISABLE TRIGGER ALL;

COPY public.user_profiles (id, email, name, tenant_id, role, password_hash, created_at, updated_at, account_locked_until, is_active, password_changed_at, failed_login_count, last_login_at, mfa_enabled, is_verified, verification_token, verification_token_expires_at, email_verified_at) FROM stdin;
8d69c1b3-70b4-414a-b3c2-c6ce54d41eb8	uk67013@gmail.com	SHOAIB MAJEED	76171092-ceac-4f7a-baab-af6d1bf4db68	tenant_admin	$2b$12$ojwVn/sH3vuIaRerp0y/mu80A/mugoIOuS9rcs0jULZ5bseJysYja	2026-02-19 21:08:46.646435+05	2026-04-27 20:17:37.622535+05	\N	t	\N	0	\N	f	t	\N	\N	2026-04-27 19:42:20.509944+05
06eecd88-c5e5-4942-bdda-cf38d076141b	codex-test2@example.com	SHOAIB MAJEED	f3d331de-c3e8-4990-a217-a9958cd49056	tenant_admin	$argon2id$v=19$m=19456,t=2,p=1$pAh89tnsXlTrGe2tcTUOSA$Du9h7olNiCaDhDlJAFmZmTfKTn0qsISiC6VxQM8P+J0	2026-03-30 18:00:25.816079+05	2026-04-27 20:17:37.622535+05	\N	t	\N	0	\N	f	t	\N	\N	2026-04-27 19:42:20.509944+05
805afb32-f989-4365-bd7c-411d57697e81	codex-test3@example.com	SHOAIB MAJEED	95f2c96b-efce-49a7-957a-55ee7ff64e7a	tenant_admin	$argon2id$v=19$m=19456,t=2,p=1$EHios6ah4jz4ZHG0gxaMyA$h0mPy//p0mXvUtZ4uRWBx7CKQMrruet5lDuIqLabjMY	2026-03-30 18:03:29.395969+05	2026-04-27 20:17:37.622535+05	\N	t	\N	0	\N	f	t	\N	\N	2026-04-27 19:42:20.509944+05
58c5c8e5-fc19-4805-ba2d-7b558bc73d08	testai@gmail.com	xyz	370b3a41-9987-4cfb-b491-2ea352966299	tenant_admin	$argon2id$v=19$m=19456,t=2,p=1$z0YVwrxF1h469c24zbJYTA$P1TNScsofgP0d9JQDNqcaBlQ8KQPn/hTdQ6K8yjMNlc	2026-03-31 16:46:17.516269+05	2026-04-27 20:17:37.622535+05	\N	t	\N	0	\N	f	t	\N	\N	2026-04-27 19:42:20.509944+05
a1ac0f22-dafd-4536-b341-303eda4ca230	xyz@gmail.com	xyz	0d1c717a-e887-4d0d-b528-cc2f702dff8d	tenant_admin	$argon2id$v=19$m=19456,t=2,p=1$fod/I1Kh/UyMKi7ZVP73Tw$vEhTD6r67FOH7EqVzSH6VJhe8O6vaHtugNL9ux3j5TU	2026-03-31 17:43:09.006652+05	2026-04-27 20:17:37.622535+05	\N	t	\N	0	\N	f	t	\N	\N	2026-04-27 19:42:20.509944+05
63dc2666-f880-4316-8063-cfa25f3f7fce	uziiikhan690@gmail.com	M UZAIR MAJEED	cf2bea16-dc16-4145-97a6-8d4bc2366a7b	tenant_admin	$argon2id$v=19$m=19456,t=2,p=1$fPJYGPPOkbns/PL0gLyjAw$AuLGMUqedWfHHTaSHnz1NJA60yAlDrgiOpclBfP43Bc	2026-03-07 22:13:26.836756+05	2026-04-27 20:17:37.622535+05	\N	t	\N	0	2026-04-02 19:26:31.19835+05	f	t	\N	\N	2026-04-27 19:42:20.509944+05
ce38fd78-e4b1-4681-bb85-21e342396d5e	uzairdevelops@gmail.com	SHOAIB MAJEED	532b5db0-2374-4579-a7ac-87bd2893016b	tenant_admin	$argon2id$v=19$m=19456,t=2,p=1$M08zbJO/GTzMZ6AMPGw4Zg$xgFD4al0phBHwmQYLS6Dz1Hf79MAaP3yxAqyuxVnrCM	2026-03-30 18:06:53.136965+05	2026-05-08 16:45:52.781848+05	\N	t	\N	0	2026-05-08 16:45:52.781848+05	f	t	\N	\N	2026-04-27 19:42:20.509944+05
\.


ALTER TABLE public.user_profiles ENABLE TRIGGER ALL;

--
-- Data for Name: abuse_events; Type: TABLE DATA; Schema: public; Owner: -
--

ALTER TABLE public.abuse_events DISABLE TRIGGER ALL;

COPY public.abuse_events (id, tenant_id, event_type, severity, source, description, details, resolved_at, resolved_by, resolution_note, created_at, updated_at) FROM stdin;
\.


ALTER TABLE public.abuse_events ENABLE TRIGGER ALL;

--
-- Data for Name: action_plans; Type: TABLE DATA; Schema: public; Owner: -
--

ALTER TABLE public.action_plans DISABLE TRIGGER ALL;

COPY public.action_plans (id, tenant_id, conversation_id, user_id, intent, context, actions, status, current_step, step_results, error, created_at, started_at, completed_at, updated_at) FROM stdin;
\.


ALTER TABLE public.action_plans ENABLE TRIGGER ALL;

--
-- Data for Name: alembic_version; Type: TABLE DATA; Schema: public; Owner: -
--

ALTER TABLE public.alembic_version DISABLE TRIGGER ALL;

COPY public.alembic_version (version_num) FROM stdin;
0001_baseline
\.


ALTER TABLE public.alembic_version ENABLE TRIGGER ALL;

--
-- Data for Name: assistant_conversations; Type: TABLE DATA; Schema: public; Owner: -
--

ALTER TABLE public.assistant_conversations DISABLE TRIGGER ALL;

COPY public.assistant_conversations (id, tenant_id, user_id, title, messages, context, status, message_count, started_at, last_message_at, created_at, updated_at) FROM stdin;
e6b208cb-6184-4cb2-bd36-88664c8c148d	76171092-ceac-4f7a-baab-af6d1bf4db68	8d69c1b3-70b4-414a-b3c2-c6ce54d41eb8	Show my campaign stats	[{"role": "user", "content": "Show my campaign stats", "timestamp": "2026-03-06T11:52:06.318120"}, {"role": "assistant", "content": "You have no campaigns.", "timestamp": "2026-03-06T11:52:08.343922"}]	{}	active	2	2026-03-06 16:52:08.345412+05	2026-03-06 16:52:08.345489+05	2026-03-06 16:52:08.474555+05	2026-03-06 17:43:14.935334+05
ae75e7aa-9ee8-4acb-9ecd-2ffa6bd8ad44	76171092-ceac-4f7a-baab-af6d1bf4db68	8d69c1b3-70b4-414a-b3c2-c6ce54d41eb8	hi	[{"role": "user", "content": "Show my campaign stats", "timestamp": "2026-03-06T11:52:06.318120"}, {"role": "assistant", "content": "You have no campaigns.", "timestamp": "2026-03-06T11:52:08.343922"}, {"role": "user", "content": "hi", "timestamp": "2026-03-06T11:52:20.545765"}, {"role": "assistant", "content": "Hello. How can I assist you today? Would you like to check your campaign stats, view recent calls, or something else?", "timestamp": "2026-03-06T11:52:20.968727"}]	{}	active	4	2026-03-06 16:52:20.968981+05	2026-03-06 16:52:20.969005+05	2026-03-06 16:52:21.035014+05	2026-03-06 17:43:14.935334+05
26b14fef-09fe-476f-ac1b-fa1b4e954543	76171092-ceac-4f7a-baab-af6d1bf4db68	8d69c1b3-70b4-414a-b3c2-c6ce54d41eb8	hi	[{"role": "user", "content": "hi", "timestamp": "2026-03-06T12:07:38.525459"}, {"role": "assistant", "content": "Hello. How can I help you today? Do you have any questions about your campaigns, leads, or usage? Or would you like to initiate a call or send a message?", "timestamp": "2026-03-06T12:07:40.151196"}]	{}	active	2	2026-03-06 17:07:40.151562+05	2026-03-06 17:07:40.15159+05	2026-03-06 17:07:40.21636+05	2026-03-06 17:43:14.935334+05
ba06028e-ae25-4bf3-b9b5-9412faa04888	76171092-ceac-4f7a-baab-af6d1bf4db68	8d69c1b3-70b4-414a-b3c2-c6ce54d41eb8	hi	[{"role": "user", "content": "hi", "timestamp": "2026-03-06T12:45:21.926809"}, {"role": "assistant", "content": "Hello. How can I help you today with your Talky.ai account? Do you have any questions about your calls, campaigns, or leads? Or would you like to take a specific action, such as sending an email or initiating a call?", "timestamp": "2026-03-06T12:45:23.228880"}]	{}	active	2	2026-03-06 17:45:23.229267+05	2026-03-06 17:45:23.229287+05	2026-03-06 17:45:23.295733+05	2026-03-06 17:45:23.295733+05
0c2c4b39-eb81-462a-9286-ffb9ff960825	76171092-ceac-4f7a-baab-af6d1bf4db68	8d69c1b3-70b4-414a-b3c2-c6ce54d41eb8	do you have whether info	[{"role": "user", "content": "do you have whether info", "timestamp": "2026-03-06T12:54:51.236586"}, {"role": "assistant", "content": "I don't have access to real-time weather information or current weather conditions. However, I can suggest checking a weather website or app for the most up-to-date and accurate weather information. If you have any other questions or need assistance with something else, feel free to ask.", "timestamp": "2026-03-06T12:54:52.485009"}, {"role": "user", "content": "hi", "timestamp": "2026-03-06T12:55:10.010283"}, {"role": "assistant", "content": "Hello. How can I assist you today? Would you like to check on your call statistics, leads, or something else?", "timestamp": "2026-03-06T12:55:10.461069"}, {"role": "user", "content": "what is the status of today", "timestamp": "2026-03-06T12:55:19.851626"}, {"role": "assistant", "content": "It seems like there are no calls made today yet. The success rate is 0% and there are no active campaigns. Would you like to start a new campaign or check on recent calls?", "timestamp": "2026-03-06T12:55:21.099529"}]	{}	active	6	2026-03-06 17:54:52.485717+05	2026-03-06 17:55:21.09994+05	2026-03-06 17:54:52.592233+05	2026-03-06 17:55:21.155683+05
082aa2fb-2f9f-4227-aec7-494675d7eb41	cf2bea16-dc16-4145-97a6-8d4bc2366a7b	63dc2666-f880-4316-8063-cfa25f3f7fce	hi	[{"role": "user", "content": "hi", "timestamp": "2026-03-09T09:35:13.584491"}, {"role": "assistant", "content": "Hello. How can I help you today? Would you like to check on your call statistics, leads, or something else?", "timestamp": "2026-03-09T09:35:14.679829"}]	{}	active	2	2026-03-09 14:35:14.680014+05	2026-03-09 14:35:14.680027+05	2026-03-09 14:35:14.791776+05	2026-03-09 14:35:14.791776+05
7968645a-d672-4d9f-8b82-5e35439ecba6	532b5db0-2374-4579-a7ac-87bd2893016b	ce38fd78-e4b1-4681-bb85-21e342396d5e	hello what is the update today	[{"role": "user", "content": "hello what is the update today", "timestamp": "2026-03-30T13:08:16.033349"}, {"role": "assistant", "content": "Hello. \\n\\nThere have been 0 calls made today, with 0 completed, 0 failed, and a success rate of 0%. There are 0 active campaigns.\\n\\nAs for the actions taken today, there have been 0 total actions, with no recent actions to report.", "timestamp": "2026-03-30T13:08:18.086904"}]	{}	active	2	2026-03-30 18:08:18.087224+05	2026-03-30 18:08:18.087236+05	2026-03-30 18:08:18.160755+05	2026-03-30 18:08:18.160755+05
6b966a31-7219-4b9a-9f92-5754f61a33b6	532b5db0-2374-4579-a7ac-87bd2893016b	ce38fd78-e4b1-4681-bb85-21e342396d5e	hi what is the status	[{"role": "user", "content": "hi what is the status", "timestamp": "2026-03-30T16:18:39.867547"}, {"role": "assistant", "content": "Today's call statistics show 1 total call, a success rate of 0%, and 0 active campaigns. The plan usage indicates that you have 300 minutes allocated, with 0 minutes used and 300 minutes remaining. Your subscription status is currently inactive.", "timestamp": "2026-03-30T16:18:46.535502"}]	{}	active	2	2026-03-30 21:18:46.536042+05	2026-03-30 21:18:46.536133+05	2026-03-30 21:18:46.704916+05	2026-03-30 21:18:46.704916+05
a9e45d70-8997-4c62-a7f3-f4f09bb48e60	0d1c717a-e887-4d0d-b528-cc2f702dff8d	a1ac0f22-dafd-4536-b341-303eda4ca230	hi waht is the status	[{"role": "user", "content": "hi waht is the status", "timestamp": "2026-03-31T12:45:45.903852"}, {"role": "assistant", "content": "Your current plan is 'Basic' with 300 minutes allocated, 0 minutes used, and 300 minutes remaining. Your subscription status is inactive. \\n\\nAs for today's call statistics, there have been 0 total calls, with 0 completed and 0 failed calls. The success rate is 0%, and there are 0 active campaigns.", "timestamp": "2026-03-31T12:45:48.964150"}]	{}	active	2	2026-03-31 17:45:48.964762+05	2026-03-31 17:45:48.964789+05	2026-03-31 17:45:49.087904+05	2026-03-31 17:45:49.087904+05
aad66719-9cc7-4474-b2cf-b344d36c435b	532b5db0-2374-4579-a7ac-87bd2893016b	ce38fd78-e4b1-4681-bb85-21e342396d5e	How many calls did I make today?	[{"role": "user", "content": "How many calls did I make today?", "timestamp": "2026-04-23T13:01:34.558770"}, {"role": "assistant", "content": "You made 1 call today.", "timestamp": "2026-04-23T13:01:36.359480"}, {"role": "user", "content": "what we have talked about that on that call", "timestamp": "2026-04-23T13:02:07.259114"}, {"role": "assistant", "content": "The recent call made today was to phone number \\"1002\\" and the call status is \\"initiated\\" but the outcome is unknown.", "timestamp": "2026-04-23T13:02:09.218197"}]	{}	active	4	2026-04-23 18:01:36.360701+05	2026-04-23 18:02:09.218543+05	2026-04-23 18:01:36.538897+05	2026-04-23 18:02:09.289635+05
80489292-8fcc-4490-ab1b-36598f10badc	532b5db0-2374-4579-a7ac-87bd2893016b	ce38fd78-e4b1-4681-bb85-21e342396d5e	hi	[{"role": "user", "content": "hi", "timestamp": "2026-05-06T13:07:40.113919"}, {"role": "assistant", "content": "Hello. How can I help you today? Do you need help with something related to your Talky.ai account, or would you like to check on some statistics or lead information?", "timestamp": "2026-05-06T13:07:41.787273"}]	{}	active	2	2026-05-06 18:07:41.787453+05	2026-05-06 18:07:41.787464+05	2026-05-06 18:07:41.834138+05	2026-05-06 18:07:41.834138+05
429c30ef-0cee-4765-ad42-2b014bb8a9fa	532b5db0-2374-4579-a7ac-87bd2893016b	ce38fd78-e4b1-4681-bb85-21e342396d5e	hi	[{"role": "user", "content": "hi", "timestamp": "2026-05-06T13:16:15.154867"}, {"role": "assistant", "content": "Hello. How can I help you today? Would you like to check on your call statistics, leads, or something else?", "timestamp": "2026-05-06T13:16:16.859438"}]	{}	active	2	2026-05-06 18:16:16.859903+05	2026-05-06 18:16:16.859923+05	2026-05-06 18:16:16.90648+05	2026-05-06 18:16:16.90648+05
\.


ALTER TABLE public.assistant_conversations ENABLE TRIGGER ALL;

--
-- Data for Name: campaigns; Type: TABLE DATA; Schema: public; Owner: -
--

ALTER TABLE public.campaigns DISABLE TRIGGER ALL;

COPY public.campaigns (id, tenant_id, name, description, status, system_prompt, voice_id, max_concurrent_calls, retry_failed, max_retries, goal, script_config, calling_config, created_at, started_at, completed_at, total_leads, calls_completed, calls_failed, updated_at) FROM stdin;
