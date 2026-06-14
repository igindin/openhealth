import Foundation
import Observation

/// Holds the snapshot the Today/Trends screens render.
///
/// `refresh()` reads a display-grade view of Apple Health on-device and replaces
/// the snapshot. When HealthKit is unavailable (e.g. previews), it falls back to
/// the bundled synthetic `sample.json`. No network, no cloud — local-first.
@Observable
@MainActor
final class HealthStore {
    var snapshot: HealthSnapshot = .empty
    private let ingest = HealthKitIngest()
    private let transportProvider: () async -> SyncTransport?

    init(transportProvider: @escaping () async -> SyncTransport? = { await Task.detached { ICloudDriveTransport() }.value }) {
        self.transportProvider = transportProvider
        load()
    }

    /// Bundled synthetic snapshot (fallback / previews).
    func load() {
        if let url = Bundle.main.url(forResource: "sample", withExtension: "json"),
           let data = try? Data(contentsOf: url),
           let decoded = try? JSONDecoder().decode(HealthSnapshot.self, from: data) {
            snapshot = decoded
        }
    }

    /// Refresh the snapshot. Prefer the Mac engine's outbox (real recovery /
    /// insights); otherwise show an on-device Apple Health view; otherwise keep
    /// the bundled fallback.
    func refresh() async {
        if let transport = await transportProvider(), let outbox = try? transport.readOutbox() {
            snapshot = outbox
            return
        }
        guard HealthKitIngest.isAvailable else { return }
        let built = Self.snapshot(from: await ingest.buildDisplay())
        // Keep the synthetic fallback if HealthKit returned nothing yet
        // (no permission granted, or no data on this device).
        if built.measurements.isEmpty && built.trends.isEmpty { return }
        snapshot = built
    }

    // MARK: - Mapping HealthKit → snapshot

    static func snapshot(from display: HealthDisplay) -> HealthSnapshot {
        var measurements: [Measurement] = []

        if let estimate = display.readinessEstimate {
            measurements.append(Measurement(metric: "recovery", title: "Readiness",
                                            value: "\(estimate)%", caption: "on-device est."))
        }
        if let sdnn = display.sdnnLatest {
            measurements.append(Measurement(metric: "hrv", title: "HRV",
                                            value: "\(Int(sdnn.rounded())) ms", caption: "SDNN"))
        }
        if let rhr = display.rhrLatest {
            measurements.append(Measurement(metric: "resting_hr", title: "Resting HR",
                                            value: "\(Int(rhr.rounded())) bpm", caption: "latest"))
        }
        if let sleep = display.sleepHours {
            measurements.append(Measurement(metric: "sleep", title: "Sleep",
                                            value: String(format: "%.1f h", sleep), caption: "last night"))
        }
        if let steps = display.stepsToday {
            measurements.append(Measurement(metric: "steps", title: "Steps",
                                            value: "\(Int(steps.rounded()))", caption: "today"))
        }

        var trends: [Trend] = []
        if !display.sdnnSeries.isEmpty {
            trends.append(Trend(metric: "hrv", title: "HRV (SDNN)", unit: "ms",
                                referenceLow: nil, referenceHigh: nil,
                                points: display.sdnnSeries.map { TrendPoint(date: mmdd($0.date), value: $0.value.rounded()) }))
        }
        if !display.rhrSeries.isEmpty {
            trends.append(Trend(metric: "resting_hr", title: "Resting HR", unit: "bpm",
                                referenceLow: nil, referenceHigh: nil,
                                points: display.rhrSeries.map { TrendPoint(date: mmdd($0.date), value: $0.value.rounded()) }))
        }

        return HealthSnapshot(greetingName: "there", measurements: measurements,
                              panels: [], trends: trends, insights: [], alerts: [])
    }

    private static let mmddFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "MM-dd"
        return f
    }()

    private static func mmdd(_ date: Date) -> String { mmddFormatter.string(from: date) }
}

extension HealthSnapshot {
    static let empty = HealthSnapshot(
        greetingName: "there",
        measurements: [],
        panels: [],
        trends: [],
        insights: [],
        alerts: []
    )
}
