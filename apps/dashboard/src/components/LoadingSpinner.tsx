/**
 * LoadingSpinner.tsx — Simple loading indicator.
 */
export function LoadingSpinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-3 py-12 text-gray-400">
      <svg
        className="h-5 w-5 animate-spin text-blue-500"
        xmlns="http://www.w3.org/2000/svg"
        fill="none"
        viewBox="0 0 24 24"
      >
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path
          className="opacity-75"
          fill="currentColor"
          d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
        />
      </svg>
      <span className="text-sm">{label}</span>
    </div>
  );
}

/**
 * ErrorBanner.tsx — Error display banner.
 */
export function ErrorBanner({ error, label }: { error: string; label?: string }) {
  return (
    <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
      <span className="font-semibold">{label ?? "Error"}: </span>
      {error}
    </div>
  );
}
