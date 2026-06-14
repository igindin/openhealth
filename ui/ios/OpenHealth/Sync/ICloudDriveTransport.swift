import Foundation

/// iCloud Drive transport. Resolves the app's ubiquity container and writes the
/// bridge layout under its `Documents/` directory. On the Mac these files appear
/// at `~/Library/Mobile Documents/iCloud~org~openhealth~app/Documents/`, readable
/// by the local OpenHealth process with no Apple authentication.
///
/// The container's `Documents/` scope is made user-visible (Files / Finder) via
/// the `NSUbiquitousContainers` Info.plist key (see project.yml).
struct ICloudDriveTransport: SyncTransport {
    static let defaultContainerId = "iCloud.org.openhealth.app"

    let containerId: String
    private let file: FileSyncTransport

    /// Fails when iCloud is unavailable or the user is signed out. Call off the
    /// main thread: `url(forUbiquityContainerIdentifier:)` can block on first use.
    init?(containerId: String = ICloudDriveTransport.defaultContainerId) {
        guard let base = FileManager.default.url(forUbiquityContainerIdentifier: containerId) else {
            return nil
        }
        self.containerId = containerId
        let documents = base.appendingPathComponent("Documents", isDirectory: true)
        self.file = FileSyncTransport(root: documents)
    }

    @discardableResult
    func writeInbox(_ records: [SyncRecord], batchName: String) throws -> URL {
        try file.writeInbox(records, batchName: batchName)
    }

    func writeManifest(_ manifest: SyncManifest) throws {
        try file.writeManifest(manifest)
    }

    func readManifest() throws -> SyncManifest? {
        try file.readManifest()
    }

    func readOutbox() throws -> HealthSnapshot? {
        try file.readOutbox()
    }
}
