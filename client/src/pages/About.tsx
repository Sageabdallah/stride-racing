import { Navbar } from "@/components/Navbar";
import { AmbientEffects } from "@/components/AmbientEffects";
import { Card, CardContent } from "@/components/ui/card";
import { Brain, BarChart3, Target, Zap } from "lucide-react";

const features = [
  {
    icon: Brain,
    title: "AI-Powered Analysis",
    description: "Advanced machine learning algorithms analyze thousands of data points to identify value opportunities in the racing market.",
  },
  {
    icon: BarChart3,
    title: "Monte Carlo Simulations",
    description: "Run up to 100,000 race simulations to calculate win probabilities with statistical precision and confidence intervals.",
  },
  {
    icon: Target,
    title: "Value Bet Detection",
    description: "Our model compares calculated probabilities against market odds to identify positive expected value betting opportunities.",
  },
  {
    icon: Zap,
    title: "Real-Time Odds",
    description: "Live odds integration ensures you never miss a value bet as market prices fluctuate throughout the day.",
  },
];

export default function About() {
  return (
    <div className="min-h-screen bg-black font-dm" data-testid="page-about">
      <AmbientEffects />
      <Navbar />
      
      <main className="max-w-7xl mx-auto px-4 sm:px-6 pt-24 pb-12 relative z-[1]">
        <div className="mb-12 text-center max-w-3xl mx-auto">
          <h1 className="text-3xl sm:text-4xl page-title mb-4" data-testid="text-title">
            ABOUT <span className="gradient-text-shimmer">WIZBET</span>
          </h1>
          <p className="text-white/40 text-lg">
            Made by a punter for punters. Precision racing intelligence powered by cutting-edge AI and statistical modeling.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-12">
          {features.map((feature, index) => (
            <Card key={index} className="glass-card border-0" data-testid={`card-feature-${index}`}>
              <CardContent className="p-6">
                <div className="flex items-start gap-4">
                  <div className="p-3 rounded-md bg-white/[0.06] shrink-0">
                    <feature.icon className="h-6 w-6 text-racing-orange" />
                  </div>
                  <div>
                    <h3 className="text-lg font-bold mb-2 text-white">{feature.title}</h3>
                    <p className="text-white/45">{feature.description}</p>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>

        <Card className="glass-card border-0">
          <CardContent className="p-8">
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
              <div>
                <h2 className="text-2xl font-bold tracking-wide mb-4 font-syne">THE EDGE ALGORITHM</h2>
                <p className="text-white/45 mb-4">
                  Our proprietary algorithm combines multiple factors to generate accurate race predictions:
                </p>
                <ul className="space-y-3 text-white/40">
                  <li className="flex items-start gap-2">
                    <span className="text-racing-orange font-bold">01</span>
                    <span>Historical performance analysis across 50,000+ races</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-racing-orange font-bold">02</span>
                    <span>Track condition and distance suitability modeling</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-racing-orange font-bold">03</span>
                    <span>Jockey and trainer form pattern recognition</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-racing-orange font-bold">04</span>
                    <span>Class level and weight differential adjustments</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-racing-orange font-bold">05</span>
                    <span>Barrier draw and race pace scenario analysis</span>
                  </li>
                </ul>
              </div>
              
              <div className="bg-white/[0.04] rounded-md p-6">
                <h3 className="text-lg font-bold tracking-wide mb-4 font-syne">KELLY CRITERION STAKING</h3>
                <p className="text-white/40 mb-4">
                  We calculate optimal bet sizes using the Kelly Criterion, a mathematical formula that maximizes long-term bankroll growth while managing risk.
                </p>
                <div className="bg-white/[0.03] rounded-md p-4 font-mono text-sm">
                  <p className="text-white/40 mb-2">// Kelly Formula</p>
                  <p className="text-racing-orange">f* = (bp - q) / b</p>
                  <p className="text-white/40 mt-2 text-xs">
                    Where b = odds, p = win probability, q = loss probability
                  </p>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      </main>
    </div>
  );
}
