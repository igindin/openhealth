import XCTest
@testable import OpenHealth

/// A fake reader that yields two pages per quantity type (a full page, then a
/// short final page), keyed off the anchor token. Lets us exercise the drain /
/// pagination loop without HealthKit — the path that crashed when it loaded the
/// entire history at once.
private final class FakeHealthReader: HealthReader {
    var isAvailable = true
    func requestAuthorization() async throws {}

    private func page(_ tag: String, anchorData: Data?, limit: Int) -> (records: [SyncRecord], newAnchor: Data?) {
        let token = anchorData.flatMap { String(data: $0, encoding: .utf8) } ?? ""
        func samples(_ n: Int, _ p: String) -> [SyncRecord] {
            (0..<n).map { i in
                .sample(HealthSample(externalId: "\(tag)-\(p)-\(i)", seriesType: .heartRate, value: 1,
                                     recordedAt: Date(timeIntervalSince1970: 1), zoneOffsetSeconds: 0, source: "fake"))
            }
        }
        switch token {
        case "":   return (samples(limit, "1"), Data("a1".utf8))      // full page
        case "a1": return (samples(limit / 2, "2"), Data("a2".utf8))  // short final page
        default:   return ([], anchorData)
        }
    }

    func readQuantityPage(series: SeriesType, anchorData: Data?, sinceDate: Date?, limit: Int) async throws -> (records: [SyncRecord], newAnchor: Data?) {
        page(series.rawValue, anchorData: anchorData, limit: limit)
    }
    func readSleepPage(anchorData: Data?, sinceDate: Date?, limit: Int) async throws -> (records: [SyncRecord], newAnchor: Data?) { ([], nil) }
    func readWorkoutPage(anchorData: Data?, sinceDate: Date?, limit: Int) async throws -> (records: [SyncRecord], newAnchor: Data?) { ([], nil) }
}

@MainActor
final class SyncCoordinatorTests: XCTestCase {

    func testRunSyncPaginatesAndWritesEachPage() async throws {
        UserDefaults.standard.removeObject(forKey: "openhealth.manifest")
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("sync-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let transport = FileSyncTransport(root: root)
        let coordinator = SyncCoordinator(
            reader: FakeHealthReader(),
            transportProvider: { transport },
            pageSize: 4,
            initialHistoryDays: 30,
            maxPagesPerType: 10
        )

        await coordinator.runSync()

        // Synced (not crashed / not failed).
        if case .synced = coordinator.status {} else {
            XCTFail("expected .synced, got \(coordinator.status)")
        }

        // Two pages written per quantity type (full + short final); sleep/workout empty.
        let inbox = root.appendingPathComponent("inbox", isDirectory: true)
        let files = try FileManager.default
            .contentsOfDirectory(at: inbox, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "ndjson" }
        let typeCount = HealthKitTypes.quantitySeries.count
        XCTAssertEqual(files.count, typeCount * 2)

        // Records add up to (full + short) per type, and each file is independently small.
        var total = 0
        for file in files {
            let count = try NDJSON.decode(Data(contentsOf: file)).count
            XCTAssertLessThanOrEqual(count, 4)   // never more than one page in a file
            total += count
        }
        XCTAssertEqual(total, typeCount * 6)      // 4 + 2 per type

        // Anchor advanced and persisted in the manifest.
        XCTAssertNotNil(try transport.readManifest()?.anchors["heart_rate"])
    }
}
