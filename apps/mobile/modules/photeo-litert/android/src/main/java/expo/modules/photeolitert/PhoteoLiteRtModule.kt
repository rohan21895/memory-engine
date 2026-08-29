package expo.modules.photeolitert

import android.net.Uri
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import org.tensorflow.lite.DataType
import org.tensorflow.lite.Interpreter
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder

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

  override fun definition() = ModuleDefinition {
    Name("PhoteoLiteRt")

    AsyncFunction("probeTinyClip") { modelUri: String ->
      synchronized(tinyClipLock) {
        tinyClip(modelUri)
        true
      }
    }

    AsyncFunction("runTinyClip") { modelUri: String, input: ByteArray ->
      require(input.size == TINY_CLIP_INPUT_BYTES) {
        "TinyCLIP input holds ${input.size} bytes, expected $TINY_CLIP_INPUT_BYTES"
      }
      synchronized(tinyClipLock) {
        invoke(tinyClip(modelUri), input)
      }
    }

    AsyncFunction("releaseTinyClip") {
      synchronized(tinyClipLock) {
        tinyClip?.close()
        tinyClip = null
        tinyClipPath = null
      }
    }

    AsyncFunction("probeFaceIdentity") { modelUri: String ->
      synchronized(faceLock) {
        face(modelUri)
        true
      }
    }

    AsyncFunction("runFaceIdentity") { modelUri: String, input: ByteArray ->
      require(input.size == FACE_INPUT_BYTES) {
        "MobileFaceNet input holds ${input.size} bytes, expected $FACE_INPUT_BYTES"
      }
      synchronized(faceLock) {
        invoke(face(modelUri), input)
      }
    }

    AsyncFunction("releaseFaceIdentity") {
      synchronized(faceLock) {
        face?.close()
        face = null
        facePath = null
      }
    }

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

    val loaded = Interpreter(File(path), Interpreter.Options().setNumThreads(1))
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

    val loaded = Interpreter(File(path), Interpreter.Options().setNumThreads(1))
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
