import { redirect } from "next/navigation";
import { auth } from "@/auth";
import Link from "next/link";
import { Brain, ArrowRight, BookOpen, Shield, Zap, Sparkles } from "lucide-react";
import Architect from "@/components/illustrations/Architect";
import Tinkerer from "@/components/illustrations/Tinker";
import Sprinter from "@/components/illustrations/Sprinter";
import Astronaut from "@/components/illustrations/Astronaut";

export default async function Home() {
  const session = await auth();

  // If logged in, go to dashboard.
  if (session) {
    redirect("/dashboard");
  }

  return (
    <div className="min-h-screen bg-[#F4F1EA] text-black font-[family-name:var(--font-kodchasan)] selection:bg-purple-300">
      {/* --- Navbar --- */}
      <nav className="w-full border-b-2 border-black bg-white px-6 py-4 flex items-center justify-between sticky top-0 z-50">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 bg-purple-500 rounded-full border-2 border-black"></div>
          <span className="text-xl font-bold tracking-tight">NeuroLearn</span>
        </div>
        <div className="flex items-center gap-6">
          <Link
            href="#archetypes"
            className="hidden md:block hover:underline underline-offset-4 decoration-2"
          >
            Archetypes
          </Link>
          <Link
            href="#methodology"
            className="hidden md:block hover:underline underline-offset-4 decoration-2"
          >
            Methodology
          </Link>
          <Link href="/signin">
            <button className="bg-[#FF6B6B] border-2 border-black px-6 py-2 rounded-lg font-bold shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] hover:translate-y-1 hover:shadow-none transition-all active:translate-y-1 active:shadow-none">
              Login
            </button>
          </Link>
        </div>
      </nav>

      <main className="flex flex-col items-center">
        {/* --- Hero Section --- */}
        <section className="w-full max-w-6xl px-4 py-20 md:py-32 flex flex-col md:flex-row items-center gap-12">
          {/* Left Content */}
          <div className="flex-1 space-y-6 text-center md:text-left">
            <p className="text-xl md:text-2xl font-bold mb-10 text-gray-800 leading-tight">
              &quot;The most powerful learning system ever created.&quot; <br className="hidden md:block"/>
              Transform your raw documents into a mastery-driven curriculum in seconds.
            </p>
            <h2 className="text-5xl md:text-7xl font-black mb-8 uppercase tracking-tighter">
              Ready to Upgrade <br /> <span className="text-transparent bg-clip-text bg-gradient-to-r from-purple-500 to-[#FF9F1C]">&quot;Your&quot;</span> Brain?
            </h2>
            <p className="text-xl font-bold text-gray-700 italic max-w-2xl mx-auto leading-relaxed">
              &quot;The brutalist design combined with advanced AI creates an learning experience that doesn&apos;t just teach—it completely rewires how you think about education.&quot;
            </p>
            <div className="flex flex-col md:flex-row gap-4 pt-4 justify-center md:justify-start">
              <Link href="/signin">
                <button className="w-full md:w-auto bg-[#4D96FF] text-white border-2 border-black px-8 py-4 rounded-xl text-lg font-bold shadow-[6px_6px_0px_0px_rgba(0,0,0,1)] hover:translate-y-[2px] hover:shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] transition-all">
                  Start Learning
                </button>
              </Link>
              <button className="w-full md:w-auto bg-white border-2 border-black px-8 py-4 rounded-xl text-lg font-bold shadow-[6px_6px_0px_0px_rgba(0,0,0,1)] hover:translate-y-[2px] hover:shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] transition-all">
                View Demo
              </button>
            </div>
          </div>

          {/* Right Image/Illustration Placeholder */}
          <div className="flex-1 w-full flex justify-center relative">
            {/* Abstract Decorative Elements (Background) */}
            <div className="absolute top-0 right-10 w-16 h-16 bg-yellow-400 rounded-full border-2 border-black animate-bounce delay-700 -z-10"></div>
            <div className="absolute bottom-10 left-10 w-12 h-12 bg-blue-400 rotate-12 border-2 border-black -z-10"></div>
            {/* Main Illustration Container */}
            <div className="relative w-full max-w-lg md:max-w-xl bg-white border-2 border-black rounded-2xl p-2 shadow-[8px_8px_0px_0px_rgba(0,0,0,1)] z-10 transform -rotate-2 hover:rotate-0 transition-transform duration-500">
              <div className="bg-[#E0E7FF] border-2 border-black rounded-xl overflow-hidden relative">
                {/* Background Grid Pattern */}
                <div className="absolute inset-0 bg-[radial-gradient(#a5b4fc_1px,transparent_1px)] [background-size:16px_16px] opacity-50"></div>

                {/* The Illustration */}
                <Astronaut className="w-full h-full max-h-[400px] text-blue-600 relative z-10" />
              </div>
            </div>
          </div>
        </section>

        {/* --- Methodology / Features Section --- */}
        <section
          id="methodology"
          className="w-full bg-white border-y-2 border-black py-20 px-4"
        >
          <div className="max-w-6xl mx-auto">
            <h2 className="text-4xl font-bold text-center mb-16">
              The{" "}
              <span className="bg-yellow-300 px-2 border-2 border-black shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]">
                Functional Engine
              </span>
            </h2>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
              {/* Feature 1: The Sensor */}
              <div className="bg-[#FF9F1C] border-2 border-black rounded-xl p-6 shadow-[6px_6px_0px_0px_rgba(0,0,0,1)] hover:translate-y-1 hover:shadow-[2px_2px_0px_0px_rgba(0,0,0,1)] transition-all">
                <div className="w-12 h-12 bg-white rounded-lg border-2 border-black flex items-center justify-center mb-4">
                  <div className="w-7 h-7 text-black" />
                </div>
                <h3 className="text-2xl font-bold mb-2">The Sensor</h3>
                <p className="font-medium opacity-90">
                  Gaze & Scroll tracking captures your &quot;digital body language&quot;
                  to detect confusion instantly.
                </p>
              </div>

              {/* Feature 2: The Brain */}
              <div className="bg-[#2EC4B6] border-2 border-black rounded-xl p-6 shadow-[6px_6px_0px_0px_rgba(0,0,0,1)] hover:translate-y-1 hover:shadow-[2px_2px_0px_0px_rgba(0,0,0,1)] transition-all">
                <div className="w-12 h-12 bg-white rounded-lg border-2 border-black flex items-center justify-center mb-4">
                  <Brain className="w-7 h-7 text-black" strokeWidth={2.5} />
                </div>
                <h3 className="text-2xl font-bold mb-2">The Brain</h3>
                <p className="font-medium opacity-90">
                  Translates raw data into a &quot;Load Score,&quot; deciding if you are
                  zoning out or deeply focused.
                </p>
              </div>

              {/* Feature 3: The Teacher */}
              <div className="bg-[#CBF3F0] border-2 border-black rounded-xl p-6 shadow-[6px_6px_0px_0px_rgba(0,0,0,1)] hover:translate-y-1 hover:shadow-[2px_2px_0px_0px_rgba(0,0,0,1)] transition-all">
                <div className="w-12 h-12 bg-white rounded-lg border-2 border-black flex items-center justify-center mb-4">
                  <div className="w-7 h-7 text-black" />
                </div>
                <h3 className="text-2xl font-bold mb-2">The Teacher</h3>
                <p className="font-medium opacity-90">
                  Adapts content by injecting JIT scaffolding, simplifying text,
                  or offering quizzes.
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* --- Archetypes Section --- */}
        <section id="archetypes" className="w-full bg-[#F4F1EA] py-20 px-4">
          <div className="max-w-6xl mx-auto">
            <div className="text-center mb-12">
              <span className="font-bold text-sm tracking-widest uppercase text-gray-500 mb-2 block">
                Personalized Learning
              </span>
              <h2 className="text-4xl md:text-5xl font-bold">
                Which{" "}
                <span>&quot;I finally understand quantum mechanics...&quot;</span>
                <span className="underline decoration-wavy decoration-purple-500">
                  Archetype
                </span>{" "}
                Are You?
              </h2>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
              {/* Archetype 1: The Architect */}
              <div className="group bg-white border-2 border-black rounded-2xl overflow-hidden shadow-[8px_8px_0px_0px_rgba(0,0,0,1)] hover:shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] hover:translate-y-1 transition-all">
                {/* Illustration Placeholder */}
                <div className="h-48 bg-[#E2E8F0] border-b-2 border-black flex items-center justify-center relative overflow-hidden">
                  <div className="absolute inset-0 bg-[radial-gradient(#cbd5e1_1px,transparent_1px)] [background-size:16px_16px] opacity-50"></div>
                  <div className="relative w-full h-full p-4 flex items-center justify-center">
                    <Architect className="w-full h-full text-purple-600" />
                  </div>
                </div>
                <div className="p-6">
                  <div className="flex justify-between items-start mb-3">
                    <h3 className="text-2xl font-bold">The Architect</h3>
                    <span className="bg-purple-100 border border-black px-2 py-0.5 text-xs font-bold rounded">
                      Holist
                    </span>
                  </div>
                  <p className="text-sm font-medium text-gray-600 mb-4">
                    Scans the whole page first. Needs to know &quot;Why&quot; before
                    &quot;How.&quot; Gets frustrated by details without context.
                  </p>
                  <div className="bg-gray-50 p-3 rounded-lg border border-black border-dashed">
                    <p className="text-xs font-bold text-purple-600">
                      AI Action:
                    </p>
                    <p className="text-xs">
                      Generates a high-level &quot;Concept Map&quot; at the start of every
                      article.
                    </p>
                  </div>
                </div>
              </div>

              {/* Archetype 2: The Tinkerer */}
              <div className="group bg-white border-2 border-black rounded-2xl overflow-hidden shadow-[8px_8px_0px_0px_rgba(0,0,0,1)] hover:shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] hover:translate-y-1 transition-all">
                {/* Illustration Placeholder */}
                <div className="h-48 bg-[#FFEDD5] border-b-2 border-black flex items-center justify-center relative overflow-hidden">
                  <div className="absolute inset-0 bg-[radial-gradient(#fdba74_1px,transparent_1px)] [background-size:16px_16px] opacity-50"></div>
                  <Tinkerer className="w-full h-full text-orange-600 p-6" />
                </div>
                <div className="p-6">
                  <div className="flex justify-between items-start mb-3">
                    <h3 className="text-2xl font-bold">The Tinkerer</h3>
                    <span className="bg-orange-100 border border-black px-2 py-0.5 text-xs font-bold rounded">
                      Active
                    </span>
                  </div>
                  <p className="text-sm font-medium text-gray-600 mb-4">
                    Learns by doing. Skips text to find the &quot;Try it yourself&quot;
                    button. Gets bored if they can&apos;t interact with the content
                    within 30 seconds.
                  </p>
                  <div className="bg-gray-50 p-3 rounded-lg border border-black border-dashed">
                    <p className="text-xs font-bold text-orange-600">
                      AI Action:
                    </p>
                    <p className="text-xs">
                      Converts static examples into interactive code blocks &
                      challenges.
                    </p>
                  </div>
                </div>
              </div>

              {/* Archetype 3: The Sprinter */}
              <div className="group bg-white border-2 border-black rounded-2xl overflow-hidden shadow-[8px_8px_0px_0px_rgba(0,0,0,1)] hover:shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] hover:translate-y-1 transition-all">
                {/* Illustration Placeholder */}
                <div className="h-48 bg-[#D1FAE5] border-b-2 border-black flex items-center justify-center relative overflow-hidden">
                  <div className="absolute inset-0 bg-[radial-gradient(#6ee7b7_1px,transparent_1px)] [background-size:16px_16px] opacity-50"></div>
                  <Sprinter className="w-full h-full text-green-600 p-6" />
                </div>
                <div className="p-6">
                  <div className="flex justify-between items-start mb-3">
                    <h3 className="text-2xl font-bold">The Sprinter</h3>
                    <span className="bg-green-100 border border-black px-2 py-0.5 text-xs font-bold rounded">
                      Micro-Learner
                    </span>
                  </div>
                  <p className="text-sm font-medium text-gray-600 mb-4">
                    High energy, short duration. Needs rapid-fire content
                    delivery. Checks phone every 3 mins.
                  </p>
                  <div className="bg-gray-50 p-3 rounded-lg border border-black border-dashed">
                    <p className="text-xs font-bold text-green-600">
                      AI Action:
                    </p>
                    <p className="text-xs">
                      Auto-summarizes text into &quot;TikTok-style&quot; 30-second
                      flashcards.
                    </p>
                  </div>
                </div>
              </div>
            </div>

            {/* CTA for Archetypes */}
            <div className="mt-16 text-center">
              <p className="text-lg font-bold">&quot;Upload any document, and we&apos;ll generate a complete learning pathway instantly.&quot;</p>
              <p className="text-xl font-bold mb-6">
                And 7 more archetypes waiting to be discovered...
              </p>
              <Link href="/signin">
                <button className="bg-purple-500 text-white border-2 border-black px-8 py-3 rounded-xl text-xl font-bold shadow-[6px_6px_0px_0px_rgba(0,0,0,1)] hover:translate-y-[2px] hover:shadow-[3px_3px_0px_0px_rgba(0,0,0,1)] transition-all">
                  Sign Up to Find Your Archetype
                </button>
              </Link>
            </div>
          </div>
        </section>

        {/* --- Footer --- */}
        <footer className="w-full py-10 text-center border-t-2 border-black bg-white">
          <p className="font-bold text-gray-600">
            © 2026 NeuroLearn. Built for the Future of EdTech.
          </p>
        </footer>
      </main>
    </div>
  );
}
