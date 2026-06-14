import Foundation

/// One canonical time-series point on the bridge wire format.
///
/// Mirrors openwearables `data_point_series` (MIT). Snake_case coding keys are the
/// NDJSON shape the Mac engine ingests. `externalId` (the HealthKit sample UUID)
/// is the dedup key: re-reading the same sample yields the same record, so the
/// Mac side overwrites by key rather than appending duplicates.
struct HealthSample: Codable, Hashable, Identifiable, Sendable {
    var schemaVersion: Int = 1
    let externalId: String
    let seriesType: SeriesType
    let value: Double
    let unit: String
    let recordedAt: Date
    let endAt: Date?
    let zoneOffsetSeconds: Int
    let source: String
    let sourceBundleId: String?
    let deviceModel: String?
    var metadata: [String: String]?

    var id: String { externalId }

    init(
        externalId: String,
        seriesType: SeriesType,
        value: Double,
        unit: String? = nil,
        recordedAt: Date,
        endAt: Date? = nil,
        zoneOffsetSeconds: Int,
        source: String,
        sourceBundleId: String? = nil,
        deviceModel: String? = nil,
        metadata: [String: String]? = nil
    ) {
        self.externalId = externalId
        self.seriesType = seriesType
        self.value = value
        self.unit = unit ?? seriesType.unit
        self.recordedAt = recordedAt
        self.endAt = endAt
        self.zoneOffsetSeconds = zoneOffsetSeconds
        self.source = source
        self.sourceBundleId = sourceBundleId
        self.deviceModel = deviceModel
        self.metadata = metadata
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case externalId = "external_id"
        case seriesType = "series_type"
        case value, unit
        case recordedAt = "recorded_at"
        case endAt = "end_at"
        case zoneOffsetSeconds = "zone_offset_seconds"
        case source
        case sourceBundleId = "source_bundle_id"
        case deviceModel = "device_model"
        case metadata
    }
}
