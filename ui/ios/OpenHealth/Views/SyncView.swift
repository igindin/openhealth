import SwiftUI

/// Operational surface for HealthKit access and bridge sync. Self-host tool, so
/// this is intentionally explicit: grant Apple Health, sync to your iCloud Drive,
/// see where the files land for the local OpenHealth engine to read.
struct SyncView: View {
    @Environment(SyncCoordinator.self) private var sync
    @Environment(HealthStore.self) private var store

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Theme.s4) {
                    appleHealthCard
                    syncCard
                    whatSyncsCard
                    Text("Health data is written to your iCloud Drive (OpenHealth folder) and read by the local OpenHealth app on your computer. Nothing is sent to any server.")
                        .font(.system(size: 12))
                        .foregroundStyle(Theme.inkDim)
                }
                .padding(Theme.s4)
            }
            .background(Theme.background.ignoresSafeArea())
            .navigationTitle("Sync")
        }
    }

    // MARK: - Apple Health

    private var appleHealthCard: some View {
        Card {
            VStack(alignment: .leading, spacing: Theme.s3) {
                cardHeader("APPLE HEALTH")
                if sync.authorized {
                    // Granted: quiet "connected" state, secondary re-check action.
                    HStack(spacing: Theme.s2) {
                        Image(systemName: "checkmark.circle.fill")
                            .font(.system(size: 18))
                            .foregroundStyle(Theme.zoneGreen)
                        Text("Connected")
                            .font(.system(size: 15, weight: .semibold))
                            .foregroundStyle(Theme.ink)
                        Spacer()
                        Button {
                            Task { await sync.requestAuthorization(); await store.refresh() }
                        } label: {
                            Text("Re-check")
                                .font(.system(size: 14, weight: .medium))
                                .foregroundStyle(Theme.accent)
                        }
                        .buttonStyle(.plain)
                    }
                } else {
                    Text("Allow on-device read access to your Health data.")
                        .font(.system(size: 14))
                        .foregroundStyle(Theme.ink)
                    Button {
                        Task { await sync.requestAuthorization(); await store.refresh() }
                    } label: {
                        Text("Allow Apple Health").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(Theme.accent)
                    .disabled(!sync.healthAvailable)
                }
            }
        }
    }

    // MARK: - Sync

    private var syncCard: some View {
        @Bindable var sync = sync
        return Card {
            VStack(alignment: .leading, spacing: Theme.s3) {
                cardHeader("SYNC")
                Picker("Transport", selection: $sync.transportKind) {
                    ForEach(SyncCoordinator.TransportKind.allCases) { kind in
                        Text(kind.label).tag(kind)
                    }
                }
                .pickerStyle(.segmented)
                HStack(alignment: .top, spacing: Theme.s3) {
                    Image(systemName: statusIcon)
                        .font(.system(size: 18))
                        .foregroundStyle(statusIsError ? Theme.warn
                                         : (sync.status == .syncing ? Theme.accent : Theme.inkSoft))
                        .frame(width: 22)
                        .symbolEffect(.pulse, isActive: sync.status == .syncing)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(statusLine)
                            .font(.system(size: 15, weight: .medium))
                            .foregroundStyle(statusIsError ? Theme.warn : Theme.ink)
                        Text("Phone → \(sync.transportKind.label), one direction.")
                            .font(.system(size: 12))
                            .foregroundStyle(Theme.inkDim)
                    }
                    Spacer()
                }
                Button {
                    Task { await sync.runSync(); await store.refresh() }
                } label: {
                    HStack(spacing: Theme.s2) {
                        if sync.status == .syncing { ProgressView().tint(Theme.background) }
                        Text(sync.status == .syncing ? "Syncing…" : "Sync now")
                    }
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .tint(Theme.accent)
                .disabled(sync.status == .syncing || !sync.healthAvailable || !sync.authorized)
                if !sync.authorized {
                    Text("Allow Apple Health first.")
                        .font(.system(size: 12)).foregroundStyle(Theme.inkDim)
                }
            }
        }
    }

    // MARK: - What syncs

    private var whatSyncsCard: some View {
        Card {
            VStack(alignment: .leading, spacing: Theme.s3) {
                cardHeader("WHAT SYNCS")
                ForEach(Array(Self.syncedTypes.enumerated()), id: \.offset) { idx, item in
                    if idx > 0 { Divider().overlay(Theme.hairline) }
                    HStack(spacing: Theme.s3) {
                        Image(systemName: item.icon)
                            .font(.system(size: 15))
                            .foregroundStyle(Theme.inkSoft)
                            .frame(width: 24)
                        Text(item.label)
                            .font(.system(size: 15))
                            .foregroundStyle(Theme.ink)
                        Spacer()
                    }
                    .padding(.vertical, 2)
                }
            }
        }
    }

    private static let syncedTypes: [(icon: String, label: String)] = [
        ("waveform.path.ecg", "Heart rate variability"),
        ("heart.fill", "Resting & walking heart rate"),
        ("bed.double.fill", "Sleep stages & duration"),
        ("figure.run", "Workouts"),
        ("flame.fill", "Steps & active energy"),
    ]

    // MARK: - Helpers

    private func cardHeader(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 11, weight: .semibold)).tracking(1.0)
            .foregroundStyle(Theme.inkSoft)
    }

    private var statusIsError: Bool {
        if case .failed = sync.status { return true }
        if case .healthUnavailable = sync.status { return true }
        return false
    }

    private var statusIcon: String {
        switch sync.status {
        case .idle: return "clock"
        case .syncing: return "arrow.triangle.2.circlepath"
        case .synced: return "checkmark.icloud.fill"
        case .failed: return "exclamationmark.triangle.fill"
        case .healthUnavailable: return "xmark.icloud"
        }
    }

    private var statusLine: String {
        switch sync.status {
        case .idle: return "Not synced yet."
        case .syncing: return "Reading Apple Health and writing to iCloud…"
        case .synced(let date): return "Last synced \(Self.formatter.string(from: date))"
        case .failed(let message): return message
        case .healthUnavailable: return "Apple Health is not available on this device."
        }
    }

    private static let formatter: DateFormatter = {
        let f = DateFormatter()
        f.dateStyle = .medium
        f.timeStyle = .short
        return f
    }()
}

#Preview {
    SyncView()
        .environment(SyncCoordinator())
        .environment(HealthStore())
}
