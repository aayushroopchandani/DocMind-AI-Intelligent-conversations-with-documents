import { notFound } from "next/navigation";
import { UniverContractRunner } from "@/components/data-analysis/dev/univer-contract-runner";

/**
 * The Univer adapter contract suite (Phase 9.13.4).
 *
 * Univer's undo behaviour needs a renderer and a DOM, so the suite cannot run
 * in Node alongside the other verify scripts — it runs here, in a browser.
 *
 * Development only: it creates and disposes workbooks, and it is a maintenance
 * tool rather than product surface. A production build still emits the route's
 * lazy chunk — Next has no route-level exclusion — but the guard below means it
 * never renders, so nothing ever fetches it. There is nothing sensitive in it
 * either: it operates on blank workbooks it makes itself.
 */
export const dynamic = "force-dynamic";

export default function Page() {
  if (process.env.NODE_ENV === "production") notFound();
  return <UniverContractRunner />;
}
