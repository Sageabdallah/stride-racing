import { useState, useEffect, useCallback, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Link } from "wouter";
import { MessageSquare, Play, TrendingUp } from "lucide-react";

interface SlideItem {
  type: "video";
  src: string;
  mobileSrc: string;
  name: string;
  era: string;
  achievement: string;
}

function useIsMobile() {
  const [isMobile, setIsMobile] = useState(false);
  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth <= 768);
    check();
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, []);
  return isMobile;
}

const legendaryHorses: SlideItem[] = [
  {
    type: "video",
    src: "/videos/horse4.mp4",
    mobileSrc: "/videos/horse4_mobile.mp4",
    name: "WINX",
    era: "2015–2019",
    achievement: "33 consecutive wins, 25 Group 1s, 4x Cox Plate champion",
  },
  {
    type: "video",
    src: "/videos/horse2.mp4",
    mobileSrc: "/videos/horse2_mobile.mp4",
    name: "BLACK CAVIAR",
    era: "2009–2013",
    achievement: "25 wins from 25 starts, 15 Group 1s, undefeated legend",
  },
  {
    type: "video",
    src: "/videos/horse3.mp4",
    mobileSrc: "/videos/horse3_mobile.mp4",
    name: "VIA SISTINA",
    era: "2024–2026",
    achievement: "12 Group 1s, back-to-back Cox Plates, 8-length demolition",
  },
  {
    type: "video",
    src: "/videos/horse1.mp4",
    mobileSrc: "/videos/horse1_mobile.mp4",
    name: "KA YING RISING",
    era: "2023–Present",
    achievement: "17 consecutive wins, 6 Group 1s, world #1 ranked sprinter",
  },
];


function VideoSlide({ src, mobileSrc, isMobile }: { src: string; mobileSrc: string; isMobile: boolean }) {
  const videoSrc = isMobile ? mobileSrc : src;
  const videoRef = useRef<HTMLVideoElement>(null);
  const [videoFailed, setVideoFailed] = useState(false);

  useEffect(() => {
    setVideoFailed(false);
    const video = videoRef.current;
    if (!video) return;

    video.load();

    const playAttempt = () => {
      video.play().catch(() => {
        video.muted = true;
        video.play().catch(() => {
          setVideoFailed(true);
        });
      });
    };

    if (video.readyState >= 2) {
      playAttempt();
    } else {
      video.addEventListener("loadeddata", playAttempt, { once: true });
    }

    video.addEventListener("error", () => setVideoFailed(true), { once: true });

    return () => {
      video.removeEventListener("loadeddata", playAttempt);
    };
  }, [videoSrc]);

  if (videoFailed) {
    return (
      <div
        className="absolute inset-0 w-full h-full bg-gradient-to-br from-black via-gray-900 to-black"
        data-testid="video-slide-fallback"
      />
    );
  }

  return (
    <video
      ref={videoRef}
      src={videoSrc}
      autoPlay
      loop
      muted
      playsInline
      preload="auto"
      {...({ "webkit-playsinline": "true" } as any)}
      className="absolute inset-0 w-full h-full object-cover"
      style={{ objectPosition: "center 30%" }}
      data-testid="video-slide"
    />
  );
}

export function HeroSection() {
  const [currentIndex, setCurrentIndex] = useState(0);
  const [resetKey, setResetKey] = useState(0);
  const isMobile = useIsMobile();

  const goToSlide = useCallback((index: number) => {
    setCurrentIndex(index);
    setResetKey((k) => k + 1);
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => {
      setCurrentIndex((prev) => (prev + 1) % legendaryHorses.length);
    }, 14000);
    return () => clearTimeout(timer);
  }, [currentIndex, resetKey]);

  const safeIndex = currentIndex % legendaryHorses.length;
  const currentHorse = legendaryHorses[safeIndex];

  return (
    <section
      className="relative min-h-screen flex items-center overflow-hidden pt-16"
      style={{ minHeight: "100dvh" }}
      data-testid="hero-section"
    >
      {/* Video slideshow — all 4 always mounted; inactive slides cut to 0 instantly, active fades in */}
      {legendaryHorses.map((horse, index) => (
        <motion.div
          key={horse.name}
          className="absolute inset-0"
          initial={{ opacity: index === 0 ? 1 : 0 }}
          animate={{ opacity: index === safeIndex ? 1 : 0 }}
          transition={
            index === safeIndex
              ? { duration: 1.0, ease: [0.16, 1, 0.3, 1] }
              : { duration: 0 }
          }
        >
          <VideoSlide src={horse.src} mobileSrc={horse.mobileSrc} isMobile={isMobile} />
          <div
            className="absolute inset-0"
            style={{
              background:
                "linear-gradient(to right, rgba(0,0,0,0.95) 0%, rgba(0,0,0,0.7) 40%, rgba(0,0,0,0.3) 100%), linear-gradient(to top, rgba(0,0,0,0.9) 0%, transparent 40%, rgba(0,0,0,0.4) 100%)",
            }}
          />
        </motion.div>
      ))}

      {/* Ambient orange glow */}
      <div
        className="absolute pointer-events-none"
        style={{
          width: 700,
          height: 700,
          top: "20%",
          left: "10%",
          background: "radial-gradient(ellipse, rgba(249,115,22,0.12) 0%, transparent 65%)",
          filter: "blur(80px)",
          animation: "glowPulse 8s ease-in-out infinite",
        }}
      />

      {/* Hero content - LEFT aligned */}
      <div className="relative z-10 max-w-[720px] px-8 ml-[max(32px,5vw)]" data-testid="hero-content">
        {/* Badge pill */}
        <div className="hero-entry hero-entry-1 mb-6">
          <div className="liquid-pill" data-testid="hero-badge">
            <span className="w-2 h-2 rounded-full bg-[#f97316] inline-block" />
            <span>AI-Powered Racing Intelligence</span>
          </div>
        </div>

        {/* Main headline */}
        <h1 className="font-syne font-extrabold leading-[0.95] tracking-tight mb-0" style={{ fontSize: "clamp(48px, 8vw, 96px)", letterSpacing: "-0.03em" }}>
          <span className="hero-entry hero-entry-2 block text-white" data-testid="text-headline-1">
            Picking
          </span>
          <span className="hero-entry hero-entry-3 block gradient-text-shimmer" data-testid="text-headline-2">
            winners.
          </span>
        </h1>

        {/* Description */}
        <p
          className="hero-entry hero-entry-4 text-white/50 max-w-[480px] leading-relaxed mt-7 mb-9"
          style={{ fontSize: "17px", lineHeight: 1.7 }}
          data-testid="text-tagline"
        >
          Australia's first AI-powered machine learning platform for thoroughbred racing. Made by a punter, for punters.
        </p>

        {/* Liquid glass action buttons */}
        <div className="hero-entry hero-entry-5 flex gap-3 flex-wrap" data-testid="action-buttons">
          <Link href="/ask-stride">
            <span className="btn-liquid" data-testid="button-ask-stride">
              <MessageSquare className="w-4 h-4" />
              Ask Stride
            </span>
          </Link>
          <Link href="/blackbook">
            <span className="btn-liquid-ghost" data-testid="button-open-blackbook">
              <Play className="w-4 h-4" />
              Open Blackbook
            </span>
          </Link>
        </div>

        {/* Horse stats bar */}
        <AnimatePresence mode="wait">
          <motion.div
            key={safeIndex}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0, transition: { duration: 0.6, ease: [0.16, 1, 0.3, 1] } }}
            exit={{ opacity: 0, y: -10, transition: { duration: 0.4 } }}
            className="hero-entry hero-entry-6 mt-12 pt-7 border-t border-white/[0.08]"
            data-testid="horse-stats-bar"
          >
            <p className="text-[10px] uppercase tracking-[0.15em] text-white/30 mb-4">
              Featured Horse
            </p>
            <div className="flex items-center gap-8 flex-wrap">
              <div>
                <p className="font-syne text-2xl font-bold text-white tracking-tight" data-testid="text-horse-name">
                  {currentHorse.name}
                </p>
                <p className="text-[13px] text-white/40" data-testid="text-horse-achievement">
                  {currentHorse.era} &bull; {currentHorse.achievement}
                </p>
              </div>
              <div className="w-px h-10 bg-white/10 hidden sm:block" />
              <div>
                <p className="font-syne text-[28px] font-bold text-[#f97316]">
                  <TrendingUp className="inline w-5 h-5 mr-1 -mt-1" />
                  AI
                </p>
                <p className="text-[10px] uppercase tracking-[0.12em] text-white/35">
                  Powered Analysis
                </p>
              </div>
            </div>

            {/* Slide dots */}
            <div className="flex gap-2 mt-5" data-testid="slideshow-indicators">
              {legendaryHorses.map((horse, index) => (
                <button
                  key={index}
                  onClick={() => goToSlide(index)}
                  className={`slide-dot-liquid ${index === safeIndex ? "active" : ""}`}
                  data-testid={`indicator-${index}`}
                  aria-label={`View ${horse.name}`}
                />
              ))}
            </div>
          </motion.div>
        </AnimatePresence>
      </div>

      {/* Scroll indicator */}
      <div className="absolute bottom-8 left-1/2 -translate-x-1/2 z-10">
        <div className="scroll-indicator-line" />
      </div>

      {/* Bottom gradient */}
      <div className="absolute bottom-0 left-0 right-0 h-32 bg-gradient-to-t from-black to-transparent" />
    </section>
  );
}
