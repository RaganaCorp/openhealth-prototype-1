"use client";

import { useState } from "react";

import type { PatientMedication } from "@/lib/api";

type MedicationsStepProps = {
  patientName: string;
  value: PatientMedication[];
  onChange: (next: PatientMedication[]) => void;
  // Hides the intake-style header when embedded in the profile-edit modal.
  embedded?: boolean;
};

export function MedicationsStep({ patientName, value, onChange, embedded }: MedicationsStepProps) {
  const [name, setName] = useState("");
  const [dose, setDose] = useState("");
  const [frequency, setFrequency] = useState("");

  const displayName = patientName.trim() || "this patient";

  function add() {
    const trimmedName = name.trim();
    if (!trimmedName) {
      return;
    }
    onChange([...value, { name: trimmedName, dose: dose.trim() || null, frequency: frequency.trim() || null }]);
    setName("");
    setDose("");
    setFrequency("");
  }

  function remove(index: number) {
    onChange(value.filter((_, i) => i !== index));
  }

  return (
    <div className="space-y-5">
      {embedded ? null : (
        <div className="border-b border-border/80 pb-4">
          <p className="eyebrow">Current medications</p>
          <h2 className="text-2xl font-semibold text-text-primary">{displayName}&rsquo;s current medications</h2>
          <p className="mt-2 text-sm leading-6 text-text-secondary">
            List medications {displayName} is currently taking. Dose and frequency are optional. You can skip this entirely.
          </p>
        </div>
      )}

      {value.length > 0 ? (
        <div className="space-y-2">
          {value.map((med, index) => {
            const detail = [med.dose, med.frequency].filter(Boolean).join(", ");
            return (
              <div
                className="flex items-center justify-between gap-3 rounded-lg border border-border/70 px-3 py-2"
                key={`${med.name}:${index}`}
              >
                <span className="text-sm text-text-primary">
                  <span className="font-medium">{med.name}</span>
                  {detail ? <span className="text-text-secondary"> — {detail}</span> : null}
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
            );
          })}
        </div>
      ) : null}

      <div className="grid gap-2 sm:grid-cols-[1fr_8rem_10rem_auto]">
        <input
          aria-label="Medication name"
          className="field-input"
          onChange={(event) => setName(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              add();
            }
          }}
          placeholder="Medication name"
          value={name}
        />
        <input
          aria-label="Dose"
          className="field-input"
          onChange={(event) => setDose(event.target.value)}
          placeholder="Dose (500mg)"
          value={dose}
        />
        <input
          aria-label="Frequency"
          className="field-input"
          onChange={(event) => setFrequency(event.target.value)}
          placeholder="Frequency (twice daily)"
          value={frequency}
        />
        <button className="button-secondary" disabled={!name.trim()} onClick={add} type="button">
          Add
        </button>
      </div>
    </div>
  );
}
