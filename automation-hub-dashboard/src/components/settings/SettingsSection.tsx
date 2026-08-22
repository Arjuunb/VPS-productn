import type { ReactNode } from "react";

export type SaveState = "saved" | "dirty" | "saving" | "error" | "restart-required" | "read-only";

export default function SettingsSection({ title, description, state, children }: {
  title: string; description: string; state?: SaveState; children: ReactNode;
}) {
  const label = state === "dirty" ? "Unsaved" : state === "saving" ? "Saving" : state === "error" ? "Save failed" : state === "restart-required" ? "Restart required" : state === "read-only" ? "Read only" : "Saved";
  return <section className="card settings-section" aria-labelledby={`settings-${title.toLowerCase().replace(/\W+/g, "-")}`}>
    <div className="settings-section-head">
      <div><h2 id={`settings-${title.toLowerCase().replace(/\W+/g, "-")}`}>{title}</h2><p className="dim">{description}</p></div>
      {state && <span className={`settings-state settings-state-${state}`}>{label}</span>}
    </div>
    {children}
  </section>;
}
