"use client";

import { useEffect, useRef } from "react";
import {
  CommandType,
  LocaleType,
  type IDisposable,
  type IRange,
} from "@univerjs/core";
import { UniverSheetsCorePreset } from "@univerjs/preset-sheets-core";
import sheetsCoreEnUS from "@univerjs/preset-sheets-core/locales/en-US";
import { SNAPSHOT_SAVE_DEBOUNCE_MS } from "@/lib/data-analysis/constants";
import { createUniver } from "@/lib/data-analysis/create-univer";
import { notifyStorageFull } from "@/lib/data-analysis/feedback";
import {
  loadWorkbookSnapshot,
  saveWorkbookSnapshot,
} from "@/lib/data-analysis/local-storage";
import { formatRangeA1 } from "@/lib/data-analysis/range-label";
import { getUniverBridge } from "@/lib/data-analysis/univer-bridge";
import { docmindUniverTheme } from "@/lib/data-analysis/univer-theme";
import { createBlankWorkbookData } from "@/lib/data-analysis/workbook-factory";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";

import "@univerjs/preset-sheets-core/lib/index.css";

/**
 * Hosts the single Univer application instance for the whole workspace.
 *
 * One instance holds many workbook units (one per spreadsheet artifact):
 * opening a tab loads its unit from the stored snapshot, switching tabs
 * calls `setCurrent`, closing a tab saves the snapshot and disposes only
 * that unit. This mirrors the official multi-unit facade API and avoids
 * duplicate Univer instances entirely.
 *
 * Mounted (lazily, via next/dynamic) while at least one spreadsheet tab is
 * open; unmounting flushes pending snapshots and disposes the instance, so
 * React Strict Mode's mount → cleanup → mount cycle stays leak-free.
 */
export default function UniverHost() {
  const { state, dispatch } = useWorkspace();
  const containerRef = useRef<HTMLDivElement>(null);

  /* ---------------- instance lifecycle (mount once) ---------------- */

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const bridge = getUniverBridge();
    const { univerAPI } = createUniver({
      darkMode: true,
      theme: docmindUniverTheme,
      locale: LocaleType.EN_US,
      locales: { [LocaleType.EN_US]: sheetsCoreEnUS },
      presets: [
        UniverSheetsCorePreset({
          container,
          // The surrounding IDE owns initial focus (analyst composer, tabs).
          disableAutoFocus: true,
        }),
      ],
    });

    bridge.api = univerAPI;
    bridge.containerEl = container;

    const flushSnapshot = (unitId: string) => {
      const timer = bridge.saveTimers.get(unitId);
      if (timer) {
        clearTimeout(timer);
        bridge.saveTimers.delete(unitId);
      }
      const workbook = univerAPI.getWorkbook(unitId);
      if (workbook) {
        if (!saveWorkbookSnapshot(unitId, workbook.save())) {
          notifyStorageFull();
        }
      }
      dispatch({ type: "SET_DIRTY", id: unitId, isDirty: false });
      if (bridge.saveTimers.size === 0) {
        dispatch({ type: "SET_SAVE_STATUS", status: "saved" });
      }
    };

    const scheduleSnapshot = (unitId: string) => {
      dispatch({ type: "SET_DIRTY", id: unitId, isDirty: true });
      dispatch({ type: "SET_SAVE_STATUS", status: "saving" });
      const existing = bridge.saveTimers.get(unitId);
      if (existing) clearTimeout(existing);
      bridge.saveTimers.set(
        unitId,
        setTimeout(() => flushSnapshot(unitId), SNAPSHOT_SAVE_DEBOUNCE_MS),
      );
    };

    const flushAllPending = () => {
      for (const unitId of [...bridge.saveTimers.keys()]) {
        flushSnapshot(unitId);
      }
    };

    const disposables: Array<IDisposable | undefined> = [
      // Dirty tracking: only MUTATIONs change the workbook model (selection
      // moves and UI changes are OPERATIONs and are ignored).
      univerAPI.addEvent(univerAPI.Event.CommandExecuted, (event) => {
        const { type, params } = event as {
          type: CommandType;
          params?: { unitId?: string };
        };
        if (type !== CommandType.MUTATION) return;
        const unitId =
          params?.unitId ?? univerAPI.getActiveWorkbook()?.getId() ?? null;
        if (!unitId || !bridge.loadedUnitIds.has(unitId)) return;
        scheduleSnapshot(unitId);
      }),

      univerAPI.addEvent(univerAPI.Event.SelectionChanged, (event) => {
        const { worksheet, selections } = event as {
          worksheet?: { getSheetName: () => string };
          selections?: IRange[];
        };
        const range = selections?.[0];
        dispatch({
          type: "SET_ANALYST_CONTEXT",
          context: {
            worksheetName: worksheet?.getSheetName() ?? null,
            selectedRange: range ? formatRangeA1(range) : null,
          },
        });
      }),

      univerAPI.addEvent(univerAPI.Event.ActiveSheetChanged, (event) => {
        const { activeSheet } = event as {
          activeSheet?: { getSheetName: () => string };
        };
        dispatch({
          type: "SET_ANALYST_CONTEXT",
          context: {
            worksheetName: activeSheet?.getSheetName() ?? null,
            selectedRange: null,
          },
        });
      }),
    ];

    const handleBeforeUnload = () => flushAllPending();
    window.addEventListener("beforeunload", handleBeforeUnload);

    dispatch({ type: "SET_UNIVER_READY", ready: true });

    return () => {
      window.removeEventListener("beforeunload", handleBeforeUnload);
      flushAllPending();
      disposables.forEach((disposable) => disposable?.dispose());
      dispatch({ type: "SET_UNIVER_READY", ready: false });
      dispatch({
        type: "SET_ANALYST_CONTEXT",
        context: { worksheetName: null, selectedRange: null },
      });
      bridge.api = null;
      bridge.containerEl = null;
      bridge.loadedUnitIds.clear();
      bridge.pendingDeleteIds.clear();
      // Disposes the whole instance, including all workbook units. Safe for
      // React Strict Mode: the second effect run boots a fresh instance.
      univerAPI.dispose();
    };
  }, [dispatch]);

  /* -------- reconcile workbook units with open workspace tabs -------- */

  useEffect(() => {
    const bridge = getUniverBridge();
    const api = bridge.api;
    if (!api || !state.univerReady) return;

    const openSpreadsheetIds = new Set(
      state.openTabIds.filter((id) =>
        state.artifacts.some(
          (artifact) => artifact.id === id && artifact.type === "spreadsheet",
        ),
      ),
    );

    // Unload units whose tab closed. Deleted artifacts skip the final save.
    for (const unitId of [...bridge.loadedUnitIds]) {
      if (openSpreadsheetIds.has(unitId)) continue;
      const timer = bridge.saveTimers.get(unitId);
      if (timer) {
        clearTimeout(timer);
        bridge.saveTimers.delete(unitId);
      }
      if (!bridge.pendingDeleteIds.has(unitId)) {
        const workbook = api.getWorkbook(unitId);
        if (workbook && !saveWorkbookSnapshot(unitId, workbook.save())) {
          notifyStorageFull();
        }
      }
      bridge.pendingDeleteIds.delete(unitId);
      api.disposeUnit(unitId);
      bridge.loadedUnitIds.delete(unitId);
      dispatch({ type: "SET_DIRTY", id: unitId, isDirty: false });
    }

    // Load units for newly opened tabs from their stored snapshots. A unit
    // created with makeCurrent is what boots Univer's sheet plugins and gives
    // units a renderer; `setCurrent` below can only focus a unit that already
    // has one.
    //
    // So exactly one unit must claim `makeCurrent` on the first pass. Normally
    // that is the active tab's unit, but the active tab can also be a PDF (the
    // workspace mounts this host whenever *any* spreadsheet tab is open, e.g.
    // after restoring a session where a PDF was in front). In that case no
    // unit would qualify, Univer would never render, and every later tab
    // switch would silently show a blank grid — so the first unit boots it.
    for (const unitId of openSpreadsheetIds) {
      if (bridge.loadedUnitIds.has(unitId)) continue;
      const artifact = state.artifacts.find((item) => item.id === unitId);
      const snapshot =
        loadWorkbookSnapshot(unitId) ??
        createBlankWorkbookData(unitId, artifact?.name ?? "Untitled");
      api.createWorkbook(snapshot, {
        makeCurrent: unitId === state.activeTabId || !api.getActiveWorkbook(),
      });
      bridge.loadedUnitIds.add(unitId);
    }

    // Focus the unit belonging to the active tab. By the time a user can
    // switch tabs, Univer has rendered and every loaded unit has a renderer,
    // which `setCurrent` requires.
    //
    // When the active tab is not a spreadsheet, Univer still needs *some*
    // current unit: its chrome (the formula bar) dereferences the current
    // workbook unconditionally and throws on null. That happens when the
    // current unit is disposed while other units remain — e.g. closing the
    // active spreadsheet lands the user on a PDF tab. Adopting any remaining
    // unit keeps the hidden host consistent and costs nothing.
    const activeId = state.activeTabId;
    const activeIsLoadedUnit = Boolean(
      activeId && bridge.loadedUnitIds.has(activeId),
    );
    const focusId = activeIsLoadedUnit
      ? activeId
      : (api.getActiveWorkbook()?.getId() ??
        bridge.loadedUnitIds.values().next().value ??
        null);

    if (focusId) {
      if (api.getActiveWorkbook()?.getId() !== focusId) {
        try {
          api.setCurrent(focusId);
        } catch (error) {
          // Never let a facade timing quirk take down the workspace.
          console.error("[data-analysis] Failed to focus workbook", error);
          return;
        }
      }
      // Only describe the workbook to the analyst when it really is what the
      // user is looking at — not when it was adopted to keep Univer happy.
      if (activeIsLoadedUnit) {
        const activeSheet = api.getWorkbook(focusId)?.getActiveSheet();
        dispatch({
          type: "SET_ANALYST_CONTEXT",
          context: {
            worksheetName: activeSheet?.getSheetName() ?? null,
            selectedRange: null,
          },
        });
      }
    }
  }, [
    dispatch,
    state.univerReady,
    state.openTabIds,
    state.activeTabId,
    state.artifacts,
  ]);

  return (
    <div
      ref={containerRef}
      aria-label="Spreadsheet editor"
      className="h-full w-full"
    />
  );
}
