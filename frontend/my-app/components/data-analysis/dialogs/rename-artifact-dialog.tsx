"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import type { ArtifactMeta } from "@/lib/data-analysis/types";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";

/** Renames an artifact everywhere: explorer, workspace tab and workbook. */
export function RenameArtifactDialog() {
  const { actions, ui } = useWorkspace();
  const target = ui.renameTarget;

  const close = () => ui.setRenameTargetId(null);

  return (
    <Dialog open={Boolean(target)} onOpenChange={(open) => !open && close()}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Rename spreadsheet</DialogTitle>
          <DialogDescription>
            The name updates in the file explorer, the workspace tab and the
            workbook itself.
          </DialogDescription>
        </DialogHeader>
        {target ? (
          // Keyed per artifact so the draft resets whenever a different
          // artifact is being renamed — no state-sync effects needed.
          <RenameForm
            key={target.id}
            target={target}
            onRename={actions.renameArtifact}
            onClose={close}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function RenameForm({
  target,
  onRename,
  onClose,
}: {
  target: ArtifactMeta;
  onRename: (id: string, name: string) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState(target.name);

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (name.trim()) onRename(target.id, name);
        onClose();
      }}
    >
      <Input
        autoFocus
        value={name}
        onChange={(event) => setName(event.target.value)}
        aria-label="Spreadsheet name"
        placeholder="Spreadsheet name"
      />
      <DialogFooter className="mt-4">
        <Button type="button" variant="outline" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" disabled={!name.trim()}>
          Rename
        </Button>
      </DialogFooter>
    </form>
  );
}
