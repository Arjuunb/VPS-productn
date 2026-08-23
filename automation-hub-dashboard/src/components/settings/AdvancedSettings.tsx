import type { ReactNode } from "react";
import SettingsSection from "./SettingsSection";
import FactoryResetSettings from "./FactoryResetSettings";

export default function AdvancedSettings({ children }: { children: ReactNode }) {
  return <SettingsSection title="Advanced" description="Backward-compatible controls and protected system operations."><div className="banner amber"><b>These settings control the legacy autonomous engine and do not configure Trading Instances.</b></div>{children}<FactoryResetSettings /></SettingsSection>;
}
