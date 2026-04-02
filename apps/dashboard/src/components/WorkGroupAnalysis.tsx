/**
 * WorkGroupAnalysis.tsx — Per-work-group breakdown.
 */

import type { WorkGroupStat } from "../types";

interface Props {
  groups: WorkGroupStat[];
}

export function WorkGroupAnalysis({ groups }: Props) {
  const sorted = [...groups].sort((a, b) => b.active - a.active);
  const maxTotal = Math.max(1, ...sorted.map((g) => g.total));

  return (
    <section className="rounded-xl border border-gray-200 bg-white shadow-sm">
      <div className="border-b border-gray-100 px-5 py-3">
        <h2 className="text-base font-semibold text-gray-800">Inferred Work Groups</h2>
        <p className="text-xs text-gray-400">
          Clustered by company + work type similarity — not hardcoded subcontractors
        </p>
      </div>

      {sorted.length === 0 ? (
        <p className="px-5 py-8 text-center text-sm text-gray-400">No work group data.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 text-xs font-semibold uppercase tracking-wider text-gray-500">
              <tr>
                <th className="px-4 py-3 text-left">Work Group</th>
                <th className="px-4 py-3 text-left">Probable Company</th>
                <th className="px-4 py-3 text-left">Counties</th>
                <th className="px-4 py-3 text-center">Active</th>
                <th className="px-4 py-3 text-center">Total</th>
                <th className="px-4 py-3 text-left">Volume</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {sorted.map((g) => {
                const barPct = Math.round((g.total / maxTotal) * 100);
                return (
                  <tr key={g.group} className="hover:bg-gray-50">
                    <td className="px-4 py-3 font-medium text-gray-800 max-w-[180px] truncate">
                      {g.group}
                    </td>
                    <td className="px-4 py-3 text-gray-500 max-w-[160px] truncate">
                      {g.probable_company ?? <span className="text-gray-300">—</span>}
                    </td>
                    <td className="px-4 py-3 text-gray-500 text-xs">
                      {g.counties.join(", ") || "—"}
                    </td>
                    <td className="px-4 py-3 text-center">
                      <span className="font-semibold text-green-700">{g.active}</span>
                    </td>
                    <td className="px-4 py-3 text-center text-gray-600">{g.total}</td>
                    <td className="px-4 py-3">
                      <div className="w-24 overflow-hidden rounded-full bg-gray-100 h-1.5">
                        <div
                          className="h-full rounded-full bg-blue-400"
                          style={{ width: `${barPct}%` }}
                        />
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
