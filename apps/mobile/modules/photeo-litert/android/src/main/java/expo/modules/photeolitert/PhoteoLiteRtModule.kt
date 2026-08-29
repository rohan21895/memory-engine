package expo.modules.photeolitert

import android.net.Uri
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import kotlinx.coroutines.CoroutineName
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.asCoroutineDispatcher
import org.tensorflow.lite.DataType
import org.tensorflow.lite.Interpreter
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.Executors

private const val TINY_CLIP_INPUT_BYTES = 1 * 224 * 224 * 3 * Float.SIZE_BYTES
private const val FACE_INPUT_BYTES = 1 * 112 * 112 * 3 * Float.SIZE_BYTES
private const val EMBEDDING_BYTES = 512 * Float.SIZE_BYTES

/**
 * The two fp32 graphs whose invokes must not depend on a JavaScript runtime.
 *
 * This deliberately is not a general model host. The model files still come
 * from the existing Metro asset path, and the TypeScript wrappers still own all
 * preprocessing and output interpretation. Native code only validates the two
 * fixed tensor contracts, invokes LiteRT, and returns the output bytes.
 */
class PhoteoLiteRtModule : Module() {
  private val tinyClipLock = Any()
  private val faceLock = Any()

  private var tinyClipPath: String? = null
  private var tinyClip: Interpreter? = null
  private var facePath: String? = null
  private var face: Interpreter? = null

  /**
   * Inference gets its own thread, because otherwise it takes the app's.
   *
   * Expo runs EVERY AsyncFunction from EVERY module on one shared
   * HandlerThread ("expo.modules.AsyncFunctionQueue", see AppContext.kt). Left
   * on it, a model invoke blocks every other native call in the app, and is
   * blocked by them in turn -- which is the same defect already fixed for
   * thumbnails and clustering in PhoteoScanServiceModule.
   *
   * ONE thread rather than a pool, for two reasons. LiteRT already uses several
   * cores inside a single invoke, so a pool would only oversubscribe them. And
   * the two interpreters hold ~28 MB of fp32 weights between them on a device
   * that has already OOMed during album build; serialising invokes means at most
   * one set of intermediate tensors is ever live.
   *
   * The `synchronized` blocks below stay. They guard the interpreter fields
   * against `OnDestroy`, which does NOT run here.
   */
  private val inferenceScope = CoroutineScope(
    Executors.newSingleThreadExecutor().asCoroutineDispatcher() +
      SupervisorJob() +
      CoroutineName("photeo.litert")
  )

  override fun definition() = ModuleDefinition {
    Name("PhoteoLiteRt")

    AsyncFunction("probeTinyClip") { modelUri: String ->
      synchronized(tinyClipLock) {
        tinyClip(modelUri)
        true
      }
    }.runOnQueue(inferenceScope)

    AsyncFunction("runTinyClip") { modelUri: String, input: ByteArray ->
      require(input.size == TINY_CLIP_INPUT_BYTES) {
        "TinyCLIP input holds ${input.size} bytes, expected $TINY_CLIP_INPUT_BYTES"
      }
      synchronized(tinyClipLock) {
        invoke(tinyClip(modelUri), input)
      }
    }.runOnQueue(inferenceScope)

    AsyncFunction("releaseTinyClip") {
      synchronized(tinyClipLock) {
        tinyClip?.close()
        tinyClip = null
        tinyClipPath = null
      }
    }.runOnQueue(inferenceScope)

    AsyncFunction("probeFaceIdentity") { modelUri: String ->
      synchronized(faceLock) {
        face(modelUri)
        true
      }
    }.runOnQueue(inferenceScope)

    AsyncFunction("runFaceIdentity") { modelUri: String, input: ByteArray ->
      require(input.size == FACE_INPUT_BYTES) {
        "MobileFaceNet input holds ${input.size} bytes, expected $FACE_INPUT_BYTES"
      }
      synchronized(faceLock) {
        invoke(face(modelUri), input)
      }
    }.runOnQueue(inferenceScope)

    AsyncFunction("releaseFaceIdentity") {
      synchronized(faceLock) {
        face?.close()
        face = null
        facePath = null
      }
    }.runOnQueue(inferenceScope)

    OnDestroy {
      synchronized(tinyClipLock) {
        tinyClip?.close()
        tinyClip = null
        tinyClipPath = null
      }
      synchronized(faceLock) {
        face?.close()
        face = null
        facePath = null
      }
    }
  }

  private fun tinyClip(modelUri: String): Interpreter {
    val path = localPath(modelUri)
    if (tinyClip != null && tinyClipPath == path) return requireNotNull(tinyClip)
    tinyClip?.close()
    tinyClip = null
    tinyClipPath = null

    val loaded = Interpreter(File(path), interpreterOptions())
    try {
      require(loaded.inputTensorCount == 1 && loaded.outputTensorCount == 1) {
        "TinyCLIP must have exactly one input and one output"
      }
      val input = loaded.getInputTensor(0)
      val output = loaded.getOutputTensor(0)
      require(
        input.dataType() == DataType.FLOAT32 &&
          input.shape().contentEquals(intArrayOf(1, 224, 224, 3)),
      ) { "TinyCLIP input tensor contract changed" }
      require(
        output.dataType() == DataType.FLOAT32 && output.numElements() == 512,
      ) { "TinyCLIP output tensor contract changed" }
    } catch (error: Throwable) {
      loaded.close()
      throw error
    }
    tinyClipPath = path
    tinyClip = loaded
    return loaded
  }

  private fun face(modelUri: String): Interpreter {
    val path = localPath(modelUri)
    if (face != null && facePath == path) return requireNotNull(face)
    face?.close()
    face = null
    facePath = null

    val loaded = Interpreter(File(path), interpreterOptions())
    try {
      require(loaded.inputTensorCount == 1 && loaded.outputTensorCount == 1) {
        "MobileFaceNet must have exactly one input and one output"
      }
      val input = loaded.getInputTensor(0)
      val output = loaded.getOutputTensor(0)
      require(
        input.dataType() == DataType.FLOAT32 &&
          input.shape().contentEquals(intArrayOf(1, 112, 112, 3)),
      ) { "MobileFaceNet input tensor contract changed" }
      require(
        output.dataType() == DataType.FLOAT32 && output.numElements() == 512,
      ) { "MobileFaceNet output tensor contract changed" }
    } catch (error: Throwable) {
      loaded.close()
      throw error
    }
    facePath = path
    face = loaded
    return loaded
  }

  /**
   * Interpreter threads.
   *
   * This was `setNumThreads(1)`, with no comment and no measurement behind it,
   * on a phone with EIGHT cores clocked to 3.5-4.3 GHz. TinyCLIP is 75% of an
   * album build -- 312856ms of a 415114ms wall on the measured 300-photo run --
   * and every millisecond of it was spent on one core.
   *
   * Four, not eight. The models are not the only thing running: MoveNet has its
   * own runtime on the fast-tflite path, ML Kit detects faces, and the JS thread
   * still has to decode and normalise. Taking every core would move the queue
   * rather than shorten it.
   *
   * CAVEAT, recorded because it is the one piece of contrary evidence:
   * `docs/DEEP-ANALYSIS-TIMING.md` measured "4t ~= 1t" for these models -- on a
   * Mac, where TinyCLIP runs in 6.03 ms and fixed overhead dominates any
   * parallel gain. At 1042.9 ms on the device there is real work to divide.
   * That is a reason to CHECK the device number, not a reason to stay at one:
   * compare `tinyclip.model-inference` against its 1042.9 ms baseline.
   */
  private fun interpreterThreads(): Int {
    val cores = Runtime.getRuntime().availableProcessors()
    return cores.minus(2).coerceIn(1, 4)
  }

  private fun interpreterOptions(): Interpreter.Options =
    Interpreter.Options().setNumThreads(interpreterThreads())

  private fun invoke(interpreter: Interpreter, input: ByteArray): ByteArray {
    val inputBuffer = ByteBuffer
      .allocateDirect(input.size)
      .order(ByteOrder.nativeOrder())
    inputBuffer.put(input)
    inputBuffer.rewind()

    val outputBuffer = ByteBuffer
      .allocateDirect(EMBEDDING_BYTES)
      .order(ByteOrder.nativeOrder())
    interpreter.run(inputBuffer, outputBuffer)
    outputBuffer.rewind()
    return ByteArray(EMBEDDING_BYTES).also(outputBuffer::get)
  }

  private fun localPath(modelUri: String): String {
    val uri = Uri.parse(modelUri)
    require(uri.scheme == "file") { "LiteRT model must be a local file URI" }
    val path = requireNotNull(uri.path) { "LiteRT model URI has no path" }
    val file = File(path)
    require(file.isFile && file.length() > 0L) { "LiteRT model file is missing" }
    return file.canonicalPath
  }
}
