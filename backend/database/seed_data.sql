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
\.


ALTER TABLE public.assistant_conversations ENABLE TRIGGER ALL;

--
-- Data for Name: campaigns; Type: TABLE DATA; Schema: public; Owner: -
--

ALTER TABLE public.campaigns DISABLE TRIGGER ALL;

COPY public.campaigns (id, tenant_id, name, description, status, system_prompt, voice_id, max_concurrent_calls, retry_failed, max_retries, goal, script_config, calling_config, created_at, started_at, completed_at, total_leads, calls_completed, calls_failed, updated_at) FROM stdin;
