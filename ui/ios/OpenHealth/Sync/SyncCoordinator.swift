import Foundation
import Observation

/// Orchestrates one sync pass: read HealthKit deltas (per persisted anchor),
/// write them as an NDJSON batch to the iCloud bridge `inbox/`, and persist the
/// updated anchors in the manifest (locally + in the bridge `meta/`).
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

    private let ingest = HealthKitIngest()
    private let defaults = UserDefaults.standard
    private let deviceKey = "openhealth.device_id"
    private let manifestKey = "openhealth.manifest"

    var healthAvailable: Bool { HealthKitIngest.isAvailable }

    var deviceId: String {
        if let id = defaults.string(forKey: deviceKey) { return id }
        let id = UUID().uuidString
        defaults.set(id, forKey: deviceKey)
        return id
    }

    // MARK: - Authorization

    func requestAuthorization() async {
        guard HealthKitIngest.isAvailable else { status = .healthUnavailable; return }
        do {
            try await ingest.requestAuthorization()
            authorized = true
        } catch {
            status = .failed("Authorization failed: \(error.localizedDescription)")
        }
    }

    // MARK: - Sync pass

    func runSync() async {
        guard HealthKitIngest.isAvailable else { status = .healthUnavailable; return }
        status = .syncing
        do {
            var manifest = loadManifest()
            var records: [SyncRecord] = []

            // Quantity series deltas.
            for entry in HealthKitTypes.quantitySeries {
                let key = entry.series.rawValue
                let anchorData = manifest.anchors[key].flatMap { Data(base64Encoded: $0) }
                let (samples, newAnchor) = try await ingest.readQuantityDelta(
                    series: entry.series,
                    identifier: entry.identifier,
                    unit: entry.unit,
                    anchorData: anchorData
                )
                records.append(contentsOf: samples.map(SyncRecord.sample))
                if let newAnchor { manifest.anchors[key] = newAnchor.base64EncodedString() }
            }

            // Sleep windows.
            let sleepAnchor = manifest.anchors["sleep"].flatMap { Data(base64Encoded: $0) }
            let (sleepEvents, sleepNew) = try await ingest.readSleepDelta(anchorData: sleepAnchor)
            records.append(contentsOf: sleepEvents.map(SyncRecord.event))
            if let sleepNew { manifest.anchors["sleep"] = sleepNew.base64EncodedString() }

            // Workouts.
            let workoutAnchor = manifest.anchors["workout"].flatMap { Data(base64Encoded: $0) }
            let (workoutEvents, workoutNew) = try await ingest.readWorkoutDelta(anchorData: workoutAnchor)
            records.append(contentsOf: workoutEvents.map(SyncRecord.event))
            if let workoutNew { manifest.anchors["workout"] = workoutNew.base64EncodedString() }

            // Write to the bridge (off the main thread for the iCloud container lookup).
            let transport = await resolveTransport()
            if let transport, !records.isEmpty {
                try transport.writeInbox(records, batchName: "batch-\(Self.batchStamp())")
            }
            manifest.lastInboxWriteAt = Date()
            if let transport { try? transport.writeManifest(manifest) }
            saveManifest(manifest)

            status = .synced(Date())
        } catch {
            status = .failed(error.localizedDescription)
        }
    }

    // MARK: - Transport & manifest persistence

    private func resolveTransport() async -> SyncTransport? {
        await Task.detached { ICloudDriveTransport() }.value
    }

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
