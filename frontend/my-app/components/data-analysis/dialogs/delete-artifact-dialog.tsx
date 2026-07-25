"use client";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";

/** Confirmed, destructive delete: removes the artifact and its local draft. */
export function DeleteArtifactDialog() {
  const { actions, ui } = useWorkspace();
  const target = ui.deleteTarget;

  const close = () => ui.setDeleteTargetId(null);

  return (
    <AlertDialog open={Boolean(target)} onOpenChange={(open) => !open && close()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            Delete “{target?.name ?? "spreadsheet"}”?
          </AlertDialogTitle>
          <AlertDialogDescription>
            This removes the spreadsheet and its locally saved draft. This
            action cannot be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel onClick={close}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={() => {
              if (target) actions.deleteArtifact(target.id);
              close();
            }}
          >
            Delete
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
