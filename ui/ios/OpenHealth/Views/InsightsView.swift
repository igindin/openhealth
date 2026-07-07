import SwiftUI

struct InsightsView: View {
    @Environment(HealthStore.self) private var store

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Theme.s4) {
                    Text("Hypotheses to explore, never conclusions. Each one shows how sure we are.")
                        .font(.system(size: 13)).foregroundStyle(Theme.inkSoft)

                    if store.snapshot.insights.isEmpty {
                        emptyState
                    } else {
                        ForEach(store.snapshot.insights) { insight in
                            insightCard(insight)
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
                .font(.system(size: 20, weight: .semibold, design: .serif))
                .foregroundStyle(Theme.ink)
            Text("Keep a daily check-in and sync your recovery. Once there's enough signal, patterns worth testing show up here — each phrased as a question, with how to test it.")
                .font(.system(size: 14))
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
                HStack {
                    Text(insight.title)
                        .font(.system(size: 17, weight: .semibold)).foregroundStyle(Theme.ink)
                    Spacer()
                    ConfidenceChip(confidence: insight.confidence)
                }

                // Phrase as a question at C3 and below.
                Text(insight.confidence.framesAsQuestion
                     ? "Possible pattern: \(insight.statement) What else could explain it?"
                     : insight.statement)
                    .font(.system(size: 15)).foregroundStyle(Theme.ink)

                if !insight.openQuestions.isEmpty {
                    VStack(alignment: .leading, spacing: Theme.s1) {
                        ForEach(insight.openQuestions, id: \.self) { q in
                            HStack(alignment: .top, spacing: Theme.s2) {
                                Text("•").foregroundStyle(Theme.inkSoft)
                                Text(q).font(.system(size: 13)).foregroundStyle(Theme.inkSoft)
                            }
                        }
                    }
                }

                if let validation = insight.suggestedValidation {
                    DisclosureGroup {
                        Text(validation).font(.system(size: 13)).foregroundStyle(Theme.inkSoft)
                            .padding(.top, Theme.s1)
                    } label: {
                        Text("How to test this")
                            .font(.system(size: 14, weight: .medium)).foregroundStyle(Theme.accent)
                    }
                    .tint(Theme.accent)
                }

                if !insight.sources.isEmpty {
                    Text("Sources: \(insight.sources.count)")
                        .font(.system(size: 11)).foregroundStyle(Theme.inkSoft)
                }
            }
        }
    }
}

#Preview {
    InsightsView().environment(HealthStore())
}
