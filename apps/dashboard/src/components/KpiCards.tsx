/**
 * KpiCards.tsx — Summary KPI cards at the top of the dashboard.
 */

import type { Summary } from "../types";

interface Props {
  summary: Summary;
}

interface CardProps {
  label: string;
  value: number | string;
  sub?: string;
  accent?: "green" | "yellow" | "red" | "blue" | "gray";
}

function KpiCard({ label, value, sub, accent = "blue" }: CardProps) {
  const accentMap = {
    green: "border-green-400 bg-green-50",
    yellow: "border-yellow-400 bg-yellow-50",
    red: "border-red-400 bg-red-50",
    blue: "border-blue-400 bg-blue-50",
    gray: "border-gray-300 bg-gray-50",
  };

  const valueMap = {
    green: "text-green-700",
    yellow: "text-yellow-700",
    red: "text-red-700",
    blue: "text-blue-700",
    gray: "text-gray-600",
  };

  return (
    <div className={`rounded-xl border-l-4 p-5 shadow-sm ${accentMap[accent]}`}>
      <p className="text-xs font-semibold uppercase tracking-wider text-gray-500">{label}</p>
      <p className={`mt-1 text-3xl font-bold ${valueMap[accent]}`}>{value}</p>
      {sub && <p className="mt-1 text-xs text-gray-500">{sub}</p>}
    </div>
  );
}

export function KpiCards({ summary }: Props) {
  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-7">
      <KpiCard
        label="Total Tickets"
        value={summary.total_tickets}
        accent="gray"
      />
      <KpiCard
        label="relevant Related"
        value={summary.relevant_related}
        accent="blue"
      />
      <KpiCard
        label="Active relevant"
        value={summary.active_relevant}
        accent="green"
        sub="not expired or cancelled"
      />
      <KpiCard
        label="Expiring Soon"
        value={summary.expiring_soon}
        accent={summary.expiring_soon > 0 ? "yellow" : "gray"}
        sub="within 14 days"
      />
      <KpiCard
        label="Cancelled"
        value={summary.cancelled}
        accent="gray"
      />
      <KpiCard
        label="Expired"
        value={summary.expired}
        accent="gray"
      />
      <KpiCard
        label="Parse Failures"
        value={summary.parse_failures_open}
        accent={summary.parse_failures_open > 0 ? "red" : "gray"}
        sub="needs review"
      />
    </div>
  );
}
