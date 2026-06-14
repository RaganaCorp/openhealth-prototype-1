"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { ConditionsStep } from "@/components/intake/ConditionsStep";
import { DemographicsStep } from "@/components/intake/DemographicsStep";
import { LifestyleStep } from "@/components/intake/LifestyleStep";
import {
  buildIntakePayload,
  demographicsTouched,
  emptyDemographics,
  emptySocialHistory,
  socialHistoryTouched,
  type DemographicsDraft,
  type SocialHistoryDraft,
} from "@/components/intake/types";
import { IngestionProgress } from "@/components/IngestionProgress";
import { UploadArea } from "@/components/UploadArea";
import { createPatient, type Patient, type PatientCondition } from "@/lib/api";

type AddPatientFlowProps = {
  open: boolean;
  onClose: () => void;
  onComplete: (patient: Patient, uploaded: boolean) => void;
};

type WizardStep = "name" | "demographics" | "lifestyle" | "conditions" | "documents" | "processing";

export function AddPatientFlow({ open, onClose, onComplete }: AddPatientFlowProps) {
  const router = useRouter();
  const [step, setStep] = useState<WizardStep>("name");
  const [name, setName] = useState("");
  const [demographics, setDemographics] = useState<DemographicsDraft>(emptyDemographics);
  const [social, setSocial] = useState<SocialHistoryDraft>(emptySocialHistory);
  const [conditions, setConditions] = useState<PatientCondition[]>([]);
  const [patient, setPatient] = useState<Patient | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Escape-to-dismiss, matching the global Settings modal. Re-registers on the
  // inputs `dismiss` reads so the unsaved-changes confirm always sees fresh state.
  useEffect(() => {
    if (!open) {
      return;
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        dismiss();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, name, demographics, social, conditions, patient]);

  if (!open) {
    return null;
  }

  function reset() {
    setStep("name");
    setName("");
    setDemographics(emptyDemographics);
    setSocial(emptySocialHistory);
    setConditions([]);
    setPatient(null);
    setJobId(null);
    setSubmitting(false);
    setError(null);
  }

  function hasUnsavedInput(): boolean {
    return (
      name.trim() !== "" ||
      demographicsTouched(demographics) ||
      socialHistoryTouched(social) ||
      conditions.length > 0
    );
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
      const created = await createPatient(buildIntakePayload(name, demographics, social, conditions));
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

  const stepIndex = { name: 0, demographics: 1, lifestyle: 2, conditions: 3, documents: 4, processing: 4 }[step];

  return (
    <div className="modal-overlay" onClick={dismiss} role="presentation">
      <div className="modal-panel max-w-2xl" onClick={(event) => event.stopPropagation()}>
        {step !== "processing" ? (
          <div className="wizard-progress" aria-hidden>
            {[0, 1, 2, 3, 4].map((index) => (
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
                A name is all you need to start. Add demographics and conditions now if you like, or skip straight to
                uploading documents.
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
            {error ? <div className="status-error">{error}</div> : null}
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <button
                className="button-secondary"
                disabled={!name.trim() || submitting}
                onClick={createWorkspace}
                type="button"
              >
                {submitting ? "Creating…" : "Skip details & add documents"}
              </button>
              <div className="flex justify-end gap-3">
                <button className="button-secondary" onClick={dismiss} type="button">
                  Cancel
                </button>
                <button className="button-primary" disabled={!name.trim() || submitting} type="submit">
                  Continue
                </button>
              </div>
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
                <button className="button-primary" onClick={() => setStep("lifestyle")} type="button">
                  Continue
                </button>
              </div>
            </div>
          </div>
        ) : null}

        {step === "lifestyle" ? (
          <div>
            <LifestyleStep onChange={setSocial} patientName={name} value={social} />
            {error ? <div className="status-error mt-4">{error}</div> : null}
            <div className="mt-6 flex justify-between gap-3">
              <button className="button-secondary" onClick={() => setStep("demographics")} type="button">
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
              <button className="button-secondary" onClick={() => setStep("lifestyle")} type="button">
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
