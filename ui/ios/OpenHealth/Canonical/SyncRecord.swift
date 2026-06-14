import Foundation

/// A journal check-in serialized for the bridge. Wraps the local `JournalEntry`
/// into the snake_case wire shape the Mac engine reads.
struct JournalSyncRecord: Codable, Hashable, Sendable {
    var schemaVersion: Int = 1
    let dayKey: String
    let recordedAt: Date
    let yesNo: [String: Bool]
    let ratings: [String: Int]
    let note: String

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case dayKey = "day_key"
        case recordedAt = "recorded_at"
        case yesNo = "yes_no"
        case ratings, note
    }
}

/// Life-context record (calendar load, weather, travel, stressor). Defined now;
/// populated from Result 3 onward.
struct ContextNote: Codable, Hashable, Sendable {
    var schemaVersion: Int = 1
    let externalId: String
    let date: String
    let kind: String          // "calendar_load" | "weather" | "travel" | "stressor"
    var values: [String: Double]?
    var text: String?

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case externalId = "external_id"
        case date, kind, values, text
    }
}

/// One line of the NDJSON bridge stream. A `kind` discriminator is written
/// alongside the wrapped record's own fields, so the Mac engine can switch on it.
enum SyncRecord: Codable, Hashable, Sendable {
    case sample(HealthSample)
    case event(HealthEvent)
    case journal(JournalSyncRecord)
    case context(ContextNote)

    private enum Kind: String, Codable { case sample, event, journal, context }
    private enum DiscriminatorKey: String, CodingKey { case kind }

    init(from decoder: Decoder) throws {
        let keyed = try decoder.container(keyedBy: DiscriminatorKey.self)
        switch try keyed.decode(Kind.self, forKey: .kind) {
        case .sample:  self = .sample(try HealthSample(from: decoder))
        case .event:   self = .event(try HealthEvent(from: decoder))
        case .journal: self = .journal(try JournalSyncRecord(from: decoder))
        case .context: self = .context(try ContextNote(from: decoder))
        }
    }

    func encode(to encoder: Encoder) throws {
        var keyed = encoder.container(keyedBy: DiscriminatorKey.self)
        switch self {
        case .sample(let v):  try keyed.encode(Kind.sample, forKey: .kind);  try v.encode(to: encoder)
        case .event(let v):   try keyed.encode(Kind.event, forKey: .kind);   try v.encode(to: encoder)
        case .journal(let v): try keyed.encode(Kind.journal, forKey: .kind); try v.encode(to: encoder)
        case .context(let v): try keyed.encode(Kind.context, forKey: .kind); try v.encode(to: encoder)
        }
    }
}

extension JournalSyncRecord {
    /// Map a locally-stored `JournalEntry` into its bridge wire shape.
    init(entry: JournalEntry) {
        var yesNo: [String: Bool] = [:]
        var ratings: [String: Int] = [:]
        for (key, value) in entry.values {
            if let b = value.boolValue { yesNo[key] = b }
            else if let n = value.scaleValue { ratings[key] = n }
        }
        self.init(
            dayKey: entry.dayKey,
            recordedAt: entry.recordedAt,
            yesNo: yesNo,
            ratings: ratings,
            note: entry.note
        )
    }
}
