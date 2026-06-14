import XCTest
@testable import OpenHealth

/// Result 2: the phone reads the Mac engine's `outbox/snapshot.json` and decodes
/// it into the same `HealthSnapshot` the screens render. Extra keys the engine
/// writes (generated_at, source) are ignored by the decoder.
final class OutboxTests: XCTestCase {

    func testReadOutboxDecodesEngineSnapshot() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("bridge-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let outbox = root.appendingPathComponent("outbox", isDirectory: true)
        try FileManager.default.createDirectory(at: outbox, withIntermediateDirectories: true)

        let json = """
        {
          "generated_at": "2026-06-14T12:00:00Z",
          "source": "apple_health",
          "greeting_name": "there",
          "measurements": [
            {"metric": "resting_hr", "title": "Resting HR", "value": "54 bpm", "caption": "latest"}
          ],
          "panels": [],
          "trends": [
            {"metric": "hrv", "title": "HRV (SDNN)", "unit": "ms",
             "reference_low": null, "reference_high": null,
             "points": [{"date": "06-12", "value": 42}]}
          ],
          "insights": [],
          "alerts": []
        }
        """
        try json.data(using: .utf8)!.write(to: outbox.appendingPathComponent("snapshot.json"))

        let transport = FileSyncTransport(root: root)
        let snapshot = try transport.readOutbox()

        XCTAssertNotNil(snapshot)
        XCTAssertEqual(snapshot?.measurements.first?.metric, "resting_hr")
        XCTAssertEqual(snapshot?.trends.first?.points.first?.value, 42)
    }

    func testReadOutboxReturnsNilWhenMissing() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("empty-\(UUID().uuidString)", isDirectory: true)
        let transport = FileSyncTransport(root: root)
        XCTAssertNil(try transport.readOutbox())
    }

    func testReadOutboxDecodesCorrelations() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("corr-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let outbox = root.appendingPathComponent("outbox", isDirectory: true)
        try FileManager.default.createDirectory(at: outbox, withIntermediateDirectories: true)
        let json = """
        {"greeting_name":"there","measurements":[],"panels":[],"trends":[],
         "insights":[],"alerts":[],
         "correlations":[{"id":"alcohol","label":"alcohol","delta":-8,"dir":"down","grade":"C2"}]}
        """
        try json.data(using: .utf8)!.write(to: outbox.appendingPathComponent("snapshot.json"))
        let snapshot = try FileSyncTransport(root: root).readOutbox()
        XCTAssertEqual(snapshot?.correlations.first?.id, "alcohol")
        XCTAssertEqual(snapshot?.correlations.first?.dir, "down")
        XCTAssertEqual(snapshot?.correlations.first?.delta, -8)
    }
}
