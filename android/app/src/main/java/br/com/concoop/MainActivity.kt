package br.com.concoop

import android.content.Intent
import android.os.Bundle
import android.webkit.CookieManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AddCircle
import androidx.compose.material.icons.filled.Home
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.text.NumberFormat
import java.util.Locale

class MainActivity : ComponentActivity() {
    private var resumeSignal by mutableIntStateOf(0)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            AgroLinkApp(
                baseUrl = BuildConfig.CONCOOP_URL,
                refreshSignal = resumeSignal,
                onOpenPortal = {
                    startActivity(Intent(this, WebAppActivity::class.java))
                },
            )
        }
    }

    override fun onResume() {
        super.onResume()
        resumeSignal++
        OfflineSyncScheduler.enqueue(this)
    }
}

private data class MobileProduct(
    val id: Int,
    val title: String,
    val description: String,
    val price: Double,
    val producer: String,
    val city: String,
)

data class MobileVet(
    val id: Int,
    val name: String,
    val city: String,
    val bio: String,
)

private data class MobileHome(
    val products: List<MobileProduct>,
    val vets: List<MobileVet>,
)

private suspend fun fetchMobileHome(baseUrl: String): MobileHome = withContext(Dispatchers.IO) {
    val endpoint = "${baseUrl.trimEnd('/')}/api/mobile/home"
    val connection = URL(endpoint).openConnection() as HttpURLConnection
    connection.connectTimeout = 10_000
    connection.readTimeout = 10_000
    connection.setRequestProperty("Accept", "application/json")

    try {
        val status = connection.responseCode
        if (status !in 200..299) {
            throw IllegalStateException("Servidor indisponível ($status)")
        }
        val payload = connection.inputStream.bufferedReader(Charsets.UTF_8).use { it.readText() }
        val json = JSONObject(payload)
        val products = json.getJSONArray("products").let { rows ->
            (0 until rows.length()).map { index ->
                val row = rows.getJSONObject(index)
                MobileProduct(
                    id = row.getInt("id"),
                    title = row.optString("title"),
                    description = row.optString("description"),
                    price = row.optDouble("price"),
                    producer = row.optString("producer_name"),
                    city = row.optString("city"),
                )
            }
        }
        val vets = json.getJSONArray("vets").let { rows ->
            (0 until rows.length()).map { index ->
                val row = rows.getJSONObject(index)
                MobileVet(
                    id = row.getInt("id"),
                    name = row.optString("name"),
                    city = row.optString("city"),
                    bio = row.optString("bio"),
                )
            }
        }
        MobileHome(products, vets)
    } finally {
        connection.disconnect()
    }
}

private suspend fun refreshMobileSession(baseUrl: String, dao: OfflineDao): CachedSession? =
    withContext(Dispatchers.IO) {
        val root = baseUrl.trimEnd('/')
        val cookie = runCatching { CookieManager.getInstance().getCookie(root) }.getOrNull()
        if (cookie.isNullOrBlank()) return@withContext dao.cachedSession()

        val connection = URL("$root/api/mobile/session").openConnection() as HttpURLConnection
        connection.connectTimeout = 10_000
        connection.readTimeout = 10_000
        connection.setRequestProperty("Cookie", cookie)
        connection.setRequestProperty("Accept", "application/json")
        try {
            val status = connection.responseCode
            if (status == 401) {
                dao.clearSession()
                return@withContext null
            }
            if (status !in 200..299) throw IllegalStateException("Sessão indisponível")
            val json = JSONObject(connection.inputStream.bufferedReader(Charsets.UTF_8).use { it.readText() })
            CachedSession(
                userId = json.getInt("id"),
                name = json.optString("name"),
                role = json.optString("role"),
            ).also { dao.saveSession(it) }
        } finally {
            connection.disconnect()
        }
    }

private fun cachedHome(products: List<CachedProduct>, vets: List<CachedVet>) = MobileHome(
    products = products.map {
        MobileProduct(it.id, it.title, it.description, it.price.toDoubleOrNull() ?: 0.0, it.producer, it.city)
    },
    vets = vets.map { MobileVet(it.id, it.name, it.city, it.bio) },
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun AgroLinkApp(baseUrl: String, refreshSignal: Int, onOpenPortal: () -> Unit) {
    val context = LocalContext.current
    val dao = remember(context) { OfflineDatabase.get(context).offlineDao() }
    val green = Color(0xFF245B3D)
    var home by remember { mutableStateOf<MobileHome?>(null) }
    var session by remember { mutableStateOf<CachedSession?>(null) }
    var pendingCount by remember { mutableIntStateOf(0) }
    var usingCache by remember { mutableStateOf(false) }
    var selectedPage by remember { mutableStateOf("browse") }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var search by remember { mutableStateOf("") }
    var reloadKey by remember { mutableIntStateOf(0) }

    LaunchedEffect(baseUrl, reloadKey, refreshSignal) {
        loading = true
        error = null
        val cachedProducts = dao.cachedProducts()
        val cachedVets = dao.cachedVets()
        if (cachedProducts.isNotEmpty() || cachedVets.isNotEmpty()) {
            home = cachedHome(cachedProducts, cachedVets)
            usingCache = true
        }
        try {
            val latest = fetchMobileHome(baseUrl)
            dao.clearProducts()
            dao.replaceProducts(latest.products.map {
                CachedProduct(it.id, it.title, it.description, it.price.toString(), it.producer, it.city)
            })
            dao.clearVets()
            dao.replaceVets(latest.vets.map { CachedVet(it.id, it.name, it.city, it.bio) })
            home = latest
            usingCache = false
        } catch (_: Exception) {
            if (home == null) {
                error = "Não há catálogo salvo. Conecte-se à internet para carregar os dados."
            }
        } finally {
            session = runCatching { refreshMobileSession(baseUrl, dao) }.getOrElse { dao.cachedSession() }
            pendingCount = dao.submissionCount()
            loading = false
        }
    }

    val query = search.trim().lowercase(Locale.getDefault())
    val products = home?.products.orEmpty().filter {
        query.isEmpty() || listOf(it.title, it.description, it.producer, it.city)
            .any { value -> value.lowercase(Locale.getDefault()).contains(query) }
    }
    val vets = home?.vets.orEmpty().filter {
        query.isEmpty() || listOf(it.name, it.city, it.bio)
            .any { value -> value.lowercase(Locale.getDefault()).contains(query) }
    }

    MaterialTheme(
        colorScheme = lightColorScheme(
            primary = green,
            secondary = Color(0xFFB66A2C),
            background = Color(0xFFF3F3E9),
            surface = Color.White,
            onSurface = Color(0xFF20271F),
        ),
    ) {
        Scaffold(
            topBar = {
                TopAppBar(
                    title = { Text("CONCOOP", fontWeight = FontWeight.Bold) },
                    actions = {
                        TextButton(onClick = onOpenPortal) { Text("Portal") }
                    },
                )
            },
            bottomBar = {
                NavigationBar {
                    NavigationBarItem(
                        selected = selectedPage == "browse",
                        onClick = { selectedPage = "browse" },
                        icon = { Icon(Icons.Filled.Home, contentDescription = "Explorar") },
                        label = { Text("Explorar") },
                    )
                    NavigationBarItem(
                        selected = selectedPage == "submit",
                        onClick = { selectedPage = "submit" },
                        icon = { Icon(Icons.Filled.AddCircle, contentDescription = "Novo envio") },
                        label = { Text("Novo envio${if (pendingCount > 0) " ($pendingCount)" else ""}") },
                    )
                }
            },
            containerColor = MaterialTheme.colorScheme.background,
        ) { contentPadding ->
            if (selectedPage == "submit") {
                OfflineSubmissionScreen(
                    dao = dao,
                    session = session,
                    vets = home?.vets.orEmpty(),
                    onOpenPortal = onOpenPortal,
                    onQueueChanged = {
                        pendingCount = dao.submissionCount()
                    },
                    modifier = Modifier.padding(contentPadding),
                )
            } else {
                LazyColumn(
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(
                        start = 16.dp,
                        top = contentPadding.calculateTopPadding() + 12.dp,
                        end = 16.dp,
                        bottom = 24.dp,
                    ),
                    verticalArrangement = Arrangement.spacedBy(14.dp),
                ) {
                    if (usingCache) {
                        item {
                            Text(
                                "Offline: exibindo o último catálogo salvo. $pendingCount envio(s) aguardando sincronização.",
                                color = Color(0xFF74551A),
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .background(Color(0xFFF7EAC5), RoundedCornerShape(12.dp))
                                    .padding(12.dp),
                            )
                        }
                    }
                    item { WelcomePanel(onOpenPortal) }
                    item {
                        OutlinedTextField(
                            value = search,
                            onValueChange = { search = it },
                            modifier = Modifier.fillMaxWidth(),
                            singleLine = true,
                            shape = RoundedCornerShape(14.dp),
                            label = { Text("Buscar produtos ou veterinários") },
                        )
                    }
                    item { SectionHeading("Produtos em destaque", "${products.size} encontrados") }
                    if (loading && home == null) {
                        item {
                            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                                CircularProgressIndicator()
                                Text("Carregando o catálogo", modifier = Modifier.padding(top = 8.dp))
                            }
                        }
                    } else if (error != null && home == null) {
                        item {
                            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                Text(error.orEmpty(), color = MaterialTheme.colorScheme.error)
                                Button(onClick = { reloadKey++ }) { Text("Tentar novamente") }
                            }
                        }
                    } else if (products.isEmpty()) {
                        item { Text("Nenhum produto encontrado.") }
                    } else {
                        items(products, key = { "product-${it.id}" }) { product ->
                            ProductCard(product)
                        }
                    }
                    item { SectionHeading("Veterinários", "Profissionais verificados") }
                    if (vets.isEmpty() && !loading && error == null) {
                        item { Text("Nenhum veterinário encontrado.") }
                    } else {
                        items(vets, key = { "vet-${it.id}" }) { vet -> VetCard(vet) }
                    }
                    item {
                        TextButton(onClick = onOpenPortal, modifier = Modifier.fillMaxWidth()) {
                            Text("Acessar todos os serviços e recursos")
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun WelcomePanel(onOpenPortal: () -> Unit) {
    Card(
        colors = CardDefaults.cardColors(containerColor = Color(0xFF17482F)),
        shape = RoundedCornerShape(20.dp),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text("CONCOOP", color = Color(0xFFE1C98E), fontSize = 13.sp, fontWeight = FontWeight.Bold)
            Text(
                "Conexões que\nfortalecem o campo.",
                color = Color.White,
                fontSize = 28.sp,
                lineHeight = 33.sp,
                fontWeight = FontWeight.Bold,
            )
            Text(
                "Encontre produtos e profissionais rurais perto de você.",
                color = Color(0xFFE4EAE3),
            )
            Button(
                onClick = onOpenPortal,
                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFE1C98E)),
            ) {
                Text("Abrir portal", color = Color(0xFF183B28))
            }
        }
    }
}

@Composable
private fun SectionHeading(title: String, supportingText: String) {
    Column {
        Text(title, fontSize = 20.sp, fontWeight = FontWeight.Bold)
        Text(supportingText, color = Color(0xFF68746A), fontSize = 13.sp)
    }
}

@Composable
private fun ProductCard(product: MobileProduct) {
    Card(shape = RoundedCornerShape(14.dp), colors = CardDefaults.cardColors(containerColor = Color.White)) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Text(product.title, fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
            if (product.description.isNotBlank()) {
                Text(
                    product.description,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                    color = Color(0xFF606B62),
                )
            }
            Spacer(Modifier.height(2.dp))
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text(
                    NumberFormat.getCurrencyInstance(Locale("pt", "BR")).format(product.price),
                    color = Color(0xFF245B3D),
                    fontWeight = FontWeight.Bold,
                )
                Text(product.city, color = Color(0xFF68746A), maxLines = 1)
            }
            Text("Por ${product.producer}", color = Color(0xFF68746A), fontSize = 13.sp)
        }
    }
}

@Composable
private fun VetCard(vet: MobileVet) {
    Card(shape = RoundedCornerShape(14.dp), colors = CardDefaults.cardColors(containerColor = Color.White)) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(5.dp),
        ) {
            Text(vet.name, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
            Text(vet.city, color = Color(0xFF245B3D), fontSize = 13.sp)
            if (vet.bio.isNotBlank()) {
                Text(vet.bio, maxLines = 2, overflow = TextOverflow.Ellipsis, color = Color(0xFF606B62))
            }
        }
    }
}
