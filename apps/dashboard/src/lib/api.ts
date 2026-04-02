/**
 * api.ts — Static JSON data fetching for the TN811 dashboard.
 *
 * All data comes from pre-generated JSON files in /data/exports/.
 * No live API calls, no secrets, no TN811 portal calls at render time.
 *
 * In development: served by Vite from ../../data/exports/ via symlink or proxy.
 * In production:  co-deployed as static files alongside the dashboard dist.
 */

import type {
  ChangesResponse,
  CountyResponse,
  ExpiringResponse,
  ParseFailuresResponse,
  Summary,
  TicketsResponse,
  WorkGroupResponse,
} from "../types";

// Base path for JSON exports. In GitHub Pages this would be the repo root
// or a /data/ path depending on your deployment setup.
const DATA_BASE = import.meta.env.VITE_DATA_BASE ?? "/data/exports";

async function fetchJson<T>(filename: string): Promise<T> {
  const url = `${DATA_BASE}/${filename}`;
  const res = await fetch(url, {
    headers: { Accept: "application/json" },
    // Cache for 5 minutes; the scraper only runs once daily
    cache: "default",
  });
  if (!res.ok) {
    throw new Error(`Failed to load ${filename}: ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  fetchSummary: () => fetchJson<Summary>("summary.json"),
  fetchActiveTickets: () => fetchJson<TicketsResponse>("tickets_active.json"),
  fetchExpiringTickets: () => fetchJson<ExpiringResponse>("tickets_expiring.json"),
  fetchByCounty: () => fetchJson<CountyResponse>("by_county.json"),
  fetchByWorkGroup: () => fetchJson<WorkGroupResponse>("by_work_group.json"),
  fetchRecentChanges: () => fetchJson<ChangesResponse>("recent_changes.json"),
  fetchParseFailures: () => fetchJson<ParseFailuresResponse>("parse_failures.json"),
};
