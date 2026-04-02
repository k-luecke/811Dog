/**
 * useData.ts — React hooks for fetching dashboard JSON data.
 *
 * Each hook fetches one JSON file and returns { data, loading, error }.
 * Data is fetched once on mount; no polling (dashboard is static).
 */

import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type {
  ChangesResponse,
  CountyResponse,
  ExpiringResponse,
  ParseFailuresResponse,
  Summary,
  TicketsResponse,
  WorkGroupResponse,
} from "../types";

interface FetchState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

function useFetch<T>(fetcher: () => Promise<T>): FetchState<T> {
  const [state, setState] = useState<FetchState<T>>({
    data: null,
    loading: true,
    error: null,
  });

  useEffect(() => {
    let cancelled = false;
    setState({ data: null, loading: true, error: null });

    fetcher()
      .then((data) => {
        if (!cancelled) setState({ data, loading: false, error: null });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const msg = err instanceof Error ? err.message : String(err);
          setState({ data: null, loading: false, error: msg });
        }
      });

    return () => {
      cancelled = true;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return state;
}

export const useSummary = () => useFetch<Summary>(api.fetchSummary);
export const useActiveTickets = () => useFetch<TicketsResponse>(api.fetchActiveTickets);
export const useExpiringTickets = () => useFetch<ExpiringResponse>(api.fetchExpiringTickets);
export const useByCounty = () => useFetch<CountyResponse>(api.fetchByCounty);
export const useByWorkGroup = () => useFetch<WorkGroupResponse>(api.fetchByWorkGroup);
export const useRecentChanges = () => useFetch<ChangesResponse>(api.fetchRecentChanges);
export const useParseFailures = () => useFetch<ParseFailuresResponse>(api.fetchParseFailures);
