import UIKit
import Capacitor

/// Entry view controller (wired up in Main.storyboard) — registers the app's
/// local plugins with the Capacitor bridge.
class MainViewController: CAPBridgeViewController {
    override open func capacitorDidLoad() {
        bridge?.registerPluginInstance(GymTimerPlugin())
    }
}
