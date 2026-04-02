/**
 * FilterBar.tsx — Dashboard filter controls.
 */

import type { FilterState, Ticket } from "../types";
import { uniqueValues } from "../lib/utils";

interface Props {
  filters: FilterState;
  onChange: (f: FilterState) => void;
  tickets: Ticket[];
}

export function FilterBar({ filters, onChange, tickets }: Props) {
  const counties = uniqueValues(tickets, "county");
  const companies = uniqueValues(tickets, "probable_company");
  const groups = uniqueValues(tickets, "probable_work_group");

  function update(patch: Partial<FilterState>) {
    onChange({ ...filters, ...patch });
  }

  function reset() {
    onChange({ county: "", relevanceMin: 0, probableCompany: "", probableWorkGroup: "", status: "" });
  }

  const hasFilters =
    filters.county || filters.probableCompany || filters.probableWorkGroup ||
    filters.status || filters.relevanceMin > 0;

  return (
    <div className="flex flex-wrap items-end gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3 shadow-sm">
      {/* County */}
      <label className="flex flex-col gap-1">
        <span className="text-xs font-semibold uppercase tracking-wider text-gray-500">County</span>
        <select
          className="rounded-md border border-gray-200 bg-gray-50 px-3 py-1.5 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-400"
          value={filters.county}
          onChange={(e) => update({ county: e.target.value })}
        >
          <option value="">All counties</option>
          {counties.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
      </label>

      {/* Min relevance */}
      <label className="flex flex-col gap-1">
        <span className="text-xs font-semibold uppercase tracking-wider text-gray-500">
          Min score ({filters.relevanceMin.toFixed(1)})
        </span>
        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={filters.relevanceMin}
          onChange={(e) => update({ relevanceMin: parseFloat(e.target.value) })}
          className="w-28 accent-blue-600"
        />
      </label>

      {/* Probable company */}
      <label className="flex flex-col gap-1">
        <span className="text-xs font-semibold uppercase tracking-wider text-gray-500">Company</span>
        <select
          className="rounded-md border border-gray-200 bg-gray-50 px-3 py-1.5 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-400"
          value={filters.probableCompany}
          onChange={(e) => update({ probableCompany: e.target.value })}
        >
          <option value="">All companies</option>
          {companies.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
      </label>

      {/* Work group */}
      <label className="flex flex-col gap-1">
        <span className="text-xs font-semibold uppercase tracking-wider text-gray-500">Work Group</span>
        <select
          className="rounded-md border border-gray-200 bg-gray-50 px-3 py-1.5 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-400"
          value={filters.probableWorkGroup}
          onChange={(e) => update({ probableWorkGroup: e.target.value })}
        >
          <option value="">All groups</option>
          {groups.map((g) => (
            <option key={g} value={g}>{g}</option>
          ))}
        </select>
      </label>

      {/* Status */}
      <label className="flex flex-col gap-1">
        <span className="text-xs font-semibold uppercase tracking-wider text-gray-500">Status</span>
        <select
          className="rounded-md border border-gray-200 bg-gray-50 px-3 py-1.5 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-400"
          value={filters.status}
          onChange={(e) => update({ status: e.target.value })}
        >
          <option value="">All statuses</option>
          <option value="active">Active</option>
          <option value="cancelled">Cancelled</option>
          <option value="expired">Expired</option>
        </select>
      </label>

      {/* Reset */}
      {hasFilters && (
        <button
          onClick={reset}
          className="mt-auto rounded-md border border-gray-200 bg-white px-3 py-1.5 text-sm text-gray-500 hover:bg-gray-50 hover:text-gray-700"
        >
          Clear filters
        </button>
      )}
    </div>
  );
}
