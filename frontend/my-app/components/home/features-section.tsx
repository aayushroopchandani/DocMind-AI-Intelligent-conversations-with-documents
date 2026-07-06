"use client";

import {
  MessageSquareText,
  Columns2,
  Quote,
  Sparkles,
  ShieldCheck,
  Zap,
} from "lucide-react";
import { FeatureCard, type FeatureCardProps } from "@/components/home/feature-card";
import { Reveal } from "@/components/reveal";

const FEATURES: FeatureCardProps[] = [
  {
    icon: MessageSquareText,
    title: "Ask questions from your PDF",
    description:
      "Have a natural conversation with any document and get precise answers grounded in its contents.",
  },
  {
    icon: Columns2,
    title: "Read while you chat",
    description:
      "View the original document side by side with the conversation — never lose your place.",
  },
  {
    icon: Quote,
    title: "Source & page citations",
    description:
      "Every answer links back to the exact page it came from, so you can verify in one click.",
  },
  {
    icon: Sparkles,
    title: "Context-aware answers",
    description:
      "Responses understand the surrounding context of your document, not just isolated keywords.",
  },
  {
    icon: ShieldCheck,
    title: "Secure authentication",
    description:
      "Sign in securely with Clerk. Your session and access are protected end to end.",
  },
  {
    icon: Zap,
    title: "Fast document exploration",
    description:
      "Jump to key ideas, summaries, and conclusions in seconds instead of skimming for minutes.",
  },
];

export function FeaturesSection() {
  return (
    <section id="features" className="relative scroll-mt-24 py-24 sm:py-32">
      <div className="mx-auto max-w-6xl px-4">
        <Reveal className="mx-auto max-w-2xl text-center">
          <h2 className="text-balance text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
            Everything you need to understand a document
          </h2>
          <p className="mt-4 text-balance text-muted-foreground">
            DocMind turns dense PDFs into a fast, verifiable conversation.
          </p>
        </Reveal>

        <div className="mt-14 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map((feature, i) => (
            <Reveal key={feature.title} delay={i * 0.06} className="h-full">
              <FeatureCard {...feature} />
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}
