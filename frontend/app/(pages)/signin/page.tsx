"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { signIn } from "next-auth/react";
import Link from "next/link";
import { ArrowRight, BookOpen } from "lucide-react";

function SignInError() {
  const params = useSearchParams();
  if (!params.get("error")) return null;
  return <p role="alert" className="mb-5 border-2 border-black border-l-8 border-l-[#FF6B5E] bg-[#FFF1EF] p-4 text-sm"><strong className="block">Sign-in did not finish.</strong>Google closed before your account could be confirmed. Nothing was changed; you can try again.</p>;
}

function AccountDeletionNotice() {
  const params = useSearchParams();
  const result = params.get("account-deletion");
  if (result === "cleanup-pending") return <p role="status" className="mb-5 border-2 border-black bg-[#FFF3BA] p-4 text-sm">Your account and learning records were deleted. Private file cleanup is still running; remaining files are tracked for retry.</p>;
  if (result === "complete") return <p role="status" className="mb-5 border-2 border-black bg-[#E6FBF3] p-4 text-sm">Your account and stored learning data have been deleted.</p>;
  return null;
}

function GoogleMark() {
  return <svg className="size-5 shrink-0" viewBox="0 0 48 48" aria-hidden="true"><path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.5 5.4 2.6 13.2l7.9 6.1C12.4 13.6 17.7 9.5 24 9.5z"/><path fill="#4285F4" d="M46.5 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.7c-.6 3-2.3 5.5-4.8 7.2l7.5 5.8c4.4-4.1 7.1-10.1 7.1-17.5z"/><path fill="#FBBC05" d="M10.5 28.7A14.5 14.5 0 0 1 9.5 24c0-1.6.3-3.2.8-4.7l-7.9-6.1A24 24 0 0 0 0 24c0 3.9.9 7.5 2.6 10.8l7.9-6.1z"/><path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.5-5.8c-2.1 1.4-4.9 2.3-8.4 2.3-6.3 0-11.6-4.1-13.5-9.8l-7.9 6.1C6.5 42.6 14.6 48 24 48z"/></svg>;
}

export default function SignInPage() {
  return <main className="nl-screen grid min-h-screen lg:grid-cols-[1.1fr_1fr]">
    <section className="flex flex-col justify-between gap-12 border-b-2 border-black bg-white p-7 md:p-12 lg:border-b-0 lg:border-r-2" aria-label="About NeuroLearn">
      <Link href="/" className="nl-brand"><span className="nl-brand-mark"><BookOpen className="size-5" /></span>NeuroLearn</Link>
      <div><p className="nl-kicker">Your sources. Your course.</p><h1 className="max-w-2xl text-4xl font-bold md:text-5xl">Turn your study material into a course that responds to your answers.</h1><p className="mt-5 max-w-xl text-lg text-zinc-700">Upload your notes, review the outline, and work through saved lessons and assessments from your own material.</p></div>
      <div className="grid gap-3 sm:grid-cols-3" aria-label="How NeuroLearn works"><div className="border-2 border-black bg-zinc-100 p-4"><strong className="block">Your files</strong><span className="text-sm">Notes and documents</span></div><div className="border-2 border-black bg-white p-4"><strong className="block">A course</strong><span className="text-sm">Reviewed outline</span></div><div className="border-2 border-black bg-[#FFD23F] p-4 shadow-[4px_4px_0_#111]"><strong className="block">Practice</strong><span className="text-sm">Based on saved answers</span></div></div>
    </section>
    <section className="grid place-items-center p-5 md:p-10">
      <div className="nl-card nl-card-accent w-full max-w-lg p-7 md:p-9">
        <p className="nl-kicker">Welcome</p><h2 className="text-3xl font-bold">Sign in</h2><p className="mt-2 mb-6 text-zinc-700">Use your Google account to save and return to your courses.</p>
        <Suspense fallback={null}><SignInError /></Suspense>
        <Suspense fallback={null}><AccountDeletionNotice /></Suspense>
        <button type="button" onClick={() => void signIn("google", { callbackUrl: "/dashboard" })} className="nl-button w-full py-3 text-base"><GoogleMark />Continue with Google</button>
        <p className="mt-5 text-sm text-zinc-600">Your account is used to sign you in and scope your course data.</p>
        <Link href="/" className="mt-6 inline-flex items-center gap-2 text-sm font-bold underline underline-offset-4">Back to NeuroLearn <ArrowRight className="size-4 rotate-180" /></Link>
      </div>
    </section>
  </main>;
}
