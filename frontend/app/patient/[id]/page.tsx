"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { startTransition, useEffect, useRef, useState } from "react";

import { AddPatientFlow } from "@/components/AddPatientFlow";
import { Chat } from "@/components/Chat";
import { IngestionProgress } from "@/components/IngestionProgress";
import { SummaryPanel } from "@/components/SummaryPanel";
import { UploadArea } from "@/components/UploadArea";
import {
  createChatSession,
  deleteDocument,
  formatDate,
  getActiveJob,
  getChatSessions,
  getDocuments,
  getPatient,
  getPatients,
  getSummary,
  type ChatSession,
  type JobStatus,
  type Patient,
  type PatientDocument,
} from "@/lib/api";

type MainTab = "summary" | "chat" | "files";

export default function PatientPage() {
  const params = useParams<{ id: string }>();
  const patientId = params.id;
  const router = useRouter();
  const searchParams = useSearchParams();
  const selectedSessionId = searchParams.get("session");

  const [patients, setPatients] = useState<Patient[]>([]);
  const [patient, setPatient] = useState<Patient | null>(null);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [summary, setSummary] = useState("");
  const [documents, setDocuments] = useState<PatientDocument[]>([]);
  const [activeJob, setActiveJob] = useState<JobStatus | null>(null);
  const [trackedJobId, setTrackedJobId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<MainTab>("chat");
  const [deletingDocumentId, setDeletingDocumentId] = useState<string | null>(null);
  // True once the tracked job reached a terminal state (failed banner kept
  // visible) — idle job detection may resume even though the banner is shown.
  const [trackedJobTerminal, setTrackedJobTerminal] = useState(false);
  // Guards against a slow response for the previous patient overwriting the
  // newly selected patient's state (the App Router reuses this component
  // instance when only the [id] param changes).
  const patientIdRef = useRef(patientId);

  async function refreshSidebar() {
    const pid = patientId;
    const [patientData, patientsData, sessionsData] = await Promise.all([
      getPatient(pid),
      getPatients(),
      getChatSessions(pid),
    ]);
    if (patientIdRef.current !== pid) {
      return sessionsData;
    }
    setPatient(patientData);
    setPatients(patientsData);
    setSessions(sessionsData);
    return sessionsData;
  }

  async function refreshRecordView() {
    const pid = patientId;
    const [summaryData, documentsData] = await Promise.all([getSummary(pid), getDocuments(pid)]);
    if (patientIdRef.current !== pid) {
      return;
    }
    setSummary(summaryData.summary);
    setDocuments(documentsData);
  }

  async function loadPage() {
    const pid = patientId;
    try {
      setError(null);
      const loadedSessions = await refreshSidebar();
      await refreshRecordView();

      if (!selectedSessionId && patientIdRef.current === pid && loadedSessions.length > 0) {
        // Land on the most recently active session — same ordering as the sidebar.
        const newest = [...loadedSessions].sort((a, b) => {
          const aTime = a.last_message_at ?? a.created_at ?? "";
          const bTime = b.last_message_at ?? b.created_at ?? "";
          return bTime.localeCompare(aTime);
        })[0];
        startTransition(() => {
          router.replace(`/patient/${pid}?session=${newest.id}`);
        });
      }
    } catch (err) {
      if (patientIdRef.current === pid) {
        setError(err instanceof Error ? err.message : "Could not load patient workspace");
      }
    } finally {
      if (patientIdRef.current === pid) {
        setLoading(false);
      }
    }
  }

  useEffect(() => {
    // Reset all patient-scoped state immediately so the previous patient's
    // medical data is never rendered (or sent to) under the new patient's URL.
    patientIdRef.current = patientId;
    setLoading(true);
    setPatient(null);
    setSummary("");
    setDocuments([]);
    setSessions([]);
    setActiveJob(null);
    setTrackedJobId(null);
    setTrackedJobTerminal(false);
    void loadPage();
  }, [patientId]);

  // Detect jobs started outside the UI (e.g. the file watcher ingesting a file
  // dropped into the data folder). While a RUNNING job is tracked, IngestionProgress
  // polls its status, so we stay idle here instead of polling redundantly. Once the
  // tracked job is terminal (e.g. a failed banner left visible), detection resumes
  // so a new backend job isn't invisible.
  useEffect(() => {
    if (trackedJobId && !trackedJobTerminal) {
      return;
    }
    let cancelled = false;
    const check = async () => {
      try {
        const job = await getActiveJob(patientId);
        if (!cancelled && job && job.job_id !== trackedJobId) {
          setActiveJob(job);
          setTrackedJobId(job.job_id);
          setTrackedJobTerminal(false);
        }
      } catch {
        // Ignore transient polling errors.
      }
    };
    void check();
    const interval = window.setInterval(() => {
      void check();
    }, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [patientId, trackedJobId, trackedJobTerminal]);

  const activeSession = sessions.find((session) => session.id === selectedSessionId) ?? null;

  if (loading) {
    return <div className="empty-state">Loading workspace…</div>;
  }

  if (error || !patient) {
    return <div className="status-error">{error ?? "Patient not found"}</div>;
  }

  return (
    <>
      <div className="grid gap-6 xl:grid-cols-[340px_minmax(0,1fr)]">
        <aside className="panel-card panel-scroll h-[calc(100vh-8.75rem)] animate-fade-up">
          <div className="flex items-center justify-between">
            <div>
              <p className="eyebrow">Patient</p>
              <p className="text-sm text-text-secondary">Pick a patient workspace.</p>
            </div>
            <button className="icon-button" onClick={() => setAddOpen(true)} type="button">
              +
            </button>
          </div>

          <div className="mt-5 flex items-center gap-2">
            <select
              className="field-input"
              onChange={(event) => {
                const nextPatientId = event.target.value;
                startTransition(() => {
                  router.push(`/patient/${nextPatientId}`);
                });
              }}
              value={patient.id}
            >
              {patients.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
            <Link aria-label="Patient settings" className="icon-button" href={`/patient/${patient.id}/settings`}>
              ⚙
            </Link>
          </div>

          <div className="mt-4 rounded-2xl border border-border/60 bg-surface p-3 text-xs text-text-secondary">
            {patient.document_count} docs · Last ingest: {formatDate(patient.last_ingested_at)}
          </div>

          <div className="mt-6 border-t border-border/80 pt-5">
            <div className="flex items-center justify-between">
              <div>
                <p className="eyebrow">Chats</p>
                <p className="text-sm text-text-secondary">Keep separate threads for distinct questions.</p>
              </div>
              <button
                className="button-secondary whitespace-nowrap px-4 py-2 text-sm"
                onClick={() => {
                  setActiveTab("chat");
                  startTransition(() => {
                    router.push(`/patient/${patient.id}?session=new`);
                  });
                }}
                type="button"
              >
                New chat
              </button>
            </div>

            <div className="mt-4 space-y-2">
              {[...sessions].sort((a, b) => {
                const aTime = a.last_message_at ?? a.created_at ?? "";
                const bTime = b.last_message_at ?? b.created_at ?? "";
                return bTime.localeCompare(aTime);
              }).map((session) => (
                <button
                  className={`session-tile ${session.id === activeSession?.id ? "session-tile-active" : ""}`}
                  key={session.id}
                  onClick={() => {
                    startTransition(() => {
                      router.push(`/patient/${patient.id}?session=${session.id}`);
                    });
                  }}
                  type="button"
                >
                  <span className="block truncate text-sm font-medium">{session.title}</span>
                  <span className="mt-1 block text-xs text-text-secondary">{formatDate(session.last_message_at ?? session.created_at)}</span>
                </button>
              ))}
            </div>
          </div>
        </aside>

        <section className="panel-card flex h-[calc(100vh-8.75rem)] min-h-[640px] flex-col animate-fade-up [animation-delay:110ms]">
          <div className="mb-4 border-b border-border/80 pb-2">
            <div className="flex">
              <button
                className={`tab-button ${activeTab === "summary" ? "active" : ""}`}
                onClick={() => setActiveTab("summary")}
                type="button"
              >
                Summary
              </button>
              <button
                className={`tab-button ${activeTab === "chat" ? "active" : ""}`}
                onClick={() => setActiveTab("chat")}
                type="button"
              >
                Chat
              </button>
              <button
                className={`tab-button ${activeTab === "files" ? "active" : ""}`}
                onClick={() => setActiveTab("files")}
                type="button"
              >
                Record Files
              </button>
            </div>
          </div>

          {trackedJobId ? (
            <div className="mb-4">
              <IngestionProgress
                jobId={trackedJobId}
                onResolved={async (job) => {
                  // Job is terminal — stop treating it as active so chat and
                  // record actions unblock immediately, and let idle job
                  // detection resume.
                  setActiveJob(null);
                  setTrackedJobTerminal(true);
                  // On success, hide the banner; on failure keep it visible
                  // (with a Dismiss button) so the error stays surfaced.
                  if (job.status === "complete") {
                    setTrackedJobId(null);
                  }
                  await refreshSidebar();
                  await refreshRecordView();
                }}
                onTerminalError={() => {
                  // Job status is unreachable (e.g. backend restarted and the
                  // in-memory job is gone) — unblock the UI and resume detection.
                  setActiveJob(null);
                  setTrackedJobTerminal(true);
                }}
                onDismiss={() => {
                  setActiveJob(null);
                  setTrackedJobId(null);
                  setTrackedJobTerminal(false);
                }}
              />
            </div>
          ) : null}

          {activeTab === "summary" ? (
            <div className="panel-scroll flex-1">
              <SummaryPanel
                onSummaryUpdated={(nextSummary) => setSummary(nextSummary)}
                patientId={patient.id}
                summary={summary}
              />
            </div>
          ) : null}

          {/* Chat stays mounted across tab switches so an in-flight send (pending
              bubble, thinking indicator, error state) isn't lost. */}
          <div className={activeTab === "chat" ? "flex min-h-0 flex-1 flex-col" : "hidden"}>
            <Chat
              activeJobId={activeJob?.job_id ?? null}
              onCreateSession={async () => {
                const created = await createChatSession(patient.id);
                await refreshSidebar();
                startTransition(() => {
                  router.replace(`/patient/${patient.id}?session=${created.chat_session_id}`);
                });
                return created.chat_session_id;
              }}
              onSessionChanged={async () => {
                await refreshSidebar();
              }}
              patientId={patient.id}
              session={activeSession}
            />
          </div>

          {activeTab === "files" ? (
            <div className="panel-scroll flex-1 space-y-5">
              <div>
                <p className="eyebrow">Record Files</p>
                <h2 className="text-2xl font-semibold text-text-primary">{documents.length} documents loaded</h2>
                <p className="mt-2 text-sm leading-6 text-text-secondary">Add source documents for this patient from your computer.</p>
              </div>

              <UploadArea
                onUploaded={(jobId) => {
                  const startedAt = new Date().toISOString();
                  setTrackedJobId(jobId);
                  setTrackedJobTerminal(false);
                  setActiveJob({
                    job_id: jobId,
                    patient_id: patient.id,
                    status: "running",
                    total: 0,
                    processed: 0,
                    current_file: null,
                    phase: "queued",
                    phase_started_at: startedAt,
                    started_at: startedAt,
                    completed_at: null,
                  });
                }}
                patientId={patient.id}
              />

              <div className="space-y-2">
                {documents.length === 0 ? <div className="empty-state">No documents loaded yet.</div> : null}
                {documents.map((doc) => (
                  <div className="patient-tile" key={doc.id}>
                    <div className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">{doc.filename}</span>
                      <span className="mt-1 block text-xs text-text-secondary">
                        {doc.document_type} · {doc.date_detected}
                      </span>
                    </div>
                    <button
                      className="button-danger px-3 py-2 text-xs"
                      disabled={Boolean(trackedJobId) || deletingDocumentId === doc.id}
                      onClick={async () => {
                        const confirmed = window.confirm(`Delete ${doc.filename}? This will trigger a rebuild.`);
                        if (!confirmed) {
                          return;
                        }

                        try {
                          setDeletingDocumentId(doc.id);
                          const result = await deleteDocument(patient.id, doc.id);
                          setTrackedJobId(result.job_id);
                          setTrackedJobTerminal(false);
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
          ) : null}
        </section>


      </div>

      <AddPatientFlow
        onClose={() => setAddOpen(false)}
        onComplete={(createdPatient, uploaded) => {
          setAddOpen(false);
          void refreshSidebar();
          if (uploaded) {
            startTransition(() => {
              router.push(`/patient/${createdPatient.id}`);
            });
          }
        }}
        open={addOpen}
      />
    </>
  );
}
