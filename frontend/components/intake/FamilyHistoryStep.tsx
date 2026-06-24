"use client";

import { useState } from "react";

import type { FamilyHistoryEntry, FamilyRelationship } from "@/lib/api";
import { CONDITION_CATEGORIES } from "@/lib/conditions";

type FamilyHistoryStepProps = {
  patientName: string;
  value: FamilyHistoryEntry[];
  onChange: (next: FamilyHistoryEntry[]) => void;
  // Hides the intake-style header when embedded in the profile-edit modal.
  embedded?: boolean;
};

const RELATIONSHIP_OPTIONS: { value: FamilyRelationship; label: string }[] = [
  { value: "mother", label: "Mother" },
  { value: "father", label: "Father" },
  { value: "sister", label: "Sister" },
  { value: "brother", label: "Brother" },
  { value: "grandmother", label: "Grandmother" },
  { value: "grandfather", label: "Grandfather" },
  { value: "aunt", label: "Aunt" },
  { value: "uncle", label: "Uncle" },
  { value: "daughter", label: "Daughter" },
  { value: "son", label: "Son" },
  { value: "cousin", label: "Cousin" },
  { value: "other", label: "Other relative" },
];

const RELATIONSHIP_LABELS: Record<FamilyRelationship, string> = Object.fromEntries(
  RELATIONSHIP_OPTIONS.map((o) => [o.value, o.label]),
) as Record<FamilyRelationship, string>;

function slugify(value: string): string {
  return value
    .toLowerCase()
    .trim()
    .replace(/[^\w\s-]/g, "")
    .replace(/[\s_]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
}

export function FamilyHistoryStep({ patientName, value, onChange, embedded }: FamilyHistoryStepProps) {
  const [relationship, setRelationship] = useState<FamilyRelationship>("mother");
  const [conditionCode, setConditionCode] = useState("");
  const [customLabel, setCustomLabel] = useState("");

  const displayName = patientName.trim() || "this patient";

  function add() {
    const custom = customLabel.trim();
    let entry: FamilyHistoryEntry | null = null;
    if (custom) {
      entry = { relationship, code: `custom-${slugify(custom) || "condition"}`, label: custom, source: "custom" };
    } else if (conditionCode) {
      const preset = CONDITION_CATEGORIES.flatMap((c) => c.conditions).find((c) => c.code === conditionCode);
      if (preset) {
        entry = { relationship, code: preset.code, label: preset.label, source: "preset" };
      }
    }
    if (!entry) {
      return;
    }
    // Dedupe on relationship + condition so the same pair isn't added twice.
    const exists = value.some((e) => e.relationship === entry!.relationship && e.code === entry!.code);
    if (!exists) {
      onChange([...value, entry]);
    }
    setConditionCode("");
    setCustomLabel("");
  }

  function remove(index: number) {
    onChange(value.filter((_, i) => i !== index));
  }

  const canAdd = customLabel.trim() !== "" || conditionCode !== "";

  return (
    <div className="space-y-5">
      {embedded ? null : (
        <div className="border-b border-border/80 pb-4">
          <p className="eyebrow">Family history</p>
          <h2 className="text-2xl font-semibold text-text-primary">{displayName}&rsquo;s family history</h2>
          <p className="mt-2 text-sm leading-6 text-text-secondary">
            Add conditions that run in {displayName}&rsquo;s family. You can skip this entirely.
          </p>
        </div>
      )}

      {value.length > 0 ? (
        <div className="space-y-2">
          {value.map((entry, index) => (
            <div
              className="flex items-center justify-between gap-3 rounded-lg border border-border/70 px-3 py-2"
              key={`${entry.relationship}:${entry.code}`}
            >
              <span className="text-sm text-text-primary">
                <span className="font-medium">{RELATIONSHIP_LABELS[entry.relationship]}</span> — {entry.label}
              </span>
              <button
                aria-label="Remove"
                className="text-text-secondary transition-colors hover:text-danger"
                onClick={() => remove(index)}
                type="button"
              >
                ×
              </button>
            </div>
          ))}
        </div>
      ) : null}

      <div className="grid gap-2 sm:grid-cols-[10rem_1fr_auto]">
        <select
          aria-label="Relationship"
          className="field-input"
          onChange={(event) => setRelationship(event.target.value as FamilyRelationship)}
          value={relationship}
        >
          {RELATIONSHIP_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <select
          aria-label="Condition"
          className="field-input"
          disabled={customLabel.trim() !== ""}
          onChange={(event) => setConditionCode(event.target.value)}
          value={conditionCode}
        >
          <option value="">Select a condition…</option>
          {CONDITION_CATEGORIES.map((category) => (
            <optgroup key={category.name} label={category.name}>
              {category.conditions.map((condition) => (
                <option key={condition.code} value={condition.code}>
                  {condition.label}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        <button className="button-secondary" disabled={!canAdd} onClick={add} type="button">
          Add
        </button>
      </div>
      <input
        className="field-input"
        onChange={(event) => setCustomLabel(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            add();
          }
        }}
        placeholder="…or type a condition not in the list"
        value={customLabel}
      />
    </div>
  );
}
