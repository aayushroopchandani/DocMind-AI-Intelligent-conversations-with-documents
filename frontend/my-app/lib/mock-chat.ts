import type { ChatResponse } from "@/lib/types";

/**
 * Placeholder assistant.
 *
 * This is the single integration seam for the backend. When the real
 * "chat with PDF" endpoint is ready, replace the body of `sendMessage` with a
 * `fetch` call that returns the same `ChatResponse` shape — no UI changes required.
 *
 * For now it returns deterministic, context-flavored mock answers with mocked
 * page citations so the interface can be exercised end to end.
 */

const DELAY_MS = 900;

function pickResponse(question: string, documentName: string): ChatResponse {
  const q = question.toLowerCase();

  if (q.includes("summar")) {
    return {
      content:
        "This document introduces its core subject, develops the key arguments across several sections, and closes with practical takeaways. The early pages frame the problem, the middle sections provide supporting evidence and examples, and the final pages synthesize the conclusions.",
      citations: [
        { documentName, pageNumber: 1, preview: "Introduction and scope of the document." },
        { documentName, pageNumber: 8, preview: "Central argument and supporting evidence." },
        { documentName, pageNumber: 14, preview: "Summary of conclusions." },
      ],
    };
  }

  if (q.includes("key idea") || q.includes("main") || q.includes("topics")) {
    return {
      content:
        "The key ideas center on three themes: the motivation behind the work, the methods used to explore it, and the implications of the findings. Each theme is developed with concrete examples and references to prior work.",
      citations: [
        { documentName, pageNumber: 3, preview: "Motivation and background." },
        { documentName, pageNumber: 9, preview: "Methodology overview." },
      ],
    };
  }

  if (q.includes("conclusion")) {
    return {
      content:
        "The main conclusion is that the proposed approach meaningfully advances the problem it set out to solve, while acknowledging trade-offs. The author recommends further validation in real-world settings.",
      citations: [{ documentName, pageNumber: 14, preview: "Concluding remarks and recommendations." }],
    };
  }

  if (q.includes("limitation")) {
    return {
      content:
        "The stated limitations include a constrained dataset, assumptions that may not generalize to every context, and areas the author flags for future research.",
      citations: [{ documentName, pageNumber: 12, preview: "Limitations and threats to validity." }],
    };
  }

  return {
    content:
      "Based on the document, here is a context-aware answer to your question. The relevant passages appear across the pages cited below, where this topic is discussed in detail.",
    citations: [
      { documentName, pageNumber: 5, preview: "Relevant discussion of the topic." },
      { documentName, pageNumber: 11, preview: "Additional supporting context." },
    ],
  };
}

/**
 * Mocked assistant call. Replace with a real backend request later.
 *
 * @param question       The user's question.
 * @param documentName   Name of the currently-loaded PDF (used for citations).
 */
export async function sendMessage(
  question: string,
  documentName: string,
): Promise<ChatResponse> {
  await new Promise((resolve) => setTimeout(resolve, DELAY_MS));
  return pickResponse(question, documentName);
}

/** Suggested prompts shown as chips in the chat panel. */
export const SUGGESTED_QUESTIONS: readonly string[] = [
  "Summarize this document.",
  "What are the key ideas?",
  "Explain the main conclusion.",
  "What topics are covered?",
  "What are the limitations mentioned?",
] as const;
