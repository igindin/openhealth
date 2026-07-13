import SwiftUI

/// Today: the recovery/strain summary and "what you're ready for". A glanceable
/// readiness board — animated recovery ring, metric tiles in their own hues,
/// one plain-language action. Observational only — never a diagnosis. The daily
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
                        recoveryCard(score: score, measurement: rec).riseIn(0)
                        doctorContext(score: score).riseIn(1)
                        boardCard.riseIn(2)
                        readinessCard(score: score).riseIn(3)
                    } else {
                        Text("No recovery data yet. Connect a source on desktop.")
                            .font(Theme.body(14))
                            .foregroundStyle(Theme.inkSoft)
                    }

                    ForEach(store.snapshot.panels.filter { !$0.abnormal.isEmpty }) { panel in
                        reviewPrompt(panel)
                    }

                    Text("A reflection helper, not a doctor. Anything worrying goes to a specialist.")
                        .font(Theme.body(11))
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
            Text(greeting)
                .font(Theme.display(18, weight: .regular))
                .foregroundStyle(Theme.inkSoft)
            Text(store.snapshot.greetingName)
                .font(Theme.display(34, weight: .bold))
                .foregroundStyle(Theme.ink)
            CapsLabel(text: todayLabel, size: 12)
                .padding(.top, Theme.s1)
        }
        .padding(.top, Theme.s2)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - Recovery hero

    // WHOOP register: the ring lives directly on the ground (no card box),
    // followed by a left-aligned headline + explaining paragraph.
    private func recoveryCard(score: Double, measurement: Measurement) -> some View {
        let color = Theme.recoveryColor(score)
        return VStack(alignment: .leading, spacing: Theme.s3) {
            RingGauge(
                progress: score / 100,
                centerValue: "\(Int(score))",
                labelInside: "Recovery",
                suffix: "%",
                tint: color,
                lineWidth: 14,
                size: 250
            )
            .padding(.vertical, Theme.s3)
            .frame(maxWidth: .infinity)
            Text(Theme.recoveryHeadline(score))
                .font(Theme.body(16, weight: .semibold))
                .foregroundStyle(Theme.ink)
            Text(readinessText(score))
                .font(Theme.body(13))
                .foregroundStyle(Theme.inkSoft)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
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
                        .font(Theme.body(14, weight: .semibold))
                        .foregroundStyle(Theme.ink)
                    Text(mood.line)
                        .font(Theme.body(13))
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
            ForEach(Array(boardMetrics.enumerated()), id: \.element.id) { i, m in
                MetricTile(measurement: m).riseIn(i + 2)
            }
        }
    }

    // MARK: - Readiness

    private func readinessCard(score: Double) -> some View {
        Card {
            VStack(alignment: .leading, spacing: Theme.s3) {
                CapsLabel(text: "Ready for today")
                HStack(alignment: .top, spacing: Theme.s3) {
                    CapsLabel(text: "Do today", size: 10, color: Theme.onAction)
                        .padding(.horizontal, Theme.s2)
                        .padding(.vertical, 6)
                        .background(Theme.recoveryColor(score))
                        .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
                    VStack(alignment: .leading, spacing: 3) {
                        Text(actionTitle(score))
                            .font(Theme.body(14, weight: .semibold))
                            .foregroundStyle(Theme.ink)
                        Text(actionWhy(score))
                            .font(Theme.body(12))
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

    private func readinessOneLiner(_ score: Double) -> String {
        if score >= 67 { return "Your HRV and resting HR are in a good range." }
        if score >= 34 { return "Signals are mixed — a moderate day fits." }
        return "Signals point to rest today."
    }

    private func readinessText(_ score: Double) -> String {
        if score >= 67 {
            return "Your signals are in a good range. A harder session or a demanding day fits well today."
        }
        if score >= 34 {
            return "Signals are mixed. A moderate day suits you — keep intensity in check and protect tonight's sleep."
        }
        return "Signals point to rest. Treat today as easy — light movement, an earlier night, and less load."
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
                            .font(Theme.body(15, weight: .semibold))
                            .foregroundStyle(Theme.ink)
                        Spacer()
                        Image(systemName: "chevron.right").foregroundStyle(Theme.inkSoft)
                    }
                    Text(panel.abnormal.map { "\($0.displayName) \($0.flag.label)" }.joined(separator: ", "))
                        .font(Theme.body(13)).foregroundStyle(Theme.inkSoft)
                    Text("A prompt to review with a clinician, not a diagnosis.")
                        .font(Theme.body(12)).foregroundStyle(Theme.inkDim)
                }
            }
        }
        .buttonStyle(.plain)
    }
}

/// Board tile: a compressed instrument numeral with the metric's own hue tick.
private struct MetricTile: View {
    @Environment(\.colorScheme) private var scheme
    let measurement: Measurement

    var body: some View {
        let hue = Theme.metricHue(measurement.metric)
        VStack(alignment: .leading, spacing: Theme.s1) {
            HStack(spacing: Theme.s2 - 2) {
                Image(systemName: Self.icon(for: measurement.metric))
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(hue)
                Text(measurement.title)
                    .font(Theme.body(13, weight: .medium))
                    .foregroundStyle(Theme.inkSoft)
            }
            Spacer(minLength: Theme.s2)
            Text(measurement.value)
                .font(Theme.numeral(28))
                .foregroundStyle(Theme.ink)
                .lineLimit(1)
                .minimumScaleFactor(0.6)
            if let caption = measurement.caption {
                Text(caption).font(Theme.body(11)).foregroundStyle(Theme.inkDim)
            }
        }
        .padding(Theme.s4)
        .frame(maxWidth: .infinity, minHeight: 100, alignment: .leading)
        .background(Theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
        .shadow(color: .black.opacity(scheme == .dark ? 0 : 0.05),
                radius: 10, x: 0, y: 3)
    }

    private static func icon(for metric: String) -> String {
        switch metric {
        case "hrv": return "waveform.path.ecg"
        case "resting_hr": return "heart.fill"
        case "sleep": return "bed.double.fill"
        case "strain": return "flame.fill"
        default: return "circle.fill"
        }
    }
}

#Preview {
    TodayView().environment(HealthStore())
}
