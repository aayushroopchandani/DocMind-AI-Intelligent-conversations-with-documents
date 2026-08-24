"use client";

import { useEffect, useRef, useState } from "react";
import { CommandType, LocaleType } from "@univerjs/core";
import { UniverSheetsCorePreset } from "@univerjs/preset-sheets-core";
import sheetsCoreEnUS from "@univerjs/preset-sheets-core/locales/en-US";
import { createUniver } from "@/lib/data-analysis/create-univer";
import { createUniverPatchAdapter } from "@/lib/data-analysis/patches/univer-patch-adapter";
import { createRevisionCoordinator } from "@/lib/data-analysis/patches/revision-coordinator";
import {
  runUniverContract,
  type ContractReport,
} from "@/lib/data-analysis/patches/univer-contract";
import { createBlankWorkbookData } from "@/lib/data-analysis/workbook-factory";

import "@univerjs/preset-sheets-core/lib/index.css";

/**
 * Boots a private Univer instance and runs the adapter contract against it.
 *
 * A private instance rather than the workspace's: the suite creates, mutates
 * and disposes several workbooks, and must not touch anything a user has open.
 */

/** Univer applies commands through its render loop; let it catch up. */
const SETTLE_MS = 90;
const BOOT_MS = 700;

export function UniverContractRunner() {
  const containerRef = useRef<HTMLDivElement>(null);
  const [report, setReport] = useState<ContractReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    let cancelled = false;

    const run = async () => {
      const { univer, univerAPI } = createUniver({
        darkMode: true,
        locale: LocaleType.EN_US,
        locales: { [LocaleType.EN_US]: sheetsCoreEnUS },
        presets: [UniverSheetsCorePreset({ container, disableAutoFocus: true })],
      });
      const injector = univer.__getInjector();
      const adapter = createUniverPatchAdapter({ api: univerAPI, injector });
      const settle = () =>
        new Promise<void>((resolve) => setTimeout(resolve, SETTLE_MS));

      // A coordinator wired the same way the workbook host wires it, so the
      // composition is measured against real Univer mutation traffic.
      const hostCalls: string[] = [];
      const coordinator = createRevisionCoordinator({
        settle: (unitId) => hostCalls.push(`settle:${unitId}`),
        commit: (unitId) => hostCalls.push(`commit:${unitId}`),
      });
      const mutationListener = univerAPI.addEvent(
        univerAPI.Event.CommandExecuted,
        (event) => {
          const { type, params } = event as {
            type: CommandType;
            params?: { unitId?: string };
          };
          if (type !== CommandType.MUTATION) return;
          const unitId =
            params?.unitId ?? univerAPI.getActiveWorkbook()?.getId() ?? null;
          if (unitId) coordinator.absorbMutation(unitId);
        },
      );

      try {
        await new Promise((resolve) => setTimeout(resolve, BOOT_MS));
        const result = await runUniverContract({
          api: univerAPI,
          adapter,
          settle,
          createWorkbook: async (name) => {
            const unitId = `${name}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
            univerAPI.createWorkbook(createBlankWorkbookData(unitId, name), {
              makeCurrent: true,
            });
            await settle();
            return unitId;
          },
          disposeWorkbook: (unitId) => univerAPI.disposeUnit(unitId),
          coordinator,
          hostCalls,
        });
        if (!cancelled) setReport(result);
      } catch (caught) {
        if (!cancelled) {
          setError(
            caught instanceof Error
              ? (caught.stack ?? caught.message)
              : String(caught),
          );
        }
      } finally {
        mutationListener?.dispose();
        univerAPI.dispose();
      }
    };

    void run();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="min-h-screen bg-background p-6 text-foreground">
      <h1 className="text-lg font-semibold">Univer patch adapter contract</h1>
      <p className="mt-1 max-w-prose text-sm text-muted-foreground">
        Run this after any Univer upgrade. It proves one AI patch stays one undo
        — and that undo reverses dependent operations in the right order, which
        Univer&rsquo;s own batching helper does not.
      </p>

      {/* The instance needs a real container, but nothing here is for looking at. */}
      <div
        ref={containerRef}
        aria-hidden
        style={{ height: 1, width: 1, overflow: "hidden", opacity: 0 }}
      />

      <div id="contract-report" data-state={error ? "error" : report ? (report.passed ? "passed" : "failed") : "running"} className="mt-5">
        {error ? (
          <pre className="whitespace-pre-wrap font-mono text-xs text-destructive">
            {error}
          </pre>
        ) : report ? (
          <>
            <p className="text-sm font-medium">
              {report.passed ? "All checks passed" : "Some checks failed"}
              <span className="ml-2 font-normal text-muted-foreground">
                adapter {report.adapterVersion} · Univer {report.engineVersion}
              </span>
            </p>
            <ul className="mt-3 flex flex-col gap-1.5">
              {report.checks.map((check) => (
                <li key={check.name} className="flex gap-2 text-sm">
                  <span
                    className={
                      check.passed
                        ? "text-[color:var(--accent-emerald)]"
                        : "text-destructive"
                    }
                  >
                    {check.passed ? "PASS" : "FAIL"}
                  </span>
                  <span className="min-w-0">
                    {check.name}
                    <span className="ml-2 font-mono text-xs text-muted-foreground">
                      {check.detail}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="text-sm text-muted-foreground">Running…</p>
        )}
      </div>
    </div>
  );
}
