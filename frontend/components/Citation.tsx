import type { Citation as CitationType } from "@/lib/api";

export function Citation({ citation }: { citation: CitationType }) {
  const label = citation.filename ?? citation.source_filename ?? citation.title ?? "Source";
  const excerpt = citation.excerpt ?? citation.quote;

  return (
    <div className="rounded-2xl border border-border/80 bg-surface-elevated px-3 py-2 text-xs leading-5 text-text-secondary">
      <p className="font-medium text-text-primary">{label}</p>
      {excerpt ? <p className="mt-1">{excerpt}</p> : null}
      {citation.note ? <p className="mt-1 text-text-muted">{citation.note}</p> : null}
    </div>
  );
}
