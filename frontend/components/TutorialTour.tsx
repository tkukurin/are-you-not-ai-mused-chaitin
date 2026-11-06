"use client";

import { useCallback, useEffect, useMemo, useState, type CSSProperties } from "react";

type TourStep = {
  title: string;
  body: string;
  target?: string;
};

const STEPS: TourStep[] = [
  {
    title: "Welcome",
    body: "This app builds itself from chat — Muse Glimmer turns messages into cards.",
  },
  {
    title: "1 · Speak as someone",
    body: "Speak as someone — click a member here to talk as them; ＋ adds people.",
    target: '[data-tour="viewer"]',
  },
  {
    title: "2 · Group chats",
    body: "Your chats live here — ＋ starts a brand-new blank UI.",
    target: '[data-tour="chats"]',
  },
  {
    title: "3 · People",
    body: "Add/remove members of this chat anytime.",
    target: '[data-tour="members"]',
  },
  {
    title: "4 · Ask the model to build",
    body: "Try a chip or type 'Make a checklist for…'; cards appear in that person's workspace.",
    target: '[data-tour="composer"]',
  },
  {
    title: "5 · Workspaces",
    body: "Every person gets a different app from the same chat. Add cards by hand too.",
    target: '[data-tour="workspace"]',
  },
  {
    title: "6 · Merge",
    body: "Combine everyone's workspaces into one shared app.",
    target: '[data-tour="merge"]',
  },
  {
    title: "History",
    body: "Every message and card is a commit; click to rewind.",
    target: '[data-tour="history"]',
  },
];

type Rect = { top: number; left: number; width: number; height: number };

export default function TutorialTour({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [step, setStep] = useState(0);
  const [rect, setRect] = useState<Rect | null>(null);

  // Restart from the welcome step every time the tour is (re)opened.
  useEffect(() => {
    if (open) setStep(0);
  }, [open ]);

  const measure = useCallback(() => {
    const selector = STEPS[step]?.target;
    if (!selector) {
      setRect(null);
      return;
    }
    const el = document.querySelector(selector);
    if (!el) {
      setRect(null);
      return;
    }
    const r = el.getBoundingClientRect();
    const pad = 6;
    setRect({
      top: r.top - pad,
      left: r.left - pad,
      width: r.width + pad * 2,
      height: r.height + pad * 2,
    });
  }, [step]);

  // Scroll the target into view first, then measure it (re-measuring after
  // the smooth scroll settles so the spotlight lands in the right place).
  useEffect(() => {
    if (!open) return;
    const selector = STEPS[step]?.target;
    if (!selector) {
      setRect(null);
      return;
    }
    const el = document.querySelector(selector);
    if (!el) {
      setRect(null);
      return;
    }
    el.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
    measure();
    const t1 = window.setTimeout(measure, 80);
    const t2 = window.setTimeout(measure, 380);
    return () => {
      window.clearTimeout(t1);
      window.clearTimeout(t2);
    };
  }, [open, step, measure]);

  // Keep the spotlight glued to its target while the page moves.
  useEffect(() => {
    if (!open) return;
    const onChange = () => measure();
    window.addEventListener("resize", onChange);
    window.addEventListener("scroll", onChange, true);
    return () => {
      window.removeEventListener("resize", onChange);
      window.removeEventListener("scroll", onChange, true);
    };
  }, [open, measure]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowRight") setStep((s) => Math.min(s + 1, STEPS.length - 1));
      else if (e.key === "ArrowLeft") setStep((s) => Math.max(s - 1, 0));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const cardStyle: CSSProperties = useMemo(() => {
    if (!rect || typeof window === "undefined") return {};
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const width = Math.min(360, vw - 24);
    const estHeight = 232;
    const fitsBelow = rect.top + rect.height + 12 + estHeight <= vh;
    const rawTop = fitsBelow ? rect.top + rect.height + 12 : rect.top - estHeight - 12;
    const top = Math.min(Math.max(12, rawTop), Math.max(12, vh - estHeight - 12));
    const left = Math.min(Math.max(12, rect.left), Math.max(12, vw - width - 12));
    return { top, left, width };
  }, [rect]);

  if (!open) return null;

  const current = STEPS[step];
  const isLast = step === STEPS.length - 1;

  return (
    <div className="tour-overlay" role="dialog" aria-modal="true" aria-label="Tutorial tour">
      {rect ? (
        <div
          className="tour-highlight"
          style={{ top: rect.top, left: rect.left, width: rect.width, height: rect.height }}
        />
      ) : (
        <div className="tour-backdrop" />
      )}
      <div className={`tour-card ${rect ? "" : "centered"}`} style={cardStyle}>
        <button type="button" className="tour-close" aria-label="Close tutorial" onClick={onClose}>
          ×
        </button>
        <div className="tour-step-count">
          Step {step + 1} of {STEPS.length}
        </div>
        <h2 className="tour-title">{current.title}</h2>
        <p className="tour-body">{current.body}</p>
        <div className="tour-dots" aria-hidden>
          {STEPS.map((s, i) => (
            <span key={s.title} className={`tour-dot ${i === step ? "active" : ""}`} />
          ))}
        </div>
        <div className="tour-actions">
          <button type="button" className="button ghost small" onClick={onClose}>
            Skip
          </button>
          <span className="tour-actions-right">
            <button
              type="button"
              className="button secondary small"
              onClick={() => setStep((s) => Math.max(s - 1, 0))}
              disabled={step === 0}
            >
              Back
            </button>
            {isLast ? (
              <button type="button" className="button small" onClick={onClose}>
                Finish
              </button>
            ) : (
              <button type="button" className="button small" onClick={() => setStep((s) => Math.min(s + 1, STEPS.length - 1))}>
                Next
              </button>
            )}
          </span>
        </div>
      </div>
    </div>
  );
}
