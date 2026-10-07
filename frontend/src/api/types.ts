// super_admin = platform owner (registers companies); the others belong to one company.
export type Role = "super_admin" | "admin" | "supervisor" | "agent";

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
  company: { id: string; name: string } | null; // null for the platform super admin
  agent_profile: AgentProfile | null;
}

// ---------- Companies (tenants) ----------
export interface CompanyUsage {
  campaigns: number;
  max_campaigns: number | null; // null = unlimited
  calls_today: number;
  daily_call_limit: number | null;
  active_calls: number;
  max_concurrent_calls: number | null;
  users: number;
  inbound_calls_today: number;
}

export type TelephonyProvider = "twilio" | "exotel";

export interface Company {
  id: string;
  name: string;
  email: string | null;
  phone: string | null;
  website: string | null;
  address: string | null;
  industry: string | null;
  description: string | null;
  products_services: string | null;
  timezone: string;
  is_active: boolean;
  max_campaigns: number | null;
  daily_call_limit: number | null;
  max_concurrent_calls: number | null;
  telephony_provider: TelephonyProvider; // the one calling provider this company uses
  twilio_account_sid: string | null;
  twilio_from_number: string | null;
  twilio_auth_token_set: boolean;
  exotel_api_key: string | null;
  exotel_account_sid: string | null;
  exotel_subdomain: string | null;
  exotel_number: string | null;
  exotel_app_id: string | null;
  exotel_api_token_set: boolean;
  exotel_stream_url: string | null; // for the Voicebot applet of the company's Exotel flow
  exotel_passthru_url: string | null; // for the Passthru applet after it
  whatsapp_phone_number_id: string | null;
  whatsapp_access_token_set: boolean;
  whatsapp_app_secret_set: boolean;
  calls_connected: boolean; // the company has connected its own Twilio account and number
  whatsapp_connected: boolean;
  inbound_allowed: boolean; // set by the platform admin
  inbound_enabled: boolean; // set by the company
  inbound_active: boolean; // calls to the company's number are answered right now
  inbound_problem: string | null; // why inbound cannot be switched on (null = it can)
  inbound_language: string;
  inbound_languages: string[]; // the languages a call may follow the caller into
  inbound_greeting: string | null;
  whatsapp_webhook_url: string;
  email_enabled: boolean; // the company's on/off switch for email
  email_address: string | null;
  email_from_name: string | null;
  sendgrid_api_key_set: boolean;
  email_connected: boolean; // support address and SendGrid key are saved
  email_active: boolean; // emails are answered and sent right now
  email_problem: string | null; // why no email is sent (null = it is)
  email_webhook_url: string | null; // for SendGrid Inbound Parse
  billing_hold: boolean; // campaigns and callbacks are paused for an overdue invoice
  usage: CompanyUsage | null;
  created_at: string;
}

export interface Credentials {
  email: string;
  password: string;
}

export interface PlatformSummary {
  companies: number;
  active_companies: number;
  users: number;
  campaigns: number;
  running_campaigns: number;
  active_calls: number;
  calls_last_24h: number;
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
  phone: string | null; // null for someone who has only written by email
  email: string | null;
  city: string | null;
  whatsapp_id: string | null;
  attributes: Record<string, unknown>;
  created_at: string;
}

export interface CustomerBrief {
  id: string;
  name: string | null;
  phone: string | null;
  email?: string | null;
}

/** Name, else phone, else email: how a customer is shown in a list. */
export const customerLabel = (c: CustomerBrief) => c.name ?? c.phone ?? c.email ?? "Unknown";
/** The customer's phone number, or their email address if they have only written by email. */
export const customerContact = (c: CustomerBrief) => c.phone ?? c.email ?? "—";

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

export type CallOutcome = "answered" | "ticket_raised" | "ticket_updated" | "abandoned" | "callback_booked";

/** The inbound phone call behind a conversation whose channel is "phone". */
export interface SupportCall {
  id: string;
  from_number: string | null; // null = the caller hid their number
  status: string;
  outcome: CallOutcome | null;
  language: string | null;
  ticket_id: string | null;
  started_at: string;
  ended_at: string | null;
  duration_seconds: number | null;
  has_recording: boolean;
  cost?: CallCost | null;
}

/** A row of the Inbound Calls page: the call, who called and the ticket it led to. */
export interface InboundCall extends SupportCall {
  conversation_id: string;
  customer: CustomerBrief;
  ticket_number: number | null;
  is_callback: boolean; // a call we placed because the customer asked to be called back
}

export type CallbackStatus = "scheduled" | "calling" | "completed" | "failed" | "cancelled";

/** A call the customer asked for ("call me at 5 pm"). */
export interface Callback {
  id: string;
  customer: CustomerBrief;
  phone: string;
  due_at: string;
  reason: string | null;
  language: string | null;
  status: CallbackStatus;
  attempts: number;
  error: string | null;
  source_conversation_id: string | null;
  conversation_id: string | null; // of the call that was placed
  created_at: string;
}

/** A question the assistant could not answer from the knowledge base. */
export interface KnowledgeGap {
  id: string;
  question: string;
  channel: string;
  language: string | null;
  reason: "no_match" | "cannot_answer";
  status: "open" | "resolved";
  domain: DomainBrief | null;
  conversation_id: string | null;
  created_at: string;
  resolved_at: string | null;
}

// ---------- Billing ----------
/** What a company pays, set by the platform admin. Calls are charged per started minute. */
export interface BillingPlan {
  enabled: boolean;
  currency: string;
  monthly_fee: number;
  outbound_per_minute: number; // campaign calls
  inbound_per_minute: number; // inbound calls and callbacks
  whatsapp_per_message: number; // replies by the assistant; 0 = free
  email_per_message: number;
  tax_percent: number;
  tax_label: string;
  billing_email: string | null;
  tax_id: string | null;
}

export interface Invoice {
  id: string;
  company_id: string;
  company_name: string | null;
  number: number;
  label: string; // "INV-1001"
  kind: "monthly" | "manual";
  period_start: string;
  period_end: string;
  status: "open" | "paid" | "void";
  overdue_days: number;
  currency: string;
  subtotal: number;
  tax_percent: number;
  tax_label: string;
  tax_amount: number;
  total: number;
  issued_at: string;
  due_date: string;
  paid_at: string | null;
  paid_via: string | null;
  note: string | null;
  hosted_url: string | null; // Stripe's page where the company pays
  pdf_url: string | null;
  stripe_error: string | null; // platform admin only
  lines: { kind: string; description: string; quantity: number; unit_price: number; amount: number }[];
}

export interface BillingSummary {
  company: { id: string; name: string };
  billing_on: boolean;
  plan: BillingPlan | null;
  period: { from: string; to: string };
  next_invoice_date: string;
  this_period: {
    usage: { kind: string; label: string; count: number; quantity: number; amount: number; seconds: number }[];
    calls: number; call_minutes: number; call_amount: number;
    average_cost_per_call: number | null; average_minutes_per_call: number | null;
    usage_amount: number; monthly_fee: number; tax_amount: number; estimated_total: number;
  };
  amount_due: number;
  on_hold: boolean; // campaigns and callbacks are paused for an overdue invoice
  stripe_connected: boolean;
  due_days: number;
  grace_days: number;
  invoices: Invoice[];
}

export interface PlatformBilling {
  billing_enabled: boolean;
  stripe_connected: boolean;
  stripe_webhook_url: string;
  stripe_webhook_ready: boolean;
  billing_day: number;
  companies: {
    company_id: string; name: string; is_active: boolean; billing_on: boolean; currency: string | null;
    outbound_per_minute: number | null; inbound_per_minute: number | null;
    calls: number; call_minutes: number; average_cost_per_call: number | null;
    estimated_total: number; amount_due: number; amount_paid: number; on_hold: boolean;
  }[];
  totals: { due: number; paid: number; this_period: number };
}

/** What one call cost the company (shown to company admins only). */
export interface CallCost { amount: number; minutes: number; per_minute: number }

export interface ReportOverview {
  days: number;
  from: string;
  to: string;
  timezone: string;
  totals: {
    tickets_created: number; tickets_resolved: number; tickets_open: number; avg_resolution_hours: number | null;
    conversations: number; conversations_finished: number; resolved_by_ai: number; ai_resolved_rate: number | null;
    inbound_calls: number; campaign_calls: number; campaign_calls_completed: number;
    callbacks_booked: number; callbacks_completed: number; open_gaps: number;
  };
  daily: { date: string; tickets_created: number; tickets_resolved: number; conversations: number; inbound_calls: number }[];
  channels: { channel: string; conversations: number; finished: number; resolved_by_ai: number; tickets: number }[];
  call_outcomes: { outcome: string; count: number }[];
  agents: { name: string; resolved: number }[];
}

export interface ConversationDetail extends Conversation {
  messages: ChatMessage[];
  ticket_ids: string[];
  call: SupportCall | null;
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
  source: "whatsapp" | "phone" | "email" | "manual";
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
export type OfferingType = "product" | "service" | "general";

export interface Question {
  id?: string;
  order_index?: number;
  key: string;
  text: string;
  answer_type: AnswerType;
  options: string[];
  extraction_hint: string | null;
  is_required: boolean;
  translations: Record<string, string>; // {"hi-IN": "..."}
}

export interface Campaign {
  id: string;
  name: string;
  description: string | null;
  offering_type: OfferingType;
  offering_name: string | null;
  offering_details: string | null;
  status: CampaignStatus;
  greeting_script: string;
  closing_script: string;
  language: string;
  alt_languages: string[];
  conversational: boolean;
  agent_prompt: string | null;
  translations: Record<string, { greeting?: string; closing?: string }>;
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
  language: string | null;
  started_at: string | null;
  answered_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
  all_questions_answered: boolean;
  has_recording: boolean;
  error: string | null;
  created_at: string;
  cost?: CallCost | null;
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
  inbound: { active: boolean; calls_today: number; answered_by_ai_today: number; tickets_today: number; active_calls: number };
  usage: CompanyUsage;
}
