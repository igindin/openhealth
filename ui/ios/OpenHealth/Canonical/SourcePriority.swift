import Foundation

/// Device-class ranking used to resolve cross-source overlap (e.g. Apple Watch
/// HRV vs a third-party app mirroring its own HRV into HealthKit).
/// Seeded from openwearables `device_type.py` (MIT).
enum DeviceClass: Int, Comparable, Sendable {
    case unknown = 0
    case scale = 1
    case phone = 2
    case ring = 3
    case band = 4
    case watch = 5

    static func < (lhs: DeviceClass, rhs: DeviceClass) -> Bool { lhs.rawValue < rhs.rawValue }

    /// Infer the device class from a HealthKit device model string (e.g. "Watch6,1").
    static func infer(fromModel model: String?) -> DeviceClass {
        guard let m = model?.lowercased() else { return .unknown }
        if m.contains("watch") { return .watch }
        if m.contains("ring") || m.contains("oura") { return .ring }
        if m.contains("band") || m.contains("whoop") { return .band }
        if m.contains("scale") { return .scale }
        if m.contains("iphone") || m.contains("phone") { return .phone }
        return .unknown
    }
}

/// Per-metric source selection. The primary dedup key is `external_id` (the
/// HealthKit UUID); this layer additionally drops writers we deliberately ignore
/// (typically a companion app whose brand we already pull directly elsewhere).
struct SourcePriority: Sendable {
    /// Bundle ids whose HealthKit writes are ignored (mirrors Terra `setIgnoredSources`).
    var ignoredBundleIds: Set<String>

    init(ignoredBundleIds: Set<String> = []) {
        self.ignoredBundleIds = ignoredBundleIds
    }

    /// Whether a sample from this writer should be kept.
    func keep(sourceBundleId: String?) -> Bool {
        guard let id = sourceBundleId else { return true }
        return !ignoredBundleIds.contains(id)
    }

    /// Higher rank wins when two sources report the same logical metric.
    static func rank(deviceModel: String?) -> Int {
        DeviceClass.infer(fromModel: deviceModel).rawValue
    }

    /// Common companion-app bundle ids worth ignoring when pulled directly.
    static let knownCompanionApps: Set<String> = [
        "com.whoop.app",
        "com.ouraring.oura",
        "com.garmin.connect.mobile",
        "com.fitbit.FitbitMobile"
    ]
}
