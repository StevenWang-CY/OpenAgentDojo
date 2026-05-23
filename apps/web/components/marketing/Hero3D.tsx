"use client";

import * as React from "react";

/**
 * Hero3D — refined glass diorama for the landing hero.
 *
 * The reference photo's "device" is structurally a translucent glass FRAME
 * containing TWO more-opaque white sub-cards (checklist on the left, score
 * on the right). That contrast — frosted housing around bright inner
 * panels — is what reads as a real 3D device. A single card with internal
 * columns would have been the obvious move but it would not survive the
 * comparison.
 *
 * Composition (front-to-back):
 *
 *   .device-contact   diffuse contact shadow grounding the device into the slab
 *   .float-sq-*       six small floating glass cubes (each cube is the
 *                     element + a ::before back-panel)
 *   .plinth-front     darker front face of the 3D pedestal
 *   .plinth-top       brighter top face of the pedestal
 *   .plinth-rim       thin specular highlight along the bottom rim
 *   .device-back      offset translucent ghost behind the device
 *   .device           outer translucent frame (the housing)
 *     .inner-card[checklist]   bright white sub-card, 4 rows + dividers
 *     .inner-card[score]       bright white sub-card, ring + sparkline
 *   .bot-tile-back    offset translucent ghost behind the bot tile
 *   .bot-tile         focal blue-tinted bot square out in front
 *
 * Lighting is consistent: a virtual light source at upper-left, so every
 * surface gets a bright top/left edge and a darker bottom/right edge,
 * expressed via asymmetric border colors, inset highlights, and a radial
 * inner gradient seeded at 0%,0%.
 */
export function Hero3D() {
  return (
    <div className="scene-wrap" aria-hidden="true">
      <style>{SCENE_CSS}</style>
      <div className="scene-stage">
        <div className="device-contact" />

        <div className="float-sq sq-1" />
        <div className="float-sq sq-2" />
        <div className="float-sq sq-3" />
        <div className="float-sq sq-4" />
        <div className="float-sq sq-5" />
        <div className="float-sq sq-6" />

        <div className="plinth-front" />
        <div className="plinth-top" />
        <div className="plinth-rim" />

        <div className="device-back" />
        <div className="device">
          <p className="device-title">Supervise. Don&rsquo;t just approve.</p>
          <div className="inner-row">
            {/* LEFT: 4-row checklist with hairline dividers */}
            <div className="inner-card">
              <ul className="checklist">
                <ChecklistItem label="Prompt" done glyph={<PromptGlyph />} />
                <ChecklistItem label="Review" done glyph={<ReviewGlyph />} />
                <ChecklistItem label="Verify" glyph={<VerifyGlyph />} />
                <ChecklistItem label="Context" glyph={<ContextGlyph />} />
              </ul>
            </div>

            {/* RIGHT: score ring + sparkline */}
            <div className="inner-card score-card">
              <span className="score-label">Overall Score</span>
              <ScoreRing value={78} />
              <TrendLine />
            </div>
          </div>
        </div>

        <div className="bot-tile-back" />
        <div className="bot-tile">
          <BotGlyph />
        </div>
      </div>
    </div>
  );
}

/* ── Checklist row ────────────────────────────────────────────────────── */

function ChecklistItem({
  label,
  glyph,
  done,
}: {
  label: string;
  glyph: React.ReactNode;
  done?: boolean;
}) {
  return (
    <li className={`check-item ${done ? "done" : "todo"}`}>
      <span className="icon" aria-hidden="true">
        {glyph}
      </span>
      {label}
      <span className="check" aria-hidden="true">
        {done ? "✓" : ""}
      </span>
    </li>
  );
}

/* ── SVG glyphs ───────────────────────────────────────────────────────── */

function glyphProps(): React.SVGProps<SVGSVGElement> {
  return {
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round",
    strokeLinejoin: "round",
  };
}

const PromptGlyph = () => (
  <svg {...glyphProps()}>
    <path d="M21 12a8 8 0 0 1-11.6 7.1L4 21l1.9-5.4A8 8 0 1 1 21 12z" />
    <circle cx="9" cy="12" r="0.8" fill="currentColor" stroke="none" />
    <circle cx="15" cy="12" r="0.8" fill="currentColor" stroke="none" />
  </svg>
);
const ReviewGlyph = () => (
  <svg {...glyphProps()}>
    <path d="M12 3l8 3v6c0 5-4 8-8 9-4-1-8-4-8-9V6z" />
  </svg>
);
const VerifyGlyph = () => (
  <svg {...glyphProps()}>
    <rect x="4" y="4" width="16" height="16" rx="3" />
    <circle cx="12" cy="12" r="3.5" />
  </svg>
);
const ContextGlyph = () => (
  <svg {...glyphProps()}>
    <rect x="4" y="10" width="16" height="11" rx="2" />
    <path d="M8 10V7a4 4 0 0 1 8 0v3" />
  </svg>
);

/**
 * Bot face — rounded square head with eye ovals, flat neutral mouth, tiny
 * antenna pair on top, side ear nubs. The reference's robot is NOT smiling;
 * it reads as focused. Mouth is a short flat line, not an upward curve.
 */
const BotGlyph = () => (
  <svg
    viewBox="0 0 64 64"
    fill="none"
    stroke="currentColor"
    strokeWidth={2.2}
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <line x1="22" y1="10" x2="22" y2="14" />
    <line x1="42" y1="10" x2="42" y2="14" />
    <rect
      x="11"
      y="15"
      width="42"
      height="36"
      rx="11"
      fill="oklch(100% 0 0 / 0.92)"
    />
    <ellipse
      cx="23"
      cy="30"
      rx="2.2"
      ry="2.8"
      fill="currentColor"
      stroke="none"
    />
    <ellipse
      cx="41"
      cy="30"
      rx="2.2"
      ry="2.8"
      fill="currentColor"
      stroke="none"
    />
    <line x1="27" y1="40" x2="37" y2="40" strokeWidth={2.4} />
    <rect
      x="7"
      y="26"
      width="4"
      height="8"
      rx="1.6"
      fill="currentColor"
      stroke="none"
    />
    <rect
      x="53"
      y="26"
      width="4"
      height="8"
      rx="1.6"
      fill="currentColor"
      stroke="none"
    />
  </svg>
);

/* ── Score ring + trend line ──────────────────────────────────────────── */

function ScoreRing({ value }: { value: number }) {
  const circumference = 2 * Math.PI * 40;
  const offset = circumference * (1 - value / 100);
  return (
    <div
      className="score-ring-wrap"
      role="img"
      aria-label={`Score ${value} of 100`}
    >
      <svg width={96} height={96} viewBox="0 0 100 100">
        <defs>
          <linearGradient id="hero3d-ring-grad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="oklch(78% 0.1 264)" />
            <stop offset="100%" stopColor="oklch(54% 0.18 264)" />
          </linearGradient>
        </defs>
        <circle
          cx="50"
          cy="50"
          r="40"
          fill="none"
          stroke="oklch(94% 0.012 245)"
          strokeWidth="8"
        />
        <circle
          cx="50"
          cy="50"
          r="40"
          fill="none"
          stroke="url(#hero3d-ring-grad)"
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          transform="rotate(-90 50 50)"
        />
      </svg>
      <span className="num">{value}</span>
      <span className="max">/100</span>
    </div>
  );
}

const TrendLine = () => (
  <svg
    className="trend"
    viewBox="0 0 120 30"
    preserveAspectRatio="none"
    aria-hidden="true"
  >
    <polyline
      fill="none"
      stroke="oklch(54% 0.18 264)"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      points="0,24 14,20 28,22 42,16 56,18 70,10 84,12 100,5 120,3"
    />
  </svg>
);

/* ── Scene CSS ─────────────────────────────────────────────────────────
 * Inlined as a <style> child so all the positional CSS lives next to the
 * markup that depends on it. No utility-class noise; this is a single
 * highly-specific visual, not a reusable component family. */

const SCENE_CSS = `
.scene-wrap {
  position: relative;
  perspective: 2400px;
  perspective-origin: 30% 55%;
  min-height: 800px;
}
.scene-stage {
  position: absolute;
  inset: 0;
  transform-style: preserve-3d;
  /* Reference photo is shot near head-on with a small 3/4 reveal.
   * A lighter tilt keeps inner text readable while still showing
   * real edge thickness on every glass surface.
   *
   * Transform reads right-to-left:
   *   1) scale(1.18)            — composition reads bigger
   *   2) rotateX/Y              — 3/4 perspective tilt
   *   3) translate(64px, 60px)  — drops the scaled+tilted stage
   *                               down + right so the bot-tile
   *                               clears the heading's right edge
   *                               on the left column instead of
   *                               overlapping it
   * transform-origin pulled up to 36% biases the scale growth
   * downward, compounding with the translate for a clean
   * "bigger, lower, righter" feel without touching per-element
   * coords. */
  transform: translate(64px, 60px) rotateY(-9deg) rotateX(2deg) scale(1.18);
  transform-origin: 50% 36%;
}

/* Contact shadow where the device meets the plinth top — a narrow
 * diffuse band that grounds the device into the slab. */
.device-contact {
  position: absolute;
  top: 396px;
  left: 8%; right: 10%;
  height: 10px;
  background: radial-gradient(ellipse 80% 100% at 50% 0%,
    oklch(38% 0.02 245 / 0.28), transparent 75%);
  filter: blur(3.5px);
  transform: translateZ(-5px);
  pointer-events: none;
}

/* Floating glass cubes */
.float-sq {
  position: absolute;
  border-radius: 14px;
  background:
    radial-gradient(110% 100% at 0% 0%,
      oklch(100% 0 0 / 0.82) 0%, oklch(100% 0 0 / 0.18) 65%),
    linear-gradient(140deg,
      oklch(100% 0 0 / 0.65) 0%, oklch(94% 0.014 245 / 0.32) 100%);
  backdrop-filter: blur(18px) saturate(135%);
  -webkit-backdrop-filter: blur(18px) saturate(135%);
  border: 1px solid oklch(100% 0 0 / 0.72);
  border-top-color: oklch(100% 0 0 / 1);
  border-left-color: oklch(100% 0 0 / 0.92);
  border-bottom-color: oklch(100% 0 0 / 0.25);
  border-right-color: oklch(100% 0 0 / 0.2);
  box-shadow:
    0 28px 40px -22px oklch(38% 0.03 250 / 0.3),
    0 12px 22px -8px oklch(38% 0.03 250 / 0.2),
    0 3px 6px oklch(38% 0.03 250 / 0.08),
    inset 0 2.5px 0 oklch(100% 0 0 / 1),
    inset 1.5px 0 0 oklch(100% 0 0 / 0.75),
    inset 0 -1.5px 0 oklch(70% 0.02 250 / 0.2),
    inset -1.5px 0 0 oklch(70% 0.02 250 / 0.14);
}
.float-sq::before {
  content: "";
  position: absolute;
  inset: 7px -9px -9px 7px;
  border-radius: inherit;
  background: linear-gradient(140deg,
    oklch(100% 0 0 / 0.38), oklch(94% 0.015 245 / 0.2));
  border: 1px solid oklch(100% 0 0 / 0.38);
  border-top-color: oklch(100% 0 0 / 0.68);
  border-left-color: oklch(100% 0 0 / 0.52);
  box-shadow:
    inset 0 1.5px 0 oklch(100% 0 0 / 0.72),
    inset 1px 0 0 oklch(100% 0 0 / 0.42);
  transform: translateZ(-14px);
  z-index: -1;
  pointer-events: none;
}

.sq-1 { width: 68px; height: 68px; top: 96px;  left: 12%;
        transform: translateZ(-40px) rotate(-2deg); }
.sq-2 { width: 48px; height: 48px; top: 112px; right: 4%;
        transform: translateZ(-55px) rotate(3deg); }
.sq-3 { width: 84px; height: 84px; top: 276px; right: -5%;
        transform: translateZ(-50px) rotate(4deg); }
.sq-4 { width: 42px; height: 42px; top: 166px; right: -2%;
        transform: translateZ(-65px) rotate(-3deg); }
.sq-5 { width: 56px; height: 56px; top: 350px; left: -3%;
        transform: translateZ(-30px) rotate(-1deg); }
.sq-6 { width: 38px; height: 38px; top: 234px; right: 6%;
        transform: translateZ(-25px) rotate(2deg); }

/* Pedestal — a real chunky glass slab. THREE composed elements:
 *   .plinth-top    polished top reflective surface (the device rests on)
 *   .plinth-front  substantial front face — the visible 3D mass, ~64px
 *                  tall, with a clear vertical depth gradient
 *   .plinth-rim    thin specular highlight along the bottom edge that
 *                  catches the floor light
 * Together they read as ~92px of solid glass thickness — chunkier than
 * a shelf, lighter than a monitor stand. Extends ~6% past the device on
 * each side, matching the reference's wider-than-device base. */
.plinth-top {
  position: absolute;
  top: 400px;
  left: -6%; right: -6%;
  height: 26px;
  border-radius: 16px 16px 6px 6px;
  background:
    /* Long horizontal reflection band across the top surface */
    linear-gradient(90deg,
      transparent 0%,
      oklch(100% 0 0 / 0.55) 18%,
      oklch(100% 0 0 / 0.75) 50%,
      oklch(100% 0 0 / 0.5) 82%,
      transparent 100%),
    /* Soft radial catchlight from above */
    radial-gradient(120% 220% at 50% -30%,
      oklch(100% 0 0 / 0.55) 0%, transparent 65%),
    linear-gradient(180deg,
      oklch(100% 0 0 / 0.92) 0%,
      oklch(98% 0.006 245 / 0.78) 55%,
      oklch(94% 0.014 245 / 0.6) 100%);
  backdrop-filter: blur(28px) saturate(140%);
  -webkit-backdrop-filter: blur(28px) saturate(140%);
  border: 1px solid oklch(100% 0 0 / 0.85);
  border-top-color: oklch(100% 0 0 / 1);
  border-left-color: oklch(100% 0 0 / 0.95);
  border-bottom-color: oklch(100% 0 0 / 0.32);
  border-right-color: oklch(100% 0 0 / 0.22);
  box-shadow:
    0 3px 4px -1px oklch(58% 0.03 245 / 0.22),
    inset 0 3px 0 oklch(100% 0 0 / 1),
    inset 2px 0 0 oklch(100% 0 0 / 0.9),
    inset 0 -2px 0 oklch(68% 0.03 245 / 0.28);
  transform: translateZ(-8px);
}
.plinth-front {
  position: absolute;
  top: 424px;
  left: -5.5%; right: -5.5%;
  height: 66px;
  border-radius: 0 0 14px 14px;
  background:
    /* Subtle vertical specular highlight near the top edge */
    linear-gradient(180deg,
      oklch(100% 0 0 / 0.42) 0%,
      transparent 18%),
    /* Main depth gradient: lighter at top, cooler/darker toward the
     * bottom — what makes the front face read as a real 3D plane */
    linear-gradient(180deg,
      oklch(98% 0.006 245 / 0.78) 0%,
      oklch(94% 0.014 245 / 0.65) 22%,
      oklch(88% 0.026 245 / 0.55) 58%,
      oklch(80% 0.038 245 / 0.48) 100%);
  backdrop-filter: blur(26px) saturate(135%);
  -webkit-backdrop-filter: blur(26px) saturate(135%);
  border: 1px solid oklch(100% 0 0 / 0.5);
  border-top: 0;
  border-bottom-color: oklch(68% 0.05 245 / 0.45);
  box-shadow:
    /* Large diffuse cast shadow on the floor below the plinth */
    0 56px 70px -22px oklch(38% 0.03 245 / 0.38),
    0 28px 42px -14px oklch(38% 0.03 245 / 0.26),
    0 8px 18px oklch(38% 0.03 245 / 0.12),
    /* Bright top edge — catches light from the same source the top
     * face's reflection comes from */
    inset 0 1.5px 0 oklch(100% 0 0 / 0.85),
    /* Cooler bottom edge — the slab's bottom rim falling into shadow */
    inset 0 -3px 0 oklch(68% 0.05 245 / 0.42),
    /* Side highlights confirming the front IS a flat 3D face */
    inset 2px 0 0 oklch(100% 0 0 / 0.7),
    inset -2px 0 0 oklch(68% 0.05 245 / 0.3);
  transform: translateZ(-28px);
}
.plinth-rim {
  position: absolute;
  top: 488px;
  left: 4%; right: 4%;
  height: 2px;
  border-radius: 1px;
  background: linear-gradient(90deg,
    transparent 0%,
    oklch(100% 0 0 / 0.65) 20%,
    oklch(100% 0 0 / 0.8) 50%,
    oklch(100% 0 0 / 0.45) 80%,
    transparent 100%);
  filter: blur(0.6px);
  transform: translateZ(-22px);
  pointer-events: none;
}

/* Device frame — translucent glass HOUSING with visibly thick bezel.
 * Increased padding (20px vs 14px) makes the white frame around the
 * inner cards substantial, the way the reference photo's device clearly
 * shows ~18-22px of glass bezel on every side. The radial highlight at
 * 0%,0% + bright inset top/left edges sell the glass picking up light
 * from upper-left; the bottom/right edges fall into cooler shadow,
 * which is what makes the housing read as a 3D object rather than a
 * sticker. */
.device {
  position: absolute;
  top: 108px; right: 4%;
  width: 78%; max-width: 470px;
  min-height: 300px;
  padding: 20px;
  border-radius: 24px;
  transform: translateZ(40px);
  background:
    radial-gradient(130% 110% at 0% 0%,
      oklch(100% 0 0 / 0.55) 0%, oklch(100% 0 0 / 0.18) 60%),
    linear-gradient(140deg,
      oklch(100% 0 0 / 0.42) 0%, oklch(96% 0.008 245 / 0.26) 100%);
  backdrop-filter: blur(30px) saturate(150%);
  -webkit-backdrop-filter: blur(30px) saturate(150%);
  border: 1px solid oklch(100% 0 0 / 0.62);
  border-top-color: oklch(100% 0 0 / 1);
  border-left-color: oklch(100% 0 0 / 0.88);
  border-bottom-color: oklch(100% 0 0 / 0.24);
  border-right-color: oklch(100% 0 0 / 0.18);
  box-shadow:
    /* Outer cast shadow stack — large diffuse + tight contact */
    0 64px 96px -32px oklch(38% 0.03 250 / 0.42),
    0 30px 52px -18px oklch(38% 0.03 250 / 0.26),
    0 8px 16px oklch(38% 0.03 250 / 0.12),
    /* Bright top + left highlights catching the upper-left light */
    inset 0 2.5px 0 oklch(100% 0 0 / 1),
    inset 2.5px 0 0 oklch(100% 0 0 / 0.7),
    /* Bottom + right edges fall into cool shadow */
    inset 0 -2px 0 oklch(68% 0.025 250 / 0.22),
    inset -2px 0 0 oklch(68% 0.025 250 / 0.16);
}
/* Device-back: visible glass thickness on the right edge. Offset further
 * right + down so it reads as a real back panel behind the device, not
 * a drop shadow. */
.device-back {
  position: absolute;
  top: 144px; right: -6%;
  width: 78%; max-width: 470px;
  height: 296px;
  border-radius: 24px;
  background: linear-gradient(140deg,
    oklch(100% 0 0 / 0.52), oklch(94% 0.014 245 / 0.24));
  backdrop-filter: blur(22px);
  -webkit-backdrop-filter: blur(22px);
  border: 1px solid oklch(100% 0 0 / 0.55);
  border-top-color: oklch(100% 0 0 / 0.88);
  border-left-color: oklch(100% 0 0 / 0.62);
  border-right-color: oklch(100% 0 0 / 0.2);
  box-shadow:
    0 32px 50px -22px oklch(38% 0.03 250 / 0.26),
    inset 0 2px 0 oklch(100% 0 0 / 0.86),
    inset 1.5px 0 0 oklch(100% 0 0 / 0.5),
    inset -1.5px 0 0 oklch(68% 0.025 250 / 0.18);
  transform: translateZ(8px);
  pointer-events: none;
}
.device-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-foreground);
  letter-spacing: -0.01em;
  padding: 2px 4px 14px;
  margin: 0;
}

/* Two inner sub-cards inside the device frame */
.inner-row {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(0, 1fr);
  gap: 10px;
}
.inner-card {
  background: oklch(100% 0 0 / 0.96);
  border-radius: 14px;
  border: 1px solid oklch(92% 0.008 245 / 0.6);
  border-top-color: oklch(100% 0 0 / 1);
  border-bottom-color: oklch(92% 0.008 245);
  padding: 12px 14px;
  box-shadow:
    0 2px 5px -2px oklch(38% 0.03 250 / 0.1),
    0 1px 2px oklch(38% 0.03 250 / 0.05),
    inset 0 1px 0 oklch(100% 0 0 / 1);
}

.checklist { display: grid; list-style: none; padding: 0; margin: 0; }
.check-item {
  display: grid;
  grid-template-columns: 26px 1fr 16px;
  gap: 11px;
  align-items: center;
  font-size: 13px;
  color: oklch(22% 0.02 245);
  font-weight: 600;
  padding: 9px 0;
  border-top: 1px solid oklch(94% 0.005 245);
}
.check-item:first-child { border-top: 0; }
.check-item .icon {
  width: 26px; height: 26px;
  border-radius: 7px;
  background: oklch(96% 0.01 245);
  display: grid; place-items: center;
  color: oklch(54% 0.18 264);
}
.check-item .icon svg { width: 14px; height: 14px; }
.check-item .check {
  width: 16px; height: 16px;
  border-radius: 50%;
  display: grid; place-items: center;
  background: oklch(62% 0.16 152);
  color: white;
  font-size: 10px; font-weight: 700;
  box-shadow: 0 1px 2px oklch(62% 0.16 152 / 0.35);
}
.check-item.todo .check {
  background: transparent;
  border: 1.5px solid oklch(85% 0.01 245);
  box-shadow: none;
}

.score-card {
  display: flex; flex-direction: column;
  align-items: flex-start;
  padding: 10px 14px 12px;
}
.score-label {
  font-size: 11px;
  color: oklch(45% 0.02 245);
  font-weight: 500;
}
.score-ring-wrap {
  position: relative;
  width: 96px; height: 96px;
  margin: 4px auto 0;
}
.score-ring-wrap .num {
  position: absolute; inset: 0;
  display: grid; place-items: center;
  font-size: 28px; font-weight: 700;
  color: oklch(22% 0.02 245);
  letter-spacing: -0.02em;
}
.score-ring-wrap .max {
  position: absolute; right: -10px; bottom: 10px;
  font-size: 11px; color: oklch(45% 0.02 245);
  font-weight: 500;
}
.trend { width: 100%; height: 30px; margin-top: 4px; }

/* Bot tile — overlapping the device's left edge so they read as a
 * connected pair, not separate floating shapes. Deep saturated blue
 * gradient bottom-right matches the reference's strong color presence. */
.bot-tile {
  position: absolute;
  top: 218px; left: 4%;
  width: 124px; height: 124px;
  border-radius: 24px;
  display: grid; place-items: center;
  transform: translateZ(80px) rotate(-2deg);
  background:
    radial-gradient(120% 100% at 0% 0%,
      oklch(100% 0 0 / 0.85) 0%, oklch(86% 0.06 264 / 0.32) 60%),
    linear-gradient(160deg,
      oklch(94% 0.025 260 / 0.78) 0%, oklch(70% 0.14 264 / 0.65) 100%);
  border: 1px solid oklch(100% 0 0 / 0.75);
  border-top-color: oklch(100% 0 0 / 1);
  border-left-color: oklch(100% 0 0 / 0.9);
  border-bottom-color: oklch(100% 0 0 / 0.14);
  border-right-color: oklch(100% 0 0 / 0.18);
  backdrop-filter: blur(26px) saturate(155%);
  -webkit-backdrop-filter: blur(26px) saturate(155%);
  box-shadow:
    0 56px 76px -28px oklch(54% 0.18 264 / 0.45),
    0 26px 40px -14px oklch(54% 0.18 264 / 0.3),
    0 5px 12px oklch(38% 0.03 250 / 0.1),
    inset 0 2.5px 0 oklch(100% 0 0 / 1),
    inset 2px 0 0 oklch(100% 0 0 / 0.8),
    inset 0 -1.5px 0 oklch(54% 0.12 264 / 0.3),
    inset -1.5px 0 0 oklch(54% 0.12 264 / 0.22);
}
.bot-tile-back {
  position: absolute;
  top: 226px; left: 1%;
  width: 124px; height: 124px;
  border-radius: 24px;
  background: linear-gradient(160deg,
    oklch(94% 0.03 260 / 0.42), oklch(78% 0.08 264 / 0.3));
  backdrop-filter: blur(18px);
  -webkit-backdrop-filter: blur(18px);
  border: 1px solid oklch(100% 0 0 / 0.3);
  border-top-color: oklch(100% 0 0 / 0.7);
  box-shadow: inset 0 1.5px 0 oklch(100% 0 0 / 0.7);
  transform: translateZ(54px) rotate(-2deg);
  pointer-events: none;
}
.bot-tile svg { width: 64px; height: 64px; color: oklch(54% 0.18 264); }

@media (max-width: 760px) {
  .scene-wrap { min-height: 520px; margin-top: 24px; }
  .scene-stage { transform: rotateY(-8deg) rotateX(2deg); }
  .device { top: 48px; right: 4%; width: 86%; }
  .device-back { top: 82px; right: 0%; width: 86%; }
  .device-contact { top: 336px; }
  .bot-tile { left: 0%; top: 158px; }
  .bot-tile-back { left: -3%; top: 166px; }
  .plinth-top { top: 340px; }
  .plinth-front { top: 364px; }
  .plinth-rim { top: 428px; }
  .sq-1 { top: 36px; }
  .sq-2 { top: 52px; }
  .sq-3 { top: 216px; }
  .sq-4 { top: 106px; }
  .sq-5 { top: 290px; }
  .sq-6 { top: 174px; }
}
`;
