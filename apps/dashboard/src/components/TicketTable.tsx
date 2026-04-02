/**
 * TicketTable.tsx — Reusable sortable ticket table component.
 */

import { useState } from "react";
import type { Ticket } from "../types";
import {
  daysUntilExpiration,
  expiryUrgencyClass,
  formatDate,
  scoreColorClass,
  statusBadgeClass,
} from "../lib/utils";

interface Props {
  tickets: Ticket[];
  title?: string;
  emptyMessage?: string;
  showRelevance?: boolean;
  showGroup?: boolean;
  highlightExpiring?: boolean;
}

type SortKey = "ticket_number" | "county" | "expiration_date" | "relevance_score" | "excavator_name";

export function TicketTable({
  tickets,
  title,
  emptyMessage = "No tickets.",
  showRelevance = true,
  showGroup = true,
  highlightExpiring = false,
}: Props) {
  const [sortKey, setSortKey] = useState<SortKey>("expiration_date");
  const [sortAsc, setSortAsc] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);

  const sorted = [...tickets].sort((a, b) => {
    const av = a[sortKey] ?? "";
    const bv = b[sortKey] ?? "";
    const cmp = String(av).localeCompare(String(bv), undefined, { numeric: true });
    return sortAsc ? cmp : -cmp;
  });

  function handleSort(key: SortKey) {
    if (key === sortKey) setSortAsc((p) => !p);
    else {
      setSortKey(key);
      setSortAsc(true);
    }
  }

  function SortBtn({ col, label }: { col: SortKey; label: string }) {
    return (
      <button
        onClick={() => handleSort(col)}
        className="flex items-center gap-1 hover:text-blue-600"
      >
        {label}
        {sortKey === col && (
          <span className="text-blue-500">{sortAsc ? "↑" : "↓"}</span>
        )}
      </button>
    );
  }

  return (
    <section className="rounded-xl border border-gray-200 bg-white shadow-sm">
      {title && (
        <div className="border-b border-gray-100 px-5 py-3">
          <h2 className="text-base font-semibold text-gray-800">{title}</h2>
          <p className="text-xs text-gray-400">{tickets.length} ticket(s)</p>
        </div>
      )}

      {tickets.length === 0 ? (
        <p className="px-5 py-8 text-center text-sm text-gray-400">{emptyMessage}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 text-xs font-semibold uppercase tracking-wider text-gray-500">
              <tr>
                <th className="px-4 py-3 text-left">
                  <SortBtn col="ticket_number" label="Ticket #" />
                </th>
                <th className="px-4 py-3 text-left">
                  <SortBtn col="county" label="County" />
                </th>
                <th className="px-4 py-3 text-left">
                  <SortBtn col="excavator_name" label="Company" />
                </th>
                <th className="px-4 py-3 text-left">Work Type</th>
                <th className="px-4 py-3 text-left">
                  <SortBtn col="expiration_date" label="Expires" />
                </th>
                <th className="px-4 py-3 text-center">Status</th>
                {showRelevance && (
                  <th className="px-4 py-3 text-left">
                    <SortBtn col="relevance_score" label="Score" />
                  </th>
                )}
                {showGroup && <th className="px-4 py-3 text-left">Work Group</th>}
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {sorted.map((t) => {
                const days = daysUntilExpiration(t.expiration_date);
                const isExpanded = expanded === t.ticket_number;
                const rowHighlight =
                  highlightExpiring && days !== null && days <= 4 && days >= 0
                    ? "bg-red-50"
                    : "";

                return (
                  <>
                    <tr
                      key={t.ticket_number}
                      className={`hover:bg-blue-50 transition-colors cursor-pointer ${rowHighlight}`}
                      onClick={() => setExpanded(isExpanded ? null : t.ticket_number)}
                    >
                      <td className="px-4 py-3 font-mono text-xs text-blue-700">
                        {t.ticket_number}
                      </td>
                      <td className="px-4 py-3 text-gray-700">{t.county}</td>
                      <td className="px-4 py-3 text-gray-700 max-w-[180px] truncate">
                        {t.excavator_name ?? <span className="text-gray-300">—</span>}
                      </td>
                      <td className="px-4 py-3 text-gray-500 max-w-[160px] truncate">
                        {t.work_type ?? <span className="text-gray-300">—</span>}
                      </td>
                      <td className={`px-4 py-3 ${expiryUrgencyClass(days)}`}>
                        {formatDate(t.expiration_date)}
                        {days !== null && (
                          <span className="ml-1 text-xs">
                            ({days >= 0 ? `${days}d` : "expired"})
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-center">
                        <span
                          className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${statusBadgeClass(t.status)}`}
                        >
                          {t.status}
                        </span>
                      </td>
                      {showRelevance && (
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2">
                            <div className="w-16 overflow-hidden rounded-full bg-gray-100 h-1.5">
                              <div
                                className={`h-full rounded-full ${scoreColorClass(t.relevance_score)}`}
                                style={{ width: `${t.relevance_score * 100}%` }}
                              />
                            </div>
                            <span className="text-xs text-gray-500">
                              {t.relevance_score.toFixed(2)}
                            </span>
                          </div>
                        </td>
                      )}
                      {showGroup && (
                        <td className="px-4 py-3 text-gray-500 max-w-[140px] truncate text-xs">
                          {t.probable_work_group ?? t.probable_company ?? (
                            <span className="text-gray-300">—</span>
                          )}
                        </td>
                      )}
                      <td className="px-4 py-3 text-right text-gray-300">
                        {isExpanded ? "▲" : "▼"}
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr key={`${t.ticket_number}-detail`} className="bg-blue-50">
                        <td colSpan={showRelevance && showGroup ? 9 : showRelevance || showGroup ? 8 : 7}>
                          <div className="px-6 py-4 grid grid-cols-2 gap-4 text-sm md:grid-cols-3 lg:grid-cols-4">
                            <Detail label="Call Date" value={formatDate(t.call_date)} />
                            <Detail label="Legal Start" value={formatDate(t.legal_start_date)} />
                            <Detail label="Caller" value={t.caller_name} />
                            <Detail label="Probable Company" value={t.probable_company} />
                            <Detail label="Probable Crew" value={t.probable_crew} />
                            <Detail label="Parsed At" value={formatDate(t.parsed_at)} />
                            {t.location_text && (
                              <div className="col-span-2">
                                <Detail label="Location" value={t.location_text} />
                              </div>
                            )}
                            {t.remarks && (
                              <div className="col-span-2 lg:col-span-4">
                                <Detail label="Remarks" value={t.remarks} />
                              </div>
                            )}
                            {t.relevance_reasons.length > 0 && (
                              <div className="col-span-2 lg:col-span-4">
                                <p className="text-xs font-semibold text-gray-500 uppercase">
                                  Relevance reasons
                                </p>
                                <p className="mt-1 font-mono text-xs text-gray-600 break-all">
                                  {t.relevance_reasons.join(", ")}
                                </p>
                              </div>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Detail({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">{label}</p>
      <p className="mt-0.5 text-gray-700">{value ?? <span className="text-gray-300">—</span>}</p>
    </div>
  );
}
