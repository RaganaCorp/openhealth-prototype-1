"use client";

import { useEffect, useState } from "react";

import { DeletePatientModal } from "@/components/DeletePatientModal";
import { patchPatient, type Patient } from "@/lib/api";

type PatientSettingsProps = {
  patient: Patient;
  onPatientSaved: (patient: Patient) => void;
  onDeleted: () => void;
};

export function PatientSettings({ patient, onPatientSaved, onDeleted }: PatientSettingsProps) {
  const [name, setName] = useState(patient.name);
  const [memoryResultsOverride, setMemoryResultsOverride] = useState(patient.memory_results_override?.toString() ?? "");
  const [contextWindowOverride, setContextWindowOverride] = useState(patient.context_window_tokens_override?.toString() ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);

  return (
    <>
      <section className="panel-card">
        <div className="border-b border-border/80 pb-5">
          <p className="eyebrow">Patient Settings</p>
          <h1 className="text-3xl font-semibold text-text-primary">{patient.name}</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-text-secondary">Per-patient overrides stay separate from the global config and only affect this patient workspace.</p>
        </div>

        <form
          className="mt-6 space-y-5"
          onSubmit={async (event) => {
            event.preventDefault();
            const updates: {
              name?: string;
              memory_results_override?: number | null;
              context_window_tokens_override?: number | null;
            } = {};
            if (name.trim() !== patient.name) {
              updates.name = name.trim();
            }
            updates.memory_results_override = memoryResultsOverride.trim() === "" ? null : Number(memoryResultsOverride);
            updates.context_window_tokens_override = contextWindowOverride.trim() === "" ? null : Number(contextWindowOverride);

            try {
              setSaving(true);
              setError(null);
              const updated = await patchPatient(patient.id, updates);
              onPatientSaved(updated);
            } catch (err) {
              setError(err instanceof Error ? err.message : "Could not save patient settings");
            } finally {
              setSaving(false);
            }
          }}
        >
          <label className="field-group">
            <span className="field-label">Patient name</span>
            <input className="field-input" onChange={(event) => setName(event.target.value)} value={name} />
          </label>

          <div className="grid gap-4 sm:grid-cols-2">
            <label className="field-group">
              <span className="field-label">Memory threshold override</span>
              <input
                className="field-input"
                onChange={(event) => setMemoryResultsOverride(event.target.value)}
                placeholder="Use global default"
                type="number"
                value={memoryResultsOverride}
              />
            </label>
            <label className="field-group">
              <span className="field-label">Context window override</span>
              <input
                className="field-input"
                onChange={(event) => setContextWindowOverride(event.target.value)}
                placeholder="Use global default"
                type="number"
                value={contextWindowOverride}
              />
            </label>
          </div>

          {error ? <div className="status-error">{error}</div> : null}

          <div className="flex flex-wrap gap-3 border-t border-border/80 pt-5">
            <button className="button-primary" disabled={saving} type="submit">
              {saving ? "Saving…" : "Save changes"}
            </button>
            <button className="button-danger" onClick={() => setDeleteOpen(true)} type="button">
              Delete patient
            </button>
          </div>
        </form>
      </section>

      <DeletePatientModal
        onClose={() => setDeleteOpen(false)}
        onDeleted={onDeleted}
        open={deleteOpen}
        patientId={patient.id}
        patientName={patient.name}
      />
    </>
  );
}
