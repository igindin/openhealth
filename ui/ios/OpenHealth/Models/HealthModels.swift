import Foundation

// Mirrors the OpenHealth engine's evidence-oriented records. The app renders
// confidence and safety signals exactly as the engine emits them; it never
// grades or diagnoses on its own.

/// Five-level confidence scale (see docs/methodology/evidence-and-trust.md).
enum Confidence: String, Codable, CaseIterable {
    case c5 = "C5"   // Established
    case c4 = "C4"   // Likely
    case c3 = "C3"   // Hypothesis
    case c2 = "C2"   // Weak signal
    case c1 = "C1"   // Speculation

    var label: String {
        switch self {
        case .c5: return "Established"
        case .c4: return "Likely"
        case .c3: return "Hypothesis"
        case .c2: return "Weak signal"
        case .c1: return "Speculation"
        }
    }

    /// Findings at C3 and below are phrased as questions, never as facts.
    var framesAsQuestion: Bool { self == .c3 || self == .c2 || self == .c1 }

    init(numeric: Double) {
        switch numeric {
        case 0.85...: self = .c5
        case 0.6..<0.85: self = .c4
        case 0.4..<0.6: self = .c3
        case 0.25..<0.4: self = .c2
        default: self = .c1
        }
    }
}

enum MarkerFlag: String, Codable {
    case low, normal, high, unknown
}

/// One lab marker with its value and the range it was judged against.
struct LabMarker: Codable, Identifiable, Hashable {
    var id: String { markerKey }
    let markerKey: String
    let displayName: String
    let loinc: String?
    let value: Double?
    let unit: String?
    let valueSI: Double?
    let siUnit: String?
    let referenceLow: Double?
    let referenceHigh: Double?
    let referenceSource: String   // "report" | "fallback"
    let flag: MarkerFlag
    let note: String?

    enum CodingKeys: String, CodingKey {
        case markerKey = "marker_key"
        case displayName = "display_name"
        case loinc, value, unit
        case valueSI = "value_si"
        case siUnit = "si_unit"
        case referenceLow = "reference_low"
        case referenceHigh = "reference_high"
        case referenceSource = "reference_source"
        case flag, note
    }
}

/// A blood/lab panel: a dated set of markers from one source.
struct LabPanel: Codable, Identifiable, Hashable {
    let id: String
    let date: String
    let title: String
    let markers: [LabMarker]
    var hasCritical: Bool { markers.contains { $0.markerKey == "glucose" && ($0.value ?? 0) >= 300 } }
    var abnormal: [LabMarker] { markers.filter { $0.flag == .low || $0.flag == .high } }
}

/// A single measured signal over time (e.g. sleep hours, a marker trend).
struct TrendPoint: Codable, Identifiable, Hashable {
    var id: String { date }
    let date: String
    let value: Double
}

struct Trend: Codable, Identifiable, Hashable {
    var id: String { metric }
    let metric: String
    let title: String
    let unit: String
    let referenceLow: Double?
    let referenceHigh: Double?
    let points: [TrendPoint]

    enum CodingKeys: String, CodingKey {
        case metric, title, unit
        case referenceLow = "reference_low"
        case referenceHigh = "reference_high"
        case points
    }
}

/// An engine-derived or community-pulled hypothesis. Confidence drives framing.
struct Insight: Codable, Identifiable, Hashable {
    let id: String
    let title: String
    let statement: String
    let confidenceValue: Double
    let openQuestions: [String]
    let suggestedValidation: String?
    let sources: [String]

    var confidence: Confidence { Confidence(numeric: confidenceValue) }

    enum CodingKeys: String, CodingKey {
        case id, title, statement
        case confidenceValue = "confidence"
        case openQuestions = "open_questions"
        case suggestedValidation = "suggested_validation"
        case sources
    }
}

/// A safety alert. The app surfaces it prominently and never interprets it.
struct SafetyAlert: Codable, Identifiable, Hashable {
    let id: String
    let title: String
    let message: String
    let urgency: String   // "emergency" | "urgent" | "out-of-scope"
}

/// A latest-measurement tile for the Today screen.
struct Measurement: Codable, Identifiable, Hashable {
    var id: String { metric }
    let metric: String
    let title: String
    let value: String
    let caption: String?
}

/// A "what affects HRV" correlation chip from the Mac engine (journal behaviour
/// vs recovery). `delta` is the recovery-point swing; `grade` is the C1-C5 evidence.
struct Correlation: Codable, Identifiable, Hashable {
    let id: String
    let label: String
    let delta: Int?
    let dir: String      // "up" | "down"
    let grade: String    // "C1".."C5"
}

/// Top-level bundle the app loads (mirrors the engine's exported JSON).
struct HealthSnapshot: Codable {
    let greetingName: String
    let measurements: [Measurement]
    let panels: [LabPanel]
    let trends: [Trend]
    let insights: [Insight]
    let alerts: [SafetyAlert]
    var correlations: [Correlation] = []

    enum CodingKeys: String, CodingKey {
        case greetingName = "greeting_name"
        case measurements, panels, trends, insights, alerts, correlations
    }
}

extension HealthSnapshot {
    // Custom decode so older payloads (sample.json, pre-correlations snapshots)
    // still load: synthesized Codable does NOT apply property defaults for a
    // missing key, so `correlations` must be decoded with decodeIfPresent.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        greetingName = try c.decode(String.self, forKey: .greetingName)
        measurements = try c.decode([Measurement].self, forKey: .measurements)
        panels = try c.decode([LabPanel].self, forKey: .panels)
        trends = try c.decode([Trend].self, forKey: .trends)
        insights = try c.decode([Insight].self, forKey: .insights)
        alerts = try c.decode([SafetyAlert].self, forKey: .alerts)
        correlations = try c.decodeIfPresent([Correlation].self, forKey: .correlations) ?? []
    }
}
