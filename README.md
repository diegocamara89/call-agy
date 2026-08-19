# call-agy

> 🇧🇷 [Português](#-português) · 🇺🇸 [English](#-english)

Camada de transporte para chamar o **`agy`** (Antigravity CLI do Google) a partir de código —
chamada única, paralelo, pipeline, fan-out/council e handoff estruturado — usando os modelos do
`agy` (incluindo **Claude Opus/Sonnet** servidos pelo Antigravity) como **executores e validadores
baratos para o Claude Code, economizando tokens da API Anthropic**.

---

## 🇧🇷 Português

### Por que isto importa (economia de tokens no Claude Code)

O `agy` dá acesso, numa única CLI, a Gemini 3.x, **Claude Opus 4.6 / Sonnet 4.6** e GPT-OSS. Com
esta camada, o **Claude Code** consegue **delegar trabalho para esses modelos via `agy`** em vez de
gastar tokens da API Anthropic:

- **Executor barato** — fan-out de subtarefas mecânicas (resumir, extrair, classificar, gerar
  rascunhos) para `Gemini Flash`/`Pro` em paralelo, e o Claude Code só revisa o resultado.
- **Validador / segunda opinião** — peça ao `Claude Opus/Sonnet (via agy)` para criticar ou validar
  um plano/diff sem consumir o orçamento de tokens da sua sessão Anthropic.
- **Council multi-modelo** — `fanout_synthesize` roda N modelos sobre a mesma pergunta e sintetiza
  um veredito, dando diversidade real de opinião por um custo marginal.

Resultado: o raciocínio caro fica no Claude Code; o volume vai para o `agy`.

### O transporte

Chamamos sempre `agy -p "..." --output-format json`, com **argv como lista** e `shell=False`. Isso
funciona por pipe, redirect e subprocess comum, e o envelope JSON traz muito mais que o texto:

```json
{"conversation_id":"74bf...","status":"SUCCESS","response":"4\n","duration_seconds":2.7,
 "num_turns":1,"usage":{"total_tokens":39656}}
```

Com `--json-schema` vem também `structured_output`, **já parseado** — nada de extrair JSON de prosa
na marra.

> **Sobre o bug TTY #76.** Versões antigas do `agy` retornavam `rc=0` e **0 bytes** quando o stdout
> não era um TTY real, e a solução era um ConPTY via [`pywinpty`](https://pypi.org/project/pywinpty/).
> **Verificado em 2026-08-15 no agy 1.1.13: o print mode foi corrigido** — pipe, redirect e
> subprocess funcionam. O ConPTY continua disponível em `transport="pty"` para quem roda versão
> antiga, mas não é mais o caminho padrão e `pywinpty` deixou de ser obrigatório.
>
> **O bug persiste no subcomando `agy models`**, que ainda trava com 0 bytes fora de TTY. Nunca o
> chame de um script — use `known_models(refresh=True)`, que arranca a lista oficial do próprio
> `agy` em ~3,4s e **zero tokens**.

### Superfícies

| Função | O que faz |
|---|---|
| `call_agy` / `call_agy_result` | chamada única robusta |
| `call_agy_parallel` | N jobs concorrentes (cap + retry/backoff, ordem preservada) |
| `pipeline` | encadeamento (a saída de A vira a entrada de B) |
| `fanout_synthesize` | fan-out de N modelos → síntese (base de um *council*) |
| `call_agy_handoff` | executa e devolve o handoff JSON do contrato `orchestrate` |
| `extract_json` | parser balanceado de 3 níveis, para quando não dá para impor schema |

### Requisitos

- **Python 3.10+** (testado em 3.12.9) · **sem dependências externas** no caminho padrão
- O binário **`agy` 1.1.13+** instalado e logado ([antigravity.google/cli](https://antigravity.google/cli)),
  no `PATH` ou em `~/AppData/Local/agy/bin/agy.exe`
- `pywinpty` **opcional**, só para `transport="pty"` (agy antigo)

Testado no Windows 11. O transporte JSON não depende de nada específico de Windows, mas só o
Windows foi verificado.

### Uso rápido

> Ajuste `<CAMINHO>` para onde você clonou este repositório.

```python
import sys
sys.path.insert(0, r"<CAMINHO>\call-agy\scripts")
from agy import call_agy

resp = call_agy("Quanto é 17*23? Responda só o número.",
                model="Gemini 3.7 Flash (Low)", timeout=90)
print(resp)   # -> 391
```

```python
# Diagnóstico estruturado: custo, sessão e status por chamada
from agy import call_agy_result
r = call_agy_result("Analise X", model="Gemini 3.1 Pro (High)", timeout=300, effort="high")
r.status, r.usage["total_tokens"], r.conversation_id

# Paralelo (fan-out) — resultados alinhados 1:1 com os jobs, na ordem
from agy import call_agy_parallel
results = call_agy_parallel([
    {"prompt": "Liste 3 riscos de X.", "model": "Gemini 3.1 Pro (Low)"},
    {"prompt": "Liste 3 riscos de X.", "model": "Gemini 3.7 Flash (Medium)"},
], max_concurrency=4, retries=2, timeout=180)

# Pipeline (encadeamento) — chain_conversation mantém UMA sessão viva no agy
from agy import pipeline
res = pipeline([
    {"model": "Gemini 3.1 Pro (Low)", "prompt": "Gere UMA ideia de feature. Conciso."},
    {"model": "Claude Opus 4.6 (Thinking)",
     "builder": lambda prev: f"Critique e aponte 3 riscos:\n\n{prev[-1].text}"},
], chain_conversation=False)
print(res["final"])

# Saída estruturada: dict já parseado, sem regex
r = call_agy_result("Avalie o risco do deploy de sexta.", model="Gemini 3.7 Flash (Low)",
                    json_schema={"type": "object",
                                 "properties": {"nivel": {"type": "string"}},
                                 "required": ["nivel"]})
print(r.structured["nivel"])

# Fan-out -> síntese (council)
from agy import fanout_synthesize
verdict = fanout_synthesize(
    "Devo lançar um curso de $297 ou um workshop de $97 primeiro?",
    models=["Gemini 3.1 Pro (High)", "Gemini 3.7 Flash (High)", "Claude Opus 4.6 (Thinking)"],
    synth_model="Claude Opus 4.6 (Thinking)", max_concurrency=5, seed=42,
)
print(verdict.text)
```

Mais exemplos (template `{prev}`, `conversation_id`, handoff, `jobs.json`, `steps.json`,
retrocompatibilidade) em [`examples.md`](examples.md).

### CLI

```bash
python scripts/agy.py single   -p "Quanto é 17*23?" --model "Gemini 3.7 Flash (Low)"
python scripts/agy.py parallel --jobs jobs.json --max-concurrency 4 --retries 2
python scripts/agy.py pipeline --steps steps.json --chain-conversation
python scripts/agy.py fanout   -p "Pergunta?" --models "Gemini 3.1 Pro (High);Claude Opus 4.6 (Thinking)"
python scripts/agy.py handoff  -p "Rode os testes e reporte"
python scripts/agy.py models   --refresh
```

Exit codes: **0** tudo ok · **1** falha parcial · **2** erro fatal.

### Modelos (`--model`)

Passe a string **exata**. Modelo inválido **não passa mais em silêncio**: com
`--output-format json` o `agy` responde `rc=1`, `status:"ERROR"` e lista os IDs válidos no campo
`error` — em ~4s e sem gastar token. A validação pré-call (`validate_model=True`) continua ligada
só para economizar esse round-trip.

Catálogo verificado em **2026-08-15** (14 IDs, agy 1.1.13). Prefira sempre a versão mais alta de
cada família.

| ID literal | Família | Uso sugerido |
|---|---|---|
| `Gemini 3.7 Flash (Low/Medium/High)` | Gemini | **linha atual** — probes, triagem, análise leve |
| `Gemini 3.6 Flash (Low/Medium/High)` | Gemini | legado / diversidade |
| `Gemini 3.5 Flash (Low/Medium/High)` | Gemini | legado |
| `Gemini 3.1 Pro (Low/High)` | Gemini | análise pontual / arquitetural |
| `Claude Sonnet 4.6 (Thinking)` | Claude | raciocínio, review |
| `Claude Opus 4.6 (Thinking)` | Claude | síntese/chairman (`SYNTH_MODEL`) |
| `GPT-OSS 120B (Medium)` | GPT-OSS | diversidade no council |

Omita `--model` para usar o default de `~/.gemini/antigravity-cli/settings.json` (nesta instalação,
`Gemini 3.7 Flash (High)`). Os IDs refletem uma instalação específica — rode
`python scripts/agy.py models --refresh` para ver os seus. O catálogo é volátil: veja **Manutenção
do catálogo** no `SKILL.md` (revisão a cada 15 dias).

### Estrutura

```
call-agy/
├── SKILL.md            # spec da skill (transporte, modelos, como chamar)
├── README.md           # este arquivo
├── examples.md         # exemplos copiáveis
├── requirements.txt    # vazio no caminho padrão; pywinpty só para transport="pty"
├── LICENSE             # MIT
├── scripts/
│   ├── agy.py          # fonte da verdade (transporte + paralelo/pipeline/fanout/handoff + CLI)
│   └── call_agy.py     # shim de retrocompatibilidade
└── tests/
    ├── test_agy.py           # puros (SKIP_LIVE=1) + vivos
    └── legacy-pty-probes/    # probes exploratórios do ConPTY
```

### Testes

```bash
SKIP_LIVE=1 python tests/test_agy.py    # só os puros (offline, instantâneo)
python tests/test_agy.py                # + os vivos (chamam o agy, alguns minutos)
```

---

## 🇺🇸 English

### Why this matters (saving Claude Code tokens)

`agy` exposes Gemini 3.x, **Claude Opus 4.6 / Sonnet 4.6** and GPT-OSS through a single CLI. With
this transport layer, **Claude Code can offload work to those models via `agy`** instead of spending
Anthropic API tokens:

- **Cheap executor** — fan out mechanical subtasks (summarize, extract, classify, draft) to
  `Gemini Flash`/`Pro` in parallel; Claude Code only reviews the results.
- **Validator / second opinion** — have `Claude Opus/Sonnet (via agy)` critique or validate a
  plan/diff without burning your Anthropic session budget.
- **Multi-model council** — `fanout_synthesize` runs N models on the same question and synthesizes a
  verdict, giving real opinion diversity at marginal cost.

The expensive reasoning stays in Claude Code; the volume goes to `agy`.

### The transport

We always call `agy -p "..." --output-format json`, with **argv as a list** and `shell=False`. That
works over pipes, redirects and plain subprocesses, and the JSON envelope carries far more than the
text: `conversation_id`, `status`, `error`, `duration_seconds`, `num_turns`, `usage`, plus
`structured_output` (**already parsed**) when you pass `--json-schema`.

> **About TTY bug #76.** Older `agy` builds returned `rc=0` and **0 bytes** whenever stdout was not
> a real TTY, and the fix was a ConPTY via [`pywinpty`](https://pypi.org/project/pywinpty/).
> **Verified 2026-08-15 on agy 1.1.13: print mode is fixed** — pipe, redirect and subprocess all
> work. ConPTY remains available as `transport="pty"` for old builds, but it is no longer the
> default and `pywinpty` is no longer required.
>
> **The bug does persist in the `agy models` subcommand**, which still hangs with 0 bytes outside a
> TTY. Never call it from a script — use `known_models(refresh=True)`, which pulls the official list
> out of `agy` itself in ~3.4s and **zero tokens**.

### Surfaces

| Function | What it does |
|---|---|
| `call_agy` / `call_agy_result` | robust single call |
| `call_agy_parallel` | N concurrent jobs (concurrency cap + retry/backoff, order preserved) |
| `pipeline` | chaining (output of A becomes input of B) |
| `fanout_synthesize` | fan-out over N models → synthesis (council engine) |
| `call_agy_handoff` | runs a task and returns the `orchestrate` handoff JSON contract |
| `extract_json` | 3-level balanced parser, for when you cannot enforce a schema |

### Requirements

- **Python 3.10+** (tested on 3.12.9) · **no external dependencies** on the default path
- The **`agy` 1.1.13+** binary installed and logged in
  ([antigravity.google/cli](https://antigravity.google/cli)), on `PATH` or at
  `~/AppData/Local/agy/bin/agy.exe`
- `pywinpty` **optional**, only for `transport="pty"` (old agy)

Tested on Windows 11. The JSON transport has no Windows-specific dependency, but only Windows was
verified.

### Quick start

> Set `<PATH>` to where you cloned this repo.

```python
import sys
sys.path.insert(0, r"<PATH>\call-agy\scripts")
from agy import call_agy

print(call_agy("What is 17*23? Answer the number only.",
               model="Gemini 3.7 Flash (Low)", timeout=90))   # -> 391
```

See [`examples.md`](examples.md) for parallel, pipeline, fan-out, structured output, handoff, CLI
and `jobs.json`/`steps.json` examples.

### CLI

```bash
python scripts/agy.py single   -p "What is 17*23?" --model "Gemini 3.7 Flash (Low)"
python scripts/agy.py parallel --jobs jobs.json --max-concurrency 4 --retries 2
python scripts/agy.py pipeline --steps steps.json --chain-conversation
python scripts/agy.py fanout   -p "Question?" --models "Gemini 3.1 Pro (High);Claude Opus 4.6 (Thinking)"
python scripts/agy.py handoff  -p "Run the tests and report"
python scripts/agy.py models   --refresh
```

Exit codes: **0** all ok · **1** partial failure · **2** fatal error.

### Notes

- Pass the **exact** model string. An invalid model no longer falls back silently: `agy` returns
  `rc=1`, `status:"ERROR"` and lists the valid IDs in `error`. Pre-call validation stays on by
  default only to save that round-trip.
- On timeout we kill the **process tree** (`taskkill /F /T` on Windows) — killing only the direct
  child leaves MCP grandchildren holding the pipes.
- Concurrency numbers and model IDs reflect empirical tests on one machine — tune to your hardware.

## License

MIT — see [`LICENSE`](LICENSE).
