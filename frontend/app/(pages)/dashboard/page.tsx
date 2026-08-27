// frontend/app/(pages)/dashboard/page.tsx
import { auth, signOut } from "@/auth";
import { requireInternalToken } from "@/lib/internal-auth";
import { redirect } from "next/navigation";
import Link from "next/link";
import {
  Brain,
  LogOut,
  BookOpen,
  Target,
  Activity,
  User as UserIcon,
  Plus,
  Clock,
} from "lucide-react";

// Helper to format "THE_VISUALIZER" into "Visualizer"
const formatArchetype = (archetype: string) => {
  if (!archetype) return "Unknown";
  const clean = archetype.replace("THE_", "");
  return clean.charAt(0) + clean.slice(1).toLowerCase();
};

export default async function DashboardPage() {
  // 1. Authenticate on the server
  const session = await auth();
  if (!session?.user?.email) {
    redirect("/signin");
  }

  // 2. Fetch the user's profile and sessions securely
  const apiUrl =
    process.env.INTERNAL_API_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    "http://backend:8000";
    
  const commonHeaders = {
    "x-user-email": session.user.email,
    "x-internal-token": requireInternalToken(),
  };

  const [profileRes, sessionsRes] = await Promise.all([
    fetch(`${apiUrl}/api/v1/profile/me`, { headers: commonHeaders, cache: "no-store" }),
    fetch(`${apiUrl}/api/v1/chat/sessions`, { headers: commonHeaders, cache: "no-store" })
  ]);

  if (!profileRes.ok) {
    redirect("/mission");
  }

  const profile = await profileRes.json();
  const raw = profile.raw_scores || {};
  const recentSessions = sessionsRes.ok ? await sessionsRes.json() : [];

  // 3. Gatekeeper: If they have no scores, force them to calibrate!
  const isOnboarded =
    Object.keys(raw).length > 0 && Object.values(raw).some((v: any) => v > 0);
  if (!isOnboarded) {
    redirect("/mission");
  }

  return (
    <div className="min-h-screen bg-[#F4F1EA] text-black font-[family-name:var(--font-kodchasan)] pb-28">
      {/* NAVBAR */}
      <nav className="w-full bg-white border-b-2 border-black px-6 py-4 flex items-center justify-between sticky top-0 z-50">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-purple-500 rounded-lg border-2 border-black flex items-center justify-center shadow-[3px_3px_0px_0px_rgba(0,0,0,1)]">
            <Brain className="w-6 h-6 text-white" strokeWidth={2.5} />
          </div>
          <span className="text-xl font-bold tracking-tight hidden md:block">
            NeuroLearn
          </span>
        </div>

        <div className="flex items-center gap-4">
          <div className="flex items-center gap-3 bg-yellow-100 px-3 py-1.5 rounded-lg border-2 border-black">
            {session.user?.image ? (
              <img
                src={session.user.image}
                alt="Profile"
                className="w-8 h-8 rounded-full border border-black"
              />
            ) : (
              <div className="w-8 h-8 bg-gray-300 rounded-full border border-black flex items-center justify-center">
                <UserIcon className="w-4 h-4" />
              </div>
            )}
            <span className="font-bold text-sm hidden md:block">
              {session.user?.name?.split(" ")[0]}
            </span>
          </div>

          <form
            action={async () => {
              "use server";
              await signOut({ redirectTo: "/signin" });
            }}
          >
            <button
              type="submit"
              className="flex items-center gap-2 bg-[#FF6B6B] hover:bg-[#ff5252] border-2 border-black px-4 py-2 rounded-lg font-bold shadow-[3px_3px_0px_0px_rgba(0,0,0,1)] transition-all active:translate-y-1 active:shadow-none"
            >
              <LogOut className="w-4 h-4" strokeWidth={3} />
              <span className="hidden md:inline">Sign Out</span>
            </button>
          </form>
        </div>
      </nav>

      {/* MAIN CONTENT */}
      <main className="max-w-6xl mx-auto px-6 py-10">
        {/* HEADER */}
        <div className="mb-12">
          <div className="inline-block bg-[#CBF3F0] border-2 border-black px-3 py-1 text-sm font-bold mb-4 shadow-[2px_2px_0px_0px_rgba(0,0,0,1)] -rotate-1">
            👋 Ready to learn?
          </div>

          <h1 className="text-4xl md:text-5xl font-bold leading-tight">
            Welcome back,
            <span className="underline decoration-wavy decoration-purple-500 underline-offset-4 ml-2">
              {session.user?.name?.split(" ")[0]}
            </span>
            !
          </h1>

          <p className="text-gray-600 font-medium mt-2 text-lg">
            Your cognitive load is optimal. Your AI is tuned for a{" "}
            <span className="font-bold text-black">
              {formatArchetype(profile.primary_archetype)}
            </span>{" "}
            profile.
          </p>

          <Link
            href="/chat"
            className="w-fit mt-6 flex items-center gap-3 bg-[#FF9F1C] hover:bg-[#ff8c00] border-2 border-black px-6 py-3 rounded-xl font-bold text-lg shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] transition-all active:translate-x-1 active:translate-y-1 active:shadow-none"
          >
            <Plus className="w-6 h-6" strokeWidth={3} />
            New Mission
          </Link>
        </div>

        {/* STATS */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-8 mb-12">
          <div className="bg-white border-2 border-black rounded-xl p-6 shadow-[6px_6px_0px_0px_rgba(0,0,0,1)] transition-transform hover:-translate-y-1">
            <div className="p-3 bg-blue-100 border-2 border-black rounded-lg mb-4 w-fit">
              <BookOpen className="w-6 h-6 text-blue-600" />
            </div>
            <h3 className="text-gray-500 font-bold text-sm uppercase">
              Learning Sessions
            </h3>
            <p className="text-4xl font-bold mt-1 text-blue-600">{profile.learning_sessions_count}</p>
          </div>

          <div className="bg-white border-2 border-black rounded-xl p-6 shadow-[6px_6px_0px_0px_rgba(0,0,0,1)] transition-transform hover:-translate-y-1">
            <div className="p-3 bg-purple-100 border-2 border-black rounded-lg mb-4 w-fit">
              <Target className="w-6 h-6 text-purple-600" />
            </div>
            <h3 className="text-gray-500 font-bold text-sm uppercase">
              Cognitive Profile
            </h3>
            <p className="text-3xl font-bold mt-1 tracking-tight truncate">
              {formatArchetype(profile.primary_archetype)}
            </p>
          </div>

          <div className="bg-white border-2 border-black rounded-xl p-6 shadow-[6px_6px_0px_0px_rgba(0,0,0,1)] transition-transform hover:-translate-y-1">
            <div className="p-3 bg-green-100 border-2 border-black rounded-lg mb-4 w-fit">
              <Activity className="w-6 h-6 text-green-600" />
            </div>
            <h3 className="text-gray-500 font-bold text-sm uppercase">
              Adaptive Score
            </h3>
            <p className="text-4xl font-bold mt-1">94</p>
          </div>
        </div>

        {/* RECENT MISSIONS SECTION */}
        <div className="mt-16">
          <h2 className="text-2xl font-bold mb-6 flex items-center gap-3">
            <div className="w-2 h-8 bg-purple-500 border-2 border-black" />
            Neural Missions
          </h2>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {recentSessions.length > 0 ? (
              recentSessions.map((s: any) => (
                <Link
                  key={s.id}
                  href={`/chat?sessionId=${s.id}`}
                  className="bg-white border-2 border-black p-5 rounded-xl shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] hover:translate-x-1 hover:translate-y-1 hover:shadow-none transition-all flex items-center justify-between group"
                >
                  <div className="flex items-center gap-4">
                    <div className="p-2 bg-yellow-100 border-2 border-black rounded-lg">
                      <Brain className="w-5 h-5 text-yellow-600" />
                    </div>
                    <div>
                      <h4 className="font-bold text-lg group-hover:underline underline-offset-4 truncate max-w-[200px]">
                        {s.title}
                      </h4>
                      <p className="text-xs text-gray-400 font-bold uppercase flex items-center gap-1 mt-1">
                        <Clock className="w-3 h-3" />
                        {new Date(s.created_at).toLocaleDateString()}
                      </p>
                    </div>
                  </div>
                  <Plus className="w-5 h-5 text-gray-300 group-hover:text-black group-hover:rotate-45 transition-all" />
                </Link>
              ))
            ) : (
              <div className="col-span-full bg-white border-2 border-black border-dashed p-10 rounded-2xl flex flex-col items-center justify-center opacity-50">
                <Brain className="w-12 h-12 mb-4" />
                <p className="font-bold">No Neural Missions started yet.</p>
              </div>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
