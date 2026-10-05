package br.com.concoop

import android.content.Context
import android.webkit.CookieManager
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import java.io.DataOutputStream
import java.io.File
import java.io.FileInputStream
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID
import org.json.JSONObject

class OfflineSyncWorker(context: Context, parameters: WorkerParameters) : CoroutineWorker(context, parameters) {
    private val dao = OfflineDatabase.get(context).offlineDao()

    override suspend fun doWork(): Result {
        val baseUrl = BuildConfig.CONCOOP_URL.trimEnd('/')
        val cookie = runCatching { CookieManager.getInstance().getCookie(baseUrl) }.getOrNull()
        if (cookie.isNullOrBlank()) {
            dao.waitingSubmissions().forEach {
                dao.updateSubmissionState(it.id, "waiting_login", "Entre no portal para sincronizar.")
            }
            return Result.success()
        }

        val activeUser = try {
            requestSessionUser(baseUrl, cookie) ?: run {
                dao.waitingSubmissions().forEach {
                    dao.updateSubmissionState(it.id, "waiting_login", "Entre no portal para sincronizar.")
                }
                return Result.success()
            }
        } catch (_: IOException) {
            return Result.retry()
        }

        for (submission in dao.waitingSubmissions()) {
            if (submission.ownerUserId != activeUser) {
                dao.updateSubmissionState(
                    submission.id,
                    "waiting_account",
                    "Este envio pertence a outra conta. Entre nela no portal para sincronizar.",
                )
                continue
            }

            dao.updateSubmissionState(submission.id, "pending", null)
            val status = try {
                sendSubmission(baseUrl, cookie, submission)
            } catch (_: IOException) {
                return Result.retry()
            }

            when {
                status in 200..299 -> {
                    submission.attachmentPath?.let { File(it).delete() }
                    dao.deleteSubmission(submission.id)
                }
                status == 401 -> {
                    dao.updateSubmissionState(submission.id, "waiting_login", "Entre no portal para sincronizar.")
                    return Result.success()
                }
                status >= 500 || status == 408 || status == 429 -> return Result.retry()
                else -> dao.updateSubmissionState(
                    submission.id,
                    "error",
                    "O servidor recusou este envio (HTTP $status). Revise os dados no portal.",
                )
            }
        }
        return Result.success()
    }

    private fun requestSessionUser(baseUrl: String, cookie: String): Int? {
        val connection = (URL("$baseUrl/api/mobile/session").openConnection() as HttpURLConnection).apply {
            connectTimeout = 10_000
            readTimeout = 10_000
            setRequestProperty("Cookie", cookie)
            setRequestProperty("Accept", "application/json")
        }
        return try {
            if (connection.responseCode == 401) return null
            if (connection.responseCode !in 200..299) throw IOException("Session check failed")
            val body = connection.inputStream.bufferedReader(Charsets.UTF_8).use { it.readText() }
            JSONObject(body).getInt("id")
        } finally {
            connection.disconnect()
        }
    }

    private fun sendSubmission(baseUrl: String, cookie: String, submission: QueuedSubmission): Int {
        val boundary = "----CONCOOP-${UUID.randomUUID()}"
        val connection = (URL("$baseUrl/api/mobile/sync").openConnection() as HttpURLConnection).apply {
            connectTimeout = 15_000
            readTimeout = 30_000
            requestMethod = "POST"
            doOutput = true
            setRequestProperty("Cookie", cookie)
            setRequestProperty("Accept", "application/json")
            setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
        }
        return try {
            DataOutputStream(connection.outputStream).use { output ->
                writeField(output, boundary, "client_id", submission.id)
                writeField(output, boundary, "type", submission.type)
                val payload = JSONObject(submission.payloadJson)
                val keys = payload.keys()
                while (keys.hasNext()) {
                    val key = keys.next()
                    writeField(output, boundary, key, payload.optString(key))
                }
                submission.attachmentPath?.let { path ->
                    val file = File(path)
                    if (file.exists()) writeFile(
                        output,
                        boundary,
                        file,
                        submission.attachmentMimeType ?: "application/octet-stream",
                    )
                }
                output.writeBytes("--$boundary--\r\n")
                output.flush()
            }
            connection.responseCode
        } finally {
            connection.disconnect()
        }
    }

    private fun writeField(output: DataOutputStream, boundary: String, name: String, value: String) {
        output.writeBytes("--$boundary\r\n")
        output.writeBytes("Content-Disposition: form-data; name=\"$name\"\r\n\r\n")
        output.write(value.toByteArray(Charsets.UTF_8))
        output.writeBytes("\r\n")
    }

    private fun writeFile(output: DataOutputStream, boundary: String, file: File, mimeType: String) {
        output.writeBytes("--$boundary\r\n")
        output.writeBytes("Content-Disposition: form-data; name=\"product_image\"; filename=\"${file.name}\"\r\n")
        output.writeBytes("Content-Type: $mimeType\r\n\r\n")
        FileInputStream(file).use { it.copyTo(output) }
        output.writeBytes("\r\n")
    }
}

object OfflineSyncScheduler {
    private const val UNIQUE_WORK_NAME = "concoop-offline-sync"

    fun enqueue(context: Context) {
        val constraints = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build()
        val request = OneTimeWorkRequestBuilder<OfflineSyncWorker>()
            .setConstraints(constraints)
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(
            UNIQUE_WORK_NAME,
            ExistingWorkPolicy.APPEND_OR_REPLACE,
            request,
        )
    }
}