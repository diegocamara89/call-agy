# call-agy - exemplos

Todos assumem que `pywinpty` esta instalado (`pip install -r requirements.txt`) e que o `agy`
esta no PATH (ou em `~/AppData/Local/agy/bin/agy.exe`).

Para importar como modulo de OUTRA skill/script, aponte para a pasta `scripts/`:

```python
import sys
sys.path.insert(0, r"<CAMINHO>\call-agy\scripts")   # ajuste <CAMINHO> para onde voce clonou
from agy import (call_agy, call_agy_result, call_agy_parallel, pipeline,
                 fanout_synthesize, template, CallResult, AgyError, KNOWN_MODELS)
```

> AVISO: nunca capture o `agy` por pipe/redirect (`agy -p "..." | cat` volta vazio - bug TTY #76).
> Sempre passe por este modulo (ConPTY). Para probe manual:
> `cd /tmp && python scripts/agy.py single -p "PROMPT" --model "Gemini 3.5 Flash (Low)"`
> Se precisar inspecionar o raw, filtre o ruido com
> `2>&1 | grep -viE 'No hook installed|Fetching available|^\s*$'`.

---

## 1. Chamada unica (PT)

```python
from agy import call_agy

resp = call_agy(
    "Quanto e 17*23? Responda apenas o numero.",
    model="Gemini 3.5 Flash (Low)",   # rapido/barato, bom p/ probes (PROBE_MODEL)
    timeout=90,                        # tier Flash = FLASH_TIMEOUT
)
print(resp)   # -> 391
```

Sem `--model` -> usa o default de `~/.gemini/antigravity-cli/settings.json`
(hoje `Claude Opus 4.6 (Thinking)`). Modelo invalido levanta `AgyError` ANTES de gastar inferencia.

Diagnostico estruturado (nunca levanta por EMPTY/TIMEOUT/AUTH):

```python
from agy import call_agy_result

r = call_agy_result("Oi", model="Gemini 3.5 Flash (Low)", timeout=90)
print(r.status, r.ok, r.elapsed_s, r.raw_len)   # status: OK | EMPTY | TIMEOUT | AUTH_ERROR
if r.ok:
    print(r.text)
```

CLI (imprime texto cru):

```bash
python scripts/agy.py single -p "Quanto e 17*23?" --model "Gemini 3.5 Flash (Low)"
```

---

## 2. Paralelo (fan-out) - jobs dict E tupla

```python
from agy import call_agy_parallel

jobs = [
    {"prompt": "Liste 3 riscos de lancar um produto sem validacao.",
     "model": "Gemini 3.1 Pro (Low)"},
    {"prompt": "Liste 3 riscos de lancar um produto sem validacao.",
     "model": "Gemini 3.5 Flash (Medium)", "timeout": 90},     # timeout por job
    ("Liste 3 riscos de lancar um produto sem validacao.",
     "Claude Sonnet 4.6 (Thinking)", 300),                     # tupla (prompt, model, timeout)
]

results = call_agy_parallel(jobs, max_concurrency=4, retries=2, timeout=180)
for r in results:                  # results[i] (CallResult) alinha com jobs[i], NA ORDEM
    tag = r.model or "default"
    if r.ok:
        print(f"== {tag} (tentativas={r.attempts}) ==\n{r.text}\n")
    else:
        print(f"== {tag} {r.status}: {r.error} ==\n")
```

CLI (com `jobs.json`):

```json
[
  {"prompt": "Liste 3 riscos de X.", "model": "Gemini 3.1 Pro (Low)"},
  {"prompt": "Liste 3 riscos de X.", "model": "Gemini 3.5 Flash (Medium)"}
]
```

```bash
python scripts/agy.py parallel --jobs jobs.json --max-concurrency 4 --retries 2
# imprime CallResult[] em JSON; exit 0 se todos ok, 1 se houve falha parcial
```

> Concorrencia desta maquina: default 4, max 6. ~4x speedup em N=5, zero 429. O cap e tunado pela
> RAM/CPU local (cada `agy.exe` e pesado), nao pelo backend. Para council de 5, use
> `max_concurrency=5`. Lotes >20 jobs: processe em ondas de tamanho=cap.

---

## 3. Encadeamento / pipeline (builder Callable E template {prev})

`builder` (canonico) recebe TODAS as saidas anteriores como `list[CallResult]` e e robusto a
`{`/`}` literais. `prompt`/template (`{prev}`/`{step_i}`/`{all}`) e acucar para casos triviais.

```python
from agy import pipeline, template

steps = [
    # step 0: gera ideia
    {"model": "Gemini 3.1 Pro (Low)",
     "prompt": "Gere UMA ideia de feature para um app de cotacao de seguros. Conciso."},

    # step 1: builder Callable - ve toda a historia, escolhe o que usar
    {"model": "Claude Opus 4.6 (Thinking)",
     "builder": lambda prev: (
         "Critique a ideia abaixo e aponte 3 riscos concretos:\n\n" + prev[-1].text)},

    # step 2: template - {step_0} = ideia, {step_1} = critica
    {"model": "Claude Sonnet 4.6 (Thinking)",
     "prompt": "Dada a IDEIA:\n{step_0}\n\ne a CRITICA:\n{step_1}\n\nEscreva 1 paragrafo de veredito."},
]

res = pipeline(steps, timeout=180, fail_fast=True)
# res -> {"ok": bool, "results": list[CallResult], "final": str, "failed_step": int|None}
if res["ok"]:
    print("VEREDITO:\n", res["final"])
else:
    print("Falhou no step", res["failed_step"], res["results"][res["failed_step"]].status)
```

Com `initial` (vira `CallResult` sintetico no indice 0, acessivel via `{prev}`/`prev[0]`):

```python
res = pipeline(
    [{"model": "Gemini 3.5 Flash (Low)", "prompt": "Resuma em 1 frase:\n\n{prev}"}],
    initial="Texto longo que ja tenho em maos...",
)
```

O helper `template("...{prev}...")` devolve um builder pronto, equivalente ao campo `"prompt"`.

CLI (so `prompt`/template; `builder` Callable e exclusivo do import Python):

```bash
python scripts/agy.py pipeline --steps steps.json
```

`steps.json`:

```json
[
  {"model": "Gemini 3.1 Pro (Low)", "prompt": "Gere uma ideia de feature concisa."},
  {"model": "Claude Opus 4.6 (Thinking)", "prompt": "Critique e aponte 3 riscos:\n\n{prev}"}
]
```

---

## 4. Fan-out -> sintese estilo council

`fanout_synthesize` empacota o caso comum: N modelos respondem o mesmo prompt em paralelo, depois 1
sintese. O builder padrao anonimiza/embaralha as respostas como `Response A..N` (`seed` reproduzivel).

```python
from agy import fanout_synthesize

verdict = fanout_synthesize(
    "Devo lancar um curso de $297 ou um workshop de $97 primeiro? Justifique.",
    models=["Gemini 3.1 Pro (High)", "Gemini 3.5 Flash (High)", "Claude Opus 4.6 (Thinking)"],
    synth_model="Claude Opus 4.6 (Thinking)",   # chairman forte (parametrizavel)
    max_concurrency=5, retries=2, timeout=180, seed=42,
)
print(verdict.text)   # verdict e um CallResult
```

Council COMPLETO (5 personas + peer-review) e composto a mao com as 3 primitivas - isto vive no
`llm-council`, nao nesta skill:

```python
from agy import call_agy_parallel, call_agy

QUESTION = "Devo lancar um curso de $297 ou um workshop de $97 primeiro?"
models = ["Gemini 3.1 Pro (High)", "Gemini 3.5 Flash (High)", "Claude Opus 4.6 (Thinking)"]

# FAN-OUT
advisors = call_agy_parallel([{"prompt": QUESTION, "model": m} for m in models],
                             max_concurrency=5)
answers = [a.text for a in advisors if a.ok]

# PEER-REVIEW ANONIMO
anon = "\n\n".join(f"Resposta {chr(65+i)}:\n{a}" for i, a in enumerate(answers))
review_prompt = (QUESTION + "\n\nRespostas anonimas:\n\n" + anon +
                 "\n\n1. Qual e a mais forte? 2. Maior ponto cego? 3. O que TODAS perderam?")
reviews = [r.text for r in call_agy_parallel(
    [{"prompt": review_prompt, "model": m} for m in models], max_concurrency=5) if r.ok]

# CHAIRMAN
chairman = call_agy(
    "Sintetize um veredito final.\n\nPERGUNTA:\n" + QUESTION +
    "\n\nRESPOSTAS:\n" + anon + "\n\nREVISOES:\n" + "\n\n".join(reviews),
    model="Claude Opus 4.6 (Thinking)", timeout=300)
print(chairman)
```

---

## 5. Importar de outra skill

```python
import sys
sys.path.insert(0, r"<CAMINHO>\call-agy\scripts")   # ajuste <CAMINHO> para onde voce clonou
from agy import call_agy, call_agy_parallel, pipeline, fanout_synthesize
```

A pasta `scripts/` e autocontida; basta `pywinpty` instalado no mesmo Python.

---

## 6. Retrocompatibilidade (interface antiga)

O codigo antigo do usuario continua funcionando via `scripts/call_agy.py` (ordem antiga dos kwargs
`prompt, timeout, model`, permissivo / sem validacao de modelo):

```python
from call_agy import call_agy      # assinatura antiga: (prompt, timeout, model)
print(call_agy("Oi", 90, "Gemini 3.5 Flash (Low)"))
```

```bash
python scripts/call_agy.py "Oi" --model "Gemini 3.5 Flash (Low)" --timeout 90
```
