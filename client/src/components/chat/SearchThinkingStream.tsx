import { motion } from "framer-motion";
import { CheckCircle2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { ChatModeTheme } from "@/lib/chatModes";
import { cn } from "@/lib/utils";

interface SearchThinkingStreamProps {
  thoughts: string[];
  activeIndex: number;
  theme: ChatModeTheme;
}

export function SearchThinkingStream({ thoughts, activeIndex, theme }: SearchThinkingStreamProps) {
  const [displayedChars, setDisplayedChars] = useState(0);
  const [showCursor, setShowCursor] = useState(true);
  const timerRef  = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cursorRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const clampedIndex  = Math.min(activeIndex, thoughts.length - 1);
  const activeThought = thoughts[clampedIndex] ?? "";

  // Reset and start typing whenever the active thought changes
  useEffect(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    setDisplayedChars(0);
    setShowCursor(true);

    let i = 0;
    function typeNext() {
      if (i >= activeThought.length) return;
      i++;
      setDisplayedChars(i);
      const char = activeThought[i - 1];
      const delay = /[.,!?—]/.test(char) ? 120 + Math.random() * 80 : 16 + Math.random() * 18;
      timerRef.current = setTimeout(typeNext, delay);
    }
    typeNext();
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [activeThought]);

  // Blink cursor
  useEffect(() => {
    cursorRef.current = setInterval(() => setShowCursor((v) => !v), 530);
    return () => {
      if (cursorRef.current) clearInterval(cursorRef.current);
    };
  }, []);

  return (
    <div className="space-y-2 font-mono text-xs leading-6">
      {/* Completed thoughts */}
      {thoughts.slice(0, clampedIndex).map((thought, i) => (
        <motion.div
          key={i}
          className="flex items-start gap-2"
          initial={{ opacity: 0, x: -4 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.2, ease: "easeOut" }}
        >
          <CheckCircle2 className={cn("mt-[3px] h-3 w-3 shrink-0", theme.accentTextClass)} />
          <p className="text-white/45">{thought}</p>
        </motion.div>
      ))}

      {/* Active thought being typed */}
      {activeThought && (
        <div className="flex items-start gap-2">
          {/* Pulsing dot for active thought */}
          <motion.div
            className={cn("mt-[5px] h-2.5 w-2.5 shrink-0 rounded-full", theme.accentStrongBgClass)}
            animate={{ scale: [1, 1.3, 1], opacity: [0.55, 1, 0.55] }}
            transition={{ duration: 1.2, repeat: Infinity, ease: "easeInOut" }}
          />
          <p className="text-white/88">
            {activeThought.slice(0, displayedChars)}
            <span
              className={cn(
                "inline-block w-[1px] h-[1em] align-middle ml-[1px]",
                theme.accentStrongBgClass,
                displayedChars < activeThought.length || showCursor ? "opacity-100" : "opacity-0",
              )}
            />
          </p>
        </div>
      )}
    </div>
  );
}
