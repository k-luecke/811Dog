/**
 * App.tsx — TN811 Monitor Dashboard main application component.
 *
 * Renders:
 *   - Header with last-updated time
 *   - KPI summary cards
 *   - Filter bar
 *   - Expiring soon table (highlighted)
 *   - Active tickets table
 *   - County analysis
 *   - Work group analysis
 *   - Recent changes
 *   - Parse failure review
 *
 * All data comes from static JSON — no live portal calls.
 */

import { useState } from "react";
import { CountyAnalysis } from "./components/CountyAnalysis";
import { FilterBar } from "./components/FilterBar";
import { KpiCards } from "./components/KpiCards";
import { ErrorBanner, LoadingSpinner } from "./components/LoadingSpinner";
import { ParseFailures } from "./components/ParseFailures";
import { TicketTable } from "./components/TicketTable";
import { WorkGroupAnalysis } from "./components/WorkGroupAnalysis";
import {
  useActiveTickets,
  useByCounty,
  useByWorkGroup,
  useExpiringTickets,
  useParseFailures,
  useRecentChanges,
  useSummary,
} from "./hooks/useData";
import { filterTickets, timeAgo } from "./lib/utils";
import type { FilterState } from "./types";

const DEFAULT_FILTERS: FilterState = {
  county: "",
  relevanceMin: 0,
  probableCompany: "",
  probableWorkGroup: "",
  status: "",
};

type Tab = "active" | "expiring" | "county" | "groups" | "changes" | "failures";

export default function App() {
  const [filters, setFilters] = useState<FilterState>(DEFAULT_FILTERS);
  const [tab, setTab] = useState<Tab>("expiring");

  const summary = useSummary();
  const active = useActiveTickets();
  const expiring = useExpiringTickets();
  const byCounty = useByCounty();
  const byGroup = useByWorkGroup();
  const changes = useRecentChanges();
  const failures = useParseFailures();

  const allTickets = active.data?.tickets ?? [];
  const filteredActive = filterTickets(allTickets, filters);
  const filteredExpiring = filterTickets(expiring.data?.tickets ?? [], filters);
  const filteredChanges = filterTickets(changes.data?.tickets ?? [], filters);

  const tabs: { id: Tab; label: string; count?: number }[] = [
    { id: "expiring", label: "Expiring Soon", count: expiring.data?.count },
    { id: "active", label: "Active Tickets", count: active.data?.count },
    { id: "county", label: "By County" },
    { id: "groups", label: "Work Groups" },
    { id: "changes", label: "Recent Changes", count: changes.data?.count },
    { id: "failures", label: "Parse Failures", count: failures.data?.count },
  ];

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="border-b border-gray-200 bg-white px-6 py-4 shadow-sm">
        <div className="mx-auto max-w-screen-2xl flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-gray-900">
              TN811 Monitor
            </h1>
            <p className="text-xs text-gray-400">
              GFiber-related excavation ticket tracker — Davidson &amp; Rutherford Counties
            </p>
          </div>
          <div className="text-right">
            {summary.data && (
              <p className="text-xs text-gray-400">
                Last updated: {timeAgo(summary.data.generated_at)}
              </p>
            )}
            <p className="text-xs text-gray-300">Static dashboard — no live portal calls</p>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-screen-2xl space-y-6 px-6 py-6">
        {/* KPI Cards */}
        {summary.loading && <LoadingSpinner label="Loading summary…" />}
        {summary.error && <ErrorBanner error={summary.error} label="Summary" />}
        {summary.data && <KpiCards summary={summary.data} />}

        {/* Filter Bar */}
        <FilterBar
          filters={filters}
          onChange={setFilters}
          tickets={allTickets}
        />

        {/* Tab Navigation */}
        <div className="border-b border-gray-200">
          <nav className="-mb-px flex gap-1 overflow-x-auto">
            {tabs.map((t) => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={[
                  "flex shrink-0 items-center gap-1.5 border-b-2 px-4 py-2.5 text-sm font-medium transition-colors",
                  tab === t.id
                    ? "border-blue-600 text-blue-700"
                    : "border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700",
                ].join(" ")}
              >
                {t.label}
                {t.count !== undefined && t.count > 0 && (
                  <span
                    className={[
                      "rounded-full px-1.5 py-0.5 text-xs font-semibold",
                      t.id === "failures"
                        ? "bg-red-100 text-red-700"
                        : t.id === "expiring"
                        ? "bg-yellow-100 text-yellow-700"
                        : "bg-gray-100 text-gray-600",
                    ].join(" ")}
                  >
                    {t.count}
                  </span>
                )}
              </button>
            ))}
          </nav>
        </div>

        {/* Tab Content */}

        {tab === "expiring" && (
          <>
            {expiring.loading && <LoadingSpinner label="Loading expiring tickets…" />}
            {expiring.error && <ErrorBanner error={expiring.error} label="Expiring tickets" />}
            {expiring.data && (
              <TicketTable
                tickets={filteredExpiring}
                title={`Expiring within ${expiring.data.window_days} days`}
                emptyMessage="No GFiber tickets expiring soon."
                highlightExpiring
              />
            )}
          </>
        )}

        {tab === "active" && (
          <>
            {active.loading && <LoadingSpinner label="Loading active tickets…" />}
            {active.error && <ErrorBanner error={active.error} label="Active tickets" />}
            {active.data && (
              <TicketTable
                tickets={filteredActive}
                title="Active GFiber-Related Tickets"
                emptyMessage="No active GFiber tickets found."
              />
            )}
          </>
        )}

        {tab === "county" && (
          <>
            {byCounty.loading && <LoadingSpinner label="Loading county data…" />}
            {byCounty.error && <ErrorBanner error={byCounty.error} label="County data" />}
            {byCounty.data && <CountyAnalysis counties={byCounty.data.counties} />}
          </>
        )}

        {tab === "groups" && (
          <>
            {byGroup.loading && <LoadingSpinner label="Loading work groups…" />}
            {byGroup.error && <ErrorBanner error={byGroup.error} label="Work groups" />}
            {byGroup.data && <WorkGroupAnalysis groups={byGroup.data.groups} />}
          </>
        )}

        {tab === "changes" && (
          <>
            {changes.loading && <LoadingSpinner label="Loading recent changes…" />}
            {changes.error && <ErrorBanner error={changes.error} label="Recent changes" />}
            {changes.data && (
              <TicketTable
                tickets={filteredChanges}
                title="Recently Changed GFiber Tickets"
                emptyMessage="No recent changes detected."
              />
            )}
          </>
        )}

        {tab === "failures" && (
          <>
            {failures.loading && <LoadingSpinner label="Loading parse failures…" />}
            {failures.error && <ErrorBanner error={failures.error} label="Parse failures" />}
            {failures.data && <ParseFailures failures={failures.data.failures} />}
          </>
        )}
      </main>

      {/* Footer */}
      <footer className="mt-12 border-t border-gray-200 bg-white px-6 py-4 text-center text-xs text-gray-400">
        TN811 Monitor — Internal tool. Not for public distribution.
        Data refreshed daily via GitHub Actions.
      </footer>
    </div>
  );
}
