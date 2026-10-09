"use client";

import type { components } from "@/lib/generated/api";
import { visibleWorkspaceContent } from "@/lib/study-workspace-state.mjs";
import { LessonDiagram } from "@/components/LessonDiagram";
import { LessonSourcePassages } from "@/components/LessonSourcePassages";
import { QuizFirstLesson } from "@/components/QuizFirstLesson";

type Content = components["schemas"]["PreparedLessonContentOut"];
type Format = Content["presentation_format"];
type SectionName = "objective" | "explanation" | "example" | "recap";
type Props = {
  state: { identity: string; content: Content | null } | null;
  identity: string;
  format: Format;
  courseId?: string;
  activeSourceChunkId?: string | null;
  onOpenSource: (chunkId: string) => void;
  onQuizReadyChange?: (ready: boolean) => void;
};

const SECTION_ORDER: Record<Format, readonly SectionName[]> = {
  concise: ["objective", "explanation", "example", "recap"],
  detailed: ["objective", "explanation", "example", "recap"],
  worked_example: ["objective", "example", "explanation", "recap"],
  analogy: ["objective", "example", "explanation", "recap"],
  diagram: ["objective", "explanation", "example", "recap"],
  source_view: ["objective", "explanation", "example", "recap"],
  quiz_first: ["objective", "explanation", "example", "recap"],
};

function sectionTitle(format: Format, section: SectionName): string {
  if (section === "example" && format === "analogy") return "Source-grounded comparison";
  if (section === "explanation" && format === "diagram") return "Source-backed points";
  return section === "example" ? "Worked example" : section;
}

export function PreparedLessonContent({ state, identity, format, courseId, activeSourceChunkId = null, onOpenSource, onQuizReadyChange }: Props) {
  const content = visibleWorkspaceContent(state, identity, format) as Content | null;
  if (!content) return null;

  const compact = format === "concise";
  const emphasizedExample = format === "worked_example" || format === "analogy";

  const renderSections = (sectionNames: readonly SectionName[], conceptIds?: string[]) => sectionNames.map((sectionName) => {
      const highlighted = emphasizedExample && sectionName === "example";
      const statements = content.sections[sectionName].filter((statement) => !conceptIds
        || statement.concept_ids.some((id) => conceptIds.includes(id)));
      if (!statements.length) return null;
      return <section key={sectionName}
        className={`border-t-2 border-black ${compact ? "py-3" : "py-5"} first:border-t-0 ${highlighted ? "my-2 border-2 bg-[#fffbe0] px-4 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]" : ""}`}>
        <h2 className={`${compact ? "mb-2 text-base" : "mb-3 text-xl"} font-bold capitalize`}>{sectionTitle(format, sectionName)}</h2>
        <div className={`${compact ? "space-y-2" : "space-y-4"} leading-relaxed`}>
          {statements.map((statement, index) => <div key={`${sectionName}-${index}`}>
            <p>{statement.text}</p>
            {statement.citation_chunk_ids.length > 0 && <div className="mt-2 flex flex-wrap gap-2" aria-label="Supporting sources">
              {statement.citation_chunk_ids.map((chunkId, citationIndex) => <button key={`${chunkId}-${citationIndex}`} type="button"
                className="nl-chip hover:bg-[#FFD23F]" aria-label={`Open supporting source passage ${citationIndex + 1} in the Sources tab`}
                title="Opens this passage in the Sources tab beside the lesson" onClick={() => onOpenSource(chunkId)}>View source {citationIndex + 1}</button>)}
            </div>}
          </div>)}
        </div>
      </section>;
    });

  return <div className="mt-4">
    {activeSourceChunkId && <p className="mb-3 border-l-4 border-[#FFD23F] bg-[#fffbe0] px-3 py-2 text-sm font-semibold" role="status">The cited passage is open in the Sources tab.</p>}
    {format === "source_view" && courseId && <LessonSourcePassages courseId={courseId} artifactId={content.artifact_id}
      sourceIds={content.source_chunk_ids} onOpenSource={onOpenSource} />}
    {format === "diagram" && <LessonDiagram nodes={content.sections.explanation} edges={content.sections.diagram_edges ?? []} onOpenSource={onOpenSource} />}
    {format === "quiz_first" ? <QuizFirstLesson key={`${identity}:${content.artifact_id}`} content={content} identity={identity}
      onReadyChange={onQuizReadyChange} renderExplanation={(conceptIds) => renderSections(["explanation", "example", "recap"], conceptIds)} />
      : renderSections(SECTION_ORDER[format])}
  </div>;
}
