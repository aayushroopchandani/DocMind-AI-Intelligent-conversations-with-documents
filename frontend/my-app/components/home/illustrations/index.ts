import type {
  FeatureId,
  JourneyId,
  SecurityId,
} from "@/components/home/data/homepage-content";
import { ChartIllustration } from "@/components/home/illustrations/chart-illustration";
import { CitationIllustration } from "@/components/home/illustrations/citation-illustration";
import { CodeIllustration } from "@/components/home/illustrations/code-illustration";
import { ContextIllustration } from "@/components/home/illustrations/context-illustration";
import {
  AnalyzeIllustration,
  ConnectIllustration,
  ShareIllustration,
} from "@/components/home/illustrations/journey-illustrations";
import {
  EncryptionIllustration,
  OwnershipIllustration,
  SandboxIllustration,
} from "@/components/home/illustrations/security-illustrations";
import { SheetIllustration } from "@/components/home/illustrations/sheet-illustration";
import { TransformIllustration } from "@/components/home/illustrations/transform-illustration";

/**
 * Content id → artwork.
 *
 * The registries live here rather than in the content file so copy stays free
 * of component imports, and a section only ever needs the id it already has.
 */

type Illustration = () => React.ReactElement;

export const FEATURE_ILLUSTRATIONS: Record<FeatureId, Illustration> = {
  charts: ChartIllustration,
  transform: TransformIllustration,
  code: CodeIllustration,
  context: ContextIllustration,
  sheet: SheetIllustration,
  citations: CitationIllustration,
};

export const JOURNEY_ILLUSTRATIONS: Record<JourneyId, Illustration> = {
  connect: ConnectIllustration,
  analyse: AnalyzeIllustration,
  share: ShareIllustration,
};

export const SECURITY_ILLUSTRATIONS: Record<SecurityId, Illustration> = {
  ownership: OwnershipIllustration,
  encryption: EncryptionIllustration,
  sandbox: SandboxIllustration,
};
