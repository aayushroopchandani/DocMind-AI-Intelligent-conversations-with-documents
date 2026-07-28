import { CapabilityVisual } from "@/components/home/capabilities/capability-visuals";
import {
  ACCENT_VAR,
  type Capability,
} from "@/components/home/data/homepage-content";
import { cn } from "@/lib/utils";

/** `span` is applied by the section to the grid item, not to the card itself. */
type CapabilityCardProps = Omit<Capability, "span"> & { className?: string };

/**
 * One bento tile. The accent colour is injected once as `--card-accent`; the
 * icon chip, visual and hover wash all read it, so a tile re-themes from a
 * single value in the content file.
 */
export function CapabilityCard({
  icon: Icon,
  title,
  description,
  accent,
  visual,
  className,
}: CapabilityCardProps) {
  return (
    <article
      className={cn("bento-card group flex h-full flex-col p-6", className)}
      style={{ "--card-accent": ACCENT_VAR[accent] } as React.CSSProperties}
    >
      <div className="accent-tile mb-4 inline-flex size-10 shrink-0 items-center justify-center rounded-xl">
        <Icon className="size-[18px]" />
      </div>

      <h3 className="mb-2 text-base font-semibold leading-snug tracking-tight text-foreground">
        {title}
      </h3>
      <p className="text-sm leading-relaxed text-muted-foreground">
        {description}
      </p>

      {visual && (
        <div className="mt-5">
          <CapabilityVisual variant={visual} />
        </div>
      )}

      <div className="accent-rule mt-auto h-px w-0 opacity-0 transition-all duration-500 group-hover:w-full group-hover:opacity-100" />
    </article>
  );
}
