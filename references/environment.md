# Ambiente e governanca do Antigravity CLI (agy)

> Fonte: skill oficial `antigravity-cli` do catalogo Hermes — **nao verificado por medicao direta**
> nesta skill (diferente do resto do `call-agy`, que documenta so o que foi medido). Trate como mapa
> de referencia, nao como fato medido. Se algo aqui divergir do que voce observar na sua maquina,
> confie na observacao e atualize este arquivo.

`call-agy` (a skill-mae deste arquivo) e o motor de **execucao programatica** do `agy`. Este arquivo
e o mapa de **onde olhar** quando uma chamada falha de um jeito que o `CallResult.error` sozinho nao
explica, e de **onde nao pisar** (administracao do `agy`, que nao e desta skill).

---

## Duas camadas — nao confunda

1. **Comandos de shell** (`agy -p "..."`, `agy help`, `agy plugin`, `agy update`, `agy changelog`) —
   rodam no terminal/subprocess. E aqui que o `call-agy` opera.
2. **Slash commands da sessao interativa** (`/config`, `/permissions`, `/skills`, `/mcp`, `/model`,
   `/agents`) — so existem DENTRO do TUI do `agy` aberto interativamente. **Nunca passe um slash
   command como argv de shell** — `agy /skills` nao faz o que parece (nem erra de forma clara).

Administrar o `agy` em si (instalar plugin, trocar settings, ver historico) e escopo da skill
oficial `antigravity-cli`/administracao do Hermes, nao do `call-agy`.

---

## Topologia de pastas

| Caminho | Descricao |
|---|---|
| `~/.gemini/antigravity-cli/` | raiz de dados do app |
| `~/.gemini/antigravity-cli/settings.json` | config persistente (modelo default, permissoes, UI) — e daqui que vem o `DEFAULT_MODEL` que o `SKILL.md` do `call-agy` documenta |
| `~/.gemini/antigravity-cli/log/cli-*.log` | logs de execucao — primeiro lugar a olhar quando `status: ERROR`/`TIMEOUT` nao da causa clara |
| `~/.gemini/antigravity-cli/conversations/` | historico de conversas gravado em disco |
| `~/.gemini/antigravity-cli/history.jsonl` | historico de prompts/comandos submetidos |
| `~/.gemini/antigravity-cli/plugins/` | plugins internos do `agy` (nao confundir com plugins do Hermes) |

### Chaves comuns em `settings.json`

- `permissions.allow` — comandos pre-autorizados.
- `enableTerminalSandbox` — liga/desliga o sandbox de terminal (`true`/`false`).
- `allowNonWorkspaceAccess` — se o agente pode ler arquivos fora do diretorio do projeto.
- `model` — modelo default quando nenhum `--model` e passado (e o que o `call-agy` chama de
  `DEFAULT_MODEL`).

---

## Diagnostico rapido quando `status`/`error` nao bastam

```bash
# log mais recente
ls -t ~/.gemini/antigravity-cli/log/cli-*.log | head -n 1

# ultimas 60 linhas
tail -n 60 "$(ls -t ~/.gemini/antigravity-cli/log/cli-*.log | head -n 1)"
```

Procure por: falha de auth (OAuth expirado / keyring travado), violacao de sandbox, timeout de
subprocesso, ou falha de rede com o endpoint do Google. O `CallResult` do `call-agy` (`status`,
`error`) ja cobre o caso comum — os logs servem para quando esses dois campos nao bastam.

---

## Gestao de plugins do `agy` (fora do escopo de execucao do `call-agy`)

- `agy plugin list` — lista plugins instalados no `agy`.
- `agy plugin install <target>` — instala um plugin.
- `agy plugin enable|disable <nome>` — ativa/desativa.

Isso e administracao interativa — chame direto no terminal, nao via `call_agy_result`/
`call_agy_parallel`/`pipeline`.
