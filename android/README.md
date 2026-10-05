# CONCOOP para Android

O aplicativo combina uma tela inicial nativa em Kotlin/Jetpack Compose com o portal Flask em uma área WebView. A tela inicial carrega produtos e veterinários verificados pela API `/api/mobile/home`; o portal mantém os fluxos de autenticação, cadastro, chat, uploads e demais recursos existentes.

## Requisitos

- Android Studio Hedgehog ou mais recente
- JDK 17
- Android SDK 35
- O Flask executando em um endereço acessível pelo dispositivo

Ao abrir o projeto pela primeira vez, use **File > Sync Project with Gradle Files**. O Android Studio baixará a distribuição Gradle 8.9 definida em `gradle-wrapper.properties`.

## Executar no emulador

1. Inicie o servidor Flask na raiz do projeto e deixe-o acessível na rede local:

   ```powershell
   $env:HOST="0.0.0.0"
   python CONCOOP_final/app.py
   ```

2. Abra a pasta `android` no Android Studio e execute o módulo `app`. A home nativa consulta o backend ao abrir; toque em **Portal** para acessar os demais fluxos.

O endereço padrão é `http://10.0.2.2:5000/`, que aponta para o computador hospedeiro quando usado no emulador Android.

## Uso offline

- Entre no portal enquanto estiver online antes de criar envios offline. O aplicativo não armazena sua senha.
- Produtos, serviços, relatos de animais e mensagens podem ser salvos no aparelho e sincronizados quando a rede voltar e a mesma conta estiver autenticada.
- Fotos de produtos ficam em armazenamento privado do aplicativo até serem sincronizadas; imagens até 10 MB são aceitas.
- O catálogo de produtos e veterinários verificados é mantido localmente para consulta offline.
- A aba **Novo envio** mostra a fila e permite remover itens que ainda não foram sincronizados.

## Publicar o APK para download no site

1. Configure a assinatura de release no Android Studio/Gradle. Não publique o APK de debug.
2. Na pasta `android`, gere o pacote apontando para o domínio de produção:

   ```powershell
   .\gradlew.bat assembleRelease -PCONCOOP_URL=https://concoop.com.br/
   ```

3. Copie o APK assinado para `CONCOOP_final/static/downloads/concoop-android.apk`.
4. Publique no servidor tanto a aplicação Flask atualizada quanto esse arquivo. A página ficará disponível em `https://concoop.com.br/app` e o botão será habilitado quando o arquivo estiver presente.

O servidor precisa incluir as rotas `/api/mobile/home`, `/api/mobile/session` e `/api/mobile/sync` para o app funcionar corretamente.

## Executar em um celular físico

Use o IP do computador na rede local:

```powershell
# Dentro da pasta android
./gradlew.bat assembleDebug -PCONCOOP_URL=http://192.168.0.10:5000/
```

Troque `192.168.0.10` pelo IP local do computador. O celular e o computador precisam estar na mesma rede, e o firewall deve liberar a porta 5000.

Para produção, gere o APK usando HTTPS:

```powershell
./gradlew.bat assembleRelease -PCONCOOP_URL=https://seu-dominio.com/
```
