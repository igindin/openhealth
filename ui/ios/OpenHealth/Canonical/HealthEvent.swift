import Foundation

/// A windowed canonical record: a workout or a sleep session.
///
/// Mirrors openwearables `event_record` (MIT). `metrics` carries the small set of
/// pre-aggregated scalars for the UI (Terra's summary half); raw per-sample series
/// stay as separate `HealthSample` records (the detailed half).
struct HealthEvent: Codable, Hashable, Identifiable, Sendable {
    var schemaVersion: Int = 1
    let externalId: String
    let category: String   // "workout" | "sleep"
    let type: String       // workout activity type, or "asleep" / stage label
    let startAt: Date
    let endAt: Date
    let zoneOffsetSeconds: Int
    let source: String
    let sourceBundleId: String?
    let deviceModel: String?
    var metrics: [String: Double]?

    var id: String { externalId }

    var durationSeconds: Double { endAt.timeIntervalSince(startAt) }

    init(
        externalId: String,
        category: String,
        type: String,
        startAt: Date,
        endAt: Date,
        zoneOffsetSeconds: Int,
        source: String,
        sourceBundleId: String? = nil,
        deviceModel: String? = nil,
        metrics: [String: Double]? = nil
    ) {
        self.externalId = externalId
        self.category = category
        self.type = type
        self.startAt = startAt
        self.endAt = endAt
        self.zoneOffsetSeconds = zoneOffsetSeconds
        self.source = source
        self.sourceBundleId = sourceBundleId
        self.deviceModel = deviceModel
        self.metrics = metrics
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case externalId = "external_id"
        case category, type
        case startAt = "start_at"
        case endAt = "end_at"
        case zoneOffsetSeconds = "zone_offset_seconds"
        case source
        case sourceBundleId = "source_bundle_id"
        case deviceModel = "device_model"
        case metrics
    }
}
