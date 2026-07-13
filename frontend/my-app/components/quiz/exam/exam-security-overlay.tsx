"use client";

import { ShieldAlert } from "lucide-react";
import { VIOLATION_LABELS, type ViolationType } from "@/lib/quiz/use-proctoring";
import { Button } from "@/components/ui/button";

interface ExamSecurityOverlayProps {
  violationType: ViolationType | null;
  violations: number;
  maxViolations: number;
  onResume: () => void;
}

/**
 * Full-screen blocking overlay shown the moment a proctoring violation is
 * detected. The exam clock keeps running underneath — leaving costs time — and
 * the learner must explicitly acknowledge before continuing.
 */
export function ExamSecurityOverlay({
  violationType,
  violations,
  maxViolations,
  onResume,
}: ExamSecurityOverlayProps) {
  const remaining = Math.max(0, maxViolations - violations);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/85 backdrop-blur-md">
      <div className="mx-4 max-w-md rounded-2xl border border-[color:var(--quiz-incorrect)]/40 bg-card p-8 text-center">
        <div className="mx-auto flex size-14 items-center justify-center rounded-full bg-[color:var(--quiz-incorrect)]/12">
          <ShieldAlert className="size-7 text-quiz-incorrect" />
        </div>
        <h2 className="mt-4 text-lg font-semibold text-foreground">
          Exam paused
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">
          {violationType
            ? VIOLATION_LABELS[violationType]
            : "A proctoring violation was detected"}
          . This has been recorded and the timer is still running.
        </p>

        <div className="mt-4 rounded-lg border border-border bg-background/60 px-3 py-2 text-sm">
          <span className="font-semibold text-quiz-incorrect">
            {violations}
          </span>{" "}
          <span className="text-muted-foreground">
            violation{violations === 1 ? "" : "s"} ·{" "}
            {remaining > 0
              ? `${remaining} more before auto-submit`
              : "auto-submitting"}
          </span>
        </div>

        <Button onClick={onResume} className="mt-6 h-10 w-full font-semibold">
          Return to exam
        </Button>
      </div>
    </div>
  );
}
