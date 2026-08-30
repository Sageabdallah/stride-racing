import { useEffect, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "framer-motion";

import { cn } from "@/lib/utils";

export interface CardFlipProps {
  flipped?: boolean;
  front: ReactNode;
  back: ReactNode;
  className?: string;
  innerClassName?: string;
  faceClassName?: string;
  durationMs?: number;
}

export default function CardFlip({
  flipped = false,
  front,
  back,
  className,
  innerClassName,
  faceClassName,
  durationMs = 700,
}: CardFlipProps) {
  const frontMeasureRef = useRef<HTMLDivElement | null>(null);
  const backMeasureRef = useRef<HTMLDivElement | null>(null);
  const [height, setHeight] = useState(0);

  useEffect(() => {
    const measure = () => {
      const frontHeight = frontMeasureRef.current?.offsetHeight ?? 0;
      const backHeight = backMeasureRef.current?.offsetHeight ?? 0;
      setHeight(Math.max(frontHeight, backHeight));
    };

    measure();

    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", measure);
      return () => window.removeEventListener("resize", measure);
    }

    const observer = new ResizeObserver(measure);
    if (frontMeasureRef.current) observer.observe(frontMeasureRef.current);
    if (backMeasureRef.current) observer.observe(backMeasureRef.current);

    return () => observer.disconnect();
  }, [front, back]);

  return (
    <div
      className={cn("relative w-full [perspective:2000px]", className)}
      style={{ height: height ? `${height}px` : undefined, minHeight: height ? undefined : "88px" }}
    >
      <div className="pointer-events-none absolute left-0 top-0 -z-10 w-full opacity-0">
        <div ref={frontMeasureRef} className="w-full">
          {front}
        </div>
        <div ref={backMeasureRef} className="mt-2 w-full">
          {back}
        </div>
      </div>
      <div
        className={cn("relative h-full w-full", innerClassName)}
        style={{ perspective: "2000px" }}
      >
        <AnimatePresence initial={false} mode="wait">
          {flipped ? (
            <motion.div
              key="back"
              initial={{ rotateY: 90, opacity: 0 }}
              animate={{ rotateY: 0, opacity: 1 }}
              exit={{ rotateY: -90, opacity: 0 }}
              transition={{ duration: durationMs / 1000, ease: [0.22, 1, 0.36, 1] }}
              className={cn("absolute inset-0", faceClassName)}
              style={{ backfaceVisibility: "hidden", transformStyle: "preserve-3d" }}
            >
              {back}
            </motion.div>
          ) : (
            <motion.div
              key="front"
              initial={{ rotateY: -90, opacity: 0 }}
              animate={{ rotateY: 0, opacity: 1 }}
              exit={{ rotateY: 90, opacity: 0 }}
              transition={{ duration: durationMs / 1000, ease: [0.22, 1, 0.36, 1] }}
              className={cn("absolute inset-0", faceClassName)}
              style={{ backfaceVisibility: "hidden", transformStyle: "preserve-3d" }}
            >
              {front}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
