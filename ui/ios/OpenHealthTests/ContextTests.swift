import XCTest
@testable import OpenHealth

/// Result 3 context: pure mappers + the ContextNote bridge record round-trip.
/// (EventKit/WeatherKit fetches run on device and aren't unit-tested here.)
final class ContextTests: XCTestCase {

    func testCalendarLoadScalesAndClamps() {
        XCTAssertEqual(ContextCollector.calendarLoad(meetingMinutes: 0), 0)
        XCTAssertEqual(ContextCollector.calendarLoad(meetingMinutes: 240, targetMinutes: 480), 50)
        XCTAssertEqual(ContextCollector.calendarLoad(meetingMinutes: 600, targetMinutes: 480), 100)
        XCTAssertEqual(ContextCollector.calendarLoad(meetingMinutes: -30), 0)
    }

    func testContextRecordRoundTripsAsContextNote() throws {
        let record = ContextCollector.record(date: "2026-06-14", kind: "calendar_load",
                                             values: ["load": 50, "meeting_minutes": 240])
        let data = try NDJSON.encode([record])

        // The envelope discriminator stays "kind"; the note's own kind is kind_tag.
        let line = String(data: data, encoding: .utf8)!
        XCTAssertTrue(line.contains("\"kind\":\"context\""))
        XCTAssertTrue(line.contains("\"kind_tag\":\"calendar_load\""))

        let back = try NDJSON.decode(data)
        guard case let .context(note) = back.first else {
            return XCTFail("expected a context record")
        }
        XCTAssertEqual(note.kind, "calendar_load")
        XCTAssertEqual(note.date, "2026-06-14")
        XCTAssertEqual(note.values?["load"], 50)
        XCTAssertEqual(note.externalId, "calendar_load-2026-06-14")
    }
}
