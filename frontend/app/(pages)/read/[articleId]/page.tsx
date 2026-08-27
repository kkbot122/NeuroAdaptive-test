import { notFound, redirect } from "next/navigation";
import { auth } from "@/auth"; // Make sure this points to your NextAuth config
import TrackedImage from "@/components/TrackedImage";
import CalibrationQuiz from "@/components/CalibrationQuiz";
import AdaptiveContent from "@/components/AdaptiveContent";

interface Paragraph {
  id: number;
  order_index: number;
  original_text: string;
  adapted_text: string;
}

interface Article {
  id: number;
  title: string;
  topic: string;
  paragraphs: Paragraph[];
}

// SECURE FETCH: the backend derives the reader from these headers, so the
// article endpoint no longer accepts a caller-supplied user id.
async function getArticle(id: string, email: string): Promise<Article | null> {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";
  try {
    const res = await fetch(`${apiUrl}/api/v1/content/articles/${id}`, {
      cache: "no-store",
      headers: {
        "x-user-email": email,
        "x-internal-token": process.env.INTERNAL_API_KEY || "",
      },
    });
    if (!res.ok) return null;
    return await res.json();
  } catch (error) {
    return null;
  }
}

// SECURE FETCH: Uses headers instead of a hardcoded ID
async function getUserProfile(email: string) {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";
  try {
    const res = await fetch(`${apiUrl}/api/v1/profile/me`, {
      cache: "no-store",
      headers: {
        "x-user-email": email,
        "x-internal-token": process.env.INTERNAL_API_KEY || "",
      },
    });
    if (!res.ok) return null;
    return await res.json();
  } catch (error) {
    return null;
  }
}

export default async function ReadPage({
  params,
}: {
  params: Promise<{ articleId: string }>;
}) {
  const session = await auth();

  if (!session?.user?.email) {
    redirect("/signin");
  }

  const { articleId } = await params;

  const [article, profile] = await Promise.all([
    getArticle(articleId, session.user.email),
    getUserProfile(session.user.email),
  ]);

  if (!article) {
    notFound();
  }

  const needsCalibration = !profile || !profile.primary_archetype;

  return (
    <div className="relative min-h-screen bg-[#F4F1EA] text-black font-[family-name:var(--font-kodchasan)] pb-20">
      {/* 1. CALIBRATION OVERLAY */}
      {needsCalibration && <CalibrationQuiz userEmail={session.user.email} />}

      <div className="max-w-4xl mx-auto px-6 py-12">
        {/* Header Section */}
        <div className="mb-12 border-b-4 border-black pb-8">
          <span className="inline-block bg-[#CBF3F0] border-2 border-black px-3 py-1 font-bold uppercase tracking-widest shadow-[3px_3px_0px_0px_rgba(0,0,0,1)] mb-6 -rotate-1">
            {article.topic}
          </span>
          <h1 className="text-5xl md:text-6xl font-extrabold leading-tight tracking-tight">
            {article.title}
          </h1>
        </div>

        {/* 2. VISUAL TRACKER (Wrapped in Neubrutalist frame) */}
        <div className="mb-12 bg-white border-4 border-black p-4 shadow-[8px_8px_0px_0px_rgba(0,0,0,1)] rotate-1">
          <div className="border-2 border-black overflow-hidden relative">
            <TrackedImage
              id={1001}
              src="https://images.unsplash.com/photo-1635070041078-e363dbe005cb?auto=format&fit=crop&q=80&w=1000"
              alt="Complex Quantum Probability Distribution Diagram"
            />
            {/* Brutalist Label */}
            <div className="absolute top-0 left-0 bg-[#FF9F1C] border-r-2 border-b-2 border-black px-3 py-1 font-bold text-sm uppercase">
              Fig 1.0
            </div>
          </div>
        </div>

        {/* 3. ADAPTIVE CONTENT WRAPPER */}
        <div className="bg-white border-4 border-black p-8 md:p-12 shadow-[10px_10px_0px_0px_rgba(0,0,0,1)] text-lg font-medium leading-relaxed">
          <AdaptiveContent paragraphs={article.paragraphs} />
        </div>

        <div className="mt-16 pt-8 text-center">
          <span className="bg-black text-white font-bold px-4 py-2 uppercase tracking-widest">
            END OF MODULE
          </span>
        </div>
      </div>
    </div>
  );
}
