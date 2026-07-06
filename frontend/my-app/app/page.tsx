import { Navbar } from "@/components/navbar";
import { HeroSection } from "@/components/home/hero-section";
import { FeaturesSection } from "@/components/home/features-section";
import { HowItWorks } from "@/components/home/how-it-works";
import { ProductPreview } from "@/components/home/product-preview";
import { FinalCta } from "@/components/home/final-cta";
import { SiteFooter } from "@/components/home/site-footer";

/**
 * Marketing homepage. Static sections are server components; only the
 * interactive/animated pieces (navbar, hero, reveal wrappers) are client.
 */
export default function Home() {
  return (
    <div className="flex min-h-dvh flex-col">
      <Navbar />
      <main className="flex-1">
        <HeroSection />
        <FeaturesSection />
        <HowItWorks />
        <ProductPreview />
        <FinalCta />
      </main>
      <SiteFooter />
    </div>
  );
}
