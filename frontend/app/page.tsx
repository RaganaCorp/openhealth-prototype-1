"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { AddPatientFlow } from "@/components/AddPatientFlow";
import { ClipboardIcon, ClockIcon, CpuIcon, LaptopIcon, PlusIcon, ShieldCheckIcon } from "@/components/icons";
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
      <section className="mx-auto grid w-full max-w-6xl gap-5 lg:grid-cols-12 lg:items-start">
        <div className="panel-card animate-fade-up lg:col-span-7">
          <div className="flex flex-col gap-4 border-b border-border/80 pb-5 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <p className="eyebrow">Patient Workspace</p>
              <h1 className="text-2xl font-semibold tracking-tight text-text-primary">Your patients</h1>
              <p className="mt-2 max-w-xl text-sm leading-6 text-text-secondary">
                Open a workspace to add documents and ask questions, all grounded in that patient&apos;s record.
              </p>
            </div>
            <button className="button-primary" onClick={() => setAddOpen(true)} type="button">
              <PlusIcon size={18} />
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
                className="group animate-fade-up rounded-[24px] border border-border/80 bg-surface p-5 shadow-lg shadow-black/5 transition-transform duration-300 hover:-translate-y-0.5"
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

        <aside className="panel-card animate-fade-up lg:col-span-5" style={{ animationDelay: "120ms" }}>
          <div className="flex items-center gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-primary-light text-primary">
              <ShieldCheckIcon size={20} />
            </span>
            <div>
              <p className="eyebrow">Private by design</p>
              <h2 className="text-xl font-semibold tracking-tight text-text-primary">Your data stays on this computer</h2>
            </div>
          </div>

          <p className="mt-4 text-sm leading-6 text-text-secondary">
            OpenHealth runs entirely on your machine. There is no account to create and no cloud service in the loop, so the
            documents and details you add never leave this device.
          </p>

          <ul className="mt-5 grid gap-4">
            <li className="flex items-start gap-3">
              <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-primary-light text-primary">
                <LaptopIcon size={18} />
              </span>
              <div>
                <p className="text-sm font-semibold text-text-primary">Your records stay local</p>
                <p className="mt-1 text-sm leading-6 text-text-secondary">
                  Every document you upload is stored and read right here, never uploaded anywhere.
                </p>
              </div>
            </li>
            <li className="flex items-start gap-3">
              <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-primary-light text-primary">
                <ClipboardIcon size={18} />
              </span>
              <div>
                <p className="text-sm font-semibold text-text-primary">Patient details make answers better</p>
                <p className="mt-1 text-sm leading-6 text-text-secondary">
                  Anything you add about a patient (age, conditions, history) also stays on your computer, and helps the AI
                  give more relevant, grounded answers.
                </p>
              </div>
            </li>
            <li className="flex items-start gap-3">
              <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-primary-light text-primary">
                <CpuIcon size={18} />
              </span>
              <div>
                <p className="text-sm font-semibold text-text-primary">Powered by local AI</p>
                <p className="mt-1 text-sm leading-6 text-text-secondary">
                  Answers come from AI models running on your own hardware through Ollama. No internet connection required.
                </p>
              </div>
            </li>
          </ul>

          <div className="mt-5 flex items-start gap-3 rounded-2xl border border-border/70 bg-surface-elevated/60 p-4">
            <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-primary-light text-primary">
              <ClockIcon size={18} />
            </span>
            <p className="text-sm leading-6 text-text-secondary">
              <span className="font-semibold text-text-primary">Worth the wait.</span> Because everything runs on your own
              computer, replies arrive slower than cloud tools like ChatGPT, and how sharp they are depends on the local models
              you choose. A little patience is the trade-off for keeping your data private.
            </p>
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
