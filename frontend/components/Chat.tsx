"use client";

import DOMPurify from "dompurify";
import { marked } from "marked";
import { useEffect, useRef, useState } from "react";

import { Citation } from "@/components/Citation";
import {
  deleteChatSession,
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

function ThinkingBubble() {
  // In-flight indicator only. The model's reasoning is persisted on the
  // assistant message and rendered as a disclosure beneath it once it arrives.
  return (
    <article className="message-row justify-start">
      <div className="message-bubble message-bubble-assistant">
        <div className="thinking-dots" aria-label="Thinking">
          <span /><span /><span />
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
  const [refinedResponseContent, setRefinedResponseContent] = useState<string | null>(null);
  const [thinking, setThinking] = useState<{ visible: boolean; sessionId: string | null }>({
    visible: false,
    sessionId: null,
  });
  const bottomRef = useRef<HTMLDivElement | null>(null);

  async function loadMessages(sessionId: string) {
    try {
      setLoadingMessages(true);
      setError(null);
      const log = await getMessages(patientId, sessionId);
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
    if (!session) {
      setMessages([]);
      setTitleDraft("New Chat");
      return;
    }
    setTitleDraft(session.title);
    void loadMessages(session.id);
  }, [patientId, session?.id]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  const isDraftSession = !session;
  const sessionTitle = session?.title ?? "New Chat";

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
                  await renameChatSession(patientId, session.id, titleDraft);
                  setEditingTitle(false);
                  if (onSessionChanged) {
                    await onSessionChanged();
                  }
                }}
              >
                <input className="field-input max-w-md" onChange={(event) => setTitleDraft(event.target.value)} value={titleDraft} />
                <button className="button-primary px-4 py-2" type="submit">
                  Save
                </button>
              </form>
            ) : (
              <div className="flex items-center gap-3">
                <h2 className="truncate text-2xl font-semibold text-text-primary">{sessionTitle}</h2>
                {session ? (
                  <button className="button-secondary px-3 py-2 text-xs" onClick={() => setEditingTitle(true)} type="button">
                    Rename
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
          {session ? (
            <button
              className="button-secondary px-3 py-2 text-xs"
              onClick={async () => {
                await deleteChatSession(patientId, session.id);
                if (onSessionChanged) {
                  await onSessionChanged();
                }
              }}
              type="button"
            >
              Delete Chat
            </button>
          ) : null}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto py-5">
        {loadingMessages ? <div className="empty-state">Loading messages...</div> : null}
        {error ? <div className="status-error mb-4">{error}</div> : null}
        {!loadingMessages && messages.filter((m) => !m.id.startsWith("local-") || m.localSessionId === session?.id).length === 0 ? <div className="empty-state">No messages yet. Start with a plain-English question.</div> : null}
        <div className="space-y-5">
          {messages.filter((m) => !m.id.startsWith("local-") || m.localSessionId === session?.id).map((message, index) => {
            const isAssistant = message.role === "assistant";
            const showRefined = Boolean(
              isAssistant && refinedResponseContent && message.content === refinedResponseContent && index === messages.length - 1
            );
            const assistantHtml = isAssistant
              ? DOMPurify.sanitize(marked.parse(message.content) as string)
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
            setRefinedResponseContent(response.grounding_retried ? response.response : null);
            setMessages((current) => current.filter((m) => m.id !== localUserId));
            await loadMessages(targetSessionId);
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
            setError(err instanceof Error ? err.message : "Could not send message");
          } finally {
            setLoading(false);
          }
        }}
      >
        <label className="field-label" htmlFor="chat-input">
          Message
        </label>
        <div className="mt-2 flex gap-3">
          <textarea
            className="field-input min-h-28 flex-1 resize-none"
            disabled={loading || Boolean(activeJobId)}
            id="chat-input"
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.ctrlKey && !event.shiftKey && !event.metaKey) {
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              } else if (event.key === "Enter" && event.ctrlKey) {
                event.preventDefault();
                setDraft((d) => d + "\n");
              }
            }}
            placeholder={activeJobId ? "Wait for ingestion to finish before sending a message." : "What should I understand from the latest lab results?"}
            value={draft}
          />
          <button className="button-primary self-end" disabled={loading || Boolean(activeJobId)} type="submit">
            {loading ? "Sending..." : "Send"}
          </button>
        </div>
      </form>
    </>
  );
}
