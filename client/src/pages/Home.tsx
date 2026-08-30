import { Navbar } from "@/components/Navbar";
import { HeroSection } from "@/components/HeroSection";
import { AmbientEffects } from "@/components/AmbientEffects";
import { BackgroundPathsEffect } from "@/components/ui/background-paths";
import HalideTopo3DHero from "@/components/HalideTopo3DHero";
import {
  FeaturesSection,
  EdgeSection,
  TrackSecretsSection,
  ChampionsSection,
  CTASection,
  Footer,
  FloatingAskButton,
} from "@/components/LandingSections";

export default function Home() {
  return (
    <div className="min-h-screen bg-black font-dm" data-testid="page-home">
      <BackgroundPathsEffect />
      <AmbientEffects />
      <Navbar />
      <HeroSection />
      <HalideTopo3DHero />
      <EdgeSection />
      <FeaturesSection />
      <TrackSecretsSection />
      <ChampionsSection />
      <CTASection />
      <Footer />
      <FloatingAskButton />
    </div>
  );
}
