import { backendUrl } from "@/lib/backend";
import type { components } from "@/lib/generated/api";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { auth } from "@/auth";
import { internalHeaders } from "@/lib/internal-auth";
import { studyHrefForRecommendation } from "@/lib/learning-route";

const BACKEND_URL = backendUrl();

function Unavailable({ courseId }: { courseId: string }) {
  return (
    <main className="min-h-screen bg-[#F4F1EA] px-6 py-16 text-center text-black">
      <h1 className="text-2xl font-bold">Your next activity is unavailable right now</h1>
      <p className="mt-3">Your published course is saved. Please try again shortly.</p>
      <div className="mt-6 flex justify-center gap-4">
        <Link href={`/courses/${courseId}/learn`} className="border-2 border-black bg-[#FF9F1C] px-5 py-3 font-bold">Try again</Link>
        <Link href="/dashboard" className="border-2 border-black bg-white px-5 py-3 font-bold">Dashboard</Link>
      </div>
    </main>
  );
}

function UnsupportedActivity({ activity }: { activity: components["schemas"]["LearningActivityOut"] }) {
  return (
    <main className="min-h-screen bg-[#F4F1EA] px-6 py-16 text-center text-black">
      <h1 className="text-2xl font-black">This activity is unavailable right now</h1>
      <p className="mx-auto mt-3 max-w-2xl">{activity.unavailable_reason}</p>
      {activity.reason && <p className="mx-auto mt-4 max-w-2xl font-bold">Recorded recommendation: {activity.reason}</p>}
      <p className="mt-3 text-sm text-zinc-600">The selected activity and course version are saved. No lesson or assessment was substituted.</p>
      <Link href="/dashboard" className="mt-6 inline-block border-2 border-black bg-white px-5 py-3 font-bold">Return to dashboard</Link>
    </main>
  );
}

export default async function LearnPage({ params }: { params: Promise<{ courseId: string }> }) {
  const session = await auth();
  if (!session?.user?.email) redirect("/signin");

  const { courseId } = await params;
  const headers = internalHeaders(session.user.email);
  const courseUrl = `${BACKEND_URL}/api/v1/courses/${courseId}`;
  let courseResponse: Response;
  try {
    courseResponse = await fetch(courseUrl, { headers, cache: "no-store" });
  } catch {
    return <Unavailable courseId={courseId} />;
  }
  if (courseResponse.status === 404) notFound();
  if (!courseResponse.ok) return <Unavailable courseId={courseId} />;

  const course: components["schemas"]["CourseOut"] = await courseResponse.json();
  if (course.status !== "PUBLISHED") redirect(`/courses/${courseId}/workspace`);

  let activityResponse: Response;
  try {
    activityResponse = await fetch(`${courseUrl}/activities/next`, { method: "POST", headers, cache: "no-store" });
  } catch {
    return <Unavailable courseId={courseId} />;
  }
  if (!activityResponse.ok) return <Unavailable courseId={courseId} />;

  const activity: components["schemas"]["LearningActivityOut"] = await activityResponse.json();
  if (activity.experience_availability === "UNAVAILABLE") {
    return <UnsupportedActivity activity={activity} />;
  }
  if (activity.assessment_session_id) {
    redirect(`/courses/${courseId}/assessment?type=activity&sessionId=${activity.assessment_session_id}`);
  }
  if (["PREREQUISITE_REMEDIATION", "TARGETED_PRACTICE", "CHALLENGE"].includes(activity.activity_type)) {
    redirect(`/courses/${courseId}/activities/${activity.id}`);
  }
  if (!activity.lesson_id) {
    return <UnsupportedActivity activity={{ ...activity, unavailable_reason: activity.unavailable_reason || "This activity has no supported P2 lesson experience." }} />;
  }
  let structureResponse: Response;
  try {
    structureResponse = await fetch(`${courseUrl}/structure`, { headers, cache: "no-store" });
  } catch {
    return <Unavailable courseId={courseId} />;
  }
  if (!structureResponse.ok) return <Unavailable courseId={courseId} />;
  const structure: components["schemas"]["StructureOut"] = await structureResponse.json();
  const studyHref = studyHrefForRecommendation(
    courseId,
    { lesson_id: activity.lesson_id, concept_ids: activity.target_concept_ids },
    structure,
  );
  if (!studyHref) return <Unavailable courseId={courseId} />;
  redirect(`${studyHref}?activityId=${activity.id}&format=${encodeURIComponent(activity.presentation_format)}`);
}
