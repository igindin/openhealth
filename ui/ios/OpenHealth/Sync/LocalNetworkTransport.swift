import Foundation
import Network

/// Result 4: a second transport that moves the bridge over the local network
/// instead of iCloud Drive. It writes the same `inbox/` / reads the same
/// `outbox/` layout as the file/iCloud transports, so the rest of the app is
/// transport-agnostic.
///
/// The `SyncTransport` surface is backed by a local staging root — the exact
/// bytes written to `inbox/` are what a peer reads, which is the loopback
/// contract the unit tests exercise. The Bonjour layer (`_openhealth._tcp`,
/// `NWListener`/`NWBrowser`) advertises and discovers the Mac peer to ship those
/// staged files; that half only does real work on a device sharing a LAN, so it
/// is kept separate from the contract-conformant core.
struct LocalNetworkTransport: SyncTransport {
    /// Bonjour service type both ends agree on.
    static let serviceType = "_openhealth._tcp"

    private let file: FileSyncTransport

    /// The staging root under Application Support (created on demand).
    init(root: URL? = nil) {
        let base = root ?? Self.defaultStagingRoot()
        self.file = FileSyncTransport(root: base)
    }

    static func defaultStagingRoot() -> URL {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        return support.appendingPathComponent("LocalNetworkBridge", isDirectory: true)
    }

    // MARK: - SyncTransport (loopback contract)

    @discardableResult
    func writeInbox(_ records: [SyncRecord], batchName: String) throws -> URL {
        try file.writeInbox(records, batchName: batchName)
    }

    func writeManifest(_ manifest: SyncManifest) throws { try file.writeManifest(manifest) }
    func readManifest() throws -> SyncManifest? { try file.readManifest() }
    func readOutbox() throws -> HealthSnapshot? { try file.readOutbox() }
}

/// Advertises this phone as an OpenHealth peer and discovers a Mac peer on the
/// same network. The transfer of staged files runs over the discovered
/// connection; discovery/advertising only progress on a real LAN, so this type
/// is inert in unit tests and drives the on-device local-network path.
@MainActor
final class BonjourPeer {
    enum State: Equatable { case idle, advertising, browsing, connected(String), failed(String) }

    private(set) var state: State = .idle
    private var listener: NWListener?
    private var browser: NWBrowser?

    /// Advertise `_openhealth._tcp` so a Mac peer can find this phone.
    func startAdvertising(name: String = "OpenHealth") {
        do {
            let listener = try NWListener(using: .tcp)
            listener.service = NWListener.Service(name: name, type: LocalNetworkTransport.serviceType)
            listener.stateUpdateHandler = { [weak self] update in
                Task { @MainActor in
                    switch update {
                    case .ready: self?.state = .advertising
                    case .failed(let error): self?.state = .failed(error.localizedDescription)
                    default: break
                    }
                }
            }
            listener.newConnectionHandler = { connection in connection.start(queue: .main) }
            listener.start(queue: .main)
            self.listener = listener
        } catch {
            state = .failed(error.localizedDescription)
        }
    }

    /// Browse for a Mac peer advertising the same service.
    func startBrowsing() {
        let browser = NWBrowser(for: .bonjour(type: LocalNetworkTransport.serviceType, domain: nil),
                                using: .tcp)
        browser.stateUpdateHandler = { [weak self] update in
            Task { @MainActor in
                if case .failed(let error) = update { self?.state = .failed(error.localizedDescription) }
            }
        }
        browser.browseResultsChangedHandler = { [weak self] results, _ in
            Task { @MainActor in
                if let first = results.first, case let .service(name, _, _, _) = first.endpoint {
                    self?.state = .connected(name)
                } else {
                    self?.state = .browsing
                }
            }
        }
        browser.start(queue: .main)
        self.browser = browser
        state = .browsing
    }

    func stop() {
        listener?.cancel(); listener = nil
        browser?.cancel(); browser = nil
        state = .idle
    }
}
