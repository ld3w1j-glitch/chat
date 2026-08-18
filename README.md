# Nossa Sala — Usuários, Admin, Online e WhatsApp

Versão preparada para Railway com PostgreSQL.

## O que esta versão faz

- Conta administradora separada.
- Cadastro público **não cria a conta imediatamente**: gera uma solicitação para o administrador.
- A solicitação contém nome, usuário desejado, WhatsApp, e-mail opcional, observação e a senha já transformada em hash.
- O administrador pode aprovar ou recusar solicitações.
- Ao aprovar, a conta é criada automaticamente.
- Depois da aprovação, o sistema tenta enviar um aviso automático pelo WhatsApp Cloud API.
- O administrador também pode criar usuários manualmente, ativar/desativar contas, redefinir senhas e reenviar o aviso de WhatsApp.
- Usuários logados aparecem como online enquanto o navegador/PWA continua enviando presença.
- Usuários online podem clicar em **Chamar**.
- O destinatário recebe convite com **Aceitar** e **Recusar**, além de Web Push se tiver ativado notificações.
- A mesma dupla reutiliza sempre a mesma conversa privada e o mesmo histórico.
- Emojis, figurinhas, editor de fundo claro/escuro e persistência no PostgreSQL continuam funcionando.
- PWA instalável e Web Push global por usuário.

## 1. Railway

Suba todos os arquivos deste projeto para o GitHub e conecte o repositório ao Railway.

Adicione PostgreSQL ao mesmo projeto e, no serviço web, configure:

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
```

O projeto já possui `railway.json`, `Procfile`, `.python-version` e `requirements.txt`.

## 2. Criar a conta administradora

### Opção recomendada — Variáveis do Railway

Antes do primeiro deploy, adicione:

```text
ADMIN_USERNAME=admin
ADMIN_PASSWORD=coloque-uma-senha-forte-aqui
ADMIN_FULL_NAME=Administrador
ADMIN_WHATSAPP=5535999999999
ADMIN_EMAIL=
```

Se essas variáveis estiverem presentes e ainda não existir administrador, a conta é criada automaticamente.

### Opção alternativa — Tela inicial

Se nenhum administrador existir e `ADMIN_USERNAME` / `ADMIN_PASSWORD` não estiverem definidos, o primeiro acesso abre:

```text
/setup-admin
```

Depois que o primeiro administrador é criado, essa tela deixa de permitir novos administradores.

## 3. Fluxo de solicitação de conta

1. A pessoa abre **Criar conta**.
2. Informa:
   - nome completo;
   - usuário desejado;
   - WhatsApp com DDD;
   - e-mail opcional;
   - senha e confirmação;
   - observação opcional;
   - autorização para receber o aviso no WhatsApp.
3. A senha é imediatamente convertida para hash e não é armazenada em texto puro.
4. A solicitação aparece no painel do administrador.
5. Se o administrador tiver Web Push ativado, também recebe uma notificação de nova solicitação.
6. Ao clicar em **Aprovar e criar conta**, o sistema cria o usuário.
7. Em seguida, tenta enviar o template de aprovação pelo WhatsApp Cloud API.

## 4. WhatsApp automático

Para o envio realmente automático, é necessário configurar a **WhatsApp Business Platform / Cloud API** da Meta.

Variáveis do Railway:

```text
WHATSAPP_ACCESS_TOKEN=seu_token
WHATSAPP_PHONE_NUMBER_ID=seu_phone_number_id
WHATSAPP_GRAPH_VERSION=v26.0
WHATSAPP_TEMPLATE_NAME=conta_criada
WHATSAPP_TEMPLATE_LANG=pt_BR
PUBLIC_BASE_URL=https://seu-dominio.up.railway.app
DEFAULT_COUNTRY_CODE=55
```

### Template esperado

Crie e aprove na Meta um template de utilidade chamado, por exemplo:

```text
conta_criada
```

Corpo sugerido, com três parâmetros:

```text
Olá {{1}}! Sua conta no Nossa Sala foi aprovada com sucesso.
Usuário: {{2}}
Acesse: {{3}}
```

O sistema envia os parâmetros nesta ordem:

1. nome completo;
2. nome de usuário;
3. URL de login.

Se a Cloud API ainda não estiver configurada, a conta **ainda será criada**, mas o painel do administrador mostrará que o aviso automático falhou. Depois você pode configurar as variáveis e clicar em **WhatsApp** ao lado do usuário para reenviar.

## 5. Web Push

Depois de entrar, clique em **Notificações**. A inscrição fica vinculada à conta, e não apenas a uma sala.

Isso permite receber:

- nova mensagem privada;
- figurinha;
- chamado de outro usuário;
- resposta ao chamado;
- nova solicitação de conta no caso do administrador.

## 6. Presença online

O navegador/PWA envia um sinal de presença aproximadamente a cada 20 segundos. Um usuário é considerado online enquanto o último sinal tiver sido recebido recentemente.

Se a pessoa fechar o site/app, ela deixa de aparecer como online após alguns instantes, mas ainda pode receber Web Push se já tiver ativado notificações.

## 7. Segurança

- Senhas usam `generate_password_hash` / `check_password_hash` do Werkzeug.
- A senha enviada numa solicitação nunca fica disponível em texto puro para o administrador.
- Links de conversa não dão acesso sozinhos: o usuário precisa estar autenticado e pertencer à conversa.
- A chave da sessão e as chaves VAPID ficam persistidas no banco para sobreviver a redeploys.
- Contas podem ser desativadas pelo administrador sem apagar o histórico.

## Rodar localmente

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Abra:

```text
http://127.0.0.1:5000
```

Sem `DATABASE_URL`, o sistema usa SQLite local apenas para desenvolvimento.
