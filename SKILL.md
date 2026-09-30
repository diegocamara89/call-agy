---
name: call-agy
description: Use para delegar trabalho ao agy (Antigravity CLI do Google, modelos Gemini e Claude pela conta Google) a partir de codigo - segunda opiniao, council ou verificacao cruzada com outra familia de modelo, lote grande de leitura ou extracao que gastaria cota do Claude, gerar imagem, ver video. Tambem quando o agy voltar vazio, travar, esgotar a cota ou parecer usar o modelo errado. TRIGGERS - chamar agy, delegar ao agy, segunda opiniao, verificacao cruzada, council com agy, agy em paralelo, poupar cota do Claude, gerar imagem, agy gera imagem, agy gera video, ver video com agy, agy vazio, agy travou, cota do agy, modelos do agy.
---

# call-agy - delegar ao agy (Antigravity CLI) de forma confiavel

`agy` e o CLI agentico do Google (antigravity.google/cli), no estilo do Claude Code. Esta skill e o
**motor reusavel** para chama-lo de dentro de codigo: chamada unica, paralelo, encadeamento,
fan-out -> sintese, handoff estruturado e geracao de imagem. Tudo vive em `scripts/agy.py`.

Duas camadas, nao confunda: **comandos de shell** (`agy -p`, `agy help`, `agy update`,
`agy changelog`) sao o que esta skill usa; **slash commands** (`/config`, `/model`, `/skills`...) so
existem dentro do TUI interativo — nunca passe um como argv. Administrar o agy (plugins, settings,
logs) nao e desta skill: ver `references/environment.md`.

---

## Quando delegar ao agy (e quando nao)

A doutrina curta mora no `CLAUDE.md` do usuario; aqui fica o detalhe. Regra geral: **tente o agy
nos casos abaixo; se ele falhar ou travar, o subagente proprio do Claude e o plano B. Cota
esgotada e outra coisa: pare e avise o usuario (ver "Cota").**

| Situacao | Quem faz | Por que |
|---|---|---|
| Gerar imagem | agy (`generate_image`) | so ele tem a ferramenta nativa |
| Ver video (entender fluxo, cortes) | agy | le `.mp4`; numero pequeno de tela, leia em quadro de resolucao cheia |
| Segunda opiniao, council, verificacao cruzada | agy | outra familia de modelo = opiniao independente |
| Lote grande de leitura, extracao, contagem | agy | poupa a cota do Claude; peca `arquivo:linha` e confira |
| Implementar sob testes ja escritos | agy (`call_agy_handoff`) | o teste e o juiz; gate mecanico antes de ler o diff |
| Busca curta no repo, edicao que precisa desta sessao (MCP, Blender, Bambu) | subagente proprio | contexto e ferramentas locais |
| Concluir, dar veredito, escrever os testes | Claude | o agy **coleta bem e conclui mal** (ver `references/delegacao.md`) |
| Dado sigiloso (processo, investigacao, dado pessoal, extrato, credencial) | **nunca o agy** | servico externo; ele le o disco sozinho e `--sandbox` nao confina. Anonimize ou use subagente proprio |
| Video ou audio GERADO | ninguem via agy | nao existe ferramenta; ele monta com ffmpeg e descreve como se fosse gerativo |

A saida do agy e **candidato, nao fato**: reconfira cada `arquivo:linha` e decida pelo efeito
verificado (testes, `git status`, arquivo), nunca pelo `status` da chamada.

---

## Transporte

**Sempre `-p` + `--output-format json`, argv como LISTA, `shell=False`** (o modulo ja faz):

```json
{"conversation_id":"74bf...","status":"SUCCESS","response":"4\n","duration_seconds":2.7,
 "num_turns":1,"usage":{"input_tokens":39623,"output_tokens":33,"total_tokens":39656}}
```

O envelope separa vazio de falha (`status` + `error`), traz `structured_output` ja parseado com
`--json-schema`, o custo (`usage`) e o `conversation_id` para continuar a sessao.

- **`agy models` trava fora de TTY.** Nunca o chame de script: use `known_models(refresh=True)`
  (~4 s, zero tokens).
- Prompt com `{}`, `|`, `%`, `&` chega intacto (argv em lista, o `cmd.exe` nao ve). Teto pratico
  ~32 mil caracteres por argv: dado grande vai em arquivo e o prompt cita o caminho.
- `transport="pty"` (pywinpty) so existe para agy antigo.

### Flags (implementadas em `_build_argv`)

| Flag | kwarg | Observacao |
|---|---|---|
| `--model` | `model` | sempre passado (ver "Modelo padrao") |
| `--conversation` / `--continue` | `conversation` / `continue_last` | retoma sessao |
| `--json-schema` | `json_schema` | raiz precisa ser `{"type": "object"}` (agy 1.2.14+) |
| `--dangerously-skip-permissions` | `skip_permissions` | auto-aprova tool calls |
| `--sandbox` | `sandbox` | restringe **comandos de terminal**; NAO impede escrita fora do `cwd` |
| `--add-dir` | `add_dirs` | contexto extra, nao confinamento |
| `--mode` | `mode` | ex. `accept-edits` |
| `--effort` | `effort` | so para modelo SEM tier no nome (hoje nenhum) |
| `--print-timeout` | (derivado de `timeout`) | relogio do agy alinhado ao do modulo |

Existem no agy e o modulo ainda nao expoe: `--project`/`--new-project`, `--log-file`,
`--disable-slash-commands`.

---

## Catalogo de modelos

**Catalogo verificado em 2026-09-30** (agy 1.2.14, 14 IDs). Estas regras valem para toda a skill:

- **Modelo padrao.** Sem `model`, o modulo usa `DEFAULT_MODEL = Gemini 3.8 Flash (High)`. Ele
  nunca omite `--model`: o agy cru usaria o default do `settings.json` do usuario, que em
  2026-09-30 era **Claude Opus 4.6 (Thinking)** — o balde de cota menor. Script que chama `agy -p`
  direto, fora do modulo, precisa passar `--model` explicito pelo mesmo motivo.
- **Tier (High) para qualquer resposta que alguem vai ler** (fan-out, council, pipeline, handoff).
  `(Low)`/`(Medium)` so para probe e triagem.
- **Nunca `GPT-OSS 120B`** — desatualizado; listado so por completude.
- **Nunca `effort` com modelo que ja tem tier no nome**: o agy responde `INVALID_MODEL`. Escolha o
  tier trocando o ID.
- **Council**: familias diferentes (`Gemini 3.1 Pro (High)`, `Gemini 3.8 Flash (High)`,
  `Claude Sonnet 4.6 (Thinking)`); 3.8 + 3.7 + 3.6 Flash sao o mesmo modelo, nao opinioes.
  Chairman: `SYNTH_MODEL = Claude Opus 4.6 (Thinking)`.

| ID literal (`--model "..."`) | Uso |
|---|---|
| `Gemini 3.8 Flash (High)` | **DEFAULT_MODEL**; implementar sob teste; imagem (`IMAGE_MODEL`) |
| `Gemini 3.8 Flash (Medium)` | triagem |
| `Gemini 3.8 Flash (Low)` | probes (**PROBE_MODEL**) |
| `Gemini 3.7 Flash (High/Medium/Low)`, `Gemini 3.6 Flash (High/Medium/Low)` | legado |
| `Gemini 3.1 Pro (High)` / `(Low)` | analise pesada / pontual |
| `Claude Sonnet 4.6 (Thinking)` | raciocinio, review (balde Claude) |
| `Claude Opus 4.6 (Thinking)` | chairman (**SYNTH_MODEL**, balde Claude) |
| `GPT-OSS 120B (Medium)` | nao usar |

Modelo invalido erra alto: `rc=1`, `status: INVALID_MODEL` e a lista dos IDs validos, em ~4 s e
sem token. A validacao local (`validate_model=True`) so poupa esse round-trip num fan-out com typo.

---

## Cota

- **Dois baldes**, da conta Google do usuario: **Gemini** (a familia inteira esgota junto) e
  **Claude** (menor). Cada chamada carrega ~40 mil tokens fixos de contexto do agy; uma imagem
  mediu ~100 mil (65 mil de cache).
- A mesma frase `Individual quota reached ... Resets in X` aparece com janela **curta** (visto
  `16s`) e **longa** (`4h12m`). O modulo decide pelo tempo: reset `>= QUOTA_FATAL_SECONDS` (600 s)
  vira **`status: QUOTA_EXHAUSTED`**, que nunca e retentado; num lote, os jobs **do mesmo balde**
  que ainda nao comecaram voltam `QUOTA_EXHAUSTED` sem chamar o agy (o outro balde segue). Janela
  curta e cota por minuto sao retentadas esperando o reset (ate 120 s). `call_agy` levanta
  `AgyError`; a CLI sai com codigo **3**; `fanout_synthesize` nao chama o chairman se nenhum
  advisor respondeu por cota; `pipeline` para mesmo com `fail_fast=False`.
- **Regra: cota esgotada = parar e avisar o usuario para trocar a conta.** Nao durma esperando
  renovar e nao troque sozinho por subagente proprio.

---

## Timeout

- `FLASH_TIMEOUT = 90` (Flash Low/Medium), `THINK_TIMEOUT = 300` (High/Thinking, imagem),
  `DEFAULT_TIMEOUT = 180`. Nunca abaixo de 60 s (cold-start). Sobrescreva por job/step.
- Em timeout o modulo mata a **arvore** de processos (`taskkill /F /T`): so `proc.kill()` deixaria
  os servidores MCP netos segurando os pipes.
- **`TIMEOUT` nao significa trabalho nao feito**: o agy trabalha no disco. Confira o efeito.

---

## Como chamar

```python
import sys
sys.path.insert(0, r"<CAMINHO>\call-agy\scripts")
from agy import (call_agy, call_agy_result, call_agy_parallel, pipeline, fanout_synthesize,
                 call_agy_handoff, generate_image)
```

**Chamada unica.** `call_agy` devolve o texto e levanta `AgyError` em modelo invalido, timeout e
cota. `call_agy_result` nunca levanta por falha do agy: devolve `CallResult`.

```python
texto = call_agy("Quanto e 17*23? So o numero.", model="Gemini 3.8 Flash (Low)", timeout=90)
r = call_agy_result("Analise X", model="Gemini 3.1 Pro (High)", timeout=300)
r.ok, r.status, r.text, r.conversation_id, r.usage["total_tokens"]
```

`status`: `OK` | `EMPTY` | `TIMEOUT` | `AUTH_ERROR` | `INVALID_MODEL` | `QUOTA_EXHAUSTED` | `ERROR`.

**Paralelo.** Ordem preservada; falha parcial nunca aborta o lote.

```python
jobs = [{"prompt": "Liste 3 riscos de X.", "model": "Gemini 3.1 Pro (High)"},
        ("Liste 3 riscos de X.", "Claude Sonnet 4.6 (Thinking)")]
results = call_agy_parallel(jobs, max_concurrency=4, retries=2, timeout=180)
```

Concorrencia: default 4, teto 6 (limite e a RAM/CPU desta maquina; N=5 mediu ~4x sem 429).
Lotes >20 em ondas. Retry em `EMPTY`/`TIMEOUT`/`AUTH_ERROR`/429/cota de janela curta; fatal em
`INVALID_MODEL` e `QUOTA_EXHAUSTED`.

**Pipeline.** Cada step: kwargs de `call_agy_result` + `builder` (`Callable[[list[CallResult]],
str]`, canonico) ou `prompt` com `{prev}`/`{step_0}`/`{all}`. `chain_conversation=True` mantem uma
sessao so (mesmo modelo em todos os steps).

```python
res = pipeline([{"model": "Gemini 3.1 Pro (High)", "prompt": "Gere UMA ideia. Conciso."},
                {"model": "Claude Opus 4.6 (Thinking)",
                 "builder": lambda prev: f"Critique:\n\n{prev[-1].text}"}], timeout=180)
res["ok"], res["final"], res["failed_step"]
```

**Saida estruturada e handoff.** `json_schema` (raiz objeto) -> `r.structured` ja parseado.
`call_agy_handoff` preenche o contrato do `orchestrate` (`status`, `changed_files`, `tests_run`,
`next_action`...) e alimenta o gate de `references/delegacao.md`. Sem schema possivel:
`extract_json(texto)`.

**Fan-out -> sintese.** `fanout_synthesize(pergunta, models=[...], synth_model=SYNTH_MODEL)` roda
os modelos em paralelo e sintetiza com as respostas anonimizadas. As 5 personas e o peer-review
sao do `llm-council`, que usa estas primitivas.

### CLI e codigos de saida

```bash
python scripts/agy.py single   -p "..." [--model "ID"] [--conversation ID] [--json]
python scripts/agy.py parallel --jobs jobs.json [--max-concurrency 4] [--retries 2]
python scripts/agy.py pipeline --steps steps.json [--chain-conversation] [--no-fail-fast]
python scripts/agy.py fanout   -p "..." --models "A;B;C" [--synth-model "ID"]
python scripts/agy.py handoff  -p "..." [--model "ID"]     # stdout = so o JSON do contrato
python scripts/agy.py image    -p "..." --dest saida.png [--ref foto.png]
python scripts/agy.py models   [--refresh]
```

**0** ok; **1** falha (parcial em lote); **2** erro de uso/ambiente; **3** cota do agy esgotada.

---

## Imagem

So a imagem e nativa (`generate_image`); video e audio nao existem como ferramenta. Use o helper:

```python
r = generate_image("A red ceramic mug on a wooden desk, soft daylight. Portrait framing 4:5.",
                   "saida/caneca.png", refs=["refs/produto.png"])
r.ok, r.path, r.width, r.height, r.status   # OK | RAW_ONLY | NO_IMAGE | QUOTA_EXHAUSTED | TIMEOUT
```

- Amarra a imagem a **esta** chamada pela pasta `brain/<conversation_id>/` do envelope — duas
  geracoes simultaneas nao trocam de arquivo. "O PNG mais novo do brain" pega o de outra geracao
  ou a referencia enviada (`.user_uploaded`).
- Acrescenta ao prompt o pedido de salvar em PNG; e esse PNG que ele colhe. So a saida crua (JPG
  com timestamp) = `RAW_ONLY`, erro alto — nao entrega a crua no lugar.
- Medido em 2026-09-30 (agy 1.2.14): 45 s, ~100 mil tokens, pedido 4:5 saiu **896x1200 (3:4)**.
  Nao ha flag de tamanho: confira `width`/`height` e recorte localmente se a proporcao importar.
- `NO_IMAGE` com `ok` na chamada = recusa: o motivo esta em `r.call.text`.

Gabarito de prompt, imagem de referencia e tabela de falhas: `references/imagem.md`.

---

## Limites praticos

- **Video lido** (`.mp4` no prompt) e visto em resolucao reduzida: use para entender fluxo e
  cortes; para ler numero pequeno de tela, extraia o quadro em resolucao cheia. Com video no
  contexto, cada passo leva 1-2 min (medido no agy 1.2.5).
- **Video/audio gerado**: pedir nao da erro — ele monta com `ffmpeg`/`python` e descreve o
  resultado como gerativo (medido: "video" com diferenca de 1 nivel de cinza entre o 1o e o
  ultimo quadro). Se quer ffmpeg, chame ffmpeg.

---

## Delegacao sob contrato (resumo)

Medido em trabalho real; detalhe, gate e casos em `references/delegacao.md`.

- **Coleta bem, conclui mal.** Numeros brutos certos; interpretacao errada com aparencia de rigor.
- **Nunca autor e juiz da mesma coisa**: ele implementa, voce escreve os testes.
- **Gate mecanico antes do diff**: hash dos testes inalterado, `git status` so com os arquivos
  permitidos, `HEAD` intacto, suite completa verde, lint limpo. Terceira rodada sem verde: assuma.
- **Isolamento: detectar, nao confinar.** Investigar em copia descartavel conferida por hash;
  implementar no repo com git como rede. Fora do repo nao ha defesa senao container/VM.

---

## Erros comuns

| Sintoma | Causa | Correcao |
|---|---|---|
| Script trava sem imprimir nada | chamou `agy models` em subprocess | `known_models(refresh=True)` |
| `INVALID_MODEL` num ID do catalogo | `effort` com modelo que ja tem tier | remova `effort` |
| Cota do Claude some rapido | chamada sem `--model` herdou o Claude do `settings.json` | passe `model` (o modulo ja passa) |
| `QUOTA_EXHAUSTED` | cota da conta esgotada | pare e avise o usuario |
| `structured` e `None` com schema | modelo devolveu prosa | `extract_json`; `call_agy_handoff` ja faz |
| Timeout de 120 s levou 280 s | kill sem `/T` | use o modulo, nao chame o agy por fora |
| `status: EMPTY` com `raw_len=0` | agy antigo | `agy update` |

Se `status`/`error` nao derem a causa, os logs do agy estao em `references/environment.md`.

---

## Arquivos

- `scripts/agy.py` - **fonte da verdade** (transporte, `call_agy*`, paralelo, pipeline, fan-out,
  handoff, `generate_image`, catalogo, CLI).
- `tests/test_agy.py` - puros (`SKIP_LIVE=1`, offline) + vivos (chamam o agy).
- `examples.md` - exemplos copiaveis.
- `references/imagem.md` - gabarito de prompt, referencia, falhas de imagem.
- `references/delegacao.md` - evidencia da divisao de papeis, gate, isolamento.
- `references/environment.md` - pastas do Antigravity e logs.
- `requirements.txt` - vazio no caminho padrao; `pywinpty` so para `transport="pty"`.

**Posicionamento:** esta skill e o transporte. `llm-council` define a metodologia do council e usa
`call_agy_parallel`/`call_agy`/`SYNTH_MODEL`; `orchestrate` roteia entre IAs e usa
`call_agy_handoff`.

---

## Manutencao do catalogo (revisao a cada 15 dias)

| Campo | Valor |
|---|---|
| **Ultima verificacao** | **2026-09-30** |
| **Proxima revisao (a partir de)** | **2026-10-15** |
| **Versao do agy verificada** | **1.2.14** |
| **Default do settings.json do usuario** | `Claude Opus 4.6 (Thinking)` (o modulo nao usa) |
| **Total de IDs** | 14 |

Se hoje for >= "Proxima revisao" e a tarefa envolver escolher modelo, rode
`python scripts/agy.py models --refresh` (zero tokens) e atualize **os dois lados**:
`scripts/agy.py` (`KNOWN_MODELS`, `PROBE_MODEL`, `DEFAULT_MODEL`, `IMAGE_MODEL`, `SYNTH_MODEL`,
`CATALOG_CHECKED`) e este arquivo (catalogo, datas, versao). Se um ID mudar, atualize tambem
`PRO`/`SONNET`/`FLASH` em `llm-council/scripts/council.py`. O teste puro
`test_catalogo_sincronizado` falha se as datas divergirem. **Sem historico**: sobrescreva os
valores; se nada mudou, atualize so as datas. Leia tambem o `agy changelog`: mudancas de flag
(como a raiz objeto do `--json-schema` na 1.2.14) entram na tabela de flags.
