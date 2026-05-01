"use client";

import DOMPurify from "dompurify";
import { marked } from "marked";
import { useEffect, useState } from "react";

import {
  getSummaryOverrides,
  regenerateSummary,
  updateSummaryOverrides,
  type SummaryOverrides,
} from "@/lib/api";

const EMPTY_OVERRIDES: SummaryOverrides = {
  active_conditions: "",
  current_medications: "",
  recent_procedures: "",
  key_concerns: "",
};

type SummaryPanelProps = {
  patientId: string;
  summary: string;
  onSummaryUpdated?: (summary: string) => void;
};

export function SummaryPanel({ patientId, summary, onSummaryUpdated }: SummaryPanelProps) {
  const [html, setHtml] = useState("");
  const [overrides, setOverrides] = useState<SummaryOverrides>(EMPTY_OVERRIDES);
  const [loadingOverrides, setLoadingOverrides] = useState(true);
  const [saving, setSaving] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function renderMarkdown() {
      if (!summary.trim()) {
        setHtml("");
        return;
      }

      const rendered = await marked.parse(summary);
      setHtml(DOMPurify.sanitize(rendered));
    }

    void renderMarkdown();
  }, [summary]);

  useEffect(() => {
    let cancelled = false;

    async function loadOverrides() {
      try {
        setLoadingOverrides(true);
        setError(null);
        const data = await getSummaryOverrides(patientId);
        if (!cancelled) {
          setOverrides(data);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Could not load summary corrections");
        }
      } finally {
        if (!cancelled) {
          setLoadingOverrides(false);
        }
      }
    }

    void loadOverrides();
    return () => {
      cancelled = true;
    };
  }, [patientId]);

  async function saveOverrides() {
    setSaving(true);
    setStatus(null);
    setError(null);
    try {
      const saved = await updateSummaryOverrides(patientId, overrides);
      setOverrides(saved);
      setStatus("Corrections saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save corrections");
    } finally {
      setSaving(false);
    }
  }

  async function saveAndRegenerate() {
    setRegenerating(true);
    setStatus(null);
    setError(null);
    try {
      const saved = await updateSummaryOverrides(patientId, overrides);
      setOverrides(saved);
      const next = await regenerateSummary(patientId);
      if (onSummaryUpdated) {
        onSummaryUpdated(next.summary);
      }
      setStatus("Corrections saved and summary regenerated.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not regenerate summary");
    } finally {
      setRegenerating(false);
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="eyebrow">Summary</p>
          <h2 className="text-2xl font-semibold text-text-primary">Patient overview</h2>
        </div>
      </div>

      {html ? (
        <div className="markdown-body mt-5" dangerouslySetInnerHTML={{ __html: html }} />
      ) : (
        <div className="empty-state mt-5">Summary will appear after ingestion completes.</div>
      )}

      <div className="mt-6 rounded-[20px] border border-border/80 bg-surface p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="eyebrow">Corrections</p>
            <h3 className="text-lg font-semibold text-text-primary">Section-level summary corrections</h3>
          </div>
        </div>

        {loadingOverrides ? <div className="empty-state mt-4">Loading corrections…</div> : null}

        {!loadingOverrides ? (
          <div className="mt-4 space-y-4">
            <label className="field-group">
              <span className="field-label">Active conditions</span>
              <textarea
                className="field-input min-h-20"
                onChange={(event) => setOverrides({ ...overrides, active_conditions: event.target.value })}
                placeholder="Add clarifications or corrections for active conditions"
                value={overrides.active_conditions}
              />
            </label>

            <label className="field-group">
              <span className="field-label">Current medications</span>
              <textarea
                className="field-input min-h-20"
                onChange={(event) => setOverrides({ ...overrides, current_medications: event.target.value })}
                placeholder="Add clarifications or corrections for medications"
                value={overrides.current_medications}
              />
            </label>

            <label className="field-group">
              <span className="field-label">Recent procedures</span>
              <textarea
                className="field-input min-h-20"
                onChange={(event) => setOverrides({ ...overrides, recent_procedures: event.target.value })}
                placeholder="Add clarifications or corrections for procedures/visits"
                value={overrides.recent_procedures}
              />
            </label>

            <label className="field-group">
              <span className="field-label">Key concerns</span>
              <textarea
                className="field-input min-h-20"
                onChange={(event) => setOverrides({ ...overrides, key_concerns: event.target.value })}
                placeholder="Add clarifications or corrections for key concerns"
                value={overrides.key_concerns}
              />
            </label>

            {error ? <div className="status-error">{error}</div> : null}
            {status ? <div className="status-chip">{status}</div> : null}

            <div className="flex flex-wrap gap-3 pt-1">
              <button className="button-secondary" disabled={saving || regenerating} onClick={() => void saveOverrides()} type="button">
                {saving ? "Saving…" : "Save corrections"}
              </button>
              <button className="button-primary" disabled={saving || regenerating} onClick={() => void saveAndRegenerate()} type="button">
                {regenerating ? "Regenerating…" : "Save + regenerate summary"}
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
