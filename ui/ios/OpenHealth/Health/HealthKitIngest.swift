import Foundation
import HealthKit

enum HealthKitError: Error {
    case unavailable
}

/// Reads Apple Health into canonical records, one bounded page at a time.
///
/// Reads are paginated via `HKAnchoredObjectQuery` with an explicit `limit`: the
/// first call (nil anchor) returns up to `limit` samples plus an anchor, each
/// subsequent call returns the next page. The coordinator drains pages and writes
/// each one immediately, so a multi-year history never lands in memory at once.
/// This is the fix for the first-sync crash (loading the entire history + encoding
/// one monolithic NDJSON exhausted memory / blocked the main thread on device).
///
/// Apple exposes HRV only as SDNN, so HRV here is SDNN. rMSSD stays with the Mac
/// engine (Whoop). Anchors are returned as `Data`; the caller base64-encodes them
/// into `SyncManifest.anchors` and persists after every page (resumable sync).
final class HealthKitIngest: HealthReader {
    let store = HKHealthStore()
    private let source = "apple_health"
    private var observerQueries: [HKObserverQuery] = []

    static var isAvailable: Bool { HKHealthStore.isHealthDataAvailable() }
    var isAvailable: Bool { Self.isAvailable }

    // MARK: - Authorization

    func requestAuthorization() async throws {
        guard Self.isAvailable else { throw HealthKitError.unavailable }
        try await store.requestAuthorization(toShare: [], read: HealthKitTypes.readObjectTypes)
    }

    // MARK: - Paginated reads

    func readQuantityPage(series: SeriesType, anchorData: Data?, sinceDate: Date?, limit: Int) async throws -> (records: [SyncRecord], newAnchor: Data?) {
        guard let entry = HealthKitTypes.quantitySeries.first(where: { $0.series == series }),
              let type = HKQuantityType.quantityType(forIdentifier: entry.identifier) else {
            return ([], anchorData)
        }
        let unit = entry.unit
        let (records, newAnchor) = try await runAnchoredPage(type: type, anchorData: anchorData, sinceDate: sinceDate, limit: limit) { samples in
            (samples as? [HKQuantitySample] ?? []).map { SyncRecord.sample(self.mapQuantity($0, series: series, unit: unit)) }
        }
        return (records, newAnchor)
    }

    func readSleepPage(anchorData: Data?, sinceDate: Date?, limit: Int) async throws -> (records: [SyncRecord], newAnchor: Data?) {
        guard let type = HKCategoryType.categoryType(forIdentifier: .sleepAnalysis) else { return ([], anchorData) }
        return try await runAnchoredPage(type: type, anchorData: anchorData, sinceDate: sinceDate, limit: limit) { samples in
            (samples as? [HKCategorySample] ?? []).map { SyncRecord.event(self.mapSleep($0)) }
        }
    }

    func readWorkoutPage(anchorData: Data?, sinceDate: Date?, limit: Int) async throws -> (records: [SyncRecord], newAnchor: Data?) {
        return try await runAnchoredPage(type: .workoutType(), anchorData: anchorData, sinceDate: sinceDate, limit: limit) { samples in
            (samples as? [HKWorkout] ?? []).map { SyncRecord.event(self.mapWorkout($0)) }
        }
    }

    /// One bounded anchored-query page. `sinceDate` (first sync only) caps history;
    /// the anchor carries pagination position on later pages.
    private func runAnchoredPage(
        type: HKSampleType,
        anchorData: Data?,
        sinceDate: Date?,
        limit: Int,
        map: @escaping ([HKSample]?) -> [SyncRecord]
    ) async throws -> (records: [SyncRecord], newAnchor: Data?) {
        let anchor = Self.decodeAnchor(anchorData)
        let predicate = sinceDate.map { HKQuery.predicateForSamples(withStart: $0, end: nil, options: .strictStartDate) }
        return try await withCheckedThrowingContinuation { continuation in
            let query = HKAnchoredObjectQuery(type: type, predicate: predicate, anchor: anchor, limit: limit) { runningQuery, samples, _, newAnchor, error in
                self.store.stop(runningQuery)
                if let error { continuation.resume(throwing: error); return }
                continuation.resume(returning: (map(samples), Self.encodeAnchor(newAnchor)))
            }
            store.execute(query)
        }
    }

    // MARK: - Background delivery & observers (used from a later result)

    func enableBackgroundDelivery() {
        for entry in HealthKitTypes.observableQuantityTypes {
            store.enableBackgroundDelivery(for: entry.type, frequency: .hourly) { _, _ in }
        }
        if let sleep = HKCategoryType.categoryType(forIdentifier: .sleepAnalysis) {
            store.enableBackgroundDelivery(for: sleep, frequency: .hourly) { _, _ in }
        }
    }

    func installObservers(onChange: @escaping (@escaping () -> Void) -> Void) {
        var types: [HKSampleType] = HealthKitTypes.observableQuantityTypes.map { $0.type }
        if let sleep = HKCategoryType.categoryType(forIdentifier: .sleepAnalysis) { types.append(sleep) }
        types.append(HKObjectType.workoutType())
        for type in types {
            let query = HKObserverQuery(sampleType: type, predicate: nil) { _, completion, _ in
                onChange(completion)
            }
            store.execute(query)
            observerQueries.append(query)
        }
    }

    // MARK: - Mapping

    private func mapQuantity(_ s: HKQuantitySample, series: SeriesType, unit: HKUnit) -> HealthSample {
        let raw = s.quantity.doubleValue(for: unit)
        let value = (series == .oxygenSaturation) ? raw * 100 : raw
        return HealthSample(
            externalId: s.uuid.uuidString,
            seriesType: series,
            value: value,
            recordedAt: s.startDate,
            endAt: (s.endDate == s.startDate) ? nil : s.endDate,
            zoneOffsetSeconds: TimeZone.current.secondsFromGMT(for: s.startDate),
            source: source,
            sourceBundleId: s.sourceRevision.source.bundleIdentifier,
            deviceModel: s.device?.model,
            metadata: s.metadata?.compactMapValues { "\($0)" }
        )
    }

    private func mapSleep(_ s: HKCategorySample) -> HealthEvent {
        HealthEvent(
            externalId: s.uuid.uuidString,
            category: "sleep",
            type: HealthKitTypes.sleepStageName(s.value),
            startAt: s.startDate,
            endAt: s.endDate,
            zoneOffsetSeconds: TimeZone.current.secondsFromGMT(for: s.startDate),
            source: source,
            sourceBundleId: s.sourceRevision.source.bundleIdentifier,
            deviceModel: s.device?.model,
            metrics: ["duration_seconds": s.endDate.timeIntervalSince(s.startDate)]
        )
    }

    private func mapWorkout(_ w: HKWorkout) -> HealthEvent {
        HealthEvent(
            externalId: w.uuid.uuidString,
            category: "workout",
            type: "hk_activity_\(w.workoutActivityType.rawValue)",
            startAt: w.startDate,
            endAt: w.endDate,
            zoneOffsetSeconds: TimeZone.current.secondsFromGMT(for: w.startDate),
            source: source,
            sourceBundleId: w.sourceRevision.source.bundleIdentifier,
            deviceModel: w.device?.model,
            metrics: ["duration_seconds": w.duration]
        )
    }

    // MARK: - Anchor (de)serialization

    static func encodeAnchor(_ anchor: HKQueryAnchor?) -> Data? {
        guard let anchor else { return nil }
        return try? NSKeyedArchiver.archivedData(withRootObject: anchor, requiringSecureCoding: true)
    }

    static func decodeAnchor(_ data: Data?) -> HKQueryAnchor? {
        guard let data else { return nil }
        return try? NSKeyedUnarchiver.unarchivedObject(ofClass: HKQueryAnchor.self, from: data)
    }
}
