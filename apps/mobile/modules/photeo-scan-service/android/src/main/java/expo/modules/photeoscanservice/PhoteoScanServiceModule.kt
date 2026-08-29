package expo.modules.photeoscanservice

import android.app.NotificationManager
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Debug
import android.os.PowerManager
import android.os.SystemClock
import android.provider.Settings
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import expo.modules.kotlin.exception.Exceptions
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import java.io.File
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicLong
import kotlinx.coroutines.CoroutineName
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.asCoroutineDispatcher

/**
 * JS control over the scan's foreground service.
 *
 * Every function reports success rather than throwing on a platform that cannot
 * honour it. The scan must still run when the service is unavailable -- the
 * caller's job is to fall back to foreground-only scanning, and an exception
 * here would turn a degraded mode into a crash.
 */
/**
 * Enough decodes in flight to keep the grid ahead of a fast scroll, few enough
 * that four full-size bitmaps are the most alive at once. Raising this trades
 * heap for latency on a device that has already OOMed once.
 */
private const val THUMBNAIL_THREADS = 4
private const val THUMBNAIL_LOG_INTERVAL = 50L

/** Aggregate-only diagnostics: no asset ids, paths, filenames, or EXIF. */
private object ThumbnailMetrics {
  private val count = AtomicLong()
  private val totalNanos = AtomicLong()
  private val maxTotalNanos = AtomicLong()
  private val decodeCount = AtomicLong()
  private val decodeNanos = AtomicLong()
  private val maxDecodeNanos = AtomicLong()
  private val cacheHits = AtomicLong()
  private val mediaStoreHits = AtomicLong()
  private val boundedFallbacks = AtomicLong()
  private val misses = AtomicLong()
  private val maxBitmapBytes = AtomicLong()
  private val maxSampledPssKb = AtomicLong()

  fun record(result: ResolvedThumbnail, elapsedNanos: Long) {
    totalNanos.addAndGet(elapsedNanos)
    if (result.source != ThumbnailSource.CACHE) {
      decodeCount.incrementAndGet()
      decodeNanos.addAndGet(result.decodeNanos)
    }
    updateMax(maxTotalNanos, elapsedNanos)
    updateMax(maxDecodeNanos, result.decodeNanos)
    updateMax(maxBitmapBytes, result.bitmapBytes)
    when (result.source) {
      ThumbnailSource.CACHE -> cacheHits.incrementAndGet()
      ThumbnailSource.MEDIA_STORE -> mediaStoreHits.incrementAndGet()
      ThumbnailSource.BOUNDED_FALLBACK -> boundedFallbacks.incrementAndGet()
      ThumbnailSource.MISSING -> misses.incrementAndGet()
    }

    val completed = count.incrementAndGet()
    if (completed % THUMBNAIL_LOG_INTERVAL != 0L) return
    updateMax(maxSampledPssKb, Debug.getPss().toLong())
    val decoded = maxOf(1L, decodeCount.get())
    Log.i(
      "PhoteoThumbnail",
      "count=$completed totalAvgMs=${millis(totalNanos.get(), completed)} " +
        "totalMaxMs=${millis(maxTotalNanos.get(), 1)} " +
        "decodeAvgMs=${millis(decodeNanos.get(), decoded)} " +
        "decodeMaxMs=${millis(maxDecodeNanos.get(), 1)} " +
        "cache=${cacheHits.get()} mediaStore=${mediaStoreHits.get()} " +
        "boundedFallback=${boundedFallbacks.get()} missing=${misses.get()} " +
        "maxBitmapKiB=${maxBitmapBytes.get() / 1024L} " +
        "maxSampledPssMiB=${maxSampledPssKb.get() / 1024L}",
    )
  }

  private fun updateMax(target: AtomicLong, candidate: Long) {
    var current = target.get()
    while (candidate > current && !target.compareAndSet(current, candidate)) {
      current = target.get()
    }
  }

  private fun millis(nanos: Long, divisor: Long): String =
    String.format(java.util.Locale.US, "%.1f", nanos.toDouble() / divisor / 1_000_000.0)
}

class PhoteoScanServiceModule : Module() {
  private val context: Context
    get() = appContext.reactContext ?: throw Exceptions.ReactContextLost()

  private var running = false

  /**
   * Expo runs EVERY AsyncFunction -- in every module in the app -- on one shared
   * HandlerThread ("expo.modules.AsyncFunctionQueue", see AppContext.kt). That
   * queue is serial, so the 40-second `clusterFaces` call blocks every thumbnail
   * behind it and the photo grid simply stops filling in while a recluster runs.
   * Thumbnails also serialise against each other: measured on the owner's phone,
   * the average resolution climbed 12ms -> 87ms as requests piled up, which is
   * queue wait, not decode cost.
   *
   * Two private scopes, so neither can starve the other or anything else.
   */
  private val thumbnailScope = CoroutineScope(
    Executors.newFixedThreadPool(THUMBNAIL_THREADS).asCoroutineDispatcher() +
      SupervisorJob() +
      CoroutineName("photeo.thumbnails")
  )

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
     * Writes one line to logcat, so a RELEASE build can be measured.
     *
     * React Native only bridges `console.log` to logcat in development. Every
     * diagnostic this app already prints -- `rebuildPeople 27850ms`, the scan
     * timings, the consolidation spike -- is therefore invisible on exactly the
     * build the owner runs, which is why "the app is slow" kept being answered
     * with guesses instead of numbers. This is the smallest thing that makes the
     * release build observable.
     *
     * Synchronous on purpose: an async hop would reorder lines relative to the
     * work they are timing, which is the one property a timeline needs.
     */
    Function("log") { message: String ->
      android.util.Log.i("Photeo", message)
      true
    }

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

    /**
     * Groups every face in the library at once and returns one label per face.
     *
     * Lives natively because it cannot live anywhere else: see `FaceGraph` for
     * the measurement, but the short version is that this pass ran seventeen
     * minutes under Hermes without finishing and 59 seconds under Node, and the
     * early-exit that was supposed to save it rejects 0.0% of real pairs.
     *
     * Returns null instead of throwing. An empty or malformed request must leave
     * the caller free to fall back to the TypeScript path rather than take down
     * a rebuild -- the fallback is slow, but slow beats a library that will not
     * group at all.
     *
     * SYNCHRONOUS on purpose, despite blocking the JS thread while it runs. The
     * TypeScript clusterer it replaces blocks that same thread for 175 seconds
     * on the owner's library today (`face-index-recluster-cost.test.ts`), so the
     * choice is not between a freeze and no freeze -- it is between a long one
     * and a short one. Staying synchronous keeps `rebuildPeople` and its six
     * callers exactly as they are, which matters: making a whole-library
     * regrouping re-entrant is its own change, and two rebuilds interleaving
     * over `index.people` would corrupt the grouping rather than slow it.
     * Internally the work is still spread across every core.
     */
    /**
     * A small on-disk thumbnail for one photo, as a `file://` URI.
     *
     * The grid currently paints `content://media/external/images/media/<id>`,
     * which is the FULL-RESOLUTION original: every ~120dp tile decodes a 12-50
     * megapixel JPEG. `ContentResolver.loadThumbnail` instead returns the
     * thumbnail MediaStore has already generated, which is why this is worth a
     * native hop rather than resizing in JS -- the saving is in never decoding
     * the original at all.
     *
     * Cached by id AND size in the app's cache directory, so a second visit to
     * a person costs one file read. The cache directory is used deliberately:
     * Android may reclaim it under storage pressure, and everything here is
     * regenerable.
     *
     * If MediaStore has no thumbnail, the fallback decoder receives its target
     * dimensions before allocating pixels. If even that fails, null tells the
     * tile to paint a quiet placeholder -- never the original.
     */
    AsyncFunction("thumbnailUri") { assetId: String, size: Int ->
      val started = SystemClock.elapsedRealtimeNanos()
      val result = resolveMediaStoreThumbnail(context, assetId, size)
      ThumbnailMetrics.record(result, SystemClock.elapsedRealtimeNanos() - started)
      result.uri
    }.runOnQueue(thumbnailScope)

    Function("clusterFaces") {
      embeddings: String,
      dim: Int,
      assetGroup: IntArray,
      bars: DoubleArray,
      seed: Int,
      rounds: Int,
      ->
      try {
        FaceGraph.cluster(embeddings, dim, assetGroup, bars, seed, rounds)
      } catch (error: Throwable) {
        null
      }
    }
  }
}
