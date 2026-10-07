import Foundation
import Capacitor
import AVFoundation
import UserNotifications
import UIKit

/// Native rest-timer support for the Gym Coach shell.
///
/// - armAlarm:    schedules a local notification so the "rest over" alarm
///                fires even when the phone is locked or the app backgrounded
///                (web timers/audio are suspended there).
/// - disarmAlarm: cancels it (pause / dismiss / timer stopped).
/// - fireAlarm:   foreground zero-cross: takes audio focus (pauses whatever
///                the athlete is watching), fires a haptic, and releases
///                focus after `duration` seconds with "resume others" so the
///                video continues. The page plays its own WebAudio alarm
///                while the session is active.
@objc(GymTimerPlugin)
public class GymTimerPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "GymTimerPlugin"
    public let jsName = "GymTimer"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "armAlarm", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "disarmAlarm", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "fireAlarm", returnType: CAPPluginReturnPromise),
    ]

    private static let alarmId = "gym.rest.alarm"
    private let notifDelegate = GymNotifDelegate()

    override public func load() {
        // Suppress the scheduled notification when the app is foreground —
        // the page handles the foreground alarm itself (sound + focus).
        UNUserNotificationCenter.current().delegate = notifDelegate
    }

    @objc func armAlarm(_ call: CAPPluginCall) {
        let seconds = call.getDouble("seconds") ?? 0
        guard seconds >= 1 else {
            call.resolve(["armed": false])
            return
        }
        let label = call.getString("label") ?? "Back to it — next set."
        let center = UNUserNotificationCenter.current()
        center.requestAuthorization(options: [.alert, .sound]) { granted, _ in
            guard granted else {
                call.resolve(["armed": false])
                return
            }
            let content = UNMutableNotificationContent()
            content.title = "Rest over"
            content.body = label
            content.sound = .default
            let trigger = UNTimeIntervalNotificationTrigger(timeInterval: seconds, repeats: false)
            center.removePendingNotificationRequests(withIdentifiers: [GymTimerPlugin.alarmId])
            center.add(UNNotificationRequest(identifier: GymTimerPlugin.alarmId,
                                             content: content, trigger: trigger))
            call.resolve(["armed": true])
        }
    }

    @objc func disarmAlarm(_ call: CAPPluginCall) {
        UNUserNotificationCenter.current()
            .removePendingNotificationRequests(withIdentifiers: [GymTimerPlugin.alarmId])
        call.resolve()
    }

    @objc func fireAlarm(_ call: CAPPluginCall) {
        let duration = call.getDouble("duration") ?? 2.5
        DispatchQueue.main.async {
            let gen = UINotificationFeedbackGenerator()
            gen.notificationOccurred(.success)
        }
        let session = AVAudioSession.sharedInstance()
        do {
            // Non-mixable playback session: other apps' audio (YouTube, music)
            // receives an interruption and pauses.
            try session.setCategory(.playback, mode: .default, options: [])
            try session.setActive(true)
        } catch {
            // Focus failed — the page still plays its alarm; nothing to do.
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + duration) {
            // Release with "resume others" so the paused app picks back up.
            try? session.setActive(false, options: .notifyOthersOnDeactivation)
        }
        call.resolve()
    }
}

/// Keeps the scheduled alarm notification quiet while the app is open —
/// the page's own alarm covers the foreground case.
class GymNotifDelegate: NSObject, UNUserNotificationCenterDelegate {
    public func userNotificationCenter(_ center: UNUserNotificationCenter,
                                       willPresent notification: UNNotification,
                                       withCompletionHandler completionHandler:
                                       @escaping (UNNotificationPresentationOptions) -> Void) {
        if notification.request.identifier == "gym.rest.alarm" {
            completionHandler([])
        } else {
            completionHandler([.banner, .sound])
        }
    }
}
