-- Destructive application Factory Reset.
-- Authentication (auth.users), secrets, schema and this audit table are never
-- part of the deletion allowlist.
CREATE TABLE IF NOT EXISTS public.factory_reset_audit (
    id text PRIMARY KEY,
    requested_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    initiated_by text NOT NULL,
    reset_version text NOT NULL,
    status text NOT NULL CHECK (status IN ('requested', 'succeeded', 'failed')),
    duration_ms bigint,
    preserved_scope jsonb NOT NULL DEFAULT '{}'::jsonb,
    error text
);

ALTER TABLE public.factory_reset_audit ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.factory_reset_audit FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON TABLE public.factory_reset_audit TO service_role;

CREATE OR REPLACE FUNCTION public.factory_reset_application_data(
    p_reset_id text,
    p_confirmation text
) RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_exists boolean;
BEGIN
    IF p_confirmation IS DISTINCT FROM 'FACTORY RESET' THEN
        RAISE EXCEPTION 'exact factory-reset confirmation is required';
    END IF;

    SELECT EXISTS (
        SELECT 1 FROM public.factory_reset_audit
        WHERE id = p_reset_id AND status = 'requested'
    ) INTO v_exists;
    IF NOT v_exists THEN
        RAISE EXCEPTION 'factory-reset audit request does not exist';
    END IF;

    -- Explicit operational-data allowlist. FK children are removed first.
    DELETE FROM public.instance_engine_logs;
    DELETE FROM public.instance_metrics;
    DELETE FROM public.instance_market_state;
    DELETE FROM public.simulation_account_audit;
    DELETE FROM public.positions;
    DELETE FROM public.paper_trades;
    DELETE FROM public.simulation_sessions;
    DELETE FROM public.webhook_events;
    DELETE FROM public.bot_logs;
    DELETE FROM public.alerts;
    DELETE FROM public.trade_memories;
    DELETE FROM public.memory_reviews;
    DELETE FROM public.trading_instances;
    DELETE FROM public.trading_instance_platform_settings;
    DELETE FROM public.user_settings;

    RETURN jsonb_build_object('ok', true, 'reset_id', p_reset_id);
END;
$$;

REVOKE ALL ON FUNCTION public.factory_reset_application_data(text, text)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.factory_reset_application_data(text, text)
    TO service_role;

NOTIFY pgrst, 'reload schema';
