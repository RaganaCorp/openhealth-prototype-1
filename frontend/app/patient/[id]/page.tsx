"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { startTransition, useEffect, useState } from "react";

import { AddPatientFlow } from "@/components/AddPatientFlow";
import { Chat } from "@/components/Chat";
import { IngestionProgress } from "@/components/IngestionProgress";
import { SummaryPanel } from "@/components/SummaryPanel";
import { Timeline } from "@/components/Timeline";
import { UploadArea } from "@/components/UploadArea";
import {
  createChatSession,
  formatDate,
  getActiveJob,
  getChatSessions,
  getDocuments,
  getPatient,
  getPatients,
  getSummary,
  getTimeline,
  type ChatSession,
  type JobStatus,
  type Patient,
  type PatientDocument,
  type TimelineEvent,
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
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [documents, setDocuments] = useState<PatientDocument[]>([]);
  const [activeJob, setActiveJob] = useState<JobStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<MainTab>("chat");

  async function refreshSidebar() {
    const [patientData, patientsData, sessionsData] = await Promise.all([
      getPatient(patientId),
      getPatients(),
      getChatSessions(patientId),
    ]);

    setPatient(patientData);
    setPatients(patientsData);
    setSessions(sessionsData);
    return sessionsData;
  }

  async function refreshRecordView() {
    const [summaryData, timelineData, documentsData] = await Promise.all([getSummary(patientId), getTimeline(patientId), getDocuments(patientId)]);
    setSummary(summaryData.summary);
    setTimeline(timelineData);
    setDocuments(documentsData);
  }

  async function loadPage() {
    try {
      setError(null);
      const loadedSessions = await refreshSidebar();
      await refreshRecordView();

      if (!selectedSessionId) {
        if (loadedSessions.length > 0) {
          startTransition(() => {
            router.replace(`/patient/${patientId}?session=${loadedSessions[0].id}`);
          });
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load patient workspace");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadPage();
  }, [patientId]);

  useEffect(() => {
    const interval = window.setInterval(async () => {
      try {
        const job = await getActiveJob(patientId);
        setActiveJob(job);
      } catch {
        setActiveJob(null);
      }
    }, 2000);

    void getActiveJob(patientId).then(setActiveJob).catch(() => setActiveJob(null));

    return () => window.clearInterval(interval);
  }, [patientId]);

  const activeSession = sessions.find((session) => session.id === selectedSessionId) ?? null;

  if (loading) {
    return <div className="empty-state">Loading workspace…</div>;
  }

  if (error || !patient) {
    return <div className="status-error">{error ?? "Patient not found"}</div>;
  }

  return (
    <>
      <div className="grid gap-6 xl:grid-cols-[340px_minmax(0,1fr)_320px]">
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

          <div className="mt-4 rounded-[20px] border border-border/80 bg-surface-elevated p-3 text-xs text-text-secondary">
            {patient.document_count} docs · Last ingest: {formatDate(patient.last_ingested_at)}
          </div>

          <div className="mt-8 border-t border-border/80 pt-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="eyebrow">Chats</p>
                <p className="text-sm text-text-secondary">Keep separate threads for distinct questions.</p>
              </div>
              <button
                className="button-secondary px-3 py-2 text-xs"
                onClick={() => {
                  startTransition(() => {
                    router.push(`/patient/${patient.id}?session=new`);
                  });
                }}
                type="button"
              >
                New Chat
              </button>
            </div>

            <div className="mt-4 space-y-2">
              {sessions.map((session) => (
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
          <div className="mb-4 border-b border-border/80 pb-4">
            <div className="flex flex-wrap gap-2">
              <button
                className={activeTab === "summary" ? "button-primary px-3 py-2 text-xs" : "button-secondary px-3 py-2 text-xs"}
                onClick={() => setActiveTab("summary")}
                type="button"
              >
                Summary
              </button>
              <button
                className={activeTab === "chat" ? "button-primary px-3 py-2 text-xs" : "button-secondary px-3 py-2 text-xs"}
                onClick={() => setActiveTab("chat")}
                type="button"
              >
                Chat
              </button>
              <button
                className={activeTab === "files" ? "button-primary px-3 py-2 text-xs" : "button-secondary px-3 py-2 text-xs"}
                onClick={() => setActiveTab("files")}
                type="button"
              >
                Record Files
              </button>
            </div>
          </div>

          {activeJob ? (
            <div className="mb-4">
              <IngestionProgress
                jobId={activeJob.job_id}
                onResolved={async () => {
                  setActiveJob(null);
                  await refreshSidebar();
                  await refreshRecordView();
                }}
              />
            </div>
          ) : null}

          {activeTab === "summary" ? (
            <div className="panel-scroll flex-1">
              <SummaryPanel summary={summary} />
            </div>
          ) : null}

          {activeTab === "chat" ? (
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
          ) : null}

          {activeTab === "files" ? (
            <div className="panel-scroll flex-1 space-y-5">
              <div>
                <p className="eyebrow">Record Files</p>
                <h2 className="text-2xl font-semibold text-text-primary">{documents.length} documents loaded</h2>
                <p className="mt-2 text-sm leading-6 text-text-secondary">Add source documents for this patient from your computer.</p>
              </div>

              <UploadArea
                onUploaded={async () => {
                  const job = await getActiveJob(patient.id);
                  setActiveJob(job);
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
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </section>

        <aside className="panel-card panel-scroll h-[calc(100vh-8.75rem)] animate-fade-up [animation-delay:240ms]">
          <Timeline events={timeline} />
        </aside>
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
