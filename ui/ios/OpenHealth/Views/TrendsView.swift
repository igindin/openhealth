import SwiftUI
import Charts

struct TrendsView: View {
    @Environment(HealthStore.self) private var store
    @State private var selectedMetric: String?

    private var trend: Trend? {
        store.snapshot.trends.first { $0.metric == selectedMetric } ?? store.snapshot.trends.first
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Theme.s4) {
                    if !store.snapshot.trends.isEmpty {
                        Picker("Metric", selection: Binding(
                            get: { trend?.metric ?? "" },
                            set: { selectedMetric = $0 }
                        )) {
                            ForEach(store.snapshot.trends) { t in
                                Text(t.title).tag(t.metric)
                            }
                        }
                        .pickerStyle(.segmented)
                    }

                    if let t = trend {
                        metricCard(t).riseIn(0)
                    } else {
                        Text("No trends yet.")
                            .font(Theme.body(14))
                            .foregroundStyle(Theme.inkSoft)
                    }

                    if !store.snapshot.correlations.isEmpty {
                        correlationsCard.riseIn(1)
                    }
                }
                .padding(Theme.s4)
            }
            .background(Theme.background)
            .navigationTitle("Trends")
        }
    }

    // MARK: - Metric card (stat header + chart + readout)

    private func metricCard(_ t: Trend) -> some View {
        Card {
            VStack(alignment: .leading, spacing: Theme.s4) {
                statHeader(t)
                chart(t)
                    .frame(height: 200)
                Text(readout(t))
                    .font(Theme.body(13)).foregroundStyle(Theme.inkSoft)
            }
        }
    }

    /// A number-forward header: the latest value in the instrument voice, its
    /// unit, and a calm delta chip versus the period's typical (mean).
    /// Observational — direction only, no good/bad grading.
    @ViewBuilder
    private func statHeader(_ t: Trend) -> some View {
        let values = t.points.map(\.value)
        let last = values.last
        HStack(alignment: .firstTextBaseline, spacing: Theme.s2) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: Theme.s2 - 2) {
                    Circle().fill(Theme.metricHue(t.metric)).frame(width: 6, height: 6)
                    CapsLabel(text: t.title)
                }
                HStack(alignment: .firstTextBaseline, spacing: Theme.s1) {
                    Text(last.map(trim) ?? "—")
                        .font(Theme.numeral(46))
                        .foregroundStyle(Theme.ink)
                    Text(t.unit)
                        .font(Theme.body(15, weight: .medium))
                        .foregroundStyle(Theme.inkSoft)
                }
            }
            Spacer()
            deltaChip(values)
        }
    }

    /// Signed change of the latest point vs the period's typical (mean of prior
    /// points), as a quiet chip. Observational — direction only, no good/bad grade.
    @ViewBuilder
    private func deltaChip(_ values: [Double]) -> some View {
        if values.count >= 2, let last = values.last {
            let mean = values.dropLast().reduce(0, +) / Double(values.count - 1)
            let delta = last - mean
            let up = delta >= 0
            HStack(spacing: 4) {
                Image(systemName: up ? "arrow.up.right" : "arrow.down.right")
                    .font(.system(size: 11, weight: .bold))
                Text("\(up ? "+" : "−")\(trim(abs(delta)))")
                    .font(Theme.body(13, weight: .semibold))
                    .monospacedDigit()
            }
            .foregroundStyle(Theme.inkSoft)
            .padding(.horizontal, Theme.s2 + 2)
            .padding(.vertical, 6)
            .background(Theme.surfaceAlt)
            .clipShape(Capsule())
        }
    }

    private var correlationsCard: some View {
        Card {
            VStack(alignment: .leading, spacing: Theme.s3) {
                CapsLabel(text: "What affects you")
                ForEach(store.snapshot.correlations) { c in
                    HStack(spacing: Theme.s2) {
                        Image(systemName: c.dir == "up" ? "arrow.up.right" : "arrow.down.right")
                            .font(.system(size: 11, weight: .bold))
                            .foregroundStyle(c.dir == "up" ? Theme.zoneGreen : Theme.warn)
                        Text(c.label).font(Theme.body(14)).foregroundStyle(Theme.ink)
                        Spacer()
                        if let d = c.delta {
                            Text("\(d > 0 ? "+" : "")\(d)")
                                .font(Theme.body(13, weight: .semibold))
                                .monospacedDigit()
                                .foregroundStyle(Theme.inkSoft)
                        }
                        Text(c.grade)
                            .font(Theme.label(10))
                            .tracking(0.5)
                            .foregroundStyle(Theme.inkDim)
                    }
                }
                Text("Behaviour ↔ recovery links from your journal. Association, not cause.")
                    .font(Theme.body(11)).foregroundStyle(Theme.inkDim)
            }
        }
    }

    // MARK: - Chart

    @ViewBuilder
    private func chart(_ t: Trend) -> some View {
        let hue = Theme.metricHue(t.metric)
        let lastPoint = t.points.last
        Chart {
            if let lo = t.referenceLow, let hi = t.referenceHigh {
                RectangleMark(yStart: .value("low", lo), yEnd: .value("high", hi))
                    .foregroundStyle(hue.opacity(0.07))
            }
            ForEach(t.points) { p in
                AreaMark(x: .value("Day", p.date), y: .value(t.unit, p.value))
                    .foregroundStyle(
                        LinearGradient(
                            colors: [hue.opacity(0.22), hue.opacity(0.0)],
                            startPoint: .top, endPoint: .bottom
                        )
                    )
                    .interpolationMethod(.catmullRom)
                LineMark(x: .value("Day", p.date), y: .value(t.unit, p.value))
                    .foregroundStyle(hue)
                    .lineStyle(StrokeStyle(lineWidth: 2.5, lineCap: .round))
                    .interpolationMethod(.catmullRom)
            }
            if let lp = lastPoint {
                PointMark(x: .value("Day", lp.date), y: .value(t.unit, lp.value))
                    .foregroundStyle(Theme.background)
                    .symbolSize(160)
                PointMark(x: .value("Day", lp.date), y: .value(t.unit, lp.value))
                    .foregroundStyle(hue)
                    .symbolSize(80)
                    .annotation(position: .top, spacing: 6) {
                        Text("\(trim(lp.value)) \(t.unit)")
                            .font(Theme.body(11, weight: .semibold))
                            .monospacedDigit()
                            .foregroundStyle(Theme.ink)
                            .padding(.horizontal, 6).padding(.vertical, 3)
                            .background(Theme.surfaceAlt)
                            .clipShape(Capsule())
                    }
            }
        }
        .chartYScale(domain: yDomain(t))
        .chartPlotStyle { $0.clipped() }
        .chartXAxis {
            AxisMarks { _ in
                AxisGridLine().foregroundStyle(Theme.hairline)
                AxisValueLabel().foregroundStyle(Theme.inkSoft)
            }
        }
        .chartYAxis {
            AxisMarks { _ in
                AxisGridLine().foregroundStyle(Theme.hairline)
                AxisValueLabel().foregroundStyle(Theme.inkSoft)
            }
        }
    }

    private func yDomain(_ t: Trend) -> ClosedRange<Double> {
        let values = t.points.map(\.value) + [t.referenceLow, t.referenceHigh].compactMap { $0 }
        let lo = (values.min() ?? 0) * 0.9
        let hi = (values.max() ?? 1) * 1.1
        return lo...max(hi, lo + 1)
    }

    private func readout(_ t: Trend) -> String {
        guard let last = t.points.last?.value else { return "" }
        let inRange: Bool = {
            if let lo = t.referenceLow, last < lo { return false }
            if let hi = t.referenceHigh, last > hi { return false }
            return true
        }()
        let state = inRange ? "within your typical range" : "outside your typical range"
        return "Latest \(trim(last)) \(t.unit) — \(state). Look for repeating patterns, not single days."
    }
}

#Preview {
    TrendsView().environment(HealthStore())
}
