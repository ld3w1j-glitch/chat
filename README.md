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

\n## IMPORTANTE — persistência no Railway\n\nEsta versão possui uma proteção contra perda de dados.\n\nNo Railway, a aplicação **não inicia mais com SQLite**. Ela exige PostgreSQL. Isso evita que usuários, mensagens, solicitações, leituras, figurinhas, chaves de sessão e demais dados sejam gravados no filesystem temporário do serviço web.\n\nNo Railway, deixe os serviços assim:\n\n```text\nProjeto\n├── web       (este aplicativo)\n└── Postgres  (banco de dados)\n```\n\nNo serviço **web** abra **Variables** e crie uma Reference Variable:\n\n```text\nDATABASE_URL=${{Postgres.DATABASE_URL}}\n```\n\nSe o seu serviço de banco tiver outro nome, use exatamente esse nome no lugar de `Postgres`. A forma mais segura é usar **Add Reference Variable** e selecionar `DATABASE_URL` do banco pelo painel do Railway.\n\nDepois aplique/deploy as alterações.\n\nPara conferir, abra:\n\n```text\nhttps://SEU-DOMINIO/health\n```\n\nEm produção, o resultado esperado é semelhante a:\n\n```json\n{"status":"ok","database":"postgresql","persistent":true}\n```\n\nSe `DATABASE_URL` estiver ausente no Railway, o deploy agora falhará com uma mensagem explicando a configuração necessária, em vez de iniciar silenciosamente com SQLite e perder os dados no próximo deploy.\n\nSQLite continua disponível ao executar localmente no computador para desenvolvimento.\n\n## 1. Railway

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


## Respostas e confirmação de leitura

- Cada mensagem e figurinha possui a ação **Responder**.
- A resposta guarda uma referência à mensagem original e mostra uma prévia no balão.
- É possível responder inclusive a uma mensagem que já é uma resposta.
- Mensagens próprias mostram **✓ Enviada** enquanto ainda não foram abertas pelo outro participante.
- Quando o outro participante abre a conversa, o estado muda para **✓✓ Visualizada**.
- A confirmação de leitura só é enviada enquanto a página da conversa está visível, evitando marcar como lida apenas por causa de uma notificação em segundo plano.
- As relações de resposta e os marcadores de leitura são persistidos no banco de dados e sobrevivem a novos deploys.

## Edição e exclusão de mensagens

- O autor pode editar as próprias mensagens de texto.
- Mensagens alteradas exibem o marcador **editada**.
- O autor pode apagar as próprias mensagens, inclusive mensagens de figurinha.
- A exclusão é lógica: o conteúdo deixa de aparecer e é substituído por **Mensagem apagada**, preservando a ordem da conversa e as respostas vinculadas.
- Edições e exclusões são sincronizadas automaticamente com o outro participante sem precisar recarregar a página.
- As alterações ficam persistidas no PostgreSQL pela tabela `message_event`, criada automaticamente no primeiro deploy desta versão.

## Indicador “digitando…”

- Quando um participante começa a escrever, o outro vê **digitando…** abaixo do nome no cabeçalho da conversa.
- O estado é renovado enquanto há atividade no campo de mensagem.
- Ele some automaticamente após alguns segundos sem digitação, ao enviar a mensagem, perder o foco, trocar de aba ou fechar a conversa.
- O estado usa o PostgreSQL com expiração por timestamp, evitando ficar preso em “digitando…” após quedas de conexão ou reinícios.


## Novidade

- Botão **+** ao lado do chat para anexar **documento, imagem ou vídeo**.
- Os anexos são salvos no PostgreSQL, mantendo a persistência entre deploys no Railway.
- Limites: imagens até 10 MB, documentos até 10 MB e vídeos até 20 MB.


## Função Fofoca

No botão **+** existe a opção **Fofoca**.

Ela permite:
- escolher uma imagem;
- escolher uma moldura pronta;
- editar somente o campo da notícia/manchete;
- gerar a arte final;
- enviar a arte como imagem no chat.


## Administração das molduras de Fofoca

O painel Admin possui uma seção **Modelos de Fofoca** com:

- **Exportar modelos (.zip)**: baixa todas as pastas de modelos atuais.
- **Importar modelos**: recebe um ZIP com uma ou mais pastas de moldura.
- Persistência dos arquivos no Railway Volume (`RAILWAY_VOLUME_MOUNT_PATH/fofoca_frames`).

Cada pasta utiliza `modelo.json`. Molduras criadas graficamente podem usar `mode: overlay` e um `overlay.png` 1080x1350. A foto é desenhada na região definida em `photo` e a única parte editável pelo usuário permanece a manchete definida por `headline`.


## Molduras em PNG

Nesta versão, as molduras de Fofoca usam **PNG**.
Cada modelo deve ter a estrutura:

```
minha_moldura/
├── modelo.json
└── overlay.png
```

Edite o `overlay.png` no Photoshop.
O `modelo.json` continua sendo usado apenas para posição e estilo da manchete.


## Ajuste de usabilidade

- A tela de **Fofoca** agora possui um botão **Voltar**, facilitando sair da edição depois de abrir a imagem.


## Exportação aprimorada dos modelos de Fofoca

O ZIP exportado pelo admin agora inclui, por categoria/modelo:
- `overlay.png`
- `modelo.json`
- `guia_areas.png`
- `noticia_exemplo.png`
- `COMO_EDITAR.txt`

Assim fica mais fácil substituir apenas a imagem da moldura editando o `overlay.png` no Photoshop e reimportando o pacote.

## Mini jogo Sim ou Não

No botão **+** da conversa existe a opção **🎲 Sim ou Não**.

Fluxo:
1. Um usuário escreve a pergunta.
2. Clica em **Sortear e lançar**.
3. O sorteio acontece uma única vez no servidor com chance 50/50.
4. O resultado é gravado como mensagem da conversa.
5. Os dois participantes recebem a mesma jogada e veem a animação SIM/NÃO até o resultado final.

O resultado do jogo não pode ser editado depois, preservando a integridade do sorteio. A mensagem do jogo pode ser respondida, visualizada e apagada pelo autor como as demais mensagens.

## Meu Perfil

Cada usuário possui a rota `/perfil`, onde pode:
- enviar/trocar/remover a foto de perfil;
- alterar o nome de exibição;
- alterar a própria senha informando a senha atual.

O nome de usuário (`@usuario`) permanece fixo para preservar o vínculo das conversas e mensagens antigas.

As fotos são recortadas para 512x512 e armazenadas em WebP na base persistente, através da tabela `UserProfile`.

## Notificações de mensagens

A versão reforça o Web Push:
- mensagens de texto continuam disparando push para o outro usuário;
- se o navegador tiver uma inscrição antiga ligada a outra chave VAPID, a inscrição é recriada automaticamente;
- quando a permissão já está concedida, Dashboard e Chat sincronizam novamente o aparelho com o servidor;
- em `/perfil` há `Ativar / reparar` e `Enviar notificação de teste`;
- `/api/push/status` informa quantos aparelhos estão registrados;
- `/api/push/test` envia um push de diagnóstico para o próprio usuário.

Cada aparelho precisa conceder permissão para notificações pelo menos uma vez. No iPhone/iPad, o PWA precisa ser adicionado à Tela de Início e aberto pelo ícone para Web Push.

## Modo Jogos privados por turnos

O sistema agora possui uma área **🎮 Jogos** para partidas privadas e assíncronas entre dois usuários.

Jogos iniciais:
- **Jogo da Velha**: regras completas, vitória/empate e revanche.
- **Damas 8×8**: movimentos diagonais, captura obrigatória, captura múltipla e coroação. As damas coroadas usam movimento curto diagonal em ambas as direções.
- **Truco Paulista (modo básico)**: baralho de 40 cartas, vira, manilha por naipe, três cartas por mão e placar até 12. O pedido de Truco/aumento de aposta não faz parte desta primeira versão.

### Persistência e privacidade
- Cada partida é vinculada exclusivamente aos dois jogadores.
- Apenas os participantes conseguem abrir a URL/API da partida.
- Estado, turno, placar e histórico de jogadas ficam no mesmo banco persistente usado pelo sistema.
- O outro jogador não precisa estar online quando a jogada é feita.
- Quando ele voltar, o jogo é restaurado no ponto exato onde parou.
- Web Push avisa o outro participante quando uma jogada passa a vez para ele.
- No Truco, as cartas da mão adversária ficam somente no servidor e não são enviadas ao navegador do outro jogador.
