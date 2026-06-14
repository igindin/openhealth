import SwiftUI

struct RootTabView: View {
    @Environment(HealthStore.self) private var store

    var body: some View {
        TabView {
            // Journal is the home tab: the mobile product's core daily job.
            JournalView()
                .tabItem { Label("Journal", systemImage: "square.and.pencil") }
            TodayView()
                .tabItem { Label("Today", systemImage: "sun.max") }
            TrendsView()
                .tabItem { Label("Trends", systemImage: "chart.xyaxis.line") }
            InsightsView()
                .tabItem { Label("Insights", systemImage: "lightbulb") }
            SyncView()
                .tabItem { Label("Sync", systemImage: "arrow.triangle.2.circlepath") }
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
