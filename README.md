# call-agy

> 🇧🇷 [Português](#-português) · 🇺🇸 [English](#-english)

Chame o **`agy`** (Antigravity CLI do Google) de forma confiável a partir de código no Windows —
contornando o bug que faz o `agy -p` retornar **0 bytes** fora de um terminal real — e use os
modelos do `agy` (incluindo **Claude Opus/Sonnet** servidos pelo Antigravity) como **executores e
validadores baratos para o Claude Code, economizando tokens da API Anthropic**.

---

## 🇧🇷 Português

### Por que isto importa (economia de tokens no Claude Code)

O `agy` dá acesso, numa única CLI, a Gemini 3.x, **Claude Opus 4.6 / Sonnet 4.6** e GPT-OSS. Com
esta camada de transporte, o **Claude Code** consegue **delegar trabalho para esses modelos via
`agy`** em vez de gastar tokens da API Anthropic:

- **Executor barato** — fan-out de subtarefas mecânicas (resumir, extrair, classificar, gerar
  rascunhos) para `Gemini Flash`/`Pro` em paralelo, e o Claude Code só revisa o resultado.
- **Validador / segunda opinião** — peça ao `Claude Opus/Sonnet (via agy)` para criticar ou validar
  um plano/diff sem consumir o orçamento de tokens da sua sessão Anthropic.
- **Council multi-modelo** — `fanout_synthesize` roda N modelos sobre a mesma pergunta e sintetiza
  um veredito, dando diversidade real de opinião por um custo marginal.

Resultado: o raciocínio caro fica no Claude Code; o volume vai para o `agy`.

### O problema (bug TTY #76)

`agy -p "prompt"` retorna `rc=0` e **0 bytes** sempre que o `stdout` **não** é um TTY real:

```bash
agy -p "..." | cat        # volta vazio
agy -p "..." > out.txt     # arquivo vazio
subprocess.run([...])      # vazio
```

O `rc=0` é inútil para diagnosticar — resposta válida, bug 0-byte e modelo inválido **todos** dão
`rc=0`. Bug confirmado: [google-antigravity/antigravity-cli#76](https://github.com/google-antigravity/antigravity-cli/issues/76).
Até onde sei, **não há solução pública para isto** — este repositório é a primeira.

**Solução:** rodar o `agy` dentro de um **ConPTY** (pseudo-terminal do Windows) via
[`pywinpty`](https://pypi.org/project/pywinpty/). O ConPTY "engana" o `agy` (ele vê um terminal
real) e a saída volta normalmente; depois limpamos ANSI/CSI/OSC e descartamos o spinner (CR-aware).

### Quatro superfícies

| Função | O que faz |
|---|---|
| `call_agy` / `call_agy_result` | chamada única robusta |
| `call_agy_parallel` | N jobs concorrentes (cap + retry/backoff, ordem preservada) |
| `pipeline` | encadeamento (a saída de A vira a entrada de B) |
| `fanout_synthesize` | fan-out de N modelos → síntese (base de um *council*) |

### Requisitos

- **Windows** (a solução depende do ConPTY) · **Python 3.12+** (testado em 3.12.9)
- **`pywinpty>=3.0.5`** — única dependência
- O binário **`agy`** instalado e logado ([antigravity.google/cli](https://antigravity.google/cli)),
  no `PATH` ou em `~/AppData/Local/agy/bin/agy.exe`

```bash
pip install -r requirements.txt
```

### Uso rápido

> Ajuste `<CAMINHO>` para onde você clonou este repositório.

```python
import sys
sys.path.insert(0, r"<CAMINHO>\call-agy\scripts")
from agy import call_agy

resp = call_agy("Quanto é 17*23? Responda só o número.",
                model="Gemini 3.5 Flash (Low)", timeout=90)
print(resp)   # -> 391
```

```python
# Paralelo (fan-out) — resultados alinhados 1:1 com os jobs, na ordem
from agy import call_agy_parallel
jobs = [
    {"prompt": "Liste 3 riscos de X.", "model": "Gemini 3.1 Pro (Low)"},
    {"prompt": "Liste 3 riscos de X.", "model": "Gemini 3.5 Flash (Medium)"},
]
results = call_agy_parallel(jobs, max_concurrency=4, retries=2, timeout=180)

# Pipeline (encadeamento)
from agy import pipeline
res = pipeline([
    {"model": "Gemini 3.1 Pro (Low)", "prompt": "Gere UMA ideia de feature. Conciso."},
    {"model": "Claude Opus 4.6 (Thinking)",
     "builder": lambda prev: f"Critique e aponte 3 riscos:\n\n{prev[-1].text}"},
])
print(res["final"])

# Fan-out -> síntese (council)
from agy import fanout_synthesize
verdict = fanout_synthesize(
    "Devo lançar um curso de $297 ou um workshop de $97 primeiro?",
    models=["Gemini 3.1 Pro (High)", "Gemini 3.5 Flash (High)", "Claude Opus 4.6 (Thinking)"],
    synth_model="Claude Opus 4.6 (Thinking)", max_concurrency=5, seed=42,
)
print(verdict.text)
```

Mais exemplos (template `{prev}`, `jobs.json`, `steps.json`, retrocompatibilidade) em
[`examples.md`](examples.md).

### CLI

```bash
python scripts/agy.py single   -p "Quanto é 17*23?" --model "Gemini 3.5 Flash (Low)"
python scripts/agy.py parallel --jobs jobs.json --max-concurrency 4 --retries 2
python scripts/agy.py pipeline --steps steps.json
python scripts/agy.py fanout   -p "Pergunta?" --models "Gemini 3.1 Pro (High);Claude Opus 4.6 (Thinking)"
python scripts/agy.py models   [--refresh]
```

Exit codes: **0** tudo ok · **1** falha parcial · **2** erro fatal.

### Modelos (`--model`)

Passe a string **exata**. O `agy` faz **fallback silencioso** para o default em modelo inválido
(sem erro, `rc=0`), então a validação pré-call é obrigatória por padrão (`validate_model=True`).

| ID literal | Família | Uso sugerido |
|---|---|---|
| `Gemini 3.5 Flash (Low/Medium/High)` | Gemini | probes, triagem, análise leve |
| `Gemini 3.1 Pro (Low/High)` | Gemini | análise pontual / arquitetural |
| `Claude Sonnet 4.6 (Thinking)` | Claude | raciocínio, review |
| `Claude Opus 4.6 (Thinking)` | Claude | síntese/chairman (default) |
| `GPT-OSS 120B (Medium)` | GPT-OSS | diversidade no council |

Omita `--model` para usar o default de `~/.gemini/antigravity-cli/settings.json`. Os IDs refletem uma
instalação específica — rode `agy models` para ver os seus.

### Estrutura

```
call-agy/
├── SKILL.md            # spec da skill (bug ConPTY, modelos, como chamar)
├── README.md           # este arquivo
├── examples.md         # exemplos copiáveis
├── requirements.txt    # pywinpty>=3.0.5
├── LICENSE             # MIT
├── scripts/
│   ├── agy.py          # fonte da verdade (transporte + paralelo/pipeline/fanout + CLI)
│   └── call_agy.py     # shim de retrocompatibilidade
└── tests/              # scripts de scratch do ConPTY
```

**Plataforma:** somente Windows.

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

### The problem (TTY bug #76)

`agy -p "prompt"` returns `rc=0` and **0 bytes** whenever `stdout` is **not** a real TTY (pipe,
redirect, plain subprocess). The `rc=0` is useless for diagnosis — a valid answer, the 0-byte bug,
and an invalid model **all** return `rc=0`. Confirmed:
[google-antigravity/antigravity-cli#76](https://github.com/google-antigravity/antigravity-cli/issues/76).
As far as I know there is **no public fix for this** — this repo is the first.

**Fix:** run `agy` inside a **ConPTY** (Windows pseudo-terminal) via
[`pywinpty`](https://pypi.org/project/pywinpty/). The ConPTY tricks `agy` into seeing a real
terminal, so output flows normally; we then strip ANSI/CSI/OSC and drop spinner frames (CR-aware).

### Four surfaces

| Function | What it does |
|---|---|
| `call_agy` / `call_agy_result` | robust single call |
| `call_agy_parallel` | N concurrent jobs (concurrency cap + retry/backoff, order preserved) |
| `pipeline` | chaining (output of A becomes input of B) |
| `fanout_synthesize` | fan-out over N models → synthesis (council engine) |

### Requirements

- **Windows** (the fix depends on ConPTY) · **Python 3.12+** (tested on 3.12.9)
- **`pywinpty>=3.0.5`** — the only dependency
- The **`agy`** binary installed and logged in ([antigravity.google/cli](https://antigravity.google/cli)),
  on `PATH` or at `~/AppData/Local/agy/bin/agy.exe`

```bash
pip install -r requirements.txt
```

### Quick start

> Set `<PATH>` to where you cloned this repo.

```python
import sys
sys.path.insert(0, r"<PATH>\call-agy\scripts")
from agy import call_agy

print(call_agy("What is 17*23? Answer the number only.",
               model="Gemini 3.5 Flash (Low)", timeout=90))   # -> 391
```

See [`examples.md`](examples.md) for parallel, pipeline, fan-out, CLI and `jobs.json`/`steps.json`
examples.

### CLI

```bash
python scripts/agy.py single   -p "What is 17*23?" --model "Gemini 3.5 Flash (Low)"
python scripts/agy.py parallel --jobs jobs.json --max-concurrency 4 --retries 2
python scripts/agy.py pipeline --steps steps.json
python scripts/agy.py fanout   -p "Question?" --models "Gemini 3.1 Pro (High);Claude Opus 4.6 (Thinking)"
python scripts/agy.py models   [--refresh]
```

Exit codes: **0** all ok · **1** partial failure · **2** fatal error.

### Notes

- Pass the **exact** model string. `agy` silently falls back to the default on an invalid model
  (no error, `rc=0`), so pre-call validation is on by default. Run `agy models` for your IDs.
- Concurrency numbers and model IDs reflect empirical tests on one machine — tune to your hardware.
- **Platform:** Windows only.

## License

MIT — see [`LICENSE`](LICENSE).
