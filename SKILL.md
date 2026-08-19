---
name: call-agy
description: Use quando precisar chamar o agy (Antigravity CLI do Google) a partir de codigo/automacao em vez do terminal interativo - capturar a saida programaticamente, escolher modelo via --model, forcar saida estruturada com json_schema, continuar uma conversa por conversation_id, ou orquestrar varias chamadas (paralelo, pipeline, fan-out/council). Tambem quando o agy voltar vazio, travar sem imprimir nada, ou parecer ignorar o --model. TRIGGERS (PT) - chamar agy, rodar agy, usar o agy, agy via script, agy nao retorna nada, agy retorna vazio, agy travou, agy em paralelo, pipeline de agy, encadear agy, council com agy, saida estruturada do agy, handoff do agy, qual o modelo mais novo do agy, atualizar modelos do agy, checar agy models. TRIGGERS (EN) - call agy, run agy, agy returns empty, agy hangs, agy in parallel, agy pipeline, agy structured output, agy json schema.
---

# call-agy - chamando o Antigravity CLI (agy) de forma confiavel

`agy` e o CLI agentico do Google (antigravity.google/cli), no estilo do Claude Code. Esta skill e o
**motor de transporte reusavel** para chama-lo de dentro de codigo: chamada unica, paralelo,
encadeamento, fan-out -> sintese e handoff estruturado.

Nao use para rodar `agy` interativamente (chame `agy` direto no terminal). Esta skill e o caminho
programatico.

---

## O transporte: `--output-format json` (LEIA ANTES DE TUDO)

**Chame o agy sempre com `-p` + `--output-format json`, argv como LISTA, `shell=False`.**

```bash
agy -p "PROMPT" --model "Gemini 3.7 Flash (Low)" --output-format json
```

Isso funciona por **pipe, redirect e subprocess comum**. O envelope que volta no stdout:

```json
{"conversation_id":"74bf...","status":"SUCCESS","response":"4\n","duration_seconds":2.7,
 "num_turns":1,"usage":{"input_tokens":39623,"output_tokens":33,"total_tokens":39656}}
```

Com `--json-schema`, ganha ainda `"structured_output": {...}` **ja parseado**.

Tres coisas que o envelope resolve de graca e o modo texto nao dava:

| Problema | Como o envelope resolve |
|---|---|
| Distinguir resposta vazia de falha | `status` = `SUCCESS` \| `ERROR` + campo `error` |
| Extrair JSON de prosa (`grep` guloso, ```` ```json ````) | `structured_output` vem parseado |
| Saber o custo da chamada | `usage.total_tokens` por chamada |
| Continuar a mesma conversa | `conversation_id` -> devolva em `--conversation` |

### O que MUDOU (bug TTY #76) - verificado em 2026-08-15, agy 1.1.13

O print mode **nao sofre mais** o bug de 0 bytes fora de TTY. Medido nesta maquina:

| Comando | Resultado |
|---|---|
| `agy -p "..." > out.txt` | 2 bytes, correto |
| `agy -p "..." --output-format json > out.json` | 252 bytes, JSON valido |
| `agy -p "..." \| cat` | 454 bytes, resposta completa |
| **`agy models`** (sem TTY) | **TRAVA, 0 bytes, rc=124 em 45s** |

> **O bug PERSISTE no subcomando `agy models`.** Nunca o chame de dentro de um script.
> Para listar modelos use `known_models(refresh=True)`, que arranca a lista oficial do proprio
> agy via probe de modelo invalido: ~3.4s e **zero tokens** (ver "Manutencao do catalogo").

O transporte ConPTY/`pywinpty` continua disponivel em `transport="pty"` para quem roda um agy
antigo. Com `transport="auto"` (default), se o JSON voltar 0 bytes o modulo loga um aviso e tenta
o ConPTY sozinho — mas a correcao de verdade e `agy update`.

### Prompt com `{}`, `|`, `%`: seguro aqui

A skill `orchestrate` documenta uma "regra de ouro": `-p "texto"` corrompe prompts no Windows
porque o `cmd.exe` interpreta `{`, `}`, `|` e `%`. **Isso vale para chamada via shell, nao para
esta skill.** Passamos argv como lista com `shell=False`, entao o `cmd.exe` nunca ve o prompt.
Verificado: `{"a":1} | 50% & <x> \`y\` $HOME` chegou intacto ao modelo. O limite pratico tambem
sobe de 8191 (cmd.exe) para 32767 chars (`CreateProcess`).

---

## Catalogo de modelos (IDs literais para `--model`)

**Catalogo verificado em 2026-08-15** contra o proprio agy (14 IDs). Prefira sempre a **versao mais
alta** de cada familia — hoje a linha Flash atual e a **3.7**; 3.6 e 3.5 seguem so como legado.

| ID literal (`--model "..."`) | Familia | Velocidade | Uso sugerido |
|---|---|---|---|
| `Gemini 3.7 Flash (High)` | Gemini | rapido | **DEFAULT** do settings.json; analise leve |
| `Gemini 3.7 Flash (Medium)` | Gemini | rapido | triagem |
| `Gemini 3.7 Flash (Low)` | Gemini | rapido (~6s medido) | probes, triagem, fan-out leve (**PROBE_MODEL**) |
| `Gemini 3.6 Flash (High/Medium/Low)` | Gemini | rapido | legado / diversidade no council |
| `Gemini 3.5 Flash (High/Medium/Low)` | Gemini | medio | legado |
| `Gemini 3.1 Pro (High)` | Gemini | lento (Thinking) | analise arquitetural, fan-out serio |
| `Gemini 3.1 Pro (Low)` | Gemini | medio | analise pontual |
| `Claude Sonnet 4.6 (Thinking)` | Claude | lento | raciocinio, review |
| `Claude Opus 4.6 (Thinking)` | Claude | lento | chairman/sintese (**SYNTH_MODEL**) |
| `GPT-OSS 120B (Medium)` | GPT-OSS | medio | diversidade extra no council |

- **Default** (sem `--model`): vem de `~/.gemini/antigravity-cli/settings.json` -> hoje
  `Gemini 3.7 Flash (High)`. Esse default e do **usuario** e pode mudar sem a skill saber: se o
  caso precisa de um modelo especifico, **passe `--model` explicitamente**.
- **Chairman/sintese** usa `SYNTH_MODEL = Claude Opus 4.6 (Thinking)`, desacoplado do default de
  proposito (herdar um Flash rebaixaria a sintese).
- Para **council**, rotacione familias diferentes (Pro / Flash / Claude / GPT-OSS). Nao monte um
  council com 3.7 + 3.6 + 3.5 Flash: sao versoes do mesmo modelo, nao opinioes independentes.

### Modelo invalido: o agy AGORA erra alto

Antes o agy fazia fallback silencioso para o default e voce rodava um council inteiro achando que
tinha 5 modelos. **Isso acabou.** Com `--output-format json`, um `--model` invalido devolve `rc=1`,
`status:"ERROR"` e um `error` que **lista os 14 IDs validos** — em ~4s e sem gastar token.

A validacao pre-call (`validate_model=True`, default) sobreviveu, mas com papel menor: evitar o
round-trip de 4s por job quando ha um typo num fanout. Um `KNOWN_MODELS` velho hoje causa, no pior
caso, um falso negativo local — nao mais um council corrompido em silencio. Passe
`validate_model=False` para deixar a validacao inteiramente com o agy.

---

## Timeout e tier de modelo

Use o tier certo (cold-start e alguns segundos mesmo no Flash; nunca use `<60s`):

- `FLASH_TIMEOUT = 90` -> Flash Low/Medium.
- `THINK_TIMEOUT = 300` -> Pro High, Sonnet/Opus Thinking, qualquer High/Thinking.
- `DEFAULT_TIMEOUT = 180` -> compromisso de `call_agy`/`parallel`/`pipeline`. Sobrescreva por job
  com `job["timeout"]` / `step["timeout"]`.

O modulo repassa o mesmo valor para o `--print-timeout` interno do agy, entao os dois relogios
ficam alinhados. Em timeout, **mata a arvore de processos** (`taskkill /F /T` no Windows, padrao
herdado do `orchestrate`): `proc.kill()` sozinho deixaria os servidores MCP netos vivos segurando
os pipes, e um timeout de 120s viraria 279s+.

---

## Como chamar

Tudo vive em `scripts/agy.py` (fonte da verdade). Importe como modulo ou use a CLI.

### Modo 1 - chamada unica

```python
import sys
sys.path.insert(0, r"<CAMINHO>\call-agy\scripts")
from agy import call_agy, call_agy_result

texto = call_agy("Quanto e 17*23? Responda so o numero.",
                 model="Gemini 3.7 Flash (Low)", timeout=90)

# Superficie estruturada: nunca levanta por EMPTY/TIMEOUT/AUTH/INVALID_MODEL.
r = call_agy_result("Analise X", model="Gemini 3.1 Pro (High)", timeout=300, effort="high")
r.ok, r.status, r.text, r.conversation_id, r.usage["total_tokens"]
```

`call_agy` levanta `AgyError` em modelo invalido e em timeout (mesmo com texto parcial — devolver
resposta truncada como se fosse completa corrompe o pipeline downstream).

CLI:

```bash
python scripts/agy.py single -p "Quanto e 17*23?" --model "Gemini 3.7 Flash (Low)" --json
```

### Modo 2 - paralelo (fan-out)

```python
from agy import call_agy_parallel

jobs = [
    {"prompt": "Liste 3 riscos de X.", "model": "Gemini 3.1 Pro (Low)"},
    {"prompt": "Liste 3 riscos de X.", "model": "Gemini 3.7 Flash (Medium)", "effort": "high"},
    ("Liste 3 riscos de X.", "Claude Sonnet 4.6 (Thinking)"),   # tupla tambem vale
]
results = call_agy_parallel(jobs, max_concurrency=4, retries=2, timeout=180)
# results[i] (CallResult) corresponde a jobs[i], NA ORDEM. Falha parcial nunca aborta o lote.
```

**Concorrencia:** default `max_concurrency=4`, teto recomendado **6**. O cap e tunado pela
**maquina** (RAM/CPU), nao pelo backend — o agy fala com Antigravity/Google direto, entao o quirk
de 429 do `gemini`/OpenRouter nao se aplica. Probes reais: N=3 -> 3/3 ok (~2.75x speedup, zero
429); N=5 -> 5/5 ok (~4x, zero 429). Para council de 5, suba para 5. Para lotes >20, processe em
**ondas** de tamanho=cap.

Politica de retry (por job, independente):
- **retry** quando `status in {EMPTY, TIMEOUT, AUTH_ERROR}` ou o erro/texto casa
  `429|rate.?limit|quota|too many|overloaded|timeout`. Backoff = `retry_backoff*attempt + jitter`.
  (`AUTH_ERROR` e retryavel de proposito: cota transitoria se mascara de erro de auth.)
- **fatal, sem retry:** `INVALID_MODEL` — o agy reporta explicitamente, retentar so queima tempo.

### Modo 3 - encadeamento / pipeline

Cada step e um dict com kwargs de `call_agy_result` + **exatamente um** de:
- `"builder"`: `Callable[[list[CallResult]], str]` — recebe **todas** as saidas anteriores e
  devolve o prompt. Contrato canonico: robusto a `{`/`}` literais e permite anonimizar/concatenar.
- `"prompt"`: str com placeholders `{prev}` / `{step_0}` / `{all}` (acucar; token ausente fica
  literal, nunca levanta).

```python
from agy import pipeline

steps = [
    {"model": "Gemini 3.1 Pro (Low)",
     "prompt": "Gere UMA ideia de feature para um app de seguros. Conciso."},
    {"model": "Claude Opus 4.6 (Thinking)",
     "builder": lambda prev: f"Critique e aponte 3 riscos:\n\n{prev[-1].text}"},
]
res = pipeline(steps, timeout=180, fail_fast=True)
print(res["final"])   # {"ok", "results", "final", "failed_step"}
```

**`chain_conversation=True`** propaga o `conversation_id` entre os steps: em vez de N sessoes
independentes, o agy mantem **uma** e o modelo lembra dos turnos anteriores sem voce reenviar o
texto. So faz sentido quando todos os steps usam o mesmo modelo.

### Modo 4 - saida estruturada e handoff

```python
from agy import call_agy_result, call_agy_handoff, HANDOFF_SCHEMA

# Schema arbitrario -> r.structured vem como dict parseado.
r = call_agy_result("Avalie o deploy de sexta.", model="Gemini 3.7 Flash (Low)",
                    json_schema={"type": "object",
                                 "properties": {"risco": {"type": "string"},
                                                "acao": {"type": "string"}},
                                 "required": ["risco", "acao"]})
r.structured["risco"]

# Contrato de handoff da skill `orchestrate`, preenchido pelo proprio agy.
h = call_agy_handoff("Refatore o parser de datas em src/dates.py e rode os testes.",
                     model="Claude Sonnet 4.6 (Thinking)", timeout=300,
                     skip_permissions=True, mode="accept-edits", cwd="D:/proj")
h.structured["next_action"]   # DONE | NEEDS_VALIDATION | NEEDS_RETRY | ESCALATE
```

`HANDOFF_SCHEMA` e o contrato do `orchestrate` (`status`, `task_summary`, `changed_files`,
`tests_run`, `risks`, `analyst_summary`, `next_action`). Usar `--json-schema` em vez de pedir JSON
no prompt **elimina de vez** a classe de bug que o `orchestrate` documenta: preambulo antes do
JSON, resposta embrulhada em ```` ```json ````, e o `grep -oP '{.*}'` guloso.

Quando nao der para impor schema, use `extract_json(texto)` — parser balanceado de 3 niveis
(texto puro -> bloco markdown -> varredura com contador de profundidade que respeita strings e
escapes).

### Helper - fan-out -> sintese (base do council)

```python
from agy import fanout_synthesize

verdict = fanout_synthesize(
    "Devo lancar um curso de $297 ou um workshop de $97 primeiro?",
    models=["Gemini 3.1 Pro (High)", "Gemini 3.7 Flash (High)", "Claude Opus 4.6 (Thinking)"],
    synth_model="Claude Opus 4.6 (Thinking)", max_concurrency=5, seed=42,
)
print(verdict.text)
```

Roda os N models em paralelo sobre o mesmo prompt, depois 1 chamada de sintese. O builder padrao
**anonimiza/embaralha** as respostas ok como `Response A..N` (`seed` p/ reproducao). Esta skill
**nao** implementa as 5 personas/peer-review do `llm-council` — so o motor fan-out -> reduce.

### CLI - subcomandos e exit codes

```bash
python scripts/agy.py single   -p "..." --model "ID" [--effort high] [--conversation ID] [--json]
python scripts/agy.py parallel --jobs jobs.json [--max-concurrency 4] [--retries 2]
python scripts/agy.py pipeline --steps steps.json [--chain-conversation] [--no-fail-fast]
python scripts/agy.py fanout   -p "..." --models "A;B;C" [--synth-model "ID"]
python scripts/agy.py handoff  -p "..." [--model "ID"]     # stdout = so o JSON do contrato
python scripts/agy.py models   [--refresh]
```

Exit: **0** tudo ok; **1** falha (parcial em parallel/pipeline/fanout); **2** em
`AgyError`/`FileNotFound`/`ImportError`. O `handoff` imprime stdout comecando com `{` e terminando
com `}` — parse direto via `jq` ou Python, como manda o contrato do `orchestrate`.

---

## O que cada arquivo faz

- `SKILL.md` - este arquivo.
- `scripts/agy.py` - **fonte da verdade**: transporte (`_run_agy`, `_kill_tree`, `_parse_envelope`),
  `call_agy_result`/`call_agy`, `call_agy_parallel`, `pipeline`, `fanout_synthesize`,
  `call_agy_handoff`, `extract_json`, `known_models`, `CallResult`, `AgyError`, e a CLI.
- `scripts/call_agy.py` - shim de retrocompatibilidade (`call_agy(prompt, timeout, model)`).
- `tests/test_agy.py` - testes puros (offline, `SKIP_LIVE=1`) + vivos (chamam o agy de verdade).
- `examples.md` - exemplos copiaveis.
- `requirements.txt` - vazio no caminho padrao; `pywinpty` so para `transport="pty"`.

---

## Erros comuns

| Sintoma | Causa provavel | Correcao |
|---|---|---|
| Script trava e nao imprime nada | chamou `agy models` em subprocess | use `known_models(refresh=True)` |
| `status: "EMPTY"` com `raw_len=0` | agy antigo com o bug #76 no print mode | `agy update` |
| Todos os advisors do council responderam igual | agy antigo fazendo fallback silencioso | atualize; hoje isso vira `INVALID_MODEL` |
| `structured` e `None` numa chamada com schema | modelo devolveu so prosa | `call_agy_handoff` ja cai no `extract_json`; para schema proprio, chame-o voce |
| Timeout de 120s levou 280s | kill sem `/T` deixou netos MCP vivos | ja tratado por `_kill_tree` — se reaparecer, verifique se nao esta chamando o agy por fora do modulo |
| Prompt chegou truncado/corrompido | montou a chamada como string de shell | passe argv como lista, `shell=False` (o modulo ja faz) |

---

## Delegacao sob contrato (Claude Code + agy trabalhando junto)

Secao empirica: tudo aqui foi **medido** em trabalho real (investigacao e correcao de um coletor
Telegram->WhatsApp em producao, 2026-08-15), nao inferido.

### O que ele acerta e o que ele erra

| Tarefa | Resultado |
|---|---|
| Implementar `slugify` sob 9 testes prontos | **acertou** — codigo idiomatico, 9/9, sem trapaca |
| Implementar `EchoGuard` sob 16 testes prontos | **acertou** — suite subiu 45 -> 60 |
| Investigar causa de bug (leu o codigo) | **errou** — citou linhas inexistentes, concluiu race condition que nao existia |
| Analisar logs e estimar latencia | **errou** — leu ausencia de log como ausencia de execucao; disse "pior caso 7,4h" onde era 20s |

O padrao e consistente: **coleta bem e conclui mal.** Os numeros brutos vinham certos (57 PUSH /
27 POLL conferiu com medicao independente); o que quebrou foi a **interpretacao**, sempre com
aparencia de rigor — "26.807,26 segundos" e preciso e falso. Nas duas vezes a causa foi a mesma:
**nao validar uma premissa** antes de construir em cima dela.

### Divisao de papeis que funciona

| Delegue ao agy | Nunca delegue |
|---|---|
| Implementar ate os testes passarem | **Escrever os testes** |
| Extrair, contar, medir, tabular | **Concluir a partir dos dados** |
| Boilerplate, conversao, scaffolding | Invariantes de seguranca e privacidade |
| Rascunho para voce criticar | Qualquer coisa que toque producao |

Regra que sustenta o resto: **ele nunca e autor e juiz da mesma coisa.** Se ele escrever os testes
e a implementacao, um teste frouxo aprova uma implementacao frouxa e os dois parecem corretos.

Peca **evidencia verificavel** (`arquivo:linha`), nunca so a conclusao — e **confira as citacoes**.
Foi assim que os dois erros apareceram: as linhas citadas apontavam para outro codigo.

### `status: TIMEOUT` NAO significa trabalho nao feito

Medido: uma chamada voltou `status=TIMEOUT`, `elapsed=291s`, **texto vazio** — e mesmo assim o
arquivo tinha sido criado e a suite inteira passava. O agy trabalha no disco; a resposta e so o
relatorio dele.

> **Nunca decida pelo `status` da chamada. Decida pelo efeito colateral verificado.**
> Rode os testes, olhe o `git status`, confira o arquivo. Se tivesse confiado no TIMEOUT, teria
> descartado trabalho pronto e refeito do zero.

### Gate mecanico antes de qualquer revisao humana

Rode isto **antes** de ler o diff. Tudo verificavel por maquina, custo ~zero:

1. arquivos de teste com **hash inalterado** — ele nao mexeu no juiz;
2. `git status --porcelain` so contem os arquivos da lista permitida;
3. `HEAD` inalterado — nao commitou, resetou nem trocou de branch;
4. **suite completa** verde, nao so os testes do alvo;
5. lint/format limpos.

Falhou qualquer um -> devolve sem gastar atencao. Aborte tambem na **terceira rodada sem verde**:
assuma a tarefa e escreva voce.

O gate pega trapaca e quebra. Ele **nao** pega o que o teste nao sabia perguntar — num caso real, o
codigo entregue passou nos 16 testes e ainda assim fazia *check-then-act* sem lock num ponto onde
duas threads concorrem. Isso so morre na revisao humana. **Gate e revisao sao camadas distintas.**

### Isolamento: detectar, nao confinar

`--sandbox` **nao** impede escrita fora do `cwd` — medido: com e sem a flag, ele criou arquivo em
caminho absoluto fora do diretorio de trabalho. A flag restringe comandos de terminal, nao a
ferramenta de escrita. Logo `cwd` e `--add-dir` sao contexto, **nao fronteira de seguranca**.

- **Investigar** -> de uma **copia descartavel** e confira por hash que nada foi tocado. E a unica
  leitura garantida. (Nas duas investigacoes ele respeitou; a garantia veio do hash, nao da promessa.)
- **Implementar** -> rode no repo real e use **git como rede**: o gate cobre o repositorio inteiro,
  nao so os arquivos que ele deveria tocar.
- Fora do repo nao ha defesa real senao container/VM. Limite conhecido, nao coberto.
- Ele sobe MCP servers proprios (`@upstash/context7-mcp`) e tem ferramental externo. Conteudo
  malicioso dentro de um arquivo lido pode instrui-lo: a contencao e *o que ele alcanca*.

### Escolha de modelo por tipo de tarefa

| Tarefa | Modelo | Por que |
|---|---|---|
| Implementar sob testes | `Gemini 3.7 Flash (High)` | rapido; o teste e o juiz, nao precisa de thinking |
| Extrair/medir dados de log | `Gemini 3.1 Pro (High)` | volume grande, mas **confira as conclusoes** |
| Tarefa longa de codigo | Flash, e **fatie** | Pro High estourou 300s numa tarefa media |

Suba o `timeout` junto com o tier (o modulo repassa ao `--print-timeout`, entao os relogios ficam
alinhados). Uma implementacao nao-trivial com modelo *thinking* passa facil de 300s.

Prefira `call_agy_handoff` quando o agy for executar tarefa de codigo: o contrato ja devolve
`changed_files`, `tests_run` e `next_action` estruturados, o que alimenta o gate direto.

---

## Posicionamento (vs llm-council / orchestrate)

- Esta skill e o **motor de transporte reusavel**: "como chamar o agy de forma confiavel". Ela
  **NAO** decide *quando* counciliar nem implementa personas.
- **`llm-council`**: define a *metodologia* (5 advisors, peer-review anonimo, chairman). Quando o
  backend for o agy, compoe as primitivas daqui: `call_agy_parallel` (fan-out + reviews) e
  `call_agy`/`fanout_synthesize` (chairman).
- **`orchestrate`**: roteia entre IAs (Claude planeja -> executor executa -> Claude valida). Se um
  worker for o agy, chame `call_agy_handoff` — ele devolve o handoff JSON do contrato ja parseado,
  sem extracao na marra.

Regra pratica: "como faco o agy me devolver texto / rodar varios agy" -> esta skill. "Qual decisao
tomar com varias opinioes" / "quem executa o que" -> `llm-council` / `orchestrate`, que por baixo
chamam esta.

---

## Manutencao do catalogo (revisao a cada 15 dias)

| Campo | Valor |
|---|---|
| **Ultima verificacao** | **2026-08-15** |
| **Proxima revisao (a partir de)** | **2026-08-30** |
| **Versao do agy verificada** | **1.1.13** |
| **Linha Flash atual** | `Gemini 3.7 Flash (Low/Medium/High)` |
| **Linha Pro atual** | `Gemini 3.1 Pro (Low/High)` |
| **Claude atual** | `Claude Opus 4.6 (Thinking)`, `Claude Sonnet 4.6 (Thinking)` |
| **Outros** | `GPT-OSS 120B (Medium)` |
| **Total de IDs** | 14 |

**Por que existe esta secao:** o Antigravity troca de versao sem avisar (a linha Flash foi 3.5 ->
3.6 -> 3.7). O catalogo aqui e um espelho manual e fica velho sozinho.

**Regra para o agente:** se hoje for **>= "Proxima revisao"** e a tarefa envolver escolher modelo do
agy, revalide antes de rodar o trabalho:

```bash
python scripts/agy.py models --refresh
```

Compare com a tabela do catalogo e faca **as duas coisas** (senao divergem):

1. `scripts/agy.py` -> `KNOWN_MODELS` (adicione os novos, **remova os que sumiram**),
   `PROBE_MODEL` (Flash Low mais novo), `DEFAULT_MODEL` (releia o `settings.json` do usuario),
   `SYNTH_MODEL` (so mude se sair um raciocinador melhor) e `CATALOG_CHECKED`.
2. `SKILL.md` -> tabela do catalogo, a linha "Catalogo verificado em ...", e esta tabela de status.

**Sem historico.** Nao acumule changelog, nao escreva "antes era 3.5, agora e 3.7". **Sobrescreva**
a data e os valores. **Se nada mudou:** atualize so as datas (aqui e em `CATALOG_CHECKED`) e siga.

> **Limite honesto:** a regra e *best-effort*, sem cron nem watcher — so dispara quando um agente
> le esta skill depois da data. A garantia real hoje e o **proprio agy**, que rejeita modelo
> invalido com `rc=1` e a lista dos IDs validos. Em duvida, rode `models --refresh`: custa 3.4s e
> zero tokens.
