import { createPluginRegistration } from "@embedpdf/core";
import { DocumentManagerPluginPackage } from "@embedpdf/plugin-document-manager/react";
import { ExportPluginPackage } from "@embedpdf/plugin-export/react";
import { InteractionManagerPluginPackage } from "@embedpdf/plugin-interaction-manager/react";
import { PanPluginPackage } from "@embedpdf/plugin-pan/react";
import { RenderPluginPackage } from "@embedpdf/plugin-render/react";
import { RotatePluginPackage } from "@embedpdf/plugin-rotate/react";
import { ScrollPluginPackage, ScrollStrategy } from "@embedpdf/plugin-scroll/react";
import { SearchPluginPackage } from "@embedpdf/plugin-search/react";
import { SelectionPluginPackage } from "@embedpdf/plugin-selection/react";
import { SpreadMode, SpreadPluginPackage } from "@embedpdf/plugin-spread/react";
import { ThumbnailPluginPackage } from "@embedpdf/plugin-thumbnail/react";
import { TilingPluginPackage } from "@embedpdf/plugin-tiling/react";
import { ViewportPluginPackage } from "@embedpdf/plugin-viewport/react";
import { ZoomMode, ZoomPluginPackage } from "@embedpdf/plugin-zoom/react";
import { PDF_THUMBNAIL_WIDTH } from "@/lib/data-analysis/constants";

/**
 * The plugin set backing the DocMind PDF workspace.
 *
 * Registered once, module-level, so the array identity is stable — passing a
 * fresh array to `<EmbedPDF>` would re-run plugin registration on every
 * render of the host.
 *
 * Deliberately excluded: annotation, redaction, form-filling, signatures,
 * stamps, print and the document-permissions/encryption features. This
 * milestone is a reader, not an editor, and every plugin left out is bundle
 * weight and memory the workspace does not pay for.
 */
export const PDF_PLUGINS = [
  // Owns document lifecycle. One document per PDF workspace tab, keyed by
  // artifact id — which is what makes tab state survive tab switches.
  createPluginRegistration(DocumentManagerPluginPackage),

  createPluginRegistration(ViewportPluginPackage, { viewportGap: 16 }),
  createPluginRegistration(ScrollPluginPackage, {
    defaultStrategy: ScrollStrategy.Vertical,
    defaultPageGap: 12,
  }),
  createPluginRegistration(RenderPluginPackage),
  // Tiling keeps memory bounded at high zoom: pages render as tiles rather
  // than one enormous bitmap. Strongly recommended by EmbedPDF for production.
  createPluginRegistration(TilingPluginPackage, {
    tileSize: 768,
    overlapPx: 2.5,
    extraRings: 0,
  }),

  // Pointer routing — required by selection, marquee zoom and the pan tool.
  createPluginRegistration(InteractionManagerPluginPackage),

  createPluginRegistration(ZoomPluginPackage, {
    defaultZoomLevel: ZoomMode.FitWidth,
    presets: [
      { name: "Fit width", value: ZoomMode.FitWidth },
      { name: "Fit page", value: ZoomMode.FitPage },
      { name: "50%", value: 0.5 },
      { name: "75%", value: 0.75 },
      { name: "100%", value: 1 },
      { name: "150%", value: 1.5 },
      { name: "200%", value: 2 },
      { name: "400%", value: 4 },
    ],
  }),
  createPluginRegistration(RotatePluginPackage),
  createPluginRegistration(PanPluginPackage),
  createPluginRegistration(SpreadPluginPackage, {
    defaultSpreadMode: SpreadMode.None,
  }),

  createPluginRegistration(SelectionPluginPackage),
  createPluginRegistration(SearchPluginPackage),
  createPluginRegistration(ThumbnailPluginPackage, {
    width: PDF_THUMBNAIL_WIDTH,
    paddingY: 8,
  }),
  // Downloads the original bytes; auto-mounts its own hidden <Download />.
  createPluginRegistration(ExportPluginPackage),
];
