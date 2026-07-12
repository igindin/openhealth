# FRAMES.md — motion contract (iOS app)

Motion pairs with docs/design/style-bible.md §5: nuance, not noise. One gesture
per surface, played once, always honoring Reduce Motion.

## Timing tokens

| Token | Value | Use |
|-------|-------|-----|
| `Theme.drawIn` | spring(response 0.9, damping 0.85), delay 0.1 | ring arc + count-up |
| `Theme.rise` | easeOut 0.45s | card entrance |
| `Theme.stagger` | 0.06s per index | board/list choreography |
| press | easeOut 0.12s | button scale 0.98 + dim |
| select | easeOut 0.15s | chip/pill fill swap |

## Sequences

- **Ring warm-up** (`RingGauge`): arc trims 0 → value with `drawIn`; the center
  numeral counts up in lockstep (`CountUpNumber`, same curve); tint shadow fades
  in with the arc. Plays once per appear.
- **Board entrance** (`riseIn(i)`): opacity 0→1 + 12pt rise, `rise` delayed by
  `i × stagger`. Hero ring is index 0; tiles continue the cascade.
- **Onboarding**: page changes ease 0.3s; goal rows cascade with `riseIn`;
  progress segments animate fill 0.25s; selection and page turns give haptics
  (`.sensoryFeedback(.selection / .impact(weight: .light))`).
- **Journal**: pill/scale selection swaps fill in 0.15s + selection haptic;
  save success = notification haptic + green fill state.
- **Sync**: status icon pulses (`symbolEffect(.pulse)`) only while syncing.

## Rules

- Every entrance plays once — no repeat on scroll-back.
- `accessibilityReduceMotion` short-circuits all of the above to instant.
- Nothing loops idly; the only continuous motion is the sync pulse while work
  is actually happening.
