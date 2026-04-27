# OpenHealth — Frontend Specification

Source of truth for the Next.js frontend. See `spec-backend.md` for API contracts and data shapes. See `spec-docker.md` for how the frontend service is wired into the compose stack.

---

## Stack

| Layer | Technology |
|---|---|
| Framework | Next.js (App Router) |
| Styling | Tailwind CSS v4 |
| Font | Inter via `next/font` (offline — no CDN) |
| Mono font | JetBrains Mono via `next/font` |
| HTTP client | `lib/api.ts` (fetch wrapper around FastAPI at `localhost:8000`) |

---

## File Structure

```
frontend/
├── app/
│   ├── page.tsx                     # Home page
│   ├── patient/[id]/
│   │   ├── page.tsx                 # Patient page
│   │   └── settings/
│   │       └── page.tsx             # Patient Settings page
│   └── layout.tsx                   # Root layout with global header
├── components/
│   ├── Header.tsx                   # Global header with cog → SettingsModal
│   ├── SettingsModal.tsx            # Global settings overlay (config.json fields)
│   ├── UploadArea.tsx               # Drag-and-drop / file picker → POST /patients/{id}/upload
│   ├── Chat.tsx                     # Chat session view (messages + input)
│   ├── Citation.tsx                 # Citation block rendered below assistant messages
│   ├── DeletePatientModal.tsx       # Confirmation modal with per-item checkboxes
│   ├── IngestionProgress.tsx        # Job progress bar; polls /status/{job_id}
│   ├── PatientSettings.tsx          # Patient settings form (rename, overrides, regenerate, delete)
│   ├── SummaryPanel.tsx             # Renders summary markdown string as sanitized HTML
│   └── Timeline.tsx                 # Vertical timeline display (read-only)
├── lib/
│   └── api.ts                       # All API calls to FastAPI backend
├── app/
│   └── app.css                      # Global stylesheet; all CSS custom properties defined here
├── Dockerfile
├── package.json
└── tailwind.config.ts
```

---

## Styling Rules

- **Single source of truth for color**: all theme values are CSS custom properties in `app.css`. Components consume variables (`var(--color-primary)`) — never raw hex values.
- **Tailwind v4**: use for spacing, layout, typography utilities. Color utilities map to the CSS variables defined in `app.css`.
- **No one-off inline styles** for color or theme values during prototyping. Changes to visual direction are made by updating variables in `app.css` only.

---

## Color Tokens

| Token | CSS Variable | Value | Usage |
|---|---|---|---|
| Background | `--color-background` | `#F8F9FA` | Page background |
| Surface | `--color-surface` | `#FFFFFF` | Cards, panels |
| Surface Elevated | `--color-surface-elevated` | `#F1F3F5` | Hover states, sidebars |
| Border | `--color-border` | `#E5E7EB` | Dividers, card outlines |
| Text Primary | `--color-text-primary` | `#1A1D21` | Body text, headings |
| Text Secondary | `--color-text-secondary` | `#6B7280` | Labels, metadata, captions |
| Text Muted | `--color-text-muted` | `#9CA3AF` | Placeholder, disabled |
| Primary | `--color-primary` | `#2E7D6B` | Buttons, active states, links |
| Primary Hover | `--color-primary-hover` | `#245F54` | Button hover |
| Primary Light | `--color-primary-light` | `#E6F2EF` | Highlight backgrounds |
| Success | `--color-success` | `#16A34A` | Completion states |
| Warning | `--color-warning` | `#D97706` | Needs attention |
| Error | `--color-error` | `#DC2626` | Errors, failures |

All values are starting points — adjust only via CSS variables in `app.css`.

Accessibility: WCAG 2.1 AA contrast for all text and interactive controls. Do not use color alone to communicate risk or urgency.

---

## Typography

| Role | Family | Weight | Size |
|---|---|---|---|
| UI base | Inter, system-ui, sans-serif | 400 | 16px |
| Heading H1 | Inter | 700 | 28px |
| Heading H2 | Inter | 600 | 22px |
| Heading H3 | Inter | 600 | 18px |
| Label / Caption | Inter | 500 | 13px |
| Code / mono | JetBrains Mono, ui-monospace, monospace | 400 | 14px |

Line height: 1.6 for body, 1.2 for headings.

---

## Layout

- 12-column desktop grid, 4-column mobile grid.
- Sticky top navigation with patient context.
- Card-based information hierarchy for summary and timeline modules.

---

## Motion and Interaction

- Staggered card reveal on dashboard load (100–180ms offsets)
- Smooth expand/collapse for timeline details
- Gentle highlight transition for newly detected changes
- Respect `prefers-reduced-motion`

---

## Pages

### Home Page (`/`)

Patient list view.

- Shows all patients: name, document count, last ingested date, "Open" button.
- Data sourced from `GET /patients` (reads `patients.json` thin index — no per-patient file loads).
- **"Add Patient" flow**:
  1. Opens a modal — user enters patient name.
  2. On submit: call `POST /patients { name }`. Backend creates folder and index entry. Returns thin patient shape.
  3. Modal transitions to a file upload step (`UploadArea` component).
  4. User can **skip** the upload step — modal closes and patient is accessible from the list.
  5. If files are uploaded: `POST /patients/{id}/upload` is called, returns `job_id`, `IngestionProgress` is shown. On completion, redirect to `/patient/[id]`.

---

### Patient Page (`/patient/[id]`)

Three-column layout:

```
┌────────────────────────────────────────────────────────────────────────────────┐
│  OpenHealth v1                                                            ⚙   │
└────────────────────────────────────────────────────────────────────────────────┘
┌──────────────────┬──────────────────────────────────────┬──────────────────────┐
│  Left Sidebar    │  Main — Chat                         │  Right Sidebar       │
│──────────────────│──────────────────────────────────────│──────────────────────│
│ Patients         │  "Medication review"  [rename]       │  Summary             │
│  ▸ Mom      ⚙   │  ─────────────────────────────────── │  ──────────────────  │
│    Dad           │                                      │  [structured text]   │
│    + Add Patient │  [message bubbles, scrollable]       │                      │
│                  │                                      │  Timeline            │
│ Chats            │                                      │  ──────────────────  │
│  + New Chat      │                                      │  ● 2026-01-15        │
│  ───────────     │                                      │    Discharge         │
│  ▸ Medication    │                                      │  ● 2026-03-04        │
│    review        │                                      │    Lab results       │
│    Follow-up     │                                      │  ● 2026-04-10        │
│    questions     │                                      │    Cardiology appt   │
│                  │  [text input]             [Send]     │                      │
└──────────────────┴──────────────────────────────────────┴──────────────────────┘
```

**Left sidebar**
- Lists all patients; active patient highlighted.
- Settings icon (⚙) next to the active patient name links to `/patient/[id]/settings`.
- Below the patient list: chat session list for the selected patient (`GET /patients/{id}/chat-sessions`).
- "New Chat" button calls `POST /patients/{id}/chat-sessions`, then navigates to the new session.
- "Add Patient" link opens the same Add Patient modal as Home.

**Main area — Chat**
- Displays messages for the active chat session (`GET /chat/messages/{patient_id}/{session_id}`).
- Session title shown at top; click to rename inline → `PATCH /patients/{id}/chat-sessions/{session_id} { title }`.
- Message bubbles: user right-aligned, assistant left-aligned.
- Each assistant message has citations block rendered below it (`Citation.tsx`). Citations always at the bottom of each response, never inline.
- When `grounding_retried: true` in a response, display a subtle "Answer was refined" indicator **attached to that message bubble**.
- Text input fixed at bottom. Send calls `POST /chat`.
- If an ingestion job is active (`GET /patients/{id}/active-job` polling every 2s), show `IngestionProgress` above the chat input.

**Right sidebar**
- `SummaryPanel.tsx`: renders `GET /summary/{patient_id}` markdown string as HTML. **HTML must be sanitized before injection** (DOMPurify or equivalent). Summary uses `##` headers: Overview, Active Conditions, Current Medications, Recent Procedures, Key Concerns.
- `Timeline.tsx`: renders `GET /timeline/{patient_id}` events, sorted oldest-to-newest top-to-bottom. Timeline is **display-only** — events are not clickable.

---

### Patient Settings Page (`/patient/[id]/settings`)

Accessible via the ⚙ icon next to the active patient name in the left sidebar.

Contains:

- **Rename patient** — text field pre-filled with current name. On save: `PATCH /patients/{id} { name }`. Folder slug is not changed.
- **Memory threshold override** — number input for `memory_results_override` (per-patient number of past exchanges retrieved per turn). Blank/empty = use global config.
- **Context window override** — number input for `context_window_tokens_override`. Blank/empty = use global config.
- **Regenerate summary** — button calls `POST /summary/{patient_id}`. Show loading state while running. Display updated summary on completion.
- **Delete patient** — button opens `DeletePatientModal.tsx` (same modal as home page delete flow — checkboxes for uploads, chats, record files, vector data, all defaulted to on).

On save of rename/overrides: calls `PATCH /patients/{id}` with changed fields only.

---

### Global Header

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  OpenHealth v1                                                          ⚙  │
└─────────────────────────────────────────────────────────────────────────────┘
```

Present on every page. Cog icon (⚙) opens `SettingsModal.tsx` as an overlay. No separate settings route.

---

### Settings Modal

Slides in as a right-side panel or centered modal. Reads from `GET /config`; saves via `POST /config`.

Fields:
- Model selector (dropdown from `GET /models`)
- Embedding model selector (dropdown from `GET /models`)
- Chunk size (number input)
- Chunk overlap (number input)
- Grounding enabled (toggle, default on)
- Grounding max retries (number, default 2)
- Context window tokens (number, default 120000)
- Ollama base URL override (text input)

Changes take effect immediately on save. Closes on save, click-outside, or Escape.

---

## Polling Behavior

- **Ingestion progress**: poll `GET /status/{job_id}` every 2 seconds while `status === "running"`.
- **Watcher-triggered jobs**: poll `GET /patients/{id}/active-job` every 2 seconds when no client-initiated `job_id` is held.
- Stop polling when `status === "complete"` or `status === "failed"`.

---

## Upload Status Chips

Use these states for file upload feedback:
- `Processing` — ingestion running
- `Ready` — ingestion complete
- `Needs Review` — ingestion failed or OCR yielded nothing

---

## UX Principles

- Calm tone — no alarmist language.
- Always cite source document for AI claims.
- Clear distinction between facts vs interpretation.
- Plain-English default.
- Show uncertainty explicitly (e.g. "Possible medication change — please confirm").
- Separate "Facts from source" and "AI interpretation" in summary views.
- Require user confirmation for ambiguous or destructive actions (delete modal).

---

## Trust and Safety

- Summary markdown rendered as HTML must be **sanitized before injection** (use DOMPurify or equivalent server-safe sanitizer). Never use `dangerouslySetInnerHTML` with raw unsanitized content.
- Do not use color alone to communicate risk or urgency — pair with text/icon.
- Confidence/uncertainty shown with neutral UI, not alarmist color spikes.
