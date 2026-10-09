"use client";

import { useEffect, useRef } from "react";
import { shouldRestoreReadingPosition } from "@/lib/study-workspace-state.mjs";

type Props = {
  identity: string;
  readingPosition: number;
  hasActivity: boolean;
  isLoading: boolean;
  isContentLoading: boolean;
  contentFormat?: string;
  format: string;
};

export function StudyPositionRestorer(props: Props) {
  const restoredIdentityRef = useRef<string | null>(null);
  const { identity, readingPosition, hasActivity, isLoading, isContentLoading, contentFormat, format } = props;

  useEffect(() => {
    if (!shouldRestoreReadingPosition({
      identity,
      restoredIdentity: restoredIdentityRef.current,
      hasActivity,
      isLoading,
      isContentLoading,
      contentFormat: contentFormat ?? null,
      format,
    })) return;
    restoredIdentityRef.current = identity;
    window.scrollTo(0, readingPosition);
  }, [contentFormat, format, hasActivity, identity, isContentLoading, isLoading, readingPosition]);

  return null;
}
