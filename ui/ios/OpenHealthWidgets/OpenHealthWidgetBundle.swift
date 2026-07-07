import WidgetKit
import SwiftUI

/// The widget extension entry point. Currently ships the recovery widget; more
/// (HRV, next action) can join this bundle without touching the app.
@main
struct OpenHealthWidgetBundle: WidgetBundle {
    var body: some Widget {
        RecoveryWidget()
    }
}
