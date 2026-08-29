package expo.modules.photeoalbumpdf

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.ImageDecoder
import android.graphics.Paint
import android.graphics.Rect
import android.graphics.RectF
import android.graphics.pdf.PdfDocument
import android.graphics.pdf.PdfRenderer
import android.net.Uri
import android.os.Build
import android.os.ParcelFileDescriptor
import android.util.Log
import expo.modules.kotlin.exception.Exceptions
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import kotlinx.coroutines.CoroutineName
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.asCoroutineDispatcher
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.Executors
import kotlin.math.abs
import kotlin.math.ceil
import kotlin.math.floor
import kotlin.math.max
import kotlin.math.roundToInt

private const val DOCUMENT_FORMAT = "photeo-album-8x8-v2"
private const val PAGE_POINTS = 593
private const val TRIM_POINTS = 576.0
private const val BLEED_POINTS = 8.5
private const val SAFE_MARGIN_POINTS = 36.0
private const val TRIM_RASTER_SIZE = 2_400
private const val RASTER_PIXELS_PER_POINT = TRIM_RASTER_SIZE / TRIM_POINTS
private const val DIAGNOSTIC_LOG = "photeo-diagnostics.log"
private const val MAX_DIAGNOSTIC_BYTES = 512L * 1024L
private const val MAX_PAGES = 200
private const val MAX_PLACEMENTS_PER_PAGE = 8
private const val MIN_RENDER_WIDTH = 128
private const val MAX_RENDER_WIDTH = 3_600

class PhoteoAlbumPdfModule : Module() {
  private val context: Context
    get() = appContext.reactContext ?: throw Exceptions.ReactContextLost()

  /**
   * Document work gets its own thread, for the same reason inference does.
   *
   * Expo runs EVERY AsyncFunction from EVERY module on one shared
   * HandlerThread ("expo.modules.AsyncFunctionQueue", see AppContext.kt). A
   * whole-album `generate` left on that queue blocks every other native call in
   * the app -- and this one is not a short call: it rasterises each page at
   * 2400x2400 for 300 DPI at the 8x8in trim.
   *
   * ONE thread, not a pool. Page rasterisation allocates the largest bitmaps
   * this app creates, on a device that has already OOMed during album build, so
   * overlapping two of them is exactly the thing to avoid. It also keeps
   * `generate` ordered against `renderPage` and `pageCount` without a lock:
   * viewing a page cannot race the document being rewritten underneath it.
   */
  private val documentScope = CoroutineScope(
    Executors.newSingleThreadExecutor().asCoroutineDispatcher() +
      SupervisorJob() +
      CoroutineName("photeo.albumpdf")
  )

  override fun definition() = ModuleDefinition {
    Name("PhoteoAlbumPdf")

    AsyncFunction("generate") { albumKey: String, documentJson: String ->
      val started = System.currentTimeMillis()
      try {
        val result = generateDocument(albumKey, documentJson)
        diagnostic(
          "[PhoteoAlbumPdf] generate pages=${result["pageCount"]} " +
            "elapsedMs=${System.currentTimeMillis() - started}",
        )
        result
      } catch (error: Throwable) {
        diagnostic(
          "[PhoteoAlbumPdf] generate failed elapsedMs=${System.currentTimeMillis() - started} " +
            "error=${error.javaClass.simpleName}",
        )
        throw error
      }
    }.runOnQueue(documentScope)

    AsyncFunction("pageCount") { uri: String ->
      openRenderer(uri) { renderer -> renderer.pageCount }
    }.runOnQueue(documentScope)

    AsyncFunction("renderPage") { uri: String, pageIndex: Int, requestedWidth: Int ->
      val started = System.currentTimeMillis()
      val result = renderPage(uri, pageIndex, requestedWidth)
      diagnostic(
        "[PhoteoAlbumPdf] render page=${pageIndex + 1} width=${result["width"]} " +
          "elapsedMs=${System.currentTimeMillis() - started}",
      )
      result
    }.runOnQueue(documentScope)
  }

  private fun generateDocument(albumKey: String, documentJson: String): Map<String, Any> {
    val spec = JSONObject(documentJson)
    require(spec.getString("format") == DOCUMENT_FORMAT) { "Unsupported album document format" }
    val pageWidth = spec.getInt("pageWidth")
    val pageHeight = spec.getInt("pageHeight")
    require(pageWidth == PAGE_POINTS && pageHeight == PAGE_POINTS) { "Invalid page dimensions" }
    require(closeTo(spec.getDouble("bleed"), BLEED_POINTS)) { "Invalid page bleed" }
    require(closeTo(spec.getDouble("safeMargin"), SAFE_MARGIN_POINTS)) { "Invalid safe margin" }
    require(
      spec.getInt("rasterWidth") == TRIM_RASTER_SIZE &&
        spec.getInt("rasterHeight") == TRIM_RASTER_SIZE,
    ) { "Invalid trim raster dimensions" }
    val trimBox = spec.getJSONObject("trimBox")
    require(
      closeTo(trimBox.getDouble("x"), BLEED_POINTS) &&
        closeTo(trimBox.getDouble("y"), BLEED_POINTS) &&
        closeTo(trimBox.getDouble("width"), TRIM_POINTS) &&
        closeTo(trimBox.getDouble("height"), TRIM_POINTS),
    ) { "Invalid trim geometry" }

    val pages = spec.getJSONArray("pages")
    require(pages.length() in 1..MAX_PAGES) { "Album must contain 1 to $MAX_PAGES pages" }

    val directory = File(context.filesDir, "album-documents").apply { mkdirs() }
    val digest = sha256(documentJson).take(16)
    val prefix = "photeo-album-${safeKey(albumKey)}-"
    val destination = File(directory, "$prefix$digest.pdf")
    val specification = File(directory, "$prefix$digest.json")
    if (!destination.isFile || destination.length() == 0L) {
      val partial = File(directory, "${destination.name}.part")
      partial.delete()
      val pdf = PdfDocument()
      try {
        for (pageIndex in 0 until pages.length()) {
          val pageSpec = pages.getJSONObject(pageIndex)
          val info = PdfDocument.PageInfo.Builder(pageWidth, pageHeight, pageIndex + 1).create()
          val page = pdf.startPage(info)
          try {
            drawPage(page.canvas, pageSpec, pageWidth, pageHeight)
          } finally {
            pdf.finishPage(page)
          }
        }
        partial.outputStream().buffered().use { output -> pdf.writeTo(output) }
      } catch (error: Throwable) {
        partial.delete()
        throw error
      } finally {
        pdf.close()
      }
      require(partial.length() > 0L && partial.renameTo(destination)) {
        partial.delete()
        "Could not save album document"
      }
    }
    // drawPage replaces any metadata estimate with the effective DPI of the
    // bitmap actually embedded. Persist the executed plan, not the input JSON.
    writeSpec(specification, spec.toString())
    directory.listFiles()
      ?.filter {
        it != destination && it != specification && it.name.startsWith(prefix) &&
          (it.extension == "pdf" || it.extension == "json")
      }
      ?.forEach(File::delete)

    val effectiveDpi = minimumEffectiveDpi(pages)
    diagnostic(
      "[PhoteoAlbumPdf] geometry media=${pageWidth}pt trim=${TRIM_POINTS.toInt()}pt " +
        "bleed=${BLEED_POINTS}pt minEffectiveDpi=${effectiveDpi?.let { "%.1f".format(it) } ?: "unknown"}",
    )

    return mapOf(
      "pageCount" to pages.length(),
      "pageHeight" to pageHeight,
      "pageWidth" to pageWidth,
      "uri" to Uri.fromFile(destination).toString(),
    )
  }

  private fun drawPage(canvas: Canvas, page: JSONObject, pageWidth: Int, pageHeight: Int) {
    canvas.drawColor(Color.parseColor(page.getString("background")))
    val placements = page.getJSONArray("placements")
    require(placements.length() in 1..MAX_PLACEMENTS_PER_PAGE) { "Invalid placement count" }
    val imagePaint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG or Paint.DITHER_FLAG)
    val matPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.rgb(255, 253, 249) }

    for (index in 0 until placements.length()) {
      val placement = placements.getJSONObject(index)
      val frameJson = placement.getJSONObject("frame")
      val frame = RectF(
        frameJson.getDouble("x").toFloat(),
        frameJson.getDouble("y").toFloat(),
        (frameJson.getDouble("x") + frameJson.getDouble("width")).toFloat(),
        (frameJson.getDouble("y") + frameJson.getDouble("height")).toFloat(),
      )
      val mat = placement.getDouble("mat").toFloat()
      require(
        frame.left >= 0 && frame.top >= 0 && frame.right <= pageWidth && frame.bottom <= pageHeight &&
          frame.width() > mat * 2 && frame.height() > mat * 2,
      ) { "Placement is outside the page" }

      if (mat > 0) canvas.drawRect(frame, matPaint)
      val target = RectF(frame.left + mat, frame.top + mat, frame.right - mat, frame.bottom - mat)
      val targetWidth = rasterPixelsForPoints(target.width().toDouble())
      val targetHeight = rasterPixelsForPoints(target.height().toDouble())
      val uri = placement.getString("uri")
      val bitmap = decodeBitmap(uri, targetWidth, targetHeight)
        ?: throw IllegalStateException("One album photo could not be read")
      try {
        placement.put("effectiveDpi", effectiveDpi(bitmap, target))
        drawCover(canvas, bitmap, target, imagePaint)
      } finally {
        bitmap.recycle()
      }
    }
  }

  private fun drawCover(canvas: Canvas, bitmap: Bitmap, target: RectF, paint: Paint) {
    val sourceRatio = bitmap.width.toFloat() / bitmap.height.toFloat()
    val targetRatio = target.width() / target.height()
    val source = if (sourceRatio > targetRatio) {
      val width = (bitmap.height * targetRatio).roundToInt().coerceAtMost(bitmap.width)
      val left = (bitmap.width - width) / 2
      Rect(left, 0, left + width, bitmap.height)
    } else {
      val height = (bitmap.width / targetRatio).roundToInt().coerceAtMost(bitmap.height)
      val top = (bitmap.height - height) / 2
      Rect(0, top, bitmap.width, top + height)
    }
    canvas.drawBitmap(bitmap, source, target, paint)
  }

  private fun decodeBitmap(uriString: String, targetWidth: Int, targetHeight: Int): Bitmap? {
    val uri = Uri.parse(uriString)
    return try {
      if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
        val source = if (uri.scheme == "file") {
          ImageDecoder.createSource(File(requireNotNull(uri.path)))
        } else {
          ImageDecoder.createSource(context.contentResolver, uri)
        }
        ImageDecoder.decodeBitmap(source) { decoder, info, _ ->
          val scale = max(
            targetWidth.toDouble() / info.size.width,
            targetHeight.toDouble() / info.size.height,
          )
          val sample = floor(1.0 / scale).toInt().coerceAtLeast(1)
          decoder.setTargetSampleSize(sample)
          decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
        }
      } else {
        decodeBitmapLegacy(uri, targetWidth, targetHeight)
      }
    } catch (_error: Throwable) {
      null
    }
  }

  private fun decodeBitmapLegacy(uri: Uri, targetWidth: Int, targetHeight: Int): Bitmap? {
    fun open() = if (uri.scheme == "file") {
      File(requireNotNull(uri.path)).inputStream()
    } else {
      context.contentResolver.openInputStream(uri)
    }

    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    open()?.use { BitmapFactory.decodeStream(it, null, bounds) }
    if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
    var sample = 1
    while (
      bounds.outWidth / (sample * 2) >= targetWidth &&
      bounds.outHeight / (sample * 2) >= targetHeight
    ) sample *= 2
    val options = BitmapFactory.Options().apply { inSampleSize = sample }
    return open()?.use { BitmapFactory.decodeStream(it, null, options) }
  }

  private fun renderPage(uri: String, pageIndex: Int, requestedWidth: Int): Map<String, Any> {
    val width = requestedWidth.coerceIn(MIN_RENDER_WIDTH, MAX_RENDER_WIDTH)
    val cacheDirectory = File(context.cacheDir, "album-document-pages/${sha256(uri).take(16)}").apply { mkdirs() }

    return openRenderer(uri) { renderer ->
      require(pageIndex in 0 until renderer.pageCount) { "Page is outside the album" }
      renderer.openPage(pageIndex).use { page ->
        val height = (width * page.height.toDouble() / page.width).roundToInt().coerceAtLeast(1)
        val destination = File(cacheDirectory, "page-${pageIndex + 1}-$width.jpg")
        if (!destination.isFile || destination.length() == 0L) {
          val partial = File(cacheDirectory, "${destination.name}.part")
          partial.delete()
          val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
          try {
            bitmap.eraseColor(Color.WHITE)
            page.render(bitmap, null, null, PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY)
            partial.outputStream().buffered().use { output ->
              require(bitmap.compress(Bitmap.CompressFormat.JPEG, 95, output)) { "Could not encode album page" }
            }
          } finally {
            bitmap.recycle()
          }
          require(partial.renameTo(destination)) {
            partial.delete()
            "Could not cache album page"
          }
        }
        mapOf(
          "height" to height,
          "uri" to Uri.fromFile(destination).toString(),
          "width" to width,
        )
      }
    }
  }

  private fun <T> openRenderer(uriString: String, block: (PdfRenderer) -> T): T {
    val uri = Uri.parse(uriString)
    val descriptor = if (uri.scheme == "file") {
      ParcelFileDescriptor.open(File(requireNotNull(uri.path)), ParcelFileDescriptor.MODE_READ_ONLY)
    } else {
      requireNotNull(context.contentResolver.openFileDescriptor(uri, "r"))
    }
    return descriptor.use { file ->
      PdfRenderer(file).use(block)
    }
  }

  private fun safeKey(value: String): String {
    val clean = value.lowercase().replace(Regex("[^a-z0-9_-]+"), "-").trim('-').take(48)
    return clean.ifBlank { sha256(value).take(12) }
  }

  private fun sha256(value: String): String =
    MessageDigest.getInstance("SHA-256")
      .digest(value.toByteArray(Charsets.UTF_8))
      .joinToString("") { "%02x".format(it.toInt() and 0xff) }

  private fun closeTo(value: Double, expected: Double): Boolean = abs(value - expected) < 0.001

  private fun rasterPixelsForPoints(points: Double): Int =
    ceil(points * RASTER_PIXELS_PER_POINT).toInt().coerceAtLeast(1)

  private fun effectiveDpi(bitmap: Bitmap, target: RectF): Double {
    val sourceRatio = bitmap.width.toDouble() / bitmap.height.toDouble()
    val targetRatio = target.width().toDouble() / target.height().toDouble()
    val croppedWidth = if (sourceRatio > targetRatio) bitmap.height * targetRatio else bitmap.width.toDouble()
    val croppedHeight = if (sourceRatio > targetRatio) bitmap.height.toDouble() else bitmap.width / targetRatio
    val dpi = minOf(
      croppedWidth / (target.width() / 72.0),
      croppedHeight / (target.height() / 72.0),
    )
    return (dpi * 10.0).roundToInt() / 10.0
  }

  private fun minimumEffectiveDpi(pages: org.json.JSONArray): Double? {
    var minimum: Double? = null
    for (pageIndex in 0 until pages.length()) {
      val placements = pages.getJSONObject(pageIndex).getJSONArray("placements")
      for (placementIndex in 0 until placements.length()) {
        val placement = placements.getJSONObject(placementIndex)
        if (placement.isNull("effectiveDpi")) continue
        val dpi = placement.getDouble("effectiveDpi")
        require(dpi.isFinite() && dpi > 0) { "Invalid effective DPI" }
        minimum = minimum?.let { minOf(it, dpi) } ?: dpi
      }
    }
    return minimum
  }

  private fun writeSpec(destination: File, documentJson: String) {
    if (destination.isFile && destination.length() > 0L) return
    val partial = File(destination.parentFile, "${destination.name}.part")
    partial.delete()
    partial.writeText(documentJson)
    require(partial.length() > 0L && partial.renameTo(destination)) {
      partial.delete()
      "Could not save album document metadata"
    }
  }

  /** ColorOS drops logcat after 300 process lines, so measurements also go to the shared file. */
  private fun diagnostic(message: String) {
    Log.i("PhoteoAlbumPdf", message)
    try {
      val directory = context.getExternalFilesDir(null) ?: return
      val file = File(directory, DIAGNOSTIC_LOG)
      if (file.length() > MAX_DIAGNOSTIC_BYTES) file.writeText("")
      file.appendText("${System.currentTimeMillis()} $message\n")
    } catch (_error: Throwable) {
      // Observability must never change the document outcome.
    }
  }
}
