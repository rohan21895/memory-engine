package expo.modules.photeoscanservice

import android.app.NotificationManager
import android.content.Context
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import expo.modules.kotlin.exception.Exceptions
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition

/**
 * JS control over the scan's foreground service.
 *
 * Every function reports success rather than throwing on a platform that cannot
 * honour it. The scan must still run when the service is unavailable -- the
 * caller's job is to fall back to foreground-only scanning, and an exception
 * here would turn a degraded mode into a crash.
 */
class PhoteoScanServiceModule : Module() {
  private val context: Context
    get() = appContext.reactContext ?: throw Exceptions.ReactContextLost()

  private var running = false

  override fun definition() = ModuleDefinition {
    Name("PhoteoScanService")

    AsyncFunction("start") { title: String, text: String ->
      try {
        ContextCompat.startForegroundService(
          context,
          ScanForegroundService.intent(context, title, text),
        )
        running = true
        true
      } catch (error: Throwable) {
        // Throwing here would take down a scan that is perfectly capable of
        // running in the foreground. Report the failure and let JS decide.
        running = false
        false
      }
    }

    AsyncFunction("update") { title: String, text: String ->
      if (!running) return@AsyncFunction false
      try {
        val manager =
          context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        // Posting the same id replaces the notification in place. Re-issuing
        // startForegroundService for a progress tick would ask Android to start
        // an already-started service on every batch.
        manager.notify(
          ScanForegroundService.NOTIFICATION_ID,
          NotificationCompat.Builder(context, ScanForegroundService.CHANNEL_ID)
            .setContentTitle(title)
            .setContentText(text)
            .setSmallIcon(android.R.drawable.ic_menu_gallery)
            .setOngoing(true)
            .setSilent(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setCategory(NotificationCompat.CATEGORY_PROGRESS)
            .build(),
        )
        true
      } catch (error: Throwable) {
        false
      }
    }

    AsyncFunction("stop") {
      try {
        context.stopService(
          ScanForegroundService.intent(context, "", ""),
        )
      } catch (error: Throwable) {
        // Already gone is the outcome we wanted.
      }
      running = false
      true
    }

    Function("isSupported") { Build.VERSION.SDK_INT >= Build.VERSION_CODES.O }
  }
}
