package expo.modules.photeoscanservice

import android.util.Base64
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import kotlin.math.sqrt

/**
 * Chinese Whispers over every pair of faces, run natively.
 *
 * WHY THIS IS NOT IN JAVASCRIPT. The clustering itself was settled offline: this
 * is the pass that took Aastha from 27 tiles to one, and its arithmetic is a
 * faithful port, not a redesign. The problem was purely that it cannot run where
 * it lived. Building the graph is O(n^2) in faces, and on the owner's library
 * that is ~87 million pairs of 512 dimensions. Node ran it in 59 seconds; the
 * same code under Hermes burned SEVENTEEN MINUTES at 100% CPU on the phone
 * without finishing, because Hermes has no optimising JIT for a numeric loop
 * this hot. That is a ~17x gap, and no amount of pruning closes it -- the
 * Cauchy-Schwarz early exit that `boundedSimilarity` relies on was measured
 * rejecting 0.0% of real pairs, because these embeddings spread their energy
 * evenly across all 512 dimensions and the bound is never tight enough to fire.
 *
 * So the loop moves to where a loop can actually be compiled and threaded.
 *
 * THE DOT PRODUCT IS EXACT INTEGER ARITHMETIC. Embeddings are stored int8, and
 * a face is compared as a unit vector, so for stored bytes a and b:
 *
 *   cos = dot(a/127, b/127) / (|a/127| * |b/127|)
 *       = (sum_d a_d*b_d) / (sqrt(sum a^2) * sqrt(sum b^2))
 *
 * The 1/127 scaling cancels completely. The numerator is therefore a sum of
 * products of signed bytes -- at most 512 * 127 * 127 = 8.3M, comfortably inside
 * an Int -- so the hot loop carries NO floating-point rounding at all, and each
 * vector contributes one precomputed `1/sqrt(sum a^2)`. This is the same
 * `dot * inverseA * inverseB` shape `scaledSimilarity` uses in TypeScript, with
 * the accumulation done exactly rather than in float64.
 */
object FaceGraph {
  /**
   * Groups faces and returns one label per face; faces sharing a label are one
   * person. Labels are arbitrary ints, not indices into anything.
   *
   * @param embeddingsBase64 n*dim signed bytes, face-major.
   * @param dim components per face (512 for the shipped model).
   * @param assetGroup one int per face identifying its photograph. Two faces in
   *   the same photograph are different people and are never linked.
   * @param bars per-face similarity required to link. Passed in rather than
   *   derived here because the child/adult split is a logistic probe over the
   *   embedding that already lives in TypeScript, and one copy of that model is
   *   the only way it stays in step. The stricter of the two endpoints wins, so
   *   a child can never be pulled in on an adult's more forgiving bar.
   * @param seed fixes the visit order. Chinese Whispers needs the order
   *   shuffled, but this app shows the user named people and remembers merges
   *   they confirmed -- a grouping that reshuffles on every scan is not
   *   something anyone can trust. Same photographs, same people, always.
   */
  fun cluster(
    embeddingsBase64: String,
    dim: Int,
    assetGroup: IntArray,
    bars: DoubleArray,
    seed: Int,
    rounds: Int,
  ): IntArray {
    val count = assetGroup.size
    if (count == 0 || dim <= 0) return IntArray(0)
    require(bars.size == count) { "bars must carry one bar per face" }

    val bytes = Base64.decode(embeddingsBase64, Base64.DEFAULT)
    require(bytes.size >= count.toLong() * dim) {
      "embeddings hold ${bytes.size} bytes, need ${count.toLong() * dim}"
    }

    // 1 / |a|, so a comparison is one integer dot product and two multiplies.
    // A zero-length vector gets 0, which makes every similarity 0 and leaves the
    // face a singleton -- the same thing the TypeScript guards do, and the safe
    // direction: a split is a question the user can answer, a fusion is not.
    val inverse = DoubleArray(count)
    for (i in 0 until count) {
      var squared = 0L
      val base = i * dim
      for (d in 0 until dim) {
        val v = bytes[base + d].toInt()
        squared += (v * v).toLong()
      }
      inverse[i] = if (squared > 0L) 1.0 / sqrt(squared.toDouble()) else 0.0
    }

    val neighbours = buildGraph(bytes, count, dim, assetGroup, bars, inverse)
    return propagate(neighbours, count, seed, rounds)
  }

  /** One face's edges: targets ascending, weights aligned. */
  private class Row(var target: IntArray, var weight: DoubleArray, var size: Int)

  /**
   * Every edge above the bar, as one adjacency row per face.
   *
   * Each worker owns a contiguous BLOCK OF ROWS and computes each of its rows
   * against all n faces -- not the upper triangle. That doubles the dot products
   * and buys two things worth more than the time: no worker ever writes to
   * another's row, so there are no atomics and no locks; and every row comes out
   * in ascending neighbour order regardless of how the threads interleave. The
   * second is the important one. Label weights are summed as doubles, and
   * floating-point addition is not associative, so an adjacency order that
   * varied with thread timing would let identical libraries cluster differently
   * between runs. Determinism here is a product requirement, not tidiness.
   */
  private fun buildGraph(
    bytes: ByteArray,
    count: Int,
    dim: Int,
    assetGroup: IntArray,
    bars: DoubleArray,
    inverse: DoubleArray,
  ): Array<Row?> {
    val rows = arrayOfNulls<Row>(count)
    val workers = Runtime.getRuntime().availableProcessors().coerceIn(1, 8)
    val pool = Executors.newFixedThreadPool(workers)
    try {
      val stride = (count + workers - 1) / workers
      val jobs = (0 until workers).map { worker ->
        Runnable {
          val from = worker * stride
          val to = minOf(from + stride, count)
          for (i in from until to) {
            rows[i] = rowFor(bytes, i, count, dim, assetGroup, bars, inverse)
          }
        }
      }
      val futures = jobs.map { pool.submit(it) }
      // Bounded rather than indefinite: a wedged worker must surface as a
      // failure the caller can fall back from, not as an app that never returns.
      for (future in futures) future.get(30, TimeUnit.MINUTES)
    } finally {
      pool.shutdownNow()
    }
    return rows
  }

  private fun rowFor(
    bytes: ByteArray,
    i: Int,
    count: Int,
    dim: Int,
    assetGroup: IntArray,
    bars: DoubleArray,
    inverse: DoubleArray,
  ): Row {
    var target = IntArray(16)
    var weight = DoubleArray(16)
    var size = 0
    val inverseI = inverse[i]
    if (inverseI == 0.0) return Row(target, weight, 0)
    val base = i * dim
    val assetI = assetGroup[i]
    val barI = bars[i]
    for (j in 0 until count) {
      if (j == i) continue
      // Two faces in one photograph are different people.
      if (assetGroup[j] == assetI) continue
      val inverseJ = inverse[j]
      if (inverseJ == 0.0) continue
      val barJ = bars[j]
      val required = if (barI > barJ) barI else barJ
      var dot = 0
      var a = base
      var b = j * dim
      val end = base + dim
      while (a < end) {
        dot += bytes[a].toInt() * bytes[b].toInt()
        a += 1
        b += 1
      }
      val similarity = dot * inverseI * inverseJ
      if (similarity < required) continue
      if (size == target.size) {
        target = target.copyOf(size * 2)
        weight = weight.copyOf(size * 2)
      }
      target[size] = j
      weight[size] = similarity
      size += 1
    }
    return Row(target, weight, size)
  }

  /**
   * Label propagation. Each face repeatedly takes whichever label its neighbours
   * argue for most loudly, by summed similarity.
   *
   * This is what unites a person across years: her face as a newborn and her
   * face today need never resemble EACH OTHER, only the photographs in between.
   * That chaining is the whole reason this beats greedy assignment, and it is
   * also why the bar cannot simply be lowered -- chains that long start joining
   * different people.
   */
  private fun propagate(
    neighbours: Array<Row?>,
    count: Int,
    seed: Int,
    rounds: Int,
  ): IntArray {
    val labels = IntArray(count) { it }
    val order = seededOrder(count, seed)
    // Reused across every node so propagation allocates nothing per face.
    val seenLabel = IntArray(count)
    val total = DoubleArray(count)
    for (round in 0 until rounds) {
      var moved = 0
      for (i in order) {
        val row = neighbours[i] ?: continue
        val size = row.size
        if (size == 0) continue
        var distinct = 0
        for (k in 0 until size) {
          val label = labels[row.target[k]]
          if (total[label] == 0.0) {
            seenLabel[distinct] = label
            distinct += 1
          }
          total[label] += row.weight[k]
        }
        // First candidate always wins outright; afterwards a tie goes to the
        // smaller label, never to whichever happened to be visited first. Two
        // runs over the same photographs have to agree down to the tile.
        var best = labels[i]
        var bestWeight = Double.NEGATIVE_INFINITY
        for (k in 0 until distinct) {
          val label = seenLabel[k]
          val sum = total[label]
          if (sum > bestWeight || (sum == bestWeight && label < best)) {
            best = label
            bestWeight = sum
          }
          total[label] = 0.0
        }
        if (best != labels[i]) {
          labels[i] = best
          moved += 1
        }
      }
      if (moved == 0) break
    }
    return labels
  }

  /**
   * The same seeded Fisher-Yates the TypeScript uses, down to the constants, so
   * the native and fallback paths visit faces in the identical order.
   */
  private fun seededOrder(count: Int, seed: Int): IntArray {
    val order = IntArray(count) { it }
    var state = if (seed == 0) 1L else (seed.toLong() and 0xFFFFFFFFL)
    for (i in count - 1 downTo 1) {
      state = (state * 1664525L + 1013904223L) and 0xFFFFFFFFL
      val j = (state % (i + 1).toLong()).toInt()
      val swap = order[i]
      order[i] = order[j]
      order[j] = swap
    }
    return order
  }
}
