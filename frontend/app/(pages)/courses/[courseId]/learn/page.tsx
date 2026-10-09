import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { auth } from "@/auth";
import { CourseOverview } from "@/components/CourseOverview";
import { backendUrl } from "@/lib/backend";
import { internalHeaders } from "@/lib/internal-auth";
import type { components } from "@/lib/generated/api";

type Course = components["schemas"]["CourseOut"];
type Structure = components["schemas"]["StructureOut"];
type LearningState = components["schemas"]["LearningStateOut"];
type Graph = components["schemas"]["GraphOut"];

function Unavailable({ courseId }: { courseId: string }) {
  return <main className="nl-screen grid place-items-center px-5 py-14 text-center">
    <section className="nl-card nl-card-raised w-full max-w-xl">
      <p className="nl-kicker">Course overview</p>
      <h1 className="text-2xl font-bold">Your course details are unavailable</h1>
      <p className="mt-3 text-zinc-700">Your saved learning work is still on the course. Try loading it again.</p>
      <div className="nl-row mt-5 justify-center"><Link href={`/courses/${courseId}/learn`} className="nl-button nl-button-primary">Try again</Link><Link href="/dashboard" className="nl-button">Dashboard</Link></div>
    </section>
  </main>;
}

export default async function LearnPage({ params }: { params: Promise<{ courseId: string }> }) {
  const session = await auth();
  if (!session?.user?.email) redirect("/signin");
  const { courseId } = await params;
  const headers = internalHeaders(session.user.email);
  const origin = `${backendUrl()}/api/v1/courses/${courseId}`;

  let courseResponse: Response;
  try {
    courseResponse = await fetch(origin, { headers, cache: "no-store" });
  } catch {
    return <Unavailable courseId={courseId} />;
  }
  if (courseResponse.status === 404) notFound();
  if (!courseResponse.ok) return <Unavailable courseId={courseId} />;
  const course: Course = await courseResponse.json();
  if (course.status !== "PUBLISHED") redirect(`/courses/${courseId}/workspace`);

  let responses: Response[];
  try {
    responses = await Promise.all([
      fetch(`${origin}/structure`, { headers, cache: "no-store" }),
      fetch(`${origin}/learning-state`, { headers, cache: "no-store" }),
      fetch(`${origin}/graph`, { headers, cache: "no-store" }),
    ]);
  } catch {
    return <Unavailable courseId={courseId} />;
  }
  if (responses.some((response) => response.status === 404)) notFound();
  if (responses.some((response) => !response.ok)) return <Unavailable courseId={courseId} />;
  const [structure, learningState, graph] = await Promise.all(responses.map((response) => response.json())) as [Structure, LearningState, Graph];

  return <CourseOverview course={course} structure={structure} learningState={learningState} graph={graph} />;
}
