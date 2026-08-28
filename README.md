# Web Device Connections

[![Licença: MIT](https://img.shields.io/badge/licença-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%20%7C%203.10%20%7C%203.11-blue.svg)](server/requirements.txt)

Gerenciamento remoto de dispositivos com terminal interativo e transferência de arquivos via WebSocket. O relay escala horizontalmente em Kubernetes; o frontend e os clients rodam separados.

## Arquitetura

```
             ┌─── HTTP (mesma origem, cookie) ──►  Frontend  ──── HTTP interno ───┐
Navegador ───┤                                     (login, sessão, grants)        ▼
             └─── WSS (grant assinado) ─────────►  Ingress (hash por device_id) ─► relay (N pods)
                                                                                   │ WSS
                                                                              Dispositivo
```

Três componentes independentes:

| Componente | Onde roda | Papel |
|---|---|---|
| `frontend/` | Docker local, ou um pod | Páginas, login, sessão, grants, e o cadastro de usuários e dispositivos (Postgres) |
| `server/` | Kubernetes, N réplicas | Relay puro. Sem login, sem usuários, sem senhas, sem banco. |
| `client/` | Em cada dispositivo | Agente com PTY e transferência de arquivos |

### Quem autentica o quê

O relay não autentica pessoas. Ele responde a duas perguntas mais estreitas:

- **É mesmo esse dispositivo?** O token é `HMAC-SHA256(DEVICE_MASTER_SECRET, device_id)`. Nada é armazenado, e um token vazado de um dispositivo não abre a sessão de outro.
- **O frontend autorizou essa sessão?** O frontend faz o login e, com o usuário autenticado, assina um **grant** curto nomeando um dispositivo e um propósito. O relay confere a assinatura e queima o `jti`, para que um grant que apareça num log de acesso não sirva duas vezes.

Toda a parte de pessoas — banco de credenciais, hash de senha, sessão — vive no frontend. Como navegador e frontend compartilham origem, a sessão é um cookie `HttpOnly`: nenhum script alcança o token.

O frontend também busca a lista de dispositivos server-to-server. Com isso o navegador **nunca faz requisição HTTP entre origens**, e o relay não precisa de CORS nenhum. A única conexão direta navegador→relay é o WebSocket, que carrega o grant.

### Como o roteamento funciona

O `device_id` está na URL das quatro rotas de WebSocket (`/device/{id}`, `/terminal/{id}`, `/file/{id}`, `/download/{id}`). O ingress extrai esse trecho e usa como chave de hash, então o dispositivo e as sessões do navegador para ele caem sempre no **mesmo pod**. Nenhum salto extra no caminho dos dados.

> **Escale por réplicas, nunca com `uvicorn --workers N`.** Vários workers compartilham a mesma porta e o kernel escolhe entre eles arbitrariamente — o ingress não consegue endereçar um worker específico.

## Requisitos

- Docker e Docker Compose (para rodar a stack local ou só o relay)
- Python 3.9+ para rodar `tools/` e scripts fora de container (3.11 recomendado — é a versão testada em CI)
- `kubectl` e acesso a um cluster com ingress-nginx, para deploy em Kubernetes

## Rodando localmente

```bash
# 1. Gere os segredos
python -c "import secrets; print('RELAY_SECRET=' + secrets.token_urlsafe(32))"
python -c "import secrets; print('SESSION_SECRET=' + secrets.token_urlsafe(32))"
python -c "import secrets; print('DEVICE_MASTER_SECRET=' + secrets.token_urlsafe(32))"

# 2. Preencha os dois .env — o RELAY_SECRET tem que ser idêntico nos dois
cp server/.env.example server/.env
cp frontend/.env.example frontend/.env

# 3. Suba
docker compose up -d

# 4. Gere o token do dispositivo
cd server && python -m tools.mint_device_token device-01 --env
```

No dispositivo, instale o agente (systemd, reinício automático, logs no journald):

```bash
sudo ./deploy/client/install.sh
# preencha DEVICE_ID, DEVICE_TOKEN e SERVER_URL em /etc/wdc-client/client.env
sudo systemctl start wdc-client && journalctl -u wdc-client -f
```

Por padrão o agente só lê e escreve arquivos dentro da home do usuário
(`ALLOWED_ROOTS`); qualquer caminho fora disso é recusado no próprio
dispositivo, mesmo que o relay peça.

Acesse `http://localhost:3000`. Na primeira subida uma conta `admin` / `admin` é semeada; troque a senha pela própria interface, em **Usuários**.

## Como o frontend é organizado

MVC, com uma camada a mais no meio:

```
app/controllers/   traduz requisição em resposta
app/services/      as regras, independentes de HTTP
app/models/        o que é guardado, e como se lê e escreve
app/views/         templates, assets e como a página é montada
```

A dependência anda numa direção só. Controller alcança service, model e view;
service alcança model; model e view não alcançam nada além de `config`. Qualquer
coisa que precisasse de uma seta apontando para cima está na camada errada.

**Por que `services/` existe.** MVC puro deixaria as regras no model ou no
controller. O model sabe *escrever uma linha*; ele não tem por que saber que
escrever aquela linha específica deixaria o sistema sem ninguém capaz de
administrá-lo. E colocar isso no controller amarraria a regra ao formato HTTP —
o mesmo guard vale para um comando de linha ou um script de migração. Cada
recusa é um `RuleError` com a mensagem já escrita para o operador, porque é
exatamente essa mensagem que vai para a tela.

**Onde procurar cada coisa:**

| Pergunta | Arquivo |
|---|---|
| Que rota atende `/manage/devices/{id}/revogar`? | `app/controllers/devices.py` |
| Quem pode rebaixar um administrador? | `app/services/users.py` |
| Como o token do dispositivo é obtido? | `app/services/relay.py` |
| Que colunas a tabela `devices` tem? | `app/models/device.py` |
| Como uma data é formatada? | `app/views/__init__.py` |
| De onde vem `RELAY_WS_URL`? | `app/config.py` |

O relay (`server/`) segue o mesmo esqueleto — `main.py` na raiz, pacote `app/`,
`tools/` — mas com `routers/` e `core/` em vez de MVC. Ele não tem model nenhum:
não guarda nada, só verifica tokens e grants e repassa bytes. Nomear pastas
`models/` e `views/` lá seria inventar camadas vazias.

### Configuração

Toda a configuração dos dois processos passa por um `Settings` de
pydantic-settings — `frontend/app/config.py` e `server/app/core/config.py` — lido
do ambiente e do `.env`, com o padrão escrito ao lado do que ele significa. Não
há `os.getenv` espalhado pelo código.

## Onde as páginas são montadas

No servidor, em Jinja. O navegador executa JavaScript só onde ele é a única peça
capaz de fazer o trabalho:

| Arquivo | Por que precisa ser no navegador |
|---|---|
| `script.js` | Fatia o arquivo, empurra os pedaços pelo WebSocket, controla a contrapressão e confere o SHA-256 do download. Os bytes nunca passam pelo frontend. |
| `common.js` | Toast e modal do progresso de transferência — o resultado chega depois, de forma assíncrona. |
| `api.js` | Pede o grant e abre o WebSocket contra o relay. |
| `terminal.html` | xterm.js. Um terminal é interativo por definição. |

Todo o resto — listar, filtrar, formular, validar, mostrar erro — é HTML vindo
pronto do Python. Duas páginas (`/manage/users`, `/manage/devices` e seus
formulários) não carregam **nenhum** `<script>`, e o bundle JavaScript do
Bootstrap não é mais usado em lugar nenhum: só a folha de estilo.

Três detalhes que caem por consequência disso:

- **Tema.** É um cookie que o servidor lê, não `localStorage`. O `data-theme`
  já sai correto no primeiro byte de HTML, então a página nunca pisca na
  paleta errada antes do script corrigir.
- **Busca.** O `?q=` é filtrado em Python e fica na barra de endereço, então
  recarregar ou favoritar mantém o filtro. Ele também casa com o **nome
  cadastrado**, coisa que o filtro antigo em JavaScript não fazia — ele só
  conhecia o `device_id`.
- **Datas.** Antes eram formatadas pelo navegador, no fuso da máquina de quem
  abrisse. Agora saem do servidor em `DISPLAY_TZ` (padrão
  `America/Sao_Paulo`), então a mesma linha se lê igual para o time inteiro.

A grade de cards do painel continua se atualizando a cada 5 s, mas o que ela
busca é **HTML** (`/partials/devices`), renderizado pelo mesmo include Jinja da
página inteira. Não existe uma segunda definição de "como é um card" morando no
JavaScript para divergir dessa.

## Usuários e dispositivos

Duas páginas, ambas restritas a administradores, alcançadas pelos ícones no topo do painel.

**Usuários** (`/manage/users`) — dois papéis:

| Papel | Pode |
|---|---|
| Operador | Terminal, upload e download em qualquer dispositivo |
| Administrador | O mesmo, mais gerenciar usuários e o cadastro de dispositivos |

Desativar é preferível a excluir: preserva o histórico de quem fez o quê e ainda bloqueia o login. O último administrador ativo não pode ser rebaixado, desativado nem excluído, e ninguém pode remover o próprio acesso de admin — sem isso é fácil ficar de fora do sistema com um clique.

O papel vai dentro do cookie de sessão, então uma mudança de papel só vale no próximo login do usuário.

**Dispositivos** (`/manage/devices`) — o cadastro da frota, com nome, responsável, descrição, o token de provisionamento e a revogação.

O painel mostra quem está **online**; esta página mostra quem está **cadastrado**. Um dispositivo pode estar online sem constar aqui, porque o relay aceita qualquer `device_id` cujo token derivado seja válido — e o painel marca esses casos, que é justo o que se quer notar.

### Revogação

Revogar desconecta o dispositivo em até 15 segundos e impede que ele autentique de novo. Como funciona, e por que assim:

O Postgres é a fonte de verdade, mas o relay **não** o consulta — ele precisa continuar funcionando se o frontend cair. Então o frontend espelha as revogações no Redis (`wdc:revoked:<device_id>`), que o relay já usa para presença e grants queimados.

A checagem **falha aberta**: se o Redis estiver inacessível, o relay aceita o dispositivo. Travar a frota inteira por causa de uma instabilidade do Redis seria um estrago muito maior do que honrar um token revogado por alguns minutos. O contrapeso é que o frontend republica todas as revogações ao iniciar, então uma perda de dados no Redis se corrige sozinha.

Duas consequências que valem saber:

- **Excluir do cadastro não revoga.** O relay autentica pelo token derivado, não pela tabela. Revogue primeiro se a intenção é tirar o dispositivo do ar — a interface avisa isso na confirmação. Um dispositivo revogado *mantém* a revogação ao ser excluído.
- **Para bloqueio garantido e imediato**, rotacione o `DEVICE_MASTER_SECRET` e re-emita os tokens da frota. A revogação é eventualmente consistente por construção.

## Testando só o relay

`server/docker-compose.yml` sobe **três réplicas** sobre um Redis compartilhado, mais um nginx que reproduz o hashing por `device_id` do ingress de produção. São duas portas de entrada, e cada uma expõe uma propriedade diferente:

| Entrada | O que exercita |
|---|---|
| `:8080` — o ingress | Afinidade. O dispositivo e as sessões de navegador para ele caem no **mesmo** pod, como em produção. |
| `:8001` `:8002` `:8003` — as réplicas | Comportamento cross-pod. Conecte na A e liste da B para provar que a presença é compartilhada. |

```bash
cd server
cp .env.example .env      # RELAY_SECRET e DEVICE_MASTER_SECRET
docker compose up -d

python scripts/smoke.py                                       # cruza réplicas
python scripts/smoke.py --a http://localhost:8080 --b http://localhost:8080   # pelo ingress
```

A regra de hash vive em `nginx/ingress.conf`. Ela usa `map` em vez do `if`+`set` de `deploy/k8s/server.yaml` — mesma chave, mas o ingress-nginx injeta seu snippet dentro do bloco `location`, onde `map` não é permitido. Para acompanhar o hash em ação, o nginx local devolve dois headers que não têm equivalente em produção:

```bash
curl -sI http://localhost:8080/device/device-01 | grep -i x-relay
# X-Relay-Upstream: 192.168.48.3:8000
# X-Relay-Hash-Key: device-01
```

A mesma chave sempre resolve para o mesmo pod; chaves diferentes se espalham. É exatamente o que um Service com round-robin *não* faz.

O smoke script faz os dois papéis: autentica um dispositivo falso na réplica A, assina um grant como o frontend faria e o resgata na réplica B, verificando que a presença é compartilhada, que o relay entrega nas duas direções e que um grant reutilizado é recusado.

```
✓ réplica A: pod=relay-a presença=redis multi-réplica=True
✓ réplica B: pod=relay-b presença=redis multi-réplica=True
✓ dispositivo autenticado na réplica A (pod relay-a)
✓ réplica B lista o dispositivo da A (dono: relay-a)
✓ grant aceito e sessão de terminal relayada até o dispositivo
✓ saída do dispositivo voltou pela sessão do navegador
✓ grant reutilizado foi recusado
✓ dispositivo saiu da lista ao desconectar
```

## Deploy em Kubernetes

```bash
kubectl create secret generic wdc-postgres \
  --from-literal=POSTGRES_USER=wdc \
  --from-literal=POSTGRES_PASSWORD="$(openssl rand -base64 24)" \
  --from-literal=POSTGRES_DB=wdc

kubectl create secret generic wdc-shared-secrets \
  --from-literal=RELAY_SECRET="$(openssl rand -base64 32)" \
  --from-literal=SESSION_SECRET="$(openssl rand -base64 32)" \
  --from-literal=DEVICE_MASTER_SECRET="$(openssl rand -base64 32)" \
  --from-literal=ADMIN_PASSWORD_HASH='<saída de tools.hash_password>' \
  --from-literal=DATABASE_URL='postgresql+asyncpg://wdc:<a senha acima>@wdc-postgres:5432/wdc'

kubectl apply -f deploy/k8s/redis.yaml
kubectl apply -f deploy/k8s/postgres.yaml
kubectl apply -f deploy/k8s/server.yaml
kubectl apply -f deploy/k8s/frontend.yaml
```

O `ADMIN_PASSWORD_HASH` só é usado **uma vez**, para semear a primeira conta quando a tabela de usuários está vazia. Depois disso as contas vivem no banco e essa variável é ignorada — rotacioná-la não reseta uma senha que já foi trocada pela interface.

O bloco que implementa o roteamento está em `deploy/k8s/server.yaml`:

```yaml
nginx.ingress.kubernetes.io/configuration-snippet: |
  set $dev_id "";
  if ($request_uri ~* "^/(?:device|terminal|file|download)/([^/?]+)") {
    set $dev_id $1;
  }
nginx.ingress.kubernetes.io/upstream-hash-by: "$dev_id"
```

Hashear por `$request_uri` **não** funciona: `/device/device-01` e `/terminal/device-01` são URIs diferentes e cairiam em pods diferentes. Pelo mesmo motivo, o `RELAY_WS_URL` do frontend precisa apontar para o **ingress**, nunca para o Service — o Service faz round-robin e o upgrade cairia na réplica errada.

**Ao escalar ou fazer deploy**, o anel de hash muda e parte dos dispositivos passa a pertencer a outro pod. Eles reconectam sozinhos, mas sessões de terminal abertas naquele instante caem.

## Rotas

**Frontend** (mesma origem do navegador, sessão por cookie)

| Método | Caminho | Descrição | Papel |
|---|---|---|---|
| `GET/POST` | `/login` | Formulário e verificação de credenciais | — |
| `GET` | `/logout` | Encerra a sessão | — |
| `GET` | `/?q=` | Painel; `q` filtra a frota, no servidor | qualquer |
| `GET` | `/partials/devices?q=` | Só a grade de cards, para a atualização periódica | qualquer |
| `GET` | `/terminal?device=` | Terminal | qualquer |
| `POST` | `/ws-grant` | Assina um grant de uso único | qualquer |
| `GET` | `/theme?to=&next=` | Guarda a preferência de tema num cookie | — |
| `GET` | `/config.js` | URL do relay para o navegador | — |
| `GET` | `/manage/users` | Lista de usuários | admin |
| `GET` | `/manage/users/novo`, `/manage/users/{id}` | Formulários de cadastro e edição | admin |
| `POST` | `/manage/users`, `/manage/users/{id}` | Cria e salva | admin |
| `POST` | `/manage/users/{id}/{ativar,desativar,excluir}` | Ações de linha | admin |
| `GET` | `/manage/devices` | Cadastro da frota | admin |
| `GET` | `/manage/devices/novo`, `/manage/devices/{id}` | Formulários | admin |
| `GET` | `/manage/devices/{id}/token` | Token de provisionamento | admin |
| `POST` | `/manage/devices`, `/manage/devices/{id}` | Cadastra e salva | admin |
| `POST` | `/manage/devices/{id}/{revogar,liberar,excluir}` | Ações de linha | admin |

Toda ação que muda alguma coisa é um `POST` de formulário respondido com `303`, e
a mensagem de resultado atravessa esse redirecionamento num cookie de vida curta.
Duas consequências práticas: recarregar a página não repete a operação, e um `GET`
solto — um prefetch, um crawler — não consegue revogar nem excluir nada.

As ações destrutivas (`excluir`, `revogar`) têm uma **página de confirmação** em
`GET` antes do `POST`, porque o que está em jogo não cabe numa linha de
`window.confirm`.

**Relay** (sem login; nada aqui é feito para um navegador chamar diretamente por HTTP)

| Método | Caminho | Credencial |
|---|---|---|
| `GET` | `/health` | — |
| `GET` | `/devices` | `X-Relay-Secret` (chamada interna) |
| `GET` | `/devices/{id}/token` | `X-Relay-Secret` (chamada interna) |
| `WS` | `/device/{id}` | Token do dispositivo |
| `WS` | `/terminal/{id}` | Grant, escopo `terminal` |
| `WS` | `/file/{id}` | Grant, escopo `upload` |
| `WS` | `/download/{id}` | Grant, escopo `download` |

Cada grant é vinculado a um `device_id` **e** a um escopo: um grant de upload não abre um shell.

## Estrutura

```
web_device_connections/
├── frontend/                 MVC — ver "Como o frontend é organizado"
│   ├── main.py               entrypoint (`uvicorn main:app`)
│   ├── app/
│   │   ├── config.py         um único modelo de settings (env + .env)
│   │   ├── main.py           monta a aplicação: lifespan, static, routers
│   │   ├── controllers/      base.py (guards e render), auth, dashboard,
│   │   │                     users, devices
│   │   ├── services/         security (senha, sessão, grants), users, devices,
│   │   │                     relay (cliente HTTP), revocation, errors
│   │   ├── models/           base (engine/sessão), user, device
│   │   └── views/
│   │       ├── templates/    _layout, _macros, _device_cards, index, login,
│   │       │                 terminal, users, user_form, devices,
│   │       │                 device_form, device_token, confirm
│   │       ├── static/       style.css e o pouco de JS que sobra:
│   │       │                 api.js (grant + fetch), common.js (toast/modal),
│   │       │                 script.js (upload/download)
│   │       └── __init__.py   Jinja, filtros de data/uptime, tema, flash
│   └── tools/hash_password.py
├── server/
│   ├── app/
│   │   ├── routers/          devices.py, terminal.py, files.py
│   │   ├── state.py          sockets vivos (locais ao processo)
│   │   └── core/
│   │       ├── presence.py   Redis ou memória: presença + grants queimados
│   │       ├── security.py   tokens de dispositivo, verificação de grants
│   │       └── config.py
│   ├── tools/mint_device_token.py
│   ├── scripts/smoke.py
│   ├── tests/                31 testes
│   ├── nginx/ingress.conf    hashing por device_id, espelho do ingress k8s
│   ├── docker-compose.yml    3 réplicas + redis + nginx, para testar só o relay
│   └── main.py
├── client/
│   ├── wdc_client/
│   │   ├── config.py         settings tipadas, validadas na partida
│   │   ├── paths.py          raízes permitidas para leitura e escrita
│   │   ├── shell.py          sessões PTY, limite e reaping dos processos
│   │   ├── transfer.py       recebimento e envio de arquivos
│   │   ├── connection.py     handshake, dispatch e backoff de reconexão
│   │   ├── protocol.py       tipos de mensagem e códigos de erro
│   │   └── __main__.py       entrypoint, shutdown em SIGTERM
│   └── tests/                73 testes
├── deploy/
│   ├── k8s/                  server.yaml (ingress + hash), frontend.yaml,
│   │                         redis.yaml, postgres.yaml
│   └── client/               unidade systemd + install.sh para os dispositivos
└── docker-compose.yml        stack completa
```

## Testes

```bash
cd server && pip install -r requirements-dev.txt && pytest
cd client && pip install -r requirements-dev.txt && pytest
```

No relay: a derivação de tokens de dispositivo, o ciclo de vida dos grants (assinatura, expiração, vínculo com device e escopo, uso único, falha fechada sem segredo), a checagem de `Origin`, e um teste ponta a ponta que conecta um dispositivo e verifica o relay nas duas direções.

No client: as raízes permitidas (traversal, symlink, prefixo irmão), o limite de tamanho no recebimento, o ciclo de checksum e cancelamento, o backoff com jitter, o dispatch que não derruba a conexão quando uma mensagem vem malformada, e as sessões PTY de verdade — inclusive um shell que ignora `SIGTERM` e o processo sendo colhido em vez de virar zumbi.

## Limitações

- A revogação é eventualmente consistente: o relay lê do Redis, e uma perda de dados ali libera o dispositivo até o frontend ressincronizar na inicialização. Para bloqueio garantido, rotacione o `DEVICE_MASTER_SECRET` e re-emita os tokens
- O esquema é criado com `create_all`; adicionar coluna funciona, mas alterar ou remover pede Alembic
- Arquivos ainda trafegam pelo pod em chunks de 64 KB — para volumes altos, URLs assinadas de object storage tirariam os bytes do control plane
- Bootstrap (só o CSS), Font Awesome e xterm.js vêm de CDN. As páginas continuam funcionando sem estilo se o CDN falhar, mas o terminal não funciona sem o xterm — em redes restritas, sirva esses arquivos junto com a aplicação
- Sem controle de acesso por dispositivo: quem entra opera a frota inteira
- Não há mais uma API JSON de usuários e dispositivos: os endpoints `/api/*` existiam só para alimentar o JavaScript que foi removido, e mantê-los seria duas descrições da mesma regra. As regras estão em `frontend/services.py`, prontas para uma API voltar por cima delas se algum dia for preciso
- `.github/workflows/ci.yml` ainda reflete o layout anterior à divisão em três componentes (instala `requirements.txt` da raiz e builda uma única imagem Docker); precisa ser atualizado para rodar lint/testes/build por componente (`server/`, `frontend/`, `client/`)

## Licença

MIT — veja [LICENSE](LICENSE).
