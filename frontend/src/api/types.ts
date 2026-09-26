export type Role = "admin" | "supervisor" | "agent";

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
}

export interface AgentProfile {
  domain_codes: string[];
  max_open_tickets: number;
  is_available: boolean;
}

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
  agent_profile: AgentProfile | null;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  user: User;
}

export interface Customer {
  id: string;
  name: string | null;
  phone: string;
  email: string | null;
  city: string | null;
  whatsapp_id: string | null;
  attributes: Record<string, unknown>;
  created_at: string;
}

export interface CustomerBrief {
  id: string;
  name: string | null;
  phone: string;
}

export interface Domain {
  id: string;
  code: string;
  name: string;
  description: string | null;
  keywords: string[];
  is_active: boolean;
}

export type DomainBrief = Pick<Domain, "id" | "code" | "name">;

// ---------- WhatsApp support ----------
export type ConversationStatus = "open" | "resolved" | "escalated" | "closed";

export interface ChatMessage {
  id: string;
  direction: "inbound" | "outbound";
  role: "customer" | "assistant" | "agent" | "system";
  content: string;
  delivery_status: string | null;
  meta: Record<string, unknown>;
  created_at: string;
}

export interface Conversation {
  id: string;
  channel: string;
  status: ConversationStatus;
  customer: CustomerBrief;
  domain: DomainBrief | null;
  unresolved_turns: number;
  summary: string | null;
  last_message_at: string;
  created_at: string;
}

export interface ConversationDetail extends Conversation {
  messages: ChatMessage[];
  ticket_ids: string[];
}

export type TicketStatus = "open" | "in_progress" | "pending_customer" | "resolved" | "closed";
export type TicketPriority = "low" | "medium" | "high" | "urgent";

export interface UserBrief {
  id: string;
  full_name: string;
  email: string;
}

export interface Ticket {
  id: string;
  number: number;
  customer: CustomerBrief;
  domain: DomainBrief | null;
  conversation_id: string | null;
  issue: string;
  ai_summary: string | null;
  priority: TicketPriority;
  status: TicketStatus;
  source: "whatsapp" | "manual";
  assigned_to: UserBrief | null;
  resolved_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface TicketEvent {
  id: string;
  event_type: string;
  data: Record<string, unknown>;
  actor: UserBrief | null;
  created_at: string;
}

export interface TicketDetail extends Ticket {
  events: TicketEvent[];
}

// ---------- Knowledge ----------
export interface KnowledgeDocument {
  id: string;
  title: string;
  domain: DomainBrief;
  source_filename: string | null;
  mime_type: string | null;
  status: "pending" | "processing" | "ready" | "failed";
  error: string | null;
  chunk_count: number;
  created_at: string;
  updated_at: string;
}

export interface SearchHit {
  chunk_id: string;
  document_id: string;
  document_title: string;
  content: string;
  similarity: number;
}

// ---------- Outbound ----------
export type CampaignStatus = "draft" | "scheduled" | "running" | "paused" | "completed" | "cancelled";
export type AnswerType = "text" | "yes_no" | "rating" | "number" | "choice";

export interface Question {
  id?: string;
  order_index?: number;
  key: string;
  text: string;
  answer_type: AnswerType;
  options: string[];
  extraction_hint: string | null;
  is_required: boolean;
}

export interface Campaign {
  id: string;
  name: string;
  description: string | null;
  status: CampaignStatus;
  greeting_script: string;
  closing_script: string;
  language: string;
  start_at: string | null;
  timezone: string;
  call_window_start: string;
  call_window_end: string;
  max_concurrent_calls: number;
  max_attempts: number;
  retry_delay_minutes: number;
  completed_at: string | null;
  created_at: string;
  questions: Question[];
  contact_count?: number;
  completed_count?: number;
}

export interface ImportPreview {
  id: string;
  campaign_id: string;
  filename: string;
  status: "preview" | "confirmed" | "discarded";
  total_rows: number;
  valid_rows: number;
  invalid_rows: number;
  duplicate_rows: number;
  valid_sample: { row: number; name: string | null; phone: string; email: string | null; city: string | null }[];
  invalid_data: { row: number; data: Record<string, unknown>; errors: string[] }[];
  created_at: string;
}

export type ContactStatus = "pending" | "scheduled" | "in_progress" | "completed" | "failed" | "do_not_call";

export interface Contact {
  id: string;
  customer: CustomerBrief;
  status: ContactStatus;
  attempts: number;
  last_attempt_at: string | null;
  variables: Record<string, unknown>;
}

export type CallStatus =
  | "initiated" | "ringing" | "in_progress" | "completed" | "no_answer" | "busy" | "failed" | "canceled";

export interface Call {
  id: string;
  campaign_id: string;
  customer: CustomerBrief;
  attempt_number: number;
  provider: string;
  provider_call_id: string | null;
  status: CallStatus;
  started_at: string | null;
  answered_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
  all_questions_answered: boolean;
  has_recording: boolean;
  error: string | null;
  created_at: string;
}

export interface CallDetail extends Call {
  transcript: { turn_index: number; speaker: "ai" | "customer"; text: string; question_id: string | null; created_at: string }[];
  answers: {
    question_id: string;
    raw_answer: string | null;
    normalized_answer: Record<string, unknown>;
    confidence: number | null;
    status: string;
  }[];
}

export interface QuestionAnalytics {
  question_id: string;
  key: string;
  text: string;
  answer_type: AnswerType;
  responses: number;
  answered: number;
  distribution?: Record<string, number>;
  average?: number | null;
  samples?: string[];
}

export interface CampaignAnalytics {
  contacts: Record<string, number> & { total: number };
  calls: Record<string, number> & { total: number };
  completion_rate: number;
  avg_call_duration_seconds: number | null;
  questions: QuestionAnalytics[];
}

export interface ResultRow {
  contact_id: string;
  customer: CustomerBrief;
  status: ContactStatus;
  attempts: number;
  call_id: string | null;
  answers: Record<string, unknown>;
}

export interface DashboardSummary {
  whatsapp: { conversations_today: number; open_conversations: number; resolved_by_ai_today: number; escalated_today: number };
  tickets: { open: number; unassigned: number; created_today: number; open_by_priority: Record<string, number> };
  outbound: { running_campaigns: number; calls_today: number; completed_calls_today: number; active_calls: number };
}
