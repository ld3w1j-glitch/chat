# Nossa Sala — Chat preparado para Railway

Aplicação Flask para criar uma sala privada por link e conversar por mensagens.

## O que já está preparado

- Flask + Gunicorn para produção.
- `railway.json` com Start Command e Healthcheck.
- Porta automática via variável `PORT` do Railway.
- PostgreSQL via variável `DATABASE_URL`.
- SQLite automático apenas quando o projeto é executado localmente.
- Driver PostgreSQL incluído em `requirements.txt`.
- Python definido em `.python-version`.

## Teste local no Windows

No terminal, dentro da pasta do projeto:

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

## Publicar no Railway usando GitHub

### 1. Coloque esta pasta em um repositório do GitHub

Na raiz do projeto:

```bat
git init
git add .
git commit -m "Nossa Sala pronta para Railway"
git branch -M main
git remote add origin URL_DO_SEU_REPOSITORIO
git push -u origin main
```

### 2. Crie o serviço no Railway

No Railway:

1. Crie um novo projeto.
2. Escolha **Deploy from GitHub repo**.
3. Selecione o repositório deste projeto.
4. O Railway detectará o Python e instalará `requirements.txt`.
5. O arquivo `railway.json` define o comando de produção automaticamente.

### 3. Adicione PostgreSQL

No mesmo projeto Railway:

1. Clique em **+ New** / **Create**.
2. Escolha **Database**.
3. Escolha **PostgreSQL**.
4. Aguarde o banco ficar disponível.

### 4. Ligue o PostgreSQL ao serviço Flask

Abra o serviço da aplicação e vá em **Variables**.

Crie a variável:

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
```

Se o serviço do banco tiver outro nome, substitua `Postgres` pelo nome mostrado no Railway.

Depois disso, faça/reinicie o deploy.

### 5. Gere o endereço público

No serviço da aplicação:

1. Abra **Settings**.
2. Procure **Networking**.
3. Clique em **Generate Domain**.

Você receberá um endereço parecido com:

```text
https://nossa-sala-production.up.railway.app
```

Abra esse endereço, clique em **Criar nova sala** e envie o link completo da sala para a outra pessoa.

## Estrutura

```text
chat_casal/
├── app.py
├── railway.json
├── requirements.txt
├── .python-version
├── .gitignore
├── Procfile
├── README.md
├── static/
│   └── style.css
└── templates/
    ├── index.html
    ├── room.html
    └── not_found.html
```

## Banco de dados

Localmente, sem `DATABASE_URL`, o sistema usa SQLite.

No Railway, configure `DATABASE_URL` para o PostgreSQL. Assim, salas e mensagens ficam no banco e continuam existindo mesmo após novos deploys da aplicação.

## Observação de privacidade

Esta versão usa um endereço de sala aleatório, mas não possui criptografia ponta a ponta. Quem tiver o endereço completo da sala poderá acessá-la. Para uma versão mais privada, o próximo passo recomendado é adicionar senha/token de participante e autenticação dos dois usuários.
