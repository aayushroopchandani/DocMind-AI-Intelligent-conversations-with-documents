import { Navbar } from "@/components/navbar";
import { HeroSection } from "@/components/home/hero-section";
import { FormatsStrip } from "@/components/home/formats-strip";
import { JourneySection } from "@/components/home/journey/journey-section";
import { FeaturesSection } from "@/components/home/features/features-section";
import { SecuritySection } from "@/components/home/security/security-section";
import { SurfacesSection } from "@/components/home/surfaces-section";
import { FinalCta } from "@/components/home/final-cta";
import { SiteFooter } from "@/components/home/site-footer";

import "@/components/home/home.css";

/**
 * Marketing homepage.
 *
 * The order is the pitch: what it is (hero), what it eats (formats), how it
 * works end to end (journey), what it produces (features), why it can be
 * trusted with the data (security), and where to start (surfaces).
 *
 * Static sections are server components; only the pieces that genuinely
 * animate — navbar, hero, the agent console, and the thin `InView`/`Reveal`
 * wrappers — ship any client JavaScript. `dm-home` carries the illustration
 * tokens the whole page inherits.
 */
export default function Home() {
  return (
    <div className="dm-home flex min-h-dvh flex-col">
      <Navbar />
      <main className="flex-1">
        <HeroSection />
        <FormatsStrip />
        <JourneySection />
        <FeaturesSection />
        <SecuritySection />
        <SurfacesSection />
        <FinalCta />
      </main>
      <SiteFooter />
    </div>
  );
}
