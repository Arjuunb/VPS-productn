import { createClient, type SupabaseClient } from "@supabase/supabase-js";

interface RuntimeConfig {
  supabaseUrl?: string;
  supabaseAnonKey?: string;
}

const runtime = (typeof window !== "undefined" ? (window as Window & { __HUB_CONFIG__?: RuntimeConfig }).__HUB_CONFIG__ : undefined) ?? {};
const url = runtime.supabaseUrl ?? (import.meta.env.VITE_SUPABASE_URL as string | undefined);
const anonKey = runtime.supabaseAnonKey ?? (import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined);

/**
 * True when real Supabase credentials are present. When false, the auth layer
 * runs in a deliberately unavailable state: no demo accounts or local password
 * fallback exist in production. Supply only the public URL/anon key; the
 * service-role key must stay on the server.
 */
export const isSupabaseConfigured = Boolean(url && anonKey);

export const supabase: SupabaseClient | null = isSupabaseConfigured
  ? createClient(url as string, anonKey as string, {
      auth: {
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: true,
      },
    })
  : null;
