import SwiftUI

/// Design system (see docs/design/style-bible.md). The app now ships the
/// "Widget Board" dark surface as its default: near-black ground, soft-fill
/// tiles, luminous numbers. Semantic colors stay fixed; recovery zones use the
/// green/yellow/red language of the web dashboard. The earlier light tokens are
/// kept (some lab views still reference them) but the app runs dark.
enum Theme {
    // Dark "Widget Board" surface (style-bible §1–2, web dashboard palette).
    static let background = Color(hex: 0x0C0D10)   // near-black ground
    static let surface = Color(hex: 0x14161B)      // tile / card fill
    static let surfaceAlt = Color(hex: 0x101216)   // recessed tile
    static let ink = Color(hex: 0xE6E9EF)          // primary text
    static let inkSoft = Color(hex: 0x7D8694)      // secondary text
    static let inkDim = Color(hex: 0x565D68)       // tertiary / units
    static let hairline = Color(hex: 0x1F2228)     // 1px separators
    static let hairlineStrong = Color(hex: 0x2A2E36)

    static let accent = Color(hex: 0x5B8DEF)   // blue: primary action / links
    static let warn = Color(hex: 0xE8B339)     // amber: attention / yellow zone
    static let danger = Color(hex: 0xE5484D)   // red: critical safety + low zone

    // Recovery zone colors (web dashboard): green ≥67, yellow 34–66, red <34.
    // A wellness summary language, never a clinical judgment.
    static let zoneGreen = Color(hex: 0x34C759)
    static let zoneYellow = Color(hex: 0xE8B339)
    static let zoneRed = Color(hex: 0xE5484D)

    /// Recovery/score (0...100) → zone color.
    static func recoveryColor(_ score: Double) -> Color {
        if score >= 67 { return zoneGreen }
        if score >= 34 { return zoneYellow }
        return zoneRed
    }
    /// Recovery/score (0...100) → short zone headline.
    static func recoveryHeadline(_ score: Double) -> String {
        if score >= 67 { return "Green zone — ready to push" }
        if score >= 34 { return "Yellow zone — go moderate" }
        return "Red zone — prioritise recovery"
    }
    /// Recovery/score (0...100) → "Doctor Context" icon + one-liner. The icon is
    /// an SF Symbol (always renders), tinted to the recovery zone by the caller.
    static func recoveryMood(_ score: Double) -> (symbol: String, line: String) {
        if score >= 67 { return ("bolt.heart.fill", "Well recovered — use the day.") }
        if score >= 34 { return ("figure.walk", "Middle ground. Pick one helpful action.") }
        return ("moon.zzz.fill", "Running low. Today is about sleep and rest.") }

    // Warm neutral surfaces kept for backward-compatible decorative use.
    static let sand = Color(hex: 0x1C1A16)
    static let sage = Color(hex: 0x16201A)
    static let mist = Color(hex: 0x161A22)

    /// Calm decorative gradient for a hero tile, varied per metric. These carry
    /// no clinical meaning — flags/safety always use the semantic colors above.
    static func heroGradient(for metric: String) -> LinearGradient {
        let pairs: [String: [Color]] = [
            "sleep": [Color(hex: 0x2E7D74), Color(hex: 0x9CC7BF)],
            "recovery": [Color(hex: 0x4A6FA5), Color(hex: 0xAFC4E0)],
            "resting_hr": [Color(hex: 0xB0654F), Color(hex: 0xE2B3A2)],
            "weight": [Color(hex: 0x6B6F86), Color(hex: 0xC2C5D6)]
        ]
        let colors = pairs[metric] ?? [Color(hex: 0x2E7D74), Color(hex: 0x9CC7BF)]
        return LinearGradient(colors: colors, startPoint: .topLeading, endPoint: .bottomTrailing)
    }

    // Spacing scale
    static let s1: CGFloat = 4
    static let s2: CGFloat = 8
    static let s3: CGFloat = 12
    static let s4: CGFloat = 16
    static let s5: CGFloat = 24
    static let s6: CGFloat = 32

    static let radius: CGFloat = 18
    static let radiusSmall: CGFloat = 12
}

extension Color {
    init(hex: UInt32) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255,
            opacity: 1
        )
    }
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
