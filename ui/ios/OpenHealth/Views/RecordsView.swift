import SwiftUI

struct RecordsView: View {
    @Environment(HealthStore.self) private var store

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Theme.s4) {
                    Text("Lab panels")
                        .font(Theme.body(20, weight: .semibold))
                        .foregroundStyle(Theme.ink)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    ForEach(store.snapshot.panels) { panel in
                        NavigationLink {
                            LabPanelDetailView(panel: panel)
                        } label: {
                            Card {
                                HStack {
                                    VStack(alignment: .leading, spacing: Theme.s1) {
                                        Text(panel.title)
                                            .font(.system(size: 16, weight: .semibold))
                                            .foregroundStyle(Theme.ink)
                                        Text("\(panel.date) · \(panel.markers.count) markers")
                                            .font(.system(size: 13)).foregroundStyle(Theme.inkSoft)
                                    }
                                    Spacer()
                                    if !panel.abnormal.isEmpty {
                                        Text("\(panel.abnormal.count)")
                                            .font(.system(size: 12, weight: .bold))
                                            .padding(.horizontal, Theme.s2).padding(.vertical, 2)
                                            .background(Theme.warn.opacity(0.15))
                                            .foregroundStyle(Theme.warn)
                                            .clipShape(Capsule())
                                    }
                                    Image(systemName: "chevron.right").foregroundStyle(Theme.inkSoft)
                                }
                            }
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(Theme.s4)
            }
            .background(Theme.background)
            .navigationTitle("Records")
        }
    }
}

#Preview {
    RecordsView().environment(HealthStore())
}
