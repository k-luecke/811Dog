/**
 * CountyAnalysis.tsx — Per-county breakdown cards.
 */

import type { CountyStat } from "../types";

interface Props {
  counties: CountyStat[];
}

export function CountyAnalysis({ counties }: Props) {
  const sorted = [...counties].sort((a, b) => b.total - a.total);

  return (
    <section className="rounded-xl border border-gray-200 bg-white shadow-sm">
      <div className="border-b border-gray-100 px-5 py-3">
        <h2 className="text-base font-semibold text-gray-800">County Analysis</h2>
        <p className="text-xs text-gray-400">GFiber-related tickets by county</p>
      </div>
      <div className="divide-y divide-gray-100">
        {sorted.length === 0 && (
          <p className="px-5 py-8 text-center text-sm text-gray-400">No county data.</p>
        )}
        {sorted.map((c) => {
          const pct = c.total > 0 ? Math.round((c.active / c.total) * 100) : 0;
          return (
            <div key={c.county} className="flex items-center gap-4 px-5 py-4">
              <div className="min-w-[160px]">
                <p className="font-medium text-gray-800">{c.county}</p>
              </div>
              <div className="flex flex-1 items-center gap-3">
                <div className="flex-1 overflow-hidden rounded-full bg-gray-100 h-2">
                  <div
                    className="h-full rounded-full bg-blue-500 transition-all"
                    style={{ width: `${pct}%` }}
                  />
                </div>
                <span className="min-w-[40px] text-right text-xs text-gray-500">{pct}% active</span>
              </div>
              <div className="flex gap-6 text-sm">
                <Stat label="Total" value={c.total} />
                <Stat label="Active" value={c.active} accent="green" />
                <Stat label="Expiring" value={c.expiring_soon} accent={c.expiring_soon > 0 ? "yellow" : "gray"} />
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function Stat({ label, value, accent = "gray" }: { label: string; value: number; accent?: string }) {
  const color =
    accent === "green" ? "text-green-700" :
    accent === "yellow" ? "text-yellow-700" :
    "text-gray-700";
  return (
    <div className="text-center">
      <p className={`text-lg font-bold ${color}`}>{value}</p>
      <p className="text-xs text-gray-400">{label}</p>
    </div>
  );
}
