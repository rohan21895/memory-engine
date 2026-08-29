package expo.modules.photeoscanservice

import android.content.ContentUris
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageDecoder
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import android.util.Size
import java.io.File
import java.util.concurrent.ConcurrentHashMap
import kotlin.math.max
import kotlin.math.roundToInt

internal enum class ThumbnailSource {
  CACHE,
  MEDIA_STORE,
  BOUNDED_FALLBACK,
  MISSING,
}

internal data class ResolvedThumbnail(
  val uri: String?,
  val source: ThumbnailSource,
  val width: Int = 0,
  val height: Int = 0,
  val decodeNanos: Long = 0L,
  val bitmapBytes: Long = 0L,
)

private const val TILE_MAX_EDGE = 1024
private const val ANALYSIS_MAX_EDGE = 1280
private const val TILE_JPEG_QUALITY = 85
private const val ANALYSIS_JPEG_QUALITY = 94

/** One lock per cache key prevents duplicate bitmaps for the same visible tile. */
private val thumbnailLocks = ConcurrentHashMap<String, Any>()

/**
 * Resolves a small on-disk JPEG for a MediaStore id.
 *
 * The normal path is ContentResolver.loadThumbnail: Android's media provider
 * returns its thumbnail without handing this process the full original. If an
 * OEM/provider has no thumbnail, ImageDecoder is allowed to open the source but
 * is given the final target dimensions before it decodes any pixels. That
 * fallback can therefore never allocate a full-resolution bitmap.
 */
internal fun resolveMediaStoreThumbnail(
  context: Context,
  assetId: String,
  requestedEdge: Int,
): ResolvedThumbnail {
  return resolveMediaStoreImage(
    context = context,
    assetId = assetId,
    requestedEdge = requestedEdge,
    maxEdge = TILE_MAX_EDGE,
    cacheDirectory = "thumbs",
    jpegQuality = TILE_JPEG_QUALITY,
  )
}

/**
 * Resolves the shared album-analysis proxy without handing the original URI to
 * Expo's image stack. Keep this separate from the tile contract: album analysis
 * requires a 1280 px long edge and quality 94, while tiles stop at 1024/85.
 */
internal fun resolveMediaStoreAnalysisProxy(
  context: Context,
  assetId: String,
  requestedEdge: Int,
): ResolvedThumbnail {
  return resolveMediaStoreImage(
    context = context,
    assetId = assetId,
    requestedEdge = requestedEdge,
    maxEdge = ANALYSIS_MAX_EDGE,
    cacheDirectory = "analysis-proxies",
    jpegQuality = ANALYSIS_JPEG_QUALITY,
  )
}

/**
 * Resolves a filtered copy for the album review strip and the finished album.
 *
 * Filtered results are cached under their own key, so the picker can re-show a
 * look instantly and the unfiltered proxy above keeps the exact cache path it
 * had before filters existed. Quality matches the analysis proxy because a
 * filtered photo is what actually lands in the album.
 */
internal fun resolveFilteredPhoto(
  context: Context,
  assetId: String,
  filter: PhotoFilter,
  requestedEdge: Int,
): ResolvedThumbnail {
  return resolveMediaStoreImage(
    context = context,
    assetId = assetId,
    requestedEdge = requestedEdge,
    maxEdge = ANALYSIS_MAX_EDGE,
    cacheDirectory = "filtered",
    jpegQuality = ANALYSIS_JPEG_QUALITY,
    filter = filter,
  )
}

private fun resolveMediaStoreImage(
  context: Context,
  assetId: String,
  requestedEdge: Int,
  maxEdge: Int,
  cacheDirectory: String,
  jpegQuality: Int,
  filter: PhotoFilter = PhotoFilter.ORIGINAL,
): ResolvedThumbnail {
  val id = assetId.toLongOrNull() ?: return ResolvedThumbnail(null, ThumbnailSource.MISSING)
  val edge = requestedEdge.coerceIn(64, maxEdge)
  // ORIGINAL keeps the pre-filter filename so already-cached tiles still hit.
  val suffix = if (filter == PhotoFilter.ORIGINAL) "" else "_${filter.id}"
  val cached = File(context.cacheDir, "$cacheDirectory/${id}_$edge$suffix.jpg")
  cached.cachedDimensions()?.let { dimensions ->
    return ResolvedThumbnail(
      uri = Uri.fromFile(cached).toString(),
      source = ThumbnailSource.CACHE,
      width = dimensions.first,
      height = dimensions.second,
    )
  }

  val cacheKey = "$cacheDirectory/${id}_$edge$suffix"
  val lock = thumbnailLocks.computeIfAbsent(cacheKey) { Any() }
  try {
    return synchronized(lock) {
      cached.cachedDimensions()?.let { dimensions ->
        return@synchronized ResolvedThumbnail(
          uri = Uri.fromFile(cached).toString(),
          source = ThumbnailSource.CACHE,
          width = dimensions.first,
          height = dimensions.second,
        )
      }

      val source = ContentUris.withAppendedId(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, id)
      val decodeStarted = System.nanoTime()
      val decoded = decodeThumbnail(context, source, id, edge)
      val decodeNanos = System.nanoTime() - decodeStarted
      if (decoded == null) {
        return@synchronized ResolvedThumbnail(
          uri = null,
          source = ThumbnailSource.MISSING,
          decodeNanos = decodeNanos,
        )
      }

      // Filtering here rather than at draw time means the album, the PDF and
      // the share sheet all get the same pixels, and the look survives export.
      val bitmap = applyFilterInPlace(decoded.first, filter)
      val bitmapBytes = bitmap.allocationByteCount.toLong()
      try {
        if (!writeAtomically(bitmap, cached, jpegQuality)) {
          return@synchronized ResolvedThumbnail(
            uri = null,
            source = ThumbnailSource.MISSING,
            decodeNanos = decodeNanos,
            bitmapBytes = bitmapBytes,
          )
        }
        ResolvedThumbnail(
          uri = Uri.fromFile(cached).toString(),
          source = decoded.second,
          width = bitmap.width,
          height = bitmap.height,
          decodeNanos = decodeNanos,
          bitmapBytes = bitmapBytes,
        )
      } finally {
        bitmap.recycle()
      }
    }
  } finally {
    thumbnailLocks.remove(cacheKey, lock)
  }
}

private fun decodeThumbnail(
  context: Context,
  source: Uri,
  id: Long,
  edge: Int,
): Pair<Bitmap, ThumbnailSource>? {
  if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
    try {
      val bitmap = context.contentResolver.loadThumbnail(source, Size(edge, edge), null)
      return boundUnexpectedProviderBitmap(bitmap, edge) to ThumbnailSource.MEDIA_STORE
    } catch (_: Throwable) {
      // Some providers have no thumbnail. The target-sized decoder below is
      // the bounded fallback; never pass the original URI back to the tile.
    }
  }

  if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
    try {
      val bitmap = decodeTargetSized(context, source, edge)
      return bitmap to ThumbnailSource.BOUNDED_FALLBACK
    } catch (_: Throwable) {
      // Deleted/corrupt/inaccessible media becomes a quiet missing tile.
    }
  } else {
    try {
      @Suppress("DEPRECATION")
      val bitmap = MediaStore.Images.Thumbnails.getThumbnail(
        context.contentResolver,
        id,
        MediaStore.Images.Thumbnails.MINI_KIND,
        null,
      )
      if (bitmap != null) {
        return boundUnexpectedProviderBitmap(bitmap, edge) to ThumbnailSource.BOUNDED_FALLBACK
      }
    } catch (_: Throwable) {
      // Old Android has no target-sized decoder. Missing is safer than an
      // unbounded original decode on a device already known to be memory-tight.
    }
  }
  return null
}

/** API 28+ decoder whose output is bounded before pixel allocation. */
private fun decodeTargetSized(context: Context, source: Uri, edge: Int): Bitmap {
  return ImageDecoder.decodeBitmap(ImageDecoder.createSource(context.contentResolver, source)) {
      decoder, info, _ ->
    val sourceWidth = max(1, info.size.width)
    val sourceHeight = max(1, info.size.height)
    val scale = minOf(1.0, edge.toDouble() / max(sourceWidth, sourceHeight).toDouble())
    val width = max(1, (sourceWidth * scale).roundToInt())
    val height = max(1, (sourceHeight * scale).roundToInt())
    decoder.setTargetSize(width, height)
    decoder.setAllocator(ImageDecoder.ALLOCATOR_SOFTWARE)
    decoder.setMemorySizePolicy(ImageDecoder.MEMORY_POLICY_LOW_RAM)
    decoder.setOnPartialImageListener { false }
  }
}

/** Defensive only: loadThumbnail is contracted to honor Size, but OEMs vary. */
private fun boundUnexpectedProviderBitmap(bitmap: Bitmap, edge: Int): Bitmap {
  if (bitmap.width <= edge && bitmap.height <= edge) return bitmap
  val scale = edge.toDouble() / max(bitmap.width, bitmap.height).toDouble()
  val width = max(1, (bitmap.width * scale).roundToInt())
  val height = max(1, (bitmap.height * scale).roundToInt())
  val bounded = Bitmap.createScaledBitmap(bitmap, width, height, true)
  if (bounded !== bitmap) bitmap.recycle()
  return bounded
}

private fun File.isUsableThumbnail(): Boolean = isFile && length() > 0L

/** Reads only the cached JPEG header; the original MediaStore item is untouched. */
private fun File.cachedDimensions(): Pair<Int, Int>? {
  if (!isUsableThumbnail()) return null
  val options = BitmapFactory.Options().apply { inJustDecodeBounds = true }
  BitmapFactory.decodeFile(absolutePath, options)
  if (options.outWidth < 1 || options.outHeight < 1) return null
  return options.outWidth to options.outHeight
}

private fun writeAtomically(
  bitmap: Bitmap,
  destination: File,
  jpegQuality: Int,
): Boolean {
  destination.parentFile?.mkdirs()
  if (destination.exists() && !destination.isUsableThumbnail() && !destination.delete()) {
    return false
  }
  val partial = File(
    destination.parentFile,
    "${destination.name}.${Thread.currentThread().id}.part",
  )
  return try {
    val compressed = partial.outputStream().use { output ->
      bitmap.compress(Bitmap.CompressFormat.JPEG, jpegQuality, output)
    }
    if (!compressed || partial.length() <= 0L) return false
    if (partial.renameTo(destination)) return true
    // Another process/thread may have won the atomic publish race.
    destination.isUsableThumbnail()
  } catch (_: Throwable) {
    false
  } finally {
    partial.delete()
  }
}
