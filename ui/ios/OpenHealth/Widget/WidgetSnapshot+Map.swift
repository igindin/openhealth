import Foundation

/// App-only mapping from the full `HealthSnapshot` to the compact widget shape.
/// Lives outside the shared file so the widget extension never links the model
/// stack. `leadingNumber` is the shared display-string parser (Components.swift).
extension WidgetSnapshot {
    init(from snapshot: HealthSnapshot) {
        let recoveryValue = snapshot.measurements.first { $0.metric == "recovery" }?.value
        let score = recoveryValue.flatMap { leadingNumber($0) }.map { Int($0) }
        let zone: Zone
        if let score {
            zone = score >= 67 ? .green : (score >= 34 ? .yellow : .red)
        } else {
            zone = .unknown
        }
        let hrv = snapshot.measurements.first { $0.metric == "hrv" }?.value
        self.init(recovery: score, recoveryZone: zone, hrv: hrv, actions: [], updatedAt: Date())
    }
}
