package expo.modules.photeoscanservice

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat

/**
 * Keeps the face scan running while the app is not on screen.
 *
 * Without this the scan simply stops. Android does not suspend a thread on
 * purpose, but it does put backgrounded processes into the cached state and,
 * from Android 12, freezes them outright -- so the JS thread doing detection
 * and embedding just stops being scheduled. A user who wants an 11,828-photo
 * library indexed would have to sit and watch it for half an hour, which is not
 * something anyone will do.
 *
 * A foreground service is the sanctioned exception, and the notification is the
 * price: Android allows unbounded work precisely because the user can see it
 * happening and can stop it. That is the same bargain behind the "Backing up
 * photos" notification every gallery app shows.
 *
 * Deliberately NOT START_STICKY. If the OS kills this service under memory
 * pressure, restarting it with a null intent would resume a scan the user never
 * asked to resume, with no UI attached to it. Scan progress is persisted to a
 * cursor, so the next launch continues where this left off -- losing the
 * service is recoverable, silently reviving it is surprising.
 */
class ScanForegroundService : Service() {
  companion object {
    const val CHANNEL_ID = "photeo-scan"
    const val NOTIFICATION_ID = 4711
    const val EXTRA_TITLE = "title"
    const val EXTRA_TEXT = "text"

    fun intent(context: Context, title: String, text: String): Intent =
      Intent(context, ScanForegroundService::class.java)
        .putExtra(EXTRA_TITLE, title)
        .putExtra(EXTRA_TEXT, text)
  }

  override fun onBind(intent: Intent?): IBinder? = null

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
