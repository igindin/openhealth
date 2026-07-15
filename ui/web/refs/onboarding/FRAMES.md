# FRAMES.md — motion contract, OpenHealth onboarding overlay

Engine: WAAPI (`element.animate`) + CSS transitions. No GSAP dependency — the
overlay is shared by both skins and V2 does not load GSAP.

## Hard rule (learned in this repo, twice)
**Visibility must never depend on an animation finishing.** rAF and CSS
animations get throttled in background/headless tabs and freeze mid-frame.
Therefore: every animated element's BASE state = fully visible; animations run
`from → base` only; anything that must land on an exact value gets a timeout
backstop. `prefers-reduced-motion: reduce` disables all of it.

## Timings & easing (single palette)
- `--ease-out`: cubic-bezier(.2,.8,.2,1)  — enters, progress rail
- `--ease-spring`: cubic-bezier(.34,1.56,.64,1) — selection pop, check draw
- step content ENTER: 380ms ease-out, translateX(18px)→0 + fade (forward),
  translateX(-16px)→0 (back). NO exit phase: the swap is SYNCHRONOUS —
  progression must never wait on a timer (throttled tabs defer setTimeout by
  seconds; a deferred swap once stranded the old step on screen).
- stagger: cards/groups/rows enter with `--d = i*40ms` delay (max ~9 items).
- progress rail: width transition 500ms ease-out (rail персистентен, не пересоздаётся).
- selection (goal card / pill / palette): border+bg via 180ms transitions;
  press = scale(.985) 90ms; release pop = scale 1 spring 240ms (WAAPI).
- ghost word: per-step swap, enters with 600ms fade to opacity .05 — then static.
- finale («Собрать»): chips «защёлкиваются» волной 40ms/chip (border→accent,
  scale .96→1), big numeral one spring pulse 320ms, then whole overlay exits
  translateY(-24px)+fade 420ms → location.reload() (backstop setTimeout 1200ms
  fires reload regardless of animation state).

## One thing per screen
welcome: ghost word + rows stagger · goal: card stagger + selection spring ·
modules: group stagger + pill toggles · palette: live theme crossfade
(450ms background-color/color transition on overlay root) · how: rows stagger ·
ready: count-up (with backstop) + chip pops → assembly finale.

## Keyboard
Enter / ArrowRight = next, ArrowLeft = back, Esc = skip/close.
:focus-visible ring = 2px var(--accent).
