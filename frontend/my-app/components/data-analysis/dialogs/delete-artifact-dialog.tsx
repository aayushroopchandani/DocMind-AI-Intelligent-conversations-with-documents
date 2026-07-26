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

/** Confirmed, destructive delete: removes the artifact and its local data. */
export function DeleteArtifactDialog() {
  const { actions, ui } = useWorkspace();
  const target = ui.deleteTarget;
  const isPdf = target?.type === "pdf";

  const close = () => ui.setDeleteTargetId(null);

  return (
    <AlertDialog open={Boolean(target)} onOpenChange={(open) => !open && close()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            Delete “{target?.name ?? "this file"}”?
          </AlertDialogTitle>
          <AlertDialogDescription>
            {isPdf
              ? "This closes the document and erases the copy stored in this browser. This action cannot be undone."
              : "This removes the spreadsheet and its locally saved draft. This action cannot be undone."}
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
