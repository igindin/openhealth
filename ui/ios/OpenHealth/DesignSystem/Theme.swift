import SwiftUI
import UIKit

/// Design system (docs/design/style-bible.md) — "Quiet Instrument".
/// Braun/Teenage-Engineering discipline with WHOOP-grade data confidence:
/// two surfaces, one system. Light is an editorial paper canvas; dark is the
/// widget board. Every token is adaptive — views never branch on color scheme.
///
/// Three type voices, strictly cast:
///  - numerals: SF heavy *compressed*, tabular — the instrument voice
///  - editorial: New York serif — greetings, onboarding, empty states only
///  - UI: SF Pro — labels (tracked caps), body, captions
/// Color rule: actions are ink, data speaks in metric hues, zones are the only
/// loud semantics. Red stays reserved for safety.
enum Theme {

    // MARK: - Adaptive canvas

    static let background = adaptive(light: 0xF6F5F2, dark: 0x0B0C0F)
    static let surface = adaptive(light: 0xFFFFFF, dark: 0x15171C)
    static let surfaceAlt = adaptive(light: 0xEFEDE8, dark: 0x101216)
    static let ink = adaptive(light: 0x17191D, dark: 0xF5F6F8)
    static let inkSoft = adaptive(light: 0x6E7480, dark: 0x9BA3AF)
    static let inkDim = adaptive(light: 0x9AA0AA, dark: 0x5F6672)

    /// Hairlines are opacity-based so they sit softly on any surface.
    static let hairline = Color.primary.opacity(0.07)
    static let hairlineStrong = Color.primary.opacity(0.13)

    /// Ink-first actions: white button on dark, near-black on light.
    static let action = adaptive(light: 0x17191D, dark: 0xF5F6F8)
    /// Label color placed on top of an `action` fill.
    static let onAction = adaptive(light: 0xF6F5F2, dark: 0x0B0C0F)

    /// Links / interactive accents when a hue is unavoidable (kept quiet).
    static let accent = adaptive(light: 0x3D6FB8, dark: 0x8FB4E8)

    static let warn = adaptive(light: 0xC07E1F, dark: 0xF2B33D)
    static let danger = adaptive(light: 0xD63B3B, dark: 0xEF4E4E)

    // MARK: - Recovery zones (green >=67, yellow 34-66, red <34)

    static let zoneGreen = adaptive(light: 0x1FA45C, dark: 0x3DD68C)
    static let zoneYellow = adaptive(light: 0xC07E1F, dark: 0xF2B33D)
    static let zoneRed = adaptive(light: 0xD63B3B, dark: 0xEF4E4E)

    static func recoveryColor(_ score: Double) -> Color {
        if score >= 67 { return zoneGreen }
        if score >= 34 { return zoneYellow }
        return zoneRed
    }
    static func recoveryHeadline(_ score: Double) -> String {
        if score >= 67 { return "Green zone — ready to push" }
        if score >= 34 { return "Yellow zone — go moderate" }
        return "Red zone — prioritise recovery"
    }
    /// Recovery/score (0...100) → "Doctor Context" icon + one-liner.
    static func recoveryMood(_ score: Double) -> (symbol: String, line: String) {
        if score >= 67 { return ("bolt.heart.fill", "Well recovered — use the day.") }
        if score >= 34 { return ("figure.walk", "Middle ground. Pick one helpful action.") }
        return ("moon.zzz.fill", "Running low. Today is about sleep and rest.") }

    // MARK: - Metric hues (data speaks in color; actions stay ink)

    static func metricHue(_ metric: String) -> Color {
        switch metric {
        case "hrv": return adaptive(light: 0x0F8E85, dark: 0x5AD8CC)
        case "resting_hr", "rhr": return adaptive(light: 0xC2593A, dark: 0xF08C5A)
        case "sleep": return adaptive(light: 0x6A5ACD, dark: 0x9D8CFF)
        case "strain": return adaptive(light: 0x3D6FB8, dark: 0x6FA8EF)
        case "weight": return adaptive(light: 0x5A6472, dark: 0xAAB4C2)
        default: return accent
        }
    }

    // MARK: - Type voices

    /// Instrument numerals: heavy, compressed, tabular. The number is the artwork.
    static func numeral(_ size: CGFloat) -> Font {
        .system(size: size, weight: .heavy).width(.compressed).monospacedDigit()
    }
    /// Editorial serif — greetings, onboarding, empty states. Nowhere else.
    static func display(_ size: CGFloat, weight: Font.Weight = .semibold) -> Font {
        .system(size: size, weight: weight, design: .serif)
    }
    /// Tracked-caps micro label (pair with .tracking(1.4) + uppercased text).
    static func label(_ size: CGFloat = 11) -> Font {
        .system(size: size, weight: .semibold)
    }
    static func body(_ size: CGFloat = 15, weight: Font.Weight = .regular) -> Font {
        .system(size: size, weight: weight)
    }

    // MARK: - Motion constants (nuance, not noise)

    static let drawIn = Animation.spring(response: 0.9, dampingFraction: 0.85)
    static let rise = Animation.easeOut(duration: 0.45)
    static let stagger = 0.06

    // MARK: - Spacing / radii

    static let s1: CGFloat = 4
    static let s2: CGFloat = 8
    static let s3: CGFloat = 12
    static let s4: CGFloat = 16
    static let s5: CGFloat = 24
    static let s6: CGFloat = 32

    static let radius: CGFloat = 20
    static let radiusSmall: CGFloat = 14

    // MARK: - Helpers

    /// Dynamic color: one token, two surfaces.
    static func adaptive(light: UInt32, dark: UInt32) -> Color {
        Color(uiColor: UIColor { traits in
            UIColor(hex: traits.userInterfaceStyle == .dark ? dark : light)
        })
    }
}

extension UIColor {
    convenience init(hex: UInt32) {
        self.init(
            red: CGFloat((hex >> 16) & 0xFF) / 255,
            green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255,
            alpha: 1
        )
    }
}

extension Color {
    init(hex: UInt32) { self.init(uiColor: UIColor(hex: hex)) }
}

extension MarkerFlag {
    var color: Color {
        switch self {
        case .normal: return Theme.accent
        case .low, .high: return Theme.warn
        case .unknown: return Theme.inkSoft
        }
    }

    var label: String {
        switch self {
        case .normal: return "in range"
        case .low: return "low"
        case .high: return "high"
        case .unknown: return "no range"
        }
    }
}
