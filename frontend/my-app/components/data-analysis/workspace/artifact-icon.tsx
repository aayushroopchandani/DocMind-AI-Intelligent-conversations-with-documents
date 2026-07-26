import {
  ChartColumn,
  FileQuestion,
  FileSpreadsheet,
  FileText,
  LayoutDashboard,
  type LucideProps,
} from "lucide-react";
import type { ArtifactType } from "@/lib/data-analysis/types";

const ICONS: Record<ArtifactType, React.ComponentType<LucideProps>> = {
  spreadsheet: FileSpreadsheet,
  pdf: FileText,
  chart: ChartColumn,
  report: FileQuestion,
  dashboard: LayoutDashboard,
};

/**
 * One icon per artifact type, shared by the explorer tree and the tab strip
 * so a PDF looks like the same file in both places.
 */
export function ArtifactIcon({
  type,
  ...props
}: LucideProps & { type: ArtifactType }) {
  const Icon = ICONS[type];
  return <Icon {...props} />;
}
