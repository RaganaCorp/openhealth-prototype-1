"use client";

import { useEffect, useState } from "react";

import { getConfig, getModels, updateConfig, type AppConfig } from "@/lib/api";

type SettingsModalProps = {
  open: boolean;
  onClose: () => void;
};

export function SettingsModal({ open, onClose }: SettingsModalProps) {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }

    async function load() {
      try {
        setError(null);
        const [loadedConfig, loadedModels] = await Promise.all([getConfig(), getModels()]);
        setConfig(loadedConfig);
        setModels(loadedModels);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not load settings");
      }
    }

    void load();
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
                  model: config.model,
                  embedding_model: config.embedding_model,
                  chunk_size: config.chunk_size,
                  chunk_overlap: config.chunk_overlap,
                  grounding_enabled: config.grounding_enabled,
                  grounding_max_retries: config.grounding_max_retries,
                  context_window_tokens: config.context_window_tokens,
                  ollama_base_url: config.ollama_base_url,
                });
                onClose();
              } catch (err) {
                setError(err instanceof Error ? err.message : "Could not save settings");
              } finally {
                setSaving(false);
              }
            }}
          >
            <label className="field-group">
              <span className="field-label">Model</span>
              <select className="field-input" onChange={(event) => setConfig({ ...config, model: event.target.value })} value={config.model}>
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
