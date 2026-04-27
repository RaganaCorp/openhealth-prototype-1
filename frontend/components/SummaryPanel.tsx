"use client";

import DOMPurify from "dompurify";
import { marked } from "marked";
import { useEffect, useState } from "react";

export function SummaryPanel({ summary }: { summary: string }) {
  const [html, setHtml] = useState("");

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
    </div>
  );
}
