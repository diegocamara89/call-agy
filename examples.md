# call-agy - exemplos

Todos assumem que o `agy` esta no PATH (ou em `~/AppData/Local/agy/bin/agy.exe`). **O caminho
padrao nao tem dependencia externa** — so a stdlib do Python. `pywinpty` so e preciso para o
transporte legado `transport="pty"`.

Para importar como modulo de OUTRA skill/script, aponte para a pasta `scripts/`:

```python
import sys
sys.path.insert(0, r"<CAMINHO>\call-agy\scripts")   # ajuste <CAMINHO> para onde voce clonou
from agy import (call_agy, call_agy_result, call_agy_parallel, pipeline, fanout_synthesize,
                 call_agy_handoff, extract_json, template, CallResult, AgyError,
                 KNOWN_MODELS, HANDOFF_SCHEMA)
```

> O `agy -p ... --output-format json` funciona por pipe/redirect/subprocess. **A unica excecao e o
> subcomando `agy models`**, que ainda trava com 0 bytes fora de TTY — nunca o chame de um script;
> use `known_models(refresh=True)`.

---

## 1. Chamada unica

```python
from agy import call_agy

resp = call_agy(
    "Quanto e 17*23? Responda apenas o numero.",
    model="Gemini 3.7 Flash (Low)",   # rapido/barato, bom p/ probes (PROBE_MODEL)
    timeout=90,                        # tier Flash = FLASH_TIMEOUT
)
print(resp)   # -> 391
```

Sem `--model` -> usa o default de `~/.gemini/antigravity-cli/settings.json`
(hoje `Gemini 3.7 Flash (High)`).

Diagnostico estruturado (nunca levanta por EMPTY/TIMEOUT/AUTH/INVALID_MODEL):

```python
from agy import call_agy_result

r = call_agy_result("Analise X", model="Gemini 3.1 Pro (High)", timeout=300, effort="high")
print(r.status, r.ok, r.elapsed_s)              # OK | EMPTY | TIMEOUT | AUTH_ERROR | INVALID_MODEL
print(r.usage["total_tokens"], r.num_turns)     # custo real da chamada
print(r.conversation_id)                        # reaproveitavel (ver exemplo 5)
if r.ok:
    print(r.text)
```

CLI:

```bash
python scripts/agy.py single -p "Quanto e 17*23?" --model "Gemini 3.7 Flash (Low)"
python scripts/agy.py single -p "..." --model "Gemini 3.1 Pro (High)" --effort high --json
```

---

## 2. Paralelo (fan-out) - jobs dict E tupla

```python
from agy import call_agy_parallel

jobs = [
    {"prompt": "Liste 3 riscos de lancar um produto sem validacao.",
     "model": "Gemini 3.1 Pro (Low)"},
    {"prompt": "Liste 3 riscos de lancar um produto sem validacao.",
     "model": "Gemini 3.7 Flash (Medium)", "timeout": 90, "effort": "high"},
    ("Liste 3 riscos de lancar um produto sem validacao.",
     "Claude Sonnet 4.6 (Thinking)", 300),                     # tupla (prompt, model, timeout)
]

results = call_agy_parallel(jobs, max_concurrency=4, retries=2, timeout=180)
for r in results:                  # results[i] (CallResult) alinha com jobs[i], NA ORDEM
    tag = r.model or "default"
    if r.ok:
        print(f"== {tag} (tentativas={r.attempts}, {r.usage.get('total_tokens')} tok) ==\n{r.text}\n")
    else:
        print(f"== {tag} {r.status}: {r.error} ==\n")
```

CLI (com `jobs.json`):

```json
[
  {"prompt": "Liste 3 riscos de X.", "model": "Gemini 3.1 Pro (Low)"},
  {"prompt": "Liste 3 riscos de X.", "model": "Gemini 3.7 Flash (Medium)"}
]
```

```bash
python scripts/agy.py parallel --jobs jobs.json --max-concurrency 4 --retries 2
# imprime CallResult[] em JSON; exit 0 se todos ok, 1 se houve falha parcial
```

> Concorrencia desta maquina: default 4, max 6. ~4x speedup em N=5, zero 429. O cap e tunado pela
> RAM/CPU local, nao pelo backend. Para council de 5, use `max_concurrency=5`. Lotes >20 jobs:
> processe em ondas de tamanho=cap.

---

## 3. Encadeamento / pipeline (builder Callable E template {prev})

`builder` (canonico) recebe TODAS as saidas anteriores como `list[CallResult]` e e robusto a
`{`/`}` literais. `prompt`/template (`{prev}`/`{step_i}`/`{all}`) e acucar para casos triviais.

```python
from agy import pipeline

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
    [{"model": "Gemini 3.7 Flash (Low)", "prompt": "Resuma em 1 frase:\n\n{prev}"}],
    initial="Texto longo que ja tenho em maos...",
)
```

CLI (so `prompt`/template; `builder` Callable e exclusivo do import Python):

```bash
python scripts/agy.py pipeline --steps steps.json
```

---

## 4. Saida estruturada (json_schema) e handoff

Com `json_schema`, o agy preenche `structured_output` no envelope e voce recebe um **dict ja
parseado** em `CallResult.structured` — sem regex, sem `grep` guloso, sem ```` ```json ````.

```python
from agy import call_agy_result

r = call_agy_result(
    "Avalie o risco de fazer deploy numa sexta as 18h.",
    model="Gemini 3.7 Flash (Low)",
    json_schema={
        "type": "object",
        "properties": {"nivel": {"type": "string", "enum": ["baixo", "medio", "alto"]},
                       "motivos": {"type": "array", "items": {"type": "string"}},
                       "acao": {"type": "string"}},
        "required": ["nivel", "motivos", "acao"],
    },
)
print(r.structured["nivel"], r.structured["acao"])
```

Handoff no contrato da skill `orchestrate` (o agy executa E devolve o JSON do contrato):

```python
from agy import call_agy_handoff

h = call_agy_handoff(
    "Refatore o parser de datas em src/dates.py e rode os testes.",
    model="Claude Sonnet 4.6 (Thinking)", timeout=300,
    skip_permissions=True,      # nao pausa pedindo aprovacao de tool call
    mode="accept-edits",
    cwd="D:/meu-projeto",
)
if h.ok:
    d = h.structured
    print(d["status"], d["next_action"], d["changed_files"])
    if d["next_action"] == "NEEDS_VALIDATION":
        ...   # escala para o validador
```

CLI (stdout comeca com `{` e termina com `}` -> `jq` direto):

```bash
python scripts/agy.py handoff -p "Rode os testes e reporte" --model "Gemini 3.7 Flash (High)" | jq .next_action
```

Quando nao der para impor schema (resposta e prosa + JSON), use o extrator de 3 niveis:

```python
from agy import extract_json

extract_json('Aqui esta:\n```json\n{"a": 1}\n```')      # -> {"a": 1}
extract_json('primeiro {"a": 1} depois {"b": 2}')        # -> {"a": 1}  (nao e guloso)
extract_json('{"code": "if (x) { y }"}')                 # -> chaves em string nao confundem
```

---

## 5. Continuar uma conversa (conversation_id)

Cada chamada devolve `conversation_id`. Passe de volta em `conversation=` e o agy continua **aquela
sessao** — o modelo lembra dos turnos anteriores sem voce reenviar o texto no prompt.

```python
from agy import call_agy_result

r1 = call_agy_result("Leia o arquivo X e me diga a arquitetura em 3 bullets.",
                     model="Gemini 3.1 Pro (High)", timeout=300, cwd="D:/meu-projeto")
r2 = call_agy_result("Agora aponte o maior risco do que voce acabou de descrever.",
                     model="Gemini 3.1 Pro (High)", timeout=300,
                     conversation=r1.conversation_id)
```

No pipeline, `chain_conversation=True` faz isso automaticamente entre os steps:

```python
res = pipeline(steps, chain_conversation=True)   # uma sessao so, nao N independentes
```

---

## 6. Fan-out -> sintese estilo council

```python
from agy import fanout_synthesize

verdict = fanout_synthesize(
    "Devo lancar um curso de $297 ou um workshop de $97 primeiro? Justifique.",
    models=["Gemini 3.1 Pro (High)", "Gemini 3.7 Flash (High)", "Claude Opus 4.6 (Thinking)"],
    synth_model="Claude Opus 4.6 (Thinking)",   # chairman forte (parametrizavel)
    max_concurrency=5, retries=2, timeout=180, seed=42,
)
print(verdict.text)   # verdict e um CallResult
```

Council COMPLETO (5 personas + peer-review) e composto a mao com as primitivas — isto vive no
`llm-council`, nao nesta skill:

```python
from agy import call_agy_parallel, call_agy

QUESTION = "Devo lancar um curso de $297 ou um workshop de $97 primeiro?"
models = ["Gemini 3.1 Pro (High)", "Gemini 3.7 Flash (High)", "Claude Opus 4.6 (Thinking)"]

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

## 7. Catalogo de modelos

```python
from agy import known_models

known_models()                # tupla estatica KNOWN_MODELS (instantaneo)
known_models(refresh=True)    # pergunta ao agy: ~3.4s, ZERO tokens
```

```bash
python scripts/agy.py models --refresh
```

Modelo invalido nao passa mais em silencio:

```python
r = call_agy_result("oi", model="Gemini 9.9 Turbo", validate_model=False)
r.status        # -> "INVALID_MODEL"
r.error         # -> "invalid model selection ... Available models: <lista dos 14>"
r.usage         # -> total_tokens = 0 (nao gastou inferencia)
```

---

## 8. Rodar os testes

```bash
SKIP_LIVE=1 python tests/test_agy.py    # so os puros (offline, instantaneo)
python tests/test_agy.py                # + os vivos (chamam o agy, alguns minutos)
```

---

## 9. Retrocompatibilidade (interface antiga)

O codigo antigo continua funcionando via `scripts/call_agy.py` (ordem antiga dos kwargs
`prompt, timeout, model`, permissivo / sem validacao de modelo):

```python
from call_agy import call_agy      # assinatura antiga: (prompt, timeout, model)
print(call_agy("Oi", 90, "Gemini 3.7 Flash (Low)"))
```

```bash
python scripts/call_agy.py "Oi" --model "Gemini 3.7 Flash (Low)" --timeout 90
```

Ele perde os campos novos do envelope (`conversation_id`, `usage`, `structured`) — codigo novo deve
importar de `agy.py`.
