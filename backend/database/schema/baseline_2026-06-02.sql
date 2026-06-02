--
-- PostgreSQL database dump
--

\restrict WthdvmB2Ew7ahIziU3gjqwSr9zUkYrnyrSAnGDhy8bZSgKAActWC7ZdVHtJBfkC

-- Dumped from database version 15.17
-- Dumped by pg_dump version 16.14 (Ubuntu 16.14-0ubuntu0.24.04.1)

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
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--
-- Name: EXTENSION pgcrypto; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';


--
-- Name: uuid-ossp; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public;


--
-- Name: EXTENSION "uuid-ossp"; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION "uuid-ossp" IS 'generate universally unique identifiers (UUIDs)';


--
-- Name: apply_audit_retention(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apply_audit_retention() RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- Archive and delete expired audit logs
    -- In production, this would move to cold storage before deletion
    DELETE FROM audit_logs WHERE retention_until < CURRENT_DATE - INTERVAL '30 days';
END;
$$;


--
-- Name: cleanup_expired_idempotency_keys(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.cleanup_expired_idempotency_keys() RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM idempotency_keys
    WHERE expires_at < NOW();

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$;


--
-- Name: log_audit_event(uuid, uuid, character varying, character varying, uuid, text, jsonb, inet, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.log_audit_event(p_user_id uuid, p_tenant_id uuid, p_action_type character varying, p_target_type character varying, p_target_id uuid, p_description text, p_metadata jsonb, p_ip_address inet, p_user_agent text) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    AS $$
DECLARE
    v_id UUID;
BEGIN
    INSERT INTO audit_logs (
        user_id, tenant_id, action_type, target_type, target_id, 
        description, metadata, ip_address, user_agent
    ) VALUES (
        p_user_id, p_tenant_id, p_action_type, p_target_type, p_target_id,
        p_description, p_metadata, p_ip_address, p_user_agent
    ) RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;


--
-- Name: tenant_phone_numbers_touch_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.tenant_phone_numbers_touch_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


--
-- Name: update_call_status(uuid, text, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_call_status(p_call_uuid uuid, p_outcome text, p_duration integer DEFAULT NULL::integer) RETURNS json
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_call RECORD;
    v_lead_status TEXT;
BEGIN
    UPDATE calls SET
        status = 'completed',
        outcome = p_outcome,
        duration_seconds = COALESCE(p_duration, duration_seconds),
        ended_at = NOW(),
        updated_at = NOW()
    WHERE id = p_call_uuid
    RETURNING id, lead_id, action_plan_id, campaign_id
    INTO v_call;

    IF v_call.id IS NULL THEN
        RETURN json_build_object('found', false, 'call_id', p_call_uuid);
    END IF;

    CASE p_outcome
        WHEN 'answered' THEN v_lead_status := 'contacted';
        WHEN 'goal_achieved' THEN v_lead_status := 'completed';
        WHEN 'spam', 'invalid', 'unavailable', 'disconnected', 'rejected' THEN v_lead_status := 'dnc';
        ELSE v_lead_status := 'called';
    END CASE;

    IF v_call.lead_id IS NOT NULL THEN
        UPDATE leads SET
            status = v_lead_status,
            last_call_result = p_outcome,
            last_called_at = NOW(),
            call_attempts = COALESCE(call_attempts, 0) + 1,
            updated_at = NOW()
        WHERE id = v_call.lead_id;
    END IF;

    RETURN json_build_object(
        'found', true,
        'call_id', v_call.id,
        'lead_id', v_call.lead_id,
        'campaign_id', v_call.campaign_id,
        'outcome', p_outcome,
        'lead_status', v_lead_status
    );
END;
$$;


--
-- Name: update_updated_at_column(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_updated_at_column() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: abuse_detection_rules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.abuse_detection_rules (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid,
    rule_name text NOT NULL,
    rule_type text NOT NULL,
    parameters jsonb DEFAULT '{}'::jsonb NOT NULL,
    warn_threshold integer,
    block_threshold integer,
    action_on_trigger text DEFAULT 'flag'::text NOT NULL,
    analysis_window_minutes integer DEFAULT 60,
    is_active boolean DEFAULT true,
    priority integer DEFAULT 100,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    created_by uuid,
    updated_by uuid,
    CONSTRAINT abuse_detection_rules_action_on_trigger_check CHECK ((action_on_trigger = ANY (ARRAY['flag'::text, 'warn'::text, 'throttle'::text, 'block'::text, 'suspend'::text]))),
    CONSTRAINT abuse_detection_rules_rule_type_check CHECK ((rule_type = ANY (ARRAY['velocity_spike'::text, 'short_duration_pattern'::text, 'repeat_number'::text, 'sequential_dialing'::text, 'premium_rate'::text, 'international_spike'::text, 'after_hours'::text, 'geographic_impossibility'::text, 'account_hopping'::text, 'toll_fraud'::text, 'wangiri'::text, 'irs_fraud'::text])))
);


--
-- Name: abuse_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.abuse_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    event_type character varying(50) DEFAULT 'velocity_anomaly'::character varying NOT NULL,
    severity character varying(20) DEFAULT 'medium'::character varying NOT NULL,
    source character varying(50) DEFAULT 'system'::character varying NOT NULL,
    description text,
    details jsonb DEFAULT '{}'::jsonb NOT NULL,
    resolved_at timestamp with time zone,
    resolved_by uuid,
    resolution_note text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT abuse_events_severity_check CHECK (((severity)::text = ANY (ARRAY[('low'::character varying)::text, ('medium'::character varying)::text, ('high'::character varying)::text, ('critical'::character varying)::text])))
);


--
-- Name: action_plans; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.action_plans (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    conversation_id uuid,
    user_id uuid,
    intent text NOT NULL,
    context jsonb DEFAULT '{}'::jsonb,
    actions jsonb DEFAULT '[]'::jsonb NOT NULL,
    status character varying(50) DEFAULT 'pending'::character varying,
    current_step integer DEFAULT 0,
    step_results jsonb DEFAULT '[]'::jsonb,
    error text,
    created_at timestamp with time zone DEFAULT now(),
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: suspension_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.suspension_events (
    suspension_id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    target_type character varying(20) NOT NULL,
    target_id uuid NOT NULL,
    suspension_type character varying(30) NOT NULL,
    reason_category character varying(50) NOT NULL,
    reason_description text NOT NULL,
    evidence jsonb,
    suspended_at timestamp with time zone DEFAULT now() NOT NULL,
    suspended_until timestamp with time zone,
    restored_at timestamp with time zone,
    suspended_by uuid,
    restored_by uuid,
    restore_reason text,
    is_active boolean DEFAULT true NOT NULL,
    propagated_services character varying(50)[],
    propagation_confirmed_at timestamp with time zone,
    appeal_submitted_at timestamp with time zone,
    appeal_reason text,
    appeal_reviewed_by uuid,
    appeal_decision character varying(20),
    appeal_response text,
    audit_log_id uuid,
    CONSTRAINT suspension_events_target_type_check CHECK (((target_type)::text = ANY (ARRAY[('user'::character varying)::text, ('tenant'::character varying)::text, ('partner'::character varying)::text])))
);


--
-- Name: active_suspensions; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.active_suspensions AS
 SELECT suspension_events.suspension_id,
    suspension_events.created_at,
    suspension_events.target_type,
    suspension_events.target_id,
    suspension_events.suspension_type,
    suspension_events.reason_category,
    suspension_events.reason_description,
    suspension_events.evidence,
    suspension_events.suspended_at,
    suspension_events.suspended_until,
    suspension_events.restored_at,
    suspension_events.suspended_by,
    suspension_events.restored_by,
    suspension_events.restore_reason,
    suspension_events.is_active,
    suspension_events.propagated_services,
    suspension_events.propagation_confirmed_at,
    suspension_events.appeal_submitted_at,
    suspension_events.appeal_reason,
    suspension_events.appeal_reviewed_by,
    suspension_events.appeal_decision,
    suspension_events.appeal_response,
    suspension_events.audit_log_id
   FROM public.suspension_events
  WHERE ((suspension_events.is_active = true) AND ((suspension_events.suspended_until IS NULL) OR (suspension_events.suspended_until > now())));


--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


--
-- Name: assistant_actions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.assistant_actions (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    conversation_id uuid,
    user_id uuid,
    call_id uuid,
    lead_id uuid,
    campaign_id uuid,
    connector_id uuid,
    type character varying(50) NOT NULL,
    status character varying(50) DEFAULT 'pending'::character varying,
    input_data jsonb,
    output_data jsonb,
    error text,
    triggered_by character varying(50),
    scheduled_at timestamp with time zone,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    duration_ms integer,
    ip_address inet,
    user_agent text,
    request_id uuid,
    outcome_status character varying(50),
    idempotency_key character varying(255),
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: assistant_conversations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.assistant_conversations (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    user_id uuid,
    title character varying(255),
    messages jsonb DEFAULT '[]'::jsonb,
    context jsonb DEFAULT '{}'::jsonb,
    status character varying(50) DEFAULT 'active'::character varying,
    message_count integer DEFAULT 0,
    started_at timestamp with time zone DEFAULT now(),
    last_message_at timestamp with time zone DEFAULT now(),
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: audit_chain_state; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_chain_state (
    id integer NOT NULL,
    last_event_id uuid,
    last_event_hash character varying(64) NOT NULL,
    events_count bigint DEFAULT 0 NOT NULL,
    verified_at timestamp with time zone,
    verification_result boolean,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: audit_chain_state_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.audit_chain_state_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: audit_chain_state_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.audit_chain_state_id_seq OWNED BY public.audit_chain_state.id;


--
-- Name: audit_event_types; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_event_types (
    event_type character varying(50) NOT NULL,
    category character varying(30) NOT NULL,
    severity character varying(10) NOT NULL,
    description text,
    retention_days integer NOT NULL
);


--
-- Name: audit_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_logs (
    event_id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    event_time timestamp with time zone DEFAULT now() NOT NULL,
    event_type character varying(50) NOT NULL,
    event_category character varying(30) NOT NULL,
    severity character varying(10) DEFAULT 'INFO'::character varying NOT NULL,
    actor_id uuid,
    actor_type character varying(20) DEFAULT 'user'::character varying NOT NULL,
    actor_role character varying(50),
    tenant_id uuid,
    resource_type character varying(50),
    resource_id uuid,
    ip_address inet,
    user_agent text,
    session_id uuid,
    device_fingerprint character varying(64),
    country_code character(2),
    action character varying(100) NOT NULL,
    description text,
    before_state jsonb,
    after_state jsonb,
    metadata jsonb,
    previous_hash character varying(64),
    entry_hash character varying(64) NOT NULL,
    signature character varying(128),
    compliance_tags character varying(50)[],
    retention_until date NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: call_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.call_events (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    call_id uuid NOT NULL,
    talklee_call_id character varying(20),
    leg_id uuid,
    event_type character varying(30) NOT NULL,
    previous_state character varying(30),
    new_state character varying(30),
    event_data jsonb DEFAULT '{}'::jsonb,
    source character varying(50) DEFAULT 'system'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: call_guard_decisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.call_guard_decisions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    partner_id uuid,
    call_id character varying(100),
    phone_number character varying(20),
    decision character varying(20) NOT NULL,
    checks_performed jsonb DEFAULT '[]'::jsonb NOT NULL,
    failed_checks jsonb DEFAULT '[]'::jsonb NOT NULL,
    queue_position integer,
    retry_after_seconds integer,
    total_latency_ms integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT call_guard_decisions_decision_check CHECK (((decision)::text = ANY (ARRAY[('allow'::character varying)::text, ('block'::character varying)::text, ('queue'::character varying)::text, ('throttle'::character varying)::text])))
);


--
-- Name: call_legs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.call_legs (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    call_id uuid NOT NULL,
    talklee_call_id character varying(20),
    leg_type character varying(30) NOT NULL,
    direction character varying(10) DEFAULT 'outbound'::character varying NOT NULL,
    provider character varying(30) DEFAULT 'vonage'::character varying NOT NULL,
    provider_leg_id character varying(100),
    from_number character varying(20),
    to_number character varying(20),
    status character varying(30) DEFAULT 'initiated'::character varying NOT NULL,
    started_at timestamp with time zone,
    answered_at timestamp with time zone,
    ended_at timestamp with time zone,
    duration_seconds integer,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: call_velocity_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.call_velocity_snapshots (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    window_start timestamp with time zone NOT NULL,
    window_end timestamp with time zone NOT NULL,
    total_calls integer DEFAULT 0,
    unique_numbers integer DEFAULT 0,
    international_calls integer DEFAULT 0,
    premium_calls integer DEFAULT 0,
    short_duration_calls integer DEFAULT 0,
    top_destinations jsonb DEFAULT '[]'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: calls; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.calls (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    campaign_id uuid NOT NULL,
    lead_id uuid NOT NULL,
    phone_number character varying(20) NOT NULL,
    external_call_uuid character varying(100),
    status character varying(50) DEFAULT 'initiated'::character varying NOT NULL,
    outcome character varying(100),
    goal_achieved boolean DEFAULT false,
    started_at timestamp with time zone,
    answered_at timestamp with time zone,
    ended_at timestamp with time zone,
    duration_seconds integer,
    recording_url text,
    transcript text,
    transcript_json jsonb,
    summary text,
    cost numeric(10,4),
    talklee_call_id character varying(20),
    crm_call_id text,
    crm_note_id text,
    crm_synced_at timestamp with time zone,
    detected_intents jsonb DEFAULT '[]'::jsonb,
    action_plan_id uuid,
    action_results jsonb DEFAULT '{}'::jsonb,
    pending_recommendations text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: campaigns; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.campaigns (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    status character varying(50) DEFAULT 'draft'::character varying NOT NULL,
    system_prompt text DEFAULT ''::text NOT NULL,
    voice_id character varying(100) DEFAULT 'default'::character varying NOT NULL,
    max_concurrent_calls integer DEFAULT 10,
    retry_failed boolean DEFAULT true,
    max_retries integer DEFAULT 3,
    goal text,
    script_config jsonb DEFAULT '{}'::jsonb,
    calling_config jsonb DEFAULT '{"caller_id": null, "retry_on_busy": true, "priority_override": null, "retry_on_no_answer": true}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    total_leads integer DEFAULT 0,
    calls_completed integer DEFAULT 0,
    calls_failed integer DEFAULT 0,
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: clients; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.clients (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid,
    name character varying(255) NOT NULL,
    company character varying(255),
    phone character varying(20),
    email character varying(255),
    tags jsonb DEFAULT '[]'::jsonb,
    notes text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: connector_accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.connector_accounts (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    connector_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    external_account_id character varying(255),
    access_token_encrypted text,
    refresh_token_encrypted text,
    token_expires_at timestamp with time zone,
    scopes text[],
    account_email character varying(255),
    status character varying(50) DEFAULT 'active'::character varying,
    last_refreshed_at timestamp with time zone,
    token_last_rotated_at timestamp with time zone,
    rotation_count integer DEFAULT 0,
    revoked_at timestamp with time zone,
    revoked_reason text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: connectors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.connectors (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    type character varying(50) NOT NULL,
    provider character varying(50) NOT NULL,
    name character varying(100),
    status character varying(50) DEFAULT 'pending'::character varying,
    config jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: conversations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conversations (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    call_id uuid NOT NULL,
    messages jsonb DEFAULT '[]'::jsonb,
    started_at timestamp with time zone DEFAULT now(),
    ended_at timestamp with time zone,
    status character varying(50) DEFAULT 'active'::character varying,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: dialer_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dialer_jobs (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    campaign_id uuid NOT NULL,
    lead_id uuid NOT NULL,
    call_id uuid,
    phone_number character varying(20) NOT NULL,
    priority integer DEFAULT 5,
    status character varying(50) DEFAULT 'pending'::character varying,
    attempt_number integer DEFAULT 1,
    scheduled_at timestamp with time zone DEFAULT now(),
    processed_at timestamp with time zone,
    completed_at timestamp with time zone,
    last_outcome character varying(50),
    last_error text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    failure_category text,
    failure_reason text,
    CONSTRAINT dialer_jobs_priority_check CHECK (((priority >= 1) AND (priority <= 10)))
);


--
-- Name: COLUMN dialer_jobs.failure_category; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dialer_jobs.failure_category IS 'Track 2 retry classifier output: one of transient_network, auth_gate, carrier_reject, invalid_input, internal. NULL for jobs that never failed or were written before the classifier shipped.';


--
-- Name: COLUMN dialer_jobs.failure_reason; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.dialer_jobs.failure_reason IS 'Track 2 retry classifier output: fine-grained snake_case reason string. Typically the bridge error.code (e.g. caller_id_not_verified) or http_<status> for status-based fallbacks.';


--
-- Name: dnc_entries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dnc_entries (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid,
    normalized_number character varying(20) NOT NULL,
    source character varying(50) DEFAULT 'manual'::character varying NOT NULL,
    reason text,
    added_by uuid,
    expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: emergency_access_requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.emergency_access_requests (
    request_id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    requestor_id uuid NOT NULL,
    scenario character varying(50) NOT NULL,
    justification text NOT NULL,
    requested_access text[] NOT NULL,
    approvers_required integer DEFAULT 2 NOT NULL,
    approvals jsonb DEFAULT '[]'::jsonb,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    approved_at timestamp with time zone,
    expires_at timestamp with time zone NOT NULL,
    session_created_at timestamp with time zone,
    session_terminated_at timestamp with time zone,
    session_token_hash character varying(64),
    actions_taken jsonb DEFAULT '[]'::jsonb,
    reviewed_at timestamp with time zone,
    reviewed_by uuid,
    review_notes text,
    CONSTRAINT emergency_access_requests_status_check CHECK (((status)::text = ANY (ARRAY[('pending'::character varying)::text, ('approved'::character varying)::text, ('denied'::character varying)::text, ('expired'::character varying)::text, ('used'::character varying)::text, ('cancelled'::character varying)::text])))
);


--
-- Name: tenant_secrets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenant_secrets (
    secret_id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    tenant_id uuid,
    created_by uuid,
    secret_type character varying(30) NOT NULL,
    secret_name character varying(100) NOT NULL,
    description text,
    encrypted_value bytea NOT NULL,
    encrypted_dek bytea NOT NULL,
    iv bytea NOT NULL,
    algorithm character varying(20) DEFAULT 'AES-256-GCM'::character varying NOT NULL,
    permissions jsonb DEFAULT '{}'::jsonb,
    version integer DEFAULT 1 NOT NULL,
    rotated_from uuid,
    rotated_to uuid,
    rotated_at timestamp with time zone,
    expires_at timestamp with time zone,
    last_accessed_at timestamp with time zone,
    last_accessed_by uuid,
    access_count integer DEFAULT 0 NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    is_compromised boolean DEFAULT false NOT NULL,
    revoked_at timestamp with time zone,
    revoked_reason text
);


--
-- Name: expiring_secrets; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.expiring_secrets AS
 SELECT tenant_secrets.secret_id,
    tenant_secrets.created_at,
    tenant_secrets.updated_at,
    tenant_secrets.tenant_id,
    tenant_secrets.created_by,
    tenant_secrets.secret_type,
    tenant_secrets.secret_name,
    tenant_secrets.description,
    tenant_secrets.encrypted_value,
    tenant_secrets.encrypted_dek,
    tenant_secrets.iv,
    tenant_secrets.algorithm,
    tenant_secrets.permissions,
    tenant_secrets.version,
    tenant_secrets.rotated_from,
    tenant_secrets.rotated_to,
    tenant_secrets.rotated_at,
    tenant_secrets.expires_at,
    tenant_secrets.last_accessed_at,
    tenant_secrets.last_accessed_by,
    tenant_secrets.access_count,
    tenant_secrets.is_active,
    tenant_secrets.is_compromised,
    tenant_secrets.revoked_at,
    tenant_secrets.revoked_reason
   FROM public.tenant_secrets
  WHERE ((tenant_secrets.is_active = true) AND (tenant_secrets.expires_at IS NOT NULL) AND (tenant_secrets.expires_at < (now() + '7 days'::interval)));


--
-- Name: invoices; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.invoices (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    stripe_invoice_id character varying(100) NOT NULL,
    stripe_subscription_id character varying(100),
    amount_due integer NOT NULL,
    amount_paid integer DEFAULT 0 NOT NULL,
    currency character varying(10) DEFAULT 'usd'::character varying,
    status character varying(50) NOT NULL,
    invoice_pdf text,
    hosted_invoice_url text,
    period_start timestamp with time zone,
    period_end timestamp with time zone,
    due_date timestamp with time zone,
    paid_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: leads; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.leads (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    campaign_id uuid NOT NULL,
    phone_number character varying(20) NOT NULL,
    first_name character varying(100),
    last_name character varying(100),
    email character varying(255),
    custom_fields jsonb DEFAULT '{}'::jsonb,
    priority integer DEFAULT 5,
    is_high_value boolean DEFAULT false,
    tags text[] DEFAULT '{}'::text[],
    status character varying(50) DEFAULT 'pending'::character varying,
    last_call_result character varying(50) DEFAULT 'pending'::character varying,
    call_attempts integer DEFAULT 0,
    last_called_at timestamp with time zone,
    crm_contact_id text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT leads_priority_check CHECK (((priority >= 1) AND (priority <= 10)))
);


--
-- Name: login_attempts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.login_attempts (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    email text NOT NULL,
    user_id uuid,
    ip_address text NOT NULL,
    user_agent text,
    success boolean NOT NULL,
    failure_reason text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: meetings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.meetings (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    lead_id uuid,
    call_id uuid,
    connector_id uuid,
    action_id uuid,
    external_event_id character varying(255),
    title character varying(255) NOT NULL,
    description text,
    start_time timestamp with time zone NOT NULL,
    end_time timestamp with time zone NOT NULL,
    timezone character varying(50) DEFAULT 'UTC'::character varying,
    location text,
    join_link text,
    status character varying(50) DEFAULT 'scheduled'::character varying,
    attendees jsonb DEFAULT '[]'::jsonb,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: mfa_challenges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mfa_challenges (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    user_id uuid NOT NULL,
    challenge_hash text NOT NULL,
    ip_address text,
    user_agent text,
    expires_at timestamp with time zone NOT NULL,
    used boolean DEFAULT false NOT NULL,
    used_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    CONSTRAINT chk_mfa_challenge_expires_after_created CHECK ((expires_at > created_at)),
    CONSTRAINT chk_mfa_challenge_used_at CHECK ((((used = false) AND (used_at IS NULL)) OR ((used = true) AND (used_at IS NOT NULL)))),
    CONSTRAINT mfa_challenges_attempts_bounds CHECK (((attempts >= 0) AND (attempts <= 100)))
);


--
-- Name: security_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.security_events (
    event_id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    event_type character varying(50) NOT NULL,
    severity character varying(10) NOT NULL,
    status character varying(20) DEFAULT 'open'::character varying NOT NULL,
    tenant_id uuid,
    user_id uuid,
    session_id uuid,
    detection_source character varying(50) NOT NULL,
    rule_id uuid,
    title character varying(200) NOT NULL,
    description text,
    evidence jsonb,
    assigned_to uuid,
    resolved_at timestamp with time zone,
    resolved_by uuid,
    resolution_notes text,
    auto_action_taken character varying(50),
    auto_action_success boolean,
    sla_deadline timestamp with time zone,
    first_response_at timestamp with time zone,
    alert_type text,
    CONSTRAINT security_events_alert_type_check CHECK (((alert_type IS NULL) OR (alert_type = ANY (ARRAY['Network'::text, 'API'::text, 'Campaign'::text, 'System'::text])))),
    CONSTRAINT security_events_severity_check CHECK (((severity)::text = ANY (ARRAY[('CRITICAL'::character varying)::text, ('HIGH'::character varying)::text, ('MEDIUM'::character varying)::text, ('LOW'::character varying)::text, ('INFO'::character varying)::text]))),
    CONSTRAINT security_events_status_check CHECK (((status)::text = ANY (ARRAY[('open'::character varying)::text, ('investigating'::character varying)::text, ('resolved'::character varying)::text, ('false_positive'::character varying)::text, ('escalated'::character varying)::text])))
);


--
-- Name: open_security_events; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.open_security_events AS
 SELECT security_events.event_id,
    security_events.created_at,
    security_events.event_type,
    security_events.severity,
    security_events.status,
    security_events.tenant_id,
    security_events.user_id,
    security_events.session_id,
    security_events.detection_source,
    security_events.rule_id,
    security_events.title,
    security_events.description,
    security_events.evidence,
    security_events.assigned_to,
    security_events.resolved_at,
    security_events.resolved_by,
    security_events.resolution_notes,
    security_events.auto_action_taken,
    security_events.auto_action_success,
    security_events.sla_deadline,
    security_events.first_response_at
   FROM public.security_events
  WHERE ((security_events.status)::text = ANY (ARRAY[('open'::character varying)::text, ('investigating'::character varying)::text, ('escalated'::character varying)::text]));


--
-- Name: partner_limits; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.partner_limits (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    partner_id uuid NOT NULL,
    max_tenants integer DEFAULT 10 NOT NULL,
    current_tenant_count integer DEFAULT 0 NOT NULL,
    aggregate_calls_per_minute integer DEFAULT 600 NOT NULL,
    aggregate_calls_per_hour integer DEFAULT 10000 NOT NULL,
    aggregate_calls_per_day integer DEFAULT 100000 NOT NULL,
    aggregate_concurrent_calls integer DEFAULT 100 NOT NULL,
    revenue_share_percent numeric(5,2) DEFAULT 20.0 NOT NULL,
    min_billing_amount numeric(12,4) DEFAULT 100.0 NOT NULL,
    max_billing_amount numeric(12,4),
    feature_whitelist text[] DEFAULT ARRAY[]::text[] NOT NULL,
    feature_blacklist text[] DEFAULT ARRAY[]::text[] NOT NULL,
    fraud_detection_sensitivity integer DEFAULT 50 NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT partner_limits_aggregate_calls_per_day_check CHECK ((aggregate_calls_per_day > 0)),
    CONSTRAINT partner_limits_aggregate_calls_per_hour_check CHECK ((aggregate_calls_per_hour > 0)),
    CONSTRAINT partner_limits_aggregate_calls_per_minute_check CHECK ((aggregate_calls_per_minute > 0)),
    CONSTRAINT partner_limits_aggregate_concurrent_calls_check CHECK ((aggregate_concurrent_calls > 0)),
    CONSTRAINT partner_limits_fraud_detection_sensitivity_check CHECK (((fraud_detection_sensitivity >= 0) AND (fraud_detection_sensitivity <= 100))),
    CONSTRAINT partner_limits_max_tenants_check CHECK ((max_tenants > 0)),
    CONSTRAINT partner_limits_revenue_share_percent_check CHECK (((revenue_share_percent >= (0)::numeric) AND (revenue_share_percent <= (100)::numeric)))
);


--
-- Name: permissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.permissions (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    name character varying(100) NOT NULL,
    description text,
    resource character varying(50) NOT NULL,
    action character varying(50) NOT NULL,
    is_system boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: plans; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.plans (
    id character varying(50) NOT NULL,
    name character varying(100) NOT NULL,
    price numeric(10,2) NOT NULL,
    description text,
    minutes integer NOT NULL,
    agents integer DEFAULT 1 NOT NULL,
    concurrent_calls integer DEFAULT 1 NOT NULL,
    features jsonb DEFAULT '[]'::jsonb,
    not_included jsonb DEFAULT '[]'::jsonb,
    popular boolean DEFAULT false,
    stripe_price_id character varying(100),
    stripe_product_id character varying(100),
    billing_period character varying(20) DEFAULT 'monthly'::character varying,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: rate_limit_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rate_limit_events (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tier text NOT NULL,
    scope_key text NOT NULL,
    endpoint text,
    action_taken text NOT NULL,
    limit_config jsonb,
    triggered_at timestamp with time zone DEFAULT now(),
    CONSTRAINT rate_limit_events_action_taken_check CHECK ((action_taken = ANY (ARRAY['allow'::text, 'warn'::text, 'throttle'::text, 'block'::text]))),
    CONSTRAINT rate_limit_events_tier_check CHECK ((tier = ANY (ARRAY['ip'::text, 'user'::text, 'tenant'::text, 'global'::text])))
);


--
-- Name: recordings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.recordings (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid,
    call_id uuid NOT NULL,
    storage_path text NOT NULL,
    duration_seconds integer,
    file_size_bytes bigint,
    mime_type character varying(50) DEFAULT 'audio/wav'::character varying,
    status character varying(50) DEFAULT 'pending'::character varying,
    drive_file_id text,
    drive_web_link text,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: recordings_s3; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.recordings_s3 (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    call_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    campaign_id uuid,
    s3_bucket character varying(255) NOT NULL,
    s3_key character varying(1024) NOT NULL,
    s3_region character varying(64) DEFAULT 'us-east-1'::character varying NOT NULL,
    storage_provider character varying(32) DEFAULT 's3'::character varying NOT NULL,
    file_size_bytes bigint,
    duration_seconds integer,
    mime_type character varying(64) DEFAULT 'audio/wav'::character varying NOT NULL,
    status character varying(32) DEFAULT 'uploaded'::character varying NOT NULL,
    upload_started_at timestamp with time zone,
    upload_finished_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT recordings_s3_status_check CHECK (((status)::text = ANY (ARRAY[('uploading'::character varying)::text, ('uploaded'::character varying)::text, ('failed'::character varying)::text, ('deleted'::character varying)::text])))
);


--
-- Name: recovery_codes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.recovery_codes (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    user_id uuid NOT NULL,
    code_hash text NOT NULL,
    batch_id uuid NOT NULL,
    used boolean DEFAULT false NOT NULL,
    used_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_recovery_codes_used_at CHECK ((((used = false) AND (used_at IS NULL)) OR ((used = true) AND (used_at IS NOT NULL))))
);


--
-- Name: refresh_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.refresh_tokens (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    family_id uuid NOT NULL,
    user_id uuid NOT NULL,
    tenant_id uuid,
    token_hash text NOT NULL,
    parent_id uuid,
    issued_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    revoked_at timestamp with time zone,
    revoked_reason text,
    ip inet,
    user_agent text,
    CONSTRAINT chk_rt_expires_after_issued CHECK ((expires_at > issued_at)),
    CONSTRAINT refresh_tokens_revoked_reason_check CHECK (((revoked_reason IS NULL) OR (revoked_reason = ANY (ARRAY['rotated'::text, 'reuse_detected'::text, 'logout'::text, 'admin'::text, 'expired'::text]))))
);


--
-- Name: reminders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reminders (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    meeting_id uuid,
    lead_id uuid,
    action_id uuid,
    type character varying(50) NOT NULL,
    status character varying(50) DEFAULT 'pending'::character varying,
    scheduled_at timestamp with time zone NOT NULL,
    sent_at timestamp with time zone,
    content jsonb,
    error text,
    retry_count integer DEFAULT 0,
    idempotency_key character varying(255),
    max_retries integer DEFAULT 3,
    next_retry_at timestamp with time zone,
    last_error text,
    channel character varying(20),
    external_message_id character varying(255),
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: role_permissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.role_permissions (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    role_id uuid NOT NULL,
    permission_id uuid NOT NULL,
    granted_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.roles (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    name character varying(50) NOT NULL,
    description text,
    level integer NOT NULL,
    is_system_role boolean DEFAULT false NOT NULL,
    tenant_scoped boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_roles_level_positive CHECK ((level > 0))
);


--
-- Name: secret_access_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.secret_access_log (
    access_id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    accessed_at timestamp with time zone DEFAULT now() NOT NULL,
    secret_id uuid NOT NULL,
    tenant_id uuid,
    accessed_by uuid,
    access_type character varying(30) NOT NULL,
    access_reason text,
    ip_address inet,
    user_agent text,
    success boolean NOT NULL,
    failure_reason text,
    api_key_prefix character varying(16),
    presented_permission character varying(50)
);


--
-- Name: security_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.security_sessions (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    user_id uuid NOT NULL,
    session_token_hash text NOT NULL,
    ip_address text,
    user_agent text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_active_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    revoked boolean DEFAULT false NOT NULL,
    revoked_at timestamp with time zone,
    revoke_reason text,
    mfa_verified boolean DEFAULT false NOT NULL,
    device_fingerprint text,
    device_name text,
    device_type text,
    browser text,
    os text,
    country_code text,
    bound_ip text,
    ip_binding_enforced boolean DEFAULT false NOT NULL,
    fingerprint_binding_enforced boolean DEFAULT false NOT NULL,
    is_suspicious boolean DEFAULT false NOT NULL,
    suspicious_reason text,
    suspicious_detected_at timestamp with time zone,
    requires_verification boolean DEFAULT false NOT NULL,
    verified_at timestamp with time zone,
    session_number integer,
    rotated_from_session_id uuid,
    is_rotated boolean DEFAULT false NOT NULL,
    CONSTRAINT chk_device_type CHECK ((device_type = ANY (ARRAY['mobile'::text, 'tablet'::text, 'desktop'::text, 'unknown'::text]))),
    CONSTRAINT chk_expires_after_created CHECK ((expires_at > created_at)),
    CONSTRAINT chk_revoked_at_consistency CHECK ((((revoked = false) AND (revoked_at IS NULL)) OR ((revoked = true) AND (revoked_at IS NOT NULL))))
);


--
-- Name: stream_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.stream_events (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    category text NOT NULL,
    title text NOT NULL,
    description text,
    severity text,
    related_campaign_id uuid,
    related_call_id uuid,
    actor_user_id uuid,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone DEFAULT (now() + '90 days'::interval) NOT NULL,
    CONSTRAINT stream_events_category_check CHECK ((category = ANY (ARRAY['campaign'::text, 'system'::text, 'alert'::text, 'user_action'::text, 'milestone'::text]))),
    CONSTRAINT stream_events_severity_check CHECK (((severity IS NULL) OR (severity = ANY (ARRAY['info'::text, 'warning'::text, 'critical'::text]))))
);


--
-- Name: subscriptions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.subscriptions (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    stripe_subscription_id character varying(100) NOT NULL,
    stripe_customer_id character varying(100) NOT NULL,
    plan_id character varying(50),
    status character varying(50) DEFAULT 'incomplete'::character varying NOT NULL,
    current_period_start timestamp with time zone,
    current_period_end timestamp with time zone,
    cancel_at timestamp with time zone,
    canceled_at timestamp with time zone,
    trial_start timestamp with time zone,
    trial_end timestamp with time zone,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: suspension_propagation_queue; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.suspension_propagation_queue (
    queue_id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    suspension_id uuid NOT NULL,
    target_type character varying(20) NOT NULL,
    target_id uuid NOT NULL,
    action character varying(20) NOT NULL,
    service_name character varying(50) NOT NULL,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    last_attempt_at timestamp with time zone,
    last_error text,
    completed_at timestamp with time zone,
    next_attempt_at timestamp with time zone,
    CONSTRAINT suspension_propagation_queue_action_check CHECK (((action)::text = ANY (ARRAY[('suspend'::character varying)::text, ('restore'::character varying)::text]))),
    CONSTRAINT suspension_propagation_queue_status_check CHECK (((status)::text = ANY (ARRAY[('pending'::character varying)::text, ('in_progress'::character varying)::text, ('completed'::character varying)::text, ('failed'::character varying)::text])))
);


--
-- Name: tenant_ai_configs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenant_ai_configs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    llm_provider character varying(50) DEFAULT 'groq'::character varying NOT NULL,
    llm_model text DEFAULT 'llama-3.3-70b-versatile'::text NOT NULL,
    llm_temperature double precision DEFAULT 0.6 NOT NULL,
    llm_max_tokens integer DEFAULT 150 NOT NULL,
    stt_provider character varying(50) DEFAULT 'deepgram'::character varying NOT NULL,
    stt_model text DEFAULT 'nova-3'::text NOT NULL,
    stt_language character varying(16) DEFAULT 'en'::character varying NOT NULL,
    tts_provider character varying(50) DEFAULT 'google'::character varying NOT NULL,
    tts_model text DEFAULT 'Chirp3-HD'::text NOT NULL,
    tts_voice_id text DEFAULT 'en-US-Chirp3-HD-Leda'::text NOT NULL,
    tts_sample_rate integer DEFAULT 24000 NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    voice_tuning jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: tenant_call_limits; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenant_call_limits (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    calls_per_minute integer DEFAULT 60 NOT NULL,
    calls_per_hour integer DEFAULT 1000 NOT NULL,
    calls_per_day integer DEFAULT 10000 NOT NULL,
    max_concurrent_calls integer DEFAULT 10 NOT NULL,
    max_queue_size integer DEFAULT 50 NOT NULL,
    monthly_minutes_allocated integer DEFAULT 0 NOT NULL,
    monthly_minutes_used integer DEFAULT 0 NOT NULL,
    monthly_spend_cap numeric(12,4),
    monthly_spend_used numeric(12,4) DEFAULT 0.0 NOT NULL,
    max_call_duration_seconds integer DEFAULT 3600 NOT NULL,
    min_call_interval_seconds integer DEFAULT 300 NOT NULL,
    allowed_country_codes text[] DEFAULT ARRAY[]::text[] NOT NULL,
    blocked_country_codes text[] DEFAULT ARRAY[]::text[] NOT NULL,
    blocked_prefixes text[] DEFAULT ARRAY[]::text[] NOT NULL,
    features_enabled jsonb DEFAULT '{}'::jsonb NOT NULL,
    features_disabled jsonb DEFAULT '{}'::jsonb NOT NULL,
    respect_business_hours boolean DEFAULT false NOT NULL,
    business_hours_start time without time zone,
    business_hours_end time without time zone,
    business_hours_timezone character varying(64) DEFAULT 'UTC'::character varying NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    effective_from timestamp with time zone DEFAULT now() NOT NULL,
    effective_until timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT tenant_call_limits_calls_per_day_check CHECK ((calls_per_day > 0)),
    CONSTRAINT tenant_call_limits_calls_per_hour_check CHECK ((calls_per_hour > 0)),
    CONSTRAINT tenant_call_limits_calls_per_minute_check CHECK ((calls_per_minute > 0)),
    CONSTRAINT tenant_call_limits_max_call_duration_seconds_check CHECK ((max_call_duration_seconds > 0)),
    CONSTRAINT tenant_call_limits_max_concurrent_calls_check CHECK ((max_concurrent_calls > 0)),
    CONSTRAINT tenant_call_limits_max_queue_size_check CHECK ((max_queue_size >= 0)),
    CONSTRAINT tenant_call_limits_min_call_interval_seconds_check CHECK ((min_call_interval_seconds >= 0))
);


--
-- Name: tenant_codec_policies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenant_codec_policies (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    policy_name character varying(100) NOT NULL,
    allowed_codecs text[] DEFAULT ARRAY['PCMU'::text, 'PCMA'::text] NOT NULL,
    preferred_codec character varying(20) DEFAULT 'PCMU'::character varying NOT NULL,
    sample_rate_hz integer DEFAULT 8000 NOT NULL,
    ptime_ms integer DEFAULT 20 NOT NULL,
    max_bitrate_kbps integer,
    jitter_buffer_ms integer DEFAULT 60 NOT NULL,
    is_active boolean DEFAULT false NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_by uuid,
    updated_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_tenant_codec_preferred_in_allowed CHECK (((preferred_codec)::text = ANY (allowed_codecs))),
    CONSTRAINT tenant_codec_policies_jitter_buffer_ms_check CHECK (((jitter_buffer_ms >= 0) AND (jitter_buffer_ms <= 1000))),
    CONSTRAINT tenant_codec_policies_max_bitrate_kbps_check CHECK (((max_bitrate_kbps IS NULL) OR (max_bitrate_kbps > 0))),
    CONSTRAINT tenant_codec_policies_ptime_ms_check CHECK ((ptime_ms = ANY (ARRAY[10, 20, 30, 40, 60]))),
    CONSTRAINT tenant_codec_policies_sample_rate_hz_check CHECK ((sample_rate_hz = ANY (ARRAY[8000, 16000, 24000, 48000])))
);

ALTER TABLE ONLY public.tenant_codec_policies FORCE ROW LEVEL SECURITY;


--
-- Name: tenant_phone_numbers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenant_phone_numbers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    e164 text NOT NULL,
    provider text DEFAULT 'manual_admin'::text NOT NULL,
    status text DEFAULT 'pending_verification'::text NOT NULL,
    verification_method text,
    verification_sent_at timestamp with time zone,
    verified_at timestamp with time zone,
    verified_by text,
    stir_shaken_token text,
    label text,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT tenant_phone_numbers_status_check CHECK ((status = ANY (ARRAY['pending_verification'::text, 'verified'::text, 'suspended'::text, 'revoked'::text]))),
    CONSTRAINT tenant_phone_numbers_verification_method_check CHECK ((verification_method = ANY (ARRAY['sms_code'::text, 'carrier_api'::text, 'manual_admin'::text, 'letter_of_authorization'::text])))
);


--
-- Name: TABLE tenant_phone_numbers; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tenant_phone_numbers IS 'Verified Caller IDs (DIDs) per tenant. A number must have status=verified before any outbound call can originate with it as caller_id. See backend/docs/telephony/production-requirements.md for STIR/SHAKEN rules.';


--
-- Name: COLUMN tenant_phone_numbers.stir_shaken_token; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tenant_phone_numbers.stir_shaken_token IS 'Attestation token from upstream provider. NULL = test-only. Production enforcement refuses to originate when NULL.';


--
-- Name: tenant_quota_usage; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenant_quota_usage (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    usage_date date DEFAULT CURRENT_DATE NOT NULL,
    emails_sent integer DEFAULT 0,
    sms_sent integer DEFAULT 0,
    calls_initiated integer DEFAULT 0,
    meetings_booked integer DEFAULT 0,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: tenant_quotas; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenant_quotas (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    emails_per_day integer DEFAULT 50,
    sms_per_day integer DEFAULT 25,
    calls_per_day integer DEFAULT 50,
    meetings_per_day integer DEFAULT 10,
    max_concurrent_connectors integer DEFAULT 5,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: tenant_route_policies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenant_route_policies (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    policy_name character varying(100) NOT NULL,
    route_type character varying(10) DEFAULT 'outbound'::character varying NOT NULL,
    priority integer DEFAULT 100 NOT NULL,
    match_pattern text NOT NULL,
    target_trunk_id uuid NOT NULL,
    codec_policy_id uuid,
    strip_digits integer DEFAULT 0 NOT NULL,
    prepend_digits character varying(20),
    is_active boolean DEFAULT true NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_by uuid,
    updated_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT tenant_route_policies_priority_check CHECK (((priority >= 1) AND (priority <= 10000))),
    CONSTRAINT tenant_route_policies_route_type_check CHECK (((route_type)::text = ANY (ARRAY[('inbound'::character varying)::text, ('outbound'::character varying)::text]))),
    CONSTRAINT tenant_route_policies_strip_digits_check CHECK (((strip_digits >= 0) AND (strip_digits <= 15)))
);

ALTER TABLE ONLY public.tenant_route_policies FORCE ROW LEVEL SECURITY;


--
-- Name: tenant_runtime_policy_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenant_runtime_policy_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    policy_version_id uuid NOT NULL,
    action character varying(20) NOT NULL,
    stage character varying(20) NOT NULL,
    status character varying(20) NOT NULL,
    details jsonb DEFAULT '{}'::jsonb NOT NULL,
    request_id character varying(128),
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT tenant_runtime_policy_events_action_check CHECK (((action)::text = ANY (ARRAY[('activate'::character varying)::text, ('rollback'::character varying)::text]))),
    CONSTRAINT tenant_runtime_policy_events_stage_check CHECK (((stage)::text = ANY (ARRAY[('precheck'::character varying)::text, ('apply'::character varying)::text, ('verify'::character varying)::text, ('commit'::character varying)::text, ('rollback'::character varying)::text]))),
    CONSTRAINT tenant_runtime_policy_events_status_check CHECK (((status)::text = ANY (ARRAY[('started'::character varying)::text, ('succeeded'::character varying)::text, ('failed'::character varying)::text])))
);

ALTER TABLE ONLY public.tenant_runtime_policy_events FORCE ROW LEVEL SECURITY;


--
-- Name: tenant_runtime_policy_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenant_runtime_policy_versions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    policy_version integer NOT NULL,
    source_hash character(64) NOT NULL,
    schema_version character varying(32) DEFAULT 'ws-g.v1'::character varying NOT NULL,
    input_snapshot jsonb DEFAULT '{}'::jsonb NOT NULL,
    compiled_artifact jsonb NOT NULL,
    validation_report jsonb DEFAULT '{}'::jsonb NOT NULL,
    build_status character varying(20) DEFAULT 'compiled'::character varying NOT NULL,
    is_active boolean DEFAULT false NOT NULL,
    is_last_good boolean DEFAULT false NOT NULL,
    created_by uuid,
    activated_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    activated_at timestamp with time zone,
    CONSTRAINT tenant_runtime_policy_versions_build_status_check CHECK (((build_status)::text = ANY (ARRAY[('compiled'::character varying)::text, ('active'::character varying)::text, ('failed'::character varying)::text, ('superseded'::character varying)::text, ('rolled_back'::character varying)::text]))),
    CONSTRAINT tenant_runtime_policy_versions_policy_version_check CHECK ((policy_version > 0))
);

ALTER TABLE ONLY public.tenant_runtime_policy_versions FORCE ROW LEVEL SECURITY;


--
-- Name: tenant_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenant_settings (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    auto_actions_enabled boolean DEFAULT false,
    drive_root_folder_id text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: tenant_sip_trunks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenant_sip_trunks (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    trunk_name character varying(100) NOT NULL,
    sip_domain character varying(255) NOT NULL,
    port integer DEFAULT 5060 NOT NULL,
    transport character varying(8) DEFAULT 'udp'::character varying NOT NULL,
    direction character varying(10) DEFAULT 'both'::character varying NOT NULL,
    auth_username character varying(255),
    auth_password_encrypted text,
    is_active boolean DEFAULT false NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_by uuid,
    updated_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    last_tested_at timestamp with time zone,
    last_test_result jsonb,
    CONSTRAINT chk_tenant_sip_trunks_auth_pair CHECK ((((auth_username IS NULL) AND (auth_password_encrypted IS NULL)) OR ((auth_username IS NOT NULL) AND (auth_password_encrypted IS NOT NULL)))),
    CONSTRAINT tenant_sip_trunks_direction_check CHECK (((direction)::text = ANY (ARRAY[('inbound'::character varying)::text, ('outbound'::character varying)::text, ('both'::character varying)::text]))),
    CONSTRAINT tenant_sip_trunks_port_check CHECK (((port >= 1) AND (port <= 65535))),
    CONSTRAINT tenant_sip_trunks_transport_check CHECK (((transport)::text = ANY (ARRAY[('udp'::character varying)::text, ('tcp'::character varying)::text, ('tls'::character varying)::text])))
);

ALTER TABLE ONLY public.tenant_sip_trunks FORCE ROW LEVEL SECURITY;


--
-- Name: tenant_sip_trust_policies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenant_sip_trust_policies (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    policy_name character varying(100) NOT NULL,
    allowed_source_cidrs cidr[] DEFAULT ARRAY[]::cidr[] NOT NULL,
    blocked_source_cidrs cidr[] DEFAULT ARRAY[]::cidr[] NOT NULL,
    kamailio_group smallint DEFAULT 1 NOT NULL,
    priority integer DEFAULT 100 NOT NULL,
    is_active boolean DEFAULT false NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_by uuid,
    updated_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_tenant_sip_trust_has_source CHECK ((cardinality(allowed_source_cidrs) > 0)),
    CONSTRAINT tenant_sip_trust_policies_kamailio_group_check CHECK ((kamailio_group > 0)),
    CONSTRAINT tenant_sip_trust_policies_priority_check CHECK (((priority >= 1) AND (priority <= 10000)))
);

ALTER TABLE ONLY public.tenant_sip_trust_policies FORCE ROW LEVEL SECURITY;


--
-- Name: tenant_telephony_concurrency_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenant_telephony_concurrency_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    policy_id uuid,
    lease_id uuid,
    event_type character varying(16) NOT NULL,
    lease_kind character varying(16) NOT NULL,
    call_id uuid,
    talklee_call_id character varying(64),
    details jsonb DEFAULT '{}'::jsonb NOT NULL,
    request_id character varying(128),
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT tenant_telephony_concurrency_events_event_type_check CHECK (((event_type)::text = ANY (ARRAY[('acquire'::character varying)::text, ('reject'::character varying)::text, ('release'::character varying)::text, ('expire'::character varying)::text, ('heartbeat'::character varying)::text]))),
    CONSTRAINT tenant_telephony_concurrency_events_lease_kind_check CHECK (((lease_kind)::text = ANY (ARRAY[('call'::character varying)::text, ('transfer'::character varying)::text])))
);

ALTER TABLE ONLY public.tenant_telephony_concurrency_events FORCE ROW LEVEL SECURITY;


--
-- Name: tenant_telephony_concurrency_leases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenant_telephony_concurrency_leases (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    policy_id uuid,
    call_id uuid NOT NULL,
    talklee_call_id character varying(64) NOT NULL,
    lease_kind character varying(16) NOT NULL,
    state character varying(16) DEFAULT 'active'::character varying NOT NULL,
    acquired_at timestamp with time zone DEFAULT now() NOT NULL,
    last_heartbeat_at timestamp with time zone DEFAULT now() NOT NULL,
    released_at timestamp with time zone,
    release_reason character varying(64),
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_by uuid,
    updated_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_tenant_telephony_concurrency_release_consistency CHECK (((((state)::text = ANY (ARRAY[('released'::character varying)::text, ('expired'::character varying)::text])) AND (released_at IS NOT NULL)) OR ((state)::text = ANY (ARRAY[('active'::character varying)::text, ('releasing'::character varying)::text])))),
    CONSTRAINT tenant_telephony_concurrency_leases_lease_kind_check CHECK (((lease_kind)::text = ANY (ARRAY[('call'::character varying)::text, ('transfer'::character varying)::text]))),
    CONSTRAINT tenant_telephony_concurrency_leases_state_check CHECK (((state)::text = ANY (ARRAY[('active'::character varying)::text, ('releasing'::character varying)::text, ('released'::character varying)::text, ('expired'::character varying)::text])))
);

ALTER TABLE ONLY public.tenant_telephony_concurrency_leases FORCE ROW LEVEL SECURITY;


--
-- Name: tenant_telephony_concurrency_policies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenant_telephony_concurrency_policies (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    policy_name character varying(100) NOT NULL,
    max_active_calls integer DEFAULT 10 NOT NULL,
    max_transfer_inflight integer DEFAULT 2 NOT NULL,
    lease_ttl_seconds integer DEFAULT 120 NOT NULL,
    heartbeat_grace_seconds integer DEFAULT 30 NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_by uuid,
    updated_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT tenant_telephony_concurrency_poli_heartbeat_grace_seconds_check CHECK (((heartbeat_grace_seconds >= 5) AND (heartbeat_grace_seconds <= 600))),
    CONSTRAINT tenant_telephony_concurrency_polici_max_transfer_inflight_check CHECK (((max_transfer_inflight >= 1) AND (max_transfer_inflight <= 500))),
    CONSTRAINT tenant_telephony_concurrency_policies_lease_ttl_seconds_check CHECK (((lease_ttl_seconds >= 10) AND (lease_ttl_seconds <= 3600))),
    CONSTRAINT tenant_telephony_concurrency_policies_max_active_calls_check CHECK (((max_active_calls >= 1) AND (max_active_calls <= 1000)))
);

ALTER TABLE ONLY public.tenant_telephony_concurrency_policies FORCE ROW LEVEL SECURITY;


--
-- Name: tenant_telephony_credentials; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenant_telephony_credentials (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    provider text NOT NULL,
    label text,
    credentials_encrypted text NOT NULL,
    from_number text,
    status text DEFAULT 'inactive'::text NOT NULL,
    last_tested_at timestamp with time zone,
    last_test_result jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT tenant_telephony_credentials_provider_check CHECK ((provider = ANY (ARRAY['twilio'::text, 'vonage'::text]))),
    CONSTRAINT tenant_telephony_credentials_status_check CHECK ((status = ANY (ARRAY['active'::text, 'inactive'::text, 'failed'::text])))
);


--
-- Name: tenant_telephony_idempotency; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenant_telephony_idempotency (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    operation character varying(120) NOT NULL,
    idempotency_key character varying(255) NOT NULL,
    request_hash character(64) NOT NULL,
    response_body jsonb,
    status_code integer,
    resource_type character varying(64),
    resource_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone DEFAULT (now() + '24:00:00'::interval) NOT NULL,
    CONSTRAINT tenant_telephony_idempotency_status_code_check CHECK (((status_code IS NULL) OR ((status_code >= 100) AND (status_code <= 599))))
);

ALTER TABLE ONLY public.tenant_telephony_idempotency FORCE ROW LEVEL SECURITY;


--
-- Name: tenant_telephony_quota_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenant_telephony_quota_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    policy_id uuid,
    event_type character varying(16) NOT NULL,
    policy_scope character varying(32) NOT NULL,
    metric_key character varying(120) NOT NULL,
    counter_value bigint DEFAULT 0 NOT NULL,
    threshold_value bigint,
    window_seconds integer NOT NULL,
    block_ttl_seconds integer DEFAULT 0 NOT NULL,
    request_id character varying(128),
    details jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT tenant_telephony_quota_events_block_ttl_seconds_check CHECK ((block_ttl_seconds >= 0)),
    CONSTRAINT tenant_telephony_quota_events_event_type_check CHECK (((event_type)::text = ANY (ARRAY[('warn'::character varying)::text, ('throttle'::character varying)::text, ('block'::character varying)::text]))),
    CONSTRAINT tenant_telephony_quota_events_window_seconds_check CHECK ((window_seconds > 0))
);

ALTER TABLE ONLY public.tenant_telephony_quota_events FORCE ROW LEVEL SECURITY;


--
-- Name: tenant_telephony_threshold_policies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenant_telephony_threshold_policies (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    policy_name character varying(100) NOT NULL,
    policy_scope character varying(32) NOT NULL,
    metric_key character varying(120) DEFAULT '*'::character varying NOT NULL,
    window_seconds integer DEFAULT 60 NOT NULL,
    warn_threshold integer DEFAULT 20 NOT NULL,
    throttle_threshold integer DEFAULT 30 NOT NULL,
    block_threshold integer DEFAULT 45 NOT NULL,
    block_duration_seconds integer DEFAULT 300 NOT NULL,
    throttle_retry_seconds integer DEFAULT 2 NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_by uuid,
    updated_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_tenant_telephony_threshold_order CHECK (((warn_threshold <= throttle_threshold) AND (throttle_threshold <= block_threshold))),
    CONSTRAINT tenant_telephony_threshold_policie_block_duration_seconds_check CHECK (((block_duration_seconds >= 1) AND (block_duration_seconds <= 86400))),
    CONSTRAINT tenant_telephony_threshold_policie_throttle_retry_seconds_check CHECK (((throttle_retry_seconds >= 1) AND (throttle_retry_seconds <= 60))),
    CONSTRAINT tenant_telephony_threshold_policies_block_threshold_check CHECK ((block_threshold > 0)),
    CONSTRAINT tenant_telephony_threshold_policies_policy_scope_check CHECK (((policy_scope)::text = ANY (ARRAY[('api_mutation'::character varying)::text, ('runtime_mutation'::character varying)::text, ('sip_edge'::character varying)::text]))),
    CONSTRAINT tenant_telephony_threshold_policies_throttle_threshold_check CHECK ((throttle_threshold > 0)),
    CONSTRAINT tenant_telephony_threshold_policies_warn_threshold_check CHECK ((warn_threshold > 0)),
    CONSTRAINT tenant_telephony_threshold_policies_window_seconds_check CHECK (((window_seconds >= 1) AND (window_seconds <= 3600)))
);

ALTER TABLE ONLY public.tenant_telephony_threshold_policies FORCE ROW LEVEL SECURITY;


--
-- Name: tenant_users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenant_users (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    user_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    role_id uuid NOT NULL,
    is_primary boolean DEFAULT false NOT NULL,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    invited_by uuid,
    invited_at timestamp with time zone,
    joined_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT tenant_users_status_check CHECK (((status)::text = ANY (ARRAY[('pending'::character varying)::text, ('active'::character varying)::text, ('suspended'::character varying)::text, ('removed'::character varying)::text])))
);


--
-- Name: tenants; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenants (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    business_name character varying(255) NOT NULL,
    plan_id character varying(50),
    minutes_allocated integer DEFAULT 0 NOT NULL,
    minutes_used integer DEFAULT 0 NOT NULL,
    calling_rules jsonb DEFAULT '{"skip_dnc": true, "timezone": "America/New_York", "allowed_days": [0, 1, 2, 3, 4], "time_window_end": "19:00", "time_window_start": "09:00", "max_retry_attempts": 3, "retry_delay_seconds": 7200, "max_concurrent_calls": 10, "high_priority_threshold": 8, "min_hours_between_calls": 2, "enable_priority_override": true}'::jsonb,
    stripe_customer_id character varying(100),
    stripe_subscription_id character varying(100),
    subscription_status character varying(50) DEFAULT 'inactive'::character varying,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    partner_id uuid,
    is_partner boolean DEFAULT false,
    status character varying(20) DEFAULT 'active'::character varying,
    suspended_at timestamp with time zone,
    suspended_by uuid,
    suspension_reason text,
    white_label_partner_id uuid,
    active_telephony_provider text DEFAULT 'none'::text,
    CONSTRAINT tenants_active_telephony_provider_check CHECK ((active_telephony_provider = ANY (ARRAY['twilio'::text, 'vonage'::text, 'sip'::text, 'none'::text]))),
    CONSTRAINT tenants_status_check CHECK (((status)::text = ANY (ARRAY[('active'::character varying)::text, ('suspended'::character varying)::text, ('pending_deletion'::character varying)::text])))
);


--
-- Name: transcripts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.transcripts (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid,
    call_id uuid NOT NULL,
    turns jsonb DEFAULT '[]'::jsonb NOT NULL,
    full_text text,
    word_count integer DEFAULT 0,
    turn_count integer DEFAULT 0,
    user_word_count integer DEFAULT 0,
    assistant_word_count integer DEFAULT 0,
    duration_seconds integer,
    drive_file_id text,
    drive_web_link text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: usage_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.usage_records (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    subscription_id uuid,
    usage_type character varying(50) DEFAULT 'minutes'::character varying NOT NULL,
    quantity integer NOT NULL,
    "timestamp" timestamp with time zone DEFAULT now(),
    reported_to_stripe boolean DEFAULT false,
    stripe_usage_record_id character varying(100),
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: user_permissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_permissions (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    user_id uuid NOT NULL,
    permission_id uuid NOT NULL,
    tenant_id uuid,
    expires_at timestamp with time zone,
    reason text,
    granted_by uuid,
    granted_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: user_profiles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_profiles (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    email character varying(255) NOT NULL,
    name character varying(255),
    tenant_id uuid,
    role character varying(50) DEFAULT 'user'::character varying NOT NULL,
    password_hash text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    account_locked_until timestamp with time zone,
    is_active boolean DEFAULT true NOT NULL,
    password_changed_at timestamp with time zone,
    failed_login_count integer DEFAULT 0 NOT NULL,
    last_login_at timestamp with time zone,
    mfa_enabled boolean DEFAULT false NOT NULL,
    is_verified boolean DEFAULT false NOT NULL,
    verification_token text,
    verification_token_expires_at timestamp with time zone,
    email_verified_at timestamp with time zone,
    passkey_count integer DEFAULT 0 NOT NULL,
    CONSTRAINT chk_email_verification_consistency CHECK (((is_verified = false) OR ((is_verified = true) AND (verification_token IS NULL) AND (email_verified_at IS NOT NULL)))),
    CONSTRAINT chk_user_profiles_role_valid CHECK (((role)::text = ANY (ARRAY[('platform_admin'::character varying)::text, ('partner_admin'::character varying)::text, ('tenant_admin'::character varying)::text, ('user'::character varying)::text, ('readonly'::character varying)::text]))),
    CONSTRAINT user_profiles_passkey_count_check CHECK ((passkey_count >= 0))
);


--
-- Name: user_effective_permissions; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.user_effective_permissions AS
 SELECT DISTINCT up.id AS user_id,
    p.id AS permission_id,
    p.name AS permission_name,
    p.resource,
    p.action,
    tu.tenant_id,
    r.name AS role_name,
    'role'::text AS grant_type
   FROM ((((public.user_profiles up
     JOIN public.tenant_users tu ON (((tu.user_id = up.id) AND ((tu.status)::text = 'active'::text))))
     JOIN public.roles r ON ((r.id = tu.role_id)))
     JOIN public.role_permissions rp ON ((rp.role_id = r.id)))
     JOIN public.permissions p ON ((p.id = rp.permission_id)))
UNION
 SELECT up.id AS user_id,
    p.id AS permission_id,
    p.name AS permission_name,
    p.resource,
    p.action,
    up_perm.tenant_id,
    NULL::character varying AS role_name,
    'direct'::text AS grant_type
   FROM ((public.user_profiles up
     JOIN public.user_permissions up_perm ON ((up_perm.user_id = up.id)))
     JOIN public.permissions p ON ((p.id = up_perm.permission_id)))
  WHERE ((up_perm.expires_at IS NULL) OR (up_perm.expires_at > now()));


--
-- Name: user_mfa; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_mfa (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    user_id uuid NOT NULL,
    totp_secret_enc text NOT NULL,
    enabled boolean DEFAULT false NOT NULL,
    verified_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    last_used_at timestamp with time zone,
    CONSTRAINT chk_user_mfa_verified_before_enabled CHECK (((enabled = false) OR ((enabled = true) AND (verified_at IS NOT NULL))))
);


--
-- Name: user_passkeys; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_passkeys (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    user_id uuid NOT NULL,
    credential_id text NOT NULL,
    credential_public_key text NOT NULL,
    sign_count bigint DEFAULT 0 NOT NULL,
    aaguid text,
    device_type text,
    backed_up boolean DEFAULT false NOT NULL,
    transports text[],
    display_name text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_used_at timestamp with time zone,
    CONSTRAINT user_passkeys_device_type_check CHECK ((device_type = ANY (ARRAY['singleDevice'::text, 'multiDevice'::text]))),
    CONSTRAINT user_passkeys_sign_count_check CHECK ((sign_count >= 0))
);


--
-- Name: user_tenant_roles; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.user_tenant_roles AS
 SELECT up.id AS user_id,
    up.email,
    tu.tenant_id,
    t.business_name AS tenant_name,
    r.id AS role_id,
    r.name AS role_name,
    r.level AS role_level,
    tu.status,
    tu.is_primary
   FROM (((public.user_profiles up
     JOIN public.tenant_users tu ON ((tu.user_id = up.id)))
     JOIN public.tenants t ON ((t.id = tu.tenant_id)))
     JOIN public.roles r ON ((r.id = tu.role_id)))
  WHERE ((tu.status)::text = ANY (ARRAY[('active'::character varying)::text, ('pending'::character varying)::text]));


--
-- Name: webauthn_challenges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.webauthn_challenges (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    challenge text NOT NULL,
    ceremony text NOT NULL,
    user_id uuid,
    ip_address text,
    user_agent text,
    expires_at timestamp with time zone NOT NULL,
    used boolean DEFAULT false NOT NULL,
    used_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_wc_expires_after_created CHECK ((expires_at > created_at)),
    CONSTRAINT chk_wc_used_at CHECK ((((used = false) AND (used_at IS NULL)) OR ((used = true) AND (used_at IS NOT NULL)))),
    CONSTRAINT webauthn_challenges_ceremony_check CHECK ((ceremony = ANY (ARRAY['registration'::text, 'authentication'::text])))
);


--
-- Name: webhook_configs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.webhook_configs (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid,
    webhook_name text NOT NULL,
    secret_key text NOT NULL,
    signature_algorithm text DEFAULT 'hmac-sha256'::text,
    is_active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: white_label_partners; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.white_label_partners (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    company_name character varying(255) NOT NULL,
    contact_email character varying(255),
    status character varying(20) DEFAULT 'active'::character varying,
    suspended_at timestamp with time zone,
    suspended_by uuid,
    suspension_reason text,
    custom_domain character varying(255),
    branding_config jsonb DEFAULT '{}'::jsonb,
    billing_config jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT white_label_partners_status_check CHECK (((status)::text = ANY (ARRAY[('active'::character varying)::text, ('suspended'::character varying)::text, ('pending_deletion'::character varying)::text])))
);


--
-- Name: audit_chain_state id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_chain_state ALTER COLUMN id SET DEFAULT nextval('public.audit_chain_state_id_seq'::regclass);


--
-- Name: abuse_detection_rules abuse_detection_rules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.abuse_detection_rules
    ADD CONSTRAINT abuse_detection_rules_pkey PRIMARY KEY (id);


--
-- Name: abuse_events abuse_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.abuse_events
    ADD CONSTRAINT abuse_events_pkey PRIMARY KEY (id);


--
-- Name: action_plans action_plans_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_plans
    ADD CONSTRAINT action_plans_pkey PRIMARY KEY (id);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: assistant_actions assistant_actions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_actions
    ADD CONSTRAINT assistant_actions_pkey PRIMARY KEY (id);


--
-- Name: assistant_conversations assistant_conversations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_conversations
    ADD CONSTRAINT assistant_conversations_pkey PRIMARY KEY (id);


--
-- Name: audit_chain_state audit_chain_state_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_chain_state
    ADD CONSTRAINT audit_chain_state_pkey PRIMARY KEY (id);


--
-- Name: audit_event_types audit_event_types_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_event_types
    ADD CONSTRAINT audit_event_types_pkey PRIMARY KEY (event_type);


--
-- Name: audit_logs audit_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_pkey PRIMARY KEY (event_id);


--
-- Name: call_events call_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_events
    ADD CONSTRAINT call_events_pkey PRIMARY KEY (id);


--
-- Name: call_guard_decisions call_guard_decisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_guard_decisions
    ADD CONSTRAINT call_guard_decisions_pkey PRIMARY KEY (id);


--
-- Name: call_legs call_legs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_legs
    ADD CONSTRAINT call_legs_pkey PRIMARY KEY (id);


--
-- Name: call_velocity_snapshots call_velocity_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_velocity_snapshots
    ADD CONSTRAINT call_velocity_snapshots_pkey PRIMARY KEY (id);


--
-- Name: call_velocity_snapshots call_velocity_snapshots_tenant_id_window_start_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_velocity_snapshots
    ADD CONSTRAINT call_velocity_snapshots_tenant_id_window_start_key UNIQUE (tenant_id, window_start);


--
-- Name: calls calls_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.calls
    ADD CONSTRAINT calls_pkey PRIMARY KEY (id);


--
-- Name: calls calls_talklee_call_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.calls
    ADD CONSTRAINT calls_talklee_call_id_key UNIQUE (talklee_call_id);


--
-- Name: campaigns campaigns_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaigns
    ADD CONSTRAINT campaigns_pkey PRIMARY KEY (id);


--
-- Name: clients clients_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clients
    ADD CONSTRAINT clients_pkey PRIMARY KEY (id);


--
-- Name: connector_accounts connector_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.connector_accounts
    ADD CONSTRAINT connector_accounts_pkey PRIMARY KEY (id);


--
-- Name: connectors connectors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.connectors
    ADD CONSTRAINT connectors_pkey PRIMARY KEY (id);


--
-- Name: conversations conversations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT conversations_pkey PRIMARY KEY (id);


--
-- Name: dialer_jobs dialer_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dialer_jobs
    ADD CONSTRAINT dialer_jobs_pkey PRIMARY KEY (id);


--
-- Name: dnc_entries dnc_entries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dnc_entries
    ADD CONSTRAINT dnc_entries_pkey PRIMARY KEY (id);


--
-- Name: emergency_access_requests emergency_access_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.emergency_access_requests
    ADD CONSTRAINT emergency_access_requests_pkey PRIMARY KEY (request_id);


--
-- Name: invoices invoices_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_pkey PRIMARY KEY (id);


--
-- Name: invoices invoices_stripe_invoice_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_stripe_invoice_id_key UNIQUE (stripe_invoice_id);


--
-- Name: leads leads_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.leads
    ADD CONSTRAINT leads_pkey PRIMARY KEY (id);


--
-- Name: meetings meetings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meetings
    ADD CONSTRAINT meetings_pkey PRIMARY KEY (id);


--
-- Name: partner_limits partner_limits_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.partner_limits
    ADD CONSTRAINT partner_limits_pkey PRIMARY KEY (id);


--
-- Name: login_attempts pk_login_attempts; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.login_attempts
    ADD CONSTRAINT pk_login_attempts PRIMARY KEY (id);


--
-- Name: mfa_challenges pk_mfa_challenges; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mfa_challenges
    ADD CONSTRAINT pk_mfa_challenges PRIMARY KEY (id);


--
-- Name: permissions pk_permissions; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.permissions
    ADD CONSTRAINT pk_permissions PRIMARY KEY (id);


--
-- Name: recovery_codes pk_recovery_codes; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recovery_codes
    ADD CONSTRAINT pk_recovery_codes PRIMARY KEY (id);


--
-- Name: role_permissions pk_role_permissions; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role_permissions
    ADD CONSTRAINT pk_role_permissions PRIMARY KEY (id);


--
-- Name: roles pk_roles; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.roles
    ADD CONSTRAINT pk_roles PRIMARY KEY (id);


--
-- Name: security_sessions pk_security_sessions; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.security_sessions
    ADD CONSTRAINT pk_security_sessions PRIMARY KEY (id);


--
-- Name: tenant_users pk_tenant_users; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_users
    ADD CONSTRAINT pk_tenant_users PRIMARY KEY (id);


--
-- Name: user_mfa pk_user_mfa; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_mfa
    ADD CONSTRAINT pk_user_mfa PRIMARY KEY (id);


--
-- Name: user_permissions pk_user_permissions; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_permissions
    ADD CONSTRAINT pk_user_permissions PRIMARY KEY (id);


--
-- Name: plans plans_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plans
    ADD CONSTRAINT plans_pkey PRIMARY KEY (id);


--
-- Name: rate_limit_events rate_limit_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rate_limit_events
    ADD CONSTRAINT rate_limit_events_pkey PRIMARY KEY (id);


--
-- Name: recordings recordings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recordings
    ADD CONSTRAINT recordings_pkey PRIMARY KEY (id);


--
-- Name: recordings_s3 recordings_s3_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recordings_s3
    ADD CONSTRAINT recordings_s3_pkey PRIMARY KEY (id);


--
-- Name: recordings_s3 recordings_s3_s3_bucket_s3_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recordings_s3
    ADD CONSTRAINT recordings_s3_s3_bucket_s3_key_key UNIQUE (s3_bucket, s3_key);


--
-- Name: refresh_tokens refresh_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.refresh_tokens
    ADD CONSTRAINT refresh_tokens_pkey PRIMARY KEY (id);


--
-- Name: refresh_tokens refresh_tokens_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.refresh_tokens
    ADD CONSTRAINT refresh_tokens_token_hash_key UNIQUE (token_hash);


--
-- Name: reminders reminders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reminders
    ADD CONSTRAINT reminders_pkey PRIMARY KEY (id);


--
-- Name: secret_access_log secret_access_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.secret_access_log
    ADD CONSTRAINT secret_access_log_pkey PRIMARY KEY (access_id);


--
-- Name: security_events security_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.security_events
    ADD CONSTRAINT security_events_pkey PRIMARY KEY (event_id);


--
-- Name: stream_events stream_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stream_events
    ADD CONSTRAINT stream_events_pkey PRIMARY KEY (id);


--
-- Name: subscriptions subscriptions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscriptions
    ADD CONSTRAINT subscriptions_pkey PRIMARY KEY (id);


--
-- Name: subscriptions subscriptions_stripe_subscription_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscriptions
    ADD CONSTRAINT subscriptions_stripe_subscription_id_key UNIQUE (stripe_subscription_id);


--
-- Name: suspension_events suspension_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.suspension_events
    ADD CONSTRAINT suspension_events_pkey PRIMARY KEY (suspension_id);


--
-- Name: suspension_propagation_queue suspension_propagation_queue_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.suspension_propagation_queue
    ADD CONSTRAINT suspension_propagation_queue_pkey PRIMARY KEY (queue_id);


--
-- Name: tenant_ai_configs tenant_ai_configs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_ai_configs
    ADD CONSTRAINT tenant_ai_configs_pkey PRIMARY KEY (id);


--
-- Name: tenant_ai_configs tenant_ai_configs_tenant_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_ai_configs
    ADD CONSTRAINT tenant_ai_configs_tenant_id_key UNIQUE (tenant_id);


--
-- Name: tenant_call_limits tenant_call_limits_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_call_limits
    ADD CONSTRAINT tenant_call_limits_pkey PRIMARY KEY (id);


--
-- Name: tenant_codec_policies tenant_codec_policies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_codec_policies
    ADD CONSTRAINT tenant_codec_policies_pkey PRIMARY KEY (id);


--
-- Name: tenant_phone_numbers tenant_phone_numbers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_phone_numbers
    ADD CONSTRAINT tenant_phone_numbers_pkey PRIMARY KEY (id);


--
-- Name: tenant_phone_numbers tenant_phone_numbers_tenant_e164_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_phone_numbers
    ADD CONSTRAINT tenant_phone_numbers_tenant_e164_unique UNIQUE (tenant_id, e164);


--
-- Name: tenant_quota_usage tenant_quota_usage_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_quota_usage
    ADD CONSTRAINT tenant_quota_usage_pkey PRIMARY KEY (id);


--
-- Name: tenant_quota_usage tenant_quota_usage_tenant_id_usage_date_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_quota_usage
    ADD CONSTRAINT tenant_quota_usage_tenant_id_usage_date_key UNIQUE (tenant_id, usage_date);


--
-- Name: tenant_quotas tenant_quotas_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_quotas
    ADD CONSTRAINT tenant_quotas_pkey PRIMARY KEY (id);


--
-- Name: tenant_quotas tenant_quotas_tenant_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_quotas
    ADD CONSTRAINT tenant_quotas_tenant_id_key UNIQUE (tenant_id);


--
-- Name: tenant_route_policies tenant_route_policies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_route_policies
    ADD CONSTRAINT tenant_route_policies_pkey PRIMARY KEY (id);


--
-- Name: tenant_runtime_policy_events tenant_runtime_policy_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_runtime_policy_events
    ADD CONSTRAINT tenant_runtime_policy_events_pkey PRIMARY KEY (id);


--
-- Name: tenant_runtime_policy_versions tenant_runtime_policy_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_runtime_policy_versions
    ADD CONSTRAINT tenant_runtime_policy_versions_pkey PRIMARY KEY (id);


--
-- Name: tenant_secrets tenant_secrets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_secrets
    ADD CONSTRAINT tenant_secrets_pkey PRIMARY KEY (secret_id);


--
-- Name: tenant_secrets tenant_secrets_tenant_id_secret_name_is_active_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_secrets
    ADD CONSTRAINT tenant_secrets_tenant_id_secret_name_is_active_key UNIQUE (tenant_id, secret_name, is_active);


--
-- Name: tenant_settings tenant_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_settings
    ADD CONSTRAINT tenant_settings_pkey PRIMARY KEY (id);


--
-- Name: tenant_settings tenant_settings_tenant_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_settings
    ADD CONSTRAINT tenant_settings_tenant_id_key UNIQUE (tenant_id);


--
-- Name: tenant_sip_trunks tenant_sip_trunks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_sip_trunks
    ADD CONSTRAINT tenant_sip_trunks_pkey PRIMARY KEY (id);


--
-- Name: tenant_sip_trust_policies tenant_sip_trust_policies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_sip_trust_policies
    ADD CONSTRAINT tenant_sip_trust_policies_pkey PRIMARY KEY (id);


--
-- Name: tenant_telephony_concurrency_events tenant_telephony_concurrency_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_telephony_concurrency_events
    ADD CONSTRAINT tenant_telephony_concurrency_events_pkey PRIMARY KEY (id);


--
-- Name: tenant_telephony_concurrency_leases tenant_telephony_concurrency_leases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_telephony_concurrency_leases
    ADD CONSTRAINT tenant_telephony_concurrency_leases_pkey PRIMARY KEY (id);


--
-- Name: tenant_telephony_concurrency_policies tenant_telephony_concurrency_policies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_telephony_concurrency_policies
    ADD CONSTRAINT tenant_telephony_concurrency_policies_pkey PRIMARY KEY (id);


--
-- Name: tenant_telephony_credentials tenant_telephony_credentials_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_telephony_credentials
    ADD CONSTRAINT tenant_telephony_credentials_pkey PRIMARY KEY (id);


--
-- Name: tenant_telephony_credentials tenant_telephony_credentials_tenant_id_provider_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_telephony_credentials
    ADD CONSTRAINT tenant_telephony_credentials_tenant_id_provider_key UNIQUE (tenant_id, provider);


--
-- Name: tenant_telephony_idempotency tenant_telephony_idempotency_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_telephony_idempotency
    ADD CONSTRAINT tenant_telephony_idempotency_pkey PRIMARY KEY (id);


--
-- Name: tenant_telephony_quota_events tenant_telephony_quota_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_telephony_quota_events
    ADD CONSTRAINT tenant_telephony_quota_events_pkey PRIMARY KEY (id);


--
-- Name: tenant_telephony_threshold_policies tenant_telephony_threshold_policies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_telephony_threshold_policies
    ADD CONSTRAINT tenant_telephony_threshold_policies_pkey PRIMARY KEY (id);


--
-- Name: tenants tenants_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenants
    ADD CONSTRAINT tenants_pkey PRIMARY KEY (id);


--
-- Name: transcripts transcripts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcripts
    ADD CONSTRAINT transcripts_pkey PRIMARY KEY (id);


--
-- Name: mfa_challenges uq_mfa_challenges_hash; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mfa_challenges
    ADD CONSTRAINT uq_mfa_challenges_hash UNIQUE (challenge_hash);


--
-- Name: permissions uq_permissions_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.permissions
    ADD CONSTRAINT uq_permissions_name UNIQUE (name);


--
-- Name: permissions uq_permissions_resource_action; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.permissions
    ADD CONSTRAINT uq_permissions_resource_action UNIQUE (resource, action);


--
-- Name: recovery_codes uq_recovery_codes_hash; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recovery_codes
    ADD CONSTRAINT uq_recovery_codes_hash UNIQUE (code_hash);


--
-- Name: role_permissions uq_role_permissions_role_perm; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role_permissions
    ADD CONSTRAINT uq_role_permissions_role_perm UNIQUE (role_id, permission_id);


--
-- Name: roles uq_roles_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.roles
    ADD CONSTRAINT uq_roles_name UNIQUE (name);


--
-- Name: security_sessions uq_security_sessions_token_hash; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.security_sessions
    ADD CONSTRAINT uq_security_sessions_token_hash UNIQUE (session_token_hash);


--
-- Name: tenant_runtime_policy_versions uq_tenant_runtime_policy_version; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_runtime_policy_versions
    ADD CONSTRAINT uq_tenant_runtime_policy_version UNIQUE (tenant_id, policy_version);


--
-- Name: tenant_telephony_concurrency_policies uq_tenant_telephony_concurrency_policy; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_telephony_concurrency_policies
    ADD CONSTRAINT uq_tenant_telephony_concurrency_policy UNIQUE (tenant_id, policy_name);


--
-- Name: tenant_telephony_idempotency uq_tenant_telephony_idempotency; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_telephony_idempotency
    ADD CONSTRAINT uq_tenant_telephony_idempotency UNIQUE (tenant_id, operation, idempotency_key);


--
-- Name: tenant_telephony_threshold_policies uq_tenant_telephony_threshold_policy; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_telephony_threshold_policies
    ADD CONSTRAINT uq_tenant_telephony_threshold_policy UNIQUE (tenant_id, policy_scope, metric_key);


--
-- Name: tenant_users uq_tenant_users_user_tenant; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_users
    ADD CONSTRAINT uq_tenant_users_user_tenant UNIQUE (user_id, tenant_id);


--
-- Name: user_mfa uq_user_mfa_user; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_mfa
    ADD CONSTRAINT uq_user_mfa_user UNIQUE (user_id);


--
-- Name: user_permissions uq_user_permissions_user_perm_tenant; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_permissions
    ADD CONSTRAINT uq_user_permissions_user_perm_tenant UNIQUE (user_id, permission_id, tenant_id);


--
-- Name: usage_records usage_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usage_records
    ADD CONSTRAINT usage_records_pkey PRIMARY KEY (id);


--
-- Name: user_passkeys user_passkeys_credential_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_passkeys
    ADD CONSTRAINT user_passkeys_credential_id_key UNIQUE (credential_id);


--
-- Name: user_passkeys user_passkeys_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_passkeys
    ADD CONSTRAINT user_passkeys_pkey PRIMARY KEY (id);


--
-- Name: user_profiles user_profiles_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_profiles
    ADD CONSTRAINT user_profiles_email_key UNIQUE (email);


--
-- Name: user_profiles user_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_profiles
    ADD CONSTRAINT user_profiles_pkey PRIMARY KEY (id);


--
-- Name: webauthn_challenges webauthn_challenges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.webauthn_challenges
    ADD CONSTRAINT webauthn_challenges_pkey PRIMARY KEY (id);


--
-- Name: webhook_configs webhook_configs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.webhook_configs
    ADD CONSTRAINT webhook_configs_pkey PRIMARY KEY (id);


--
-- Name: webhook_configs webhook_configs_tenant_id_webhook_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.webhook_configs
    ADD CONSTRAINT webhook_configs_tenant_id_webhook_name_key UNIQUE (tenant_id, webhook_name);


--
-- Name: white_label_partners white_label_partners_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.white_label_partners
    ADD CONSTRAINT white_label_partners_pkey PRIMARY KEY (id);


--
-- Name: white_label_partners white_label_partners_tenant_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.white_label_partners
    ADD CONSTRAINT white_label_partners_tenant_id_key UNIQUE (tenant_id);


--
-- Name: idx_abuse_events_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_abuse_events_tenant ON public.abuse_events USING btree (tenant_id, created_at DESC);


--
-- Name: idx_abuse_events_tenant_severity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_abuse_events_tenant_severity ON public.abuse_events USING btree (tenant_id, severity, created_at DESC);


--
-- Name: idx_abuse_events_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_abuse_events_type ON public.abuse_events USING btree (event_type, severity, created_at DESC);


--
-- Name: idx_abuse_events_unresolved; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_abuse_events_unresolved ON public.abuse_events USING btree (tenant_id, created_at DESC) WHERE (resolved_at IS NULL);


--
-- Name: idx_abuse_rules_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_abuse_rules_tenant ON public.abuse_detection_rules USING btree (tenant_id) WHERE (is_active = true);


--
-- Name: idx_abuse_rules_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_abuse_rules_type ON public.abuse_detection_rules USING btree (rule_type, is_active);


--
-- Name: idx_assistant_actions_idempotency; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_assistant_actions_idempotency ON public.assistant_actions USING btree (tenant_id, idempotency_key) WHERE (idempotency_key IS NOT NULL);


--
-- Name: idx_audit_logs_actor_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_logs_actor_id ON public.audit_logs USING btree (actor_id);


--
-- Name: idx_audit_logs_actor_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_logs_actor_time ON public.audit_logs USING btree (actor_id, event_time DESC);


--
-- Name: idx_audit_logs_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_logs_category ON public.audit_logs USING btree (event_category);


--
-- Name: idx_audit_logs_compliance; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_logs_compliance ON public.audit_logs USING gin (compliance_tags);


--
-- Name: idx_audit_logs_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_logs_created_at ON public.audit_logs USING btree (created_at DESC);


--
-- Name: idx_audit_logs_event_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_logs_event_time ON public.audit_logs USING btree (event_time DESC);


--
-- Name: idx_audit_logs_event_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_logs_event_type ON public.audit_logs USING btree (event_type);


--
-- Name: idx_audit_logs_resource; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_logs_resource ON public.audit_logs USING btree (resource_type, resource_id);


--
-- Name: idx_audit_logs_retention; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_logs_retention ON public.audit_logs USING btree (retention_until);


--
-- Name: idx_audit_logs_severity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_logs_severity ON public.audit_logs USING btree (severity);


--
-- Name: idx_audit_logs_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_logs_tenant_id ON public.audit_logs USING btree (tenant_id);


--
-- Name: idx_audit_logs_tenant_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_logs_tenant_time ON public.audit_logs USING btree (tenant_id, event_time DESC);


--
-- Name: idx_call_events_call_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_call_events_call_id ON public.call_events USING btree (call_id);


--
-- Name: idx_call_events_talklee_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_call_events_talklee_id ON public.call_events USING btree (talklee_call_id) WHERE (talklee_call_id IS NOT NULL);


--
-- Name: idx_call_guard_decisions_blocked; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_call_guard_decisions_blocked ON public.call_guard_decisions USING btree (tenant_id, decision) WHERE ((decision)::text <> 'allow'::text);


--
-- Name: idx_call_guard_decisions_call; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_call_guard_decisions_call ON public.call_guard_decisions USING btree (call_id) WHERE (call_id IS NOT NULL);


--
-- Name: idx_call_guard_decisions_phone; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_call_guard_decisions_phone ON public.call_guard_decisions USING btree (phone_number, created_at DESC) WHERE (phone_number IS NOT NULL);


--
-- Name: idx_call_guard_decisions_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_call_guard_decisions_tenant ON public.call_guard_decisions USING btree (tenant_id, created_at DESC);


--
-- Name: idx_call_guard_decisions_tenant_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_call_guard_decisions_tenant_created ON public.call_guard_decisions USING btree (tenant_id, created_at DESC);


--
-- Name: idx_call_legs_call_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_call_legs_call_id ON public.call_legs USING btree (call_id);


--
-- Name: idx_call_velocity_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_call_velocity_tenant ON public.call_velocity_snapshots USING btree (tenant_id, window_start DESC);


--
-- Name: idx_calls_campaign_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_calls_campaign_id ON public.calls USING btree (campaign_id);


--
-- Name: idx_calls_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_calls_created_at ON public.calls USING btree (created_at);


--
-- Name: idx_calls_external_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_calls_external_uuid ON public.calls USING btree (external_call_uuid);


--
-- Name: idx_calls_lead_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_calls_lead_id ON public.calls USING btree (lead_id);


--
-- Name: idx_calls_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_calls_status ON public.calls USING btree (status);


--
-- Name: idx_calls_talklee_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_calls_talklee_id ON public.calls USING btree (talklee_call_id) WHERE (talklee_call_id IS NOT NULL);


--
-- Name: idx_calls_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_calls_tenant_id ON public.calls USING btree (tenant_id);


--
-- Name: idx_campaigns_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_campaigns_status ON public.campaigns USING btree (status);


--
-- Name: idx_campaigns_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_campaigns_tenant_id ON public.campaigns USING btree (tenant_id);


--
-- Name: idx_clients_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_clients_tenant_id ON public.clients USING btree (tenant_id);


--
-- Name: idx_connectors_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_connectors_tenant_id ON public.connectors USING btree (tenant_id);


--
-- Name: idx_conversations_call_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conversations_call_id ON public.conversations USING btree (call_id);


--
-- Name: idx_conversations_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conversations_tenant_id ON public.conversations USING btree (tenant_id);


--
-- Name: idx_dialer_jobs_failure_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dialer_jobs_failure_category ON public.dialer_jobs USING btree (failure_category) WHERE (failure_category IS NOT NULL);


--
-- Name: idx_dialer_jobs_priority; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dialer_jobs_priority ON public.dialer_jobs USING btree (priority DESC, created_at);


--
-- Name: idx_dialer_jobs_queue; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dialer_jobs_queue ON public.dialer_jobs USING btree (tenant_id, status, priority DESC, scheduled_at);


--
-- Name: idx_dialer_jobs_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dialer_jobs_status ON public.dialer_jobs USING btree (status);


--
-- Name: idx_dialer_jobs_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dialer_jobs_tenant_id ON public.dialer_jobs USING btree (tenant_id);


--
-- Name: idx_dnc_entries_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dnc_entries_expires ON public.dnc_entries USING btree (expires_at) WHERE (expires_at IS NOT NULL);


--
-- Name: idx_dnc_entries_global; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dnc_entries_global ON public.dnc_entries USING btree (normalized_number) WHERE (tenant_id IS NULL);


--
-- Name: idx_dnc_entries_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dnc_entries_number ON public.dnc_entries USING btree (normalized_number);


--
-- Name: idx_dnc_entries_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dnc_entries_tenant ON public.dnc_entries USING btree (tenant_id);


--
-- Name: idx_emergency_access_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_emergency_access_created ON public.emergency_access_requests USING btree (created_at DESC);


--
-- Name: idx_emergency_access_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_emergency_access_expires ON public.emergency_access_requests USING btree (expires_at) WHERE ((status)::text = 'approved'::text);


--
-- Name: idx_emergency_access_requestor; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_emergency_access_requestor ON public.emergency_access_requests USING btree (requestor_id);


--
-- Name: idx_emergency_access_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_emergency_access_status ON public.emergency_access_requests USING btree (status);


--
-- Name: idx_la_email_failures; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_la_email_failures ON public.login_attempts USING btree (email, created_at DESC) WHERE (success = false);


--
-- Name: idx_la_email_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_la_email_time ON public.login_attempts USING btree (email, created_at DESC);


--
-- Name: idx_la_ip_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_la_ip_time ON public.login_attempts USING btree (ip_address, created_at DESC);


--
-- Name: idx_la_user_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_la_user_time ON public.login_attempts USING btree (user_id, created_at DESC) WHERE (user_id IS NOT NULL);


--
-- Name: idx_leads_campaign_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_leads_campaign_id ON public.leads USING btree (campaign_id);


--
-- Name: idx_leads_campaign_phone_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_leads_campaign_phone_unique ON public.leads USING btree (campaign_id, phone_number) WHERE ((status)::text <> 'deleted'::text);


--
-- Name: idx_leads_crm_contact_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_leads_crm_contact_id ON public.leads USING btree (crm_contact_id) WHERE (crm_contact_id IS NOT NULL);


--
-- Name: idx_leads_phone_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_leads_phone_number ON public.leads USING btree (phone_number);


--
-- Name: idx_leads_priority; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_leads_priority ON public.leads USING btree (priority DESC, created_at);


--
-- Name: idx_leads_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_leads_status ON public.leads USING btree (status);


--
-- Name: idx_leads_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_leads_tenant_id ON public.leads USING btree (tenant_id);


--
-- Name: idx_mc_cleanup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mc_cleanup ON public.mfa_challenges USING btree (expires_at) WHERE (used = false);


--
-- Name: idx_mc_hash_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mc_hash_active ON public.mfa_challenges USING btree (challenge_hash) WHERE (used = false);


--
-- Name: idx_mc_user_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mc_user_time ON public.mfa_challenges USING btree (user_id, created_at DESC);


--
-- Name: idx_partner_limits_partner; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_partner_limits_partner ON public.partner_limits USING btree (partner_id) WHERE (is_active = true);


--
-- Name: idx_partner_limits_partner_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_partner_limits_partner_active ON public.partner_limits USING btree (partner_id, is_active);


--
-- Name: idx_permissions_resource; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_permissions_resource ON public.permissions USING btree (resource);


--
-- Name: idx_permissions_resource_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_permissions_resource_action ON public.permissions USING btree (resource, action);


--
-- Name: idx_rate_limit_events_scope; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rate_limit_events_scope ON public.rate_limit_events USING btree (tier, scope_key, triggered_at DESC);


--
-- Name: idx_rate_limit_events_triggered_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rate_limit_events_triggered_at ON public.rate_limit_events USING btree (triggered_at DESC);


--
-- Name: idx_rc_code_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rc_code_hash ON public.recovery_codes USING btree (code_hash) WHERE (used = false);


--
-- Name: idx_rc_user_batch; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rc_user_batch ON public.recovery_codes USING btree (user_id, batch_id);


--
-- Name: idx_rc_user_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rc_user_created ON public.recovery_codes USING btree (user_id, created_at DESC);


--
-- Name: idx_rc_user_unused; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rc_user_unused ON public.recovery_codes USING btree (user_id, used) WHERE (used = false);


--
-- Name: idx_recordings_call_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_recordings_call_id ON public.recordings USING btree (call_id);


--
-- Name: idx_recordings_s3_call_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_recordings_s3_call_id ON public.recordings_s3 USING btree (call_id);


--
-- Name: idx_recordings_s3_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_recordings_s3_status ON public.recordings_s3 USING btree (status);


--
-- Name: idx_recordings_s3_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_recordings_s3_tenant_id ON public.recordings_s3 USING btree (tenant_id);


--
-- Name: idx_recordings_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_recordings_tenant_id ON public.recordings USING btree (tenant_id);


--
-- Name: idx_reminders_idempotency_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_reminders_idempotency_key ON public.reminders USING btree (idempotency_key) WHERE (idempotency_key IS NOT NULL);


--
-- Name: idx_roles_level; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_roles_level ON public.roles USING btree (level DESC);


--
-- Name: idx_rp_permission_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rp_permission_id ON public.role_permissions USING btree (permission_id);


--
-- Name: idx_rp_role_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rp_role_id ON public.role_permissions USING btree (role_id);


--
-- Name: idx_rt_family; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rt_family ON public.refresh_tokens USING btree (family_id);


--
-- Name: idx_rt_token_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rt_token_lookup ON public.refresh_tokens USING btree (token_hash) WHERE (revoked_at IS NULL);


--
-- Name: idx_rt_user_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rt_user_active ON public.refresh_tokens USING btree (user_id, expires_at) WHERE (revoked_at IS NULL);


--
-- Name: idx_se_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_se_expires ON public.stream_events USING btree (expires_at);


--
-- Name: idx_se_tenant_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_se_tenant_category ON public.stream_events USING btree (tenant_id, category, created_at DESC);


--
-- Name: idx_se_tenant_recent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_se_tenant_recent ON public.stream_events USING btree (tenant_id, created_at DESC);


--
-- Name: idx_secret_access_secret; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_secret_access_secret ON public.secret_access_log USING btree (secret_id);


--
-- Name: idx_secret_access_success; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_secret_access_success ON public.secret_access_log USING btree (success);


--
-- Name: idx_secret_access_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_secret_access_tenant ON public.secret_access_log USING btree (tenant_id);


--
-- Name: idx_secret_access_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_secret_access_time ON public.secret_access_log USING btree (accessed_at DESC);


--
-- Name: idx_secret_access_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_secret_access_user ON public.secret_access_log USING btree (accessed_by);


--
-- Name: idx_security_events_alert_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_security_events_alert_type ON public.security_events USING btree (alert_type, created_at DESC) WHERE (alert_type IS NOT NULL);


--
-- Name: idx_security_events_assigned; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_security_events_assigned ON public.security_events USING btree (assigned_to);


--
-- Name: idx_security_events_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_security_events_created ON public.security_events USING btree (created_at DESC);


--
-- Name: idx_security_events_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_security_events_created_at ON public.security_events USING btree (created_at DESC);


--
-- Name: idx_security_events_event_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_security_events_event_type ON public.security_events USING btree (event_type);


--
-- Name: idx_security_events_severity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_security_events_severity ON public.security_events USING btree (severity);


--
-- Name: idx_security_events_sla; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_security_events_sla ON public.security_events USING btree (sla_deadline) WHERE ((status)::text = 'open'::text);


--
-- Name: idx_security_events_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_security_events_status ON public.security_events USING btree (status);


--
-- Name: idx_security_events_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_security_events_tenant ON public.security_events USING btree (tenant_id);


--
-- Name: idx_security_events_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_security_events_user ON public.security_events USING btree (user_id);


--
-- Name: idx_ss_active_binding; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ss_active_binding ON public.security_sessions USING btree (session_token_hash, bound_ip, device_fingerprint) WHERE (revoked = false);


--
-- Name: idx_ss_cleanup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ss_cleanup ON public.security_sessions USING btree (expires_at) WHERE (revoked = false);


--
-- Name: idx_ss_fingerprint; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ss_fingerprint ON public.security_sessions USING btree (device_fingerprint) WHERE (device_fingerprint IS NOT NULL);


--
-- Name: idx_ss_mfa_verified; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ss_mfa_verified ON public.security_sessions USING btree (user_id, mfa_verified) WHERE (mfa_verified = true);


--
-- Name: idx_ss_suspicious; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ss_suspicious ON public.security_sessions USING btree (user_id, is_suspicious, suspicious_detected_at DESC) WHERE (is_suspicious = true);


--
-- Name: idx_ss_suspicious_cleanup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ss_suspicious_cleanup ON public.security_sessions USING btree (suspicious_detected_at) WHERE (is_suspicious = true);


--
-- Name: idx_ss_token_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ss_token_lookup ON public.security_sessions USING btree (session_token_hash) WHERE (revoked = false);


--
-- Name: idx_ss_user_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ss_user_active ON public.security_sessions USING btree (user_id, last_active_at DESC) WHERE (revoked = false);


--
-- Name: idx_ss_user_all; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ss_user_all ON public.security_sessions USING btree (user_id, created_at DESC);


--
-- Name: idx_ss_user_session_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ss_user_session_number ON public.security_sessions USING btree (user_id, session_number DESC) WHERE (revoked = false);


--
-- Name: idx_suspension_events_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_suspension_events_active ON public.suspension_events USING btree (target_id, is_active) WHERE (is_active = true);


--
-- Name: idx_suspension_events_appeal; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_suspension_events_appeal ON public.suspension_events USING btree (appeal_submitted_at) WHERE ((appeal_decision)::text = 'pending'::text);


--
-- Name: idx_suspension_events_suspended_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_suspension_events_suspended_at ON public.suspension_events USING btree (suspended_at DESC);


--
-- Name: idx_suspension_events_target; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_suspension_events_target ON public.suspension_events USING btree (target_type, target_id);


--
-- Name: idx_suspension_events_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_suspension_events_tenant ON public.suspension_events USING btree (target_id) WHERE ((target_type)::text = 'tenant'::text);


--
-- Name: idx_suspension_events_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_suspension_events_type ON public.suspension_events USING btree (suspension_type);


--
-- Name: idx_suspension_events_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_suspension_events_user ON public.suspension_events USING btree (target_id) WHERE ((target_type)::text = 'user'::text);


--
-- Name: idx_suspension_queue_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_suspension_queue_status ON public.suspension_propagation_queue USING btree (status, next_attempt_at);


--
-- Name: idx_suspension_queue_suspension; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_suspension_queue_suspension ON public.suspension_propagation_queue USING btree (suspension_id);


--
-- Name: idx_tenant_ai_configs_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenant_ai_configs_tenant_id ON public.tenant_ai_configs USING btree (tenant_id);


--
-- Name: idx_tenant_call_limits_effective; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenant_call_limits_effective ON public.tenant_call_limits USING btree (tenant_id, effective_from, effective_until);


--
-- Name: idx_tenant_call_limits_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenant_call_limits_tenant ON public.tenant_call_limits USING btree (tenant_id) WHERE (is_active = true);


--
-- Name: idx_tenant_call_limits_tenant_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenant_call_limits_tenant_active ON public.tenant_call_limits USING btree (tenant_id, is_active, effective_from DESC);


--
-- Name: idx_tenant_codec_policies_tenant_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenant_codec_policies_tenant_active ON public.tenant_codec_policies USING btree (tenant_id, is_active);


--
-- Name: idx_tenant_codec_policies_tenant_id_id_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_tenant_codec_policies_tenant_id_id_unique ON public.tenant_codec_policies USING btree (tenant_id, id);


--
-- Name: idx_tenant_codec_policies_tenant_name_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_tenant_codec_policies_tenant_name_unique ON public.tenant_codec_policies USING btree (tenant_id, lower((policy_name)::text));


--
-- Name: idx_tenant_phone_numbers_tenant_e164; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenant_phone_numbers_tenant_e164 ON public.tenant_phone_numbers USING btree (tenant_id, e164) WHERE (status = 'verified'::text);


--
-- Name: idx_tenant_phone_numbers_tenant_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenant_phone_numbers_tenant_status ON public.tenant_phone_numbers USING btree (tenant_id, status);


--
-- Name: idx_tenant_route_policies_tenant_name_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_tenant_route_policies_tenant_name_unique ON public.tenant_route_policies USING btree (tenant_id, lower((policy_name)::text));


--
-- Name: idx_tenant_route_policies_tenant_route_active_priority; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenant_route_policies_tenant_route_active_priority ON public.tenant_route_policies USING btree (tenant_id, route_type, is_active, priority);


--
-- Name: idx_tenant_runtime_policy_events_policy_version; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenant_runtime_policy_events_policy_version ON public.tenant_runtime_policy_events USING btree (policy_version_id, created_at DESC);


--
-- Name: idx_tenant_runtime_policy_events_tenant_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenant_runtime_policy_events_tenant_created ON public.tenant_runtime_policy_events USING btree (tenant_id, created_at DESC);


--
-- Name: idx_tenant_runtime_policy_versions_tenant_active_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_tenant_runtime_policy_versions_tenant_active_unique ON public.tenant_runtime_policy_versions USING btree (tenant_id) WHERE (is_active = true);


--
-- Name: idx_tenant_runtime_policy_versions_tenant_last_good; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenant_runtime_policy_versions_tenant_last_good ON public.tenant_runtime_policy_versions USING btree (tenant_id, is_last_good, policy_version DESC);


--
-- Name: idx_tenant_runtime_policy_versions_tenant_version; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenant_runtime_policy_versions_tenant_version ON public.tenant_runtime_policy_versions USING btree (tenant_id, policy_version DESC);


--
-- Name: idx_tenant_secrets_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenant_secrets_active ON public.tenant_secrets USING btree (tenant_id, is_active) WHERE (is_active = true);


--
-- Name: idx_tenant_secrets_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenant_secrets_expires ON public.tenant_secrets USING btree (expires_at) WHERE ((expires_at IS NOT NULL) AND (is_active = true));


--
-- Name: idx_tenant_secrets_rotated_from; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenant_secrets_rotated_from ON public.tenant_secrets USING btree (rotated_from);


--
-- Name: idx_tenant_secrets_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenant_secrets_tenant ON public.tenant_secrets USING btree (tenant_id);


--
-- Name: idx_tenant_secrets_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenant_secrets_type ON public.tenant_secrets USING btree (secret_type);


--
-- Name: idx_tenant_sip_trunks_tenant_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenant_sip_trunks_tenant_active ON public.tenant_sip_trunks USING btree (tenant_id, is_active);


--
-- Name: idx_tenant_sip_trunks_tenant_id_id_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_tenant_sip_trunks_tenant_id_id_unique ON public.tenant_sip_trunks USING btree (tenant_id, id);


--
-- Name: idx_tenant_sip_trunks_tenant_name_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_tenant_sip_trunks_tenant_name_unique ON public.tenant_sip_trunks USING btree (tenant_id, lower((trunk_name)::text));


--
-- Name: idx_tenant_sip_trust_policies_tenant_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenant_sip_trust_policies_tenant_active ON public.tenant_sip_trust_policies USING btree (tenant_id, is_active, priority);


--
-- Name: idx_tenant_sip_trust_policies_tenant_name_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_tenant_sip_trust_policies_tenant_name_unique ON public.tenant_sip_trust_policies USING btree (tenant_id, lower((policy_name)::text));


--
-- Name: idx_tenant_telephony_concurrency_events_event_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenant_telephony_concurrency_events_event_type ON public.tenant_telephony_concurrency_events USING btree (tenant_id, event_type, created_at DESC);


--
-- Name: idx_tenant_telephony_concurrency_events_tenant_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenant_telephony_concurrency_events_tenant_created ON public.tenant_telephony_concurrency_events USING btree (tenant_id, created_at DESC);


--
-- Name: idx_tenant_telephony_concurrency_leases_active_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_tenant_telephony_concurrency_leases_active_unique ON public.tenant_telephony_concurrency_leases USING btree (tenant_id, call_id, lease_kind) WHERE ((released_at IS NULL) AND ((state)::text = ANY (ARRAY[('active'::character varying)::text, ('releasing'::character varying)::text])));


--
-- Name: idx_tenant_telephony_concurrency_leases_tenant_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenant_telephony_concurrency_leases_tenant_active ON public.tenant_telephony_concurrency_leases USING btree (tenant_id, state, acquired_at DESC);


--
-- Name: idx_tenant_telephony_concurrency_leases_tenant_heartbeat; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenant_telephony_concurrency_leases_tenant_heartbeat ON public.tenant_telephony_concurrency_leases USING btree (tenant_id, last_heartbeat_at DESC);


--
-- Name: idx_tenant_telephony_concurrency_policy_active_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_tenant_telephony_concurrency_policy_active_unique ON public.tenant_telephony_concurrency_policies USING btree (tenant_id) WHERE (is_active = true);


--
-- Name: idx_tenant_telephony_concurrency_policy_tenant_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenant_telephony_concurrency_policy_tenant_active ON public.tenant_telephony_concurrency_policies USING btree (tenant_id, is_active, updated_at DESC);


--
-- Name: idx_tenant_telephony_idempotency_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenant_telephony_idempotency_expires ON public.tenant_telephony_idempotency USING btree (expires_at);


--
-- Name: idx_tenant_telephony_idempotency_tenant_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenant_telephony_idempotency_tenant_created ON public.tenant_telephony_idempotency USING btree (tenant_id, created_at DESC);


--
-- Name: idx_tenant_telephony_quota_events_policy; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenant_telephony_quota_events_policy ON public.tenant_telephony_quota_events USING btree (policy_id, created_at DESC);


--
-- Name: idx_tenant_telephony_quota_events_scope_metric; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenant_telephony_quota_events_scope_metric ON public.tenant_telephony_quota_events USING btree (tenant_id, policy_scope, metric_key, created_at DESC);


--
-- Name: idx_tenant_telephony_quota_events_tenant_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenant_telephony_quota_events_tenant_created ON public.tenant_telephony_quota_events USING btree (tenant_id, created_at DESC);


--
-- Name: idx_tenant_telephony_threshold_metric; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenant_telephony_threshold_metric ON public.tenant_telephony_threshold_policies USING btree (tenant_id, policy_scope, metric_key);


--
-- Name: idx_tenant_telephony_threshold_scope_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenant_telephony_threshold_scope_active ON public.tenant_telephony_threshold_policies USING btree (tenant_id, policy_scope, is_active);


--
-- Name: idx_tenants_is_partner; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenants_is_partner ON public.tenants USING btree (is_partner) WHERE (is_partner = true);


--
-- Name: idx_tenants_partner; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenants_partner ON public.tenants USING btree (partner_id) WHERE (partner_id IS NOT NULL);


--
-- Name: idx_tenants_plan_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenants_plan_id ON public.tenants USING btree (plan_id);


--
-- Name: idx_tenants_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenants_status ON public.tenants USING btree (status);


--
-- Name: idx_tenants_subscription_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenants_subscription_status ON public.tenants USING btree (subscription_status);


--
-- Name: idx_tenants_white_label_partner_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenants_white_label_partner_id ON public.tenants USING btree (white_label_partner_id);


--
-- Name: idx_transcripts_call_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_transcripts_call_id ON public.transcripts USING btree (call_id);


--
-- Name: idx_transcripts_full_text_search; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_transcripts_full_text_search ON public.transcripts USING gin (to_tsvector('english'::regconfig, COALESCE(full_text, ''::text)));


--
-- Name: idx_transcripts_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_transcripts_tenant_id ON public.transcripts USING btree (tenant_id);


--
-- Name: idx_ttc_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ttc_tenant ON public.tenant_telephony_credentials USING btree (tenant_id);


--
-- Name: idx_tu_role_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tu_role_id ON public.tenant_users USING btree (role_id);


--
-- Name: idx_tu_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tu_status ON public.tenant_users USING btree (status) WHERE ((status)::text = ANY (ARRAY[('pending'::character varying)::text, ('active'::character varying)::text]));


--
-- Name: idx_tu_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tu_tenant_id ON public.tenant_users USING btree (tenant_id);


--
-- Name: idx_tu_user_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tu_user_active ON public.tenant_users USING btree (user_id) WHERE ((status)::text = 'active'::text);


--
-- Name: idx_tu_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tu_user_id ON public.tenant_users USING btree (user_id);


--
-- Name: idx_tu_user_primary; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_tu_user_primary ON public.tenant_users USING btree (user_id) WHERE (is_primary = true);


--
-- Name: idx_up_credential_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_up_credential_id ON public.user_passkeys USING btree (credential_id);


--
-- Name: idx_up_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_up_expires ON public.user_permissions USING btree (expires_at) WHERE (expires_at IS NOT NULL);


--
-- Name: idx_up_permission_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_up_permission_id ON public.user_permissions USING btree (permission_id);


--
-- Name: idx_up_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_up_user_id ON public.user_permissions USING btree (user_id);


--
-- Name: idx_user_mfa_enabled; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_mfa_enabled ON public.user_mfa USING btree (user_id) WHERE (enabled = true);


--
-- Name: idx_user_mfa_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_mfa_user_id ON public.user_mfa USING btree (user_id);


--
-- Name: idx_user_profiles_email; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_profiles_email ON public.user_profiles USING btree (email);


--
-- Name: idx_user_profiles_has_passkey; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_profiles_has_passkey ON public.user_profiles USING btree (passkey_count) WHERE (passkey_count > 0);


--
-- Name: idx_user_profiles_is_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_profiles_is_active ON public.user_profiles USING btree (is_active) WHERE (is_active = false);


--
-- Name: idx_user_profiles_is_verified; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_profiles_is_verified ON public.user_profiles USING btree (is_verified) WHERE (is_verified = false);


--
-- Name: idx_user_profiles_mfa_enabled; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_profiles_mfa_enabled ON public.user_profiles USING btree (mfa_enabled) WHERE (mfa_enabled = true);


--
-- Name: idx_user_profiles_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_profiles_tenant_id ON public.user_profiles USING btree (tenant_id);


--
-- Name: idx_user_profiles_verification_token; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_profiles_verification_token ON public.user_profiles USING btree (verification_token) WHERE (verification_token IS NOT NULL);


--
-- Name: idx_wc_cleanup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wc_cleanup ON public.webauthn_challenges USING btree (expires_at) WHERE (used = false);


--
-- Name: idx_wc_id_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wc_id_active ON public.webauthn_challenges USING btree (id) WHERE (used = false);


--
-- Name: idx_wc_user_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wc_user_time ON public.webauthn_challenges USING btree (user_id, created_at DESC) WHERE (user_id IS NOT NULL);


--
-- Name: idx_webhook_configs_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_webhook_configs_tenant ON public.webhook_configs USING btree (tenant_id, webhook_name) WHERE (is_active = true);


--
-- Name: idx_white_label_partners_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_white_label_partners_status ON public.white_label_partners USING btree (status);


--
-- Name: tenant_phone_numbers trg_tenant_phone_numbers_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_tenant_phone_numbers_updated_at BEFORE UPDATE ON public.tenant_phone_numbers FOR EACH ROW EXECUTE FUNCTION public.tenant_phone_numbers_touch_updated_at();


--
-- Name: abuse_detection_rules update_abuse_detection_rules_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_abuse_detection_rules_updated_at BEFORE UPDATE ON public.abuse_detection_rules FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: action_plans update_action_plans_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_action_plans_updated_at BEFORE UPDATE ON public.action_plans FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: assistant_actions update_assistant_actions_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_assistant_actions_updated_at BEFORE UPDATE ON public.assistant_actions FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: assistant_conversations update_assistant_conversations_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_assistant_conversations_updated_at BEFORE UPDATE ON public.assistant_conversations FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: call_legs update_call_legs_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_call_legs_updated_at BEFORE UPDATE ON public.call_legs FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: calls update_calls_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_calls_updated_at BEFORE UPDATE ON public.calls FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: campaigns update_campaigns_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_campaigns_updated_at BEFORE UPDATE ON public.campaigns FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

ALTER TABLE public.campaigns DISABLE TRIGGER update_campaigns_updated_at;


--
-- Name: clients update_clients_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_clients_updated_at BEFORE UPDATE ON public.clients FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: connector_accounts update_connector_accounts_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_connector_accounts_updated_at BEFORE UPDATE ON public.connector_accounts FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: connectors update_connectors_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_connectors_updated_at BEFORE UPDATE ON public.connectors FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: conversations update_conversations_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_conversations_updated_at BEFORE UPDATE ON public.conversations FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: dialer_jobs update_dialer_jobs_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_dialer_jobs_updated_at BEFORE UPDATE ON public.dialer_jobs FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: leads update_leads_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_leads_updated_at BEFORE UPDATE ON public.leads FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: meetings update_meetings_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_meetings_updated_at BEFORE UPDATE ON public.meetings FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: partner_limits update_partner_limits_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_partner_limits_updated_at BEFORE UPDATE ON public.partner_limits FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: plans update_plans_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_plans_updated_at BEFORE UPDATE ON public.plans FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: reminders update_reminders_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_reminders_updated_at BEFORE UPDATE ON public.reminders FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: subscriptions update_subscriptions_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_subscriptions_updated_at BEFORE UPDATE ON public.subscriptions FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: tenant_call_limits update_tenant_call_limits_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_tenant_call_limits_updated_at BEFORE UPDATE ON public.tenant_call_limits FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: tenant_codec_policies update_tenant_codec_policies_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_tenant_codec_policies_updated_at BEFORE UPDATE ON public.tenant_codec_policies FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: tenant_quota_usage update_tenant_quota_usage_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_tenant_quota_usage_updated_at BEFORE UPDATE ON public.tenant_quota_usage FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: tenant_quotas update_tenant_quotas_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_tenant_quotas_updated_at BEFORE UPDATE ON public.tenant_quotas FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: tenant_route_policies update_tenant_route_policies_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_tenant_route_policies_updated_at BEFORE UPDATE ON public.tenant_route_policies FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: tenant_runtime_policy_events update_tenant_runtime_policy_events_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_tenant_runtime_policy_events_updated_at BEFORE UPDATE ON public.tenant_runtime_policy_events FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: tenant_runtime_policy_versions update_tenant_runtime_policy_versions_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_tenant_runtime_policy_versions_updated_at BEFORE UPDATE ON public.tenant_runtime_policy_versions FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: tenant_secrets update_tenant_secrets_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_tenant_secrets_updated_at BEFORE UPDATE ON public.tenant_secrets FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: tenant_settings update_tenant_settings_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_tenant_settings_updated_at BEFORE UPDATE ON public.tenant_settings FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: tenant_sip_trunks update_tenant_sip_trunks_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_tenant_sip_trunks_updated_at BEFORE UPDATE ON public.tenant_sip_trunks FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: tenant_sip_trust_policies update_tenant_sip_trust_policies_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_tenant_sip_trust_policies_updated_at BEFORE UPDATE ON public.tenant_sip_trust_policies FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: tenant_telephony_concurrency_leases update_tenant_telephony_concurrency_leases_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_tenant_telephony_concurrency_leases_updated_at BEFORE UPDATE ON public.tenant_telephony_concurrency_leases FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: tenant_telephony_concurrency_policies update_tenant_telephony_concurrency_policies_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_tenant_telephony_concurrency_policies_updated_at BEFORE UPDATE ON public.tenant_telephony_concurrency_policies FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: tenant_telephony_threshold_policies update_tenant_telephony_threshold_policies_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_tenant_telephony_threshold_policies_updated_at BEFORE UPDATE ON public.tenant_telephony_threshold_policies FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: tenants update_tenants_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_tenants_updated_at BEFORE UPDATE ON public.tenants FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: transcripts update_transcripts_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_transcripts_updated_at BEFORE UPDATE ON public.transcripts FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: user_profiles update_user_profiles_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_user_profiles_updated_at BEFORE UPDATE ON public.user_profiles FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: webhook_configs update_webhook_configs_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_webhook_configs_updated_at BEFORE UPDATE ON public.webhook_configs FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: abuse_detection_rules abuse_detection_rules_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.abuse_detection_rules
    ADD CONSTRAINT abuse_detection_rules_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: abuse_events abuse_events_resolved_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.abuse_events
    ADD CONSTRAINT abuse_events_resolved_by_fkey FOREIGN KEY (resolved_by) REFERENCES public.user_profiles(id) ON DELETE SET NULL;


--
-- Name: abuse_events abuse_events_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.abuse_events
    ADD CONSTRAINT abuse_events_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: action_plans action_plans_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_plans
    ADD CONSTRAINT action_plans_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: action_plans action_plans_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_plans
    ADD CONSTRAINT action_plans_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.user_profiles(id);


--
-- Name: assistant_actions assistant_actions_call_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_actions
    ADD CONSTRAINT assistant_actions_call_id_fkey FOREIGN KEY (call_id) REFERENCES public.calls(id) ON DELETE SET NULL;


--
-- Name: assistant_actions assistant_actions_campaign_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_actions
    ADD CONSTRAINT assistant_actions_campaign_id_fkey FOREIGN KEY (campaign_id) REFERENCES public.campaigns(id) ON DELETE SET NULL;


--
-- Name: assistant_actions assistant_actions_connector_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_actions
    ADD CONSTRAINT assistant_actions_connector_id_fkey FOREIGN KEY (connector_id) REFERENCES public.connectors(id) ON DELETE SET NULL;


--
-- Name: assistant_actions assistant_actions_conversation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_actions
    ADD CONSTRAINT assistant_actions_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.assistant_conversations(id) ON DELETE SET NULL;


--
-- Name: assistant_actions assistant_actions_lead_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_actions
    ADD CONSTRAINT assistant_actions_lead_id_fkey FOREIGN KEY (lead_id) REFERENCES public.leads(id) ON DELETE SET NULL;


--
-- Name: assistant_actions assistant_actions_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_actions
    ADD CONSTRAINT assistant_actions_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: assistant_actions assistant_actions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_actions
    ADD CONSTRAINT assistant_actions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.user_profiles(id) ON DELETE SET NULL;


--
-- Name: assistant_conversations assistant_conversations_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_conversations
    ADD CONSTRAINT assistant_conversations_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: assistant_conversations assistant_conversations_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assistant_conversations
    ADD CONSTRAINT assistant_conversations_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.user_profiles(id) ON DELETE SET NULL;


--
-- Name: audit_chain_state audit_chain_state_last_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_chain_state
    ADD CONSTRAINT audit_chain_state_last_event_id_fkey FOREIGN KEY (last_event_id) REFERENCES public.audit_logs(event_id);


--
-- Name: audit_logs audit_logs_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.user_profiles(id) ON DELETE SET NULL;


--
-- Name: audit_logs audit_logs_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);


--
-- Name: call_events call_events_call_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_events
    ADD CONSTRAINT call_events_call_id_fkey FOREIGN KEY (call_id) REFERENCES public.calls(id) ON DELETE CASCADE;


--
-- Name: call_events call_events_leg_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_events
    ADD CONSTRAINT call_events_leg_id_fkey FOREIGN KEY (leg_id) REFERENCES public.call_legs(id) ON DELETE SET NULL;


--
-- Name: call_guard_decisions call_guard_decisions_partner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_guard_decisions
    ADD CONSTRAINT call_guard_decisions_partner_id_fkey FOREIGN KEY (partner_id) REFERENCES public.tenants(id) ON DELETE SET NULL;


--
-- Name: call_guard_decisions call_guard_decisions_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_guard_decisions
    ADD CONSTRAINT call_guard_decisions_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: call_legs call_legs_call_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_legs
    ADD CONSTRAINT call_legs_call_id_fkey FOREIGN KEY (call_id) REFERENCES public.calls(id) ON DELETE CASCADE;


--
-- Name: call_velocity_snapshots call_velocity_snapshots_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_velocity_snapshots
    ADD CONSTRAINT call_velocity_snapshots_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: calls calls_campaign_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.calls
    ADD CONSTRAINT calls_campaign_id_fkey FOREIGN KEY (campaign_id) REFERENCES public.campaigns(id) ON DELETE CASCADE;


--
-- Name: calls calls_lead_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.calls
    ADD CONSTRAINT calls_lead_id_fkey FOREIGN KEY (lead_id) REFERENCES public.leads(id) ON DELETE CASCADE;


--
-- Name: calls calls_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.calls
    ADD CONSTRAINT calls_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: campaigns campaigns_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaigns
    ADD CONSTRAINT campaigns_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: clients clients_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clients
    ADD CONSTRAINT clients_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: connector_accounts connector_accounts_connector_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.connector_accounts
    ADD CONSTRAINT connector_accounts_connector_id_fkey FOREIGN KEY (connector_id) REFERENCES public.connectors(id) ON DELETE CASCADE;


--
-- Name: connector_accounts connector_accounts_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.connector_accounts
    ADD CONSTRAINT connector_accounts_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: connectors connectors_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.connectors
    ADD CONSTRAINT connectors_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: conversations conversations_call_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT conversations_call_id_fkey FOREIGN KEY (call_id) REFERENCES public.calls(id) ON DELETE CASCADE;


--
-- Name: conversations conversations_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversations
    ADD CONSTRAINT conversations_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: dialer_jobs dialer_jobs_call_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dialer_jobs
    ADD CONSTRAINT dialer_jobs_call_id_fkey FOREIGN KEY (call_id) REFERENCES public.calls(id);


--
-- Name: dialer_jobs dialer_jobs_campaign_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dialer_jobs
    ADD CONSTRAINT dialer_jobs_campaign_id_fkey FOREIGN KEY (campaign_id) REFERENCES public.campaigns(id) ON DELETE CASCADE;


--
-- Name: dialer_jobs dialer_jobs_lead_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dialer_jobs
    ADD CONSTRAINT dialer_jobs_lead_id_fkey FOREIGN KEY (lead_id) REFERENCES public.leads(id) ON DELETE CASCADE;


--
-- Name: dialer_jobs dialer_jobs_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dialer_jobs
    ADD CONSTRAINT dialer_jobs_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: dnc_entries dnc_entries_added_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dnc_entries
    ADD CONSTRAINT dnc_entries_added_by_fkey FOREIGN KEY (added_by) REFERENCES public.user_profiles(id) ON DELETE SET NULL;


--
-- Name: dnc_entries dnc_entries_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dnc_entries
    ADD CONSTRAINT dnc_entries_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: emergency_access_requests emergency_access_requests_requestor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.emergency_access_requests
    ADD CONSTRAINT emergency_access_requests_requestor_id_fkey FOREIGN KEY (requestor_id) REFERENCES public.user_profiles(id);


--
-- Name: emergency_access_requests emergency_access_requests_reviewed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.emergency_access_requests
    ADD CONSTRAINT emergency_access_requests_reviewed_by_fkey FOREIGN KEY (reviewed_by) REFERENCES public.user_profiles(id);


--
-- Name: login_attempts fk_login_attempts_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.login_attempts
    ADD CONSTRAINT fk_login_attempts_user FOREIGN KEY (user_id) REFERENCES public.user_profiles(id) ON UPDATE CASCADE ON DELETE SET NULL;


--
-- Name: mfa_challenges fk_mfa_challenges_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mfa_challenges
    ADD CONSTRAINT fk_mfa_challenges_user FOREIGN KEY (user_id) REFERENCES public.user_profiles(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: recovery_codes fk_recovery_codes_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recovery_codes
    ADD CONSTRAINT fk_recovery_codes_user FOREIGN KEY (user_id) REFERENCES public.user_profiles(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: role_permissions fk_rp_permission; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role_permissions
    ADD CONSTRAINT fk_rp_permission FOREIGN KEY (permission_id) REFERENCES public.permissions(id) ON DELETE CASCADE;


--
-- Name: role_permissions fk_rp_role; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role_permissions
    ADD CONSTRAINT fk_rp_role FOREIGN KEY (role_id) REFERENCES public.roles(id) ON DELETE CASCADE;


--
-- Name: security_sessions fk_security_sessions_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.security_sessions
    ADD CONSTRAINT fk_security_sessions_user FOREIGN KEY (user_id) REFERENCES public.user_profiles(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: tenant_route_policies fk_tenant_route_policies_codec; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_route_policies
    ADD CONSTRAINT fk_tenant_route_policies_codec FOREIGN KEY (tenant_id, codec_policy_id) REFERENCES public.tenant_codec_policies(tenant_id, id) ON DELETE SET NULL;


--
-- Name: tenant_route_policies fk_tenant_route_policies_trunk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_route_policies
    ADD CONSTRAINT fk_tenant_route_policies_trunk FOREIGN KEY (tenant_id, target_trunk_id) REFERENCES public.tenant_sip_trunks(tenant_id, id) ON DELETE RESTRICT;


--
-- Name: tenant_users fk_tu_inviter; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_users
    ADD CONSTRAINT fk_tu_inviter FOREIGN KEY (invited_by) REFERENCES public.user_profiles(id) ON DELETE SET NULL;


--
-- Name: tenant_users fk_tu_role; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_users
    ADD CONSTRAINT fk_tu_role FOREIGN KEY (role_id) REFERENCES public.roles(id) ON DELETE RESTRICT;


--
-- Name: tenant_users fk_tu_tenant; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_users
    ADD CONSTRAINT fk_tu_tenant FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: tenant_users fk_tu_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_users
    ADD CONSTRAINT fk_tu_user FOREIGN KEY (user_id) REFERENCES public.user_profiles(id) ON DELETE CASCADE;


--
-- Name: user_permissions fk_up_granter; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_permissions
    ADD CONSTRAINT fk_up_granter FOREIGN KEY (granted_by) REFERENCES public.user_profiles(id) ON DELETE SET NULL;


--
-- Name: user_permissions fk_up_permission; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_permissions
    ADD CONSTRAINT fk_up_permission FOREIGN KEY (permission_id) REFERENCES public.permissions(id) ON DELETE CASCADE;


--
-- Name: user_permissions fk_up_tenant; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_permissions
    ADD CONSTRAINT fk_up_tenant FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: user_permissions fk_up_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_permissions
    ADD CONSTRAINT fk_up_user FOREIGN KEY (user_id) REFERENCES public.user_profiles(id) ON DELETE CASCADE;


--
-- Name: user_mfa fk_user_mfa_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_mfa
    ADD CONSTRAINT fk_user_mfa_user FOREIGN KEY (user_id) REFERENCES public.user_profiles(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: invoices invoices_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: leads leads_campaign_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.leads
    ADD CONSTRAINT leads_campaign_id_fkey FOREIGN KEY (campaign_id) REFERENCES public.campaigns(id) ON DELETE CASCADE;


--
-- Name: leads leads_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.leads
    ADD CONSTRAINT leads_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: meetings meetings_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meetings
    ADD CONSTRAINT meetings_action_id_fkey FOREIGN KEY (action_id) REFERENCES public.assistant_actions(id) ON DELETE SET NULL;


--
-- Name: meetings meetings_call_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meetings
    ADD CONSTRAINT meetings_call_id_fkey FOREIGN KEY (call_id) REFERENCES public.calls(id) ON DELETE SET NULL;


--
-- Name: meetings meetings_connector_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meetings
    ADD CONSTRAINT meetings_connector_id_fkey FOREIGN KEY (connector_id) REFERENCES public.connectors(id) ON DELETE SET NULL;


--
-- Name: meetings meetings_lead_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meetings
    ADD CONSTRAINT meetings_lead_id_fkey FOREIGN KEY (lead_id) REFERENCES public.leads(id) ON DELETE SET NULL;


--
-- Name: meetings meetings_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meetings
    ADD CONSTRAINT meetings_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: partner_limits partner_limits_partner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.partner_limits
    ADD CONSTRAINT partner_limits_partner_id_fkey FOREIGN KEY (partner_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: recordings recordings_call_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recordings
    ADD CONSTRAINT recordings_call_id_fkey FOREIGN KEY (call_id) REFERENCES public.calls(id) ON DELETE CASCADE;


--
-- Name: recordings_s3 recordings_s3_call_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recordings_s3
    ADD CONSTRAINT recordings_s3_call_id_fkey FOREIGN KEY (call_id) REFERENCES public.calls(id) ON DELETE CASCADE;


--
-- Name: recordings_s3 recordings_s3_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recordings_s3
    ADD CONSTRAINT recordings_s3_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: recordings recordings_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recordings
    ADD CONSTRAINT recordings_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: refresh_tokens refresh_tokens_parent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.refresh_tokens
    ADD CONSTRAINT refresh_tokens_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.refresh_tokens(id) ON DELETE SET NULL;


--
-- Name: refresh_tokens refresh_tokens_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.refresh_tokens
    ADD CONSTRAINT refresh_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.user_profiles(id) ON DELETE CASCADE;


--
-- Name: reminders reminders_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reminders
    ADD CONSTRAINT reminders_action_id_fkey FOREIGN KEY (action_id) REFERENCES public.assistant_actions(id) ON DELETE SET NULL;


--
-- Name: reminders reminders_lead_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reminders
    ADD CONSTRAINT reminders_lead_id_fkey FOREIGN KEY (lead_id) REFERENCES public.leads(id) ON DELETE SET NULL;


--
-- Name: reminders reminders_meeting_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reminders
    ADD CONSTRAINT reminders_meeting_id_fkey FOREIGN KEY (meeting_id) REFERENCES public.meetings(id) ON DELETE CASCADE;


--
-- Name: reminders reminders_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reminders
    ADD CONSTRAINT reminders_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: secret_access_log secret_access_log_accessed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.secret_access_log
    ADD CONSTRAINT secret_access_log_accessed_by_fkey FOREIGN KEY (accessed_by) REFERENCES public.user_profiles(id);


--
-- Name: secret_access_log secret_access_log_secret_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.secret_access_log
    ADD CONSTRAINT secret_access_log_secret_id_fkey FOREIGN KEY (secret_id) REFERENCES public.tenant_secrets(secret_id);


--
-- Name: secret_access_log secret_access_log_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.secret_access_log
    ADD CONSTRAINT secret_access_log_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);


--
-- Name: security_events security_events_assigned_to_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.security_events
    ADD CONSTRAINT security_events_assigned_to_fkey FOREIGN KEY (assigned_to) REFERENCES public.user_profiles(id);


--
-- Name: security_events security_events_resolved_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.security_events
    ADD CONSTRAINT security_events_resolved_by_fkey FOREIGN KEY (resolved_by) REFERENCES public.user_profiles(id);


--
-- Name: security_events security_events_rule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.security_events
    ADD CONSTRAINT security_events_rule_id_fkey FOREIGN KEY (rule_id) REFERENCES public.abuse_detection_rules(id);


--
-- Name: security_events security_events_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.security_events
    ADD CONSTRAINT security_events_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);


--
-- Name: security_events security_events_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.security_events
    ADD CONSTRAINT security_events_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.user_profiles(id);


--
-- Name: stream_events stream_events_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stream_events
    ADD CONSTRAINT stream_events_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: subscriptions subscriptions_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscriptions
    ADD CONSTRAINT subscriptions_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES public.plans(id);


--
-- Name: subscriptions subscriptions_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscriptions
    ADD CONSTRAINT subscriptions_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: suspension_events suspension_events_appeal_reviewed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.suspension_events
    ADD CONSTRAINT suspension_events_appeal_reviewed_by_fkey FOREIGN KEY (appeal_reviewed_by) REFERENCES public.user_profiles(id);


--
-- Name: suspension_events suspension_events_audit_log_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.suspension_events
    ADD CONSTRAINT suspension_events_audit_log_id_fkey FOREIGN KEY (audit_log_id) REFERENCES public.audit_logs(event_id);


--
-- Name: suspension_events suspension_events_restored_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.suspension_events
    ADD CONSTRAINT suspension_events_restored_by_fkey FOREIGN KEY (restored_by) REFERENCES public.user_profiles(id);


--
-- Name: suspension_events suspension_events_suspended_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.suspension_events
    ADD CONSTRAINT suspension_events_suspended_by_fkey FOREIGN KEY (suspended_by) REFERENCES public.user_profiles(id);


--
-- Name: suspension_propagation_queue suspension_propagation_queue_suspension_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.suspension_propagation_queue
    ADD CONSTRAINT suspension_propagation_queue_suspension_id_fkey FOREIGN KEY (suspension_id) REFERENCES public.suspension_events(suspension_id);


--
-- Name: tenant_ai_configs tenant_ai_configs_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_ai_configs
    ADD CONSTRAINT tenant_ai_configs_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: tenant_call_limits tenant_call_limits_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_call_limits
    ADD CONSTRAINT tenant_call_limits_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: tenant_codec_policies tenant_codec_policies_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_codec_policies
    ADD CONSTRAINT tenant_codec_policies_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.user_profiles(id) ON DELETE SET NULL;


--
-- Name: tenant_codec_policies tenant_codec_policies_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_codec_policies
    ADD CONSTRAINT tenant_codec_policies_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: tenant_codec_policies tenant_codec_policies_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_codec_policies
    ADD CONSTRAINT tenant_codec_policies_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.user_profiles(id) ON DELETE SET NULL;


--
-- Name: tenant_phone_numbers tenant_phone_numbers_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_phone_numbers
    ADD CONSTRAINT tenant_phone_numbers_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: tenant_quota_usage tenant_quota_usage_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_quota_usage
    ADD CONSTRAINT tenant_quota_usage_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: tenant_quotas tenant_quotas_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_quotas
    ADD CONSTRAINT tenant_quotas_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: tenant_route_policies tenant_route_policies_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_route_policies
    ADD CONSTRAINT tenant_route_policies_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.user_profiles(id) ON DELETE SET NULL;


--
-- Name: tenant_route_policies tenant_route_policies_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_route_policies
    ADD CONSTRAINT tenant_route_policies_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: tenant_route_policies tenant_route_policies_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_route_policies
    ADD CONSTRAINT tenant_route_policies_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.user_profiles(id) ON DELETE SET NULL;


--
-- Name: tenant_runtime_policy_events tenant_runtime_policy_events_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_runtime_policy_events
    ADD CONSTRAINT tenant_runtime_policy_events_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.user_profiles(id) ON DELETE SET NULL;


--
-- Name: tenant_runtime_policy_events tenant_runtime_policy_events_policy_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_runtime_policy_events
    ADD CONSTRAINT tenant_runtime_policy_events_policy_version_id_fkey FOREIGN KEY (policy_version_id) REFERENCES public.tenant_runtime_policy_versions(id) ON DELETE CASCADE;


--
-- Name: tenant_runtime_policy_events tenant_runtime_policy_events_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_runtime_policy_events
    ADD CONSTRAINT tenant_runtime_policy_events_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: tenant_runtime_policy_versions tenant_runtime_policy_versions_activated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_runtime_policy_versions
    ADD CONSTRAINT tenant_runtime_policy_versions_activated_by_fkey FOREIGN KEY (activated_by) REFERENCES public.user_profiles(id) ON DELETE SET NULL;


--
-- Name: tenant_runtime_policy_versions tenant_runtime_policy_versions_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_runtime_policy_versions
    ADD CONSTRAINT tenant_runtime_policy_versions_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.user_profiles(id) ON DELETE SET NULL;


--
-- Name: tenant_runtime_policy_versions tenant_runtime_policy_versions_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_runtime_policy_versions
    ADD CONSTRAINT tenant_runtime_policy_versions_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: tenant_secrets tenant_secrets_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_secrets
    ADD CONSTRAINT tenant_secrets_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.user_profiles(id);


--
-- Name: tenant_secrets tenant_secrets_rotated_from_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_secrets
    ADD CONSTRAINT tenant_secrets_rotated_from_fkey FOREIGN KEY (rotated_from) REFERENCES public.tenant_secrets(secret_id);


--
-- Name: tenant_secrets tenant_secrets_rotated_to_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_secrets
    ADD CONSTRAINT tenant_secrets_rotated_to_fkey FOREIGN KEY (rotated_to) REFERENCES public.tenant_secrets(secret_id);


--
-- Name: tenant_secrets tenant_secrets_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_secrets
    ADD CONSTRAINT tenant_secrets_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);


--
-- Name: tenant_settings tenant_settings_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_settings
    ADD CONSTRAINT tenant_settings_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: tenant_sip_trunks tenant_sip_trunks_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_sip_trunks
    ADD CONSTRAINT tenant_sip_trunks_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.user_profiles(id) ON DELETE SET NULL;


--
-- Name: tenant_sip_trunks tenant_sip_trunks_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_sip_trunks
    ADD CONSTRAINT tenant_sip_trunks_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: tenant_sip_trunks tenant_sip_trunks_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_sip_trunks
    ADD CONSTRAINT tenant_sip_trunks_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.user_profiles(id) ON DELETE SET NULL;


--
-- Name: tenant_sip_trust_policies tenant_sip_trust_policies_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_sip_trust_policies
    ADD CONSTRAINT tenant_sip_trust_policies_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.user_profiles(id) ON DELETE SET NULL;


--
-- Name: tenant_sip_trust_policies tenant_sip_trust_policies_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_sip_trust_policies
    ADD CONSTRAINT tenant_sip_trust_policies_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: tenant_sip_trust_policies tenant_sip_trust_policies_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_sip_trust_policies
    ADD CONSTRAINT tenant_sip_trust_policies_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.user_profiles(id) ON DELETE SET NULL;


--
-- Name: tenant_telephony_concurrency_events tenant_telephony_concurrency_events_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_telephony_concurrency_events
    ADD CONSTRAINT tenant_telephony_concurrency_events_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.user_profiles(id) ON DELETE SET NULL;


--
-- Name: tenant_telephony_concurrency_events tenant_telephony_concurrency_events_lease_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_telephony_concurrency_events
    ADD CONSTRAINT tenant_telephony_concurrency_events_lease_id_fkey FOREIGN KEY (lease_id) REFERENCES public.tenant_telephony_concurrency_leases(id) ON DELETE SET NULL;


--
-- Name: tenant_telephony_concurrency_events tenant_telephony_concurrency_events_policy_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_telephony_concurrency_events
    ADD CONSTRAINT tenant_telephony_concurrency_events_policy_id_fkey FOREIGN KEY (policy_id) REFERENCES public.tenant_telephony_concurrency_policies(id) ON DELETE SET NULL;


--
-- Name: tenant_telephony_concurrency_events tenant_telephony_concurrency_events_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_telephony_concurrency_events
    ADD CONSTRAINT tenant_telephony_concurrency_events_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: tenant_telephony_concurrency_leases tenant_telephony_concurrency_leases_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_telephony_concurrency_leases
    ADD CONSTRAINT tenant_telephony_concurrency_leases_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.user_profiles(id) ON DELETE SET NULL;


--
-- Name: tenant_telephony_concurrency_leases tenant_telephony_concurrency_leases_policy_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_telephony_concurrency_leases
    ADD CONSTRAINT tenant_telephony_concurrency_leases_policy_id_fkey FOREIGN KEY (policy_id) REFERENCES public.tenant_telephony_concurrency_policies(id) ON DELETE SET NULL;


--
-- Name: tenant_telephony_concurrency_leases tenant_telephony_concurrency_leases_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_telephony_concurrency_leases
    ADD CONSTRAINT tenant_telephony_concurrency_leases_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: tenant_telephony_concurrency_leases tenant_telephony_concurrency_leases_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_telephony_concurrency_leases
    ADD CONSTRAINT tenant_telephony_concurrency_leases_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.user_profiles(id) ON DELETE SET NULL;


--
-- Name: tenant_telephony_concurrency_policies tenant_telephony_concurrency_policies_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_telephony_concurrency_policies
    ADD CONSTRAINT tenant_telephony_concurrency_policies_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.user_profiles(id) ON DELETE SET NULL;


--
-- Name: tenant_telephony_concurrency_policies tenant_telephony_concurrency_policies_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_telephony_concurrency_policies
    ADD CONSTRAINT tenant_telephony_concurrency_policies_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: tenant_telephony_concurrency_policies tenant_telephony_concurrency_policies_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_telephony_concurrency_policies
    ADD CONSTRAINT tenant_telephony_concurrency_policies_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.user_profiles(id) ON DELETE SET NULL;


--
-- Name: tenant_telephony_credentials tenant_telephony_credentials_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_telephony_credentials
    ADD CONSTRAINT tenant_telephony_credentials_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: tenant_telephony_idempotency tenant_telephony_idempotency_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_telephony_idempotency
    ADD CONSTRAINT tenant_telephony_idempotency_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: tenant_telephony_quota_events tenant_telephony_quota_events_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_telephony_quota_events
    ADD CONSTRAINT tenant_telephony_quota_events_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.user_profiles(id) ON DELETE SET NULL;


--
-- Name: tenant_telephony_quota_events tenant_telephony_quota_events_policy_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_telephony_quota_events
    ADD CONSTRAINT tenant_telephony_quota_events_policy_id_fkey FOREIGN KEY (policy_id) REFERENCES public.tenant_telephony_threshold_policies(id) ON DELETE SET NULL;


--
-- Name: tenant_telephony_quota_events tenant_telephony_quota_events_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_telephony_quota_events
    ADD CONSTRAINT tenant_telephony_quota_events_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: tenant_telephony_threshold_policies tenant_telephony_threshold_policies_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_telephony_threshold_policies
    ADD CONSTRAINT tenant_telephony_threshold_policies_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.user_profiles(id) ON DELETE SET NULL;


--
-- Name: tenant_telephony_threshold_policies tenant_telephony_threshold_policies_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_telephony_threshold_policies
    ADD CONSTRAINT tenant_telephony_threshold_policies_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: tenant_telephony_threshold_policies tenant_telephony_threshold_policies_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_telephony_threshold_policies
    ADD CONSTRAINT tenant_telephony_threshold_policies_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.user_profiles(id) ON DELETE SET NULL;


--
-- Name: tenants tenants_partner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenants
    ADD CONSTRAINT tenants_partner_id_fkey FOREIGN KEY (partner_id) REFERENCES public.tenants(id);


--
-- Name: tenants tenants_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenants
    ADD CONSTRAINT tenants_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES public.plans(id);


--
-- Name: tenants tenants_suspended_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenants
    ADD CONSTRAINT tenants_suspended_by_fkey FOREIGN KEY (suspended_by) REFERENCES public.user_profiles(id);


--
-- Name: tenants tenants_white_label_partner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenants
    ADD CONSTRAINT tenants_white_label_partner_id_fkey FOREIGN KEY (white_label_partner_id) REFERENCES public.white_label_partners(id);


--
-- Name: transcripts transcripts_call_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcripts
    ADD CONSTRAINT transcripts_call_id_fkey FOREIGN KEY (call_id) REFERENCES public.calls(id) ON DELETE CASCADE;


--
-- Name: transcripts transcripts_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcripts
    ADD CONSTRAINT transcripts_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: usage_records usage_records_subscription_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usage_records
    ADD CONSTRAINT usage_records_subscription_id_fkey FOREIGN KEY (subscription_id) REFERENCES public.subscriptions(id) ON DELETE SET NULL;


--
-- Name: usage_records usage_records_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usage_records
    ADD CONSTRAINT usage_records_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: user_passkeys user_passkeys_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_passkeys
    ADD CONSTRAINT user_passkeys_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.user_profiles(id) ON DELETE CASCADE;


--
-- Name: user_profiles user_profiles_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_profiles
    ADD CONSTRAINT user_profiles_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);


--
-- Name: webauthn_challenges webauthn_challenges_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.webauthn_challenges
    ADD CONSTRAINT webauthn_challenges_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.user_profiles(id) ON DELETE CASCADE;


--
-- Name: webhook_configs webhook_configs_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.webhook_configs
    ADD CONSTRAINT webhook_configs_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: white_label_partners white_label_partners_suspended_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.white_label_partners
    ADD CONSTRAINT white_label_partners_suspended_by_fkey FOREIGN KEY (suspended_by) REFERENCES public.user_profiles(id);


--
-- Name: white_label_partners white_label_partners_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.white_label_partners
    ADD CONSTRAINT white_label_partners_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: calls; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.calls ENABLE ROW LEVEL SECURITY;

--
-- Name: calls calls_tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY calls_tenant_isolation ON public.calls USING (((tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid) OR ((current_setting('app.bypass_rls'::text, true))::boolean = true)));


--
-- Name: campaigns; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.campaigns ENABLE ROW LEVEL SECURITY;

--
-- Name: campaigns campaigns_tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY campaigns_tenant_isolation ON public.campaigns USING (((tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid) OR ((current_setting('app.bypass_rls'::text, true))::boolean = true)));


--
-- Name: connectors; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.connectors ENABLE ROW LEVEL SECURITY;

--
-- Name: connectors connectors_tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY connectors_tenant_isolation ON public.connectors USING (((tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid) OR ((current_setting('app.bypass_rls'::text, true))::boolean = true)));


--
-- Name: conversations; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.conversations ENABLE ROW LEVEL SECURITY;

--
-- Name: conversations conversations_tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY conversations_tenant_isolation ON public.conversations USING (((tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid) OR ((current_setting('app.bypass_rls'::text, true))::boolean = true)));


--
-- Name: leads; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.leads ENABLE ROW LEVEL SECURITY;

--
-- Name: leads leads_tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY leads_tenant_isolation ON public.leads USING (((tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid) OR ((current_setting('app.bypass_rls'::text, true))::boolean = true)));


--
-- Name: tenant_codec_policies p_tenant_codec_policies_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_codec_policies_delete ON public.tenant_codec_policies FOR DELETE USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_codec_policies p_tenant_codec_policies_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_codec_policies_insert ON public.tenant_codec_policies FOR INSERT WITH CHECK (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_codec_policies p_tenant_codec_policies_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_codec_policies_select ON public.tenant_codec_policies FOR SELECT USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_codec_policies p_tenant_codec_policies_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_codec_policies_update ON public.tenant_codec_policies FOR UPDATE USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true))) WITH CHECK (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_route_policies p_tenant_route_policies_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_route_policies_delete ON public.tenant_route_policies FOR DELETE USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_route_policies p_tenant_route_policies_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_route_policies_insert ON public.tenant_route_policies FOR INSERT WITH CHECK (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_route_policies p_tenant_route_policies_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_route_policies_select ON public.tenant_route_policies FOR SELECT USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_route_policies p_tenant_route_policies_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_route_policies_update ON public.tenant_route_policies FOR UPDATE USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true))) WITH CHECK (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_runtime_policy_events p_tenant_runtime_policy_events_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_runtime_policy_events_delete ON public.tenant_runtime_policy_events FOR DELETE USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_runtime_policy_events p_tenant_runtime_policy_events_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_runtime_policy_events_insert ON public.tenant_runtime_policy_events FOR INSERT WITH CHECK (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_runtime_policy_events p_tenant_runtime_policy_events_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_runtime_policy_events_select ON public.tenant_runtime_policy_events FOR SELECT USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_runtime_policy_events p_tenant_runtime_policy_events_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_runtime_policy_events_update ON public.tenant_runtime_policy_events FOR UPDATE USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true))) WITH CHECK (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_runtime_policy_versions p_tenant_runtime_policy_versions_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_runtime_policy_versions_delete ON public.tenant_runtime_policy_versions FOR DELETE USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_runtime_policy_versions p_tenant_runtime_policy_versions_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_runtime_policy_versions_insert ON public.tenant_runtime_policy_versions FOR INSERT WITH CHECK (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_runtime_policy_versions p_tenant_runtime_policy_versions_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_runtime_policy_versions_select ON public.tenant_runtime_policy_versions FOR SELECT USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_runtime_policy_versions p_tenant_runtime_policy_versions_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_runtime_policy_versions_update ON public.tenant_runtime_policy_versions FOR UPDATE USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true))) WITH CHECK (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_sip_trunks p_tenant_sip_trunks_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_sip_trunks_delete ON public.tenant_sip_trunks FOR DELETE USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_sip_trunks p_tenant_sip_trunks_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_sip_trunks_insert ON public.tenant_sip_trunks FOR INSERT WITH CHECK (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_sip_trunks p_tenant_sip_trunks_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_sip_trunks_select ON public.tenant_sip_trunks FOR SELECT USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_sip_trunks p_tenant_sip_trunks_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_sip_trunks_update ON public.tenant_sip_trunks FOR UPDATE USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true))) WITH CHECK (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_sip_trust_policies p_tenant_sip_trust_policies_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_sip_trust_policies_delete ON public.tenant_sip_trust_policies FOR DELETE USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_sip_trust_policies p_tenant_sip_trust_policies_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_sip_trust_policies_insert ON public.tenant_sip_trust_policies FOR INSERT WITH CHECK (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_sip_trust_policies p_tenant_sip_trust_policies_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_sip_trust_policies_select ON public.tenant_sip_trust_policies FOR SELECT USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_sip_trust_policies p_tenant_sip_trust_policies_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_sip_trust_policies_update ON public.tenant_sip_trust_policies FOR UPDATE USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true))) WITH CHECK (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_telephony_concurrency_events p_tenant_telephony_concurrency_events_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_telephony_concurrency_events_delete ON public.tenant_telephony_concurrency_events FOR DELETE USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_telephony_concurrency_events p_tenant_telephony_concurrency_events_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_telephony_concurrency_events_insert ON public.tenant_telephony_concurrency_events FOR INSERT WITH CHECK (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_telephony_concurrency_events p_tenant_telephony_concurrency_events_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_telephony_concurrency_events_select ON public.tenant_telephony_concurrency_events FOR SELECT USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_telephony_concurrency_events p_tenant_telephony_concurrency_events_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_telephony_concurrency_events_update ON public.tenant_telephony_concurrency_events FOR UPDATE USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true))) WITH CHECK (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_telephony_concurrency_leases p_tenant_telephony_concurrency_leases_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_telephony_concurrency_leases_delete ON public.tenant_telephony_concurrency_leases FOR DELETE USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_telephony_concurrency_leases p_tenant_telephony_concurrency_leases_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_telephony_concurrency_leases_insert ON public.tenant_telephony_concurrency_leases FOR INSERT WITH CHECK (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_telephony_concurrency_leases p_tenant_telephony_concurrency_leases_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_telephony_concurrency_leases_select ON public.tenant_telephony_concurrency_leases FOR SELECT USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_telephony_concurrency_leases p_tenant_telephony_concurrency_leases_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_telephony_concurrency_leases_update ON public.tenant_telephony_concurrency_leases FOR UPDATE USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true))) WITH CHECK (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_telephony_concurrency_policies p_tenant_telephony_concurrency_policies_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_telephony_concurrency_policies_delete ON public.tenant_telephony_concurrency_policies FOR DELETE USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_telephony_concurrency_policies p_tenant_telephony_concurrency_policies_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_telephony_concurrency_policies_insert ON public.tenant_telephony_concurrency_policies FOR INSERT WITH CHECK (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_telephony_concurrency_policies p_tenant_telephony_concurrency_policies_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_telephony_concurrency_policies_select ON public.tenant_telephony_concurrency_policies FOR SELECT USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_telephony_concurrency_policies p_tenant_telephony_concurrency_policies_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_telephony_concurrency_policies_update ON public.tenant_telephony_concurrency_policies FOR UPDATE USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true))) WITH CHECK (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_telephony_idempotency p_tenant_telephony_idempotency_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_telephony_idempotency_delete ON public.tenant_telephony_idempotency FOR DELETE USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_telephony_idempotency p_tenant_telephony_idempotency_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_telephony_idempotency_insert ON public.tenant_telephony_idempotency FOR INSERT WITH CHECK (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_telephony_idempotency p_tenant_telephony_idempotency_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_telephony_idempotency_select ON public.tenant_telephony_idempotency FOR SELECT USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_telephony_idempotency p_tenant_telephony_idempotency_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_telephony_idempotency_update ON public.tenant_telephony_idempotency FOR UPDATE USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true))) WITH CHECK (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_telephony_quota_events p_tenant_telephony_quota_events_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_telephony_quota_events_delete ON public.tenant_telephony_quota_events FOR DELETE USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_telephony_quota_events p_tenant_telephony_quota_events_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_telephony_quota_events_insert ON public.tenant_telephony_quota_events FOR INSERT WITH CHECK (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_telephony_quota_events p_tenant_telephony_quota_events_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_telephony_quota_events_select ON public.tenant_telephony_quota_events FOR SELECT USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_telephony_quota_events p_tenant_telephony_quota_events_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_telephony_quota_events_update ON public.tenant_telephony_quota_events FOR UPDATE USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true))) WITH CHECK (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_telephony_threshold_policies p_tenant_telephony_threshold_policies_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_telephony_threshold_policies_delete ON public.tenant_telephony_threshold_policies FOR DELETE USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_telephony_threshold_policies p_tenant_telephony_threshold_policies_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_telephony_threshold_policies_insert ON public.tenant_telephony_threshold_policies FOR INSERT WITH CHECK (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_telephony_threshold_policies p_tenant_telephony_threshold_policies_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_telephony_threshold_policies_select ON public.tenant_telephony_threshold_policies FOR SELECT USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_telephony_threshold_policies p_tenant_telephony_threshold_policies_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY p_tenant_telephony_threshold_policies_update ON public.tenant_telephony_threshold_policies FOR UPDATE USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true))) WITH CHECK (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: recordings_s3; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.recordings_s3 ENABLE ROW LEVEL SECURITY;

--
-- Name: recordings_s3 recordings_s3_tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY recordings_s3_tenant_isolation ON public.recordings_s3 USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: stream_events; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.stream_events ENABLE ROW LEVEL SECURITY;

--
-- Name: stream_events stream_events_tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY stream_events_tenant_isolation ON public.stream_events USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: tenant_codec_policies; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.tenant_codec_policies ENABLE ROW LEVEL SECURITY;

--
-- Name: tenant_phone_numbers; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.tenant_phone_numbers ENABLE ROW LEVEL SECURITY;

--
-- Name: tenant_phone_numbers tenant_phone_numbers_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_phone_numbers_isolation ON public.tenant_phone_numbers USING ((tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid)) WITH CHECK ((tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid));


--
-- Name: tenant_route_policies; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.tenant_route_policies ENABLE ROW LEVEL SECURITY;

--
-- Name: tenant_runtime_policy_events; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.tenant_runtime_policy_events ENABLE ROW LEVEL SECURITY;

--
-- Name: tenant_runtime_policy_versions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.tenant_runtime_policy_versions ENABLE ROW LEVEL SECURITY;

--
-- Name: tenant_sip_trunks; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.tenant_sip_trunks ENABLE ROW LEVEL SECURITY;

--
-- Name: tenant_sip_trust_policies; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.tenant_sip_trust_policies ENABLE ROW LEVEL SECURITY;

--
-- Name: tenant_telephony_concurrency_events; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.tenant_telephony_concurrency_events ENABLE ROW LEVEL SECURITY;

--
-- Name: tenant_telephony_concurrency_leases; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.tenant_telephony_concurrency_leases ENABLE ROW LEVEL SECURITY;

--
-- Name: tenant_telephony_concurrency_policies; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.tenant_telephony_concurrency_policies ENABLE ROW LEVEL SECURITY;

--
-- Name: tenant_telephony_credentials; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.tenant_telephony_credentials ENABLE ROW LEVEL SECURITY;

--
-- Name: tenant_telephony_idempotency; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.tenant_telephony_idempotency ENABLE ROW LEVEL SECURITY;

--
-- Name: tenant_telephony_quota_events; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.tenant_telephony_quota_events ENABLE ROW LEVEL SECURITY;

--
-- Name: tenant_telephony_threshold_policies; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.tenant_telephony_threshold_policies ENABLE ROW LEVEL SECURITY;

--
-- Name: tenant_telephony_credentials ttc_tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY ttc_tenant_isolation ON public.tenant_telephony_credentials USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- PostgreSQL database dump complete
--

\unrestrict WthdvmB2Ew7ahIziU3gjqwSr9zUkYrnyrSAnGDhy8bZSgKAActWC7ZdVHtJBfkC

