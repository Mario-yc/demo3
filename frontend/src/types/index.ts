export type ParticipantGroup = number;
export type RoundStatus = "locked" | "active" | "input" | "closing" | "completed";
export type GroupResultStatus = "pending" | "processing" | "ready" | "validation_failed" | "edited";

export interface Participant {
  id: number;
  workshop_id: number;
  name: string;
  group_id: ParticipantGroup;
  is_group_leader: boolean;
}

export interface ParticipantWithToken extends Participant {
  session_token: string;
}

export interface Round {
  id: number;
  workshop_id: number;
  round_number: number;
  title: string;
  objective?: string;
  status: RoundStatus;
  discussion_time: number;
  input_time: number;
  timer_started_at: string | null;
  timer_phase: string | null;
  timer_remaining_seconds: number | null;
  questions: Question[];
}

export interface Question {
  id: number;
  round_id: number;
  content: string;
  order: number;
}

export interface Answer {
  id: number;
  question_id: number;
  participant_id: number;
  content: string;
  created_at: string;
  participant_name?: string;
  group_id?: number;
}

export interface GroupRoundResult {
  id: number;
  round_id: number;
  group_id: number;
  status: GroupResultStatus;
  original_content: string | null;
  edited_content: string | null;
  version: number;
  validation_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface SynthesisResult {
  id: number;
  workshop_id: number;
  round_id: number;
  status: GroupResultStatus;
  original_content: string | null;
  edited_content: string | null;
  validation_error: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface Summary {
  id: number;
  round_id: number;
  content: string;
  created_at?: string;
  updated_at?: string;
}

export interface HostInput {
  id: number;
  workshop_id: number;
  round_id: number;
  content: string;
  created_at: string;
  updated_at: string;
}

export interface GroupInfo {
  group_id: number;
  participant_count: number;
  leader_name: string | null;
  members: Participant[];
}

export interface RoundInfo {
  id: number;
  round_number: number;
  title: string;
  objective?: string;
  status: RoundStatus;
  discussion_time: number;
  input_time: number;
  timer_started_at: string | null;
  timer_phase: string | null;
  timer_remaining_seconds: number | null;
  questions: Question[];
  answers: Answer[];
  group_results: GroupRoundResult[];
  synthesis: SynthesisResult | null;
  host_input: HostInput | null;
}

export interface KnowledgeDocument {
  id: number;
  workshop_id: number;
  original_filename: string;
  file_size: number;
  content_type: string;
  chunk_count: number;
  embedding_model: string;
  upload_params: string | null;
  is_deleted: boolean;
  uploaded_at: string;
}

export interface KnowledgeUploadTask {
  task_id: string;
  filename: string;
  status: "pending" | "running" | "success" | "failed";
  stage: string;
  progress: number;
  message: string;
  error: string | null;
  document: KnowledgeDocument | null;
  created_at: string;
  updated_at: string;
}

export interface AIQuestion {
  id: number;
  round_id: number | null;
  question: string;
  answer: string | null;
  created_at: string;
}

export interface WorkshopMemberView {
  id: number;
  title: string;
  host_name: string;
  invite_code: string;
  group_count: number;
  current_round: number;
  flow_round_number: number;
  is_review_mode: boolean;
  status: "active" | "completed";
  created_at: string;
  participant: ParticipantWithToken | null;
  group_members: Participant[];
  rounds: Round[];
}

export interface WorkshopHostView {
  id: number;
  title: string;
  host_name: string;
  invite_code: string;
  host_code: string;
  kb_admin_code: string;
  group_count: number;
  current_round: number;
  flow_round_number: number;
  is_review_mode: boolean;
  status: "active" | "completed";
  created_at: string;
  groups: GroupInfo[];
  rounds: RoundInfo[];
  knowledge_docs: KnowledgeDocument[];
}

export interface WorkshopCreateResponse {
  id: number;
  title: string;
  host_name: string;
  group_count: number;
  invite_code: string;
  host_code: string;
  kb_admin_code: string;
  created_at: string;
}

export interface ValidateResponse {
  valid: boolean;
  workshop_id?: number;
  workshop_title?: string;
}

export interface ExportResponse {
  markdown: string;
  filename: string;
}

export interface WSMessage {
  type: "new_answer" | "round_changed" | "result_ready" | "synthesis_ready" | "timer" | "group_leader_changed" | "workshop_completed" | "ai_result_status";
  data: Record<string, unknown>;
}
