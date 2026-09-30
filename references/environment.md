# Ambiente do Antigravity CLI (agy)

> Mapa de referencia vindo da skill oficial `antigravity-cli`, **nao medido** por esta skill. Se
> algo divergir da sua maquina, confie na observacao e atualize este arquivo.

Serve para quando o `CallResult.error` sozinho nao explica a falha. Administrar o agy (plugins,
settings, historico) e feito direto no terminal, nao via `call-agy`.

## Pastas

| Caminho | Descricao |
|---|---|
| `~/.gemini/antigravity-cli/settings.json` | config persistente: modelo default (`model`), permissoes, UI |
| `~/.gemini/antigravity-cli/log/cli-*.log` | logs de execucao — primeiro lugar quando `ERROR`/`TIMEOUT` nao da causa |
| `~/.gemini/antigravity-cli/brain/<conversation_id>/` | o que cada conversa gerou (imagens, artefatos) — ver `imagem.md` |
| `~/.gemini/antigravity-cli/conversations/` | historico de conversas |
| `~/.gemini/antigravity-cli/history.jsonl` | historico de prompts |
| `~/.gemini/antigravity-cli/plugins/` | plugins do agy |

Chaves comuns do `settings.json`: `model` (default quando nenhum `--model` e passado — o modulo
`agy.py` sempre passa um, ver `DEFAULT_MODEL`), `permissions.allow`, `enableTerminalSandbox`,
`allowNonWorkspaceAccess`.

## Diagnostico pelo log

```bash
tail -n 60 "$(ls -t ~/.gemini/antigravity-cli/log/cli-*.log | head -n 1)"
```

Procure: auth (OAuth expirado, keyring travado), cota, violacao de sandbox, timeout de subprocesso,
falha de rede com o endpoint do Google.
