import { LoaderCircle } from "lucide-react";
import type { components } from "@/lib/generated/api";

type Preparation = components["schemas"]["PreparationOut"];

export function EmptyLessonContentState({
  preparation,
  isLoading,
}: {
  preparation: Preparation | null;
  isLoading: boolean;
}) {
  if (preparation && ["PENDING", "RUNNING"].includes(preparation.status)) {
    return (
      <p className="mt-5 flex items-center gap-3 border-2 border-dashed border-zinc-500 p-5 text-zinc-700" role="status">
        <LoaderCircle className="size-5 animate-spin" />
        Preparing this saved format… {preparation.progress}%
      </p>
    );
  }

  if (isLoading) {
    return <p className="mt-5 flex items-center gap-3 border-2 border-dashed border-zinc-500 p-5 text-zinc-700" role="status"><LoaderCircle className="size-5 animate-spin" />Loading saved content for this format…</p>;
  }

  return <p className="mt-5 text-zinc-700">Validated content is not ready yet. Your saved activity remains available.</p>;
}
