"use client";

import { useUser } from "@clerk/nextjs";
import {
  ChevronsDownUp,
  ChevronsUpDown,
  History,
  Share2,
  Sparkles,
} from "lucide-react";
import { AuthenticatedUserMenu } from "@/components/auth/authenticated-user-menu";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { notifyBackendPending } from "@/lib/data-analysis/feedback";
import { activeArtifact } from "@/lib/data-analysis/workspace-state";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";

/**
 * Right-hand side of the app bar.
 *
 * Undo and redo are not here: Univer's own ribbon carries them a row below,
 * the Edit menu lists them with their shortcuts, and ⌘Z works globally — a
 * third copy would only cost width.
 */
export function AppBarActions() {
  const { state, layout, updateLayout, ui } = useWorkspace();
  const { isSignedIn } = useUser();

  const ribbonCollapsed = layout.ribbonCollapsed;
  // Only a spreadsheet has a ribbon to fold, so the control stays out of the
  // way while a PDF is in front.
  const showRibbonToggle = activeArtifact(state)?.type === "spreadsheet";

  return (
    <div className="flex shrink-0 items-center gap-0.5">
      {showRibbonToggle ? (
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={
                  ribbonCollapsed
                    ? "Show formatting toolbar"
                    : "Hide formatting toolbar"
                }
                aria-pressed={ribbonCollapsed}
                onClick={() =>
                  updateLayout({ ribbonCollapsed: !ribbonCollapsed })
                }
              >
                {ribbonCollapsed ? <ChevronsUpDown /> : <ChevronsDownUp />}
              </Button>
            }
          />
          <TooltipContent>
            {ribbonCollapsed ? "Show toolbar" : "Hide toolbar"}
          </TooltipContent>
        </Tooltip>
      ) : null}

      <Tooltip>
        <TooltipTrigger
          render={
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Run history"
              onClick={() => ui.setHistoryOpen(true)}
            >
              <History />
            </Button>
          }
        />
        <TooltipContent>Run history</TooltipContent>
      </Tooltip>

      <Tooltip>
        <TooltipTrigger
          render={
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Share"
              className="hidden sm:inline-flex"
              onClick={() => notifyBackendPending("Sharing")}
            >
              <Share2 />
            </Button>
          }
        />
        <TooltipContent>Share — backend pending</TooltipContent>
      </Tooltip>

      {/* Below `lg` the analyst panel is an overlay sheet, not a column. */}
      <Tooltip>
        <TooltipTrigger
          render={
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Open AI analyst"
              className="text-[color:var(--accent-cyan)] lg:hidden"
              onClick={() => ui.setAnalystSheetOpen(true)}
            >
              <Sparkles />
            </Button>
          }
        />
        <TooltipContent>AI Analyst</TooltipContent>
      </Tooltip>

      {isSignedIn ? (
        <div className="ml-1">
          <AuthenticatedUserMenu />
        </div>
      ) : null}
    </div>
  );
}
