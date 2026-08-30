import React, { useEffect, useRef, useState } from "react";
import { Link } from "wouter";

const HORSES = [
  { src: "/videos/horse4.mp4", name: "WINX" },
  { src: "/videos/horse2.mp4", name: "BLACK CAVIAR" },
  { src: "/videos/horse3.mp4", name: "VIA SISTINA" },
  { src: "/videos/horse1.mp4", name: "KA YING RISING" },
];

const HalideTopo3DHero: React.FC = () => {
  const canvasRef   = useRef<HTMLDivElement>(null);
  const layerRef    = useRef<HTMLDivElement>(null);
  const videoRef    = useRef<HTMLVideoElement>(null);
  const [current, setCurrent] = useState(0);
  const [visible, setVisible] = useState(true);

  // ── Entrance + mouse parallax ───────────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    canvas.style.opacity   = "0";
    canvas.style.transform = "rotateX(90deg) rotateZ(0deg) scale(0.8)";

    const entranceTimer = setTimeout(() => {
      canvas.style.transition = "all 2.5s cubic-bezier(0.16, 1, 0.3, 1)";
      canvas.style.opacity    = "1";
      canvas.style.transform  = "rotateX(55deg) rotateZ(-25deg) scale(1)";
    }, 300);

    const onMouseMove = (e: MouseEvent) => {
      const x = (window.innerWidth  / 2 - e.pageX) / 25;
      const y = (window.innerHeight / 2 - e.pageY) / 25;
      canvas.style.transform = `rotateX(${55 + y / 2}deg) rotateZ(${-25 + x / 2}deg)`;
      const layer = layerRef.current;
      if (layer) {
        layer.style.transform = `translateZ(20px) translate(${x * 0.4}px, ${y * 0.4}px)`;
      }
    };

    window.addEventListener("mousemove", onMouseMove);
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      clearTimeout(entranceTimer);
    };
  }, []);

  // ── Slideshow: 10s per slide, fade out → swap → fade in ────────────────
  useEffect(() => {
    const interval = setInterval(() => {
      // Fade out
      setVisible(false);

      // After fade-out completes, swap the video and fade back in
      setTimeout(() => {
        setCurrent((prev) => (prev + 1) % HORSES.length);
        setVisible(true);
      }, 600);
    }, 10000);

    return () => clearInterval(interval);
  }, []);

  // Play the video whenever the src changes
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.load();
    video.play().catch(() => {});
  }, [current]);

  return (
    <section
      style={{
        background: "#0a0a0a",
        color: "#e0e0e0",
        height: "100vh",
        overflow: "hidden",
        position: "relative",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontFamily: "var(--font-syne, 'Syncopate', sans-serif)",
      }}
    >
      {/* ── Ambient orange glow — left-centre, matching hero section ─────── */}
      <div style={{
        position: "absolute",
        width: 700,
        height: 700,
        top: "15%",
        left: "8%",
        background: "radial-gradient(ellipse, rgba(249,115,22,0.14) 0%, transparent 65%)",
        filter: "blur(80px)",
        pointerEvents: "none",
        zIndex: 1,
      }} />
      {/* Secondary cooler glow top-right */}
      <div style={{
        position: "absolute",
        width: 500,
        height: 500,
        top: "-10%",
        right: "10%",
        background: "radial-gradient(ellipse, rgba(234,88,12,0.10) 0%, transparent 65%)",
        filter: "blur(100px)",
        pointerEvents: "none",
        zIndex: 1,
      }} />

      {/* ── SVG grain filter ───────────────────────────────────────────────── */}
      <svg style={{ position: "absolute", width: 0, height: 0 }}>
        <filter id="halide-grain">
          <feTurbulence type="fractalNoise" baseFrequency="0.65" numOctaves="3" />
          <feColorMatrix type="saturate" values="0" />
        </filter>
      </svg>
      <div
        style={{
          position: "fixed",
          inset: 0,
          pointerEvents: "none",
          zIndex: 100,
          opacity: 0.12,
          filter: "url(#halide-grain)",
        }}
      />

      {/* ── HUD / interface grid ────────────────────────────────────────────── */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          padding: "clamp(2rem, 4vw, 4rem)",
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gridTemplateRows: "auto 1fr auto",
          zIndex: 10,
          pointerEvents: "none",
        }}
      >
        <div style={{ fontWeight: 700, fontSize: "0.7rem", letterSpacing: "0.12em" }}>
          STRIDE_AI
        </div>
        <div style={{ textAlign: "right", fontFamily: "monospace", color: "#f97316", fontSize: "0.65rem", lineHeight: 1.8 }}>
          <div>TRACK: FLEMINGTON</div>
          <div>MODEL DEPTH: 97.4%</div>
        </div>

        <h2
          style={{
            gridColumn: "1 / -1",
            alignSelf: "center",
            fontSize: "clamp(3rem, 10vw, 9rem)",
            lineHeight: 0.85,
            letterSpacing: "-0.04em",
            mixBlendMode: "difference",
            fontWeight: 800,
            margin: 0,
          }}
        >
          STRIDE
          <br />
          RACING
        </h2>

        <div style={{ gridColumn: "1 / -1", display: "flex", justifyContent: "space-between", alignItems: "flex-end", flexWrap: "wrap", gap: "1rem" }}>
          <div style={{ fontFamily: "monospace", fontSize: "0.7rem", lineHeight: 1.9 }}>
            <p style={{ margin: 0 }}>[ ARCHIVE 2026 ]</p>
            <p style={{ margin: 0 }}>VELOCITY ANALYSIS &amp; FORM INTELLIGENCE</p>
          </div>
          <Link href="/ask-stride">
            <span
              style={{
                pointerEvents: "auto",
                display: "inline-block",
                background: "#e0e0e0",
                color: "#0a0a0a",
                padding: "0.9rem 2rem",
                fontWeight: 700,
                fontSize: "0.75rem",
                letterSpacing: "0.08em",
                clipPath: "polygon(0 0, 100% 0, 100% 65%, 88% 100%, 0 100%)",
                transition: "background 0.3s, transform 0.3s",
                cursor: "pointer",
                textDecoration: "none",
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLElement).style.background = "#f97316";
                (e.currentTarget as HTMLElement).style.transform  = "translateY(-4px)";
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLElement).style.background = "#e0e0e0";
                (e.currentTarget as HTMLElement).style.transform  = "translateY(0)";
              }}
            >
              ASK STRIDE
            </span>
          </Link>
        </div>
      </div>

      {/* ── 3-D viewport ───────────────────────────────────────────────────── */}
      <div
        style={{
          perspective: "2000px",
          width: "100vw",
          height: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          overflow: "hidden",
        }}
      >
        <div
          ref={canvasRef}
          style={{
            position: "relative",
            width: "min(800px, 90vw)",
            height: "min(500px, 56vw)",
            transformStyle: "preserve-3d",
            transition: "transform 0.8s cubic-bezier(0.16, 1, 0.3, 1)",
          }}
        >
          {/* Single video layer — fades out/in on slide change */}
          <div
            ref={layerRef}
            style={{
              position: "absolute",
              inset: 0,
              overflow: "hidden",
              border: "1px solid rgba(249,115,22,0.25)",
              boxShadow: "0 0 60px rgba(249,115,22,0.18), inset 0 0 40px rgba(249,115,22,0.06)",
              filter: "contrast(1.1) brightness(0.75) saturate(1.1)",
              opacity: visible ? 1 : 0,
              transition: "opacity 0.5s ease",
            }}
          >
            <video
              ref={videoRef}
              src={HORSES[current].src}
              autoPlay
              loop
              muted
              playsInline
              style={{
                position: "absolute",
                inset: 0,
                width: "100%",
                height: "100%",
                objectFit: "cover",
                objectPosition: "center 30%",
              }}
            />
            {/* Warm colour grade over the video */}
            <div style={{
              position: "absolute",
              inset: 0,
              background: "linear-gradient(135deg, rgba(249,115,22,0.22) 0%, rgba(234,88,12,0.08) 50%, rgba(0,0,0,0.35) 100%)",
              mixBlendMode: "multiply",
              pointerEvents: "none",
            }} />
            {/* Bottom vignette */}
            <div style={{
              position: "absolute",
              inset: 0,
              background: "linear-gradient(to top, rgba(0,0,0,0.55) 0%, transparent 50%)",
              pointerEvents: "none",
            }} />
          </div>

          {/* Topographic contour overlay — orange tinted */}
          <div
            style={{
              position: "absolute",
              width: "200%",
              height: "200%",
              top: "-50%",
              left: "-50%",
              backgroundImage:
                "repeating-radial-gradient(circle at 50% 50%, transparent 0, transparent 40px, rgba(249,115,22,0.06) 41px, transparent 42px)",
              transform: "translateZ(120px)",
              pointerEvents: "none",
            }}
          />
        </div>
      </div>

      {/* ── Scroll hint ─────────────────────────────────────────────────────── */}
      <div
        style={{
          position: "absolute",
          bottom: "2rem",
          left: "50%",
          width: "1px",
          height: "60px",
          background: "linear-gradient(to bottom, #e0e0e0, transparent)",
          animation: "halideScroll 2s infinite ease-in-out",
        }}
      />

      <style>{`
        @keyframes halideScroll {
          0%, 100% { transform: scaleY(0); transform-origin: top; }
          49%       { transform: scaleY(1); transform-origin: top; }
          50%       { transform: scaleY(1); transform-origin: bottom; }
        }
      `}</style>
    </section>
  );
};

export default HalideTopo3DHero;
