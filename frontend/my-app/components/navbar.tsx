"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { BrainCircuit, ArrowRight } from "lucide-react";
import { Show, SignInButton } from "@clerk/nextjs";
import { Button } from "@/components/ui/button";
import { AuthenticatedUserMenu } from "@/components/auth/authenticated-user-menu";
import { cn } from "@/lib/utils";

const NAV_LINKS = [
  { label: "Features", href: "#features" },
  { label: "How It Works", href: "#how-it-works" },
] as const;

/**
 * Sticky top navigation. Transparent over the hero, then transitions to a
 * frosted-glass bar once the user scrolls past a small threshold.
 */
export function Navbar() {
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 16);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <header
      className={cn(
        "fixed inset-x-0 top-0 z-50 transition-all duration-300",
        scrolled ? "py-2" : "py-4",
      )}
    >
      <div className="mx-auto max-w-6xl px-4">
        <nav
          className={cn(
            "flex items-center justify-between rounded-2xl px-4 py-2.5 transition-all duration-300",
            scrolled
              ? "glass shadow-lg shadow-black/20"
              : "border border-transparent",
          )}
        >
          <Link
            href="/"
            className="flex items-center gap-2 text-base font-semibold tracking-tight text-foreground"
          >
            <BrainCircuit className="size-5" />
            DocMind
          </Link>

          <div className="hidden items-center gap-1 md:flex">
            {NAV_LINKS.map((link) => (
              <Button
                key={link.href}
                variant="ghost"
                size="sm"
                nativeButton={false}
                render={<a href={link.href} />}
                className="text-muted-foreground hover:text-foreground"
              >
                {link.label}
              </Button>
            ))}
          </div>

          <div className="flex items-center gap-2">
            <Show when="signed-out">
              <SignInButton mode="modal">
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-muted-foreground hover:text-foreground"
                >
                  Sign In
                </Button>
              </SignInButton>
            </Show>

            <Button
              size="sm"
              nativeButton={false}
              render={<Link href="/chat" />}
              className="gap-1.5"
              data-icon="inline-end"
            >
              Chat with PDF
              <ArrowRight className="size-3.5" />
            </Button>

            <Show when="signed-in">
              <AuthenticatedUserMenu showDashboardLink />
            </Show>
          </div>
        </nav>
      </div>
    </header>
  );
}
