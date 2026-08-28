"use client";

import { useState, useRef, useEffect } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { Brain, ArrowLeft, Send, Loader2 } from "lucide-react";
import { MarkdownMessage } from "@/components/MarkdownMessage";

type ChatMessage = {
  id: string;
  role: "user" | "bot";
  content: string;
  citations?: { claim: string; validation_status: string }[];
  isInsufficient?: boolean;
};

export default function TutorPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const router = useRouter();
  
  const courseId = params.courseId as string;
  const lessonId = searchParams.get("lessonId") || undefined;

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [prompt, setPrompt] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string | undefined>(undefined);

  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isLoading]);

  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!prompt.trim() || isLoading) return;

    const userMessage: ChatMessage = {
      id: Date.now().toString(),
      role: "user",
      content: prompt.trim(),
    };

    setMessages(prev => [...prev, userMessage]);
    setPrompt("");
    setIsLoading(true);

    try {
      const response = await fetch(`/api/v1/courses/${courseId}/tutor`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: userMessage.content,
          context_lesson_id: lessonId,
          conversation_id: conversationId,
        }),
      });

      if (!response.ok) {
        throw new Error("Failed to get response from tutor");
      }

      if (!response.body) throw new Error("No response body");

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      
      const botMessageId = "bot_" + Date.now();
      let botContent = "";
      let isInsufficient = false;
      let citations: { claim: string; validation_status: string }[] = [];

      const appendOrUpdateBotMessage = (content: string, insuff: boolean, cits: { claim: string; validation_status: string }[]) => {
        setMessages(prev => {
          const newMessages = [...prev];
          const lastIdx = newMessages.findIndex(m => m.id === botMessageId);
          if (lastIdx !== -1) {
            newMessages[lastIdx] = { ...newMessages[lastIdx], content, isInsufficient: insuff, citations: cits };
          } else {
            newMessages.push({ id: botMessageId, role: "bot", content, isInsufficient: insuff, citations: cits });
          }
          return newMessages;
        });
      };

      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop() || "";

        for (const eventStr of events) {
          if (!eventStr.trim()) continue;
          
          const eventMatch = eventStr.match(/event: (.*?)\n/);
          const dataMatch = eventStr.match(/data: (.*)/);
          
          if (eventMatch && dataMatch) {
            const event = eventMatch[1];
            const data = JSON.parse(dataMatch[1]);
            
            if (event === "insufficient") {
              isInsufficient = true;
              botContent = data.text;
              appendOrUpdateBotMessage(botContent, isInsufficient, citations);
            } else if (event === "token") {
              botContent += data.text;
              appendOrUpdateBotMessage(botContent, isInsufficient, citations);
            } else if (event === "citation") {
              citations = [...citations, data];
              appendOrUpdateBotMessage(botContent, isInsufficient, citations);
            } else if (event === "done") {
              // The backend doesn't seem to return conversation_id in 'done'
              // but if it did, we'd update it here.
              appendOrUpdateBotMessage(botContent, isInsufficient, citations);
            }
          }
        }
      }
    } catch (err) {
      console.error(err);
      setMessages(prev => [...prev, {
        id: "err_" + Date.now(),
        role: "bot",
        content: "Sorry, an error occurred while connecting to the tutor."
      }]);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="h-screen bg-[#F4F1EA] text-black font-[family-name:var(--font-kodchasan)] flex flex-col overflow-hidden">
      <nav className="w-full bg-white border-b-2 border-black px-6 py-4 flex items-center justify-between flex-shrink-0 z-50">
        <div className="flex items-center gap-4">
          <button
            onClick={() => router.back()}
            className="p-2 hover:bg-gray-100 rounded-full border-2 border-transparent hover:border-black transition-all"
          >
            <ArrowLeft className="w-5 h-5" />
          </button>
          <div className="w-10 h-10 bg-purple-500 rounded-lg border-2 border-black flex items-center justify-center shadow-[3px_3px_0px_0px_rgba(0,0,0,1)]">
            <Brain className="w-6 h-6 text-white" strokeWidth={2.5} />
          </div>
          <span className="text-xl font-bold tracking-tight">Course Tutor</span>
        </div>
      </nav>

      <main className="flex-1 overflow-y-auto">
        <div className="max-w-4xl mx-auto w-full px-4 md:px-6 py-6">
          {messages.length === 0 && (
            <div className="flex flex-col items-center justify-center h-[50vh] text-center">
              <div className="bg-white border-2 border-black p-8 rounded-2xl shadow-[8px_8px_0px_0px_rgba(0,0,0,1)] max-w-md">
                <Brain className="w-16 h-16 text-purple-600 mx-auto mb-4" />
                <h2 className="text-2xl font-bold mb-2">Ask the Tutor</h2>
                <p className="text-gray-600 text-sm">
                  {lessonId 
                    ? "Ask questions specific to this lesson, or general course concepts." 
                    : "Ask any question about your course material. Responses are grounded strictly in your uploaded documents."}
                </p>
              </div>
            </div>
          )}

          <div className="flex flex-col gap-6">
            {messages.map((msg) => (
              <div key={msg.id} className={`flex flex-col ${msg.role === "user" ? "items-end" : "items-start"}`}>
                <div className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"} w-full`}>
                  {msg.role === "bot" && (
                    <div className="w-8 h-8 mr-3 mt-1 bg-purple-500 rounded-lg border-2 border-black flex-shrink-0 flex items-center justify-center shadow-[2px_2px_0px_0px_rgba(0,0,0,1)]">
                      <Brain className="w-5 h-5 text-white" />
                    </div>
                  )}
                  <div className={`max-w-[82%] p-4 rounded-xl border-2 border-black shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] ${
                    msg.role === "user" ? "bg-purple-100 rounded-br-none" : "bg-white rounded-bl-none"
                  }`}>
                    {msg.role === "bot" ? (
                      <div>
                        {msg.isInsufficient && (
                          <div className="mb-2 inline-block bg-orange-100 border-2 border-orange-500 text-orange-700 px-2 py-1 text-xs font-bold rounded">
                            UNVERIFIED / OUT OF SCOPE
                          </div>
                        )}
                        <MarkdownMessage content={msg.content || "..."} />
                        {msg.citations && msg.citations.length > 0 && (
                          <div className="mt-4 pt-4 border-t border-gray-200">
                            <h4 className="text-sm font-bold text-gray-500 mb-2">Sources</h4>
                            <ul className="space-y-1">
                              {msg.citations.map((cit, idx) => (
                                <li key={idx} className="text-xs text-gray-600">
                                  [{idx + 1}] {cit.claim} ({cit.validation_status})
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}
                      </div>
                    ) : (
                      <p className="text-sm md:text-base whitespace-pre-wrap">{msg.content}</p>
                    )}
                  </div>
                </div>
              </div>
            ))}
            {isLoading && (
              <div className="flex justify-start">
                <div className="w-8 h-8 mr-3 mt-1 bg-purple-500 rounded-lg border-2 border-black flex-shrink-0 flex items-center justify-center shadow-[2px_2px_0px_0px_rgba(0,0,0,1)]">
                  <Brain className="w-5 h-5 text-white" />
                </div>
                <div className="p-4 rounded-xl border-2 border-black bg-white rounded-bl-none shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] flex items-center gap-2">
                  <Loader2 className="w-5 h-5 text-purple-500 animate-spin" />
                </div>
              </div>
            )}
            <div ref={messagesEndRef} className="h-4" />
          </div>
        </div>
      </main>

      <div className="flex-shrink-0 w-full bg-[#F4F1EA] border-t-2 border-black px-4 py-4 pb-8">
        <form onSubmit={handleSubmit} className="max-w-3xl mx-auto flex items-center bg-white border-2 border-black rounded-full shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] px-4 py-2">
          <input
            type="text"
            placeholder="Ask a question..."
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            disabled={isLoading}
            className="flex-1 outline-none px-3 py-2 bg-transparent text-sm md:text-base disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={isLoading || !prompt.trim()}
            className="p-2 bg-black text-white rounded-full hover:bg-gray-800 transition-colors disabled:opacity-40 disabled:cursor-not-allowed flex-shrink-0"
          >
            <Send className="w-4 h-4" />
          </button>
        </form>
      </div>
    </div>
  );
}
