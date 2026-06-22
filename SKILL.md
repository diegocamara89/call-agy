---
name: call-agy
description: "Chama o agy (Antigravity CLI do Google) de forma confiavel a partir de codigo/automacao, contornando o bug TTY #76 que faz o agy retornar 0 bytes fora de um TTY real. Tres modos: chamada unica robusta, paralelo (N jobs concorrentes com cap+retry) e encadeamento/pipeline (saida de A vira entrada de B), mais um helper fan-out->sintese (base de council). TRIGGERS (PT): 'chamar agy', 'rodar agy', 'usar o agy', 'agy via script', 'agy nao retorna nada', 'agy retorna vazio', 'agy em paralelo', 'rodar varios agy', 'fan-out com agy', 'pipeline de agy', 'encadear agy', 'council com agy', 'multiplos modelos do agy'. TRIGGERS (EN): 'call agy', 'run agy', 'agy returns empty', 'agy in parallel', 'agy pipeline', 'chain agy'. Use sempre que precisar capturar a saida do agy programaticamente, escolher modelo via --model, ou orquestrar varias chamadas do agy (single/paralelo/encadeado). Esta skill e o motor de transporte reusavel: o llm-council e o orchestrate consomem esta skill quando o backend e o agy."
---

# call-agy - chamando o Antigravity CLI (agy) de forma confiavel

`agy` e o CLI agentico do Google (antigravity.google/cli), no estilo do Claude Code.
Voce roda `agy -p "prompt"` para uma chamada nao-interativa e escolhe o modelo com `--model "ID"`.

So que tem um problema que quebra toda automacao: **`agy -p` retorna 0 bytes quando o stdout
nao e um TTY real** (pipe, redirect, subprocess comum). Esta skill resolve isso de uma vez e
expoe quatro superficies em cima da solucao: chamada unica, paralelo, encadeamento (pipeline) e
um helper fan-out -> sintese.

Esta skill e o **motor de transporte reusavel**, nao um produto final. Outras skills (como
`llm-council` e `orchestrate`) chamam estas funcoes quando o backend de inferencia e o `agy`.
Veja "Posicionamento" no fim.

---

## Quando usar esta skill

Use quando qualquer um destes for verdade:

- Voce precisa **capturar a saida do `agy` em codigo/automacao** (e nao no terminal interativo).
- O `agy -p` esta **retornando vazio / 0 bytes** quando voce roda por pipe, redirect ou subprocess.
- Voce quer **escolher o modelo** por chamada via `--model`.
- Voce quer rodar **varias chamadas do `agy` em paralelo** (fan-out de N modelos/prompts).
- Voce quer **encadear** chamadas: a resposta de um agente vira parte do prompt do proximo.
- Voce esta montando um **council** (fan-out -> peer-review anonimo -> chairman) com `agy` como backend.

Nao use para rodar `agy` interativamente (so chame `agy` direto no terminal). Esta skill e para
o caminho programatico/automatizado.

---

## O bug do TTY #76 e a solucao ConPTY (LEIA ANTES DE TUDO)

**Sintoma:** `agy -p "prompt"` retorna `rc=0` e **0 bytes** sempre que o stdout nao e um TTY real.
O `rc=0` e inutil para diagnosticar: resposta valida, bug-TTY 0-byte e modelo invalido **todos**
dao `rc=0`. O bug afeta `agy -p`, `agy models`, e tudo mais quando rodado por:

- pipe: `agy -p "..." | cat`  -> volta **vazio**
- redirect: `agy -p "..." > out.txt`  -> arquivo **vazio**
- subprocess comum (`subprocess.run`, `os.popen`, etc.)  -> **vazio**

Bug confirmado: github.com/google-antigravity/antigravity-cli/issues/76.

**Solucao confirmada e funcionando:** rodar o `agy` dentro de um **ConPTY** (pseudo-terminal do
Windows) via **`pywinpty`**. O ConPTY engana o `agy` (ele "ve" um terminal real), entao ele emite
a saida normalmente. Depois e so **limpar as sequencias ANSI/CSI/OSC** e descartar as frames de
spinner (strip CR-aware).

Receita canonica (e o que `scripts/agy.py` faz por baixo):

1. `winpty.PtyProcess.spawn([agy, "-p", prompt, "--model", M], dimensions=(50, 220), cwd=home)`
   - **argv como LISTA**, nunca string de shell (IDs de modelo tem parenteses/espacos; o shell
     corromperia).
2. Uma thread le chunks de **4096** ate `EOFError`/timeout (`done = threading.Event()`).
3. Tres sinais de fim: `finished = done.wait(timeout)`, `alive_after = p.isalive()`,
   `rc = p.exitstatus`. EOF limpo = `finished and not alive_after and rc==0`.
4. `p.close()` **sempre** no finally (mata o ConPTY orfao / spinner / hang de MCP).
5. **Strip CR-aware:** OSC -> CSI -> outros ESC; depois, **por linha fisica** (split `\n`),
   `line.split('\r')` e ficar com o **ultimo segmento NAO-vazio**. Isso descarta as frames do
   spinner Braille ('...Fetching available models...') sem grudar no primeiro texto real.
6. Filtra linhas de chrome (`No hook installed`, `Fetching available`, `exec @upstash`,
   `context7-mcp`, vazias) e junta -> resposta limpa.

> **AVISO ConPTY (critico):** NUNCA tente capturar o `agy` por `pipe`/`redirect`/`subprocess`
> simples. **NUNCA use `agy -p ... | cat`** num probe (volta vazio e te engana). SEMPRE passe por
> `pywinpty` (este modulo). Para um probe manual, use o proprio modulo:
> `cd /tmp && python scripts/agy.py single -p "PROMPT" --model "Gemini 3.5 Flash (Low)"`
> (ou `python agy_capture.py -p "PROMPT" --model "Gemini 3.5 Flash (Low)"`). Se precisar inspecionar
> o raw, filtre o ruido com: `2>&1 | grep -viE 'No hook installed|Fetching available|^\s*$'`.

Requisitos confirmados: **Python 3.12.9 + pywinpty 3.0.5** (Windows-only). Veja `requirements.txt`.

---

## Catalogo de modelos (IDs literais para `--model`)

Passe a string **exata** em `--model`. IDs validos (constante `KNOWN_MODELS`):

| ID literal (`--model "..."`)        | Familia  | Velocidade            | Uso sugerido                                   |
|-------------------------------------|----------|-----------------------|------------------------------------------------|
| `Gemini 3.5 Flash (Low)`            | Gemini   | rapido (~13s, terse)  | probes, triagem, fan-out leve (**PROBE_MODEL**) |
| `Gemini 3.5 Flash (Medium)`         | Gemini   | rapido                | triagem                                         |
| `Gemini 3.5 Flash (High)`           | Gemini   | medio (pode spinnar)  | analise leve                                    |
| `Gemini 3.1 Pro (Low)`              | Gemini   | medio (~16s)          | analise pontual                                 |
| `Gemini 3.1 Pro (High)`             | Gemini   | lento (Thinking)      | analise arquitetural, fan-out serio             |
| `Claude Sonnet 4.6 (Thinking)`      | Claude   | lento                 | raciocinio, review                              |
| `Claude Opus 4.6 (Thinking)`        | Claude   | lento (~14-18s+)      | **DEFAULT** do settings.json E chairman/sintese |
| `GPT-OSS 120B (Medium)`             | GPT-OSS  | medio                 | diversidade extra no council                    |

Notas:
- **Default** (quando voce NAO passa `--model`): vem de `~/.gemini/antigravity-cli/settings.json`
  -> hoje `"Claude Opus 4.6 (Thinking)"` (`DEFAULT_MODEL`). **Omita** `--model` para usar o
  configurado (omitir e diferente de passar o ID).
- Para **probes/testes**, use `Gemini 3.5 Flash (Low)` (`PROBE_MODEL`) e mantenha probes pequenos
  (1-3 chamadas) - cada chamada e inferencia real e custa tempo.
- Para **council**, rotacione familias diferentes (ex.: `Gemini 3.1 Pro`, `Gemini 3.5 Flash`,
  `Claude Opus 4.6`, `GPT-OSS 120B`) para ter diversidade real de opiniao.
- `toolPermission: "always-proceed"` no settings.json -> o `agy` nao bloqueia em permissoes.

> **ALERTA - FALLBACK SILENCIOSO em modelo invalido:** o `agy` **NAO** erra com um `--model`
> invalido. Ele faz fallback silencioso para o default (`Claude Opus 4.6 (Thinking)`), `rc=0`, zero
> sinal de erro ('Totally Invalid Model 9000' respondeu normal num probe). Sem validacao, um typo
> ('flash low' minusculo, 'Gemini 3.1 Pro' sem tier) rodaria **Opus caro** achando que e Flash e
> **quebraria o council** (todos os advisors virariam o mesmo modelo). Por isso a **validacao
> pre-call e obrigatoria** (`validate_model=True`, default): o modulo checa contra `KNOWN_MODELS`
> ANTES de spawnar e levanta `AgyError`. NUNCA confie na saida para detectar modelo errado.

---

## Timeout e tier de modelo

Use o tier certo (cold-start e ~13s mesmo para Flash; nunca use `<60s`):

- `FLASH_TIMEOUT = 90`  -> Flash Low/Medium.
- `THINK_TIMEOUT = 300` -> Pro High, Sonnet/Opus Thinking, qualquer High/Thinking.
- `DEFAULT_TIMEOUT = 180` -> compromisso usado por `call_agy`/`parallel`/`pipeline` quando o tier
  nao e especificado. Sobrescreva por job/step com `job["timeout"]` / `step["timeout"]`.

O `--print-timeout` interno do `agy` e 5m; mantenha o timeout Python `<= 300s` alinhado.

---

## Como chamar

Tudo vive em `scripts/agy.py` (fonte da verdade). Importe como modulo ou use a CLI.

### Modo 1 - chamada unica

```python
import sys
sys.path.insert(0, r"<CAMINHO>\call-agy\scripts")   # ajuste <CAMINHO> para onde voce clonou
from agy import call_agy

resp = call_agy("Quanto e 17*23? Responda so o numero.",
                model="Gemini 3.5 Flash (Low)", timeout=90)
print(resp)
```

`call_agy` levanta `AgyError` em modelo invalido e em timeout sem texto. Saida vazia "legitima"
volta `""` (sem raise) salvo `raise_on_empty=True`. Para diagnostico fino, use a superficie
estruturada `call_agy_result(...) -> CallResult` (`ok`, `text`, `status`, `error`, `elapsed_s`,
`attempts`, `raw_len`); ela **nunca** levanta por EMPTY/TIMEOUT/AUTH.

CLI (imprime texto cru):

```bash
python scripts/agy.py single -p "Quanto e 17*23?" --model "Gemini 3.5 Flash (Low)"
```

### Modo 2 - paralelo (fan-out)

N jobs concorrentes, com **cap de concorrencia** e **retry/backoff**. Cada `agy.exe` e um processo
isolado via ConPTY -> threads bastam (`ThreadPoolExecutor`; o `read()` do PTY libera o GIL).

```python
from agy import call_agy_parallel

jobs = [
    {"prompt": "Liste 3 riscos de X.", "model": "Gemini 3.1 Pro (Low)"},
    {"prompt": "Liste 3 riscos de X.", "model": "Gemini 3.5 Flash (Medium)"},
    ("Liste 3 riscos de X.", "Claude Sonnet 4.6 (Thinking)"),   # tupla tambem vale
]
results = call_agy_parallel(jobs, max_concurrency=4, retries=2, timeout=180)
# results[i] (CallResult) corresponde a jobs[i], NA ORDEM. Falha parcial nunca aborta o lote.
```

CLI (jobs em JSON; imprime `CallResult[]`):

```bash
python scripts/agy.py parallel --jobs jobs.json --max-concurrency 4 --retries 2
```

**Concorrencia (numeros empiricos desta maquina):** default `max_concurrency=4`, **max 6**. Probe
real: N=3 modelos distintos -> 3/3 ok, ~2.75x speedup, **zero 429**; N=5 (incl. 3x o mesmo Flash
Low) -> 5/5 ok, **~4x** speedup, **zero 429**, zero corrupcao cruzada de stdout, zero torn-write em
`history.jsonl`. O cap e tunado pela **maquina** (RAM/CPU), nao pelo backend: o `agy` usa
Antigravity/Google direto (o quirk de 429 do `gemini`/OpenRouter **nao** se aplica). Mesmo assim o
retry/backoff fica mantido por seguranca (N>5 e rajadas sustentadas nao foram testados). Para council
de 5, suba para `max_concurrency=5`. Para lotes grandes (>20 jobs), processe em **ondas** de
tamanho=cap.

Politica de retry (por job, independente; falha de um nunca derruba o lote):
- **retry** quando `status in {EMPTY, TIMEOUT}` ou a saida/erro casa `429|rate.?limit|quota|too
  many|overloaded|timeout`. Backoff = `retry_backoff*attempt + jitter(0..1s)`.
- **fatal, sem retry:** `INVALID_MODEL` e `AUTH_ERROR` (permanentes).

### Modo 3 - encadeamento / pipeline

A saida de cada step alimenta o proximo. Cada step e um dict com (`model`, `timeout`?) + **exatamente
um** de:
- `"builder"`: `Callable[[list[CallResult]], str]` - recebe **todas** as saidas anteriores (nao so a
  imediata) e devolve o prompt. **Contrato canonico:** robusto a `{`/`}` literais (codigo/JSON/LaTeX)
  e permite anonimizar/concatenar N saidas. Acessa `prev[-1].text`, `prev[0].text`, `prev[i].model`.
- `"prompt"`: str com placeholders `{prev}` / `{step_0}` / `{all}` (acucar; chave ausente fica
  literal, nunca levanta). Use so quando as saidas nao tem `{`/`}` literais.

```python
from agy import pipeline, template

steps = [
    {"model": "Gemini 3.1 Pro (Low)",
     "prompt": "Gere UMA ideia de feature para um app de seguros. Conciso."},
    {"model": "Claude Opus 4.6 (Thinking)",
     "builder": lambda prev: f"Critique e aponte 3 riscos:\n\n{prev[-1].text}"},
]
res = pipeline(steps, timeout=180, fail_fast=True)
# res -> {"ok", "results": list[CallResult], "final": str, "failed_step": int|None}
print(res["final"])
```

`initial != None` injeta um `CallResult` sintetico (ok, `text=initial`) no indice 0 do historico.
`fail_fast=True` (default) para no 1o step nao-ok; `fail_fast=False` segue e o builder decide
(`CallResult.ok` e load-bearing: nunca alimente texto de erro como resposta valida).

CLI (so suporta `prompt`/template; `builder` Callable e exclusivo do import Python):

```bash
python scripts/agy.py pipeline --steps steps.json
```

### Helper - fan-out -> sintese (base do council)

```python
from agy import fanout_synthesize

verdict = fanout_synthesize(
    "Devo lancar um curso de $297 ou um workshop de $97 primeiro?",
    models=["Gemini 3.1 Pro (High)", "Gemini 3.5 Flash (High)", "Claude Opus 4.6 (Thinking)"],
    synth_model="Claude Opus 4.6 (Thinking)",
    max_concurrency=5, seed=42,
)
print(verdict.text)   # verdict e um CallResult
```

Roda os N models em paralelo sobre o mesmo prompt, depois 1 chamada de sintese. O builder padrao
**anonimiza/embaralha** as respostas ok como `Response A..N` (`seed` p/ reproducao). Esta skill **nao**
implementa as 5 personas/peer-review do `llm-council` - fornece so o motor fan-out -> reduce; o
council completo o `llm-council` monta compondo as 3 primitivas.

CLI:

```bash
python scripts/agy.py fanout -p "Pergunta?" --models "Gemini 3.1 Pro (High);Claude Opus 4.6 (Thinking)"
```

### CLI - exit codes

`single` imprime texto cru; `parallel`/`pipeline`/`fanout` imprimem JSON (`CallResult` serializado);
`models` lista `KNOWN_MODELS` (`--refresh` roda `agy models` via ConPTY). Exit: **0** se tudo ok;
**1** falha parcial (parallel/pipeline/fanout com algum `ok=False`); **2** em
`AgyError`/`FileNotFound`/`ImportError`.

---

## O que cada arquivo faz

- `SKILL.md` - este arquivo (frontmatter, quando usar, bug ConPTY, modelos, como chamar).
- `scripts/agy.py` - **fonte da verdade**: `_find_agy`, `_strip_ansi` (CR-aware), `_classify`,
  `call_agy_result`/`call_agy`, `call_agy_parallel`, `pipeline`, `fanout_synthesize`, `template`,
  `known_models`, `CallResult`, `AgyError`, e o CLI `argparse`.
- `scripts/call_agy.py` - shim de retrocompatibilidade: preserva a interface antiga
  `call_agy(prompt, timeout, model)` (ordem antiga, `validate_model=False`) reexportando de `agy.py`.
- `examples.md` - exemplos prontos (single, paralelo, pipeline com builder e template, fanout,
  import, jobs.json/steps.json) copiaveis.
- `requirements.txt` - `pywinpty>=3.0.5` (a unica dependencia real).

---

## Posicionamento (vs llm-council / orchestrate)

- Esta skill e o **motor de transporte reusavel**: "como chamar o `agy` de forma confiavel" (single
  / paralelo / encadeado / fan-out). Ela **NAO** decide *quando* counciliar nem implementa personas.
- **`llm-council`**: define a *metodologia* (5 advisors, peer-review anonimo, chairman). Quando o
  backend for o `agy`, o council compoe as 3 primitivas daqui: `call_agy_parallel` (fan-out +
  reviews) e `call_agy`/`fanout_synthesize` (chairman). A anonimizacao/personas vivem no council.
- **`orchestrate`**: roteia entre IAs (Claude planeja -> Codex executa -> Claude valida). Se um worker
  for o `agy`, `orchestrate` chama `call_agy` daqui. Detalhe-chave: o `gemini` via OpenRouter da 429
  com 2+ simultaneas; o `agy` e backend diferente (Antigravity/Google direto), entao o cap/retry
  desta skill e calibrado separado (4-6 nesta maquina, zero 429 ate N=5).

Regra pratica: se o pedido e "como faco o `agy` me devolver texto / rodar varios `agy`", e esta
skill. Se e "qual decisao tomar com varias opinioes" ou "quem executa o que", e `llm-council` /
`orchestrate` - que por baixo podem chamar esta.
