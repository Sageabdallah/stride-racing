import React from "react";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import {
  ArrowUp,
  Square,
  StopCircle,
  Mic,
  Globe,
  BrainCog,
} from "lucide-react";
import { motion } from "framer-motion";
import type { ChatModeState } from "@shared/schema";

import { cn } from "@/lib/utils";

const PROMPT_BOX_STYLE_ID = "stride-ai-prompt-box-styles";
const promptBoxStyles = `
  *:focus-visible {
    outline-offset: 0 !important;
  }
  textarea::-webkit-scrollbar {
    width: 6px;
  }
  textarea::-webkit-scrollbar-track {
    background: transparent;
  }
  textarea::-webkit-scrollbar-thumb {
    background-color: #444444;
    border-radius: 3px;
  }
  textarea::-webkit-scrollbar-thumb:hover {
    background-color: #555555;
  }
`;

function ensurePromptBoxStyles() {
  if (typeof document === "undefined" || document.getElementById(PROMPT_BOX_STYLE_ID)) {
    return;
  }

  const styleSheet = document.createElement("style");
  styleSheet.id = PROMPT_BOX_STYLE_ID;
  styleSheet.innerText = promptBoxStyles;
  document.head.appendChild(styleSheet);
}

interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  className?: string;
}

const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(({ className, ...props }, ref) => (
  <textarea
    ref={ref}
    rows={1}
    className={cn(
      "flex w-full resize-none rounded-md border-none bg-transparent px-3 py-2.5 text-base text-gray-100 placeholder:text-gray-400 focus-visible:outline-none focus-visible:ring-0 disabled:cursor-not-allowed disabled:opacity-50 min-h-[44px]",
      className,
    )}
    {...props}
  />
));
Textarea.displayName = "Textarea";

const TooltipProvider = TooltipPrimitive.Provider;
const Tooltip = TooltipPrimitive.Root;
const TooltipTrigger = TooltipPrimitive.Trigger;

const TooltipContent = React.forwardRef<
  React.ElementRef<typeof TooltipPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>
>(({ className, sideOffset = 4, ...props }, ref) => (
  <TooltipPrimitive.Portal>
    <TooltipPrimitive.Content
      ref={ref}
      sideOffset={sideOffset}
      className={cn(
        "z-50 overflow-hidden rounded-md border border-[#333333] bg-[#1F2023] px-3 py-1.5 text-sm text-white shadow-md animate-in fade-in-0 zoom-in-95 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2",
        className,
      )}
      {...props}
    />
  </TooltipPrimitive.Portal>
));
TooltipContent.displayName = TooltipPrimitive.Content.displayName;

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "default" | "outline" | "ghost";
  size?: "default" | "sm" | "lg" | "icon";
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "default", size = "default", ...props }, ref) => {
    const variantClasses = {
      default: "bg-white hover:bg-white/80 text-black",
      outline: "border border-[#444444] bg-transparent hover:bg-[#3A3A40] text-white",
      ghost: "bg-transparent hover:bg-[#3A3A40] text-white",
    };

    const sizeClasses = {
      default: "h-10 px-4 py-2",
      sm: "h-8 px-3 text-sm",
      lg: "h-12 px-6",
      icon: "h-8 w-8 rounded-full aspect-[1/1]",
    };

    return (
      <button
        ref={ref}
        className={cn(
          "inline-flex items-center justify-center font-medium transition-colors focus-visible:outline-none disabled:pointer-events-none disabled:opacity-50",
          variantClasses[variant],
          sizeClasses[size],
          className,
        )}
        {...props}
      />
    );
  },
);
Button.displayName = "PromptBoxButton";

interface VoiceRecorderProps {
  isRecording: boolean;
  onStartRecording: () => void;
  onStopRecording: (duration: number) => void;
  visualizerBars?: number;
}

const VoiceRecorder: React.FC<VoiceRecorderProps> = ({
  isRecording,
  onStartRecording,
  onStopRecording,
  visualizerBars = 24,
}) => {
  const [time, setTime] = React.useState(0);
  const latestTimeRef = React.useRef(0);

  React.useEffect(() => {
    latestTimeRef.current = time;
  }, [time]);

  React.useEffect(() => {
    if (!isRecording) {
      return;
    }

    onStartRecording();
    latestTimeRef.current = 0;
    setTime(0);

    const timer = setInterval(() => {
      setTime((current) => {
        const next = current + 1;
        latestTimeRef.current = next;
        return next;
      });
    }, 1000);

    return () => {
      clearInterval(timer);
      onStopRecording(latestTimeRef.current);
      latestTimeRef.current = 0;
      setTime(0);
    };
  }, [isRecording, onStartRecording, onStopRecording]);

  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
  };

  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center w-full transition-all duration-300 py-3",
        isRecording ? "opacity-100" : "opacity-0 h-0",
      )}
    >
      <div className="flex items-center gap-2 mb-3">
        <div className="h-2 w-2 rounded-full bg-red-500 animate-pulse" />
        <span className="font-mono text-sm text-white/80">{formatTime(time)}</span>
      </div>
      <div className="w-full h-10 flex items-center justify-center gap-0.5 px-4">
        {[...Array(visualizerBars)].map((_, index) => (
          <div
            key={index}
            className="w-0.5 rounded-full bg-white/50 animate-pulse"
            style={{
              height: `${Math.max(18, ((index * 37) % 100))}%`,
              animationDelay: `${index * 0.05}s`,
              animationDuration: `${0.55 + (index % 5) * 0.1}s`,
            }}
          />
        ))}
      </div>
    </div>
  );
};

interface PromptInputContextType {
  isLoading: boolean;
  value: string;
  setValue: (value: string) => void;
  maxHeight: number | string;
  onSubmit?: () => void;
  disabled?: boolean;
}

const PromptInputContext = React.createContext<PromptInputContextType | null>(null);

function usePromptInput() {
  const context = React.useContext(PromptInputContext);
  if (!context) {
    throw new Error("usePromptInput must be used within a PromptInput");
  }
  return context;
}

interface PromptInputProps {
  isLoading?: boolean;
  value?: string;
  onValueChange?: (value: string) => void;
  maxHeight?: number | string;
  onSubmit?: () => void;
  children: React.ReactNode;
  className?: string;
  disabled?: boolean;
  onDragOver?: (e: React.DragEvent) => void;
  onDragLeave?: (e: React.DragEvent) => void;
  onDrop?: (e: React.DragEvent) => void;
}

const PromptInput = React.forwardRef<HTMLDivElement, PromptInputProps>(
  (
    {
      className,
      isLoading = false,
      maxHeight = 240,
      value,
      onValueChange,
      onSubmit,
      children,
      disabled = false,
      onDragOver,
      onDragLeave,
      onDrop,
    },
    ref,
  ) => {
    const [internalValue, setInternalValue] = React.useState(value ?? "");
    const currentValue = value ?? internalValue;

    const handleChange = (nextValue: string) => {
      if (value === undefined) {
        setInternalValue(nextValue);
      }
      onValueChange?.(nextValue);
    };

    return (
      <TooltipProvider>
        <PromptInputContext.Provider
          value={{
            isLoading,
            value: currentValue,
            setValue: handleChange,
            maxHeight,
            onSubmit,
            disabled,
          }}
        >
          <div
            ref={ref}
            className={cn(
              "rounded-3xl border border-[#444444] bg-[#1F2023] p-2 shadow-[0_8px_30px_rgba(0,0,0,0.24)] transition-all duration-300",
              isLoading && "border-red-500/70",
              className,
            )}
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
            onDrop={onDrop}
          >
            {children}
          </div>
        </PromptInputContext.Provider>
      </TooltipProvider>
    );
  },
);
PromptInput.displayName = "PromptInput";

interface PromptInputTextareaProps extends React.ComponentProps<typeof Textarea> {
  disableAutosize?: boolean;
  placeholder?: string;
}

const PromptInputTextarea: React.FC<PromptInputTextareaProps> = ({
  className,
  onKeyDown,
  disableAutosize = false,
  placeholder,
  ...props
}) => {
  const { value, setValue, maxHeight, onSubmit, disabled } = usePromptInput();
  const textareaRef = React.useRef<HTMLTextAreaElement>(null);

  React.useEffect(() => {
    if (disableAutosize || !textareaRef.current) {
      return;
    }

    textareaRef.current.style.height = "auto";
    textareaRef.current.style.height =
      typeof maxHeight === "number"
        ? `${Math.min(textareaRef.current.scrollHeight, maxHeight)}px`
        : `min(${textareaRef.current.scrollHeight}px, ${maxHeight})`;
  }, [value, maxHeight, disableAutosize]);

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      onSubmit?.();
    }
    onKeyDown?.(event);
  };

  return (
    <Textarea
      ref={textareaRef}
      value={value}
      onChange={(event) => setValue(event.target.value)}
      onKeyDown={handleKeyDown}
      className={cn("text-base", className)}
      disabled={disabled}
      placeholder={placeholder}
      {...props}
    />
  );
};

interface PromptInputActionsProps extends React.HTMLAttributes<HTMLDivElement> {}

const PromptInputActions: React.FC<PromptInputActionsProps> = ({ children, className, ...props }) => (
  <div className={cn("flex items-center gap-2", className)} {...props}>
    {children}
  </div>
);

interface PromptInputActionProps {
  tooltip: React.ReactNode;
  children: React.ReactNode;
  side?: "top" | "bottom" | "left" | "right";
  delayDuration?: number;
}

const PromptInputAction: React.FC<PromptInputActionProps> = ({
  tooltip,
  children,
  side = "top",
  delayDuration = 0,
}) => {
  const { disabled } = usePromptInput();

  return (
    <Tooltip delayDuration={delayDuration}>
      <TooltipTrigger asChild disabled={disabled}>
        {children}
      </TooltipTrigger>
      <TooltipContent side={side}>{tooltip}</TooltipContent>
    </Tooltip>
  );
};

interface PromptInputBoxProps {
  onSend?: (message: string, files?: File[]) => void;
  isLoading?: boolean;
  placeholder?: string;
  className?: string;
  value?: string;
  onValueChange?: (value: string) => void;
  mode?: ChatModeState;
  onModeChange?: (mode: ChatModeState) => void;
}

export const PromptInputBox = React.forwardRef<HTMLDivElement, PromptInputBoxProps>(
  (
    {
      onSend = () => {},
      isLoading = false,
      placeholder = "Type your message here...",
      className,
      value,
      onValueChange,
      mode,
      onModeChange,
    },
    ref,
  ) => {
    const [internalInput, setInternalInput] = React.useState(value ?? "");
    const [internalMode, setInternalMode] = React.useState<ChatModeState>(mode ?? { brain: false, search: false });
    const [isRecording, setIsRecording] = React.useState(false);

    React.useEffect(() => {
      ensurePromptBoxStyles();
    }, []);

    React.useEffect(() => {
      if (value !== undefined) {
        setInternalInput(value);
      }
    }, [value]);

    React.useEffect(() => {
      if (mode !== undefined) {
        setInternalMode(mode);
      }
    }, [mode]);

    const input = value ?? internalInput;
    const resolvedMode = mode ?? internalMode;
    const setInput = React.useCallback(
      (nextValue: string) => {
        if (value === undefined) {
          setInternalInput(nextValue);
        }
        onValueChange?.(nextValue);
      },
      [onValueChange, value],
    );

    const setMode = React.useCallback(
      (nextMode: ChatModeState) => {
        if (mode === undefined) {
          setInternalMode(nextMode);
        }
        onModeChange?.(nextMode);
      },
      [mode, onModeChange],
    );

    const handleToggleChange = (toggle: "search" | "brain") => {
      if (toggle === "search") {
        setMode(resolvedMode.search ? { brain: false, search: false } : { brain: false, search: true });
        return;
      }

      setMode(resolvedMode.brain ? { brain: false, search: false } : { brain: true, search: false });
    };

    const handleSubmit = () => {
      if (!input.trim()) {
        return;
      }

      onSend(input.trim());
      setInput("");
    };

    const handleStartRecording = React.useCallback(() => {
      // Placeholder for future media recorder integration.
    }, []);

    const handleStopRecording = React.useCallback(
      (duration: number) => {
        setIsRecording(false);
        onSend(`[Voice message - ${duration} seconds]`, []);
      },
      [onSend],
    );

    const hasContent = input.trim() !== "";

    return (
      <PromptInput
        ref={ref}
        value={input}
        onValueChange={setInput}
        isLoading={isLoading}
        onSubmit={handleSubmit}
        className={cn(
          "w-full bg-[#1F2023] border-[#444444] shadow-[0_8px_30px_rgba(0,0,0,0.24)] transition-all duration-300 ease-in-out",
          isRecording && "border-red-500/70",
          className,
        )}
        disabled={isLoading || isRecording}
      >
          <div
            className={cn(
              "transition-all duration-300",
              isRecording ? "h-0 overflow-hidden opacity-0" : "opacity-100",
            )}
          >
            <PromptInputTextarea
              placeholder={
                resolvedMode.search
                  ? "Search the web..."
                  : resolvedMode.brain
                    ? "Think deeply about this..."
                    : placeholder
              }
              className="text-base"
            />
          </div>

          {isRecording && (
            <VoiceRecorder
              isRecording={isRecording}
              onStartRecording={handleStartRecording}
              onStopRecording={handleStopRecording}
            />
          )}

          <PromptInputActions className="flex items-center justify-between gap-2 p-0 pt-2">
            <div
              className={cn(
                "flex items-center gap-1 transition-opacity duration-300 min-w-0 flex-1",
                isRecording ? "opacity-0 invisible h-0" : "opacity-100 visible",
              )}
            >
              <div className="flex w-full flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => handleToggleChange("brain")}
                  className={cn(
                    "group min-w-[180px] flex-1 rounded-2xl border px-3 py-2 text-left transition-all duration-200",
                    resolvedMode.brain
                      ? "border-[#8B5CF6]/75 bg-[#8B5CF6]/16 text-white shadow-[0_0_0_1px_rgba(139,92,246,0.16)]"
                      : resolvedMode.search
                        ? "border-white/[0.05] bg-white/[0.02] text-white/45 hover:border-white/[0.10] hover:text-white/70"
                        : "border-white/[0.08] bg-white/[0.03] text-white/72 hover:border-[#8B5CF6]/40 hover:bg-[#8B5CF6]/10 hover:text-white",
                  )}
                >
                  <div className="flex items-center gap-2.5">
                    <motion.div
                      animate={{ rotate: resolvedMode.brain ? 360 : 0, scale: resolvedMode.brain ? 1.06 : 1 }}
                      whileHover={{
                        rotate: resolvedMode.brain ? 360 : 10,
                        scale: 1.05,
                        transition: { type: "spring", stiffness: 300, damping: 10 },
                      }}
                      transition={{ type: "spring", stiffness: 260, damping: 25 }}
                      className={cn(
                        "flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border transition-colors",
                        resolvedMode.brain
                          ? "border-[#8B5CF6]/70 bg-[#8B5CF6]/18 text-[#d9c7ff]"
                          : "border-white/[0.08] bg-white/[0.04] text-white/60 group-hover:border-[#8B5CF6]/35 group-hover:text-[#d9c7ff]",
                      )}
                    >
                      <BrainCog className="h-4 w-4" />
                    </motion.div>
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className={cn("text-[12px] font-semibold tracking-[0.02em]", resolvedMode.brain ? "text-[#e4d8ff]" : "text-white/88")}>
                          Deep Thought
                        </span>
                        {resolvedMode.brain && (
                          <span className="rounded-full border border-[#8B5CF6]/40 bg-[#8B5CF6]/14 px-2 py-0.5 text-[9px] font-semibold uppercase tracking-[0.2em] text-[#e4d8ff]">
                            On
                          </span>
                        )}
                      </div>
                      <p className="mt-0.5 text-[10px] leading-4 text-white/42">
                        Visible reasoning for analysis, strategy, comparisons, and multi-step problem solving
                      </p>
                    </div>
                  </div>
                </button>

                <button
                  type="button"
                  onClick={() => handleToggleChange("search")}
                  className={cn(
                    "group min-w-[160px] flex-1 rounded-2xl border px-3 py-2 text-left transition-all duration-200",
                    resolvedMode.search
                      ? "border-[#1EAEDB]/75 bg-[#1EAEDB]/16 text-white shadow-[0_0_0_1px_rgba(30,174,219,0.16)]"
                      : resolvedMode.brain
                        ? "border-white/[0.05] bg-white/[0.02] text-white/45 hover:border-white/[0.10] hover:text-white/70"
                        : "border-white/[0.08] bg-white/[0.03] text-white/72 hover:border-[#1EAEDB]/40 hover:bg-[#1EAEDB]/10 hover:text-white",
                  )}
                >
                  <div className="flex items-center gap-2.5">
                    <motion.div
                      animate={{ rotate: resolvedMode.search ? 360 : 0, scale: resolvedMode.search ? 1.06 : 1 }}
                      whileHover={{
                        rotate: resolvedMode.search ? 360 : 10,
                        scale: 1.05,
                        transition: { type: "spring", stiffness: 300, damping: 10 },
                      }}
                      transition={{ type: "spring", stiffness: 260, damping: 25 }}
                      className={cn(
                        "flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border transition-colors",
                        resolvedMode.search
                          ? "border-[#1EAEDB]/70 bg-[#1EAEDB]/18 text-[#a7eeff]"
                          : "border-white/[0.08] bg-white/[0.04] text-white/60 group-hover:border-[#1EAEDB]/35 group-hover:text-[#a7eeff]",
                      )}
                    >
                      <Globe className="h-4 w-4" />
                    </motion.div>
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className={cn("text-[12px] font-semibold tracking-[0.02em]", resolvedMode.search ? "text-[#c9f6ff]" : "text-white/88")}>
                          Search
                        </span>
                        {resolvedMode.search && (
                          <span className="rounded-full border border-[#1EAEDB]/40 bg-[#1EAEDB]/14 px-2 py-0.5 text-[9px] font-semibold uppercase tracking-[0.2em] text-[#c9f6ff]">
                            On
                          </span>
                        )}
                      </div>
                      <p className="mt-0.5 text-[10px] leading-4 text-white/42">
                        Live internet retrieval with selected sources, visible trace, and inline citations
                      </p>
                    </div>
                  </div>
                </button>
              </div>
            </div>

            <PromptInputAction
              tooltip={
                isLoading ? "Stop generation" : isRecording ? "Stop recording" : hasContent ? "Send message" : "Voice message"
              }
            >
              <Button
                variant="default"
                size="icon"
                className={cn(
                  "h-8 w-8 rounded-full transition-all duration-200",
                  isRecording
                    ? "bg-transparent hover:bg-gray-600/30 text-red-500 hover:text-red-400"
                    : hasContent
                      ? "bg-white hover:bg-white/80 text-[#1F2023]"
                      : "bg-transparent hover:bg-gray-600/30 text-[#9CA3AF] hover:text-[#D1D5DB]",
                )}
                onClick={() => {
                  if (isRecording) {
                    setIsRecording(false);
                  } else if (hasContent) {
                    handleSubmit();
                  } else {
                    setIsRecording(true);
                  }
                }}
                disabled={isLoading && !hasContent}
              >
                {isLoading ? (
                  <Square className="h-4 w-4 fill-[#1F2023] animate-pulse" />
                ) : isRecording ? (
                  <StopCircle className="h-5 w-5 text-red-500" />
                ) : hasContent ? (
                  <ArrowUp className="h-4 w-4 text-[#1F2023]" />
                ) : (
                  <Mic className="h-5 w-5 text-[#1F2023] transition-colors" />
                )}
              </Button>
            </PromptInputAction>
          </PromptInputActions>
      </PromptInput>
    );
  },
);

PromptInputBox.displayName = "PromptInputBox";
