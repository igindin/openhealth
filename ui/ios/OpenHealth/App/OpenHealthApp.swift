import SwiftUI

@main
struct OpenHealthApp: App {
    @State private var store = HealthStore()
    @State private var journal = JournalStore()
    @State private var sync = SyncCoordinator()
    @AppStorage("oh.onboarded") private var onboarded = false

    // Screenshot automation: `-OHTheme dark|light` forces a scheme; unset = system.
    private var forcedScheme: ColorScheme? {
        switch UserDefaults.standard.string(forKey: "OHTheme") {
        case "dark": return .dark
        case "light": return .light
        default: return nil
        }
    }

    var body: some Scene {
        WindowGroup {
            Group {
                if onboarded {
                    RootTabView()
                        .transition(.opacity)
                } else {
                    OnboardingView()
                        .transition(.opacity)
                }
            }
            .environment(store)
            .environment(journal)
            .environment(sync)
            .tint(Theme.ink)
            .preferredColorScheme(forcedScheme)
        }
    }
}
