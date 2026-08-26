package expo.modules.photeoscanservice

import android.app.NotificationManager
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import expo.modules.kotlin.exception.Exceptions
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import java.io.File

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

    /**
     * Whether Android will already let this app work with the screen off.
     *
     * Cheap and side-effect free, so the caller can ask every time and prompt
     * only when the answer is no.
     */
    Function("isBatteryUnrestricted") {
      try {
        val power = context.getSystemService(Context.POWER_SERVICE) as PowerManager
        power.isIgnoringBatteryOptimizations(context.packageName)
      } catch (error: Throwable) {
        // Unknown reads as "already fine": this only gates whether to ASK, and
        // nagging on every launch is worse than missing one prompt.
        true
      }
    }

    /**
     * Asks Android to exempt this app from battery optimisation.
     *
     * This is the system dialog, so the user grants or refuses in one tap and
     * the app never sees a credential or writes a setting itself.
     *
     * Two honest limits, both handled rather than hidden:
     *
     * 1. ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS is the direct "allow?"
     *    dialog, but some OEM builds refuse it. The fallback opens the settings
     *    list instead, which always resolves.
     * 2. It covers Android's own Doze whitelist and NOT ColorOS's separate
     *    "sleep standby optimisation", which is a proprietary layer on top. So
     *    granting this is necessary and may not be sufficient on this device;
     *    `openOemBatterySettings` exists for the rest.
     */
    AsyncFunction("requestBatteryUnrestricted") {
      val direct =
        Intent(
          Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
          Uri.parse("package:${context.packageName}"),
        )
          .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
      val fallback =
        Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)
          .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
      for (intent in listOf(direct, fallback)) {
        try {
          context.startActivity(intent)
          return@AsyncFunction true
        } catch (error: ActivityNotFoundException) {
          // Try the next one.
        } catch (error: Throwable) {
          return@AsyncFunction false
        }
      }
      false
    }

    /**
     * Opens this app's own OS settings page, which is where every OEM's extra
     * background restrictions live.
     *
     * ColorOS keeps "sleep standby optimisation" and auto-start behind its own
     * screens, and there is no public intent that toggles them -- they are
     * deliberately not automatable, which is why they cannot be set from adb
     * either. Landing the user on the right page is the most any app can do.
     */
    AsyncFunction("openAppSettings") {
      try {
        context.startActivity(
          Intent(
            Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
            Uri.parse("package:${context.packageName}"),
          )
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        )
        true
      } catch (error: Throwable) {
        false
      }
    }

    /**
     * Copies a file out of the app's private storage into its external files
     * directory, where `adb pull` can reach it without a debuggable build.
     *
     * This exists so clustering can be tuned against the real library offline.
     * Every threshold experiment otherwise costs a rebuild plus a five-minute
     * on-device recluster, which is why guesses kept winning over measurements.
     * Returns the destination path, or null when there is nothing to copy.
     */
    AsyncFunction("exportPrivateFile") { name: String ->
      try {
        val source = File(context.filesDir, name)
        if (!source.isFile) return@AsyncFunction null
        val directory = context.getExternalFilesDir(null) ?: return@AsyncFunction null
        val destination = File(directory, name)
        // The index runs to tens of megabytes, so an unconditional copy on every
        // launch is a real cost on the user's phone for a diagnostic they did
        // not ask for. Copy only what is actually new.
        if (destination.isFile && destination.lastModified() >= source.lastModified()) {
          return@AsyncFunction destination.absolutePath
        }
        source.copyTo(destination, overwrite = true)
        destination.absolutePath
      } catch (error: Throwable) {
        null
      }
    }
  }
}
