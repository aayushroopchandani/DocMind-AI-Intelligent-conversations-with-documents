import { FileText, Sparkles, Quote } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Static, presentational mockup of the DocMind workspace: a PDF viewer on the
 * left and an AI chat (with citation cards) on the right. Pure markup — no
 * interactivity — so it can render as a server component in marketing sections.
 */
export function ProductMockup({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "glass overflow-hidden rounded-2xl shadow-2xl shadow-black/40",
        className,
      )}
    >
      {/* Window chrome */}
      <div className="flex items-center gap-2 border-b border-border px-4 py-3">
        <span className="size-3 rounded-full bg-muted-foreground/30" />
        <span className="size-3 rounded-full bg-muted-foreground/20" />
        <span className="size-3 rounded-full bg-muted-foreground/10" />
        <div className="ml-3 flex items-center gap-2 text-xs text-muted-foreground">
          <FileText className="size-3.5" />
          Building-ML-Systems.pdf
        </div>
      </div>

      <div className="grid grid-cols-1 gap-px bg-border sm:grid-cols-2">
        {/* PDF pane */}
        <div className="bg-card p-4">
          <div className="bg-grid mx-auto aspect-[3/4] w-full rounded-lg border border-border bg-background/60 p-4">
            <div className="space-y-2">
              <div className="h-2.5 w-2/3 rounded bg-foreground/25" />
              <div className="h-2 w-full rounded bg-foreground/10" />
              <div className="h-2 w-11/12 rounded bg-foreground/10" />
              <div className="h-2 w-4/5 rounded bg-foreground/10" />
              <div className="mt-4 h-24 w-full rounded bg-foreground/[0.06]" />
              <div className="h-2 w-full rounded bg-foreground/10" />
              <div className="h-2 w-10/12 rounded bg-foreground/10" />
              <div className="h-2 w-9/12 rounded bg-foreground/10" />
            </div>
          </div>
          <div className="mt-3 flex items-center justify-center gap-2 text-[11px] text-muted-foreground">
            <span>Page 12 / 184</span>
          </div>
        </div>

        {/* Chat pane */}
        <div className="flex flex-col gap-3 bg-card p-4">
          <div className="ml-auto max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-3 py-2 text-xs font-medium text-primary-foreground">
            What are the key ideas in this document?
          </div>

          <div className="max-w-[92%] rounded-2xl rounded-bl-sm border border-border bg-background/60 px-3 py-2.5">
            <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
              <Sparkles className="size-3" />
              DocMind
            </div>
            <p className="text-xs leading-relaxed text-foreground/80">
              The document centers on three themes: motivation, methodology, and
              the practical implications of the findings.
            </p>

            <div className="mt-3 grid gap-1.5">
              {[
                { page: 3, preview: "Motivation and background" },
                { page: 9, preview: "Methodology overview" },
              ].map((c) => (
                <div
                  key={c.page}
                  className="flex items-center gap-2 rounded-lg border border-border bg-card px-2.5 py-1.5"
                >
                  <Quote className="size-3 shrink-0 text-muted-foreground" />
                  <span className="text-[11px] font-medium text-foreground/70">
                    Page {c.page}
                  </span>
                  <span className="truncate text-[11px] text-muted-foreground">
                    {c.preview}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
