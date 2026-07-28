import { Navbar } from "@/components/navbar";
import { HeroSection } from "@/components/home/hero-section";
import { FormatsStrip } from "@/components/home/formats-strip";
import { CapabilitiesSection } from "@/components/home/capabilities/capabilities-section";
import { WorkflowSection } from "@/components/home/workflow-section";
import { SurfacesSection } from "@/components/home/surfaces-section";
import { FinalCta } from "@/components/home/final-cta";
import { SiteFooter } from "@/components/home/site-footer";

/**
 * Marketing homepage. Static sections are server components; only the pieces
 * that actually animate (navbar, hero, agent console, reveal wrappers) ship
 * client JavaScript.
 */
export default function Home() {
  return (
    <div className="flex min-h-dvh flex-col">
      <Navbar />
      <main className="flex-1">
        <HeroSection />
        <FormatsStrip />
        <CapabilitiesSection />
        <WorkflowSection />
        <SurfacesSection />
        <FinalCta />
      </main>
      <SiteFooter />
    </div>
  );
}
