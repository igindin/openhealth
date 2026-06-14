import SwiftUI

@main
struct OpenHealthApp: App {
    @State private var store = HealthStore()
    @State private var journal = JournalStore()
    @State private var sync = SyncCoordinator()

    var body: some Scene {
        WindowGroup {
            RootTabView()
                .environment(store)
                .environment(journal)
                .environment(sync)
                .tint(Theme.accent)
                .preferredColorScheme(.dark)   // ships the dark Widget-Board surface
        }
    }
}
