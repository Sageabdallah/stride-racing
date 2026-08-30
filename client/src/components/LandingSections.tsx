import { useState, useEffect } from "react";
import { Link } from "wouter";
import { ScrollReveal } from "./ScrollReveal";
import { AnimatedCounter } from "./AnimatedCounter";
import {
  Brain,
  BarChart3,
  Zap,
  Target,
  TrendingUp,
  Cpu,
  Trophy,
  MessageSquare,
  Database,
  ShieldCheck,
  Crosshair,
  MapPin,
  Activity,
  Layers,
  GitBranch,
} from "lucide-react";

const stats = [
  { icon: Brain, value: 10000, suffix: "+", label: "Simulations", subtext: "Monte Carlo per race" },
  { icon: BarChart3, value: 0.807, decimals: 3, label: "AUC ROC", subtext: "Ensemble ML accuracy" },
  { icon: Layers, value: 100, suffix: "", label: "ML Features", subtext: "Per runner analysed" },
  { icon: Target, value: 85, suffix: "%", label: "Calibration", subtext: "Isotonic verified" },
];

const features = [
  {
    icon: GitBranch,
    title: "Form Franking Engine",
    desc: "ELO-based strength ratings validate collateral form. We score how beaten opponents performed next start, so you know if a horse's form is real or inflated.",
    stat: "8,062",
    statLabel: "Horses Rated",
  },
  {
    icon: Layers,
    title: "ML Ensemble",
    desc: "Three gradient boosting models (XGBoost, LightGBM, CatBoost) vote on every runner using 100 features. Optuna tunes them. SHAP explains them.",
    stat: "3",
    statLabel: "Models Combined",
  },
  {
    icon: Activity,
    title: "Fitness Peak Analysis",
    desc: "Tracks each horse's training pattern, spell length, and race spacing to identify when they're at peak fitness versus when they're declining.",
    stat: "100%",
    statLabel: "Runners Scored",
  },
];

const champions = [
  { name: "Blackbook", years: "Structured Market Memory", desc: "Track horses whose last-start run was better than the result. WizBet monitors the next setup so you know when the conditions finally align.", stat1: "10", stat1Label: "Readiness Bands", stat2: "Live", stat2Label: "Alerts" },
  { name: "The Track Board", years: "Top 3 Per Metro Track", desc: "Hand-selected from metro tracks only. Our ensemble model picks the top 3 runners per track with the biggest edge over the market. Quality over quantity.", stat1: "Top 3", stat1Label: "Per Track", stat2: "Edge", stat2Label: "Ranked" },
  { name: "Proof", years: "Credibility Layer", desc: "Forward testing, calibration, backtesting, and model-ops diagnostics in one place. This is where the edge is measured, not just marketed.", stat1: "Proof", stat1Label: "Layer", stat2: "Live", stat2Label: "Review" },
];

export function StatsSection() {
  return (
    <section className="py-24 px-8 relative overflow-hidden border-t border-white/[0.05]" data-testid="section-stats">
      <div className="ambient-grid" />
      <div className="max-w-[1200px] mx-auto">
        <ScrollReveal>
          <div className="mb-14">
            <p className="section-label-text mb-3">Performance</p>
            <h2 className="font-syne font-bold text-white tracking-tight" style={{ fontSize: "clamp(28px, 4vw, 44px)", letterSpacing: "-0.02em" }}>
              Numbers that matter
            </h2>
          </div>
        </ScrollReveal>
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4" data-testid="stats-grid">
          {stats.map((stat, i) => (
            <ScrollReveal key={stat.label} delay={i * 0.06}>
              <div className="liquid-glass p-7 text-left" data-testid={`stat-card-${i}`}>
                <div className="stat-icon-wrap mb-4">
                  <stat.icon className="w-5 h-5 text-[#f97316]" />
                </div>
                <p className="font-syne text-4xl font-bold text-white tracking-tight mb-1">
                  <AnimatedCounter end={stat.value} suffix={stat.suffix || ""} decimals={stat.decimals || 0} />
                </p>
                <p className="text-xs uppercase tracking-[0.1em] text-white/35 mb-1">{stat.label}</p>
                <p className="text-[11px] text-white/20">{stat.subtext}</p>
              </div>
            </ScrollReveal>
          ))}
        </div>
      </div>
    </section>
  );
}

export function FeaturesSection() {
  return (
    <section className="py-24 px-8 relative overflow-hidden border-t border-white/[0.05]" data-testid="section-features">
      <div className="ambient-grid" />
      <div className="max-w-[1200px] mx-auto">
        <ScrollReveal>
          <div className="mb-14">
            <p className="section-label-text mb-3">Technology</p>
            <h2 className="font-syne font-bold text-white tracking-tight" style={{ fontSize: "clamp(28px, 4vw, 44px)", letterSpacing: "-0.02em" }}>
              Engineered for edge
            </h2>
          </div>
        </ScrollReveal>
        <div className="grid md:grid-cols-3 gap-4" data-testid="features-grid">
          {features.map((f, i) => (
            <ScrollReveal key={f.title} delay={i * 0.06}>
              <div className="liquid-glass p-8" data-testid={`feature-card-${i}`}>
                <div className="flex items-start justify-between gap-2 mb-6">
                  <div className="feature-icon-wrap">
                    <f.icon className="w-5 h-5 text-[#f97316]" />
                  </div>
                  <div className="text-right">
                    <p className="font-syne text-[22px] font-bold text-[#f97316]">{f.stat}</p>
                    <p className="text-[10px] uppercase tracking-[0.1em] text-white/30">{f.statLabel}</p>
                  </div>
                </div>
                <h3 className="font-syne text-xl font-semibold text-white mb-2 tracking-tight">{f.title}</h3>
                <p className="text-sm text-white/40 leading-relaxed">{f.desc}</p>
              </div>
            </ScrollReveal>
          ))}
        </div>
      </div>
    </section>
  );
}

export function ChampionsSection() {
  return (
    <section className="py-24 px-8 relative overflow-hidden border-t border-white/[0.05]" data-testid="section-champions">
      <div className="max-w-[1200px] mx-auto">
        <ScrollReveal>
          <div className="mb-14">
            <p className="section-label-text mb-3">Platform</p>
            <h2 className="font-syne font-bold text-white tracking-tight" style={{ fontSize: "clamp(28px, 4vw, 44px)", letterSpacing: "-0.02em" }}>
              The WizBet stack
            </h2>
          </div>
        </ScrollReveal>
        <div className="champions-scroll" data-testid="champions-scroll">
          {champions.map((c, i) => (
            <ScrollReveal key={c.name} delay={i * 0.08}>
              <div className="liquid-glass flex-shrink-0 w-[300px]" style={{ scrollSnapAlign: "start" }} data-testid={`champion-card-${i}`}>
                <div className="p-5">
                  <p className="font-syne text-lg font-bold text-white mb-1">{c.name}</p>
                  <p className="text-xs text-white/40 mb-3">{c.years}</p>
                  <p className="text-[13px] text-white/45 leading-relaxed mb-4">{c.desc}</p>
                  <div className="flex gap-4">
                    <div>
                      <p className="font-syne text-xl font-bold text-[#f97316]">{c.stat1}</p>
                      <p className="text-[10px] uppercase tracking-[0.08em] text-white/30">{c.stat1Label}</p>
                    </div>
                    <div>
                      <p className="font-syne text-xl font-bold text-[#f97316]">{c.stat2}</p>
                      <p className="text-[10px] uppercase tracking-[0.08em] text-white/30">{c.stat2Label}</p>
                    </div>
                  </div>
                </div>
              </div>
            </ScrollReveal>
          ))}
        </div>
      </div>
    </section>
  );
}

const edgeStats = [
  { icon: Database, value: 100, suffix: "", label: "FACTORS ANALYSED", subtext: "Per runner, every race" },
  { icon: ShieldCheck, value: 32000, suffix: "+", label: "HORSES ANALYSED", subtext: "From 22,450 historical results" },
  { icon: Crosshair, value: 1, suffix: " in 3", label: "WINNERS HIT", subtext: "Selections that salute" },
];

export function EdgeSection() {
  return (
    <section className="py-24 px-8 relative overflow-hidden border-t border-white/[0.05]" data-testid="section-edge">
      <div className="ambient-grid" />
      <div className="max-w-[1200px] mx-auto">
        <ScrollReveal>
          <div className="mb-4">
            <p className="section-label-text mb-3">Your Unfair Advantage</p>
            <h2 className="font-syne font-bold text-white tracking-tight" style={{ fontSize: "clamp(28px, 4vw, 44px)", letterSpacing: "-0.02em" }}>
              Hedge fund technology.{" "}
              <span className="gradient-text-shimmer">Punter-friendly pricing.</span>
            </h2>
          </div>
          <p className="text-[15px] text-white/40 max-w-[600px] leading-relaxed mb-14">
            We use the same quantitative methods as Wall Street hedge funds — applied to Australian thoroughbred racing.
          </p>
        </ScrollReveal>
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4" data-testid="edge-grid">
          {edgeStats.map((stat, i) => (
            <ScrollReveal key={stat.label} delay={i * 0.06}>
              <div className="liquid-glass p-7 text-left" data-testid={`edge-card-${i}`}>
                <div className="stat-icon-wrap mb-4">
                  <stat.icon className="w-5 h-5 text-[#f97316]" />
                </div>
                <p className={`font-syne font-bold text-white tracking-tight mb-1 ${stat.value >= 1000 ? 'text-2xl sm:text-3xl' : 'text-4xl'}`}>
                  <AnimatedCounter end={stat.value} suffix={stat.suffix} />
                </p>
                <p className="text-xs uppercase tracking-[0.1em] text-white/35 mb-1">{stat.label}</p>
                <p className="text-[11px] text-white/20">{stat.subtext}</p>
              </div>
            </ScrollReveal>
          ))}
          <ScrollReveal delay={0.18}>
            <div className="liquid-glass p-7 text-left relative" data-testid="edge-card-3">
              <div className="stat-icon-wrap mb-4">
                <TrendingUp className="w-5 h-5 text-[#f97316]" />
              </div>
              <div className="flex items-baseline gap-1 mb-1">
                <p className="font-syne text-2xl font-bold text-white tracking-tight">Hedge Fund</p>
              </div>
              <p className="text-xs uppercase tracking-[0.1em] text-white/35 mb-1">QUANTITATIVE METHODS</p>
              <p className="text-[11px] text-white/20">Wall Street tech for racing</p>
            </div>
          </ScrollReveal>
        </div>
      </div>
    </section>
  );
}

const trackBiases = [
  {
    track: "Doomben",
    subtitle: "Sprint Races",
    inside: 16.8,
    wide: 5.1,
    multiplier: "3.3x",
    insight: "Inside barriers at Doomben win sprints at 3 times the rate of wide barriers.",
  },
  {
    track: "Randwick",
    subtitle: "Sprint Races",
    inside: 14.0,
    wide: 3.7,
    multiplier: "3.8x",
    insight: "At Randwick, inside draws dominate sprints — nearly 4 times more winners.",
  },
  {
    track: "Flemington",
    subtitle: "Longer Races",
    inside: 8.2,
    wide: 11.5,
    multiplier: "Reverse",
    insight: "Flemington breaks the mould. In longer races, middle and wide barriers actually outperform inside draws.",
  },
];

export function TrackSecretsSection() {
  return (
    <section className="py-24 px-8 relative overflow-hidden border-t border-white/[0.05]" data-testid="section-track-secrets">
      <div className="max-w-[1200px] mx-auto">
        <ScrollReveal>
          <div className="mb-4">
            <p className="section-label-text mb-3">Track Intelligence</p>
            <h2 className="font-syne font-bold text-white tracking-tight" style={{ fontSize: "clamp(28px, 4vw, 44px)", letterSpacing: "-0.02em" }}>
              Insights the bookies{" "}
              <span className="gradient-text-shimmer">don't share</span>
            </h2>
          </div>
          <p className="text-[15px] text-white/40 max-w-[600px] leading-relaxed mb-14">
            Our system analyses thousands of past races to uncover hidden patterns at every major track. Here's a taste of what we find.
          </p>
        </ScrollReveal>
        <div className="grid md:grid-cols-3 gap-4" data-testid="track-secrets-grid">
          {trackBiases.map((t, i) => (
            <ScrollReveal key={t.track} delay={i * 0.08}>
              <div className="liquid-glass p-8" data-testid={`track-secret-card-${i}`}>
                <div className="flex items-center gap-3 mb-5">
                  <div className="feature-icon-wrap">
                    <MapPin className="w-5 h-5 text-[#f97316]" />
                  </div>
                  <div>
                    <h3 className="font-syne text-lg font-semibold text-white tracking-tight">{t.track}</h3>
                    <p className="text-[11px] text-white/30 uppercase tracking-wider">{t.subtitle}</p>
                  </div>
                  <div className="ml-auto">
                    <span
                      className="font-syne text-xl font-bold"
                      style={{ color: t.multiplier === "Reverse" ? "#c084fc" : "#f97316" }}
                    >
                      {t.multiplier}
                    </span>
                  </div>
                </div>
                <div className="space-y-3 mb-5">
                  <div>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs text-white/50">Inside barriers</span>
                      <span className="text-xs font-semibold text-white/70">{t.inside}%</span>
                    </div>
                    <div className="h-2 rounded-full bg-white/[0.06] overflow-hidden">
                      <div
                        className="h-full rounded-full transition-all duration-1000"
                        style={{
                          width: `${(t.inside / 20) * 100}%`,
                          background: t.multiplier === "Reverse"
                            ? "linear-gradient(90deg, #c084fc, #a855f7)"
                            : "linear-gradient(90deg, #f97316, #fb923c)",
                        }}
                      />
                    </div>
                  </div>
                  <div>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs text-white/50">Wide barriers</span>
                      <span className="text-xs font-semibold text-white/70">{t.wide}%</span>
                    </div>
                    <div className="h-2 rounded-full bg-white/[0.06] overflow-hidden">
                      <div
                        className="h-full rounded-full transition-all duration-1000"
                        style={{
                          width: `${(t.wide / 20) * 100}%`,
                          background: "linear-gradient(90deg, rgba(255,255,255,0.2), rgba(255,255,255,0.3))",
                        }}
                      />
                    </div>
                  </div>
                </div>
                <p className="text-[13px] text-white/40 leading-relaxed">{t.insight}</p>
              </div>
            </ScrollReveal>
          ))}
        </div>
      </div>
    </section>
  );
}

export function CTASection() {
  return (
    <section className="py-28 px-8 text-center relative overflow-hidden" data-testid="section-cta">
      <div
        className="absolute pointer-events-none"
        style={{
          width: 600, height: 600,
          top: "50%", left: "50%",
          transform: "translate(-50%, -50%)",
          background: "radial-gradient(ellipse, rgba(249,115,22,0.1) 0%, transparent 60%)",
          filter: "blur(80px)",
        }}
      />
      <ScrollReveal>
        <h2 className="font-syne font-extrabold text-white tracking-tight relative mb-4" style={{ fontSize: "clamp(32px, 5vw, 56px)", letterSpacing: "-0.03em" }}>
          Ready to sharpen your <span className="gradient-text-shimmer">edge?</span>
        </h2>
        <p className="text-[17px] text-white/45 max-w-[500px] mx-auto mb-9 leading-relaxed">
          Open the Blackbook, work the raceday card, or pressure-test the model through the proof layer.
        </p>
        <div className="flex gap-3 justify-center flex-wrap">
          <Link href="/ask-stride">
            <span className="btn-liquid" data-testid="button-cta-stride">
              <MessageSquare className="w-4 h-4" />
              Ask Stride
            </span>
          </Link>
          <Link href="/blackbook">
            <span className="btn-liquid-ghost" data-testid="button-cta-bets">
              <Trophy className="w-4 h-4" />
              Open Blackbook
            </span>
          </Link>
        </div>
      </ScrollReveal>
    </section>
  );
}

export function Footer() {
  return (
    <footer className="py-20 px-8 border-t border-white/[0.05]" data-testid="footer">
      <div className="max-w-[1200px] mx-auto">
        <div className="flex justify-between items-start gap-12 mb-12 flex-wrap">
          <div className="max-w-[360px]">
            <p className="font-syne text-[22px] font-extrabold tracking-tight mb-3 brand-liquid" data-text="WizBet">
              WizBet
            </p>
            <p className="text-sm text-white/35 leading-relaxed">
              Racing intelligence for sharp punters. Blackbook workflow, raceday execution, and proof-driven model accountability.
            </p>
          </div>
          <div className="flex gap-14 flex-wrap">
            <div>
              <p className="text-[11px] uppercase tracking-[0.15em] text-white/25 mb-4">Platform</p>
              <div className="flex flex-col gap-2">
                <Link href="/blackbook"><span className="text-sm text-white/45 hover:text-white transition-colors cursor-pointer" data-testid="link-footer-blackbook">Blackbook</span></Link>
                <Link href="/best-bets"><span className="text-sm text-white/45 hover:text-white transition-colors cursor-pointer" data-testid="link-footer-best-bets">The Track Board</span></Link>
                <Link href="/race-day"><span className="text-sm text-white/45 hover:text-white transition-colors cursor-pointer" data-testid="link-footer-race-day">Race Day</span></Link>
                <Link href="/proof"><span className="text-sm text-white/45 hover:text-white transition-colors cursor-pointer" data-testid="link-footer-proof">Proof</span></Link>
                <Link href="/ask-stride"><span className="text-sm text-white/45 hover:text-white transition-colors cursor-pointer" data-testid="link-footer-stride">Ask Stride</span></Link>
              </div>
            </div>
            <div>
              <p className="text-[11px] uppercase tracking-[0.15em] text-white/25 mb-4">Racing</p>
              <div className="flex flex-col gap-2">
                <Link href="/tracks"><span className="text-sm text-white/45 hover:text-white transition-colors cursor-pointer" data-testid="link-footer-tracks">Tracks</span></Link>
                <Link href="/form-guide"><span className="text-sm text-white/45 hover:text-white transition-colors cursor-pointer" data-testid="link-footer-form-guide">Form Guide</span></Link>
                <Link href="/about"><span className="text-sm text-white/45 hover:text-white transition-colors cursor-pointer" data-testid="link-footer-about">About</span></Link>
              </div>
            </div>
          </div>
        </div>
        <div className="h-px bg-gradient-to-r from-transparent via-white/[0.08] to-transparent mb-7" />
        <div className="flex justify-between items-center flex-wrap gap-4">
          <p className="text-xs text-white/25">&copy; {new Date().getFullYear()} WizBet. All rights reserved.</p>
          <p className="text-xs text-white/25">
            Built by machines. Backed by data. Made in Australia.
          </p>
        </div>
      </div>
    </footer>
  );
}

export function FloatingAskButton() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const handleScroll = () => setVisible(window.scrollY > 400);
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  return (
    <Link href="/ask-stride">
      <span className={`floating-btn ${visible ? "visible" : ""}`} data-testid="button-floating-stride">
        <MessageSquare className="w-4 h-4" />
        Ask Stride
      </span>
    </Link>
  );
}
