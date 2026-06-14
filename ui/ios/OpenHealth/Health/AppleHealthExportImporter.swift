import Foundation

/// Parses an Apple Health "Export All Health Data" `export.xml` into canonical
/// records for one-shot bulk backfill. Streaming (XMLParser) so multi-hundred-MB
/// exports don't blow memory. The live HealthKit path stays authoritative for
/// units and provenance; this just fills deep history. Foundation-only.
///
/// Export records carry no UUID, so a deterministic synthetic `external_id` is
/// derived from (series, start, value, source) — re-importing the same export is
/// idempotent against the live UUID-keyed records and against itself.
final class AppleHealthExportImporter: NSObject {

    struct ImportResult: Equatable {
        var samples: [HealthSample] = []
        var events: [HealthEvent] = []
        var skipped: Int = 0
    }

    private var result = ImportResult()
    private let source = "apple_health_export"

    private static let dateFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd HH:mm:ss Z"
        return f
    }()

    /// Quantity / category identifier strings → canonical series.
    private static let seriesByIdentifier: [String: SeriesType] = [
        "HKQuantityTypeIdentifierHeartRate": .heartRate,
        "HKQuantityTypeIdentifierRestingHeartRate": .restingHeartRate,
        "HKQuantityTypeIdentifierWalkingHeartRateAverage": .walkingHeartRateAverage,
        "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": .heartRateVariabilitySDNN,
        "HKQuantityTypeIdentifierRespiratoryRate": .respiratoryRate,
        "HKQuantityTypeIdentifierOxygenSaturation": .oxygenSaturation,
        "HKQuantityTypeIdentifierStepCount": .stepCount,
        "HKQuantityTypeIdentifierActiveEnergyBurned": .activeEnergyBurned,
        "HKQuantityTypeIdentifierBasalEnergyBurned": .basalEnergyBurned,
        "HKQuantityTypeIdentifierVO2Max": .vo2Max,
        "HKQuantityTypeIdentifierBodyMass": .bodyMass
    ]

    private static let sleepIdentifier = "HKCategoryTypeIdentifierSleepAnalysis"

    // MARK: - Entry points

    func parse(fileURL: URL) throws -> ImportResult {
        guard let parser = XMLParser(contentsOf: fileURL) else {
            throw CocoaError(.fileReadUnknown)
        }
        return run(parser)
    }

    func parse(data: Data) -> ImportResult {
        run(XMLParser(data: data))
    }

    private func run(_ parser: XMLParser) -> ImportResult {
        result = ImportResult()
        parser.delegate = self
        parser.parse()
        return result
    }

    // MARK: - Helpers

    /// Parse an export date string, returning the instant and its zone offset.
    static func parseDate(_ string: String) -> (date: Date, offset: Int)? {
        guard let date = dateFormatter.date(from: string) else { return nil }
        var offset = 0
        if let token = string.split(separator: " ").last, token.count == 5, let sign = token.first {
            let digits = token.dropFirst()
            if let hours = Int(digits.prefix(2)), let minutes = Int(digits.suffix(2)) {
                offset = (hours * 3600 + minutes * 60) * (sign == "-" ? -1 : 1)
            }
        }
        return (date, offset)
    }

    private func syntheticId(_ parts: String...) -> String {
        "ahx|" + parts.joined(separator: "|")
    }
}

extension AppleHealthExportImporter: XMLParserDelegate {
    func parser(_ parser: XMLParser,
                didStartElement elementName: String,
                namespaceURI: String?,
                qualifiedName qName: String?,
                attributes attributeDict: [String: String]) {
        switch elementName {
        case "Record":  handleRecord(attributeDict)
        case "Workout": handleWorkout(attributeDict)
        default:        break
        }
    }

    private func handleRecord(_ attr: [String: String]) {
        guard let type = attr["type"],
              let startStr = attr["startDate"],
              let start = Self.parseDate(startStr) else {
            result.skipped += 1
            return
        }
        let end = attr["endDate"].flatMap { Self.parseDate($0) }
        let bundleId = attr["sourceName"]

        if let series = Self.seriesByIdentifier[type], let raw = attr["value"], let value = Double(raw) {
            result.samples.append(
                HealthSample(
                    externalId: syntheticId(series.rawValue, startStr, raw, bundleId ?? ""),
                    seriesType: series,
                    value: value,
                    recordedAt: start.date,
                    endAt: end?.date,
                    zoneOffsetSeconds: start.offset,
                    source: source,
                    sourceBundleId: bundleId,
                    deviceModel: attr["device"]
                )
            )
        } else if type == Self.sleepIdentifier, let value = attr["value"], let end {
            let stage = value.replacingOccurrences(of: "HKCategoryValueSleepAnalysis", with: "").lowercased()
            result.events.append(
                HealthEvent(
                    externalId: syntheticId("sleep", startStr, value, bundleId ?? ""),
                    category: "sleep",
                    type: stage.isEmpty ? "unknown" : stage,
                    startAt: start.date,
                    endAt: end.date,
                    zoneOffsetSeconds: start.offset,
                    source: source,
                    sourceBundleId: bundleId,
                    deviceModel: attr["device"],
                    metrics: ["duration_seconds": end.date.timeIntervalSince(start.date)]
                )
            )
        } else {
            result.skipped += 1
        }
    }

    private func handleWorkout(_ attr: [String: String]) {
        guard let activity = attr["workoutActivityType"],
              let startStr = attr["startDate"],
              let start = Self.parseDate(startStr),
              let endStr = attr["endDate"],
              let end = Self.parseDate(endStr) else {
            result.skipped += 1
            return
        }
        let bundleId = attr["sourceName"]
        var metrics: [String: Double] = ["duration_seconds": end.date.timeIntervalSince(start.date)]
        if let durStr = attr["duration"], let dur = Double(durStr) { metrics["reported_duration"] = dur }

        result.events.append(
            HealthEvent(
                externalId: syntheticId("workout", startStr, activity, bundleId ?? ""),
                category: "workout",
                type: activity.replacingOccurrences(of: "HKWorkoutActivityType", with: "").lowercased(),
                startAt: start.date,
                endAt: end.date,
                zoneOffsetSeconds: start.offset,
                source: source,
                sourceBundleId: bundleId,
                deviceModel: attr["device"],
                metrics: metrics
            )
        )
    }
}
