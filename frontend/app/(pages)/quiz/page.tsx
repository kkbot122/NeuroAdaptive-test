"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { Brain, ArrowLeft, CheckCircle2, XCircle, ChevronRight, Trophy } from "lucide-react";

interface Question {
  question: string;
  topic: string;
  options: string[];
  correct_answer: string;
  explanation: string;
}

interface QuizData {
  title: string;
  questions: Question[];
}

export default function QuizPage() {
  const router = useRouter();
  const [quiz, setQuiz] = useState<QuizData | null>(null);
  const [currentStep, setCurrentStep] = useState(0);
  const [answers, setAnswers] = useState<string[]>([]);
  const [showResults, setShowResults] = useState(false);

  useEffect(() => {
    const data = sessionStorage.getItem("current_quiz");
    if (data) {
      try {
        setQuiz(JSON.parse(data));
      } catch (e) {
        console.error("Quiz parsing error", e);
        router.push("/chat");
      }
    } else {
      router.push("/chat");
    }
  }, [router]);

  if (!quiz) return null;

  const currentQuestion = quiz.questions[currentStep];
  const isLastStep = currentStep === quiz.questions.length - 1;

  const handleSelect = (option: string) => {
    const newAnswers = [...answers];
    newAnswers[currentStep] = option;
    setAnswers(newAnswers);
  };

  const handleNext = () => {
    if (isLastStep) {
      setShowResults(true);
    } else {
      setCurrentStep(currentStep + 1);
    }
  };

  const calculateResults = () => {
    let score = 0;
    const missedTopics: string[] = [];
    
    quiz.questions.forEach((q, i) => {
      if (answers[i] === q.correct_answer) {
        score++;
      } else {
        if (q.topic && !missedTopics.includes(q.topic)) {
          missedTopics.push(q.topic);
        }
      }
    });

    return { score, total: quiz.questions.length, missedTopics };
  };

  const handleFinish = async () => {
    const results = calculateResults();

    // Persist server-side so the attempt survives the tab and can feed mastery
    // estimation. The backend re-grades from questions + answers; the score
    // computed here is only for the results screen. Failure is logged, not
    // surfaced — a telemetry problem must not trap the learner on this page.
    try {
      const response = await fetch("/api/quiz", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: quiz.title,
          topic: quiz.questions[0]?.topic ?? null,
          questions: quiz.questions,
          answers: quiz.questions.map((_, i) => answers[i] ?? null),
        }),
      });
      if (!response.ok) {
        console.warn(`Quiz attempt not recorded: ${response.status}`);
      }
    } catch (error) {
      console.warn("Quiz attempt failed to send:", error);
    }

    sessionStorage.setItem("last_quiz_results", JSON.stringify({
      title: quiz.title,
      score: results.score,
      total: results.total,
      missed_topics: results.missedTopics
    }));
    // Include session ID in URL so chat page restores the correct session
    // (avoids relying solely on sessionStorage which can be lost)
    const savedSessionId = sessionStorage.getItem("chat_session_id");
    const returnUrl = savedSessionId
      ? `/chat?quiz_done=true&sessionId=${savedSessionId}`
      : "/chat?quiz_done=true";
    router.push(returnUrl);
  };

  if (showResults) {
    const { score, total, missedTopics } = calculateResults();
    const percentage = Math.round((score / total) * 100);

    return (
      <div className="min-h-screen bg-[#F4F1EA] flex flex-col items-center justify-center p-6 font-[family-name:var(--font-kodchasan)]">
        <div className="max-w-2xl w-full bg-white border-4 border-black p-10 rounded-3xl shadow-[12px_12px_0px_0px_rgba(0,0,0,1)] text-center">
          <div className="inline-block p-4 bg-yellow-400 border-4 border-black rounded-full mb-6 rotate-3">
            <Trophy className="w-12 h-12 text-black" />
          </div>
          
          <h1 className="text-4xl font-black mb-2 uppercase tracking-tight">Mission Report</h1>
          <p className="text-xl font-bold text-gray-600 mb-8">{quiz.title}</p>
          
          <div className="flex items-center justify-center gap-8 mb-10">
            <div className="flex flex-col">
              <span className="text-6xl font-black">{percentage}%</span>
              <span className="text-sm font-bold uppercase tracking-widest text-gray-400">Accuracy</span>
            </div>
            <div className="w-px h-16 bg-black" />
            <div className="flex flex-col">
              <span className="text-6xl font-black">{score}/{total}</span>
              <span className="text-sm font-bold uppercase tracking-widest text-gray-400">Score</span>
            </div>
          </div>

          {missedTopics.length > 0 && (
            <div className="text-left mb-10">
              <h3 className="font-black text-lg mb-4 flex items-center gap-2">
                <Brain className="w-5 h-5" />
                NEURAL GAPS IDENTIFIED:
              </h3>
              <div className="flex flex-wrap gap-2">
                {missedTopics.map((topic, i) => (
                  <span key={i} className="bg-red-100 border-2 border-black px-3 py-1 font-bold text-sm">
                    {topic}
                  </span>
                ))}
              </div>
            </div>
          )}

          <button
            onClick={handleFinish}
            className="w-full bg-black text-white hover:bg-gray-800 border-4 border-black py-4 rounded-2xl font-black text-xl shadow-[6px_6px_0px_0px_rgba(255,159,28,1)] transition-all active:translate-y-1 active:shadow-none"
          >
            RETURN TO COMMAND CENTER
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#F4F1EA] text-black font-[family-name:var(--font-kodchasan)] flex flex-col">
      <nav className="w-full bg-white border-b-4 border-black px-6 py-4 flex items-center justify-between sticky top-0 z-50">
        <div className="flex items-center gap-3">
          <button 
            onClick={() => router.push("/chat")}
            className="p-2 hover:bg-gray-100 rounded-full border-2 border-transparent hover:border-black transition-all"
          >
            <ArrowLeft className="w-5 h-5" />
          </button>
          <div className="w-10 h-10 bg-purple-500 rounded-lg border-2 border-black flex items-center justify-center shadow-[3px_3px_0px_0px_rgba(0,0,0,1)]">
            <Brain className="w-6 h-6 text-white" strokeWidth={2.5} />
          </div>
          <span className="text-xl font-bold tracking-tight">
            Neuro Assessment
          </span>
        </div>
        
        <div className="bg-black text-white px-4 py-1.5 border-2 border-black font-bold rounded-lg shadow-[3px_3px_0px_0px_rgba(168,85,247,1)]">
          QUESTION {currentStep + 1} OF {quiz.questions.length}
        </div>
      </nav>

      <main className="flex-1 flex items-center justify-center p-6 pb-24">
        <div className="max-w-3xl w-full">
          {/* Progress Bar */}
          <div className="w-full h-6 bg-white border-4 border-black rounded-full mb-12 overflow-hidden shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]">
            <div 
              className="h-full bg-purple-500 border-r-4 border-black transition-all duration-500"
              style={{ width: `${((currentStep + 1) / quiz.questions.length) * 100}%` }}
            />
          </div>

          <div className="bg-white border-4 border-black p-8 md:p-12 rounded-3xl shadow-[10px_10px_0px_0px_rgba(0,0,0,1)]">
            <h2 className="text-2xl md:text-3xl font-black mb-10 leading-tight">
              {currentQuestion.question}
            </h2>

            <div className="space-y-4">
              {currentQuestion.options.map((option, i) => (
                <button
                  key={i}
                  onClick={() => handleSelect(option)}
                  className={`w-full text-left p-6 border-4 border-black rounded-2xl font-black text-xl transition-all flex items-center justify-between group
                    ${answers[currentStep] === option 
                      ? "bg-purple-100 translate-x-1 translate-y-1 shadow-none" 
                      : "bg-[#CBF3F0] hover:bg-white shadow-[6px_6px_0px_0px_rgba(0,0,0,1)] active:translate-y-1 active:shadow-none"
                    }`}
                >
                  <span className="flex items-center gap-4">
                    <span className="w-10 h-10 bg-white border-2 border-black rounded-lg flex items-center justify-center group-hover:bg-purple-500 group-hover:text-white transition-colors">
                      {String.fromCharCode(65 + i)}
                    </span>
                    {option}
                  </span>
                  {answers[currentStep] === option && (
                    <CheckCircle2 className="w-8 h-8 text-black" />
                  )}
                </button>
              ))}
            </div>

            <div className="mt-12 flex justify-end">
              <button
                disabled={!answers[currentStep]}
                onClick={handleNext}
                className={`flex items-center gap-3 px-8 py-4 rounded-2xl border-4 border-black font-black text-xl transition-all
                  ${!answers[currentStep] 
                    ? "bg-gray-200 text-gray-400 cursor-not-allowed border-gray-300" 
                    : "bg-[#FF9F1C] hover:bg-[#ff8c00] shadow-[6px_6px_0px_0px_rgba(0,0,0,1)] active:translate-y-1 active:shadow-none"
                  }`}
              >
                {isLastStep ? "SEE RESULTS" : "NEXT QUESTION"}
                <ChevronRight className="w-6 h-6" strokeWidth={3} />
              </button>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
