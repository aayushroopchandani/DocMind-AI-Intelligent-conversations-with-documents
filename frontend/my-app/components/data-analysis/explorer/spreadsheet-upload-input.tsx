"use client";

import type { ChangeEvent, RefObject } from "react";
import { SPREADSHEET_ACCEPT_ATTRIBUTE } from "@/lib/data-analysis/constants";

interface SpreadsheetUploadInputProps {
  inputRef: RefObject<HTMLInputElement | null>;
  onChange: (event: ChangeEvent<HTMLInputElement>) => void;
}

/**
 * The hidden file input behind every "Import spreadsheet" affordance.
 *
 * Visually hidden rather than `display: none` so assistive tech can still
 * reach it, and labelled because it has no visible text of its own.
 */
export function SpreadsheetUploadInput({
  inputRef,
  onChange,
}: SpreadsheetUploadInputProps) {
  return (
    <input
      ref={inputRef}
      type="file"
      accept={SPREADSHEET_ACCEPT_ATTRIBUTE}
      onChange={onChange}
      aria-label="Import an Excel or CSV spreadsheet"
      tabIndex={-1}
      className="sr-only"
    />
  );
}
