"use client";

import { useCallback, useRef, useState } from "react";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";

/**
 * Shared PDF picker behaviour.
 *
 * Owns a hidden `<input type="file">` so every entry point — the explorer
 * "+" menu, the tab strip, the empty state, the collapsed rail — opens the
 * same dialog and runs the same validation. The input is reset after each
 * selection so picking the same file twice still fires `change`.
 */
export function usePdfUpload() {
  const { actions } = useWorkspace();
  const inputRef = useRef<HTMLInputElement>(null);
  const [isAdding, setIsAdding] = useState(false);

  const openFilePicker = useCallback(() => {
    inputRef.current?.click();
  }, []);

  const addFiles = useCallback(
    async (files: readonly File[]) => {
      if (files.length === 0) return;
      setIsAdding(true);
      try {
        await actions.addPdfFiles(files);
      } finally {
        setIsAdding(false);
      }
    },
    [actions],
  );

  const handleInputChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(event.target.files ?? []);
      event.target.value = "";
      void addFiles(files);
    },
    [addFiles],
  );

  return { inputRef, isAdding, openFilePicker, addFiles, handleInputChange };
}
