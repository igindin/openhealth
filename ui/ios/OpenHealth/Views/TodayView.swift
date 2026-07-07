import SwiftUI

/// Today: the recovery/strain summary and "what you're ready for". A glanceable
/// readiness screen in the dark Widget-Board style, with the recovery ring
/// color-coded green/yellow/red (web-dashboard language). "Doctor Context" gives
/// a mood read on recovery. Observational only — never a diagnosis. The daily
/// journal lives on its own (first) tab.
struct TodayView: View {
    @Environment(HealthStore.self) private var store

    private var greeting: String {
        let hour = Calendar.current.component(.hour, from: Date())
        switch hour {
        case 5..<12: return "Good morning"
        case 12..<18: return "Good afternoon"
        default: return "Good evening"
        }
    }

    private var todayLabel: String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "EEEE, MMM d"
        return f.string(from: Date())
    }

    private func measurement(_ metric: String) -> Measurement? {
        store.snapshot.measurements.first { $0.metric == metric }
    }
    private var recovery: Measurement? { measurement("recovery") }

    /// Recovery as 0...100 (parsed from the display value).
    private var recoveryScore: Double? {
        recovery.flatMap { leadingNumber($0.value) }
    }

    // Board metrics under the ring, in display order.
    private var boardMetrics: [Measurement] {
        ["strain", "hrv", "resting_hr", "sleep"].compactMap { measurement($0) }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Theme.s5) {
                    header

                    ForEach(store.snapshot.alerts) { alert in
                        SafetyBanner(alert: alert)
                    }

                    if let score = recoveryScore, let rec = recovery {
                        recoveryCard(score: score, measurement: rec)
                        doctorContext(score: score)
                        boardCard
                        readinessCard(score: score)
                    } else {
                        Text("No recovery data yet. Connect a source on desktop.")
                            .font(.system(size: 14))
                            .foregroundStyle(Theme.inkSoft)
                    }

                    ForEach(store.snapshot.panels.filter { !$0.abnormal.isEmpty }) { panel in
                        reviewPrompt(panel)
                    }

                    Text("A reflection helper, not a doctor. Anything worrying goes to a specialist.")
                        .font(.system(size: 11))
                        .foregroundStyle(Theme.inkDim)
                }
                .padding(Theme.s4)
            }
            .background(Theme.background.ignoresSafeArea())
            .navigationBarHidden(true)
        }
    }

    // MARK: - Header

    private var header: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(greeting)
                        .font(.system(size: 18, weight: .regular, design: .serif))
                        .foregroundStyle(Theme.inkSoft)
                    Text(store.snapshot.greetingName)
                        .font(.system(size: 34, weight: .bold, design: .serif))
                        .foregroundStyle(Theme.ink)
                }
                Spacer()
            }
            Text(todayLabel)
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(Theme.inkSoft)
                .padding(.top, Theme.s1)
        }
        .padding(.top, Theme.s2)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - Recovery hero

    private func recoveryCard(score: Double, measurement: Measurement) -> some View {
        let color = Theme.recoveryColor(score)
        return Card {
            VStack(spacing: Theme.s3) {
                Text("RECOVERY")
                    .font(.system(size: 11, weight: .semibold))
                    .tracking(1.0)
                    .foregroundStyle(Theme.inkSoft)
                    .frame(maxWidth: .infinity, alignment: .leading)
                ZStack {
                    Circle()
                        .stroke(Theme.hairlineStrong, lineWidth: 14)
                    Circle()
                        .trim(from: 0, to: min(max(score / 100, 0), 1))
                        .stroke(color, style: StrokeStyle(lineWidth: 14, lineCap: .round))
                        .rotationEffect(.degrees(-90))
                    VStack(spacing: 0) {
                        Text("\(Int(score))")
                            .font(.system(size: 64, weight: .bold, design: .rounded))
                            .monospacedDigit()
                            .foregroundStyle(color)
                        Text(measurement.caption ?? "recovery")
                            .font(.system(size: 11, weight: .medium))
                            .tracking(0.8)
                            .foregroundStyle(Theme.inkSoft)
                    }
                }
                .frame(width: 190, height: 190)
                .padding(.vertical, Theme.s2)
                Text(Theme.recoveryHeadline(score))
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(Theme.ink)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    // MARK: - Doctor Context

    private func doctorContext(score: Double) -> some View {
        let mood = Theme.recoveryMood(score)
        let tint = Theme.recoveryColor(score)
        return Card {
            HStack(spacing: Theme.s3) {
                Image(systemName: mood.symbol)
                    .font(.system(size: 22, weight: .semibold))
                    .foregroundStyle(tint)
                    .frame(width: 48, height: 48)
                    .background(tint.opacity(0.14))
                    .clipShape(Circle())
                VStack(alignment: .leading, spacing: 2) {
                    Text("Doctor Context")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(Theme.ink)
                    Text(mood.line)
                        .font(.system(size: 13))
                        .foregroundStyle(Theme.inkSoft)
                }
                Spacer()
            }
        }
    }

    // MARK: - Board

    private var boardCard: some View {
        LazyVGrid(columns: [GridItem(.flexible(), spacing: Theme.s3),
                            GridItem(.flexible(), spacing: Theme.s3)],
                  spacing: Theme.s3) {
            ForEach(boardMetrics) { m in
                MetricTile(measurement: m)
            }
        }
    }

    // MARK: - Readiness

    private func readinessCard(score: Double) -> some View {
        Card {
            VStack(alignment: .leading, spacing: Theme.s3) {
                Text("READY FOR TODAY")
                    .font(.system(size: 11, weight: .semibold))
                    .tracking(1.0)
                    .foregroundStyle(Theme.inkSoft)
                Text(readinessText(score))
                    .font(.system(size: 15))
                    .foregroundStyle(Theme.ink)
                HStack(alignment: .top, spacing: Theme.s3) {
                    Text("DO TODAY")
                        .font(.system(size: 10, weight: .semibold, design: .monospaced))
                        .tracking(0.6)
                        .padding(.horizontal, Theme.s2)
                        .padding(.vertical, 6)
                        .background(Theme.recoveryColor(score))
                        .foregroundStyle(Theme.background)
                        .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
                    VStack(alignment: .leading, spacing: 3) {
                        Text(actionTitle(score))
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundStyle(Theme.ink)
                        Text(actionWhy(score))
                            .font(.system(size: 12))
                            .foregroundStyle(Theme.inkSoft)
                    }
                }
                .padding(Theme.s3)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.surfaceAlt)
                .clipShape(RoundedRectangle(cornerRadius: Theme.radiusSmall, style: .continuous))
            }
        }
    }

    private func readinessText(_ score: Double) -> String {
        let zone = Theme.recoveryHeadline(score).lowercased()
        if score >= 67 {
            return "Recovery \(Int(score))% (\(zone)). Your body is primed — a harder session or a demanding day fits well today."
        }
        if score >= 34 {
            return "Recovery \(Int(score))% (\(zone)). A moderate day suits you — keep intensity in check and protect tonight's sleep."
        }
        return "Recovery \(Int(score))% (\(zone)). Treat today as easy — light movement, an earlier night, and less load."
    }
    private func actionTitle(_ score: Double) -> String {
        if score >= 67 { return "Use the window for your hardest task" }
        if score >= 34 { return "Pick one helpful action, skip the rest" }
        return "Go to bed 30 minutes earlier"
    }
    private func actionWhy(_ score: Double) -> String {
        if score >= 67 { return "High recovery is a good time to spend effort." }
        if score >= 34 { return "Middle ground rewards focus over volume." }
        return "Sleep is the strongest lever on recovery." }

    // MARK: - Lab review prompt (kept from the records layer)

    private func reviewPrompt(_ panel: LabPanel) -> some View {
        NavigationLink {
            LabPanelDetailView(panel: panel)
        } label: {
            Card {
                VStack(alignment: .leading, spacing: Theme.s2) {
                    HStack {
                        Image(systemName: "info.circle").foregroundStyle(Theme.warn)
                        Text("Some markers to review")
                            .font(.system(size: 15, weight: .semibold))
                            .foregroundStyle(Theme.ink)
                        Spacer()
                        Image(systemName: "chevron.right").foregroundStyle(Theme.inkSoft)
                    }
                    Text(panel.abnormal.map { "\($0.displayName) \($0.flag.label)" }.joined(separator: ", "))
                        .font(.system(size: 13)).foregroundStyle(Theme.inkSoft)
                    Text("A prompt to review with a clinician, not a diagnosis.")
                        .font(.system(size: 12)).foregroundStyle(Theme.inkDim)
                }
            }
        }
        .buttonStyle(.plain)
    }
}

/// Dark board tile: a luminous number on a recessed surface.
private struct MetricTile: View {
    let measurement: Measurement

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.s1) {
            Text(measurement.title.uppercased())
                .font(.system(size: 10, weight: .semibold, design: .monospaced))
                .tracking(0.6)
                .foregroundStyle(Theme.inkSoft)
            Spacer(minLength: Theme.s2)
            Text(measurement.value)
                .font(.system(size: 26, weight: .semibold, design: .rounded))
                .monospacedDigit()
                .foregroundStyle(Theme.ink)
                .lineLimit(1)
                .minimumScaleFactor(0.6)
            if let caption = measurement.caption {
                Text(caption).font(.system(size: 11)).foregroundStyle(Theme.inkDim)
            }
        }
        .padding(Theme.s3 + 1)
        .frame(maxWidth: .infinity, minHeight: 96, alignment: .leading)
        .background(Theme.surfaceAlt)
        .overlay(
            RoundedRectangle(cornerRadius: Theme.radiusSmall, style: .continuous)
                .stroke(Theme.hairline, lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: Theme.radiusSmall, style: .continuous))
    }
}

#Preview {
    TodayView().environment(HealthStore())
}
