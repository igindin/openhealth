import Foundation
import HealthKit

/// Bridges the canonical `SeriesType` vocabulary to HealthKit identifiers and
/// units, and defines the minimal v1 read set (HRV, RHR, sleep, steps, energy,
/// plus a few vitals). Expand the lists to widen coverage later.
enum HealthKitTypes {

    /// Quantity series read in v1: canonical series, HealthKit identifier, unit.
    static let quantitySeries: [(series: SeriesType, identifier: HKQuantityTypeIdentifier, unit: HKUnit)] = [
        (.heartRate,                 .heartRate,                 .count().unitDivided(by: .minute())),
        (.restingHeartRate,          .restingHeartRate,          .count().unitDivided(by: .minute())),
        (.walkingHeartRateAverage,   .walkingHeartRateAverage,   .count().unitDivided(by: .minute())),
        (.heartRateVariabilitySDNN,  .heartRateVariabilitySDNN,  .secondUnit(with: .milli)),
        (.respiratoryRate,           .respiratoryRate,           .count().unitDivided(by: .minute())),
        (.oxygenSaturation,          .oxygenSaturation,          .percent()),
        (.stepCount,                 .stepCount,                 .count()),
        (.activeEnergyBurned,        .activeEnergyBurned,        .kilocalorie()),
        (.basalEnergyBurned,         .basalEnergyBurned,         .kilocalorie()),
        (.vo2Max,                    .vo2Max,                    HKUnit(from: "ml/kg*min")),
        (.bodyMass,                  .bodyMass,                  .gramUnit(with: .kilo))
    ]

    /// The full set of object types we request read access to.
    static var readObjectTypes: Set<HKObjectType> {
        var types = Set<HKObjectType>()
        for entry in quantitySeries {
            if let t = HKQuantityType.quantityType(forIdentifier: entry.identifier) { types.insert(t) }
        }
        if let sleep = HKCategoryType.categoryType(forIdentifier: .sleepAnalysis) { types.insert(sleep) }
        types.insert(HKObjectType.workoutType())
        return types
    }

    /// Quantity types only, used to install per-type observers / background delivery.
    static var observableQuantityTypes: [(series: SeriesType, type: HKQuantityType, unit: HKUnit)] {
        quantitySeries.compactMap { entry in
            guard let t = HKQuantityType.quantityType(forIdentifier: entry.identifier) else { return nil }
            return (entry.series, t, entry.unit)
        }
    }

    /// Human-stable label for a sleep-analysis category value.
    static func sleepStageName(_ value: Int) -> String {
        switch HKCategoryValueSleepAnalysis(rawValue: value) {
        case .inBed:              return "in_bed"
        case .asleepUnspecified:  return "asleep_unspecified"
        case .awake:              return "awake"
        case .asleepCore:         return "asleep_core"
        case .asleepDeep:         return "asleep_deep"
        case .asleepREM:          return "asleep_rem"
        default:                  return "unknown"
        }
    }
}
