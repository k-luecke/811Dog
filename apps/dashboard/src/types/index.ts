// types/index.ts — TypeScript interfaces matching backend JSON schemas

export type TicketStatus = "active" | "cancelled" | "expired" | "unknown";

export interface Ticket {
  ticket_number: string;
  county: string;
  state: string;
  call_date: string | null;
  legal_start_date: string | null;
  expiration_date: string | null;
  excavator_name: string | null;
  caller_name: string | null;
  work_type: string | null;
  location_text: string | null;
  remarks: string | null;
  done_for: string | null;
  utility_codes: string[];
  status: TicketStatus;
  is_cancelled: boolean;
  relevance_score: number;
  relevance_reasons: string[];
  is_relevant: boolean;
  probable_company: string | null;
  probable_work_group: string | null;
  probable_crew: string | null;
  parsed_at: string | null;
  updated_at: string | null;
}

export interface Summary {
  generated_at: string;
  total_tickets: number;
  relevant: number;
  active_relevant: number;
  expiring_soon: number;
  cancelled: number;
  expired: number;
  parse_failures_open: number;
}

export interface TicketsResponse {
  generated_at: string;
  count: number;
  tickets: Ticket[];
}

export interface ExpiringResponse extends TicketsResponse {
  window_days: number;
}

export interface CountyStat {
  county: string;
  total: number;
  active: number;
  expiring_soon: number;
}

export interface CountyResponse {
  generated_at: string;
  counties: CountyStat[];
}

export interface WorkGroupStat {
  group: string;
  probable_company: string | null;
  total: number;
  active: number;
  counties: string[];
}

export interface WorkGroupResponse {
  generated_at: string;
  groups: WorkGroupStat[];
}

export interface ParseFailure {
  id: number;
  ticket_number: string | null;
  county: string;
  reason: string;
  detail: string;
  raw_url: string | null;
  raw_path: string | null;
  occurred_at: string | null;
}

export interface ParseFailuresResponse {
  generated_at: string;
  count: number;
  failures: ParseFailure[];
}

export interface ChangesResponse {
  generated_at: string;
  count: number;
  tickets: Ticket[];
}

// Filter state used by the dashboard
export interface FilterState {
  county: string;
  relevanceMin: number;
  probableCompany: string;
  probableWorkGroup: string;
  status: string;
}
