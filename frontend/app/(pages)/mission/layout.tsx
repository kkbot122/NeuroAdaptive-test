// frontend/app/(pages)/mission/layout.tsx
import { auth } from "@/auth";
import { requireInternalToken } from "@/lib/internal-auth";
import { redirect } from "next/navigation";
import { headers } from "next/headers"; // <-- Import headers!

export default async function MissionLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const session = await auth();
  if (!session?.user?.email) {
    redirect("/signin");
  }

  const apiUrl = process.env.INTERNAL_API_URL || process.env.NEXT_PUBLIC_API_URL || "http://backend:8000";
  let shouldRedirect = false;

  try {
    const res = await fetch(`${apiUrl}/api/v1/profile/me`, {
      headers: {
        "x-user-email": session.user.email,
        "x-internal-token": requireInternalToken(),
      },
      cache: "no-store", 
    });

    if (res.ok) {
      const profile = await res.json();
      const raw = profile.raw_scores || {};
      const isOnboarded = Object.keys(raw).length > 0 && Object.values(raw).some((v: any) => v > 0);
      
      // MAGIC TRICK: Check if the request is an active Server Action
      const headersList = await headers();
      const isServerAction = headersList.has("next-action");
      
      // Only kick them out if they are onboarded AND they aren't currently submitting the quiz
      if (isOnboarded && !isServerAction) {
        shouldRedirect = true; 
      }
    }
  } catch (error) {
    console.error("Failed to verify onboarding status in layout:", error);
  }

  if (shouldRedirect) {
    redirect("/dashboard");
  }

  return <>{children}</>;
}