"use client";

import { useEffect, useRef, useState } from "react";
import { registerProjectNameFocus } from "@/lib/data-analysis/project-name-focus";

interface ProjectNameInputProps {
  name: string;
  onCommit: (name: string) => void;
}

/**
 * Inline-editable project name.
 *
 * Escape restores the committed value and Enter commits — neither reaches
 * the window-level undo/redo handler, which deliberately ignores text
 * fields. "File → Rename project…" focuses this field through the
 * registration below.
 */
export function ProjectNameInput({ name, onCommit }: ProjectNameInputProps) {
  const [value, setValue] = useState(name);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(
    () =>
      registerProjectNameFocus(() => {
        inputRef.current?.focus();
        inputRef.current?.select();
      }),
    [],
  );

  return (
    <input
      ref={inputRef}
      value={value}
      onChange={(event) => setValue(event.target.value)}
      onBlur={() => onCommit(value)}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          onCommit(value);
          inputRef.current?.blur();
        }
        if (event.key === "Escape") {
          setValue(name);
          inputRef.current?.blur();
        }
      }}
      aria-label="Project name"
      spellCheck={false}
      className="h-7 w-24 min-w-0 truncate rounded-md border border-transparent bg-transparent px-1.5 text-[13px] font-medium text-foreground outline-none transition-colors hover:border-border focus-visible:border-border focus-visible:bg-card sm:w-36 lg:w-44"
    />
  );
}
