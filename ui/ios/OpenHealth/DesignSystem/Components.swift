import SwiftUI

/// A soft surface card. Light: white paper + one soft shadow. Dark: tonal fill
/// with a whisper of top light. Never a double border.
struct Card<Content: View>: View {
    @Environment(\.colorScheme) private var scheme
    @ViewBuilder var content: Content

    var body: some View {
        content
            .padding(Theme.s4)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.surface)
            .clipShape(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: Theme.radius, style: .continuous)
                    .strokeBorder(
                        LinearGradient(
                            colors: [Color.white.opacity(scheme == .dark ? 0.07 : 0),
                                     Color.white.opacity(0)],
                            startPoint: .top, endPoint: .bottom
                        ),
                        lineWidth: 1
                    )
            )
            .shadow(color: .black.opacity(scheme == .dark ? 0 : 0.06),
                    radius: 12, x: 0, y: 4)
    }
}

/// Tracked-caps micro label — the one way section headers are set.
struct CapsLabel: View {
    let text: String
    var size: CGFloat = 11
    var color: Color = Theme.inkSoft

    var body: some View {
        Text(text.uppercased())
            .font(Theme.label(size))
            .tracking(1.4)
            .foregroundStyle(color)
    }
}

/// Primary action: ink fill, full width. The only loud button in the app.
struct PrimaryButtonStyle: ButtonStyle {
    var tint: Color = Theme.action
    var label: Color = Theme.onAction

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(Theme.body(16, weight: .semibold))
            .frame(maxWidth: .infinity)
            .padding(.vertical, Theme.s4 - 2)
            .foregroundStyle(label)
            .background(tint.opacity(configuration.isPressed ? 0.85 : 1))
            .clipShape(Capsule())
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

/// Quiet secondary action — text only.
struct GhostButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(Theme.body(15, weight: .medium))
            .foregroundStyle(Theme.inkSoft)
            .opacity(configuration.isPressed ? 0.6 : 1)
    }
}

/// Staggered entrance: fade + small rise, once, honoring Reduce Motion.
private struct RiseIn: ViewModifier {
    let index: Int
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var shown = false

    func body(content: Content) -> some View {
        content
            .opacity(shown ? 1 : 0)
            .offset(y: shown ? 0 : 12)
            .onAppear(perform: reveal)
            .task { reveal() }   // fallback: fires post-attach even when onAppear races
    }

    private func reveal() {
        guard !shown else { return }
        if reduceMotion { shown = true; return }
        withAnimation(Theme.rise.delay(Double(index) * Theme.stagger)) {
            shown = true
        }
    }
}

extension View {
    /// Entrance choreography for boards and lists.
    func riseIn(_ index: Int) -> some View { modifier(RiseIn(index: index)) }
}

/// Confidence chip. Visual weight drops as certainty drops — low-confidence
/// claims look quiet on purpose.
struct ConfidenceChip: View {
    let confidence: Confidence

    var body: some View {
        Text("\(confidence.rawValue) · \(confidence.label)")
            .font(Theme.label(11))
            .tracking(0.3)
            .padding(.horizontal, Theme.s2 + 2)
            .padding(.vertical, Theme.s1 + 1)
            .foregroundStyle(foreground)
            .background(background)
            .clipShape(Capsule())
    }

    private var foreground: Color {
        switch confidence {
        case .c5, .c4: return Theme.onAction
        case .c3: return Theme.ink
        case .c2, .c1: return Theme.inkSoft
        }
    }
    private var background: Color {
        switch confidence {
        case .c5, .c4: return Theme.action
        case .c3: return Theme.surfaceAlt
        case .c2, .c1: return Theme.surfaceAlt.opacity(0.6)
        }
    }
}

/// Horizontal range bar: band = reference range, dot = the value.
/// State is paired with a word + icon, never color alone (accessibility).
struct RangeBar: View {
    let value: Double?
    let low: Double?
    let high: Double?
    let flag: MarkerFlag

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            ZStack(alignment: .leading) {
                Capsule().fill(Theme.hairline).frame(height: 6)
                Capsule().fill(flag.color.opacity(0.25))
                    .frame(width: max(0, bandWidth(w)), height: 6)
                    .offset(x: bandStart(w))
                Circle().fill(flag.color)
                    .frame(width: 12, height: 12)
                    .offset(x: dotX(w) - 6)
            }
            .frame(height: 12)
        }
        .frame(height: 12)
    }

    private var domain: (Double, Double) {
        let lo = low ?? (value.map { $0 * 0.6 } ?? 0)
        let hi = high ?? (value.map { $0 * 1.4 } ?? 1)
        let lowBound = min(lo, value ?? lo)
        let highBound = max(hi, value ?? hi)
        let pad = (highBound - lowBound) * 0.15 + 0.0001
        return (lowBound - pad, highBound + pad)
    }
    private func pos(_ v: Double, _ w: CGFloat) -> CGFloat {
        let (a, b) = domain
        guard b > a else { return 0 }
        return CGFloat((v - a) / (b - a)) * w
    }
    private func bandStart(_ w: CGFloat) -> CGFloat { pos(low ?? domain.0, w) }
    private func bandWidth(_ w: CGFloat) -> CGFloat { pos(high ?? domain.1, w) - bandStart(w) }
    private func dotX(_ w: CGFloat) -> CGFloat {
        guard let v = value else { return 0 }
        return min(max(pos(v, w), 6), w - 6)
    }
}

/// A number that counts up to its value once on appear (instrument warm-up).
struct CountUpNumber: View, Animatable {
    var value: Double
    var font: Font
    var color: Color
    var fraction: Double = 1

    var animatableData: Double {
        get { fraction }
        set { fraction = newValue }
    }

    var body: some View {
        Text("\(Int((value * fraction).rounded()))")
            .font(font)
            .foregroundStyle(color)
    }
}

/// Circular gauge in the WHOOP/Bevel register: a thin gradient arc on a quiet
/// track, caps label inside above a plain bold numeral. No glow. Draws in and
/// counts up once on appear. A wellness summary, never a clinical judgment.
struct RingGauge: View {
    let progress: Double           // 0...1
    let centerValue: String
    var centerUnit: String? = nil
    var labelInside: String? = nil
    var suffix: String? = nil       // e.g. "%" — set small beside the numeral
    var tint: Color = Theme.accent
    var lineWidth: CGFloat = 12
    var size: CGFloat = 200
    var animated: Bool = true

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var shown = false

    private var clamped: Double { min(max(progress, 0), 1) }
    private var numeric: Double? { Double(centerValue) }

    var body: some View {
        ZStack {
            Circle()
                .stroke(Theme.hairlineStrong, lineWidth: lineWidth)
            Circle()
                .trim(from: 0, to: shown ? clamped : 0)
                .stroke(
                    AngularGradient(
                        gradient: Gradient(colors: [tint.opacity(0.45), tint]),
                        center: .center,
                        startAngle: .degrees(0),
                        endAngle: .degrees(360 * clamped)
                    ),
                    style: StrokeStyle(lineWidth: lineWidth, lineCap: .round)
                )
                .rotationEffect(.degrees(-90))
            VStack(spacing: 2) {
                if let label = labelInside {
                    CapsLabel(text: label, size: 11, color: Theme.ink.opacity(0.85))
                }
                if let n = numeric {
                    HStack(alignment: .firstTextBaseline, spacing: 2) {
                        CountUpNumber(value: n,
                                      font: Theme.numeral(size * 0.3),
                                      color: Theme.ink,
                                      fraction: shown ? 1 : 0)
                        if let suffix {
                            Text(suffix)
                                .font(Theme.numeral(size * 0.13))
                                .foregroundStyle(Theme.ink)
                        }
                    }
                } else {
                    Text(centerValue)
                        .font(Theme.numeral(size * 0.26))
                        .foregroundStyle(Theme.ink)
                        .lineLimit(1)
                        .minimumScaleFactor(0.5)
                }
                if let unit = centerUnit {
                    Text(unit)
                        .font(Theme.body(12, weight: .medium))
                        .foregroundStyle(Theme.inkSoft)
                }
            }
        }
        .frame(width: size, height: size)
        .onAppear {
            guard !shown else { return }
            if reduceMotion || !animated { shown = true; return }
            withAnimation(Theme.drawIn.delay(0.1)) { shown = true }
        }
    }
}

/// Parse a leading number out of a display string like "64%" or "7.2 h".
func leadingNumber(_ s: String) -> Double? {
    let prefix = s.drop(while: { !$0.isNumber }).prefix { $0.isNumber || $0 == "." }
    return Double(prefix)
}

/// Prominent safety banner. The only place red is used loudly.
struct SafetyBanner: View {
    let alert: SafetyAlert
    var body: some View {
        HStack(alignment: .top, spacing: Theme.s3) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.white)
            VStack(alignment: .leading, spacing: Theme.s1) {
                Text(alert.title).font(Theme.body(15, weight: .bold))
                Text(alert.message).font(Theme.body(13))
            }
            .foregroundStyle(.white)
        }
        .padding(Theme.s4)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.danger)
        .clipShape(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
    }
}
