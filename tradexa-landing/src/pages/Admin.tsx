import { useEffect, useState } from "react";
import { Activity, ShieldAlert, Users } from "lucide-react";
import { Logo } from "@/components/Logo";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { auth } from "@/lib/auth";
import { APP_URL } from "@/lib/utils";

interface AdminStatus {
  users: Array<{ id: string; full_name: string; role: string; created_at: string; last_login: string | null }>;
  engine: { running?: boolean; mode?: string; strategy?: string };
}

export default function Admin() {
  const [status, setStatus] = useState<AdminStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = async () => {
    setError(null);
    const response = await fetch("/admin/api/status", { credentials: "include" });
    if (!response.ok) {
      setStatus(null);
      setError(response.status === 403 ? "Administrator access is required." : "Unable to load system status.");
      return;
    }
    setStatus(await response.json() as AdminStatus);
  };
  useEffect(() => { void load(); }, []);

  return <main className="min-h-screen bg-ink px-4 py-10 text-white sm:px-8">
    <div className="mx-auto max-w-5xl">
      <header className="mb-10 flex items-center justify-between gap-4">
        <Logo />
        <div className="flex gap-3">
          <a className="text-sm text-white/60 hover:text-white" href={APP_URL}>Dashboard</a>
          <Button size="sm" variant="outline" onClick={() => void auth.signOut().then(() => { window.location.assign("/auth/login"); })}>Sign out</Button>
        </div>
      </header>
      <h1 className="text-3xl font-bold">Administration</h1>
      <p className="mt-2 text-white/55">Users, deployment health, and the single-instance trading engine.</p>
      {error ? <Card className="mt-8 border-loss/30 p-6"><div className="flex gap-3 text-loss-soft"><ShieldAlert /><div><p className="font-semibold">Access unavailable</p><p className="mt-1 text-sm">{error}</p></div></div></Card> : null}
      {status ? <>
        <div className="mt-8 grid gap-4 sm:grid-cols-3">
          <Card className="p-5"><Users className="h-5 w-5 text-gold" /><p className="mt-3 text-2xl font-semibold">{status.users.length}</p><p className="text-sm text-white/50">Registered users</p></Card>
          <Card className="p-5"><Activity className="h-5 w-5 text-emerald" /><p className="mt-3 text-2xl font-semibold">{status.engine.running ? "Running" : "Stopped"}</p><p className="text-sm text-white/50">Paper engine</p></Card>
          <Card className="p-5"><ShieldAlert className="h-5 w-5 text-gold" /><p className="mt-3 text-2xl font-semibold">RLS</p><p className="text-sm text-white/50">Supabase policy enforced</p></Card>
        </div>
        <Card className="mt-6 overflow-hidden p-0"><div className="flex items-center justify-between border-b border-line p-5"><h2 className="font-semibold">Users</h2><Button size="sm" variant="outline" onClick={() => void load()}>Refresh</Button></div><div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="text-white/45"><tr><th className="p-4">Name</th><th className="p-4">Role</th><th className="p-4">Last sign-in</th></tr></thead><tbody>{status.users.map((user) => <tr key={user.id} className="border-t border-line/60"><td className="p-4">{user.full_name || user.id}</td><td className="p-4 capitalize">{user.role}</td><td className="p-4 text-white/55">{user.last_login ?? "Never"}</td></tr>)}</tbody></table></div></Card>
      </> : !error ? <p className="mt-8 text-white/50">Loading secure system status…</p> : null}
    </div>
  </main>;
}
