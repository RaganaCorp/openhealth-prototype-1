export type SexAssignedAtBirth = "male" | "female" | "intersex" | "undisclosed";

export type PatientCondition = {
  category: string;
  code: string;
  label: string;
  source: "preset" | "custom";
};

export type SocialHistory = {
  tobacco_status?: "never" | "former" | "current" | null;
  tobacco_details?: string | null;
  alcohol_status?: "never" | "occasional" | "moderate" | "heavy" | null;
  alcohol_drinks_per_week?: number | null;
  drug_status?: "never" | "former" | "current" | null;
  drug_substances?: string | null;
  sexual_activity_status?: "not_active" | "active" | null;
  sexual_partner_genders?: string | null;
  sexual_protection?: "always" | "sometimes" | "never" | null;
};

export type PatientProfileData = {
  dob?: string | null;
  sex_assigned_at_birth?: SexAssignedAtBirth | null;
  gender_identity?: string | null;
  height_cm?: number | null;
  weight_kg?: number | null;
  social_history?: SocialHistory | null;
  conditions?: PatientCondition[];
};

export type PatientIntake = {
  name: string;
  profile?: PatientProfileData;
};

export type Patient = {
  id: string;
  name: string;
  folder_slug: string;
  folder_path: string;
  document_count: number;
  last_ingested_at: string | null;
  created_at: string;
  memory_results_override?: number | null;
  context_window_tokens_override?: number | null;
  profile?: PatientProfileData | null;
};

export type ChatSession = {
  id: string;
  patient_id: string;
  title: string;
  title_auto_generated: boolean;
  created_at: string;
  last_message_at: string | null;
  message_count: number;
};

export type Citation = {
  filename?: string;
  excerpt?: string;
  source_filename?: string;
  title?: string;
  quote?: string;
  note?: string;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  thinking?: string | null;
  timestamp: string;
};

export type ChatLog = {
  session_id: string;
  messages: ChatMessage[];
};

export type PhaseHistoryEntry = {
  phase: string;
  elapsed_ms: number;
};

export type JobStatus = {
  job_id: string;
  patient_id: string;
  status: "running" | "complete" | "failed";
  phase?: string;
  phase_started_at?: string;
  phase_history?: PhaseHistoryEntry[];
  total: number;
  processed: number;
  current_file: string | null;
  started_at: string;
  completed_at: string | null;
  error?: string;
};

export type PatientDocument = {
  id: string;
  patient_id: string;
  filename: string;
  file_path: string;
  extracted_file_path: string;
  date_detected: string;
  document_type: string;
  ingested_at: string;
  mtime: number;
  size: number;
};

export type AppConfig = {
  chat_model: string;
  clinical_model: string;
  verification_model: string;
  embedding_model: string;
  embed_timeout_seconds: number;
  chat_timeout_seconds: number;
  meta_timeout_seconds: number;
  chunk_size: number;
  chunk_overlap: number;
  memory_results: number;
  context_window_tokens: number;
  data_path: string;
  ollama_base_url: string;
  routing_mode: "fast" | "balanced" | "strict";
  medgemma_verification_enabled: boolean;
  grounding_enabled: boolean;
  grounding_max_retries: number;
};

type RequestOptions = RequestInit & {
  isFormData?: boolean;
};

function resolveApiBaseUrl(): string {
  const configured = process.env.NEXT_PUBLIC_API_BASE_URL ?? process.env.NEXT_PUBLIC_API_URL;
  if (configured && configured.trim()) {
    return configured.replace(/\/+$/, "");
  }

  if (typeof window !== "undefined") {
    const host = window.location.hostname;
    if (host === "localhost" || host === "127.0.0.1") {
      // In local Docker runs, bypass the Next.js proxy to avoid long-request proxy resets.
      return `${window.location.protocol}//${host}:8000`;
    }
  }

  return "/api/backend";
}

const API_BASE_URL = resolveApiBaseUrl();

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (!options.isFormData && options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const url = `${API_BASE_URL}${path}`;
  let response: Response;
  try {
    response = await fetch(url, {
      ...options,
      headers,
    });
  } catch (err) {
    console.error("[api] network request failed", {
      url,
      method: options.method ?? "GET",
      error: err instanceof Error ? err.message : String(err),
    });
    throw new Error(`Network error contacting API at ${url}`);
  }

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const text = await response.text();
      try {
        const data = JSON.parse(text);
        detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? data);
      } catch {
        if (text) detail = text;
      }
    } catch {
      // use statusText fallback
    }
    console.error("[api] request failed", {
      url,
      method: options.method ?? "GET",
      status: response.status,
      detail,
    });
    throw new Error(detail || `Request failed: ${response.status}`);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

export function getPatients() {
  return request<Patient[]>("/patients");
}

export function getPatient(patientId: string) {
  return request<Patient>(`/patients/${patientId}`);
}

export function createPatient(intake: PatientIntake) {
  return request<Patient>("/patients", {
    method: "POST",
    body: JSON.stringify(intake),
  });
}

export function patchPatient(
  patientId: string,
  updates: {
    name?: string;
    memory_results_override?: number | null;
    context_window_tokens_override?: number | null;
  },
) {
  return request<Patient>(`/patients/${patientId}`, {
    method: "PATCH",
    body: JSON.stringify(updates),
  });
}

export function updatePatientProfile(patientId: string, profile: PatientProfileData) {
  return request<Patient>(`/patients/${patientId}/profile`, {
    method: "PUT",
    body: JSON.stringify(profile),
  });
}

export function deletePatient(
  patientId: string,
  options: {
    delete_uploads: boolean;
    delete_chats: boolean;
    delete_record_files: boolean;
    delete_vector_data: boolean;
  },
) {
  return request<void>(`/patients/${patientId}`, {
    method: "DELETE",
    body: JSON.stringify(options),
  });
}

export function uploadFiles(patientId: string, files: File[]) {
  const formData = new FormData();
  for (const file of files) {
    formData.append("files", file);
  }
  return request<{ filenames: string[]; job_id: string }>(`/patients/${patientId}/upload`, {
    method: "POST",
    body: formData,
    isFormData: true,
  });
}

export function getActiveJob(patientId: string) {
  return request<JobStatus | null>(`/patients/${patientId}/active-job`);
}

export function getDocuments(patientId: string) {
  return request<PatientDocument[]>(`/documents/${patientId}`);
}

export function deleteDocument(patientId: string, documentId: string) {
  return request<{ job_id: string }>(`/documents/${patientId}/${documentId}`, {
    method: "DELETE",
  });
}

export function getJobStatus(jobId: string) {
  return request<JobStatus>(`/status/${jobId}`);
}

export function getChatSessions(patientId: string) {
  return request<ChatSession[]>(`/patients/${patientId}/chat-sessions`);
}

export function createChatSession(patientId: string, title?: string) {
  return request<{ chat_session_id: string }>(`/patients/${patientId}/chat-sessions`, {
    method: "POST",
    body: JSON.stringify({ title }),
  });
}

export function renameChatSession(patientId: string, sessionId: string, title: string) {
  return request<ChatSession>(`/patients/${patientId}/chat-sessions/${sessionId}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
}

export function deleteChatSession(patientId: string, sessionId: string) {
  return request<void>(`/patients/${patientId}/chat-sessions/${sessionId}`, {
    method: "DELETE",
  });
}

export function getMessages(patientId: string, sessionId: string) {
  return request<ChatLog>(`/chat/messages/${patientId}/${sessionId}`);
}

export function sendChat(patientId: string, chatSessionId: string, message: string) {
  return request<{
    response: string;
    grounding_retried: boolean;
    retry_count: number;
    thinking?: string | null;
    citations: Citation[];
  }>(`/chat`, {
    method: "POST",
    body: JSON.stringify({ patient_id: patientId, chat_session_id: chatSessionId, message }),
  });
}

export function getConfig() {
  return request<AppConfig>("/config");
}

export function updateConfig(updates: Partial<AppConfig>) {
  return request<AppConfig>("/config", {
    method: "POST",
    body: JSON.stringify(updates),
  });
}

export async function getModels() {
  const data = await request<{ models: string[] }>("/models");
  return data.models;
}

export function formatDate(value: string | null | undefined) {
  if (!value) {
    return "Not yet ingested";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}
