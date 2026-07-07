import XCTest
@testable import OpenHealth

/// Result 3: the compact widget snapshot the app shares with the extension via
/// the App Group. We test the mapping and the JSON round-trip through a store
/// backed by an injected temp directory (App Groups aren't available to the
/// unit-test host on the simulator).
final class WidgetSnapshotTests: XCTestCase {

    func testMapsRecoveryZoneAndHRV() {
        let snapshot = HealthSnapshot(
            greetingName: "there",
            measurements: [
                Measurement(metric: "recovery", title: "Recovery", value: "72", caption: "demo"),
                Measurement(metric: "hrv", title: "HRV", value: "48 ms", caption: "SDNN"),
            ],
            panels: [], trends: [], insights: [], alerts: []
        )
        let widget = WidgetSnapshot(from: snapshot)
        XCTAssertEqual(widget.recovery, 72)
        XCTAssertEqual(widget.recoveryZone, .green)   // >= 67
        XCTAssertEqual(widget.hrv, "48 ms")
    }

    func testUnknownZoneWhenNoRecovery() {
        let snapshot = HealthSnapshot(greetingName: "there", measurements: [],
                                      panels: [], trends: [], insights: [], alerts: [])
        let widget = WidgetSnapshot(from: snapshot)
        XCTAssertNil(widget.recovery)
        XCTAssertEqual(widget.recoveryZone, .unknown)
    }

    func testStoreRoundTripsThroughInjectedDirectory() {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("widget-\(UUID().uuidString)", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }

        let store = WidgetSnapshotStore(directory: dir)
        XCTAssertNil(store.read())

        let value = WidgetSnapshot(recovery: 40, recoveryZone: .yellow, hrv: "35 ms",
                                   actions: ["Pick one helpful action"], updatedAt: nil)
        store.write(value)
        XCTAssertEqual(store.read(), value)
    }
}
