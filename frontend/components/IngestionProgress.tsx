"use client";

import { useEffect, useRef, useState } from "react";

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
    processing_files: "Extracting and embedding files",
    deleting_files: "Removing files",
    removing_chunks: "Removing vectors",
    rebuilding_patient_md: "Rebuilding patient document",
    saving: "Saving updates",
    complete: "Ready",
    failed: "Needs Review",
  };

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
    processing_files: 45_000,
    rebuilding_patient_md: 15_000,
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

      {Object.keys(job.phase_history ?? {}).length > 0 || (job.status === "running" && job.phase) ? (
        <div className="mt-3 space-y-1 border-t border-border/40 pt-3">
          {(job.phase_history ?? []).map((entry, i) => (
            <div key={i} className="flex justify-between text-xs text-text-secondary">
              <span>{phaseLabels[entry.phase] ?? entry.phase}</span>
              <span>{formatDuration(entry.elapsed_ms)}</span>
            </div>
          ))}
          {job.status === "running" && job.phase ? (
            <div className="flex justify-between text-xs text-text-secondary">
              <span>{phaseLabels[job.phase] ?? job.phase}</span>
              <span>{formatDuration(phaseElapsedMs)} (running)</span>
            </div>
          ) : null}
        </div>
      ) : null}

      {isPhaseSlow ? (
        <div className="mt-2 text-xs text-error">
          This step is taking longer than usual. The model may still be working.
        </div>
      ) : null}
    </div>
  );
}
