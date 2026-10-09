"use client";

import { useState } from "react";

type Props = {
  disabled?: boolean;
  onRetry: () => Promise<void>;
};

export function GradingRetryButton({ disabled = false, onRetry }: Props) {
  const [isRetrying, setIsRetrying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const retry = async () => {
    if (isRetrying || disabled) return;
    setIsRetrying(true);
    setError(null);
    try {
      await onRetry();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Grading could not be retried.");
    } finally {
      setIsRetrying(false);
    }
  };

  return <>
    <button type="button" onClick={() => void retry()} disabled={disabled || isRetrying} className="nl-button mt-4">
      {isRetrying ? "Retrying…" : "Retry grading"}
    </button>
    {error && <p role="alert" className="mt-3 border-2 border-red-800 bg-red-50 p-3 text-sm text-red-900">{error}</p>}
  </>;
}
