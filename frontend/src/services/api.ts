import type {
  WorkshopCreateResponse, WorkshopMemberView, WorkshopHostView,
  Participant, ParticipantWithToken, Question, Answer, GroupRoundResult,
  SynthesisResult, HostInput, KnowledgeDocument, AIQuestion,
  KnowledgeUploadTask, ValidateResponse, ExportResponse,
} from "@/types";

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000/api";

export function getWebSocketUrl(workshopId: number, channel: string) {
  const explicitWsBase = import.meta.env.VITE_WS_BASE_URL;
  const encodedChannel = encodeURIComponent(channel);
  if (explicitWsBase) {
    return `${String(explicitWsBase).replace(/\/$/, "")}/ws/${workshopId}?channel=${encodedChannel}`;
  }

  try {
    const apiUrl = new URL(API_BASE_URL, window.location.origin);
    const protocol = apiUrl.protocol === "https:" ? "wss:" : "ws:";
    const basePath = apiUrl.pathname.replace(/\/api\/?$/, "").replace(/\/$/, "");
    return `${protocol}//${apiUrl.host}${basePath}/ws/${workshopId}?channel=${encodedChannel}`;
  } catch {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${window.location.host}/ws/${workshopId}?channel=${encodedChannel}`;
  }
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${url}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const errorText = await res.text().catch(() => "Unknown error");
    let message = errorText;
    try {
      const parsed = JSON.parse(errorText);
      if (typeof parsed.detail === "string") {
        message = parsed.detail;
      } else if (Array.isArray(parsed.detail)) {
        message = parsed.detail
          .map((item: { msg?: string }) => item.msg ?? JSON.stringify(item))
          .join("; ");
      }
    } catch {
      // Keep raw response text when it is not JSON.
    }
    throw new Error(`API error ${res.status}: ${message}`);
  }
  return res.json() as Promise<T>;
}

// ── Workshop ──────────────────────────────────────────────────────────

export const workshopApi = {
  create: (title: string, host_name: string, group_count = 4) =>
    request<WorkshopCreateResponse>("/workshops", {
      method: "POST",
      body: JSON.stringify({ title, host_name, group_count }),
    }),

  get: (id: number, participant_id?: number, session_token?: string) => {
    const params = participant_id && session_token
      ? `?participant_id=${participant_id}&session_token=${encodeURIComponent(session_token)}`
      : "";
    return request<WorkshopMemberView>(`/workshops/${id}${params}`);
  },

  getHost: (id: number, code: string) =>
    request<WorkshopHostView>(`/workshops/${id}/host?code=${encodeURIComponent(code)}`),

  validateHost: (host_code: string) =>
    request<ValidateResponse>("/workshops/validate-host", {
      method: "POST",
      body: JSON.stringify({ host_code }),
    }),

  validateInvite: (invite_code: string) =>
    request<ValidateResponse>("/workshops/validate-invite", {
      method: "POST",
      body: JSON.stringify({ invite_code }),
    }),

  join: (id: number, name: string, invite_code: string) =>
    request<ParticipantWithToken>(`/workshops/${id}/join`, {
      method: "POST",
      body: JSON.stringify({ name, invite_code }),
    }),

  unlockRound: (id: number, code: string) =>
    request<WorkshopHostView>(`/workshops/${id}/unlock-round?code=${encodeURIComponent(code)}`, {
      method: "POST",
    }),

  previousRound: (id: number, code: string) =>
    request<WorkshopHostView>(`/workshops/${id}/previous-round?code=${encodeURIComponent(code)}`, {
      method: "POST",
    }),

  returnCurrentRound: (id: number, code: string) =>
    request<WorkshopHostView>(`/workshops/${id}/return-current-round?code=${encodeURIComponent(code)}`, {
      method: "POST",
    }),

  updateRoundSettings: (id: number, code: string, discussion_time?: number, input_time?: number) =>
    request<WorkshopHostView>(`/workshops/${id}/round-settings?code=${encodeURIComponent(code)}`, {
      method: "POST",
      body: JSON.stringify({ discussion_time, input_time }),
    }),

  startTimer: (id: number, code: string) =>
    request<WorkshopHostView>(`/workshops/${id}/timer/start?code=${encodeURIComponent(code)}`, {
      method: "POST",
    }),

  submitHostInput: (id: number, code: string, round_id: number, content: string) =>
    request<HostInput>(`/workshops/${id}/host-input?code=${encodeURIComponent(code)}`, {
      method: "POST",
      body: JSON.stringify({ round_id, content }),
    }),

  editGroupResult: (id: number, resultId: number, code: string, edited_content: string) =>
    request<GroupRoundResult>(`/workshops/${id}/group-results/${resultId}?code=${encodeURIComponent(code)}`, {
      method: "PUT",
      body: JSON.stringify({ edited_content }),
    }),

  editSynthesis: (id: number, roundId: number, code: string, edited_content: string) =>
    request<SynthesisResult>(`/workshops/${id}/synthesis/${roundId}?code=${encodeURIComponent(code)}`, {
      method: "PUT",
      body: JSON.stringify({ edited_content }),
    }),

  setGroupLeader: (id: number, groupId: number, host_code: string, new_leader_participant_id: number) =>
    request<WorkshopHostView>(`/workshops/${id}/groups/${groupId}/leader`, {
      method: "PUT",
      body: JSON.stringify({ host_code, new_leader_participant_id }),
    }),

  export: (id: number, code: string) =>
    request<ExportResponse>(`/workshops/${id}/export?code=${encodeURIComponent(code)}`),
};

// ── Group ─────────────────────────────────────────────────────────────

export const groupApi = {
  getQuestions: (groupId: number, workshopId: number, roundId?: number | null) => {
    const roundParam = roundId ? `&round_id=${roundId}` : "";
    return request<Question[]>(`/groups/${groupId}/questions?workshop_id=${workshopId}${roundParam}`);
  },

  submitAnswer: (groupId: number, data: { participant_id: number; session_token: string; question_id: number; content: string }) =>
    request<Answer>(`/groups/${groupId}/answers`, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  getAnswers: (groupId: number, workshopId: number, roundId?: number | null) => {
    const roundParam = roundId ? `&round_id=${roundId}` : "";
    return request<Answer[]>(`/groups/${groupId}/answers?workshop_id=${workshopId}${roundParam}`);
  },

  triggerAI: (groupId: number, workshopId: number, participant_id: number, session_token: string, round_id?: number | null) =>
    request<GroupRoundResult>(`/groups/${groupId}/ai-generate?workshop_id=${workshopId}`, {
      method: "POST",
      body: JSON.stringify({ participant_id, session_token, round_id }),
    }),

  getAIResult: (groupId: number, workshopId: number, roundId?: number | null) => {
    const roundParam = roundId ? `&round_id=${roundId}` : "";
    return request<GroupRoundResult>(`/groups/${groupId}/ai-result?workshop_id=${workshopId}${roundParam}`);
  },

  editAIResult: (
    groupId: number,
    workshopId: number,
    participant_id: number,
    session_token: string,
    edited_content: string,
    round_id?: number | null,
  ) =>
    request<GroupRoundResult>(`/groups/${groupId}/ai-result?workshop_id=${workshopId}`, {
      method: "PUT",
      body: JSON.stringify({ participant_id, session_token, edited_content, round_id }),
    }),

  transferLeader: (
    groupId: number,
    workshop_id: number,
    participant_id: number,
    session_token: string,
    new_leader_participant_id: number,
  ) =>
    request<Participant[]>(`/groups/${groupId}/leader`, {
      method: "PUT",
      body: JSON.stringify({ workshop_id, participant_id, session_token, new_leader_participant_id }),
    }),

  synthesize: (roundId: number) =>
    request<SynthesisResult>(`/rounds/${roundId}/synthesize`, {
      method: "POST",
    }),

  getSynthesis: (roundId: number) =>
    request<SynthesisResult>(`/rounds/${roundId}/synthesis`),
};

// ── Knowledge Base ────────────────────────────────────────────────────

export const knowledgeApi = {
  validateAdmin: (admin_code: string) =>
    request<ValidateResponse>("/knowledge/validate-admin", {
      method: "POST",
      body: JSON.stringify({ admin_code }),
    }),

  upload: (filename: string, content_base64: string, content_type: string, workshop_id: number, admin_code: string) =>
    request<KnowledgeDocument>("/knowledge/upload", {
      method: "POST",
      body: JSON.stringify({ filename, content_base64, content_type, workshop_id, admin_code }),
    }),

  createUploadTask: (filename: string, content_base64: string, content_type: string, workshop_id: number, admin_code: string) =>
    request<KnowledgeUploadTask>("/knowledge/upload-tasks", {
      method: "POST",
      body: JSON.stringify({ filename, content_base64, content_type, workshop_id, admin_code }),
    }),

  getUploadTask: (taskId: string) =>
    request<KnowledgeUploadTask>(`/knowledge/upload-tasks/${taskId}`),

  list: (workshop_id: number, admin_code: string) =>
    request<KnowledgeDocument[]>(`/knowledge/documents?workshop_id=${workshop_id}&admin_code=${encodeURIComponent(admin_code)}`),

  delete: (docId: number, workshop_id: number, admin_code: string) =>
    request<{ status: string }>(`/knowledge/documents/${docId}?workshop_id=${workshop_id}&admin_code=${encodeURIComponent(admin_code)}`, {
      method: "DELETE",
    }),
};

// ── AI QA ─────────────────────────────────────────────────────────────

export const aiApi = {
  ask: (workshopId: number, participant_id: number, question: string) =>
    request<AIQuestion>(`/workshops/${workshopId}/ai-ask`, {
      method: "POST",
      body: JSON.stringify({ participant_id, question }),
    }),

  getHistory: (workshopId: number, participant_id: number) =>
    request<AIQuestion[]>(`/workshops/${workshopId}/ai-questions?participant_id=${participant_id}`),
};
