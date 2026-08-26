package expo.modules.photeoscanservice

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import androidx.core.app.NotificationCompat
import com.facebook.react.HeadlessJsTaskService
import com.facebook.react.bridge.Arguments
import com.facebook.react.jstasks.HeadlessJsTaskConfig

/**
 * Keeps the face scan running while the app is not on screen.
 *
 * This does TWO things, and both are needed. Keeping the process alive was the
 * obvious half and it was never sufficient: measured on device, a backgrounded
 * scan advanced 4 photos in 100 seconds where ~530 were expected, with the
 * service reporting itself foregrounded the whole time and zero face
 * detections attempted.
 *
 * The reason is that React Native stops the JS timer loop when the Activity
 * pauses. `JavaTimerManager.onHostPause` calls `clearFrameCallback`, so
 * `setTimeout` stops firing, and the scan loop awaits a `setTimeout(0)` yield
 * once per batch -- it parks there forever. The process was alive the whole
 * time; nothing was scheduled to run in it.
 *
 * The sanctioned way to hold that loop open is a headless JS task, so this is
 * a `HeadlessJsTaskService` as well as a foreground service.
 * `JavaTimerManager.onHeadlessJsTaskStart` calls `setChoreographerCallback`,
 * the exact counterpart of the `onHostPause` teardown. The task runs on the
 * app's EXISTING React context when the process is alive, which is what makes
 * this cheap: the scan cursor, the in-memory face index and every native
 * module are already there, so nothing has to be rehydrated or reconciled.
 *
 * The notification is the price of the foreground service: Android allows
 * unbounded background work precisely because the user can see it happening
 * and stop it. Same bargain as every gallery app's "Backing up photos".
 *
 * Deliberately NOT START_STICKY, and deliberately not the base class's
 * START_REDELIVER_INTENT. If the OS kills this under memory pressure,
 * restarting it would resume a scan the user never asked to resume, with no UI
 * attached. Progress is persisted to a cursor, so the next launch continues
 * where this left off -- losing the service is recoverable, silently reviving
 * it is surprising.
 */
class ScanForegroundService : HeadlessJsTaskService() {
  companion object {
    const val CHANNEL_ID = "photeo-scan"
    const val NOTIFICATION_ID = 4711
    const val EXTRA_TITLE = "title"
    const val EXTRA_TEXT = "text"

    /** Must match the key JS registers with `AppRegistry.registerHeadlessTask`. */
    const val TASK_KEY = "PhoteoScan"

    fun intent(context: Context, title: String, text: String): Intent =
      Intent(context, ScanForegroundService::class.java)
        .putExtra(EXTRA_TITLE, title)
        .putExtra(EXTRA_TEXT, text)
  }

  private var taskStarted = false

  /**
   * timeout 0: the task lives as long as the scan does, and an 11,828-photo
   * library takes about half an hour. The safeguard the timeout normally
   * provides is served instead by JS resolving the task when the scan ends, and
   * by the user being able to stop a visible foreground service.
   *
   * allowedInForeground: a scan usually STARTS on screen and only later gets
   * backgrounded. The base class crashes rather than running a task while a
   * host is resumed, so without this the common path is a crash on launch.
   */
  override fun getTaskConfig(intent: Intent?): HeadlessJsTaskConfig =
    HeadlessJsTaskConfig(TASK_KEY, Arguments.createMap(), 0L, true)

  override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
    val title = intent?.getStringExtra(EXTRA_TITLE) ?: "Photeo"
    val text = intent?.getStringExtra(EXTRA_TEXT) ?: "Organising your photos"
    ensureChannel()
    val notification = buildNotification(title, text)
    // Android 14 refuses startForeground without a declared type, and the type
    // has to match the manifest or the call throws rather than degrading.
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
      startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
    } else {
      startForeground(NOTIFICATION_ID, notification)
    }
    // Guarded because a redundant start would register a SECOND never-resolving
    // task, and the base class only stops itself once every task has finished.
    if (!taskStarted) {
      taskStarted = true
      super.onStartCommand(intent, flags, startId)
    }
    return START_NOT_STICKY
  }

  private fun ensureChannel() {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
    val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
    if (manager.getNotificationChannel(CHANNEL_ID) != null) return
    // LOW, not DEFAULT: this notification exists because Android requires one,
    // not because it is news. It must never make a sound or peek over what the
    // user is doing.
    val channel = NotificationChannel(
      CHANNEL_ID,
      "Organising photos",
      NotificationManager.IMPORTANCE_LOW,
    ).apply {
      description = "Shown while Photeo is grouping faces in the background."
      setShowBadge(false)
    }
    manager.createNotificationChannel(channel)
  }

  private fun buildNotification(title: String, text: String): Notification =
    NotificationCompat.Builder(this, CHANNEL_ID)
      .setContentTitle(title)
      .setContentText(text)
      // A stock platform icon rather than a bundled asset: this ships without
      // adding a drawable to every density bucket, and the notification is
      // functional furniture, not branding.
      .setSmallIcon(android.R.drawable.ic_menu_gallery)
      .setOngoing(true)
      .setSilent(true)
      .setPriority(NotificationCompat.PRIORITY_LOW)
      .setCategory(NotificationCompat.CATEGORY_PROGRESS)
      .build()
}
