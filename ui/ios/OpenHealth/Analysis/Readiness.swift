import Foundation

/// On-device, display-grade readiness estimate. This is NOT the engine's recovery
/// score: until the Mac round-trip lands (Result 2), the phone shows a transparent
/// estimate of how today's HRV sits against the user's own recent baseline.
///
/// Apple Health exposes HRV only as SDNN, so this runs on SDNN. rMSSD-based
/// recovery stays with the Mac engine (Whoop). The two are never mixed.
enum Readiness {
    /// z-score of `latest` against a trailing `baseline`. Returns nil when the
    /// baseline is too short or has no variance.
    static func zScore(latest: Double, baseline: [Double]) -> Double? {
        guard baseline.count >= 7 else { return nil }
        let mean = baseline.reduce(0, +) / Double(baseline.count)
        let variance = baseline.reduce(0) { $0 + ($1 - mean) * ($1 - mean) } / Double(baseline.count)
        let sd = variance.squareRoot()
        guard sd > 0 else { return nil }
        return (latest - mean) / sd
    }

    /// Map a HRV z-score to a 0...100 estimate: z of -2 -> 0, 0 -> 50, +2 -> 100.
    /// Higher HRV vs baseline reads as better readiness.
    static func estimate(fromZScore z: Double) -> Int {
        let clamped = max(-2.0, min(2.0, z))
        return Int(((clamped + 2.0) / 4.0 * 100.0).rounded())
    }

    /// Convenience: estimate directly from a latest SDNN value and its baseline.
    /// Returns nil when the baseline is insufficient (caller shows "no estimate yet").
    static func estimate(latestSDNN: Double, baselineSDNN: [Double]) -> Int? {
        guard let z = zScore(latest: latestSDNN, baseline: baselineSDNN) else { return nil }
        return estimate(fromZScore: z)
    }
}
