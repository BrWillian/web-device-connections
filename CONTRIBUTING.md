# Contribuindo com o Web Device Connections

## Código de conduta

- Seja respeitoso e inclusivo
- Dê retorno construtivo
- Foque no que é melhor para o projeto
- Tenha empatia com os demais participantes

## Como o projeto está dividido

Três componentes que rodam em máquinas diferentes e têm dependências diferentes:

```
web_device_connections/
├── frontend/     Páginas, login, sessão e assinatura de grants. Roda perto do usuário.
├── server/       Relay puro. Roda no Kubernetes, N réplicas. Sem login e sem usuários.
├── client/       Agente com PTY. Roda em cada dispositivo.
└── deploy/       Manifests do k8s (ingress com hash por device_id) e a
                  instalação do agente nos dispositivos (systemd + install.sh).
```

A separação não é cosmética — vale entender antes de mover código de lugar:

- **Autenticação de pessoas pertence ao `frontend/`.** Se você se pegar adicionando `passlib`, sessão ou rota de login no `server/`, a mudança está no componente errado. O relay só verifica tokens de dispositivo e grants assinados.
- **O `client/` tem dependências mínimas de propósito** (`websockets`, `python-dotenv`). Ele é instalado em dispositivos; não arraste FastAPI para lá. É por isso que a configuração dele é uma dataclass escrita à mão em vez de `pydantic-settings` como no relay: em placa armv7 sem wheel, `pydantic-core` significa compilar Rust.
- **O que o dispositivo aceita, quem decide é o dispositivo.** O relay roteia bytes; ele não escolhe caminho no filesystem do dispositivo. `ALLOWED_ROOTS`, `MAX_FILE_SIZE` e `MAX_SESSIONS` são checados no `client/`, e é lá que precisam continuar — um relay comprometido não pode virar escrita em `/etc/cron.d`.
- **`error_code` faz parte do contrato de fio.** As mensagens do dispositivo são em inglês; a tradução vive em `frontend/app/views/static/script.js`. Renomear um código quebra a tradução em silêncio.
- **`RELAY_SECRET` é compartilhado entre `frontend/` e `server/`.** Mudar o formato do grant em um exige mudar a verificação no outro.

Cada componente tem seu próprio `requirements.txt`, `Dockerfile` e `.env.example`.

## Ambiente de desenvolvimento

```bash
git clone https://github.com/seuusuario/web_device_connections.git
cd web_device_connections

# Gere os segredos e preencha os dois .env — RELAY_SECRET idêntico nos dois
python -c "import secrets; print(secrets.token_urlsafe(32))"
cp server/.env.example server/.env
cp frontend/.env.example frontend/.env

docker compose up -d
```

Para trabalhar só no relay, sem subir a UI:

```bash
cd server
docker compose up -d      # duas réplicas + redis
python scripts/smoke.py   # exercita dispositivo + grant ponta a ponta
```

Em Python, um venv por componente:

```bash
cd server && python -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt
```

## Estilo

- PEP 8; `black` para formatar, `flake8` para checar
- Docstrings explicando *por quê*, não *o quê* — o código já diz o quê
- Funções pequenas e com um propósito só

### Mensagens de commit

```
feat: adiciona autenticação por dispositivo
fix: corrige timeout no upload de arquivos
docs: atualiza instruções de instalação
test: adiciona testes para o controller de terminal
```

## Testes

```bash
cd server && pytest
```

Antes de abrir um PR, garanta que a suíte passa. Ao mexer em qualquer coisa relacionada a confiança — tokens de dispositivo, grants, escopos, `Origin` — **escreva o teste do caminho negativo**, não só do positivo. A suíte atual cobre grant forjado, expirado, de outro dispositivo, de outro escopo, reutilizado, e o caso de segredo ausente. Um bug aí não aparece como erro: aparece como shell aberto.

Se a mudança atravessa componentes, rode também `server/scripts/smoke.py` contra o compose, que é o que valida o comportamento entre réplicas.

## Pull requests

1. Atualize o README se o comportamento mudou
2. Garanta que os testes passam
3. Descreva como você testou
4. Abra o PR e aguarde revisão

```markdown
## Descrição
O que muda e por quê

## Tipo
- [ ] Correção
- [ ] Nova funcionalidade
- [ ] Mudança incompatível
- [ ] Documentação

## Como testei

## Checklist
- [ ] Segue o estilo do projeto
- [ ] Testes adicionados ou atualizados
- [ ] Documentação atualizada
- [ ] Sem mudança incompatível (ou documentada)
```

## Onde ajuda é bem-vinda

- Transferência de arquivos por object storage, tirando os bytes do control plane
- Múltiplos usuários com permissões por dispositivo
- Servir Bootstrap, Font Awesome e xterm.js localmente, para redes restritas
- Persistência: histórico de comandos e auditoria
- Testes de integração para reconexão e rebalanceamento de hash

## Relatando problemas

Inclua versão do Python, sistema operacional, passos para reproduzir, comportamento esperado e obtido, e a saída de erro. Para problemas de conexão, o `GET /health` do relay diz qual backend de presença está ativo e se a réplica pode ser escalada — vale colar junto.

## Licença

Contribuindo, você concorda em licenciar sua contribuição sob a licença MIT.
