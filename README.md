# Nossa Sala — PWA + Railway + Notificações + Figurinhas

Chat privado por sala preparado para publicar no Railway.

## Recursos desta versão

- Sala por link aleatório.
- Mensagens salvas em PostgreSQL no Railway.
- Atualização automática da conversa.
- PWA instalável no celular/computador.
- Service Worker.
- Web Push com VAPID.
- Botão para ativar notificações.
- Notificação mesmo quando a página não está em primeiro plano, quando o navegador/SO permitir.
- Emojis.
- Estúdio de figurinhas.
- Escolha de foto.
- Controle de opacidade.
- Remoção de fundo escuro/preto.
- Remoção de fundo claro/branco.
- Ajuste de limite de cor e suavidade.
- Figurinhas finais em WebP 512x512.
- Figurinhas salvas no PostgreSQL e reutilizáveis.
- Exclusão da própria figurinha.
- Sem dependência de disco persistente do Railway para as figurinhas.

## Importante sobre notificações

Web Push precisa de HTTPS. O domínio público do Railway já usa HTTPS.

No Android e desktop, Chrome/Edge/Firefox normalmente permitem ativar pelo botão **Notificações**.

No iPhone/iPad, a experiência de Web Push depende do suporte do Safari/PWA. Em versões compatíveis, adicione o site à **Tela de Início**, abra o app por esse ícone e então toque em **Notificações**.

A aplicação gera automaticamente um par de chaves VAPID na primeira inicialização e salva essas chaves no próprio banco PostgreSQL. Assim, novos deploys não trocam a chave e não invalidam as inscrições já existentes.

Se desejar informar um contato VAPID real, crie a variável Railway:

```text
VAPID_SUBJECT=mailto:seuemail@exemplo.com
```

## Publicar no Railway

### 1. GitHub

Na raiz desta pasta:

```bat
git init
git add .
git commit -m "Nossa Sala PWA"
git branch -M main
git remote add origin URL_DO_REPOSITORIO
git push -u origin main
```

### 2. Railway

1. Crie um projeto.
2. `Deploy from GitHub Repo`.
3. Escolha o repositório.
4. Adicione um serviço **PostgreSQL** ao mesmo projeto.
5. No serviço Flask, em **Variables**, crie:

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
```

Se o banco tiver outro nome, troque `Postgres`.

6. Em **Settings > Networking**, gere um domínio público.

O `railway.json` já contém o comando:

```text
gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 120 --access-logfile - app:app
```

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

Localmente, sem `DATABASE_URL`, o sistema usa SQLite.

## Como criar figurinha

1. Entre na sala.
2. Toque em **🖼️**.
3. Abra a aba **Figurinhas**.
4. Escolha uma foto.
5. Ajuste:
   - Opacidade.
   - `Remover escuros / preto`, ou
   - `Remover claros / branco`.
   - Limite de cor.
   - Suavidade.
6. Clique em **Salvar figurinha**.
7. Ela aparecerá em **Figurinhas salvas**.
8. Clique nela para enviar.

A figurinha processada é salva no banco da sala e continuará disponível após redeploy.

## Estrutura

```text
Nossa_Sala_PWA_Figurinhas/
├── app.py
├── railway.json
├── Procfile
├── requirements.txt
├── .python-version
├── .gitignore
├── README.md
├── templates/
│   ├── index.html
│   ├── room.html
│   └── not_found.html
└── static/
    ├── style.css
    ├── manifest.webmanifest
    ├── sw.js
    └── icons/
        ├── icon-192.png
        └── icon-512.png
```

## Privacidade

A sala usa um endereço aleatório, mas esta versão ainda não tem criptografia ponta a ponta nem autenticação forte. Quem tiver o link completo consegue abrir a sala. Para maior privacidade, a próxima evolução pode adicionar senha da sala e dois perfis autorizados.
