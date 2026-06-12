"use client";

import { useState } from "react";

import type { PatientCondition } from "@/lib/api";
import { CONDITION_CATEGORIES } from "@/lib/conditions";

type ConditionsStepProps = {
  patientName: string;
  selected: PatientCondition[];
  onChange: (next: PatientCondition[]) => void;
};

function slugify(value: string): string {
  return value
    .toLowerCase()
    .trim()
    .replace(/[^\w\s-]/g, "")
    .replace(/[\s_]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
}

function isSelected(selected: PatientCondition[], category: string, code: string): boolean {
  return selected.some((c) => c.category === category && c.code === code);
}

export function ConditionsStep({ patientName, selected, onChange }: ConditionsStepProps) {
  const [openCategory, setOpenCategory] = useState<string | null>(CONDITION_CATEGORIES[0]?.name ?? null);
  const [customDrafts, setCustomDrafts] = useState<Record<string, string>>({});

  const displayName = patientName.trim() || "this patient";

  function toggle(category: string, condition: { code: string; label: string }) {
    if (isSelected(selected, category, condition.code)) {
      onChange(selected.filter((c) => !(c.category === category && c.code === condition.code)));
    } else {
      onChange([...selected, { category, code: condition.code, label: condition.label, source: "preset" }]);
    }
  }

  function addCustom(category: string) {
    const raw = (customDrafts[category] ?? "").trim();
    if (!raw) {
      return;
    }
    const code = `custom-${slugify(raw) || "condition"}`;
    if (!isSelected(selected, category, code)) {
      onChange([...selected, { category, code, label: raw, source: "custom" }]);
    }
    setCustomDrafts((prev) => ({ ...prev, [category]: "" }));
  }

  function removeCondition(condition: PatientCondition) {
    onChange(selected.filter((c) => !(c.category === condition.category && c.code === condition.code)));
  }

  return (
    <div className="space-y-5">
      <div className="border-b border-border/80 pb-4">
        <p className="eyebrow">Health conditions</p>
        <h2 className="text-2xl font-semibold text-text-primary">{displayName}&rsquo;s health conditions</h2>
        <p className="mt-2 text-sm leading-6 text-text-secondary">
          Select any conditions {displayName} is currently managing. You can skip this entirely.
        </p>
      </div>

      {selected.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {selected.map((condition) => (
            <button
              className="chip-removable"
              key={`${condition.category}:${condition.code}`}
              onClick={() => removeCondition(condition)}
              title="Remove"
              type="button"
            >
              {condition.label}
              <span aria-hidden className="ml-1">×</span>
            </button>
          ))}
        </div>
      ) : null}

      <div className="space-y-2">
        {CONDITION_CATEGORIES.map((category) => {
          const open = openCategory === category.name;
          const count = selected.filter((c) => c.category === category.name).length;
          const panelId = `conditions-${slugify(category.name)}`;
          return (
            <div className="accordion-item" key={category.name}>
              <button
                aria-controls={panelId}
                aria-expanded={open}
                className="accordion-header"
                onClick={() => setOpenCategory(open ? null : category.name)}
                type="button"
              >
                <span className="font-medium text-text-primary">{category.name}</span>
                <span className="flex items-center gap-2">
                  {count > 0 ? <span className="status-chip">{count}</span> : null}
                  <span aria-hidden className="text-text-secondary">{open ? "−" : "+"}</span>
                </span>
              </button>
              {open ? (
                <div className="accordion-panel" id={panelId}>
                  <div className="grid gap-1.5">
                    {category.conditions.map((condition) => {
                      const checked = isSelected(selected, category.name, condition.code);
                      return (
                        <label className="condition-row" key={condition.code}>
                          <input
                            checked={checked}
                            onChange={() => toggle(category.name, condition)}
                            type="checkbox"
                          />
                          <span>{condition.label}</span>
                        </label>
                      );
                    })}
                  </div>
                  <div className="mt-3 flex gap-2">
                    <input
                      className="field-input flex-1"
                      onChange={(event) =>
                        setCustomDrafts((prev) => ({ ...prev, [category.name]: event.target.value }))
                      }
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.preventDefault();
                          addCustom(category.name);
                        }
                      }}
                      placeholder="Add another condition…"
                      value={customDrafts[category.name] ?? ""}
                    />
                    <button
                      className="button-secondary"
                      disabled={!(customDrafts[category.name] ?? "").trim()}
                      onClick={() => addCustom(category.name)}
                      type="button"
                    >
                      Add
                    </button>
                  </div>
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}
