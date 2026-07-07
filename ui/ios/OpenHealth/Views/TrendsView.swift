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
                        metricCard(t)
                    } else {
                        Text("No trends yet.").foregroundStyle(Theme.inkSoft)
                    }

                    if !store.snapshot.correlations.isEmpty {
                        correlationsCard
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
                    .font(.system(size: 13)).foregroundStyle(Theme.inkSoft)
            }
        }
    }

    /// A number-forward header: the latest value, its unit, and a calm delta chip
    /// versus the period's typical (mean). Observational — direction only, no
    /// good/bad grading.
    @ViewBuilder
    private func statHeader(_ t: Trend) -> some View {
        let values = t.points.map(\.value)
        let last = values.last
        HStack(alignment: .firstTextBaseline, spacing: Theme.s2) {
            VStack(alignment: .leading, spacing: 2) {
                Text(t.title.uppercased())
                    .font(.system(size: 11, weight: .semibold)).tracking(1.0)
                    .foregroundStyle(Theme.inkSoft)
                HStack(alignment: .firstTextBaseline, spacing: Theme.s1) {
                    Text(last.map(trim) ?? "—")
                        .font(.system(size: 40, weight: .bold, design: .rounded))
                        .monospacedDigit()
                        .foregroundStyle(Theme.ink)
                    Text(t.unit)
                        .font(.system(size: 15, weight: .medium))
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
                    .font(.system(size: 13, weight: .semibold, design: .monospaced))
            }
            .foregroundStyle(Theme.inkSoft)
            .padding(.horizontal, Theme.s2 + 2)
            .padding(.vertical, 6)
            .background(Theme.surfaceAlt)
            .overlay(Capsule().stroke(Theme.hairlineStrong, lineWidth: 1))
            .clipShape(Capsule())
        }
    }

    private var correlationsCard: some View {
        Card {
            VStack(alignment: .leading, spacing: Theme.s3) {
                Text("WHAT AFFECTS YOU")
                    .font(.system(size: 11, weight: .semibold)).tracking(1.0)
                    .foregroundStyle(Theme.inkSoft)
                ForEach(store.snapshot.correlations) { c in
                    HStack(spacing: Theme.s2) {
                        Text(c.dir == "up" ? "▲" : "▼")
                            .font(.system(size: 12, weight: .bold))
                            .foregroundStyle(c.dir == "up" ? Theme.accent : Theme.warn)
                        Text(c.label).font(.system(size: 14)).foregroundStyle(Theme.ink)
                        Spacer()
                        if let d = c.delta {
                            Text("\(d > 0 ? "+" : "")\(d)")
                                .font(.system(size: 13, weight: .semibold, design: .monospaced))
                                .foregroundStyle(Theme.inkSoft)
                        }
                        Text(c.grade)
                            .font(.system(size: 10, weight: .semibold, design: .monospaced))
                            .foregroundStyle(Theme.inkDim)
                    }
                }
                Text("Behaviour ↔ recovery links from your journal. Association, not cause.")
                    .font(.system(size: 11)).foregroundStyle(Theme.inkDim)
            }
        }
    }

    // MARK: - Chart

    @ViewBuilder
    private func chart(_ t: Trend) -> some View {
        let lastPoint = t.points.last
        Chart {
            if let lo = t.referenceLow, let hi = t.referenceHigh {
                RectangleMark(yStart: .value("low", lo), yEnd: .value("high", hi))
                    .foregroundStyle(Theme.accent.opacity(0.08))
            }
            ForEach(t.points) { p in
                AreaMark(x: .value("Day", p.date), y: .value(t.unit, p.value))
                    .foregroundStyle(
                        LinearGradient(
                            colors: [Theme.accent.opacity(0.22), Theme.accent.opacity(0.0)],
                            startPoint: .top, endPoint: .bottom
                        )
                    )
                    .interpolationMethod(.catmullRom)
                LineMark(x: .value("Day", p.date), y: .value(t.unit, p.value))
                    .foregroundStyle(Theme.accent)
                    .lineStyle(StrokeStyle(lineWidth: 2.5, lineCap: .round))
                    .interpolationMethod(.catmullRom)
            }
            if let lp = lastPoint {
                PointMark(x: .value("Day", lp.date), y: .value(t.unit, lp.value))
                    .foregroundStyle(Theme.background)
                    .symbolSize(160)
                PointMark(x: .value("Day", lp.date), y: .value(t.unit, lp.value))
                    .foregroundStyle(Theme.accent)
                    .symbolSize(80)
                    .annotation(position: .top, spacing: 6) {
                        Text("\(trim(lp.value)) \(t.unit)")
                            .font(.system(size: 11, weight: .semibold, design: .rounded))
                            .foregroundStyle(Theme.ink)
                            .padding(.horizontal, 6).padding(.vertical, 3)
                            .background(Theme.surfaceAlt)
                            .clipShape(Capsule())
                    }
            }
        }
        .chartYScale(domain: yDomain(t))
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
