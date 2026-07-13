import SwiftUI

/// Goal-first onboarding in the pliability register: quiet dark canvas, serif
/// editorial headlines, one ink CTA per page, ghost escape hatches. Four pages:
/// brand moment → goal pick → Apple Health prime → how data flows (privacy).
/// Completion is stored in `oh.onboarded`; goals in `oh.goals`.
struct OnboardingView: View {
    @Environment(SyncCoordinator.self) private var sync
    @AppStorage("oh.onboarded") private var onboarded = false
    @AppStorage("oh.goals") private var storedGoals = ""

    // Screenshot automation: `-OHOnbPage N` opens a page directly.
    @State private var page = min(max(UserDefaults.standard.integer(forKey: "OHOnbPage"), 0), 3)
    @State private var goals: Set<String> = []

    private let pageCount = 4

    var body: some View {
        VStack(spacing: 0) {
            header
            TabView(selection: $page) {
                brandPage.tag(0)
                goalPage.tag(1)
                connectPage.tag(2)
                privacyPage.tag(3)
            }
            .tabViewStyle(.page(indexDisplayMode: .never))
            .animation(.easeInOut(duration: 0.3), value: page)
            footer
        }
        .background(Theme.background.ignoresSafeArea())
        .sensoryFeedback(.selection, trigger: goals)
        .sensoryFeedback(.impact(weight: .light), trigger: page)
    }

    // MARK: - Chrome

    private var header: some View {
        HStack(spacing: Theme.s3) {
            // Segmented progress: four quiet bars, filled up to the current page.
            HStack(spacing: Theme.s1 + 2) {
                ForEach(0..<pageCount, id: \.self) { i in
                    Capsule()
                        .fill(i <= page ? Theme.ink : Theme.hairlineStrong)
                        .frame(height: 3)
                        .animation(.easeOut(duration: 0.25), value: page)
                }
            }
            .frame(maxWidth: 160)
            Spacer()
            if page < pageCount - 1 {
                Button("Skip") { finish() }
                    .buttonStyle(GhostButtonStyle())
            }
        }
        .padding(.horizontal, Theme.s5)
        .padding(.top, Theme.s4)
        .padding(.bottom, Theme.s2)
    }

    private var footer: some View {
        VStack(spacing: Theme.s3) {
            switch page {
            case 0:
                Button("Get started") { page = 1 }
                    .buttonStyle(PrimaryButtonStyle())
            case 1:
                Button("Continue") { page = 2 }
                    .buttonStyle(PrimaryButtonStyle())
                    .disabled(goals.isEmpty)
                    .opacity(goals.isEmpty ? 0.4 : 1)
            case 2:
                Button(sync.authorized ? "Connected — continue" : "Allow Apple Health") {
                    if sync.authorized { page = 3 } else {
                        Task { await sync.requestAuthorization(); page = 3 }
                    }
                }
                .buttonStyle(PrimaryButtonStyle())
                Button("Not now") { page = 3 }
                    .buttonStyle(GhostButtonStyle())
            default:
                Button("Start") { finish() }
                    .buttonStyle(PrimaryButtonStyle())
            }
        }
        .padding(.horizontal, Theme.s5)
        .padding(.bottom, Theme.s5)
        .padding(.top, Theme.s2)
    }

    private func finish() {
        storedGoals = goals.sorted().joined(separator: ",")
        withAnimation(.easeInOut(duration: 0.35)) { onboarded = true }
    }

    // MARK: - Page 1 · Brand moment

    private var brandPage: some View {
        VStack(spacing: 0) {
            Spacer()
            RingGauge(progress: 0.76, centerValue: "76",
                      labelInside: "Recovery",
                      tint: Theme.zoneGreen, lineWidth: 13, size: 220)
                .padding(.bottom, Theme.s6)
            CapsLabel(text: "OpenHealth", size: 12, color: Theme.inkSoft)
                .padding(.bottom, Theme.s3)
            Text("Understand what\nmoves your recovery.")
                .font(Theme.display(32))
                .foregroundStyle(Theme.ink)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.bottom, Theme.s3)
            Text("Private by design. Your health data stays on your devices — analysis runs on your own computer.")
                .font(Theme.body(15))
                .foregroundStyle(Theme.inkSoft)
                .multilineTextAlignment(.center)
                .padding(.horizontal, Theme.s6)
            Spacer()
            Spacer().frame(height: Theme.s5)
        }
    }

    // MARK: - Page 2 · Goal pick

    private struct Goal: Identifiable {
        let id: String
        let title: String
        let caption: String
        let icon: String
        let metric: String
    }

    private let goalOptions: [Goal] = [
        Goal(id: "recovery", title: "Recovery & HRV",
             caption: "What helps and what hurts your baseline",
             icon: "waveform.path.ecg", metric: "hrv"),
        Goal(id: "sleep", title: "Sleep quality",
             caption: "Patterns behind good and rough nights",
             icon: "bed.double.fill", metric: "sleep"),
        Goal(id: "training", title: "Training load",
             caption: "Balancing effort against recovery",
             icon: "figure.run", metric: "strain"),
        Goal(id: "labs", title: "Long-term labs",
             caption: "Bloodwork trends over months and years",
             icon: "testtube.2", metric: "rhr"),
    ]

    private var goalPage: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("What do you want\nto understand?")
                .font(Theme.display(30))
                .foregroundStyle(Theme.ink)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.bottom, Theme.s2)
            Text("Pick what matters — the app leads with it.")
                .font(Theme.body(15))
                .foregroundStyle(Theme.inkSoft)
                .padding(.bottom, Theme.s5)
            VStack(spacing: Theme.s3) {
                ForEach(Array(goalOptions.enumerated()), id: \.element.id) { i, goal in
                    goalRow(goal).riseIn(i)
                }
            }
            Spacer()
        }
        .padding(.horizontal, Theme.s5)
        .padding(.top, Theme.s4)
    }

    private func goalRow(_ goal: Goal) -> some View {
        let selected = goals.contains(goal.id)
        let hue = Theme.metricHue(goal.metric)
        return Button {
            if selected { goals.remove(goal.id) } else { goals.insert(goal.id) }
        } label: {
            HStack(spacing: Theme.s3) {
                Image(systemName: goal.icon)
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(hue)
                    .frame(width: 40, height: 40)
                    .background(hue.opacity(0.14))
                    .clipShape(RoundedRectangle(cornerRadius: Theme.radiusSmall, style: .continuous))
                VStack(alignment: .leading, spacing: 2) {
                    Text(goal.title)
                        .font(Theme.body(16, weight: .semibold))
                        .foregroundStyle(Theme.ink)
                    Text(goal.caption)
                        .font(Theme.body(13))
                        .foregroundStyle(Theme.inkSoft)
                }
                Spacer()
                Image(systemName: selected ? "checkmark.circle.fill" : "circle")
                    .font(.system(size: 22))
                    .foregroundStyle(selected ? Theme.ink : Theme.inkDim)
            }
            .padding(Theme.s4)
            .background(selected ? Theme.surface : Theme.surfaceAlt.opacity(0.6))
            .clipShape(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: Theme.radius, style: .continuous)
                    .strokeBorder(selected ? Theme.ink.opacity(0.5) : .clear, lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
    }

    // MARK: - Page 3 · Apple Health prime

    private var connectPage: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: Theme.s3) {
                appBadge(systemName: "circle.hexagongrid.fill", tint: Theme.ink)
                Image(systemName: "arrow.right")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(Theme.inkDim)
                appBadge(systemName: "heart.fill", tint: Theme.zoneRed)
            }
            .padding(.bottom, Theme.s5)
            Text("Connect Apple Health")
                .font(Theme.display(30))
                .foregroundStyle(Theme.ink)
                .padding(.bottom, Theme.s2)
            Text("Read-only, on this device. You choose exactly which types to share on the next screen.")
                .font(Theme.body(15))
                .foregroundStyle(Theme.inkSoft)
                .padding(.bottom, Theme.s5)
            VStack(spacing: 0) {
                ForEach(Array(Self.syncRows.enumerated()), id: \.offset) { i, row in
                    if i > 0 { Divider().overlay(Theme.hairline) }
                    HStack(spacing: Theme.s3) {
                        Image(systemName: row.icon)
                            .font(.system(size: 15))
                            .foregroundStyle(Theme.metricHue(row.metric))
                            .frame(width: 26)
                        Text(row.label)
                            .font(Theme.body(15))
                            .foregroundStyle(Theme.ink)
                        Spacer()
                    }
                    .padding(.vertical, Theme.s3)
                    .riseIn(i)
                }
            }
            .padding(.horizontal, Theme.s4)
            .background(Theme.surface)
            .clipShape(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
            Spacer()
        }
        .padding(.horizontal, Theme.s5)
        .padding(.top, Theme.s4)
    }

    private static let syncRows: [(icon: String, label: String, metric: String)] = [
        ("waveform.path.ecg", "Heart rate variability", "hrv"),
        ("heart.fill", "Resting & walking heart rate", "rhr"),
        ("bed.double.fill", "Sleep stages & duration", "sleep"),
        ("figure.run", "Workouts", "strain"),
        ("flame.fill", "Steps & active energy", "weight"),
    ]

    private func appBadge(systemName: String, tint: Color) -> some View {
        Image(systemName: systemName)
            .font(.system(size: 24, weight: .medium))
            .foregroundStyle(tint)
            .frame(width: 56, height: 56)
            .background(Theme.surface)
            .clipShape(RoundedRectangle(cornerRadius: Theme.radiusSmall, style: .continuous))
            .shadow(color: .black.opacity(0.15), radius: 8, y: 2)
    }

    // MARK: - Page 4 · How data flows

    private var privacyPage: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Your data flows home,\nnot to a server.")
                .font(Theme.display(30))
                .foregroundStyle(Theme.ink)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.bottom, Theme.s2)
            Text("The phone writes encrypted files to your own iCloud Drive. Your computer reads them, runs the analysis, and sends a snapshot back. No accounts, no third parties.")
                .font(Theme.body(15))
                .foregroundStyle(Theme.inkSoft)
                .padding(.bottom, Theme.s6)
            VStack(spacing: Theme.s2) {
                flowNode(icon: "iphone", title: "This iPhone",
                         caption: "Apple Health, journal, context", index: 0)
                flowArrow(index: 1)
                flowNode(icon: "icloud", title: "Your iCloud Drive",
                         caption: "A folder only you can read", index: 2)
                flowArrow(index: 3)
                flowNode(icon: "desktopcomputer", title: "Your computer",
                         caption: "OpenHealth engine: trends, insights", index: 4)
            }
            Spacer()
        }
        .padding(.horizontal, Theme.s5)
        .padding(.top, Theme.s4)
    }

    private func flowNode(icon: String, title: String, caption: String, index: Int) -> some View {
        HStack(spacing: Theme.s3) {
            Image(systemName: icon)
                .font(.system(size: 18, weight: .medium))
                .foregroundStyle(Theme.ink)
                .frame(width: 40, height: 40)
                .background(Theme.surfaceAlt)
                .clipShape(RoundedRectangle(cornerRadius: Theme.radiusSmall, style: .continuous))
            VStack(alignment: .leading, spacing: 1) {
                Text(title)
                    .font(Theme.body(15, weight: .semibold))
                    .foregroundStyle(Theme.ink)
                Text(caption)
                    .font(Theme.body(13))
                    .foregroundStyle(Theme.inkSoft)
            }
            Spacer()
        }
        .padding(Theme.s3 + 2)
        .background(Theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
        .riseIn(index)
    }

    private func flowArrow(index: Int) -> some View {
        Image(systemName: "arrow.down")
            .font(.system(size: 13, weight: .semibold))
            .foregroundStyle(Theme.inkDim)
            .riseIn(index)
    }
}

#Preview {
    OnboardingView()
        .environment(SyncCoordinator())
}
