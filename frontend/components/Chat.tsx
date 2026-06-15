"use client";

import DOMPurify from "dompurify";
import { marked } from "marked";
import { useEffect, useRef, useState } from "react";

import { Citation } from "@/components/Citation";
import { PencilIcon, SendIcon } from "@/components/icons";
import {
  getMessages,
  renameChatSession,
  sendChat,
  type ChatMessage,
  type ChatSession,
} from "@/lib/api";

type ChatProps = {
  patientId: string;
  session: ChatSession | null;
  activeJobId: string | null;
  onCreateSession?: () => Promise<string>;
  onSessionChanged?: () => void | Promise<void>;
};

type LocalChatMessage = ChatMessage & {
  localStatus?: "pending" | "failed";
  localSessionId?: string;
};

// The local model can take 10–60s and the backend pipeline (retrieve → draft →
// ground-check) has no real-time progress stream, so a bare spinner reads as a
// hang. Instead we surface the actual sequential stages on an approximate timer
// plus a live elapsed counter, so the wait feels accountable. We intentionally do
// NOT stream tokens: the grounding gate can rewrite the answer after generation,
// and showing an unverified draft that later changes is unsafe for a medical app.
const THINKING_STAGES = ["Reading the record", "Working through your question", "Checking it against the sources"];

function ThinkingBubble() {
  const [elapsed, setElapsed] = useState(0);
  const [stage, setStage] = useState(0);

  useEffect(() => {
    const startedAt = Date.now();
    const tick = window.setInterval(() => setElapsed(Math.floor((Date.now() - startedAt) / 1000)), 1000);
    // Advance the stage label on a gentle timer; cap at the last stage so it
    // never loops or implies more progress than the model has actually made.
    const advance = window.setInterval(() => setStage((s) => Math.min(s + 1, THINKING_STAGES.length - 1)), 7000);
    return () => {
      window.clearInterval(tick);
      window.clearInterval(advance);
    };
  }, []);

  return (
    <article className="message-row justify-start">
      <div className="message-bubble message-bubble-assistant">
        <div className="flex items-center gap-3">
          <div className="thinking-dots" aria-label="Thinking">
            <span /><span /><span />
          </div>
          <span className="text-xs text-text-secondary" aria-live="polite">
            {THINKING_STAGES[stage]}
            {elapsed >= 3 ? ` · ${elapsed}s` : ""}
          </span>
        </div>
      </div>
    </article>
  );
}

export function Chat({ patientId, session, activeJobId, onCreateSession, onSessionChanged }: ChatProps) {
  const [messages, setMessages] = useState<LocalChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  // Track which session the refined answer belongs to, so the "Answer was
  // refined" badge can't leak onto an identically-worded message in another
  // session after the user switches views.
  const [refinedResponse, setRefinedResponse] = useState<{ sessionId: string; content: string } | null>(null);
  const [thinking, setThinking] = useState<{ visible: boolean; sessionId: string | null }>({
    visible: false,
    sessionId: null,
  });
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  // Tracks the session currently in view so a slow response (message load or a
  // long-running send) can't overwrite the view after the user switched sessions.
  const currentSessionIdRef = useRef<string | null>(session?.id ?? null);

  async function loadMessages(sessionId: string) {
    try {
      setLoadingMessages(true);
      setError(null);
      const log = await getMessages(patientId, sessionId);
      if (currentSessionIdRef.current !== sessionId) {
        // The user moved to another session while this request was in flight —
        // applying it would render the wrong conversation under this view.
        return;
      }
      setMessages((current) => {
        // Keep ALL in-flight local messages across all sessions so that
        // navigating away and back never drops a pending/failed message.
        // Render-time filtering (below) ensures they only show in their
        // own session's view.
        const allLocal = current.filter(
          (m) =>
            m.id.startsWith("local-") &&
            (m.localStatus === "failed" || m.localStatus === "pending")
        );
        return [...log.messages, ...allLocal];
      });
    } catch (err) {
      console.error("[ui] failed to load chat messages", {
        patientId,
        sessionId,
        error: err instanceof Error ? err.message : err,
      });
      setError(err instanceof Error ? err.message : "Could not load messages");
    } finally {
      setLoadingMessages(false);
    }
  }

  useEffect(() => {
    currentSessionIdRef.current = session?.id ?? null;
    // Clear per-session UI affordances so they never carry over to the next chat.
    setEditingTitle(false);
    if (!session) {
      // Clear server messages but keep in-flight/failed local messages — they
      // belong to their own sessions and are filtered at render time.
      setMessages((current) =>
        current.filter(
          (m) =>
            m.id.startsWith("local-") &&
            (m.localStatus === "failed" || m.localStatus === "pending")
        )
      );
      setTitleDraft("New Chat");
      return;
    }
    setTitleDraft(session.title);
    void loadMessages(session.id);
  }, [patientId, session?.id]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  // Auto-grow the composer from a single line up to a cap, then it scrolls.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) {
      return;
    }
    el.style.height = "auto";
    const capped = el.scrollHeight > 200;
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
    // Only show the scrollbar once the composer hits its max height; otherwise a
    // sub-pixel overflow leaves a tiny scrollbar that barely scrolls.
    el.style.overflowY = capped ? "auto" : "hidden";
  }, [draft]);

  const isDraftSession = !session;
  const sessionTitle = session?.title ?? "New Chat";
  // Local (in-flight/failed) messages show only in their own session's view.
  // In a draft chat (no session yet) they're keyed by the "__pending__" marker.
  const visibleMessages = messages.filter(
    (m) => !m.id.startsWith("local-") || m.localSessionId === (session?.id ?? "__pending__")
  );

  return (
    <>
      <div className="border-b border-border/80 pb-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0 flex-1">
            {editingTitle && session ? (
              <form
                className="flex gap-2"
                onSubmit={async (event) => {
                  event.preventDefault();
                  try {
                    setError(null);
                    await renameChatSession(patientId, session.id, titleDraft);
                    setEditingTitle(false);
                    if (onSessionChanged) {
                      await onSessionChanged();
                    }
                  } catch (err) {
                    setError(err instanceof Error ? err.message : "Could not rename chat");
                  }
                }}
              >
                <input
                  autoFocus
                  className="field-input max-w-md"
                  onChange={(event) => setTitleDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Escape") {
                      event.preventDefault();
                      setTitleDraft(session.title);
                      setEditingTitle(false);
                    }
                  }}
                  value={titleDraft}
                />
                <button className="button-primary px-4 py-2" type="submit">
                  Save
                </button>
                <button
                  className="button-secondary px-4 py-2"
                  onClick={() => {
                    setTitleDraft(session.title);
                    setEditingTitle(false);
                  }}
                  type="button"
                >
                  Cancel
                </button>
              </form>
            ) : (
              <div className="group flex items-center gap-2">
                <h2 className="truncate text-2xl font-semibold text-text-primary">{sessionTitle}</h2>
                {session && !session.title_auto_generated ? (
                  <button
                    aria-label="Rename chat"
                    className="shrink-0 rounded-md p-1.5 text-text-muted opacity-0 transition-opacity hover:text-primary focus-visible:opacity-100 group-hover:opacity-100"
                    onClick={() => setEditingTitle(true)}
                    type="button"
                  >
                    <PencilIcon size={16} />
                  </button>
                ) : null}
              </div>
            )}
            <p className="mt-2 text-sm text-text-secondary">
              {isDraftSession
                ? "This chat will be saved only after you send your first message."
                : "Ask grounded questions about the uploaded record. Citations stay attached to each assistant response."}
            </p>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto py-5">
        {loadingMessages ? <div className="empty-state">Loading messages...</div> : null}
        {error ? <div className="status-error mb-4">{error}</div> : null}
        {!loadingMessages && visibleMessages.length === 0 ? <div className="empty-state">No messages yet. Start with a plain-English question.</div> : null}
        <div className="space-y-5">
          {visibleMessages.map((message, index) => {
            const isAssistant = message.role === "assistant";
            const showRefined = Boolean(
              isAssistant &&
                refinedResponse &&
                refinedResponse.sessionId === (session?.id ?? null) &&
                message.content === refinedResponse.content &&
                index === visibleMessages.length - 1
            );
            const assistantHtml = isAssistant
              ? DOMPurify.sanitize(marked.parse(message.content, { async: false }))
              : "";
            return (
              <article className={`message-row ${isAssistant ? "justify-start" : "justify-end"}`} key={message.id}>
                <div className={`message-bubble ${isAssistant ? "message-bubble-assistant" : "message-bubble-user"}`}>
                  {isAssistant ? (
                    <div className="markdown-body text-sm leading-7" dangerouslySetInnerHTML={{ __html: assistantHtml }} />
                  ) : (
                    <p className="whitespace-pre-wrap text-sm leading-7">{message.content}</p>
                  )}
                  {isAssistant && message.thinking ? (
                    <details className="thinking-disclosure mt-3">
                      <summary>Thinking…</summary>
                      <div className="thinking-disclosure-body">{message.thinking}</div>
                    </details>
                  ) : null}
                  {!isAssistant && message.localStatus === "failed" ? (
                    <div className="mt-3 text-xs text-error">Failed to send. Please retry.</div>
                  ) : null}
                  {showRefined ? <div className="mt-4 inline-flex rounded-full bg-primary-light px-3 py-1 text-xs font-medium text-primary">Answer was refined</div> : null}
                  {isAssistant && message.citations.length > 0 ? (
                    <div className="mt-4 space-y-2 border-t border-border/70 pt-4">
                      {message.citations.map((citation, citationIndex) => (
                        <Citation citation={citation} key={`${message.id}-${citationIndex}`} />
                      ))}
                    </div>
                  ) : null}
                </div>
              </article>
            );
          })}
          {thinking.visible && thinking.sessionId === (session?.id ?? null) ? <ThinkingBubble /> : null}
          <div ref={bottomRef} />
        </div>
      </div>

      <form
        className="border-t border-border/80 pt-4"
        onSubmit={async (event) => {
          event.preventDefault();
          const message = draft.trim();
          if (!message || loading || activeJobId || (!session && !onCreateSession)) {
            return;
          }

          const localUserId = `local-${Date.now()}`;

          try {
            setLoading(true);
            setError(null);
            setDraft("");
            // Persist the user message immediately — before any async work —
            // so it survives session creation, effect-triggered reloads, etc.
            // We may not have a session ID yet (new chat), so we'll update
            // localSessionId once we get one.
            setMessages((current) => [
              ...current,
              {
                id: localUserId,
                role: "user",
                content: message,
                citations: [],
                timestamp: new Date().toISOString(),
                localStatus: "pending",
                localSessionId: session?.id ?? "__pending__",
              },
            ]);
            let targetSessionId = session?.id ?? null;
            if (!targetSessionId && onCreateSession) {
              targetSessionId = await onCreateSession();
            }
            if (!targetSessionId) {
              throw new Error("Could not start chat session");
            }
            // Stamp the local message with the now-resolved session ID.
            setMessages((current) =>
              current.map((m) =>
                m.id === localUserId ? { ...m, localSessionId: targetSessionId! } : m
              )
            );
            setThinking({ visible: true, sessionId: targetSessionId });
            const response = await sendChat(patientId, targetSessionId, message);
            setThinking({ visible: false, sessionId: null });
            setRefinedResponse(
              response.grounding_retried ? { sessionId: targetSessionId, content: response.response } : null
            );
            // Reconcile in place rather than refetching the whole log: confirm the
            // optimistic user bubble and append the reply we already received. Both
            // keep local- ids so a later loadMessages (on a real session switch)
            // cleanly swaps in the server records — but there's no flash and no
            // serial refetch storm on the happy path.
            const assistantTimestamp = new Date().toISOString();
            setMessages((current) => [
              ...current.map((m) => (m.id === localUserId ? { ...m, localStatus: undefined } : m)),
              {
                id: `local-asst-${Date.now()}`,
                role: "assistant",
                content: response.response,
                citations: response.citations ?? [],
                thinking: response.thinking ?? null,
                timestamp: assistantTimestamp,
                localSessionId: targetSessionId!,
              },
            ]);
            if (onSessionChanged) {
              await onSessionChanged();
            }
          } catch (err) {
            console.error("[ui] chat send failed", {
              patientId,
              sessionId: session?.id ?? null,
              messageLen: message.length,
              error: err instanceof Error ? err.message : err,
            });
             console.error("[ui] raw error:", err);
            setThinking({ visible: false, sessionId: null });
            setMessages((current) =>
              current.map((m) =>
                m.id === localUserId
                  ? { ...m, localStatus: "failed" }
                  : m
              )
            );
            // Restore the message text so it can be edited and re-sent (unless
            // the user has already started typing something new).
            setDraft((current) => (current ? current : message));
            setError(err instanceof Error ? err.message : "Could not send message");
          } finally {
            setLoading(false);
          }
        }}
      >
        <div className="flex items-end gap-3">
          <textarea
            aria-label="Message"
            className="field-input max-h-[200px] flex-1 resize-none overflow-y-hidden"
            disabled={loading}
            id="chat-input"
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              // Enter sends; Shift+Enter inserts a newline (the native default).
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              }
            }}
            placeholder={activeJobId ? "Waiting for ingestion to finish before you can send…" : "Ask a plain-English question about the record…"}
            ref={textareaRef}
            rows={1}
            value={draft}
          />
          <button
            aria-label="Send message"
            className="button-primary self-end"
            disabled={loading || Boolean(activeJobId) || !draft.trim()}
            type="submit"
          >
            <SendIcon size={16} />
            {loading ? "Sending…" : "Send"}
          </button>
        </div>
      </form>
    </>
  );
}
