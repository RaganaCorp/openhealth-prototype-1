"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { IngestionProgress } from "@/components/IngestionProgress";
import { UploadArea } from "@/components/UploadArea";
import { createPatient, type Patient } from "@/lib/api";

type AddPatientFlowProps = {
  open: boolean;
  onClose: () => void;
  onComplete: (patient: Patient, uploaded: boolean) => void;
};

export function AddPatientFlow({ open, onClose, onComplete }: AddPatientFlowProps) {
  const router = useRouter();
  const [step, setStep] = useState<"details" | "upload" | "processing">("details");
  const [name, setName] = useState("");
  const [patient, setPatient] = useState<Patient | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) {
    return null;
  }

  function reset() {
    setStep("details");
    setName("");
    setPatient(null);
    setJobId(null);
    setSubmitting(false);
    setError(null);
  }

  function dismiss() {
    // If a patient was already created, complete (so the parent refreshes its
    // patient list) rather than silently closing — otherwise the new patient
    // is missing from the list until a reload. Any in-flight ingestion keeps
    // running server-side and the workspace picks it up via job detection.
    if (patient) {
      onComplete(patient, false);
    } else {
      onClose();
    }
    reset();
  }

  return (
    <div
      className="modal-overlay"
      onClick={dismiss}
      role="presentation"
    >
      <div className="modal-panel max-w-2xl" onClick={(event) => event.stopPropagation()}>
        {step === "details" ? (
          <form
            className="space-y-5"
            onSubmit={async (event) => {
              event.preventDefault();
              try {
                setSubmitting(true);
                setError(null);
                const created = await createPatient(name);
                setPatient(created);
                setStep("upload");
              } catch (err) {
                setError(err instanceof Error ? err.message : "Could not create patient");
              } finally {
                setSubmitting(false);
              }
            }}
          >
            <div className="border-b border-border/80 pb-4">
              <p className="eyebrow">Add Patient</p>
              <h2 className="text-2xl font-semibold text-text-primary">Create a new patient workspace</h2>
              <p className="mt-2 text-sm leading-6 text-text-secondary">Start with a patient name, then optionally upload source documents right away.</p>
            </div>
            <label className="field-group">
              <span className="field-label">Patient name</span>
              <input className="field-input" onChange={(event) => setName(event.target.value)} placeholder="Jane Doe" value={name} />
            </label>
            {error ? <div className="status-error">{error}</div> : null}
            <div className="flex justify-end gap-3">
              <button
                className="button-secondary"
                onClick={() => {
                  reset();
                  onClose();
                }}
                type="button"
              >
                Cancel
              </button>
              <button className="button-primary" disabled={submitting || !name.trim()} type="submit">
                {submitting ? "Creating…" : "Continue"}
              </button>
            </div>
          </form>
        ) : null}

        {step === "upload" && patient ? (
          <div>
            <div className="border-b border-border/80 pb-4">
              <p className="eyebrow">Upload Documents</p>
              <h2 className="text-2xl font-semibold text-text-primary">Add files for {patient.name}</h2>
              <p className="mt-2 text-sm leading-6 text-text-secondary">You can skip this step and return later, or upload now and wait for ingestion to finish.</p>
            </div>
            <div className="mt-5">
              <UploadArea
                onUploaded={(nextJobId) => {
                  setJobId(nextJobId);
                  setStep("processing");
                }}
                patientId={patient.id}
              />
            </div>
            <div className="mt-5 flex justify-between gap-3">
              <button
                className="button-secondary"
                onClick={() => {
                  onComplete(patient, false);
                  reset();
                }}
                type="button"
              >
                Skip for now
              </button>
              <button
                className="button-secondary"
                onClick={() => {
                  router.push(`/patient/${patient.id}`);
                  onComplete(patient, false);
                  reset();
                }}
                type="button"
              >
                Open patient
              </button>
            </div>
          </div>
        ) : null}

        {step === "processing" && patient && jobId ? (
          <div>
            <div className="border-b border-border/80 pb-4">
              <p className="eyebrow">Ingestion</p>
              <h2 className="text-2xl font-semibold text-text-primary">Processing source files</h2>
              <p className="mt-2 text-sm leading-6 text-text-secondary">The workspace will open automatically once ingestion completes.</p>
            </div>
            <div className="mt-5">
              <IngestionProgress
                jobId={jobId}
                onResolved={(job) => {
                  if (job.status === "complete") {
                    onComplete(patient, true);
                    reset();
                    router.push(`/patient/${patient.id}`);
                  }
                  // On failure the banner stays visible (with a Dismiss button)
                  // so the error is surfaced before the modal closes.
                }}
                onDismiss={dismiss}
                onTerminalError={dismiss}
              />
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
