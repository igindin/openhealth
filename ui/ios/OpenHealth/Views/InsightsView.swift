import SwiftUI

struct InsightsView: View {
    @Environment(HealthStore.self) private var store

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Theme.s4) {
                    Text("Hypotheses to explore, never conclusions. Each one shows how sure we are.")
                        .font(Theme.body(13)).foregroundStyle(Theme.inkSoft)

                    if store.snapshot.insights.isEmpty {
                        emptyState
                    } else {
                        ForEach(Array(store.snapshot.insights.enumerated()), id: \.element.id) { i, insight in
                            insightCard(insight).riseIn(i)
                        }
                    }
                }
                .padding(Theme.s4)
            }
            .background(Theme.background)
            .navigationTitle("Insights")
        }
    }

    private var emptyState: some View {
        VStack(spacing: Theme.s3) {
            Image(systemName: "lightbulb")
                .font(.system(size: 44, weight: .regular))
                .foregroundStyle(Theme.inkSoft)
                .padding(.bottom, Theme.s1)
            Text("No hypotheses yet")
                .font(Theme.display(21))
                .foregroundStyle(Theme.ink)
            Text("Keep a daily check-in and sync your recovery. Once there's enough signal, patterns worth testing show up here — each phrased as a question, with how to test it.")
                .font(Theme.body(14))
                .foregroundStyle(Theme.inkSoft)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, Theme.s5 * 2)
        .padding(.horizontal, Theme.s3)
    }

    private func insightCard(_ insight: Insight) -> some View {
        Card {
            VStack(alignment: .leading, spacing: Theme.s3) {
                HStack(alignment: .top) {
                    Text(insight.title)
                        .font(Theme.body(16, weight: .semibold)).foregroundStyle(Theme.ink)
                    Spacer()
                    ConfidenceChip(confidence: insight.confidence)
                }

                // Phrase as a question at C3 and below.
                Text(insight.confidence.framesAsQuestion
                     ? "Possible pattern: \(insight.statement) What else could explain it?"
                     : insight.statement)
                    .font(Theme.body(15)).foregroundStyle(Theme.ink)

                if !insight.openQuestions.isEmpty {
                    VStack(alignment: .leading, spacing: Theme.s1) {
                        ForEach(insight.openQuestions, id: \.self) { q in
                            HStack(alignment: .top, spacing: Theme.s2) {
                                Text("•").foregroundStyle(Theme.inkSoft)
                                Text(q).font(Theme.body(13)).foregroundStyle(Theme.inkSoft)
                            }
                        }
                    }
                }

                if let validation = insight.suggestedValidation {
                    DisclosureGroup {
                        Text(validation).font(Theme.body(13)).foregroundStyle(Theme.inkSoft)
                            .padding(.top, Theme.s1)
                    } label: {
                        Text("How to test this")
                            .font(Theme.body(14, weight: .medium)).foregroundStyle(Theme.accent)
                    }
                    .tint(Theme.accent)
                }

                if !insight.sources.isEmpty {
                    Text("Sources: \(insight.sources.count)")
                        .font(Theme.body(11)).foregroundStyle(Theme.inkSoft)
                }
            }
        }
    }
}

#Preview {
    InsightsView().environment(HealthStore())
}
