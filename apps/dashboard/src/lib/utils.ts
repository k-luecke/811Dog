/**
 * utils.ts — Shared utility functions for the dashboard.
 */

import type { FilterState, Ticket } from "../types";

/** Format an ISO date string as a short human-readable date. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
      timeZone: "America/Chicago",
    });
  } catch {
    return iso;
  }
}

/** Return a CSS class based on ticket status. */
export function statusBadgeClass(status: string): string {
  switch (status) {
    case "active":
      return "bg-green-100 text-green-800";
    case "cancelled":
      return "bg-gray-100 text-gray-700";
    case "expired":
      return "bg-red-100 text-red-800";
    default:
      return "bg-yellow-100 text-yellow-800";
  }
}

/** Return a color class for a relevance score bar. */
export function scoreColorClass(score: number): string {
  if (score >= 0.8) return "bg-green-500";
  if (score >= 0.5) return "bg-yellow-400";
  if (score >= 0.3) return "bg-orange-400";
  return "bg-gray-300";
}

/** Return days until expiration (negative = already expired). */
export function daysUntilExpiration(expDate: string | null): number | null {
  if (!expDate) return null;
  const exp = new Date(expDate);
  const now = new Date();
  const diffMs = exp.getTime() - now.getTime();
  return Math.ceil(diffMs / (1000 * 60 * 60 * 24));
}

/** Return a badge class for expiry urgency. */
export function expiryUrgencyClass(days: number | null): string {
  if (days === null) return "text-gray-400";
  if (days < 0) return "text-red-600 font-semibold";
  if (days <= 4) return "text-red-500 font-semibold";
  if (days <= 7) return "text-orange-500 font-medium";
  if (days <= 14) return "text-yellow-600";
  return "text-gray-600";
}

/** Filter tickets based on the current filter state. */
export function filterTickets(tickets: Ticket[], filters: FilterState): Ticket[] {
  return tickets.filter((t) => {
    if (filters.county && !t.county.toLowerCase().includes(filters.county.toLowerCase())) {
      return false;
    }
    if (filters.relevanceMin > 0 && t.relevance_score < filters.relevanceMin) {
      return false;
    }
    if (
      filters.probableCompany &&
      !(t.probable_company ?? "").toLowerCase().includes(filters.probableCompany.toLowerCase())
    ) {
      return false;
    }
    if (
      filters.probableWorkGroup &&
      !(t.probable_work_group ?? "").toLowerCase().includes(filters.probableWorkGroup.toLowerCase())
    ) {
      return false;
    }
    if (filters.status && t.status !== filters.status) {
      return false;
    }
    return true;
  });
}

/** Collect unique values for a field across tickets (for dropdown options). */
export function uniqueValues(tickets: Ticket[], field: keyof Ticket): string[] {
  const seen = new Set<string>();
  for (const t of tickets) {
    const val = t[field];
    if (typeof val === "string" && val) seen.add(val);
  }
  return Array.from(seen).sort();
}

/** Format a timestamp as "last updated X minutes ago". */
export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "unknown";
  const d = new Date(iso);
  const diffMs = Date.now() - d.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  if (diffMins < 1) return "just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d ago`;
}
