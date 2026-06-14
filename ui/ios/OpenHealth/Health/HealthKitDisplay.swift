import Foundation
import HealthKit

/// A small, display-grade read of Apple Health for the Today/Trends screens:
/// latest values plus 30-day series for the north-star metrics. This is what the
/// phone shows instantly/offline before (and alongside) the Mac engine's results.
struct HealthDisplay {
    var sdnnLatest: Double?
    var rhrLatest: Double?
    var sleepHours: Double?
    var stepsToday: Double?
    var sdnnSeries: [(date: Date, value: Double)] = []
    var rhrSeries: [(date: Date, value: Double)] = []

    /// On-device readiness estimate from latest SDNN vs its own recent baseline.
    var readinessEstimate: Int? {
        guard let sdnn = sdnnLatest else { return nil }
        return Readiness.estimate(latestSDNN: sdnn, baselineSDNN: sdnnSeries.map { $0.value })
    }
}

extension HealthKitIngest {

    func buildDisplay() async -> HealthDisplay {
        let bpm = HKUnit.count().unitDivided(by: .minute())
        let ms = HKUnit.secondUnit(with: .milli)

        var display = HealthDisplay()
        display.sdnnLatest = await latestValue(identifier: .heartRateVariabilitySDNN, unit: ms)
        display.rhrLatest = await latestValue(identifier: .restingHeartRate, unit: bpm)
        display.sleepHours = await lastNightSleepHours()
        display.stepsToday = await todaySum(identifier: .stepCount, unit: .count())
        display.sdnnSeries = await dailySeries(identifier: .heartRateVariabilitySDNN, unit: ms, days: 30, sum: false)
        display.rhrSeries = await dailySeries(identifier: .restingHeartRate, unit: bpm, days: 30, sum: false)
        return display
    }

    /// Most recent sample value for a quantity series.
    func latestValue(identifier: HKQuantityTypeIdentifier, unit: HKUnit) async -> Double? {
        guard let type = HKQuantityType.quantityType(forIdentifier: identifier) else { return nil }
        return await withCheckedContinuation { continuation in
            let sort = NSSortDescriptor(key: HKSampleSortIdentifierEndDate, ascending: false)
            let query = HKSampleQuery(sampleType: type, predicate: nil, limit: 1, sortDescriptors: [sort]) { _, samples, _ in
                let value = (samples?.first as? HKQuantitySample)?.quantity.doubleValue(for: unit)
                continuation.resume(returning: value)
            }
            store.execute(query)
        }
    }

    /// Daily series over the last `days` days (average per day, or cumulative sum).
    func dailySeries(identifier: HKQuantityTypeIdentifier, unit: HKUnit, days: Int, sum: Bool) async -> [(date: Date, value: Double)] {
        guard let type = HKQuantityType.quantityType(forIdentifier: identifier) else { return [] }
        let calendar = Calendar.current
        let end = Date()
        let anchor = calendar.startOfDay(for: end)
        guard let start = calendar.date(byAdding: .day, value: -(days - 1), to: anchor) else { return [] }
        let options: HKStatisticsOptions = sum ? .cumulativeSum : .discreteAverage
        let predicate = HKQuery.predicateForSamples(withStart: start, end: end, options: .strictStartDate)

        return await withCheckedContinuation { continuation in
            let query = HKStatisticsCollectionQuery(
                quantityType: type,
                quantitySamplePredicate: predicate,
                options: options,
                anchorDate: anchor,
                intervalComponents: DateComponents(day: 1)
            )
            query.initialResultsHandler = { _, collection, _ in
                var output: [(date: Date, value: Double)] = []
                collection?.enumerateStatistics(from: start, to: end) { stat, _ in
                    let quantity = sum ? stat.sumQuantity() : stat.averageQuantity()
                    if let quantity { output.append((stat.startDate, quantity.doubleValue(for: unit))) }
                }
                continuation.resume(returning: output)
            }
            store.execute(query)
        }
    }

    /// Cumulative sum for today (e.g. steps).
    func todaySum(identifier: HKQuantityTypeIdentifier, unit: HKUnit) async -> Double? {
        guard let type = HKQuantityType.quantityType(forIdentifier: identifier) else { return nil }
        let start = Calendar.current.startOfDay(for: Date())
        let predicate = HKQuery.predicateForSamples(withStart: start, end: Date(), options: .strictStartDate)
        return await withCheckedContinuation { continuation in
            let query = HKStatisticsQuery(quantityType: type, quantitySamplePredicate: predicate, options: .cumulativeSum) { _, stats, _ in
                continuation.resume(returning: stats?.sumQuantity()?.doubleValue(for: unit))
            }
            store.execute(query)
        }
    }

    /// Hours classified as asleep in the last 24h.
    func lastNightSleepHours() async -> Double? {
        guard let type = HKCategoryType.categoryType(forIdentifier: .sleepAnalysis) else { return nil }
        guard let start = Calendar.current.date(byAdding: .hour, value: -24, to: Date()) else { return nil }
        let predicate = HKQuery.predicateForSamples(withStart: start, end: Date(), options: [])
        return await withCheckedContinuation { continuation in
            let query = HKSampleQuery(sampleType: type, predicate: predicate, limit: HKObjectQueryNoLimit, sortDescriptors: nil) { _, samples, _ in
                let asleep = (samples as? [HKCategorySample] ?? []).filter { Self.isAsleep($0.value) }
                let seconds = asleep.reduce(0.0) { $0 + $1.endDate.timeIntervalSince($1.startDate) }
                continuation.resume(returning: seconds > 0 ? seconds / 3600 : nil)
            }
            store.execute(query)
        }
    }

    private static func isAsleep(_ value: Int) -> Bool {
        switch HKCategoryValueSleepAnalysis(rawValue: value) {
        case .asleepCore, .asleepDeep, .asleepREM, .asleepUnspecified:
            return true
        default:
            return false
        }
    }
}
