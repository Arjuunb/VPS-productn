import type { NexusPetAppearance, NexusPetId, NexusPetSize } from "./types";

type PetOption = {
  id: NexusPetId;
  name: string;
  description: string;
};

export const NEXUS_PETS: readonly PetOption[] = [
  { id: "sprig", name: "Sprig", description: "The signature Nexus companion—steady growth, quietly monitored." },
  { id: "pulse", name: "Pulse", description: "Tracks market rhythm and keeps the operating heartbeat visible." },
  { id: "orbit", name: "Orbit", description: "Keeps every instance, venue, and risk boundary in view." },
  { id: "glint", name: "Glint", description: "Surfaces the small detail that deserves operator attention." },
  { id: "echo", name: "Echo", description: "Listens for repeated signals without amplifying noise." },
  { id: "nova", name: "Nova", description: "Bright, composed energy for important trading milestones." },
  { id: "volt", name: "Volt", description: "Fast awareness for urgent—but controlled—market events." },
  { id: "kiro", name: "Kiro", description: "A precise, minimal companion for focused execution." },
] as const;

const PET_SIZES: readonly { id: NexusPetSize; label: string }[] = [
  { id: "small", label: "Small" },
  { id: "medium", label: "Medium" },
  { id: "large", label: "Large" },
] as const;

type Props = {
  appearance: NexusPetAppearance;
  onChange: (appearance: NexusPetAppearance) => void;
  onClose: () => void;
};

export default function NexusPetSettings({ appearance, onChange, onClose }: Props) {
  const choosePet = (pet: NexusPetId) => onChange({ ...appearance, pet });
  const chooseSize = (size: NexusPetSize) => onChange({ ...appearance, size });

  return (
    <section className="nexus-pet-settings" role="dialog" aria-label="Nexus pet settings">
      <header className="nexus-pet-settings-head">
        <div>
          <span>COMPANION MATRIX</span>
          <h3>Pets</h3>
        </div>
        <button type="button" className="nexus-pet-close" aria-label="Close pet settings" onClick={onClose} autoFocus>×</button>
      </header>

      <div className="nexus-pet-settings-scroll">
        <div className="nexus-pet-settings-intro">
          <b>Pick a pet</b>
          <p>Nexus pets monitor Trading Instances and surface what needs attention.</p>
        </div>

        <div className="nexus-pet-roster" role="radiogroup" aria-label="Pick a Nexus pet">
          {NEXUS_PETS.map((pet) => {
            const selected = appearance.pet === pet.id;
            return (
              <button
                key={pet.id}
                type="button"
                role="radio"
                aria-checked={selected}
                className="nexus-pet-choice"
                data-pet={pet.id}
                data-selected={selected}
                onClick={() => choosePet(pet.id)}
              >
                <span className="nexus-pet-choice-avatar" aria-hidden="true" />
                <span className="nexus-pet-choice-copy"><b>{pet.name}</b><small>{pet.description}</small></span>
                <span className="nexus-pet-choice-check" aria-hidden="true">{selected ? "✓" : ""}</span>
              </button>
            );
          })}
        </div>

        <div className="nexus-pet-custom">
          <b>Custom pets</b>
          <code>~/.codex/pets</code>
          <p>Custom companions are managed by Codex desktop. This VPS dashboard uses the audited Nexus roster above.</p>
        </div>

        <div className="nexus-pet-appearance">
          <b>Appearance</b>
          <div className="nexus-pet-size-row">
            <span>Pet size<small>Adjust your companion's dashboard footprint.</small></span>
            <div className="nexus-pet-size-options" role="radiogroup" aria-label="Pet size">
              {PET_SIZES.map((size) => (
                <button
                  key={size.id}
                  type="button"
                  role="radio"
                  aria-checked={appearance.size === size.id}
                  data-selected={appearance.size === size.id}
                  onClick={() => chooseSize(size.id)}
                >{size.label}</button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
