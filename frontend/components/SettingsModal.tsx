"use client";

import { useEffect, useState } from "react";

import { getConfig, getModels, updateConfig, type AppConfig } from "@/lib/api";

type SettingsModalProps = {
  open: boolean;
  onClose: () => void;
};

function pickFastModel(installedModels: string[], currentModel: string): string {
  const normalized = installedModels.map((model) => ({
    raw: model,
    lower: model.toLowerCase(),
  }));

  const preferred = [
    /(gemma-4|gemma4).*(e2b|2b).*(q4|q5)/,
    /(gemma-4|gemma4).*(e4b|4b).*(q4|q5)/,
    /(gemma-4|gemma4).*(e2b|2b)/,
    /(gemma-4|gemma4).*(e4b|4b)/,
    /gemma3.*4b.*(q4|q5)/,
    /(gemma).*(2b|3b|4b).*(q4|q5)/,
  ];

  for (const pattern of preferred) {
    const match = normalized.find((entry) => pattern.test(entry.lower));
    if (match) {
      return match.raw;
    }
  }

  return currentModel;
}

function pickMedicalModel(installedModels: string[], currentModel: string): string {
  const normalized = installedModels.map((model) => ({
    raw: model,
    lower: model.toLowerCase(),
  }));

  const preferred = [
    /medgemma.*1\.5.*4b.*(q4|q5)/,
    /medgemma.*4b.*(q4|q5)/,
    /medgemma.*1\.5.*4b/,
    /medgemma.*4b/,
  ];

  for (const pattern of preferred) {
    const match = normalized.find((entry) => pattern.test(entry.lower));
    if (match) {
      return match.raw;
    }
  }

  return currentModel;
}

export function SettingsModal({ open, onClose }: SettingsModalProps) {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }

    let cancelled = false;
    async function load() {
      try {
        setError(null);
        const [loadedConfig, loadedModels] = await Promise.all([getConfig(), getModels()]);
        if (cancelled) {
          return;
        }
        setConfig(loadedConfig);
        setModels(loadedModels);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Could not load settings");
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [open]);

  useEffect(() => {
    if (!open) {
      return;
    }

    function handleKeydown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
      }
    }

    window.addEventListener("keydown", handleKeydown);
    return () => window.removeEventListener("keydown", handleKeydown);
  }, [open, onClose]);

  if (!open) {
    return null;
  }

  return (
    <div className="modal-overlay" onClick={onClose} role="presentation">
      <div className="modal-panel" onClick={(event) => event.stopPropagation()}>
        <div className="flex items-start justify-between gap-4 border-b border-border/80 pb-4">
          <div>
            <p className="eyebrow">Global Settings</p>
            <h2 className="text-2xl font-semibold text-text-primary">Model and ingestion defaults</h2>
          </div>
          <button aria-label="Close settings" className="icon-button" onClick={onClose} type="button">
            ×
          </button>
        </div>

        {error ? <div className="status-error mt-4">{error}</div> : null}
        {!config ? <div className="empty-state mt-4">Loading settings…</div> : null}

        {config ? (
          <form
            className="mt-6 space-y-5"
            onSubmit={async (event) => {
              event.preventDefault();
              try {
                setSaving(true);
                setError(null);
                await updateConfig({
                  chat_model: config.chat_model,
                  clinical_model: config.clinical_model,
                  verification_model: config.verification_model,
                  embedding_model: config.embedding_model,
                  chunk_size: config.chunk_size,
                  chunk_overlap: config.chunk_overlap,
                  memory_results: config.memory_results,
                  grounding_enabled: config.grounding_enabled,
                  grounding_max_retries: config.grounding_max_retries,
                  context_window_tokens: config.context_window_tokens,
                  ollama_base_url: config.ollama_base_url,
                  routing_mode: config.routing_mode,
                  medgemma_verification_enabled: config.medgemma_verification_enabled,
                });
                onClose();
              } catch (err) {
                setError(err instanceof Error ? err.message : "Could not save settings");
              } finally {
                setSaving(false);
              }
            }}
          >
            <div className="rounded-xl border border-border bg-surface-elevated/70 p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="field-label">Laptop preset</p>
                  <p className="mt-1 text-sm text-text-secondary">Uses Gemma for fast drafting, MedGemma for specialist checks, and balanced routing.</p>
                </div>
                <button
                  className="button-secondary"
                  onClick={() => {
                    const fastModel = pickFastModel(models, config.chat_model);
                    const medicalModel = pickMedicalModel(models, config.clinical_model);
                    setConfig({
                      ...config,
                      chat_model: fastModel,
                      clinical_model: medicalModel,
                      verification_model: medicalModel,
                      routing_mode: "balanced",
                      medgemma_verification_enabled: true,
                      chunk_size: 750,
                      chunk_overlap: 80,
                      memory_results: 8,
                      context_window_tokens: 32000,
                      grounding_max_retries: 1,
                    });
                  }}
                  type="button"
                >
                  Apply hybrid preset
                </button>
              </div>
            </div>

            <label className="field-group">
              <span className="field-label">Chat model (fast path)</span>
              <select className="field-input" onChange={(event) => setConfig({ ...config, chat_model: event.target.value })} value={config.chat_model}>
                {models.map((model) => (
                  <option key={model} value={model}>
                    {model}
                  </option>
                ))}
              </select>
            </label>

            <label className="field-group">
              <span className="field-label">Clinical model</span>
              <select className="field-input" onChange={(event) => setConfig({ ...config, clinical_model: event.target.value })} value={config.clinical_model}>
                {models.map((model) => (
                  <option key={model} value={model}>
                    {model}
                  </option>
                ))}
              </select>
            </label>

            <label className="field-group">
              <span className="field-label">Verification model</span>
              <select className="field-input" onChange={(event) => setConfig({ ...config, verification_model: event.target.value })} value={config.verification_model}>
                {models.map((model) => (
                  <option key={model} value={model}>
                    {model}
                  </option>
                ))}
              </select>
            </label>

            <label className="field-group">
              <span className="field-label">Embedding model</span>
              <select
                className="field-input"
                onChange={(event) => setConfig({ ...config, embedding_model: event.target.value })}
                value={config.embedding_model}
              >
                {models.map((model) => (
                  <option key={model} value={model}>
                    {model}
                  </option>
                ))}
              </select>
            </label>

            <div className="grid gap-4 sm:grid-cols-2">
              <label className="field-group">
                <span className="field-label">Chunk size</span>
                <input
                  className="field-input"
                  min={1}
                  onChange={(event) => setConfig({ ...config, chunk_size: Number(event.target.value) })}
                  type="number"
                  value={config.chunk_size}
                />
              </label>
              <label className="field-group">
                <span className="field-label">Chunk overlap</span>
                <input
                  className="field-input"
                  min={0}
                  onChange={(event) => setConfig({ ...config, chunk_overlap: Number(event.target.value) })}
                  type="number"
                  value={config.chunk_overlap}
                />
              </label>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <label className="field-group">
                <span className="field-label">Grounding max retries</span>
                <input
                  className="field-input"
                  min={0}
                  onChange={(event) => setConfig({ ...config, grounding_max_retries: Number(event.target.value) })}
                  type="number"
                  value={config.grounding_max_retries}
                />
              </label>
              <label className="field-group">
                <span className="field-label">Memory results</span>
                <input
                  className="field-input"
                  min={1}
                  onChange={(event) => setConfig({ ...config, memory_results: Number(event.target.value) })}
                  type="number"
                  value={config.memory_results}
                />
              </label>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <label className="field-group">
                <span className="field-label">Routing mode</span>
                <select
                  className="field-input"
                  onChange={(event) =>
                    setConfig({
                      ...config,
                      routing_mode: event.target.value as "fast" | "balanced" | "strict",
                    })
                  }
                  value={config.routing_mode}
                >
                  <option value="fast">fast</option>
                  <option value="balanced">balanced</option>
                  <option value="strict">strict</option>
                </select>
              </label>

              <label className="field-group">
                <span className="field-label">Context window tokens</span>
                <input
                  className="field-input"
                  min={1000}
                  onChange={(event) => setConfig({ ...config, context_window_tokens: Number(event.target.value) })}
                  type="number"
                  value={config.context_window_tokens}
                />
              </label>
            </div>

            <label className="field-group">
              <span className="field-label">Ollama base URL</span>
              <input
                className="field-input"
                onChange={(event) => setConfig({ ...config, ollama_base_url: event.target.value })}
                type="text"
                value={config.ollama_base_url}
              />
            </label>

            <label className="toggle-row">
              <span>
                <span className="field-label block">MedGemma verification enabled</span>
                <span className="mt-1 block text-sm text-text-secondary">When enabled, high-risk or strict-mode turns run specialist verification.</span>
              </span>
              <input
                checked={config.medgemma_verification_enabled}
                className="h-5 w-5 accent-[var(--color-primary)]"
                onChange={(event) => setConfig({ ...config, medgemma_verification_enabled: event.target.checked })}
                type="checkbox"
              />
            </label>

            <label className="toggle-row">
              <span>
                <span className="field-label block">Grounding enabled</span>
                <span className="mt-1 block text-sm text-text-secondary">Verify and refine answers against retrieved patient context.</span>
              </span>
              <input
                checked={config.grounding_enabled}
                className="h-5 w-5 accent-[var(--color-primary)]"
                onChange={(event) => setConfig({ ...config, grounding_enabled: event.target.checked })}
                type="checkbox"
              />
            </label>

            <div className="flex justify-end gap-3 border-t border-border/80 pt-5">
              <button className="button-secondary" onClick={onClose} type="button">
                Cancel
              </button>
              <button className="button-primary" disabled={saving} type="submit">
                {saving ? "Saving…" : "Save settings"}
              </button>
            </div>
          </form>
        ) : null}
      </div>
    </div>
  );
}
