import XCTest
@testable import OpenHealth

/// Result 1 foundation: canonical records, NDJSON wire format, file transport,
/// dedup priority, and the on-device readiness estimate.
final class BridgeTests: XCTestCase {

    // MARK: - SeriesType vocabulary

    func testSeriesTypeUnits() {
        XCTAssertEqual(SeriesType.heartRateVariabilitySDNN.unit, "ms")
        XCTAssertEqual(SeriesType.restingHeartRate.unit, "bpm")
        XCTAssertEqual(SeriesType.stepCount.unit, "count")
        XCTAssertEqual(SeriesType.activeEnergyBurned.unit, "kcal")
        XCTAssertEqual(SeriesType.vo2Max.unit, "ml_kg_min")
    }

    func testHRVMetricsAreFlaggedSeparately() {
        XCTAssertTrue(SeriesType.heartRateVariabilitySDNN.isHRV)
        XCTAssertTrue(SeriesType.heartRateVariabilityRMSSD.isHRV)
        XCTAssertFalse(SeriesType.stepCount.isHRV)
        // The two HRV metrics are distinct cases — never folded into one.
        XCTAssertNotEqual(SeriesType.heartRateVariabilitySDNN, .heartRateVariabilityRMSSD)
    }

    // MARK: - NDJSON wire format

    func testSampleNDJSONRoundTrip() throws {
        let sample = HealthSample(
            externalId: "uuid-1",
            seriesType: .heartRateVariabilitySDNN,
            value: 42.0,
            recordedAt: Date(timeIntervalSince1970: 1_700_000_000),
            zoneOffsetSeconds: 7200,
            source: "apple_health",
            sourceBundleId: "com.apple.health",
            deviceModel: "Watch6,1"
        )
        let data = try NDJSON.encode([.sample(sample)])
        let back = try NDJSON.decode(data)
        XCTAssertEqual(back, [.sample(sample)])
        // unit defaulted from the series type
        XCTAssertEqual(sample.unit, "ms")
    }

    func testNDJSONDiscriminatorAndMultipleLines() throws {
        let sample = HealthSample(externalId: "u1", seriesType: .heartRate, value: 60,
                                  recordedAt: Date(timeIntervalSince1970: 1), zoneOffsetSeconds: 0,
                                  source: "apple_health")
        let event = HealthEvent(externalId: "w1", category: "workout", type: "running",
                                startAt: Date(timeIntervalSince1970: 1), endAt: Date(timeIntervalSince1970: 100),
                                zoneOffsetSeconds: 0, source: "apple_health")
        let data = try NDJSON.encode([.sample(sample), .event(event)])
        let text = String(data: data, encoding: .utf8)!

        XCTAssertEqual(text.split(separator: "\n").count, 2)
        XCTAssertTrue(text.contains("\"kind\":\"sample\""))
        XCTAssertTrue(text.contains("\"kind\":\"event\""))

        let back = try NDJSON.decode(data)
        XCTAssertEqual(back, [.sample(sample), .event(event)])
    }

    func testNDJSONSkipsBlankLines() throws {
        let raw = "\n  \n".data(using: .utf8)!
        XCTAssertEqual(try NDJSON.decode(raw), [])
    }

    // MARK: - File transport

    func testFileTransportWritesInboxAndManifest() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("bridge-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let transport = FileSyncTransport(root: root)
        let sample = HealthSample(externalId: "u1", seriesType: .restingHeartRate, value: 54,
                                  recordedAt: Date(timeIntervalSince1970: 1), zoneOffsetSeconds: 0,
                                  source: "apple_health")
        let url = try transport.writeInbox([.sample(sample)], batchName: "samples-test")

        XCTAssertTrue(FileManager.default.fileExists(atPath: url.path))
        XCTAssertEqual(url.pathExtension, "ndjson")
        XCTAssertEqual(try NDJSON.decode(Data(contentsOf: url)), [.sample(sample)])

        var manifest = SyncManifest(deviceId: "dev-1")
        manifest.anchors["heart_rate_variability_sdnn"] = "base64anchor=="
        try transport.writeManifest(manifest)

        let loaded = try transport.readManifest()
        XCTAssertEqual(loaded?.deviceId, "dev-1")
        XCTAssertEqual(loaded?.anchors["heart_rate_variability_sdnn"], "base64anchor==")
    }

    // MARK: - Readiness estimate (on-device, SDNN)

    func testReadinessZScoreNeedsEnoughBaseline() {
        XCTAssertNil(Readiness.zScore(latest: 50, baseline: [40, 41]))
    }

    func testReadinessEstimateMapping() {
        XCTAssertEqual(Readiness.estimate(fromZScore: 0), 50)
        XCTAssertEqual(Readiness.estimate(fromZScore: 2), 100)
        XCTAssertEqual(Readiness.estimate(fromZScore: -2), 0)
        XCTAssertEqual(Readiness.estimate(fromZScore: 5), 100)   // clamped
        XCTAssertEqual(Readiness.estimate(fromZScore: -5), 0)    // clamped
    }

    func testReadinessFromBaseline() {
        let baseline = [40.0, 42, 38, 41, 39, 43, 40]
        let estimate = Readiness.estimate(latestSDNN: 46, baselineSDNN: baseline)
        XCTAssertNotNil(estimate)
        XCTAssertGreaterThan(estimate ?? 0, 50)   // above baseline reads as better
    }

    // MARK: - Source priority / dedup

    func testDeviceClassInference() {
        XCTAssertEqual(DeviceClass.infer(fromModel: "Watch6,1"), .watch)
        XCTAssertEqual(DeviceClass.infer(fromModel: "iPhone14,2"), .phone)
        XCTAssertEqual(DeviceClass.infer(fromModel: nil), .unknown)
        XCTAssertTrue(DeviceClass.watch > DeviceClass.phone)
    }

    func testSourcePriorityIgnoreList() {
        let priority = SourcePriority(ignoredBundleIds: ["com.whoop.app"])
        XCTAssertFalse(priority.keep(sourceBundleId: "com.whoop.app"))
        XCTAssertTrue(priority.keep(sourceBundleId: "com.apple.health"))
        XCTAssertTrue(priority.keep(sourceBundleId: nil))
    }

    // MARK: - Journal mapping

    func testJournalEntryMapsToSyncRecord() {
        let entry = JournalEntry(
            dayKey: "2026-06-13",
            recordedAt: Date(timeIntervalSince1970: 1),
            values: ["alcohol": .bool(true), "energy": .scale(4)],
            note: "felt ok"
        )
        let record = JournalSyncRecord(entry: entry)
        XCTAssertEqual(record.yesNo["alcohol"], true)
        XCTAssertEqual(record.ratings["energy"], 4)
        XCTAssertEqual(record.note, "felt ok")
        XCTAssertEqual(record.dayKey, "2026-06-13")
    }

    // MARK: - Apple Health export importer

    func testExportImporterParsesRecordsSleepWorkout() {
        let xml = """
        <HealthData>
          <Record type="HKQuantityTypeIdentifierHeartRateVariabilitySDNN" sourceName="Apple Watch" unit="ms" startDate="2024-06-01 09:00:00 +0000" endDate="2024-06-01 09:00:00 +0000" value="42.5"/>
          <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="Apple Watch" startDate="2024-06-01 23:00:00 +0000" endDate="2024-06-02 06:00:00 +0000" value="HKCategoryValueSleepAnalysisAsleepCore"/>
          <Workout workoutActivityType="HKWorkoutActivityTypeRunning" duration="30" startDate="2024-06-01 18:00:00 +0000" endDate="2024-06-01 18:30:00 +0000" sourceName="Apple Watch"/>
          <Record type="HKQuantityTypeIdentifierUnknownThing" startDate="2024-06-01 09:00:00 +0000" value="1"/>
        </HealthData>
        """.data(using: .utf8)!

        let result = AppleHealthExportImporter().parse(data: xml)
        XCTAssertEqual(result.samples.count, 1)
        XCTAssertEqual(result.samples.first?.seriesType, .heartRateVariabilitySDNN)
        XCTAssertEqual(result.samples.first?.value, 42.5)
        XCTAssertEqual(result.events.count, 2)   // sleep + workout
        XCTAssertEqual(result.skipped, 1)        // unknown quantity type
    }
}
