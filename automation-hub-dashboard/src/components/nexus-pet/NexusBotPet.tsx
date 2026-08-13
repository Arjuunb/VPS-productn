import { useCallback, useEffect, useRef, useState } from "react";
import { useApp } from "../../app-context";
import NexusPetPopover from "./NexusPetPopover";
import { useNexusPetController } from "./NexusPetController";
import "./NexusPet.css";

type Interaction = "idle" | "hover" | "click";

export default function NexusBotPet() {
  const app = useApp();
  const model = useNexusPetController(app.selectedInstanceId);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const frameRef = useRef<number | null>(null);
  const interactionTimer = useRef<number | null>(null);
  const [open, setOpen] = useState(false);
  const [interaction, setInteraction] = useState<Interaction>("idle");

  const react = useCallback((next: Exclude<Interaction, "idle">, duration: number) => {
    if (interactionTimer.current !== null) window.clearTimeout(interactionTimer.current);
    setInteraction(next);
    interactionTimer.current = window.setTimeout(() => setInteraction("idle"), duration);
  }, []);

  useEffect(() => () => {
    if (interactionTimer.current !== null) window.clearTimeout(interactionTimer.current);
    if (frameRef.current !== null) window.cancelAnimationFrame(frameRef.current);
  }, []);

  useEffect(() => {
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const trackPointer = (event: PointerEvent) => {
      if (reduceMotion.matches || frameRef.current !== null) return;
      const { clientX, clientY } = event;
      frameRef.current = window.requestAnimationFrame(() => {
        frameRef.current = null;
        const root = rootRef.current;
        const button = buttonRef.current;
        if (!root || !button) return;
        const rect = button.getBoundingClientRect();
        const dx = clientX - (rect.left + rect.width / 2);
        const dy = clientY - (rect.top + rect.height / 2);
        const distance = Math.hypot(dx, dy);
        const strength = Math.max(0, 1 - distance / 190);
        const x = distance ? dx / distance * strength : 0;
        const y = distance ? dy / distance * strength : 0;
        root.style.setProperty("--pet-look-x", `${(x * 2.2).toFixed(2)}px`);
        root.style.setProperty("--pet-look-y", `${(y * 1.4).toFixed(2)}px`);
        root.style.setProperty("--pet-head-turn", `${(x * 3.2).toFixed(2)}deg`);
        root.style.setProperty("--pet-leaf-turn", `${(x * 5).toFixed(2)}deg`);
      });
    };
    window.addEventListener("pointermove", trackPointer, { passive: true });
    return () => window.removeEventListener("pointermove", trackPointer);
  }, []);

  useEffect(() => {
    const onVisibility = () => { if (rootRef.current) rootRef.current.dataset.hidden = String(document.hidden); };
    onVisibility();
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const toggle = () => {
    react("click", 560);
    setOpen((value) => !value);
  };

  return (
    <div ref={rootRef} className="nexus-pet-root" data-state={model.state} data-interaction={interaction}>
      {open && <NexusPetPopover model={model} onClose={() => setOpen(false)} />}
      <button
        ref={buttonRef}
        type="button"
        className="nexus-pet-button"
        aria-label={`Nexus Engine: ${model.statusLabel}. Open status.`}
        aria-haspopup="dialog"
        aria-expanded={open}
        title={`Nexus Engine · ${model.statusLabel}`}
        onClick={toggle}
        onPointerEnter={() => react("hover", 480)}
      >
        <span className="nexus-pet-stage" aria-hidden="true">
          <svg viewBox="0 0 64 74" role="presentation" focusable="false">
            <ellipse className="nexus-pet-floor" cx="32" cy="69" rx="20" ry="3.5" />
            <g className="nexus-pet-body">
              <path d="M22 48h20c4 0 7 3 7 7v8c0 3-2 5-5 5H20c-3 0-5-2-5-5v-8c0-4 3-7 7-7Z" />
              <path className="nexus-pet-body-gold" d="M27 52h10l2 4-7 7-7-7 2-4Z" />
              <path className="nexus-pet-arm nexus-pet-arm-left" d="M17 53c-4 1-6 4-6 8" />
              <path className="nexus-pet-arm nexus-pet-arm-right" d="M47 53c4 1 6 4 6 8" />
              <path className="nexus-pet-foot" d="M22 67v3M42 67v3" />
            </g>
            <g className="nexus-pet-head">
              <path className="nexus-pet-antenna" d="M32 15V9" />
              <g className="nexus-pet-leaf">
                <path d="M32 10c1-6 6-8 11-7-1 5-4 9-11 7Z" />
                <path d="M32 10c-1-5-5-7-9-6 0 4 3 7 9 6Z" />
              </g>
              <rect className="nexus-pet-head-shell" x="10" y="15" width="44" height="36" rx="14" />
              <rect className="nexus-pet-face" x="15" y="21" width="34" height="23" rx="9" />
              <path className="nexus-pet-brow" d="M20 27h9M35 27h9" />
              <g className="nexus-pet-eyes">
                <rect x="21" y="30" width="7" height="4" rx="2" />
                <rect x="36" y="30" width="7" height="4" rx="2" />
              </g>
              <path className="nexus-pet-mouth" d="M28 39h8" />
              <path className="nexus-pet-trim" d="M18 46c8 3 20 3 28 0" />
              <path className="nexus-pet-warning" d="M50 18l5-8 5 8h-10Zm5-5v2.5m0 1.5v.2" />
            </g>
            <g className="nexus-pet-particles">
              <circle cx="8" cy="45" r="1.2" /><circle cx="56" cy="43" r="1" /><circle cx="52" cy="52" r=".8" />
            </g>
          </svg>
        </span>
      </button>
    </div>
  );
}
