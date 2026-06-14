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

    private var appleHealthCard: some View {
        Card {
            VStack(alignment: .leading, spacing: Theme.s3) {
                Text("APPLE HEALTH")
                    .font(.system(size: 11, weight: .semibold)).tracking(1.0)
                    .foregroundStyle(Theme.inkSoft)
                Text(sync.authorized
                     ? "Access granted. New samples sync on demand."
                     : "Allow on-device read access to your Health data.")
                    .font(.system(size: 14))
                    .foregroundStyle(Theme.ink)
                Button {
                    Task { await sync.requestAuthorization(); await store.refresh() }
                } label: {
                    Text(sync.authorized ? "Re-check access" : "Allow Apple Health")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .tint(Theme.accent)
                .disabled(!sync.healthAvailable)
            }
        }
    }

    private var syncCard: some View {
        Card {
            VStack(alignment: .leading, spacing: Theme.s3) {
                Text("SYNC")
                    .font(.system(size: 11, weight: .semibold)).tracking(1.0)
                    .foregroundStyle(Theme.inkSoft)
                Text(statusLine)
                    .font(.system(size: 14))
                    .foregroundStyle(statusIsError ? Theme.warn : Theme.ink)
                Button {
                    Task { await sync.runSync(); await store.refresh() }
                } label: {
                    Text(sync.status == .syncing ? "Syncing…" : "Sync now")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .tint(Theme.accent)
                .disabled(sync.status == .syncing || !sync.healthAvailable)
            }
        }
    }

    private var statusIsError: Bool {
        if case .failed = sync.status { return true }
        if case .healthUnavailable = sync.status { return true }
        return false
    }

    private var statusLine: String {
        switch sync.status {
        case .idle: return "Not synced yet."
        case .syncing: return "Reading Apple Health and writing to iCloud…"
        case .synced(let date): return "Last synced \(Self.formatter.string(from: date))."
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
