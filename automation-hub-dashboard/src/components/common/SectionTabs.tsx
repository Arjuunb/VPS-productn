import type { KeyboardEvent } from "react";

export type SectionTab = { id: string; label: string };

export default function SectionTabs({ tabs, active }: { tabs: SectionTab[]; active?: string }) {
  const selected = tabs.some((tab) => tab.id === active) ? active : tabs[0]?.id;

  const select = (id: string) => {
    const raw = window.location.hash.replace(/^#\/?/, "");
    const [path, query = ""] = raw.split("?", 2);
    const params = new URLSearchParams(query);
    params.set("tab", id);
    window.location.hash = `/${path}?${params.toString()}`;
  };

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabs.length - 1;
    else return;
    event.preventDefault();
    select(tabs[next].id);
    requestAnimationFrame(() => document.getElementById(`section-tab-${tabs[next].id}`)?.focus());
  };

  return (
    <div className="section-tabs" role="tablist" aria-label="Page sections">
      {tabs.map((tab, index) => (
        <button
          id={`section-tab-${tab.id}`}
          key={tab.id}
          className={`section-tab ${selected === tab.id ? "active" : ""}`}
          role="tab"
          aria-selected={selected === tab.id}
          tabIndex={selected === tab.id ? 0 : -1}
          type="button"
          onClick={() => select(tab.id)}
          onKeyDown={(event) => onKeyDown(event, index)}
        >{tab.label}</button>
      ))}
    </div>
  );
}
