import type { ReactNode } from "react";
import SettingsSection from "./SettingsSection";

export default function AdvancedSettings({ children }: { children: ReactNode }) {
  return <SettingsSection title="Advanced" description="Backward-compatible controls for the stopped legacy autonomous engine."><div className="banner amber"><b>These settings control the legacy autonomous engine and do not configure Trading Instances.</b></div>{children}</SettingsSection>;
}
