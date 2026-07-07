import Foundation
import EventKit
#if canImport(WeatherKit)
import WeatherKit
#endif
import CoreLocation

/// Result 3 context: gather lightweight life-context (calendar load, weather) and
/// emit `ContextNote` bridge records the Mac engine correlates against HRV.
///
/// Pure mappers are unit-tested; the EventKit/WeatherKit fetches run on device
/// (calendar needs permission via `NSCalendarsFullAccessUsageDescription`,
/// WeatherKit needs its entitlement).
struct ContextCollector {

    /// Calendar load 0...100 from total meeting minutes vs a target workday.
    static func calendarLoad(meetingMinutes: Double, targetMinutes: Double = 480) -> Int {
        guard targetMinutes > 0 else { return 0 }
        return Int((min(max(meetingMinutes, 0) / targetMinutes, 1.0) * 100).rounded())
    }

    /// Build a ContextNote bridge record.
    static func record(date: String, kind: String, values: [String: Double], text: String? = nil) -> SyncRecord {
        .context(ContextNote(externalId: "\(kind)-\(date)", date: date, kind: kind, values: values, text: text))
    }

    static func dayKey(_ date: Date = Date()) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.string(from: date)
    }

    /// Total minutes of calendar events for `day`.
    func meetingMinutes(eventStore: EKEventStore, day: Date = Date()) -> Double {
        let calendar = Calendar.current
        let start = calendar.startOfDay(for: day)
        guard let end = calendar.date(byAdding: .day, value: 1, to: start) else { return 0 }
        let predicate = eventStore.predicateForEvents(withStart: start, end: end, calendars: nil)
        return eventStore.events(matching: predicate)
            .reduce(0.0) { $0 + max(0, $1.endDate.timeIntervalSince($1.startDate)) / 60.0 }
    }

    /// Collect calendar (+ optional weather) as ContextNote records.
    func collect(eventStore: EKEventStore = EKEventStore(), location: CLLocation? = nil) async -> [SyncRecord] {
        var out: [SyncRecord] = []
        let day = Self.dayKey()

        if (try? await eventStore.requestFullAccessToEvents()) == true {
            let minutes = meetingMinutes(eventStore: eventStore)
            out.append(Self.record(date: day, kind: "calendar_load", values: [
                "meeting_minutes": minutes,
                "load": Double(Self.calendarLoad(meetingMinutes: minutes)),
            ]))
        }

        #if canImport(WeatherKit)
        if let location, let weather = try? await WeatherService.shared.weather(for: location) {
            let now = weather.currentWeather
            out.append(Self.record(date: day, kind: "weather", values: [
                "temperature_c": now.temperature.converted(to: .celsius).value,
                "pressure_hpa": now.pressure.converted(to: .hectopascals).value,
                "humidity_pct": now.humidity * 100,
                "uv_index": Double(now.uvIndex.value),
            ]))
        }
        #endif

        return out
    }
}
