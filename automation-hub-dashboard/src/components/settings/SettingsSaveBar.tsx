import type { SaveState } from "./SettingsSection";

export default function SettingsSaveBar({ state, onSave, onDiscard, error }: {
  state: SaveState; onSave: () => void; onDiscard: () => void; error?: string | null;
}) {
  return <div className="settings-savebar">
    <span className={state === "error" ? "neg" : "dim"}>{error || (state === "dirty" ? "You have unsaved changes." : state === "saving" ? "Saving changes…" : "Changes are persisted by the server.")}</span>
    <div className="row-actions">
      <button className="btn btn-soft btn-sm" type="button" disabled={state !== "dirty" && state !== "error"} onClick={onDiscard}>Discard Changes</button>
      <button className="btn btn-primary btn-sm" type="button" disabled={state !== "dirty" && state !== "error"} onClick={onSave}>Save Changes</button>
    </div>
  </div>;
}
