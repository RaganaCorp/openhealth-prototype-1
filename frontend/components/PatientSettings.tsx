"use client";

import { useEffect, useState } from "react";

import { DeletePatientModal } from "@/components/DeletePatientModal";
import { IngestionProgress } from "@/components/IngestionProgress";
import { UploadArea } from "@/components/UploadArea";
import {
  deleteDocument,
  getActiveJob,
  getDocuments,
  patchPatient,
  type JobStatus,
  type Patient,
  type PatientDocument,
} from "@/lib/api";

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
  const [documents, setDocuments] = useState<PatientDocument[]>([]);
  const [loadingDocuments, setLoadingDocuments] = useState(true);
  const [activeJob, setActiveJob] = useState<JobStatus | null>(null);
  const [trackedJobId, setTrackedJobId] = useState<string | null>(null);
  const [deletingDocumentId, setDeletingDocumentId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadDocumentsAndJob() {
      try {
        setLoadingDocuments(true);
        const [docs, job] = await Promise.all([getDocuments(patient.id), getActiveJob(patient.id)]);
        if (!cancelled) {
          setDocuments(docs);
          setActiveJob(job);
          setTrackedJobId(job?.job_id ?? null);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Could not load documents");
        }
      } finally {
        if (!cancelled) {
          setLoadingDocuments(false);
        }
      }
    }

    void loadDocumentsAndJob();

    const interval = window.setInterval(async () => {
      try {
        const job = await getActiveJob(patient.id);
        if (!cancelled && job) {
          setActiveJob(job);
          setTrackedJobId(job.job_id);
        }
      } catch {
        // Keep the last known job visible if status polling is already handling it.
      }
    }, 2000);

    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [patient.id]);

  function formatBytes(size: number): string {
    if (!Number.isFinite(size) || size <= 0) {
      return "0 B";
    }
    const units = ["B", "KB", "MB", "GB"];
    let value = size;
    let unitIndex = 0;
    while (value >= 1024 && unitIndex < units.length - 1) {
      value /= 1024;
      unitIndex += 1;
    }
    const rounded = value >= 100 ? Math.round(value) : Math.round(value * 10) / 10;
    return `${rounded} ${units[unitIndex]}`;
  }

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

        <div className="mt-8 border-t border-border/80 pt-6">
          <div className="flex items-center justify-between">
            <div>
              <p className="eyebrow">Documents Loaded</p>
              <h2 className="text-2xl font-semibold text-text-primary">{documents.length} documents</h2>
              <p className="mt-1 text-sm text-text-secondary">Upload new files or delete files from this patient's record.</p>
            </div>
          </div>

          {trackedJobId ? (
            <div className="mt-5">
              <IngestionProgress
                jobId={trackedJobId}
                onResolved={async (job) => {
                  const [docs, nextJob] = await Promise.all([getDocuments(patient.id), getActiveJob(patient.id)]);
                  setDocuments(docs);
                  setActiveJob(nextJob);
                  if (job?.status === "complete") {
                    setTrackedJobId(null);
                  }
                }}
              />
            </div>
          ) : null}

          <div className="mt-5">
            <UploadArea
              onUploaded={async (jobId) => {
                setTrackedJobId(jobId);
                setActiveJob({
                  job_id: jobId,
                  patient_id: patient.id,
                  status: "running",
                  total: 0,
                  processed: 0,
                  current_file: null,
                  phase: "queued",
                  phase_started_at: new Date().toISOString(),
                  started_at: new Date().toISOString(),
                  completed_at: null,
                });
              }}
              patientId={patient.id}
            />
          </div>

          <div className="mt-5 space-y-2">
            {loadingDocuments ? <div className="empty-state">Loading documents…</div> : null}
            {!loadingDocuments && documents.length === 0 ? <div className="empty-state">No documents loaded yet.</div> : null}

            {documents.map((doc) => (
              <div className="patient-tile" key={doc.id}>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-text-primary">{doc.filename}</p>
                  <p className="mt-1 text-xs text-text-secondary">
                    {doc.document_type} · {doc.date_detected} · {formatBytes(doc.size)}
                  </p>
                </div>
                <button
                  className="button-danger px-3 py-2 text-xs"
                  disabled={Boolean(activeJob) || deletingDocumentId === doc.id}
                  onClick={async () => {
                    const confirmed = window.confirm(`Delete ${doc.filename}? This will trigger a rebuild.`);
                    if (!confirmed) {
                      return;
                    }

                    try {
                      setDeletingDocumentId(doc.id);
                      setError(null);
                      const result = await deleteDocument(patient.id, doc.id);
                      setTrackedJobId(result.job_id);
                      setActiveJob({
                        job_id: result.job_id,
                        patient_id: patient.id,
                        status: "running",
                        total: 0,
                        processed: 0,
                        current_file: null,
                        phase: "queued",
                        phase_started_at: new Date().toISOString(),
                        started_at: new Date().toISOString(),
                        completed_at: null,
                      });
                    } catch (err) {
                      setError(err instanceof Error ? err.message : "Could not delete document");
                    } finally {
                      setDeletingDocumentId(null);
                    }
                  }}
                  type="button"
                >
                  {deletingDocumentId === doc.id ? "Deleting…" : "Delete"}
                </button>
              </div>
            ))}
          </div>
        </div>
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
