"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { AddPatientFlow } from "@/components/AddPatientFlow";
import { formatDate, getPatients, type Patient } from "@/lib/api";

export default function HomePage() {
  const [patients, setPatients] = useState<Patient[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);

  async function loadPatients() {
    try {
      setError(null);
      const data = await getPatients();
      setPatients(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load patients");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadPatients();
  }, []);

  return (
    <>
      <section className="grid gap-6 lg:grid-cols-[1.3fr_0.7fr]">
        <div className="panel-card animate-fade-up">
          <div className="flex flex-col gap-4 border-b border-border/80 pb-5 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <p className="eyebrow">Patient Workspace</p>
              <h1 className="text-3xl font-semibold tracking-tight text-text-primary">Medical records, locally grounded</h1>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-text-secondary">
                Create a patient profile, add documents, and ask questions against a local medical record without depending on a cloud service.
              </p>
            </div>
            <button className="button-primary" onClick={() => setAddOpen(true)} type="button">
              Add Patient
            </button>
          </div>

          <div className="mt-6 grid gap-4">
            {loading ? <div className="empty-state">Loading patients…</div> : null}
            {error ? <div className="status-error">{error}</div> : null}
            {!loading && !error && patients.length === 0 ? (
              <div className="empty-state">
                <p className="text-base font-medium text-text-primary">No patients yet</p>
                <p className="mt-2 text-sm text-text-secondary">Start by creating a patient profile, then upload documents when you are ready.</p>
              </div>
            ) : null}

            {patients.map((patient, index) => (
              <article
                className="group rounded-[24px] border border-border/80 bg-surface p-5 shadow-lg shadow-black/5 transition-transform duration-300 hover:-translate-y-0.5"
                key={patient.id}
                style={{ animationDelay: `${index * 120}ms` }}
              >
                <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                  <div>
                    <p className="text-lg font-semibold text-text-primary">{patient.name}</p>
                    <div className="mt-2 flex flex-wrap gap-3 text-sm text-text-secondary">
                      <span>{patient.document_count} documents</span>
                      <span>Last ingest: {formatDate(patient.last_ingested_at)}</span>
                    </div>
                  </div>
                  <Link className="button-secondary" href={`/patient/${patient.id}`}>
                    Open
                  </Link>
                </div>
              </article>
            ))}
          </div>
        </div>

        <aside className="panel-card animate-fade-up [animation-delay:120ms]">
          <p className="eyebrow">What This Builds</p>
          <h2 className="text-2xl font-semibold text-text-primary">A calm, local clinical workspace</h2>
          <div className="mt-5 space-y-4 text-sm leading-6 text-text-secondary">
            <p>Chat sits beside the patient's records so answers stay grounded in the source documents.</p>
            <p>Uploads trigger ingestion jobs with visible progress, and citations stay attached to every assistant response.</p>
            <p>Global model settings live in the source-controlled defaults file and can be overridden through the mounted runtime config.</p>
          </div>
        </aside>
      </section>

      <AddPatientFlow
        onClose={() => setAddOpen(false)}
        onComplete={() => {
          setAddOpen(false);
          void loadPatients();
        }}
        open={addOpen}
      />
    </>
  );
}
