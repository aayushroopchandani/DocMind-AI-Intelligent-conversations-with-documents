"use client";

import { useCallback, useRef, useState } from "react";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";

/**
 * Shared spreadsheet picker behaviour, mirroring `usePdfUpload`.
 *
 * Owns a hidden `<input type="file">` so every entry point — the File menu,
 * the explorer "+" menu, the empty state — opens the same dialog and runs the
 * same conversion. The input is reset after each selection so picking the
 * same file twice still fires `change`.
 */
export function useSpreadsheetUpload() {
  const { actions } = useWorkspace();
  const inputRef = useRef<HTMLInputElement>(null);
  const [isImporting, setIsImporting] = useState(false);

  const openFilePicker = useCallback(() => {
    inputRef.current?.click();
  }, []);

  const importFile = useCallback(
    async (file: File | undefined) => {
      if (!file) return;
      setIsImporting(true);
      try {
        await actions.importSpreadsheet(file);
      } finally {
        setIsImporting(false);
      }
    },
    [actions],
  );

  const handleInputChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const [file] = Array.from(event.target.files ?? []);
      event.target.value = "";
      void importFile(file);
    },
    [importFile],
  );

  return { inputRef, isImporting, openFilePicker, importFile, handleInputChange };
}
