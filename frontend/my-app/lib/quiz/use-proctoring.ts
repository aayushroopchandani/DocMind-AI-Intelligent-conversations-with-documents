"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type ViolationType =
  | "tab-hidden"
  | "window-blur"
  | "fullscreen-exit"
  | "blocked-key"
  | "context-menu";

export const VIOLATION_LABELS: Record<ViolationType, string> = {
  "tab-hidden": "You switched away from the exam tab",
  "window-blur": "The exam window lost focus",
  "fullscreen-exit": "You exited full-screen mode",
  "blocked-key": "A blocked shortcut was pressed",
  "context-menu": "The right-click menu was blocked",
};

interface ProctoringOptions {
  /** Only monitors while true (i.e. during the exam, not intro/review). */
  active: boolean;
  onViolation: (type: ViolationType) => void;
}

interface Proctoring {
  isFullscreen: boolean;
  enterFullscreen: () => Promise<void>;
  exitFullscreen: () => Promise<void>;
}

// Copy / print / save / new-tab / close / reload shortcuts we actively block.
const BLOCKED_KEYS = new Set(["c", "v", "x", "p", "s", "u", "t", "w", "r", "n"]);

/**
 * Best-effort exam proctoring for the browser.
 *
 * NOTE ON LIMITS: a web page cannot truly prevent a user from leaving — the OS
 * and browser own alt-tab, the address bar, and dev-tools. What we *can* do is
 * (1) hold the page in full-screen, (2) detect every focus/visibility/full-screen
 * change and report it as a violation, and (3) suppress the common copy / print /
 * context-menu affordances. The orchestrator decides how to penalise.
 */
export function useProctoring({
  active,
  onViolation,
}: ProctoringOptions): Proctoring {
  const [isFullscreen, setIsFullscreen] = useState(false);
  const onViolationRef = useRef(onViolation);

  useEffect(() => {
    onViolationRef.current = onViolation;
  }, [onViolation]);

  const enterFullscreen = useCallback(async () => {
    try {
      if (!document.fullscreenElement) {
        await document.documentElement.requestFullscreen();
      }
    } catch {
      // Full-screen can be refused (e.g. no user gesture); fail soft.
    }
  }, []);

  const exitFullscreen = useCallback(async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    if (!active) return;

    const report = (type: ViolationType) => onViolationRef.current(type);

    const onVisibility = () => {
      if (document.hidden) report("tab-hidden");
    };
    const onBlur = () => report("window-blur");
    const onFullscreenChange = () => {
      const fs = Boolean(document.fullscreenElement);
      setIsFullscreen(fs);
      if (!fs) report("fullscreen-exit");
    };
    const onContextMenu = (e: MouseEvent) => {
      e.preventDefault();
      report("context-menu");
    };
    const onCopy = (e: Event) => e.preventDefault();
    const onKeyDown = (e: KeyboardEvent) => {
      const combo = e.ctrlKey || e.metaKey;
      const key = e.key.toLowerCase();
      if (
        e.key === "F12" ||
        (combo && BLOCKED_KEYS.has(key)) ||
        (combo && e.shiftKey && (key === "i" || key === "j" || key === "c"))
      ) {
        e.preventDefault();
        report("blocked-key");
      }
    };
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };

    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("blur", onBlur);
    document.addEventListener("fullscreenchange", onFullscreenChange);
    document.addEventListener("contextmenu", onContextMenu);
    document.addEventListener("copy", onCopy);
    document.addEventListener("cut", onCopy);
    window.addEventListener("keydown", onKeyDown, { capture: true });
    window.addEventListener("beforeunload", onBeforeUnload);

    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("blur", onBlur);
      document.removeEventListener("fullscreenchange", onFullscreenChange);
      document.removeEventListener("contextmenu", onContextMenu);
      document.removeEventListener("copy", onCopy);
      document.removeEventListener("cut", onCopy);
      window.removeEventListener("keydown", onKeyDown, { capture: true });
      window.removeEventListener("beforeunload", onBeforeUnload);
    };
  }, [active]);

  return { isFullscreen, enterFullscreen, exitFullscreen };
}
