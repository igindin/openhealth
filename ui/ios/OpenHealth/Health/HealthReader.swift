import Foundation

/// The read surface the sync coordinator depends on.
///
/// `HealthKitIngest` is the production implementation; tests inject a fake to
/// exercise the pagination/drain loop without HealthKit. Each call returns one
/// bounded page of records plus the advanced anchor, so the coordinator can drain
/// arbitrarily large histories without loading everything into memory at once.
protocol HealthReader {
    var isAvailable: Bool { get }
    func requestAuthorization() async throws
    func readQuantityPage(series: SeriesType, anchorData: Data?, sinceDate: Date?, limit: Int) async throws -> (records: [SyncRecord], newAnchor: Data?)
    func readSleepPage(anchorData: Data?, sinceDate: Date?, limit: Int) async throws -> (records: [SyncRecord], newAnchor: Data?)
    func readWorkoutPage(anchorData: Data?, sinceDate: Date?, limit: Int) async throws -> (records: [SyncRecord], newAnchor: Data?)
}
