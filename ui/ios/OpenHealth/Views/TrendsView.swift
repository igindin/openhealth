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
                        if let last = t.points.last?.value {
                            RingGauge(
                                progress: ringProgress(t, last),
                                centerValue: trim(last),
                                centerUnit: t.unit,
                                tint: inRange(t, last) ? Theme.accent : Theme.warn,
                                size: 170
                            )
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, Theme.s2)
                        }
                        Card {
                            VStack(alignment: .leading, spacing: Theme.s3) {
                                Text(t.title).font(.system(size: 18, weight: .semibold))
                                    .foregroundStyle(Theme.ink)
                                chart(t)
                                    .frame(height: 200)
                                Text(readout(t))
                                    .font(.system(size: 13)).foregroundStyle(Theme.inkSoft)
                            }
                        }
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
                Text("Связи поведение↔recovery из журнала. Не причинность.")
                    .font(.system(size: 11)).foregroundStyle(Theme.inkDim)
            }
        }
    }

    @ViewBuilder
    private func chart(_ t: Trend) -> some View {
        Chart {
            if let lo = t.referenceLow, let hi = t.referenceHigh {
                RectangleMark(
                    yStart: .value("low", lo),
                    yEnd: .value("high", hi)
                )
                .foregroundStyle(Theme.accent.opacity(0.10))
            }
            ForEach(t.points) { p in
                LineMark(x: .value("Day", p.date), y: .value(t.unit, p.value))
                    .foregroundStyle(Theme.accent)
                    .interpolationMethod(.catmullRom)
                PointMark(x: .value("Day", p.date), y: .value(t.unit, p.value))
                    .foregroundStyle(Theme.accent)
            }
        }
        .chartYScale(domain: yDomain(t))
    }

    private func yDomain(_ t: Trend) -> ClosedRange<Double> {
        let values = t.points.map(\.value) + [t.referenceLow, t.referenceHigh].compactMap { $0 }
        let lo = (values.min() ?? 0) * 0.9
        let hi = (values.max() ?? 1) * 1.1
        return lo...max(hi, lo + 1)
    }

    /// Ring fill: position of the latest value inside its reference band (or the
    /// observed min/max when no reference exists).
    private func ringProgress(_ t: Trend, _ value: Double) -> Double {
        let lo = t.referenceLow ?? t.points.map(\.value).min() ?? value
        let hi = t.referenceHigh ?? t.points.map(\.value).max() ?? (value + 1)
        guard hi > lo else { return 0.5 }
        return min(max((value - lo) / (hi - lo), 0), 1)
    }
    private func inRange(_ t: Trend, _ value: Double) -> Bool {
        if let lo = t.referenceLow, value < lo { return false }
        if let hi = t.referenceHigh, value > hi { return false }
        return true
    }

    private func readout(_ t: Trend) -> String {
        guard let last = t.points.last?.value else { return "" }
        let inRange: Bool = {
            if let lo = t.referenceLow, last < lo { return false }
            if let hi = t.referenceHigh, last > hi { return false }
            return true
        }()
        let state = inRange ? "within your typical range" : "outside your typical range"
        return "Latest: \(trim(last)) \(t.unit) — \(state). Look for repeating patterns, not single days."
    }
}

#Preview {
    TrendsView().environment(HealthStore())
}
