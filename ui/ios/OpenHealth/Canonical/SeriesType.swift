import Foundation

/// Canonical metric vocabulary for the OpenHealth bridge wire format.
///
/// Seeded from openwearables `series_types.py` (MIT, Copyright (c) 2025 Momentum).
/// The string raw values are a durable contract: never rename an existing case's
/// raw value once records carrying it have been persisted or synced.
enum SeriesType: String, Codable, CaseIterable, Sendable {
    // Heart
    case heartRate = "heart_rate"
    case restingHeartRate = "resting_heart_rate"
    case walkingHeartRateAverage = "walking_heart_rate_average"
    case heartRateVariabilitySDNN = "heart_rate_variability_sdnn"
    case heartRateVariabilityRMSSD = "heart_rate_variability_rmssd"
    case heartRateRecoveryOneMinute = "heart_rate_recovery_one_minute"

    // Respiratory / blood
    case respiratoryRate = "respiratory_rate"
    case oxygenSaturation = "oxygen_saturation"
    case bloodGlucose = "blood_glucose"

    // Activity
    case stepCount = "step_count"
    case activeEnergyBurned = "active_energy_burned"
    case basalEnergyBurned = "basal_energy_burned"
    case distanceWalkingRunning = "distance_walking_running"
    case flightsClimbed = "flights_climbed"
    case appleExerciseTime = "apple_exercise_time"
    case vo2Max = "vo2_max"

    // Body
    case bodyMass = "body_mass"
    case bodyFatPercentage = "body_fat_percentage"
    case skinTemperature = "skin_temperature"

    // Sleep (duration roll-up; per-stage windows are HealthEvent)
    case sleepDurationMinutes = "sleep_duration_minutes"

    /// Canonical unit string (matches the openwearables unit vocabulary).
    var unit: String {
        switch self {
        case .heartRate, .restingHeartRate, .walkingHeartRateAverage, .heartRateRecoveryOneMinute:
            return "bpm"
        case .heartRateVariabilitySDNN, .heartRateVariabilityRMSSD:
            return "ms"
        case .respiratoryRate:
            return "breaths_per_min"
        case .oxygenSaturation, .bodyFatPercentage:
            return "percent"
        case .bloodGlucose:
            return "mg_dl"
        case .stepCount, .flightsClimbed:
            return "count"
        case .activeEnergyBurned, .basalEnergyBurned:
            return "kcal"
        case .distanceWalkingRunning:
            return "m"
        case .appleExerciseTime, .sleepDurationMinutes:
            return "min"
        case .vo2Max:
            return "ml_kg_min"
        case .bodyMass:
            return "kg"
        case .skinTemperature:
            return "celsius"
        }
    }

    /// True for either HRV metric. Terra discipline: rMSSD and SDNN are different
    /// measures and must never be mixed or compared within one experiment.
    var isHRV: Bool {
        self == .heartRateVariabilitySDNN || self == .heartRateVariabilityRMSSD
    }
}
