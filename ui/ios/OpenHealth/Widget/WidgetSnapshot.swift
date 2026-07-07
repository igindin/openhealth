import Foundation

/// Compact snapshot the home-screen widget renders, shared with the main app
/// through an App Group container. Kept minimal and self-contained so the widget
/// extension doesn't pull in the full model or HealthKit stack.
struct WidgetSnapshot: Codable, Hashable {
    var recovery: Int?          // 0...100
    var recoveryZone: Zone
    var hrv: String?            // e.g. "50 ms"
    var actions: [String]       // 0-3 short suggestions
    var updatedAt: Date?

    enum Zone: String, Codable { case green, yellow, red, unknown }

    static let placeholder = WidgetSnapshot(
        recovery: 70, recoveryZone: .green, hrv: "50 ms",
        actions: ["Use the window for your hardest task"], updatedAt: nil
    )
}

/// Reads/writes the widget snapshot as JSON in the shared App Group container.
/// A directory can be injected for tests (App Groups aren't reliably available
/// to unit-test hosts on the simulator), falling back to caches otherwise.
struct WidgetSnapshotStore {
    static let appGroup = "group.org.openhealth.app"
    static let fileName = "widget-snapshot.json"

    let directory: URL

    init(directory: URL? = nil) {
        if let directory {
            self.directory = directory
        } else if let group = FileManager.default
            .containerURL(forSecurityApplicationGroupIdentifier: Self.appGroup) {
            self.directory = group
        } else {
            self.directory = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
        }
    }

    private var fileURL: URL { directory.appendingPathComponent(Self.fileName) }

    func write(_ snapshot: WidgetSnapshot) {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        if let data = try? encoder.encode(snapshot) {
            try? data.write(to: fileURL, options: [.atomic])
        }
    }

    func read() -> WidgetSnapshot? {
        guard let data = try? Data(contentsOf: fileURL) else { return nil }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try? decoder.decode(WidgetSnapshot.self, from: data)
    }
}
