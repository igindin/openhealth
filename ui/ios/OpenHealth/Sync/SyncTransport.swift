import Foundation

/// A pluggable sync sink for the bridge. iCloud Drive is the default transport;
/// a local-network transport lands in a later result. The phone writes the
/// `inbox/`, the Mac engine writes the `outbox/` (read support arrives in Result 2).
protocol SyncTransport: Sendable {
    /// Write a batch of records as one immutable NDJSON file in `inbox/`.
    /// Returns the written file URL. `batchName` should be unique and sortable.
    @discardableResult
    func writeInbox(_ records: [SyncRecord], batchName: String) throws -> URL

    /// Persist the sync manifest (anchors, timestamps) to `meta/manifest.json`.
    func writeManifest(_ manifest: SyncManifest) throws

    /// Load the manifest if present.
    func readManifest() throws -> SyncManifest?

    /// Read the Mac engine's snapshot from `outbox/snapshot.json`, if present.
    func readOutbox() throws -> HealthSnapshot?
}

/// Writes the bridge layout to a plain directory. This is the testable base used
/// directly in tests and wrapped by `ICloudDriveTransport` for the iCloud path.
struct FileSyncTransport: SyncTransport {
    let root: URL

    private var inboxURL: URL { root.appendingPathComponent("inbox", isDirectory: true) }
    private var metaURL: URL { root.appendingPathComponent("meta", isDirectory: true) }
    private var manifestURL: URL { metaURL.appendingPathComponent("manifest.json") }

    init(root: URL) {
        self.root = root
    }

    private func ensureDirectories() throws {
        try FileManager.default.createDirectory(at: inboxURL, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: metaURL, withIntermediateDirectories: true)
    }

    @discardableResult
    func writeInbox(_ records: [SyncRecord], batchName: String) throws -> URL {
        try ensureDirectories()
        let data = try NDJSON.encode(records)
        let fileURL = inboxURL.appendingPathComponent("\(batchName).ndjson")
        try data.write(to: fileURL, options: [.atomic])
        return fileURL
    }

    func writeManifest(_ manifest: SyncManifest) throws {
        try ensureDirectories()
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        encoder.dateEncodingStrategy = .iso8601
        let data = try encoder.encode(manifest)
        try data.write(to: manifestURL, options: [.atomic])
    }

    func readManifest() throws -> SyncManifest? {
        guard let data = try? Data(contentsOf: manifestURL) else { return nil }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try decoder.decode(SyncManifest.self, from: data)
    }

    func readOutbox() throws -> HealthSnapshot? {
        let snapshotURL = root.appendingPathComponent("outbox", isDirectory: true)
            .appendingPathComponent("snapshot.json")
        guard let data = try? Data(contentsOf: snapshotURL) else { return nil }
        return try JSONDecoder().decode(HealthSnapshot.self, from: data)
    }
}
