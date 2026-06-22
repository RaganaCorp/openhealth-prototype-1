"use client";

import { useEffect, useRef, useState } from "react";

import { InfoIcon } from "@/components/icons";
import { getJobStatus, type JobStatus } from "@/lib/api";

type IngestionProgressProps = {
  jobId: string;
  onResolved?: (job: JobStatus) => void | Promise<void>;
  onDismiss?: () => void;
  // Called when the job's status becomes unreachable (e.g. the backend restarted
  // and its in-memory job record is gone) so the parent can unblock the UI.
  onTerminalError?: () => void;
};

export function IngestionProgress({ jobId, onResolved, onDismiss, onTerminalError }: IngestionProgressProps) {
  const [job, setJob] = useState<JobStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const resolvedRef = useRef(false);
  const onResolvedRef = useRef(onResolved);
  const onTerminalErrorRef = useRef(onTerminalError);

  // Keep the latest callbacks in refs so they aren't effect dependencies. The
  // parent passes fresh inline callbacks on every render; depending on them would
  // tear down and recreate the poll interval (and reset resolvedRef) each time.
  useEffect(() => {
    onResolvedRef.current = onResolved;
    onTerminalErrorRef.current = onTerminalError;
  });

  useEffect(() => {
    let cancelled = false;
    let intervalId: number | undefined;
    let consecutiveErrors = 0;

    function stopPolling() {
      if (intervalId !== undefined) {
        window.clearInterval(intervalId);
      }
    }

    async function load() {
      try {
        const nextJob = await getJobStatus(jobId);
        if (cancelled) {
          return;
        }

        consecutiveErrors = 0;
        setError(null);
        setJob(nextJob);
        if (nextJob.status !== "running") {
          // Terminal (complete or failed) — stop polling so a finished or failed
          // job never keeps hitting the backend every 2s.
          stopPolling();
          if (!resolvedRef.current) {
            resolvedRef.current = true;
            await onResolvedRef.current?.(nextJob);
          }
        }
      } catch (err) {
        if (cancelled) {
          return;
        }
        const message = err instanceof Error ? err.message : "Could not load job status";
        consecutiveErrors += 1;
        // A 404 means the job record no longer exists (jobs are in-memory, so a
        // backend restart loses them); persistent errors mean polling is futile.
        // Either way, stop — otherwise this polls a dead job forever with the
        // chat locked.
        if (message === "Job not found" || consecutiveErrors >= 5) {
          stopPolling();
          setError(`${message}. The job's status is no longer available.`);
          onTerminalErrorRef.current?.();
        } else {
          setError(message);
        }
      }
    }

    resolvedRef.current = false;
    void load();
    intervalId = window.setInterval(() => {
      void load();
    }, 2000);

    return () => {
      cancelled = true;
      stopPolling();
    };
  }, [jobId]);

  if (error) {
    return (
      <div className="status-error flex flex-wrap items-center justify-between gap-3">
        <span>{error}</span>
        {onDismiss ? (
          <button className="button-secondary px-3 py-1 text-xs" onClick={onDismiss} type="button">
            Dismiss
          </button>
        ) : null}
      </div>
    );
  }

  if (!job) {
    return <div className="status-chip">Checking ingestion status…</div>;
  }

  // Deletion is a fast, no-model operation — show a slim indicator rather than the
  // full ingestion panel. We still track the job (to block concurrent actions and
  // surface failures); we just don't dress it up as an ingest.
  if (job.operation === "deletion") {
    if (job.status === "failed") {
      return (
        <div className="status-error flex flex-wrap items-center justify-between gap-3">
          <span>Could not remove document{job.error ? ` — ${job.error}` : ""}</span>
          {onDismiss ? (
            <button className="button-secondary px-3 py-1 text-xs" onClick={onDismiss} type="button">
              Dismiss
            </button>
          ) : null}
        </div>
      );
    }
    return <div className="status-chip">Removing document…</div>;
  }

  const safeTotal = Math.max(job.total, 1);
  const percent = Math.round((job.processed / safeTotal) * 100);
  const tone = job.status === "failed" ? "status-chip-error" : job.status === "complete" ? "status-chip-success" : "status-chip";
  const title = job.operation === "rebuild" ? "Rebuilding record" : "Document ingestion";
  const phaseLabels: Record<string, string> = {
    queued: "Queued",
    loading: "Loading patient record",
    extracting_text: "Reading documents",
    extracting_facts: "Extracting clinical facts",
    embedding: "Embedding for search",
    deleting_files: "Removing files",
    removing_chunks: "Removing vectors",
    writing_export: "Updating record export",
    saving: "Saving updates",
    complete: "Ready",
    failed: "Needs Review",
  };

  // Canonical pipeline order for the per-phase breakdown. The per-file sub-phases
  // (reading/facts/embedding) repeat once per document but the backend accumulates
  // them into one entry each, so we render a clean one-line-per-stage timeline.
  const phaseOrder = [
    "queued",
    "loading",
    "extracting_text",
    "extracting_facts",
    "embedding",
    "writing_export",
    "saving",
  ];

  const phaseText = phaseLabels[job.phase ?? ""];
  const runningText = phaseText
    ? `${phaseText}${job.current_file ? ` (${job.current_file})` : ""}`
    : `Processing ${job.current_file ?? "queued files"}`;

  const phaseStartedAtMs = job.phase_started_at ? Date.parse(job.phase_started_at) : NaN;
  const phaseElapsedMs = Number.isFinite(phaseStartedAtMs) ? Math.max(0, Date.now() - phaseStartedAtMs) : 0;
  const totalStartedAtMs = Date.parse(job.started_at);
  const completedAtMs = job.completed_at ? Date.parse(job.completed_at) : NaN;
  const totalElapsedMs = Number.isFinite(totalStartedAtMs)
    ? Math.max(0, (Number.isFinite(completedAtMs) ? completedAtMs : Date.now()) - totalStartedAtMs)
    : 0;

  const phaseWarningThresholdMs: Record<string, number> = {
    loading: 10_000,
    extracting_text: 120_000,
    extracting_facts: 120_000,
    embedding: 60_000,
    writing_export: 15_000,
    saving: 10_000,
  };

  const activeThreshold = job.phase ? phaseWarningThresholdMs[job.phase] : undefined;
  const isPhaseSlow = job.status === "running" && !!activeThreshold && phaseElapsedMs > activeThreshold;

  function formatDuration(ms: number): string {
    const seconds = Math.max(0, Math.floor(ms / 1000));
    const minutes = Math.floor(seconds / 60);
    const remSeconds = seconds % 60;
    return minutes > 0 ? `${minutes}m ${remSeconds}s` : `${remSeconds}s`;
  }

  // Per-phase timeline: start from the accumulated completed-phase totals, then fold
  // the live segment of the currently-running phase into its own row so a stage that
  // recurs across files shows a single growing total — not a duplicate "(running)" row.
  const accumulatedMs = new Map<string, number>();
  for (const entry of job.phase_history ?? []) {
    accumulatedMs.set(entry.phase, (accumulatedMs.get(entry.phase) ?? 0) + entry.elapsed_ms);
  }
  const activePhase = job.status === "running" ? job.phase ?? undefined : undefined;
  if (activePhase) {
    accumulatedMs.set(activePhase, (accumulatedMs.get(activePhase) ?? 0) + phaseElapsedMs);
  }
  // Render known stages in pipeline order, then any unexpected phases that still
  // showed up (so nothing silently disappears from the accounting).
  const orderedPhases = [
    ...phaseOrder.filter((p) => accumulatedMs.has(p)),
    ...[...accumulatedMs.keys()].filter((p) => !phaseOrder.includes(p)),
  ];
  const breakdown = orderedPhases.map((phase) => ({
    phase,
    elapsedMs: accumulatedMs.get(phase) ?? 0,
    active: phase === activePhase,
  }));

  return (
    <div className="rounded-[22px] border border-border/80 bg-surface-elevated p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-text-primary">{title}</p>
          <p className="mt-1 text-sm text-text-secondary">
            {job.status === "running" ? runningText : job.status === "complete" ? "Ready" : "Needs Review"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className={tone}>{job.status === "running" ? "Processing" : job.status === "complete" ? "Ready" : "Needs Review"}</span>
          {job.status === "failed" && onDismiss ? (
            <button className="button-secondary px-3 py-1 text-xs" onClick={onDismiss} type="button">
              Dismiss
            </button>
          ) : null}
        </div>
      </div>

      <div className="mt-4 h-2 overflow-hidden rounded-full bg-background">
        <div className="h-full rounded-full bg-primary transition-all duration-300" style={{ width: `${job.status === "complete" ? 100 : percent}%` }} />
      </div>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-text-secondary">
        <span>
          {job.processed} of {job.total} files processed
        </span>
        {job.status === "running" ? (
          <span>
            Phase elapsed {formatDuration(phaseElapsedMs)} · Total elapsed {formatDuration(totalElapsedMs)}
          </span>
        ) : (
          <span>Total elapsed {formatDuration(totalElapsedMs)}</span>
        )}
        {job.error ? <span className="text-error">{job.error}</span> : null}
      </div>

      {breakdown.length > 0 ? (
        <div className="mt-3 space-y-1 border-t border-border/40 pt-3">
          {breakdown.map((row) => (
            <div key={row.phase} className="flex justify-between text-xs text-text-secondary">
              <span className={row.active ? "text-text-primary" : undefined}>
                {phaseLabels[row.phase] ?? row.phase}
              </span>
              <span className={row.active ? "text-text-primary" : undefined}>
                {formatDuration(row.elapsedMs)}
                {row.active ? " (running)" : ""}
              </span>
            </div>
          ))}
        </div>
      ) : null}

      {isPhaseSlow ? (
        <div className="mt-2 flex items-start gap-1.5 text-xs text-text-secondary">
          <InfoIcon size={13} className="mt-px shrink-0" />
          <span>Running on local hardware, extraction can take a few minutes — longer for large or scanned documents. Still working…</span>
        </div>
      ) : null}
    </div>
  );
}
