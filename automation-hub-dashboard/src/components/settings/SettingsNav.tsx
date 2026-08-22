export const SETTINGS_SECTIONS = [
  ["general", "General"], ["trading", "Trading Defaults"], ["market-data", "Market Data"],
  ["paper", "Paper Trading"], ["live", "Live Trading"], ["risk", "Risk & Safety"],
  ["notifications", "Notifications"], ["system", "System Health"],
  ["security", "Security"], ["advanced", "Advanced"],
] as const;

export default function SettingsNav({ active, onSelect }: { active: string; onSelect: (id: string) => void }) {
  return <nav className="settings-nav" aria-label="Settings sections">
    {SETTINGS_SECTIONS.map(([id, label]) => <button key={id} type="button" className={active === id ? "active" : ""} onClick={() => onSelect(id)}>{label}</button>)}
  </nav>;
}
