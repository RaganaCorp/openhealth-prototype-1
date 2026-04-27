import { type TimelineEvent } from "@/lib/api";

export function Timeline({ events }: { events: TimelineEvent[] }) {
  return (
    <div>
      <div>
        <p className="eyebrow">Timeline</p>
        <h2 className="text-2xl font-semibold text-text-primary">Clinical sequence</h2>
      </div>

      {events.length === 0 ? (
        <div className="empty-state mt-5">Timeline events will appear after source documents are processed.</div>
      ) : (
        <div className="mt-6 space-y-5">
          {events.map((event) => (
            <article className="timeline-item" key={event.id}>
              <div className="timeline-dot" />
              <div className="min-w-0 flex-1">
                <p className="font-mono text-xs uppercase tracking-[0.18em] text-primary">{event.date}</p>
                <h3 className="mt-2 text-base font-semibold text-text-primary">{event.title}</h3>
                <p className="mt-2 text-sm leading-6 text-text-secondary">{event.summary}</p>
                <div className="mt-3 flex flex-wrap gap-2 text-xs text-text-muted">
                  <span className="status-chip">{event.document_type}</span>
                  <span>{event.source_filename}</span>
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
