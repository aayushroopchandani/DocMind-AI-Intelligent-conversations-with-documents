"use client";

import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactElement,
} from "react";
import {
  ChevronDown,
  ChevronUp,
  Loader2,
  Search as SearchIcon,
  X,
} from "lucide-react";
import { MatchFlag } from "@embedpdf/models";
import { useScrollCapability } from "@embedpdf/plugin-scroll/react";
import { useSearch } from "@embedpdf/plugin-search/react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

interface PdfSearchPanelProps {
  documentId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  trigger: ReactElement;
}

const DEBOUNCE_MS = 220;

/**
 * In-document search in a popover.
 *
 * Matches are highlighted by EmbedPDF's `<SearchLayer />` on each page; this
 * panel owns the query, the match counter, result navigation and the hit list.
 *
 * Scanned PDFs have no text layer, so PDFium finds nothing. That case reports
 * an honest "no matches" with an explanation rather than implying the search
 * ran against image content — no OCR is attempted anywhere.
 */
export function PdfSearchPanel({
  documentId,
  open,
  onOpenChange,
  trigger,
}: PdfSearchPanelProps) {
  const { state, provides: search } = useSearch(documentId);
  const { provides: scrollCapability } = useScrollCapability();
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");

  /* ---------------- debounced query ---------------- */

  useEffect(() => {
    if (!search || !open) return;
    const trimmed = query.trim();
    if (trimmed.length === 0) {
      search.stopSearch();
      return;
    }
    const timer = setTimeout(() => {
      search.startSearch();
      search.searchAllPages(trimmed);
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query, search, open]);

  // Closing the panel clears highlights so stale matches never linger.
  useEffect(() => {
    if (open) {
      inputRef.current?.focus();
      inputRef.current?.select();
    } else {
      search?.stopSearch();
    }
  }, [open, search]);

  /* ---------------- keep the active hit in view ---------------- */

  useEffect(() => {
    if (state.loading) return;
    const result = state.results[state.activeResultIndex];
    if (!result) return;
    const topLeft = result.rects.reduce(
      (min, rect) => ({
        x: Math.min(min.x, rect.origin.x),
        y: Math.min(min.y, rect.origin.y),
      }),
      { x: Infinity, y: Infinity },
    );
    scrollCapability?.forDocument(documentId).scrollToPage({
      pageNumber: result.pageIndex + 1,
      pageCoordinates: topLeft,
      alignX: 50,
      alignY: 40,
    });
    // Re-running on `results` identity is what makes a new query jump to its
    // first hit; `activeResultIndex` handles next/previous.
  }, [
    state.activeResultIndex,
    state.results,
    state.loading,
    documentId,
    scrollCapability,
  ]);

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onOpenChange(false);
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      if (state.total === 0) return;
      if (event.shiftKey) search?.previousResult();
      else search?.nextResult();
    }
  };

  const toggleFlag = (flag: MatchFlag, enabled: boolean) => {
    if (!search) return;
    search.setFlags(
      enabled
        ? [...state.flags, flag]
        : state.flags.filter((item) => item !== flag),
    );
  };

  const hasQuery = query.trim().length > 0;
  const showNoMatches = hasQuery && !state.loading && state.total === 0;

  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger render={trigger} />
      <PopoverContent
        align="end"
        className="w-80 gap-0 p-0"
        aria-label="Search document"
      >
        <div className="flex items-center gap-1.5 p-2">
          <div className="relative flex-1">
            <SearchIcon className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              ref={inputRef}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Search this document…"
              aria-label="Search this document"
              className="h-7 pl-7 pr-7 text-xs"
            />
            {hasQuery ? (
              <button
                type="button"
                aria-label="Clear search"
                onClick={() => {
                  setQuery("");
                  inputRef.current?.focus();
                }}
                className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50"
              >
                <X className="size-3" />
              </button>
            ) : null}
          </div>
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label="Previous match"
            disabled={state.total === 0}
            onClick={() => search?.previousResult()}
          >
            <ChevronUp />
          </Button>
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label="Next match"
            disabled={state.total === 0}
            onClick={() => search?.nextResult()}
          >
            <ChevronDown />
          </Button>
        </div>

        <div className="flex items-center gap-3 px-2 pb-2">
          <FlagCheckbox
            label="Case sensitive"
            checked={state.flags.includes(MatchFlag.MatchCase)}
            onChange={(checked) => toggleFlag(MatchFlag.MatchCase, checked)}
          />
          <FlagCheckbox
            label="Whole word"
            checked={state.flags.includes(MatchFlag.MatchWholeWord)}
            onChange={(checked) => toggleFlag(MatchFlag.MatchWholeWord, checked)}
          />
        </div>

        {hasQuery ? (
          <>
            <Separator />
            <div
              aria-live="polite"
              className="flex h-7 items-center justify-between px-2 text-[11px] text-muted-foreground"
            >
              {state.loading ? (
                <span className="flex items-center gap-1.5">
                  <Loader2 className="size-3 animate-spin" />
                  Searching…
                </span>
              ) : (
                <span className="tabular-nums">
                  {state.total === 0
                    ? "No matches"
                    : `${state.activeResultIndex + 1} of ${state.total} matches`}
                </span>
              )}
            </div>
          </>
        ) : null}

        {showNoMatches ? (
          <div className="border-t border-border px-2 py-2.5">
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              Nothing matched “{query.trim()}”. Scanned PDFs have no text layer,
              so search and text selection find nothing in them — text
              recognition is not part of this milestone.
            </p>
          </div>
        ) : null}

        {state.results.length > 0 && !state.loading ? (
          <>
            <Separator />
            {/*
              A plain scroll container rather than <ScrollArea>: Base UI's
              viewport is `height: 100%`, which needs a definite height on the
              root — with only `max-height` the list would overflow the
              popover instead of scrolling inside it.
            */}
            <div className="scrollbar-thin max-h-56 overflow-y-auto overscroll-contain">
              <div className="flex flex-col gap-0.5 p-1.5">
                {state.results.map((result, index) => (
                  <button
                    key={`${result.pageIndex}-${index}`}
                    type="button"
                    onClick={() => search?.goToResult(index)}
                    aria-current={index === state.activeResultIndex}
                    className={cn(
                      "rounded-md border px-2 py-1.5 text-left text-[11px] leading-relaxed outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/50",
                      index === state.activeResultIndex
                        ? "border-[color:var(--accent-cyan)]/40 bg-muted/70 text-foreground"
                        : "border-transparent text-muted-foreground hover:bg-muted/40 hover:text-foreground",
                    )}
                  >
                    <span className="mb-0.5 block text-[10px] uppercase tracking-wider text-muted-foreground/70">
                      Page {result.pageIndex + 1}
                    </span>
                    <span>
                      {result.context.truncatedLeft ? "… " : null}
                      {result.context.before}
                      <mark className="rounded bg-[color:var(--accent-cyan)]/25 px-0.5 text-foreground">
                        {result.context.match}
                      </mark>
                      {result.context.after}
                      {result.context.truncatedRight ? " …" : null}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          </>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}

/** Native checkbox, styled — cheaper than pulling in another shadcn part. */
function FlagCheckbox({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-center gap-1.5 text-[11px] text-muted-foreground select-none hover:text-foreground">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="size-3 accent-[color:var(--accent-cyan)]"
      />
      {label}
    </label>
  );
}
