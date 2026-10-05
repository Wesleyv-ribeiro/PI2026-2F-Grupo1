package br.com.concoop

import android.content.Context
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Room
import androidx.room.RoomDatabase

@Entity(tableName = "queued_submissions")
data class QueuedSubmission(
    @PrimaryKey val id: String,
    val type: String,
    val payloadJson: String,
    val attachmentPath: String?,
    val attachmentMimeType: String?,
    val ownerUserId: Int,
    val createdAt: Long,
    val state: String = "pending",
    val lastError: String? = null,
)

@Entity(tableName = "cached_products")
data class CachedProduct(
    @PrimaryKey val id: Int,
    val title: String,
    val description: String,
    val price: String,
    val producer: String,
    val city: String,
)

@Entity(tableName = "cached_vets")
data class CachedVet(
    @PrimaryKey val id: Int,
    val name: String,
    val city: String,
    val bio: String,
)

@Entity(tableName = "cached_session")
data class CachedSession(
    @PrimaryKey val singletonId: Int = 0,
    val userId: Int,
    val name: String,
    val role: String,
)

@Dao
interface OfflineDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertSubmission(submission: QueuedSubmission)

    @Query("SELECT * FROM queued_submissions ORDER BY createdAt")
    suspend fun submissions(): List<QueuedSubmission>

    @Query("SELECT * FROM queued_submissions WHERE state IN ('pending', 'waiting_login', 'waiting_account') ORDER BY createdAt")
    suspend fun waitingSubmissions(): List<QueuedSubmission>

    @Query("SELECT COUNT(*) FROM queued_submissions")
    suspend fun submissionCount(): Int

    @Query("UPDATE queued_submissions SET state = :state, lastError = :error WHERE id = :id")
    suspend fun updateSubmissionState(id: String, state: String, error: String?)

    @Query("DELETE FROM queued_submissions WHERE id = :id")
    suspend fun deleteSubmission(id: String)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun replaceProducts(products: List<CachedProduct>)

    @Query("DELETE FROM cached_products")
    suspend fun clearProducts()

    @Query("SELECT * FROM cached_products ORDER BY id DESC")
    suspend fun cachedProducts(): List<CachedProduct>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun replaceVets(vets: List<CachedVet>)

    @Query("DELETE FROM cached_vets")
    suspend fun clearVets()

    @Query("SELECT * FROM cached_vets ORDER BY id DESC")
    suspend fun cachedVets(): List<CachedVet>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun saveSession(session: CachedSession)

    @Query("SELECT * FROM cached_session WHERE singletonId = 0 LIMIT 1")
    suspend fun cachedSession(): CachedSession?

    @Query("DELETE FROM cached_session")
    suspend fun clearSession()
}

@Database(
    entities = [QueuedSubmission::class, CachedProduct::class, CachedVet::class, CachedSession::class],
    version = 1,
    exportSchema = false,
)
abstract class OfflineDatabase : RoomDatabase() {
    abstract fun offlineDao(): OfflineDao

    companion object {
        @Volatile
        private var instance: OfflineDatabase? = null

        fun get(context: Context): OfflineDatabase = instance ?: synchronized(this) {
            instance ?: Room.databaseBuilder(
                context.applicationContext,
                OfflineDatabase::class.java,
                "concoop-offline.db",
            ).build().also { instance = it }
        }
    }
}