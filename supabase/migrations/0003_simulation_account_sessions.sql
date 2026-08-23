-- Durable, instance-scoped paper-account sessions and protected restart RPC.
ALTER TABLE public.positions
 ADD COLUMN IF NOT EXISTS simulation_session_id TEXT NOT NULL DEFAULT '';
ALTER TABLE public.paper_trades
 ADD COLUMN IF NOT EXISTS simulation_session_id TEXT NOT NULL DEFAULT '';
ALTER TABLE public.trading_instances
 ADD COLUMN IF NOT EXISTS simulation_session_id TEXT NOT NULL DEFAULT '';
ALTER TABLE public.trading_instances
 ADD COLUMN IF NOT EXISTS simulation_session_number INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS public.simulation_sessions (
 id TEXT PRIMARY KEY, instance_id TEXT NOT NULL, session_number INTEGER NOT NULL,
 starting_balance DOUBLE PRECISION NOT NULL, ending_balance DOUBLE PRECISION,
 realized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0, trades_count INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL, started_at TIMESTAMPTZ NOT NULL, ended_at TIMESTAMPTZ,
 end_reason TEXT, UNIQUE(instance_id,session_number)
);
CREATE TABLE IF NOT EXISTS public.simulation_account_audit (
 id TEXT PRIMARY KEY, action TEXT NOT NULL, instance_id TEXT NOT NULL,
 previous_session_id TEXT, new_session_id TEXT NOT NULL,
 previous_balance DOUBLE PRECISION NOT NULL, new_balance DOUBLE PRECISION NOT NULL,
 open_positions_cleared INTEGER NOT NULL, pending_orders_cleared INTEGER NOT NULL,
 timestamp TIMESTAMPTZ NOT NULL, initiated_by TEXT NOT NULL, result TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_simulation_sessions_instance
 ON public.simulation_sessions(instance_id,session_number DESC);
CREATE INDEX IF NOT EXISTS idx_simulation_restart_audit
 ON public.simulation_account_audit(instance_id,timestamp DESC);

INSERT INTO public.simulation_sessions
 (id,instance_id,session_number,starting_balance,ending_balance,realized_pnl,
  trades_count,status,started_at)
SELECT 'session_' || md5(t.id || ':1'),t.id,1,
 COALESCE(t.starting_equity,t.capital_allocation),NULL,
 COALESCE(t.current_realized_equity,t.capital_allocation)-COALESCE(t.starting_equity,t.capital_allocation),
 (SELECT COUNT(*) FROM public.paper_trades p WHERE p.instance_id=t.id AND p.status='closed'),
 'active',t.created_at
FROM public.trading_instances t WHERE COALESCE(t.simulation_session_id,'')=''
ON CONFLICT (instance_id,session_number) DO NOTHING;
UPDATE public.trading_instances t
 SET simulation_session_id=s.id,simulation_session_number=s.session_number
 FROM public.simulation_sessions s
 WHERE s.instance_id=t.id AND s.status='active' AND COALESCE(t.simulation_session_id,'')='';
UPDATE public.paper_trades p SET simulation_session_id=t.simulation_session_id
 FROM public.trading_instances t
 WHERE p.instance_id=t.id AND COALESCE(p.simulation_session_id,'')='';
UPDATE public.positions p SET simulation_session_id=t.simulation_session_id
 FROM public.trading_instances t
 WHERE p.instance_id=t.id AND COALESCE(p.simulation_session_id,'')='';

CREATE OR REPLACE FUNCTION public.restart_simulation_account(
 p_instance_id TEXT,p_new_session_id TEXT,p_previous_balance DOUBLE PRECISION,
 p_initiated_by TEXT,p_timestamp TIMESTAMPTZ
) RETURNS JSONB LANGUAGE plpgsql AS $$
DECLARE
 v_instance public.trading_instances%ROWTYPE;
 v_previous_session_id TEXT; v_next_session_number INTEGER;
 v_open_positions INTEGER; v_pending_orders INTEGER; v_closed_trades INTEGER;
 v_audit_id TEXT;
BEGIN
 SELECT * INTO v_instance FROM public.trading_instances WHERE id=p_instance_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Trading instance not found'; END IF;
 IF LOWER(v_instance.execution_mode)<>'paper' OR v_instance.mode<>'trading' THEN
  RAISE EXCEPTION 'Simulation account restart is allowed only for Paper Trading instances';
 END IF;
 v_previous_session_id:=v_instance.simulation_session_id;
 v_next_session_number:=GREATEST(1,v_instance.simulation_session_number+1);
 SELECT COUNT(*) INTO v_open_positions FROM public.positions
  WHERE instance_id=p_instance_id AND status='open';
 SELECT COUNT(*) INTO v_pending_orders
  FROM public.instance_market_state AS market_state
  CROSS JOIN LATERAL jsonb_object_keys(
    COALESCE(market_state.pending_orders_json,'{}'::jsonb)
  ) AS pending_order(key)
  WHERE market_state.instance_id=p_instance_id;
 v_pending_orders:=COALESCE(v_pending_orders,0);
 SELECT COUNT(*) INTO v_closed_trades FROM public.paper_trades
  WHERE instance_id=p_instance_id AND simulation_session_id=v_previous_session_id AND status='closed';
 UPDATE public.positions SET status='reset',pnl=0,closed_at=p_timestamp
  WHERE instance_id=p_instance_id AND status='open';
 UPDATE public.paper_trades SET status='cancelled',pnl=0,realized_pnl=0,closed_at=p_timestamp
  WHERE instance_id=p_instance_id AND status='open';
 UPDATE public.simulation_sessions
  SET status='ended',ending_balance=p_previous_balance,
      realized_pnl=p_previous_balance-v_instance.starting_equity,trades_count=v_closed_trades,
      ended_at=p_timestamp,end_reason='account restart'
  WHERE id=v_previous_session_id AND instance_id=p_instance_id;
 INSERT INTO public.simulation_sessions
  (id,instance_id,session_number,starting_balance,realized_pnl,trades_count,status,started_at)
 VALUES (p_new_session_id,p_instance_id,v_next_session_number,v_instance.starting_equity,0,0,'active',p_timestamp);
 UPDATE public.trading_instances
  SET current_realized_equity=starting_equity,risk_basis=starting_equity,
      simulation_session_id=p_new_session_id,simulation_session_number=v_next_session_number,
      updated_at=p_timestamp WHERE id=p_instance_id;
 UPDATE public.instance_market_state SET pending_orders_json='{}'::jsonb,updated_at=p_timestamp
  WHERE instance_id=p_instance_id;
 DELETE FROM public.instance_metrics WHERE instance_id=p_instance_id;
 v_audit_id:=md5(p_instance_id||p_new_session_id||p_timestamp::text);
 INSERT INTO public.simulation_account_audit
  (id,action,instance_id,previous_session_id,new_session_id,previous_balance,new_balance,
   open_positions_cleared,pending_orders_cleared,timestamp,initiated_by,result)
 VALUES (v_audit_id,'simulation_account_restart',p_instance_id,v_previous_session_id,p_new_session_id,
  p_previous_balance,v_instance.starting_equity,v_open_positions,v_pending_orders,p_timestamp,
  p_initiated_by,'success');
 RETURN jsonb_build_object(
  'action','simulation_account_restart','instance_id',p_instance_id,
  'previous_session_id',v_previous_session_id,'new_session_id',p_new_session_id,
  'previous_balance',p_previous_balance,'new_balance',v_instance.starting_equity,
  'open_positions_cleared',v_open_positions,'pending_orders_cleared',v_pending_orders,
  'timestamp',p_timestamp,'initiated_by',p_initiated_by,'result','success',
  'session_number',v_next_session_number);
END;
$$;
REVOKE ALL ON FUNCTION public.restart_simulation_account(TEXT,TEXT,DOUBLE PRECISION,TEXT,TIMESTAMPTZ)
 FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.restart_simulation_account(TEXT,TEXT,DOUBLE PRECISION,TEXT,TIMESTAMPTZ)
 TO service_role;

NOTIFY pgrst,'reload schema';
