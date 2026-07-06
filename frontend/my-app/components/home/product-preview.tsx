import { ProductMockup } from "@/components/home/product-mockup";
import { Reveal } from "@/components/reveal";

export function ProductPreview() {
  return (
    <section className="relative py-24 sm:py-32">
      <div className="mx-auto max-w-6xl px-4">
        <Reveal className="mx-auto max-w-2xl text-center">
          <h2 className="text-balance text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
            A workspace built for reading and reasoning
          </h2>
          <p className="mt-4 text-balance text-muted-foreground">
            The document on the left, the conversation on the right, and
            citations right where you need them.
          </p>
        </Reveal>

        <Reveal delay={0.1} className="mx-auto mt-14 max-w-4xl">
          <ProductMockup />
        </Reveal>
      </div>
    </section>
  );
}
