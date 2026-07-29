import type { JourneyStep } from "@/components/home/data/homepage-content";
import { JOURNEY_ILLUSTRATIONS } from "@/components/home/illustrations";
import { InView } from "@/components/home/lib/in-view";

/**
 * One step of the journey strip: the surface on top, the explanation beneath.
 *
 * The order is deliberately the inverse of the feature cards — here the
 * picture is the argument and the words are the caption.
 */
export function JourneyCard({ id, title, description }: JourneyStep) {
  const Illustration = JOURNEY_ILLUSTRATIONS[id];

  return (
    <article className="dm-card flex h-full flex-col">
      <InView className="dm-stage h-[168px] px-5 pt-6">
        <Illustration />
      </InView>

      <div className="mt-5 border-t border-border px-5 py-5 text-center sm:px-6">
        <h3 className="text-base font-semibold tracking-tight text-foreground">
          {title}
        </h3>
        <p className="mt-2 text-[13px] leading-relaxed text-muted-foreground">
          {description}
        </p>
      </div>
    </article>
  );
}
