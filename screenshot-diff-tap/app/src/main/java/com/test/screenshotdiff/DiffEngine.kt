package com.test.screenshotdiff

import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.Rect
import kotlin.math.abs

object DiffEngine {

    private const val DIFF_WIDTH = 100
    private const val DIFF_HEIGHT = 220
    private const val GRID_COLS = 10
    private const val GRID_ROWS = 20
    private const val CELL_W = DIFF_WIDTH / GRID_COLS
    private const val CELL_H = DIFF_HEIGHT / GRID_ROWS
    private const val MAX_CLUSTER_CELLS = 4

    data class Result(val region: Rect, val confidence: String)

    /**
     * Compares [before] and [after] on a coarse grid and returns the bounding box of the
     * most-changed cluster, scaled into [targetWidth] x [targetHeight] coordinates.
     * Returns null if no meaningful change is found.
     */
    fun computeDiff(before: Bitmap, after: Bitmap, targetWidth: Int, targetHeight: Int): Result? {
        val beforeScaled = Bitmap.createScaledBitmap(before, DIFF_WIDTH, DIFF_HEIGHT, true)
        val afterScaled = Bitmap.createScaledBitmap(after, DIFF_WIDTH, DIFF_HEIGHT, true)
        try {
            val beforePixels = IntArray(DIFF_WIDTH * DIFF_HEIGHT)
            val afterPixels = IntArray(DIFF_WIDTH * DIFF_HEIGHT)
            beforeScaled.getPixels(beforePixels, 0, DIFF_WIDTH, 0, 0, DIFF_WIDTH, DIFF_HEIGHT)
            afterScaled.getPixels(afterPixels, 0, DIFF_WIDTH, 0, 0, DIFF_WIDTH, DIFF_HEIGHT)

            val scores = Array(GRID_ROWS) { DoubleArray(GRID_COLS) }
            for (row in 0 until GRID_ROWS) {
                for (col in 0 until GRID_COLS) {
                    var sum = 0L
                    val x0 = col * CELL_W
                    val y0 = row * CELL_H
                    for (y in y0 until y0 + CELL_H) {
                        val rowOffset = y * DIFF_WIDTH
                        for (x in x0 until x0 + CELL_W) {
                            val pb = beforePixels[rowOffset + x]
                            val pa = afterPixels[rowOffset + x]
                            sum += abs(Color.red(pb) - Color.red(pa)) +
                                abs(Color.green(pb) - Color.green(pa)) +
                                abs(Color.blue(pb) - Color.blue(pa))
                        }
                    }
                    scores[row][col] = sum.toDouble()
                }
            }

            var maxScore = -1.0
            var maxRow = 0
            var maxCol = 0
            val allScores = ArrayList<Double>(GRID_ROWS * GRID_COLS)
            for (row in 0 until GRID_ROWS) {
                for (col in 0 until GRID_COLS) {
                    val s = scores[row][col]
                    allScores.add(s)
                    if (s > maxScore) {
                        maxScore = s
                        maxRow = row
                        maxCol = col
                    }
                }
            }
            if (maxScore <= 0.0) return null

            // Flood-fill a small cluster of adjacent cells that are also clearly changed.
            val threshold = maxScore * 0.6
            val included = LinkedHashSet<Pair<Int, Int>>()
            included.add(maxRow to maxCol)
            val queue = ArrayDeque<Pair<Int, Int>>()
            queue.add(maxRow to maxCol)
            while (queue.isNotEmpty() && included.size < MAX_CLUSTER_CELLS) {
                val (r, c) = queue.removeFirst()
                val neighbors = listOf(r - 1 to c, r + 1 to c, r to c - 1, r to c + 1)
                for (n in neighbors) {
                    if (included.size >= MAX_CLUSTER_CELLS) break
                    val (nr, nc) = n
                    if (nr in 0 until GRID_ROWS && nc in 0 until GRID_COLS && n !in included) {
                        if (scores[nr][nc] >= threshold) {
                            included.add(n)
                            queue.add(n)
                        }
                    }
                }
            }

            val minRow = included.minOf { it.first }
            val maxRowIdx = included.maxOf { it.first }
            val minCol = included.minOf { it.second }
            val maxColIdx = included.maxOf { it.second }

            val scaleX = targetWidth.toFloat() / DIFF_WIDTH
            val scaleY = targetHeight.toFloat() / DIFF_HEIGHT
            val left = (minCol * CELL_W * scaleX).toInt()
            val top = (minRow * CELL_H * scaleY).toInt()
            val right = ((maxColIdx + 1) * CELL_W * scaleX).toInt()
            val bottom = ((maxRowIdx + 1) * CELL_H * scaleY).toInt()

            // High confidence only when the peak cell clearly stands out from cells outside
            // the chosen cluster; otherwise several regions changed similarly (ambiguous).
            val outsideCluster = allScores.filterIndexed { idx, _ ->
                val r = idx / GRID_COLS
                val c = idx % GRID_COLS
                (r to c) !in included
            }
            val nextBest = outsideCluster.maxOrNull() ?: 0.0
            val confidence = if (nextBest == 0.0 || maxScore > nextBest * 2.0) "high" else "low"

            return Result(Rect(left, top, right, bottom), confidence)
        } finally {
            beforeScaled.recycle()
            afterScaled.recycle()
        }
    }
}
