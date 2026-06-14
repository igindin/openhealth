import Foundation

/// Newline-delimited JSON for the bridge: one `SyncRecord` per line. Compact
/// (no pretty-printing), ISO-8601 dates, sorted keys for stable, diff-friendly
/// output. Append-only files; the Mac engine reads them line by line.
enum NDJSON {
    static let encoder: JSONEncoder = {
        let e = JSONEncoder()
        e.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        e.dateEncodingStrategy = .iso8601
        return e
    }()

    static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .iso8601
        return d
    }()

    /// Encode records to NDJSON bytes (trailing newline included).
    static func encode(_ records: [SyncRecord]) throws -> Data {
        var out = Data()
        for record in records {
            let line = try encoder.encode(record)
            out.append(line)
            out.append(0x0A)   // "\n"
        }
        return out
    }

    /// Decode NDJSON bytes, skipping blank lines.
    static func decode(_ data: Data) throws -> [SyncRecord] {
        guard let text = String(data: data, encoding: .utf8) else { return [] }
        var records: [SyncRecord] = []
        for line in text.split(separator: "\n", omittingEmptySubsequences: true) {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            guard !trimmed.isEmpty, let lineData = trimmed.data(using: .utf8) else { continue }
            records.append(try decoder.decode(SyncRecord.self, from: lineData))
        }
        return records
    }
}
