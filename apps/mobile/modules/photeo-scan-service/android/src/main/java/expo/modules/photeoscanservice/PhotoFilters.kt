package expo.modules.photeoscanservice

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.ColorMatrix
import android.graphics.ColorMatrixColorFilter
import android.graphics.Paint

/**
 * Album photo filters, as ColorMatrix presets.
 *
 * These are deliberately Android's own primitive rather than a new dependency.
 * A ColorMatrix is a 4x5 matrix applied per pixel by the framework's Skia
 * pipeline, so a filtered draw costs about as much as an ordinary one and needs
 * no GL context, no Skia binding, and no second copy of the image in JS.
 *
 * The set is small on purpose. Seven looks a photographer would recognise beat
 * thirty that differ by a hue degree, and every one here has to survive being
 * seen next to the untouched original at [ORIGINAL].
 */
internal enum class PhotoFilter(val id: String) {
  ORIGINAL("original"),
  MONO("mono"),
  NOIR("noir"),
  WARM("warm"),
  COOL("cool"),
  FADE("fade"),
  VIVID("vivid");

  companion object {
    /** Unknown ids fall back to the original rather than failing the request. */
    fun fromId(value: String): PhotoFilter =
      entries.firstOrNull { it.id.equals(value, ignoreCase = true) } ?: ORIGINAL
  }
}

/**
 * Contrast about the mid-grey pivot.
 *
 * Scaling a channel alone pivots at black, which darkens everything as it
 * contrasts. Offsetting by `128 * (1 - scale)` moves the pivot to mid-grey, so
 * highlights and shadows separate around the subject instead of the frame
 * simply going dim.
 */
private fun contrastMatrix(scale: Float): ColorMatrix {
  val shift = 128f * (1f - scale)
  return ColorMatrix(
    floatArrayOf(
      scale, 0f, 0f, 0f, shift,
      0f, scale, 0f, 0f, shift,
      0f, 0f, scale, 0f, shift,
      0f, 0f, 0f, 1f, 0f,
    ),
  )
}

/** Per-channel gain plus lift. Gain tints, lift is what makes a matte black. */
private fun channelMatrix(
  redGain: Float,
  greenGain: Float,
  blueGain: Float,
  lift: Float = 0f,
): ColorMatrix = ColorMatrix(
  floatArrayOf(
    redGain, 0f, 0f, 0f, lift,
    0f, greenGain, 0f, 0f, lift,
    0f, 0f, blueGain, 0f, lift,
    0f, 0f, 0f, 1f, 0f,
  ),
)

private fun saturationMatrix(amount: Float): ColorMatrix =
  ColorMatrix().apply { setSaturation(amount) }

private fun matrixFor(filter: PhotoFilter): ColorMatrix? = when (filter) {
  PhotoFilter.ORIGINAL -> null
  PhotoFilter.MONO -> saturationMatrix(0f)
  // Black and white with the shoulder a mono conversion alone never has.
  PhotoFilter.NOIR -> saturationMatrix(0f).apply { postConcat(contrastMatrix(1.28f)) }
  // Late afternoon: red carries, blue retreats, and skin stays skin.
  PhotoFilter.WARM -> channelMatrix(1.09f, 1.02f, 0.91f)
  PhotoFilter.COOL -> channelMatrix(0.93f, 0.99f, 1.10f)
  // Matte: blacks lifted off zero, colour pulled back a little.
  PhotoFilter.FADE -> saturationMatrix(0.82f).apply { postConcat(channelMatrix(0.94f, 0.94f, 0.96f, 18f)) }
  // Saturation alone reads as garish, so the contrast comes with it.
  PhotoFilter.VIVID -> saturationMatrix(1.38f).apply { postConcat(contrastMatrix(1.08f)) }
}

/**
 * Returns a filtered copy, or the SAME bitmap when the filter is a no-op.
 *
 * Returning the input unchanged for [PhotoFilter.ORIGINAL] is what lets the
 * caller treat "no filter" as an ordinary case instead of branching around it.
 * The caller therefore must not assume the result is a distinct object; the
 * recycle contract is "recycle the result, and the input only if it differs",
 * which is exactly what `applyFilterInPlace` below does for you.
 */
internal fun filteredCopy(bitmap: Bitmap, filter: PhotoFilter): Bitmap {
  val matrix = matrixFor(filter) ?: return bitmap
  val output = Bitmap.createBitmap(bitmap.width, bitmap.height, Bitmap.Config.ARGB_8888)
  val paint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG).apply {
    colorFilter = ColorMatrixColorFilter(matrix)
  }
  Canvas(output).drawBitmap(bitmap, 0f, 0f, paint)
  return output
}

/**
 * Applies [filter] and recycles the source if a new bitmap was produced.
 *
 * Album filtering runs on the same phone that hit a 268 MB heap ceiling during
 * analysis, so an intermediate full-size bitmap left for the collector is not
 * an abstraction worth having.
 */
internal fun applyFilterInPlace(bitmap: Bitmap, filter: PhotoFilter): Bitmap {
  val filtered = filteredCopy(bitmap, filter)
  if (filtered !== bitmap) bitmap.recycle()
  return filtered
}
