import Foundation
import Observation

/// Orchestrates one sync pass: drain HealthKit deltas page by page (per persisted
/// anchor) and write each page as its own NDJSON batch to the iCloud bridge
/// `inbox/`. The anchor is persisted after every page, so a sync interrupted by a
/// crash or backgrounding resumes where it left off — and never holds more than
/// one page in memory.
///
/// Result 1 is one-directional (phone → bridge). Reading the Mac's `outbox/`
/// arrives in Result 2.
@Observable
@MainActor
final class SyncCoordinator {

    enum Status: Equatable {
        case idle
        case syncing
        case synced(Date)
        case failed(String)
        case healthUnavailable
    }

    private(set) var status: Status = .idle
    private(set) var authorized = false

    private let reader: HealthReader
    private let transportProvider: () async -> SyncTransport?
    private let pageSize: Int
    private let initialHistoryDays: Int
    private let maxPagesPerType: Int

    private let defaults = UserDefaults.standard
    private let deviceKey = "openhealth.device_id"
    private let manifestKey = "openhealth.manifest"
    private var manifest: SyncManifest

    init(
        reader: HealthReader = HealthKitIngest(),
        transportProvider: @escaping () async -> SyncTransport? = { await Task.detached { ICloudDriveTransport() }.value },
        pageSize: Int = 2000,
        initialHistoryDays: Int = 365,
        maxPagesPerType: Int = 1000
    ) {
        self.reader = reader
        self.transportProvider = transportProvider
        self.pageSize = pageSize
        self.initialHistoryDays = initialHistoryDays
        self.maxPagesPerType = maxPagesPerType
        self.manifest = SyncManifest(deviceId: defaults.string(forKey: deviceKey) ?? UUID().uuidString)
        self.manifest = loadManifest()
        // Restore status across launches so a relaunch doesn't read as "never synced".
        if let last = manifest.lastInboxWriteAt {
            status = .synced(last)
            authorized = true
        }
    }

    var healthAvailable: Bool { reader.isAvailable }

    var deviceId: String {
        if let id = defaults.string(forKey: deviceKey) { return id }
        let id = UUID().uuidString
        defaults.set(id, forKey: deviceKey)
        return id
    }

    // MARK: - Authorization

    func requestAuthorization() async {
        guard reader.isAvailable else { status = .healthUnavailable; return }
        do {
            try await reader.requestAuthorization()
            authorized = true
        } catch {
            status = .failed("Authorization failed: \(error.localizedDescription)")
        }
    }

    // MARK: - Sync pass

    func runSync() async {
        guard reader.isAvailable else { status = .healthUnavailable; return }
        guard let transport = await transportProvider() else {
            status = .failed("iCloud Drive is not available. Sign in to iCloud and enable Drive.")
            return
        }
        status = .syncing
        manifest = loadManifest()
        let floor = Calendar.current.date(byAdding: .day, value: -initialHistoryDays, to: Date())

        do {
            for entry in HealthKitTypes.quantitySeries {
                try await drain(key: entry.series.rawValue, transport: transport) { [reader, pageSize] anchor in
                    try await reader.readQuantityPage(series: entry.series,
                                                      anchorData: anchor,
                                                      sinceDate: anchor == nil ? floor : nil,
                                                      limit: pageSize)
                }
            }
            try await drain(key: "sleep", transport: transport) { [reader, pageSize] anchor in
                try await reader.readSleepPage(anchorData: anchor, sinceDate: anchor == nil ? floor : nil, limit: pageSize)
            }
            try await drain(key: "workout", transport: transport) { [reader, pageSize] anchor in
                try await reader.readWorkoutPage(anchorData: anchor, sinceDate: anchor == nil ? floor : nil, limit: pageSize)
            }

            manifest.lastInboxWriteAt = Date()
            try? transport.writeManifest(manifest)
            saveManifest(manifest)
            status = .synced(Date())
        } catch {
            status = .failed(error.localizedDescription)
        }
    }

    /// Drain one type page by page: write each non-empty page, advance + persist
    /// the anchor after every page, stop on a short (final) page or the safety cap.
    private func drain(
        key: String,
        transport: SyncTransport,
        fetch: (Data?) async throws -> (records: [SyncRecord], newAnchor: Data?)
    ) async throws {
        var anchorData = manifest.anchors[key].flatMap { Data(base64Encoded: $0) }
        var page = 0
        while true {
            let (records, newAnchor) = try await fetch(anchorData)
            if !records.isEmpty {
                try transport.writeInbox(records, batchName: "\(key)-\(Self.batchStamp())-p\(page)")
            }
            if let newAnchor {
                anchorData = newAnchor
                manifest.anchors[key] = newAnchor.base64EncodedString()
                saveManifest(manifest)
            }
            page += 1
            if records.count < pageSize { break }
            if page >= maxPagesPerType { break }
        }
    }

    // MARK: - Manifest persistence

    private func loadManifest() -> SyncManifest {
        if let data = defaults.data(forKey: manifestKey) {
            let decoder = JSONDecoder()
            decoder.dateDecodingStrategy = .iso8601
            if let manifest = try? decoder.decode(SyncManifest.self, from: data) { return manifest }
        }
        return SyncManifest(deviceId: deviceId)
    }

    private func saveManifest(_ manifest: SyncManifest) {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        if let data = try? encoder.encode(manifest) { defaults.set(data, forKey: manifestKey) }
    }

    private static func batchStamp() -> String {
        "\(Int(Date().timeIntervalSince1970))-\(UUID().uuidString.prefix(6))"
    }
}
