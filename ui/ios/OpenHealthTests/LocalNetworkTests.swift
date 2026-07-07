import XCTest
@testable import OpenHealth

/// Result 4: the local-network transport implements the same `SyncTransport`
/// contract as the file/iCloud transports. The bytes written to `inbox/` are the
/// bytes a peer reads back — the loopback contract exercised here. (Bonjour
/// discovery over a real LAN is device-only and not unit-tested.)
final class LocalNetworkTests: XCTestCase {

    private func makeRoot() -> URL {
        FileManager.default.temporaryDirectory
            .appendingPathComponent("lan-\(UUID().uuidString)", isDirectory: true)
    }

    func testInboxRoundTripsThroughContract() throws {
        let root = makeRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let transport = LocalNetworkTransport(root: root)

        let records: [SyncRecord] = [
            .sample(HealthSample(externalId: "hr-1", seriesType: .heartRate, value: 61,
                                 recordedAt: Date(timeIntervalSince1970: 1), zoneOffsetSeconds: 0, source: "test")),
            .context(ContextNote(externalId: "calendar_load-2026-07-06", date: "2026-07-06",
                                 kind: "calendar_load", values: ["load": 40], text: nil)),
        ]
        let url = try transport.writeInbox(records, batchName: "batch-1")

        // Same bytes are readable back as the same records (loopback).
        let back = try NDJSON.decode(Data(contentsOf: url))
        XCTAssertEqual(back.count, 2)
        guard case let .sample(s) = back.first else { return XCTFail("expected a sample") }
        XCTAssertEqual(s.externalId, "hr-1")
        XCTAssertEqual(s.value, 61)
    }

    func testManifestRoundTrips() throws {
        let root = makeRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let transport = LocalNetworkTransport(root: root)

        var manifest = SyncManifest(deviceId: "device-xyz")
        manifest.anchors["heart_rate"] = "YW5jaG9y"
        try transport.writeManifest(manifest)

        let back = try transport.readManifest()
        XCTAssertEqual(back?.deviceId, "device-xyz")
        XCTAssertEqual(back?.anchors["heart_rate"], "YW5jaG9y")
    }

    func testReadOutboxDecodesSnapshot() throws {
        let root = makeRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let outbox = root.appendingPathComponent("outbox", isDirectory: true)
        try FileManager.default.createDirectory(at: outbox, withIntermediateDirectories: true)
        let json = """
        {"greeting_name":"there","measurements":[],"panels":[],"trends":[],"insights":[],"alerts":[]}
        """
        try json.data(using: .utf8)!.write(to: outbox.appendingPathComponent("snapshot.json"))

        let snapshot = try LocalNetworkTransport(root: root).readOutbox()
        XCTAssertEqual(snapshot?.greetingName, "there")
    }

    func testServiceTypeIsStable() {
        XCTAssertEqual(LocalNetworkTransport.serviceType, "_openhealth._tcp")
    }
}
