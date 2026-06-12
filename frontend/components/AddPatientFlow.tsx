"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { ConditionsStep } from "@/components/intake/ConditionsStep";
import { DemographicsStep } from "@/components/intake/DemographicsStep";
import {
  buildIntakePayload,
  demographicsTouched,
  emptyDemographics,
  type DemographicsDraft,
} from "@/components/intake/types";
import { IngestionProgress } from "@/components/IngestionProgress";
import { UploadArea } from "@/components/UploadArea";
import { createPatient, type Patient, type PatientCondition } from "@/lib/api";

type AddPatientFlowProps = {
  open: boolean;
  onClose: () => void;
  onComplete: (patient: Patient, uploaded: boolean) => void;
};

type WizardStep = "name" | "demographics" | "conditions" | "documents" | "processing";

export function AddPatientFlow({ open, onClose, onComplete }: AddPatientFlowProps) {
  const router = useRouter();
  const [step, setStep] = useState<WizardStep>("name");
  const [name, setName] = useState("");
  const [demographics, setDemographics] = useState<DemographicsDraft>(emptyDemographics);
  const [conditions, setConditions] = useState<PatientCondition[]>([]);
  const [patient, setPatient] = useState<Patient | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) {
    return null;
  }

  function reset() {
    setStep("name");
    setName("");
    setDemographics(emptyDemographics);
    setConditions([]);
    setPatient(null);
    setJobId(null);
    setSubmitting(false);
    setError(null);
  }

  function hasUnsavedInput(): boolean {
    return name.trim() !== "" || demographicsTouched(demographics) || conditions.length > 0;
  }

  function dismiss() {
    // If a patient was already created, complete (so the parent refreshes its
    // patient list) rather than silently closing — otherwise the new patient is
    // missing from the list until a reload. Any in-flight ingestion keeps running
    // server-side and the workspace picks it up via job detection.
    if (patient) {
      onComplete(patient, false);
      reset();
      return;
    }
    // No patient yet: confirm before discarding entered intake data.
    if (hasUnsavedInput() && !window.confirm("Discard this new patient? Entered information will be lost.")) {
      return;
    }
    reset();
    onClose();
  }

  // Create the workspace once intake (name → demographics → conditions) is done.
  // The patient must exist before the optional documents step so uploads can
  // attach to its id, mirroring the rest of the app's ingest flow.
  async function createWorkspace() {
    try {
      setSubmitting(true);
      setError(null);
      const created = await createPatient(buildIntakePayload(name, demographics, conditions));
      setPatient(created);
      setStep("documents");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create patient");
    } finally {
      setSubmitting(false);
    }
  }

  function goToWorkspace(target: Patient, uploaded: boolean) {
    onComplete(target, uploaded);
    reset();
    router.push(`/patient/${target.id}`);
  }

  const stepIndex = { name: 0, demographics: 1, conditions: 2, documents: 3, processing: 3 }[step];

  return (
    <div className="modal-overlay" onClick={dismiss} role="presentation">
      <div className="modal-panel max-w-2xl" onClick={(event) => event.stopPropagation()}>
        {step !== "processing" ? (
          <div className="wizard-progress" aria-hidden>
            {[0, 1, 2, 3].map((index) => (
              <span className={`wizard-dot ${index <= stepIndex ? "active" : ""}`} key={index} />
            ))}
          </div>
        ) : null}

        {step === "name" ? (
          <form
            className="space-y-5"
            onSubmit={(event) => {
              event.preventDefault();
              if (name.trim()) {
                setStep("demographics");
              }
            }}
          >
            <div className="border-b border-border/80 pb-4">
              <p className="eyebrow">Add Patient</p>
              <h2 className="text-2xl font-semibold text-text-primary">Create a new patient workspace</h2>
              <p className="mt-2 text-sm leading-6 text-text-secondary">
                Start with a name, then add demographics, conditions, and source documents — every step after this is
                optional.
              </p>
            </div>
            <label className="field-group">
              <span className="field-label">Patient name</span>
              <input
                autoFocus
                className="field-input"
                onChange={(event) => setName(event.target.value)}
                placeholder="Jane Doe"
                value={name}
              />
            </label>
            <div className="flex justify-end gap-3">
              <button className="button-secondary" onClick={dismiss} type="button">
                Cancel
              </button>
              <button className="button-primary" disabled={!name.trim()} type="submit">
                Continue
              </button>
            </div>
          </form>
        ) : null}

        {step === "demographics" ? (
          <div>
            <DemographicsStep onChange={setDemographics} patientName={name} value={demographics} />
            {error ? <div className="status-error mt-4">{error}</div> : null}
            <div className="mt-6 flex justify-between gap-3">
              <button className="button-secondary" onClick={() => setStep("name")} type="button">
                Back
              </button>
              <div className="flex gap-3">
                <button className="button-secondary" onClick={dismiss} type="button">
                  Cancel
                </button>
                <button className="button-primary" onClick={() => setStep("conditions")} type="button">
                  Continue
                </button>
              </div>
            </div>
          </div>
        ) : null}

        {step === "conditions" ? (
          <div>
            <ConditionsStep onChange={setConditions} patientName={name} selected={conditions} />
            {error ? <div className="status-error mt-4">{error}</div> : null}
            <div className="mt-6 flex justify-between gap-3">
              <button className="button-secondary" onClick={() => setStep("demographics")} type="button">
                Back
              </button>
              <div className="flex gap-3">
                <button className="button-secondary" onClick={dismiss} type="button">
                  Cancel
                </button>
                <button className="button-primary" disabled={submitting} onClick={createWorkspace} type="button">
                  {submitting ? "Creating…" : "Continue"}
                </button>
              </div>
            </div>
          </div>
        ) : null}

        {step === "documents" && patient ? (
          <div>
            <div className="border-b border-border/80 pb-4">
              <p className="eyebrow">Upload Documents</p>
              <h2 className="text-2xl font-semibold text-text-primary">Add files for {patient.name}</h2>
              <p className="mt-2 text-sm leading-6 text-text-secondary">
                Optional — upload source documents now and wait for ingestion, or skip and add them later.
              </p>
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
            <div className="mt-5 flex justify-end gap-3">
              <button className="button-primary" onClick={() => goToWorkspace(patient, false)} type="button">
                Skip & open patient
              </button>
            </div>
          </div>
        ) : null}

        {step === "processing" && patient && jobId ? (
          <div>
            <div className="border-b border-border/80 pb-4">
              <p className="eyebrow">Ingestion</p>
              <h2 className="text-2xl font-semibold text-text-primary">Processing source files</h2>
              <p className="mt-2 text-sm leading-6 text-text-secondary">
                The workspace will open automatically once ingestion completes.
              </p>
            </div>
            <div className="mt-5">
              <IngestionProgress
                jobId={jobId}
                onDismiss={dismiss}
                onResolved={(job) => {
                  if (job.status === "complete") {
                    goToWorkspace(patient, true);
                  }
                  // On failure the banner stays visible (with a Dismiss button) so
                  // the error is surfaced before the modal closes.
                }}
                onTerminalError={dismiss}
              />
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
