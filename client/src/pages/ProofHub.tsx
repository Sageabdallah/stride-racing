import { Link } from "wouter";
import { AmbientEffects } from "@/components/AmbientEffects";
import { Navbar } from "@/components/Navbar";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ArrowRight, BarChart3, Brain, ShieldCheck, Target, TrendingUp } from "lucide-react";

const proofCards = [
  {
    title: "Calibration Review",
    description: "Review the forward-test snapshot, raceday ROI curve, track slices, and replay outcomes from the latest verified card.",
    href: "/calibration",
    icon: Target,
    kicker: "Live Proof",
  },
  {
    title: "Backtest Results",
    description: "See how the model performed under walk-forward testing, leak-proof splits, and historical result replay.",
    href: "/backtest",
    icon: TrendingUp,
    kicker: "Historical Edge",
  },
  {
    title: "Simulation Lab",
    description: "Run Monte Carlo race simulations and inspect the probability stack underneath each runner.",
    href: "/simulations",
    icon: Brain,
    kicker: "Race Mechanics",
  },
  {
    title: "Gap Analysis",
    description: "Track the training-inference gap, result collection health, and live performance drift across the stack.",
    href: "/gap-analysis",
    icon: BarChart3,
    kicker: "Model Ops",
  },
];

export default function ProofHub() {
  return (
    <div className="min-h-screen bg-black font-dm text-white" data-testid="page-proof">
      <AmbientEffects />
      <Navbar />

      <main className="max-w-7xl mx-auto px-4 sm:px-6 pt-24 pb-12 relative z-[1]">
        <div className="max-w-4xl mb-12">
          <div className="inline-flex items-center gap-2 rounded-full border border-racing-gold/20 bg-racing-gold/10 px-4 py-1.5 text-[11px] font-semibold uppercase tracking-[0.18em] text-racing-gold">
            <ShieldCheck className="h-3.5 w-3.5" />
            Proof
          </div>
          <h1 className="mt-5 text-4xl sm:text-5xl font-syne font-extrabold tracking-tight text-white">
            Evidence that the edge is real.
          </h1>
          <p className="mt-4 max-w-3xl text-base leading-8 text-white/55">
            WizBet&apos;s proof layer brings your calibration, backtest, simulation, and live-performance tooling into one place.
            This is where the model earns trust, not just attention.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {proofCards.map((card) => (
            <Card key={card.href} className="glass-card border-0 overflow-hidden">
              <CardContent className="p-0">
                <div className="h-full bg-[radial-gradient(circle_at_top_left,rgba(249,115,22,0.16),transparent_45%),linear-gradient(145deg,rgba(255,255,255,0.05),rgba(255,255,255,0.02))] p-6">
                  <div className="flex items-start justify-between gap-4">
                    <div className="space-y-4">
                      <div className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-[11px] uppercase tracking-[0.18em] text-white/45">
                        <card.icon className="h-3.5 w-3.5 text-racing-orange" />
                        {card.kicker}
                      </div>
                      <div>
                        <h2 className="text-2xl font-syne font-bold tracking-tight text-white">{card.title}</h2>
                        <p className="mt-3 text-sm leading-7 text-white/55">{card.description}</p>
                      </div>
                    </div>
                  </div>

                  <div className="mt-6">
                    <Link href={card.href}>
                      <Button className="gap-2 bg-racing-orange text-black hover:bg-racing-orange/90">
                        Open {card.title}
                        <ArrowRight className="h-4 w-4" />
                      </Button>
                    </Link>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      </main>
    </div>
  );
}
