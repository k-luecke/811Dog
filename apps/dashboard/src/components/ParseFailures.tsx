/**
 * ParseFailures.tsx — Parse failure review section.
 */

import type { ParseFailure } from "../types";
import { formatDate } from "../lib/utils";

interface Props {
  failures: ParseFailure[];
}

const REASON_LABELS: Record<string, string> = {
  pdf_download_failed: "PDF download failed",
  pdf_parse_error: "PDF parse error",
  missing_required_fields: "Missing required fields",
  html_parse_error: "HTML parse error",
  unknown: "Unknown error",
};

export function ParseFailures({ failures }: Props) {
  if (failures.length === 0) {
    return (
      <section className="rounded-xl border border-gray-200 bg-white shadow-sm">
        <div className="border-b border-gray-100 px-5 py-3">
          <h2 className="text-base font-semibold text-gray-800">Parse Failures</h2>
        </div>
        <p className="px-5 py-8 text-center text-sm text-gray-400">
          ✓ No unresolved parse failures.
        </p>
      </section>
    );
  }

  return (
    <section className="rounded-xl border border-red-200 bg-white shadow-sm">
      <div className="border-b border-red-100 bg-red-50 px-5 py-3">
        <h2 className="text-base font-semibold text-red-800">
          Parse Failures — {failures.length} Needing Review
        </h2>
        <p className="text-xs text-red-500">
          These tickets could not be fully parsed. Manual review recommended.
        </p>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead className="bg-gray-50 text-xs font-semibold uppercase tracking-wider text-gray-500">
            <tr>
              <th className="px-4 py-3 text-left">Ticket #</th>
              <th className="px-4 py-3 text-left">County</th>
              <th className="px-4 py-3 text-left">Reason</th>
              <th className="px-4 py-3 text-left">Detail</th>
              <th className="px-4 py-3 text-left">Occurred</th>
              <th className="px-4 py-3 text-left">Raw</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {failures.map((f) => (
              <tr key={f.id} className="hover:bg-red-50">
                <td className="px-4 py-3 font-mono text-xs text-red-700">
                  {f.ticket_number ?? <span className="text-gray-300">unknown</span>}
                </td>
                <td className="px-4 py-3 text-gray-600">{f.county}</td>
                <td className="px-4 py-3">
                  <span className="rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700">
                    {REASON_LABELS[f.reason] ?? f.reason}
                  </span>
                </td>
                <td className="px-4 py-3 text-gray-500 max-w-xs truncate text-xs">
                  {f.detail}
                </td>
                <td className="px-4 py-3 text-gray-400 text-xs">
                  {formatDate(f.occurred_at)}
                </td>
                <td className="px-4 py-3 text-xs">
                  {f.raw_path && (
                    <span className="font-mono text-gray-400 truncate block max-w-[180px]">
                      {f.raw_path}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
