import { notFound, redirect } from "next/navigation";
import { auth } from "@/auth";
import { DiagnosticIntro } from "@/components/DiagnosticIntro";
import { backendUrl } from "@/lib/backend";
import { internalHeaders } from "@/lib/internal-auth";
import type { components } from "@/lib/generated/api";

type Course = components["schemas"]["CourseOut"];
type LearningState = components["schemas"]["LearningStateOut"];

export default async function DiagnosticIntroPage({ params }: { params: Promise<{ courseId: string }> }) {
  const session = await auth();
  if (!session?.user?.email) redirect("/signin");
  const { courseId } = await params;
  const headers = internalHeaders(session.user.email);
  const courseUrl = `${backendUrl()}/api/v1/courses/${courseId}`;
  let courseResponse: Response;
  try {
    courseResponse = await fetch(courseUrl, { headers, cache: "no-store" });
  } catch {
    return <main className="nl-screen grid place-items-center p-6"><p className="nl-card">This course is unavailable right now.</p></main>;
  }
  if (courseResponse.status === 404) notFound();
  if (!courseResponse.ok) return <main className="nl-screen grid place-items-center p-6"><p className="nl-card">This course is unavailable right now.</p></main>;
  const course: Course = await courseResponse.json();
  if (course.status !== "PUBLISHED") redirect(`/courses/${courseId}/workspace`);

  let stateResponse: Response;
  try {
    stateResponse = await fetch(`${courseUrl}/learning-state`, { headers, cache: "no-store" });
  } catch {
    return <main className="nl-screen grid place-items-center p-6"><p className="nl-card">Your saved course activity could not be loaded.</p></main>;
  }
  if (stateResponse.status === 404) notFound();
  if (!stateResponse.ok) return <main className="nl-screen grid place-items-center p-6"><p className="nl-card">Your saved course activity could not be loaded.</p></main>;
  const learningState: LearningState = await stateResponse.json();
  return <DiagnosticIntro course={course} learningState={learningState} />;
}
