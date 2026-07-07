import SwiftUI

struct RootTabView: View {
    @Environment(HealthStore.self) private var store
    @State private var selection = Self.initialTab

    // Debug affordance: `simctl launch ... org.openhealth.app -OHTab 2` opens a
    // given tab directly (screenshot automation); no effect in normal launches.
    private static var initialTab: Int {
        guard let raw = UserDefaults.standard.string(forKey: "OHTab"), let n = Int(raw) else { return 0 }
        return min(max(n, 0), 4)
    }

    var body: some View {
        TabView(selection: $selection) {
            // Journal is the home tab: the mobile product's core daily job.
            JournalView()
                .tabItem { Label("Journal", systemImage: "square.and.pencil") }
                .tag(0)
            TodayView()
                .tabItem { Label("Today", systemImage: "sun.max") }
                .tag(1)
            TrendsView()
                .tabItem { Label("Trends", systemImage: "chart.xyaxis.line") }
                .tag(2)
            InsightsView()
                .tabItem { Label("Insights", systemImage: "lightbulb") }
                .tag(3)
            SyncView()
                .tabItem { Label("Sync", systemImage: "arrow.triangle.2.circlepath") }
                .tag(4)
        }
        .task { await store.refresh() }
    }
}

#Preview {
    RootTabView()
        .environment(HealthStore())
        .environment(JournalStore())
        .environment(SyncCoordinator())
}
