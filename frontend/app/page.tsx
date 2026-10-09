import Link from "next/link";
import { redirect } from "next/navigation";
import { auth } from "@/auth";
import Tinkerer from "@/components/illustrations/Tinker";

const progressLabels = [
  ["Not assessed", "No graded evidence is available yet."],
  ["Needs attention", "Recent answers show a gap."],
  ["Developing", "Some answers are correct, but not consistently yet."],
  ["Proficient", "Recent answers are consistently correct on this concept."],
  ["Mastered", "Correct on varied questions over time."],
] as const;

const steps = [
  {
    number: "1",
    title: "Upload your material",
    text: "Add PDFs, text, or Markdown and say what you want to be able to do. Your sources are checked as the course is prepared.",
  },
  {
    number: "2",
    title: "Review the outline",
    text: "Inspect the modules, lessons, and objectives built from your sources. Rename lessons, check the outline, then publish it yourself.",
  },
  {
    number: "3",
    title: "Study and practice",
    text: "Read a lesson, answer questions, and review the saved evidence. The next activity is selected from your answers, with the reason shown.",
  },
] as const;

const features = [
  {
    color: "violet",
    title: "Built from your files only",
    text: "Saved lessons and tutor answers are checked against your course sources. Citations open the supporting passage.",
  },
  {
    color: "yellow",
    title: "Reading is not mastery",
    text: "Finishing a lesson records reading coverage. Understanding labels use graded answers.",
  },
  {
    color: "mint",
    title: "Pending stays pending",
    text: "Unresolved grading is shown separately and is never counted as an incorrect answer.",
  },
  {
    color: "coral",
    title: "Tutor answers show sources",
    text: "Tutor replies use checked course material. When the sources cannot support an answer, the tutor says so.",
  },
] as const;

const questions = [
  ["What files can I use?", "PDF, TXT, and Markdown files are supported. If a source cannot be read, you can review the issue in the course workspace."],
  ["Is my material private?", "Your course files and saved learning records are scoped to your account."],
  ["Do I have to take the diagnostic?", "No. The diagnostic is optional, and you can start with a lesson instead."],
  ["What if I disagree with a grade?", "You can report a grading judgment from submitted results. A reviewer can correct it, and the original judgment remains visible."],
] as const;

export default async function Home() {
  const session = await auth();
  if (session) redirect("/dashboard");

  return <main className="nl-screen nl-landing">
    <nav className="nl-landing-nav" aria-label="Main">
      <Link href="/" className="nl-landing-logo"><i aria-hidden="true" />NeuroLearn</Link>
      <div className="nl-landing-links">
        <a href="#how">How it works</a>
        <a href="#different">Why it is different</a>
        <a href="#progress">Progress</a>
        <a href="#faq">FAQ</a>
      </div>
      <Link href="/signin" className="nl-landing-button nl-landing-button-small">Sign in</Link>
    </nav>

    <header className="nl-landing-wrap nl-landing-hero">
      <div>
        <h1>Study from your own material, not someone else&apos;s course.</h1>
        <p className="nl-landing-lead">Upload your notes, slides, and PDFs. NeuroLearn builds lessons from those sources, checks what you understand, and picks what to study next.</p>
        <div className="nl-landing-actions">
          <Link href="/signin" className="nl-landing-button nl-landing-button-primary nl-landing-button-large">Continue with Google</Link>
          <a href="#how" className="nl-landing-button nl-landing-button-large">See how it works</a>
        </div>
        <p className="nl-landing-fine">Your files and progress stay private to you.</p>
      </div>

      <figure className="nl-landing-frame">
        <div className="nl-landing-scene">
          <Tinkerer className="nl-landing-illustration" />
        </div>
        <figcaption className="nl-landing-caption">
          <span>Concept understanding</span>
          <span><span className="nl-landing-label nl-landing-label-mint">Saved answers</span> <span className="nl-landing-label nl-landing-label-coral">Evidence based</span></span>
        </figcaption>
      </figure>
    </header>

    <section className="nl-landing-section nl-landing-section-white" id="how"><div className="nl-landing-wrap">
      <h2>From a pile of files to a course in three steps.</h2>
      <p className="nl-landing-sub">You stay in control at each step. Nothing is published until you say so.</p>
      <div className="nl-landing-steps">
        {steps.map((step) => <article className="nl-landing-step" key={step.number}>
          <span className="nl-landing-step-number">{step.number}</span>
          <h3>{step.title}</h3>
          <p>{step.text}</p>
        </article>)}
      </div>
    </div></section>

    <section className="nl-landing-section" id="different"><div className="nl-landing-wrap">
      <h2>Built to be honest about what you know.</h2>
      <p className="nl-landing-sub">Most study tools count pages read. NeuroLearn separates reading from understanding.</p>
      <div className="nl-landing-features">
        {features.map((feature) => <article className="nl-landing-feature" key={feature.title}>
          <span className={`nl-landing-feature-mark nl-landing-feature-mark-${feature.color}`} aria-hidden="true" />
          <h3>{feature.title}</h3>
          <p>{feature.text}</p>
        </article>)}
      </div>
    </div></section>

    <section className="nl-landing-section nl-landing-section-white" id="progress"><div className="nl-landing-wrap">
      <h2>Progress you can read at a glance.</h2>
      <p className="nl-landing-sub">Five plain labels instead of a percentage that pretends to be exact.</p>
      <div className="nl-landing-legend">
        {progressLabels.map(([label, description]) => <div key={label}>
          <span className={`nl-landing-label nl-landing-label-${label.toLowerCase().replaceAll(" ", "-")}`}>{label}</span>
          <p>{description}</p>
        </div>)}
      </div>
      <p className="nl-landing-note">Lesson coverage and concept understanding are kept apart, so finishing every lesson never hides a weak spot.</p>
    </div></section>

    <section className="nl-landing-section" id="faq"><div className="nl-landing-wrap">
      <h2>Questions you might have.</h2>
      <p className="nl-landing-sub">Short answers to the common ones.</p>
      <div className="nl-landing-faq">
        {questions.map(([question, answer]) => <details key={question}>
          <summary>{question}</summary>
          <p>{answer}</p>
        </details>)}
      </div>
    </div></section>

    <section className="nl-landing-final"><div className="nl-landing-wrap">
      <h2>Turn what you already have into a course.</h2>
      <p>Sign in with Google and add your first source. You can review the outline before it is published.</p>
      <Link href="/signin" className="nl-landing-button nl-landing-button-warm nl-landing-button-large">Continue with Google</Link>
    </div></section>

    <footer className="nl-landing-footer"><div className="nl-landing-wrap">
      <span><b>NeuroLearn</b>&nbsp; A final year BTech project</span>
      <span>Your files and progress stay private to you.</span>
    </div></footer>
  </main>;
}
