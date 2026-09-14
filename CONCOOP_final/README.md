## AgroLink - Rede para produtores e veterinários

Projeto exemplo de uma rede social/profissional simples focada em produtores rurais, veterinários e usuários comuns.

### Tecnologias

- **Backend**: Python, Flask, PostgreSQL (via `psycopg2`)
- **Frontend**: HTML, CSS, JavaScript

### Funcionalidades principais

- Cadastro e login de usuários com três tipos de conta:
  - `produtor`
  - `veterinario`
  - `usuario` (usuário comum)
- Perfis públicos básicos.
- Produtores podem cadastrar produtos/ofertas.
- Página de mercado listando todas as ofertas.
- Listagem de veterinários.
- Produtores (ou qualquer usuário logado) podem enviar mensagem para veterinários.
- Painel do usuário com resumo de produtos e mensagens.

### Como rodar

1. Crie e ative um ambiente virtual (opcional, mas recomendado):

   ```bash
   python -m venv venv
   venv\Scripts\activate  # Windows
   ```

2. Instale as dependências:

   ```bash
   pip install -r requirements.txt
   ```

3. Configure a URL de conexão com PostgreSQL:

   ```bash
   set DATABASE_URL=postgresql://postgres:postgres@localhost:5432/agrolink  # Windows (cmd)
   $env:DATABASE_URL="postgresql://SEU_USUARIO:SUA_SENHA@127.0.0.1:5432/agrolink"  # PowerShell
   ```

   Opcionalmente, você pode criar um arquivo `.env` na pasta `PI/` com:

   ```env
   DATABASE_URL=postgresql://SEU_USUARIO:SUA_SENHA@127.0.0.1:5432/agrolink
   GEMINI_API_KEY=sua_chave_do_gemini
   # Alternativa aceita pela SDK: GOOGLE_API_KEY=sua_chave_do_gemini
   # Opcional; o padrão é gemini-2.5-flash (gemini-2.0-flash foi desligado)
   GEMINI_MODEL=gemini-2.5-flash
   ```

4. Execute a aplicação:

   ```bash
   python app.py
   ```

5. Acesse no navegador:

   - `http://127.0.0.1:5000/`

### Estrutura básica de pastas

- `app.py` — aplicação Flask e modelos de dados.
- `templates/` — arquivos HTML.
- `static/css/style.css` — estilos.
- `static/js/main.js` — JavaScript simples.
- Banco PostgreSQL configurado via `DATABASE_URL`.

### Observações

- A chave `SECRET_KEY` em `app.py` está fixa apenas para desenvolvimento. Em produção, use uma variável de ambiente ou valor gerado de forma segura.
- Este é um MVP simplificado; mais recursos (busca avançada, filtros, upload de fotos, etc.) podem ser adicionados facilmente.

### Troubleshooting (PostgreSQL)

- Se ocorrer erro de conexão ao iniciar, confirme:
  - serviço PostgreSQL ativo;
  - usuário/senha da `DATABASE_URL` corretos;
  - banco `agrolink` criado no servidor.
- Exemplo para criar o banco (em uma conexão administrativa):
  - `CREATE DATABASE agrolink;`

