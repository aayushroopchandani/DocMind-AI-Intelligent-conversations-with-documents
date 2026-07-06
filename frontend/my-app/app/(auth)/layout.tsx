import Link from "next/link";
import { BrainCircuit } from "lucide-react";

/**
 * Shared shell for Clerk's sign-in / sign-up screens: centered card on the
 * same monochrome, subtly-gridded background used across the product.
 */
export default function AuthLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <div className="relative flex min-h-dvh flex-col items-center justify-center overflow-hidden bg-background px-4 py-16">
      <div className="bg-grid pointer-events-none absolute inset-0 opacity-40" />
      <div
        className="pointer-events-none absolute left-1/2 top-1/3 h-[42rem] w-[42rem] -translate-x-1/2 rounded-full opacity-30 blur-3xl"
        style={{
          background:
            "radial-gradient(circle, color-mix(in oklch, var(--foreground) 14%, transparent), transparent 70%)",
        }}
      />

      <div className="relative z-10 flex w-full flex-col items-center gap-8">
        <Link
          href="/"
          className="flex items-center gap-2 text-lg font-semibold tracking-tight text-foreground transition-opacity hover:opacity-80"
        >
          <BrainCircuit className="size-6" />
          DocMind
        </Link>
        {children}
      </div>
    </div>
  );
}
