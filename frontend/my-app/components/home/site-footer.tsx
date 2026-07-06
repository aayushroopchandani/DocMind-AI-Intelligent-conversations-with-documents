import Link from "next/link";
import { BrainCircuit } from "lucide-react";

/** Inline GitHub mark (lucide v1 dropped brand icons). */
function GithubMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden
      className={className}
    >
      <path d="M12 .5C5.73.5.5 5.73.5 12a11.5 11.5 0 0 0 7.86 10.92c.58.1.79-.25.79-.56v-2c-3.2.7-3.88-1.54-3.88-1.54-.53-1.34-1.3-1.7-1.3-1.7-1.06-.72.08-.71.08-.71 1.17.08 1.79 1.2 1.79 1.2 1.04 1.79 2.73 1.27 3.4.97.1-.76.41-1.27.74-1.56-2.55-.29-5.24-1.28-5.24-5.68 0-1.26.45-2.28 1.19-3.08-.12-.29-.52-1.46.11-3.05 0 0 .97-.31 3.18 1.18a11 11 0 0 1 5.8 0c2.2-1.49 3.17-1.18 3.17-1.18.63 1.59.23 2.76.11 3.05.74.8 1.19 1.82 1.19 3.08 0 4.41-2.69 5.38-5.25 5.67.42.36.8 1.08.8 2.18v3.23c0 .31.21.67.8.56A11.5 11.5 0 0 0 23.5 12C23.5 5.73 18.27.5 12 .5Z" />
    </svg>
  );
}

export function SiteFooter() {
  const year = new Date().getFullYear();

  return (
    <footer className="border-t border-border bg-card/30">
      <div className="mx-auto max-w-6xl px-4 py-14">
        <div className="flex flex-col justify-between gap-10 md:flex-row">
          <div className="max-w-sm">
            <Link
              href="/"
              className="flex items-center gap-2 text-base font-semibold tracking-tight text-foreground"
            >
              <BrainCircuit className="size-5" />
              DocMind
            </Link>
            <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
              Chat with your documents intelligently. Upload a PDF, ask
              questions, and get context-aware answers with source citations.
            </p>
          </div>

          <nav className="flex gap-16">
            <div className="flex flex-col gap-3 text-sm">
              <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground/70">
                Product
              </span>
              <a
                href="#features"
                className="text-muted-foreground transition-colors hover:text-foreground"
              >
                Features
              </a>
              <a
                href="#how-it-works"
                className="text-muted-foreground transition-colors hover:text-foreground"
              >
                How It Works
              </a>
              <Link
                href="/chat"
                className="text-muted-foreground transition-colors hover:text-foreground"
              >
                Chat with PDF
              </Link>
            </div>

            <div className="flex flex-col gap-3 text-sm">
              <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground/70">
                More
              </span>
              <a
                href="https://github.com"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 text-muted-foreground transition-colors hover:text-foreground"
              >
                <GithubMark className="size-3.5" />
                GitHub
              </a>
              <a
                href="#"
                className="text-muted-foreground transition-colors hover:text-foreground"
              >
                Privacy
              </a>
            </div>
          </nav>
        </div>

        <div className="mt-12 border-t border-border pt-6 text-xs text-muted-foreground">
          © {year} DocMind. All rights reserved.
        </div>
      </div>
    </footer>
  );
}
