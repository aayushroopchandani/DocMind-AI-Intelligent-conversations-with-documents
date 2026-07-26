"use client";

import type { ChangeEvent, RefObject } from "react";
import {
  MAX_PDF_UPLOAD_BATCH,
  PDF_ACCEPT_ATTRIBUTE,
} from "@/lib/data-analysis/constants";

interface PdfUploadInputProps {
  inputRef: RefObject<HTMLInputElement | null>;
  onChange: (event: ChangeEvent<HTMLInputElement>) => void;
}

/**
 * The hidden file input behind every "Upload PDF" affordance.
 *
 * Visually hidden rather than `display: none` so assistive tech can still
 * reach it, and labelled because it has no visible text of its own.
 */
export function PdfUploadInput({ inputRef, onChange }: PdfUploadInputProps) {
  return (
    <input
      ref={inputRef}
      type="file"
      accept={PDF_ACCEPT_ATTRIBUTE}
      multiple
      onChange={onChange}
      aria-label={`Upload up to ${MAX_PDF_UPLOAD_BATCH} PDF documents`}
      tabIndex={-1}
      className="sr-only"
    />
  );
}
