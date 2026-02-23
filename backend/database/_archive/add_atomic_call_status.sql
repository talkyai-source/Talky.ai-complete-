-- ============================================================================
-- Atomic Call Status Update
-- ============================================================================
-- Replaces multiple sequential writes in the application layer with a single
-- atomic database function. Prevents partial updates when one step fails.
--
-- Called from CallService.handle_call_status() via supabase.rpc()
-- ============================================================================

CREATE OR REPLACE FUNCTION update_call_status(
    p_call_uuid UUID,
    p_outcome TEXT,
    p_duration INT DEFAULT NULL
)
RETURNS JSON
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_call RECORD;
    v_lead_status TEXT;
    v_last_call_result TEXT;
    v_current_attempts INT;
BEGIN
    -- 1. Update call record and capture related IDs
    UPDATE calls SET
        status = 'completed',
        outcome = p_outcome,
        duration_seconds = COALESCE(p_duration, duration_seconds),
        ended_at = NOW(),
        updated_at = NOW()
    WHERE id = p_call_uuid
    RETURNING id, lead_id, dialer_job_id, campaign_id
    INTO v_call;
    
    -- If call not found, return early
    IF v_call.id IS NULL THEN
        RETURN json_build_object(
            'found', false,
            'call_id', p_call_uuid
        );
    END IF;
    
    -- 2. Determine lead status based on outcome
    v_last_call_result := p_outcome;
    
    CASE p_outcome
        WHEN 'answered' THEN
            v_lead_status := 'contacted';
        WHEN 'goal_achieved' THEN
            v_lead_status := 'completed';
            v_last_call_result := 'goal_achieved';
        WHEN 'spam', 'invalid', 'unavailable', 'disconnected', 'rejected' THEN
            v_lead_status := 'dnc';
        ELSE
            v_lead_status := 'called';
    END CASE;
    
    -- 3. Update lead atomically (increment call_attempts in-place)
    IF v_call.lead_id IS NOT NULL THEN
        UPDATE leads SET
            status = v_lead_status,
            last_call_result = v_last_call_result,
            last_called_at = NOW(),
            call_attempts = COALESCE(call_attempts, 0) + 1,
            updated_at = NOW()
        WHERE id = v_call.lead_id;
    END IF;
    
    -- 4. Return call metadata for application-layer job/campaign handling
    RETURN json_build_object(
        'found', true,
        'call_id', v_call.id,
        'lead_id', v_call.lead_id,
        'job_id', v_call.dialer_job_id,
        'campaign_id', v_call.campaign_id,
        'outcome', p_outcome,
        'lead_status', v_lead_status
    );
END;
$$;

-- Grant access to the authenticated and service_role users
GRANT EXECUTE ON FUNCTION update_call_status(UUID, TEXT, INT) TO authenticated;
GRANT EXECUTE ON FUNCTION update_call_status(UUID, TEXT, INT) TO service_role;
