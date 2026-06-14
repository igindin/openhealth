import Foundation

/// Sync state written to the bridge `meta/manifest.json`. Holds the per-series
/// HealthKit query anchors (base64-encoded `HKQueryAnchor`) so re-ingest is
/// incremental and idempotent across launches.
struct SyncManifest: Codable, Hashable, Sendable {
    var schemaVersion: Int = 1
    var deviceId: String
    var anchors: [String: String]      // series_type raw value -> base64 anchor
    var lastInboxWriteAt: Date?

    init(deviceId: String, anchors: [String: String] = [:], lastInboxWriteAt: Date? = nil) {
        self.deviceId = deviceId
        self.anchors = anchors
        self.lastInboxWriteAt = lastInboxWriteAt
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case deviceId = "device_id"
        case anchors
        case lastInboxWriteAt = "last_inbox_write_at"
    }
}
