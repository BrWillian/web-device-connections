# wdc-client

O agente que roda no dispositivo. Mantém um WebSocket autenticado com o relay e serve,
por cima dele, terminais interativos (PTY) e transferência de arquivos nas duas
direções.

```
wdc_client/
├── config.py      settings tipadas, lidas do ambiente e validadas na partida
├── paths.py       as raízes que o relay pode ler e escrever neste dispositivo
├── shell.py       sessões PTY: limite, encerramento e reaping dos processos
├── transfer.py    recebimento (browser → dispositivo) e envio (dispositivo → browser)
├── connection.py  handshake, dispatch das mensagens e backoff de reconexão
├── protocol.py    tipos de mensagem e códigos de erro — contrato de fio
└── __main__.py    entrypoint, com shutdown limpo em SIGTERM
```

## Instalação no dispositivo

```bash
sudo ./deploy/client/install.sh
```

Instala em `/opt/wdc-client`, cria a unidade systemd, e escreve
`/etc/wdc-client/client.env` a partir do `.env.example` (sem sobrescrever, se já
existir). Preencha `DEVICE_ID`, `DEVICE_TOKEN` e `SERVER_URL` e suba:

```bash
sudo systemctl start wdc-client
journalctl -u wdc-client -f
```

O token sai do relay: `python -m tools.mint_device_token <device-id>`.

## Rodando direto

```bash
pip install -r requirements.txt
cp .env.example .env      # preencha DEVICE_ID, DEVICE_TOKEN e SERVER_URL
python -m wdc_client
```

## O que o dispositivo decide sozinho

O relay roteia bytes; ele não escolhe o que acontece neste filesystem. Três
limites são checados aqui, e é aqui que precisam continuar:

| Variável | Padrão | O que impede |
|---|---|---|
| `ALLOWED_ROOTS` | `~` | Escrita em `/etc/cron.d`, leitura de `~/.ssh` — inclusive via `..` ou symlink, porque a checagem é feita sobre o caminho já resolvido |
| `MAX_FILE_SIZE` | 100 MB | Um upload encher o disco do dispositivo, mesmo que o `size` anunciado minta |
| `MAX_SESSIONS` | 10 | Um número descontrolado de PTYs consumindo a memória da placa |

Todas as opções estão comentadas em [.env.example](.env.example).

## Erros

Cada erro reportado ao relay carrega `error_code` — um slug estável — e `error`,
uma frase em inglês para o log. Os códigos estão em
[`protocol.py`](wdc_client/protocol.py) e a tradução para a interface vive em
`frontend/app/views/static/script.js`. Renomear um código quebra a tradução em
silêncio.

## Testes

```bash
pip install -r requirements-dev.txt
pytest
```

Os testes de `shell.py` sobem shells de verdade: a parte que mais importa aqui —
a fiação do PTY, e o dicionário de sessões andando junto com a tabela de
processos — não sobrevive a ser mockada.
