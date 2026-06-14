import Foundation
import HealthKit

enum HealthKitError: Error {
    case unavailable
}

/// Reads Apple Health into canonical records with incremental anchors.
///
/// - Live deltas: `HKAnchoredObjectQuery` (first run returns history, later runs
///   only the changes since the persisted anchor).
/// - Background wakes: `HKObserverQuery` + `enableBackgroundDelivery` (the
///   observer only signals *that* something changed; we then run an anchored
///   query to fetch *what* changed, and always call the observer completion).
///
/// Apple exposes HRV only as SDNN, so HRV here is SDNN. rMSSD stays with the Mac
/// engine (Whoop). Anchors are returned as `Data`; the caller base64-encodes them
/// into `SyncManifest.anchors`.
final class HealthKitIngest {
    let store = HKHealthStore()
    private let source = "apple_health"
    private var observerQueries: [HKObserverQuery] = []

    static var isAvailable: Bool { HKHealthStore.isHealthDataAvailable() }

    // MARK: - Authorization

    func requestAuthorization() async throws {
        guard Self.isAvailable else { throw HealthKitError.unavailable }
        try await store.requestAuthorization(toShare: [], read: HealthKitTypes.readObjectTypes)
    }

    // MARK: - Anchored delta reads

    /// Read new/changed samples for one quantity series since `anchorData`.
    func readQuantityDelta(
        series: SeriesType,
        identifier: HKQuantityTypeIdentifier,
        unit: HKUnit,
        anchorData: Data?
    ) async throws -> (samples: [HealthSample], newAnchor: Data?) {
        guard let type = HKQuantityType.quantityType(forIdentifier: identifier) else {
            return ([], anchorData)
        }
        let anchor = Self.decodeAnchor(anchorData)
        return try await withCheckedThrowingContinuation { continuation in
            let query = HKAnchoredObjectQuery(
                type: type, predicate: nil, anchor: anchor, limit: HKObjectQueryNoLimit
            ) { runningQuery, samples, _, newAnchor, error in
                self.store.stop(runningQuery)
                if let error { continuation.resume(throwing: error); return }
                let mapped = (samples as? [HKQuantitySample] ?? [])
                    .map { self.mapQuantity($0, series: series, unit: unit) }
                continuation.resume(returning: (mapped, Self.encodeAnchor(newAnchor)))
            }
            store.execute(query)
        }
    }

    /// Read new/changed sleep-analysis windows since `anchorData`.
    func readSleepDelta(anchorData: Data?) async throws -> (events: [HealthEvent], newAnchor: Data?) {
        guard let type = HKCategoryType.categoryType(forIdentifier: .sleepAnalysis) else {
            return ([], anchorData)
        }
        let anchor = Self.decodeAnchor(anchorData)
        return try await withCheckedThrowingContinuation { continuation in
            let query = HKAnchoredObjectQuery(
                type: type, predicate: nil, anchor: anchor, limit: HKObjectQueryNoLimit
            ) { runningQuery, samples, _, newAnchor, error in
                self.store.stop(runningQuery)
                if let error { continuation.resume(throwing: error); return }
                let mapped = (samples as? [HKCategorySample] ?? []).map { self.mapSleep($0) }
                continuation.resume(returning: (mapped, Self.encodeAnchor(newAnchor)))
            }
            store.execute(query)
        }
    }

    /// Read new/changed workouts since `anchorData`.
    func readWorkoutDelta(anchorData: Data?) async throws -> (events: [HealthEvent], newAnchor: Data?) {
        let anchor = Self.decodeAnchor(anchorData)
        return try await withCheckedThrowingContinuation { continuation in
            let query = HKAnchoredObjectQuery(
                type: .workoutType(), predicate: nil, anchor: anchor, limit: HKObjectQueryNoLimit
            ) { runningQuery, samples, _, newAnchor, error in
                self.store.stop(runningQuery)
                if let error { continuation.resume(throwing: error); return }
                let mapped = (samples as? [HKWorkout] ?? []).map { self.mapWorkout($0) }
                continuation.resume(returning: (mapped, Self.encodeAnchor(newAnchor)))
            }
            store.execute(query)
        }
    }

    // MARK: - Background delivery & observers

    /// Ask HealthKit to wake the app on new data (requires the background-delivery
    /// entitlement). Frequency is capped per type by the system (hourly for steps).
    func enableBackgroundDelivery() {
        for entry in HealthKitTypes.observableQuantityTypes {
            store.enableBackgroundDelivery(for: entry.type, frequency: .hourly) { _, _ in }
        }
        if let sleep = HKCategoryType.categoryType(forIdentifier: .sleepAnalysis) {
            store.enableBackgroundDelivery(for: sleep, frequency: .hourly) { _, _ in }
        }
    }

    /// Install observer queries that call `onChange` when matching data changes.
    /// Each observer MUST call its completion handler or HealthKit backs off and
    /// eventually stops delivering — so we forward it through `onChange`.
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
        // HealthKit reports saturation as a 0...1 fraction; canonical unit is percent.
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
