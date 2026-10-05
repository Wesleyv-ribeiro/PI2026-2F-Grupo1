package br.com.concoop

import android.content.Context
import android.net.Uri
import android.webkit.MimeTypeMap
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.util.Locale
import java.util.UUID

private val submissionTypes = listOf(
    "product" to "Produto",
    "service" to "Serviço",
    "animal_report" to "Relato de animal",
    "message" to "Mensagem para veterinário",
)

@Composable
fun OfflineSubmissionScreen(
    dao: OfflineDao,
    session: CachedSession?,
    vets: List<MobileVet>,
    onOpenPortal: () -> Unit,
    onQueueChanged: suspend () -> Unit,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var selectedType by remember { mutableStateOf("product") }
    val fields = remember(selectedType) {
        mutableStateMapOf<String, String>().apply {
            putAll(defaultFields(selectedType))
        }
    }
    var menuExpanded by remember { mutableStateOf(false) }
    var attachmentUri by remember { mutableStateOf<Uri?>(null) }
    var queueItems by remember { mutableStateOf<List<QueuedSubmission>>(emptyList()) }
    var formMessage by remember { mutableStateOf<String?>(null) }
    var saving by remember { mutableStateOf(false) }

    val photoPicker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) {
        attachmentUri = it
    }

    suspend fun refreshQueue() {
        queueItems = dao.submissions()
        onQueueChanged()
    }

    LaunchedEffect(dao) {
        queueItems = dao.submissions()
    }

    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(horizontal = 16.dp, vertical = 14.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text("Novo envio", fontWeight = FontWeight.Bold, color = Color(0xFF245B3D))
                Text("Salve no aparelho; a sincronização acontece quando a rede voltar.")
            }
        }
        item {
            Card(colors = CardDefaults.cardColors(containerColor = Color(0xFFEAF1E8))) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    if (session == null) {
                        Text("Entre no portal enquanto estiver online antes de criar envios offline.")
                        TextButton(onClick = onOpenPortal) { Text("Entrar no portal") }
                    } else {
                        Text("Conta: ${session.name}", fontWeight = FontWeight.SemiBold)
                        Text("Os envios ficam associados a esta conta e só sincronizam quando ela estiver conectada.")
                    }
                }
            }
        }
        item {
            Box {
                OutlinedButton(onClick = { menuExpanded = true }, modifier = Modifier.fillMaxWidth()) {
                    Text(submissionTypes.first { it.first == selectedType }.second)
                }
                DropdownMenu(expanded = menuExpanded, onDismissRequest = { menuExpanded = false }) {
                    submissionTypes.forEach { (type, label) ->
                        DropdownMenuItem(
                            text = { Text(label) },
                            onClick = {
                                selectedType = type
                                attachmentUri = null
                                formMessage = null
                                menuExpanded = false
                            },
                        )
                    }
                }
            }
        }
        when (selectedType) {
            "product" -> {
                item { FormField(fields, "title", "Título do produto") }
                item { FormField(fields, "description", "Descrição", multiline = true) }
                item { FormField(fields, "price", "Preço (opcional)") }
                item {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(
                            checked = fields["made_to_order"] == "1",
                            onCheckedChange = { fields["made_to_order"] = if (it) "1" else "0" },
                        )
                        Text("Feito sob encomenda")
                    }
                }
                if (fields["made_to_order"] != "1") {
                    item { FormField(fields, "stock", "Estoque") }
                }
                item {
                    OutlinedButton(onClick = { photoPicker.launch("image/*") }, modifier = Modifier.fillMaxWidth()) {
                        Text(if (attachmentUri == null) "Adicionar foto do produto" else "Trocar foto selecionada")
                    }
                }
                if (attachmentUri != null) item { Text("A foto será armazenada no aparelho até o envio.") }
            }
            "service" -> {
                item { FormField(fields, "title", "Título do serviço") }
                item { FormField(fields, "description", "Descrição", multiline = true) }
                item { FormField(fields, "category", "Categoria (opcional)") }
                item { FormField(fields, "price", "Preço (opcional)") }
                item { FormField(fields, "location", "Área de atendimento") }
                item { FormField(fields, "contact", "Contato") }
            }
            "animal_report" -> {
                item { FormField(fields, "title", "Título do relato") }
                item { FormField(fields, "species", "Espécie") }
                item { FormField(fields, "urgency", "Urgência (baixa, media ou alta)") }
                item { FormField(fields, "description", "Descrição", multiline = true) }
                item { FormField(fields, "location", "Localização") }
            }
            "message" -> {
                item { FormField(fields, "content", "Mensagem", multiline = true) }
                item { Text("Escolha um veterinário verificado", fontWeight = FontWeight.SemiBold) }
                if (vets.isEmpty()) {
                    item { Text("Conecte-se uma vez para carregar veterinários antes de escrever offline.") }
                } else {
                    items(vets, key = { "recipient-${it.id}" }) { vet ->
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .clickable { fields["receiver_id"] = vet.id.toString() }
                                .padding(vertical = 4.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            RadioButton(
                                selected = fields["receiver_id"] == vet.id.toString(),
                                onClick = { fields["receiver_id"] = vet.id.toString() },
                            )
                            Column {
                                Text(vet.name, fontWeight = FontWeight.SemiBold)
                                Text(vet.city, color = Color(0xFF68746A))
                            }
                        }
                    }
                }
            }
        }
        if (formMessage != null) {
            item { Text(formMessage.orEmpty(), color = Color(0xFF245B3D)) }
        }
        item {
            Button(
                enabled = !saving && session != null,
                onClick = {
                    val requiredFields = when (selectedType) {
                        "product", "service" -> listOf("title", "description")
                        "animal_report" -> listOf("title", "description", "species")
                        else -> listOf("content", "receiver_id")
                    }
                    if (session == null) {
                        formMessage = "Entre no portal para associar o envio à sua conta."
                    } else if (selectedType == "product" && session.role !in setOf("produtor", "vendedor", "loja")) {
                        formMessage = "Este perfil não pode publicar produtos. Entre com uma conta de produtor, vendedor ou loja."
                    } else if (requiredFields.any { fields[it].isNullOrBlank() }) {
                        formMessage = "Preencha os campos obrigatórios antes de salvar."
                    } else if (selectedType == "product" && fields["made_to_order"] != "1" && fields["stock"].orEmpty().toIntOrNull() == null) {
                        formMessage = "Informe um estoque válido."
                    } else {
                        saving = true
                        formMessage = null
                        scope.launch {
                            val clientId = UUID.randomUUID().toString()
                            try {
                                val savedFile = attachmentUri?.let { copyAttachment(context, it, clientId) }
                                val payload = JSONObject()
                                fields.forEach { (key, value) -> payload.put(key, value) }
                                dao.insertSubmission(
                                    QueuedSubmission(
                                        id = clientId,
                                        type = selectedType,
                                        payloadJson = payload.toString(),
                                        attachmentPath = savedFile?.first,
                                        attachmentMimeType = savedFile?.second,
                                        ownerUserId = session.userId,
                                        createdAt = System.currentTimeMillis(),
                                    ),
                                )
                                OfflineSyncScheduler.enqueue(context)
                                attachmentUri = null
                                fields.clear()
                                fields.putAll(defaultFields(selectedType))
                                formMessage = "Salvo no aparelho. Será enviado assim que houver conexão."
                                refreshQueue()
                            } catch (error: Exception) {
                                formMessage = error.message ?: "Não foi possível salvar este envio."
                            } finally {
                                saving = false
                            }
                        }
                    }
                },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(if (saving) "Salvando..." else "Salvar e sincronizar")
            }
        }
        item { Text("Fila de sincronização", fontWeight = FontWeight.Bold) }
        if (queueItems.isEmpty()) {
            item { Text("Nenhum envio pendente.") }
        } else {
            items(queueItems, key = { "queue-${it.id}" }) { queued ->
                Card(shape = RoundedCornerShape(12.dp), colors = CardDefaults.cardColors(containerColor = Color.White)) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(start = 14.dp, top = 12.dp, bottom = 12.dp, end = 8.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(modifier = Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                            Text(submissionTypes.first { it.first == queued.type }.second, fontWeight = FontWeight.SemiBold)
                            Text(queued.lastError ?: statusLabel(queued.state), color = Color(0xFF68746A))
                        }
                        TextButton(onClick = {
                            scope.launch {
                                queued.attachmentPath?.let { File(it).delete() }
                                dao.deleteSubmission(queued.id)
                                refreshQueue()
                            }
                        }) {
                            Text("Remover")
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun FormField(
    fields: MutableMap<String, String>,
    key: String,
    label: String,
    multiline: Boolean = false,
) {
    OutlinedTextField(
        value = fields[key].orEmpty(),
        onValueChange = { fields[key] = it },
        modifier = Modifier.fillMaxWidth(),
        label = { Text(label) },
        singleLine = !multiline,
        minLines = if (multiline) 3 else 1,
        maxLines = if (multiline) 6 else 1,
        shape = RoundedCornerShape(12.dp),
    )
}

private fun defaultFields(type: String): Map<String, String> = when (type) {
    "product" -> mapOf("title" to "", "description" to "", "price" to "", "stock" to "1", "made_to_order" to "0")
    "service" -> mapOf("title" to "", "description" to "", "category" to "", "price" to "", "location" to "", "contact" to "")
    "animal_report" -> mapOf("title" to "", "description" to "", "species" to "", "urgency" to "media", "location" to "")
    else -> mapOf("content" to "", "receiver_id" to "")
}

private fun statusLabel(state: String): String = when (state) {
    "waiting_login" -> "Aguardando login"
    "waiting_account" -> "Aguardando a conta correta"
    "error" -> "Envio recusado"
    else -> "Aguardando conexão"
}

private suspend fun copyAttachment(context: Context, uri: Uri, clientId: String): Pair<String, String> =
    withContext(Dispatchers.IO) {
        val mimeType = context.contentResolver.getType(uri)
            ?: throw IOException("Não foi possível identificar o tipo da imagem.")
        val extension = MimeTypeMap.getSingleton().getExtensionFromMimeType(mimeType)?.lowercase(Locale.ROOT)
        if (mimeType.substringBefore('/') != "image" || extension !in setOf("jpg", "jpeg", "png", "gif", "webp")) {
            throw IOException("Use uma imagem JPG, PNG, GIF ou WebP.")
        }

        val directory = File(context.filesDir, "offline-attachments").apply { mkdirs() }
        val file = File(directory, "$clientId.$extension")
        val input = context.contentResolver.openInputStream(uri)
            ?: throw IOException("Não foi possível abrir a imagem selecionada.")
        input.use { source ->
            FileOutputStream(file).use { target ->
                val buffer = ByteArray(8192)
                var totalBytes = 0L
                while (true) {
                    val count = source.read(buffer)
                    if (count < 0) break
                    totalBytes += count
                    if (totalBytes > MAX_OFFLINE_IMAGE_BYTES) {
                        file.delete()
                        throw IOException("A imagem deve ter no máximo 10 MB.")
                    }
                    target.write(buffer, 0, count)
                }
            }
        }
        file.absolutePath to mimeType
    }

private const val MAX_OFFLINE_IMAGE_BYTES = 10 * 1024 * 1024