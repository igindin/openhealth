import WidgetKit
import SwiftUI

/// Home-screen widget: the recovery ring + HRV, read from the shared App Group
/// snapshot the app publishes. Observational glance, never a diagnosis.
struct RecoveryWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "RecoveryWidget", provider: RecoveryProvider()) { entry in
            RecoveryWidgetView(snapshot: entry.snapshot)
                .containerBackground(Color(white: 0.05), for: .widget)
        }
        .configurationDisplayName("Recovery")
        .description("Your latest recovery and HRV at a glance.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}

struct RecoveryEntry: TimelineEntry {
    let date: Date
    let snapshot: WidgetSnapshot
}

struct RecoveryProvider: TimelineProvider {
    func placeholder(in context: Context) -> RecoveryEntry {
        RecoveryEntry(date: previewDate, snapshot: .placeholder)
    }

    func getSnapshot(in context: Context, completion: @escaping (RecoveryEntry) -> Void) {
        completion(RecoveryEntry(date: previewDate, snapshot: WidgetSnapshotStore().read() ?? .placeholder))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<RecoveryEntry>) -> Void) {
        let snapshot = WidgetSnapshotStore().read() ?? .placeholder
        let entry = RecoveryEntry(date: previewDate, snapshot: snapshot)
        // Recovery changes slowly; a next-hour refresh is plenty.
        completion(Timeline(entries: [entry], policy: .after(previewDate.addingTimeInterval(3600))))
    }

    // A fixed reference instant keeps the provider deterministic for previews.
    private var previewDate: Date { Date(timeIntervalSinceReferenceDate: 0) }
}

struct RecoveryWidgetView: View {
    let snapshot: WidgetSnapshot

    private var tint: Color {
        switch snapshot.recoveryZone {
        case .green: return Color(red: 0.20, green: 0.78, blue: 0.35)
        case .yellow: return Color(red: 0.91, green: 0.70, blue: 0.22)
        case .red: return Color(red: 0.90, green: 0.28, blue: 0.30)
        case .unknown: return Color(white: 0.5)
        }
    }

    var body: some View {
        HStack(spacing: 14) {
            ZStack {
                Circle().stroke(Color(white: 0.16), lineWidth: 8)
                Circle()
                    .trim(from: 0, to: min(max(Double(snapshot.recovery ?? 0) / 100, 0), 1))
                    .stroke(tint, style: StrokeStyle(lineWidth: 8, lineCap: .round))
                    .rotationEffect(.degrees(-90))
                Text(snapshot.recovery.map(String.init) ?? "—")
                    .font(.system(size: 26, weight: .bold, design: .rounded))
                    .monospacedDigit()
                    .foregroundStyle(tint)
            }
            .frame(width: 72, height: 72)

            VStack(alignment: .leading, spacing: 3) {
                Text("RECOVERY")
                    .font(.system(size: 10, weight: .semibold))
                    .tracking(0.8)
                    .foregroundStyle(Color(white: 0.6))
                if let hrv = snapshot.hrv {
                    Text(hrv)
                        .font(.system(size: 17, weight: .semibold, design: .rounded))
                        .foregroundStyle(.white)
                    Text("HRV")
                        .font(.system(size: 10))
                        .foregroundStyle(Color(white: 0.5))
                }
            }
            Spacer(minLength: 0)
        }
        .padding(14)
    }
}
