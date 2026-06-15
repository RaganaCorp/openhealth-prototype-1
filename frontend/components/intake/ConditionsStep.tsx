"use client";

import { useState } from "react";

import {
  AppleIcon,
  BoneIcon,
  BrainIcon,
  DropletIcon,
  EyeIcon,
  FlaskIcon,
  HeartIcon,
  LayersIcon,
  ShieldIcon,
  SmileIcon,
  SproutIcon,
  WindIcon,
} from "@/components/icons";
import type { PatientCondition } from "@/lib/api";
import { CONDITION_CATEGORIES } from "@/lib/conditions";

// One icon per condition category, keyed by the category name from CONDITION_CATEGORIES.
const CATEGORY_ICONS: Record<string, typeof HeartIcon> = {
  "Heart & Circulation": HeartIcon,
  "Hormones & Metabolism": FlaskIcon,
  "Brain & Nerves": BrainIcon,
  "Lungs & Breathing": WindIcon,
  "Digestive System": AppleIcon,
  "Kidneys & Urinary": DropletIcon,
  "Bones & Joints": BoneIcon,
  Skin: LayersIcon,
  "Mental Health": SmileIcon,
  "Blood & Immune": ShieldIcon,
  "Eyes & Vision": EyeIcon,
  "Reproductive Health": SproutIcon,
};

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
  const [openCategory, setOpenCategory] = useState<string | null>(null);
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

      <div className="space-y-2">
        {CONDITION_CATEGORIES.map((category) => {
          const open = openCategory === category.name;
          const count = selected.filter((c) => c.category === category.name).length;
          const panelId = `conditions-${slugify(category.name)}`;
          const Icon = CATEGORY_ICONS[category.name];
          const customForCategory = selected.filter(
            (c) => c.category === category.name && c.source === "custom",
          );
          return (
            <div className="accordion-item" key={category.name}>
              <button
                aria-controls={panelId}
                aria-expanded={open}
                className="accordion-header"
                onClick={() => setOpenCategory(open ? null : category.name)}
                type="button"
              >
                <span className="flex items-center gap-2.5 font-medium text-text-primary">
                  <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-primary-light text-primary">
                    {Icon ? <Icon size={16} /> : null}
                  </span>
                  {category.name}
                </span>
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
                    {/* Custom conditions added to this category stay listed here. */}
                    {customForCategory.map((custom) => (
                      <label className="condition-row" key={custom.code}>
                        <input checked onChange={() => removeCondition(custom)} type="checkbox" />
                        <span>{custom.label}</span>
                      </label>
                    ))}
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
