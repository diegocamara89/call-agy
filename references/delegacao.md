# Delegacao sob contrato (Claude + agy trabalhando junto)

Tudo aqui foi **medido** em trabalho real (investigacao e correcao de um servico em producao,
agosto de 2026), nao inferido.

## O que ele acerta e o que ele erra

| Tarefa | Resultado |
|---|---|
| Implementar uma funcao pequena sob 9 testes prontos | acertou: codigo idiomatico, 9/9, sem trapaca |
| Implementar uma classe sob 16 testes prontos | acertou: a suite subiu de 45 para 60 |
| Investigar a causa de um bug lendo o codigo | errou: citou linhas inexistentes e concluiu uma condicao de corrida que nao existia |
| Analisar logs e estimar latencia | errou: leu ausencia de log como ausencia de execucao; estimou horas onde eram segundos |

O padrao e consistente: **coleta bem e conclui mal.** As contagens brutas batiam com medicao
independente; o que quebrou foi a interpretacao, sempre com aparencia de rigor (numero preciso e
falso). Nas duas vezes a causa foi **nao validar uma premissa** antes de construir em cima dela.

## Divisao de papeis

| Delegue ao agy | Nunca delegue |
|---|---|
| Implementar ate os testes passarem | Escrever os testes |
| Extrair, contar, medir, tabular | Concluir a partir dos dados |
| Boilerplate, conversao, scaffolding | Invariantes de seguranca e privacidade |
| Rascunho para voce criticar | Qualquer coisa que toque producao |

Ele **nunca e autor e juiz da mesma coisa**: teste frouxo aprova implementacao frouxa e os dois
parecem corretos. Peca evidencia verificavel (`arquivo:linha`) e **confira as citacoes** — foi assim
que os dois erros apareceram.

## `TIMEOUT` nao significa trabalho nao feito

Uma chamada voltou `status=TIMEOUT`, 291 s, texto vazio — e o arquivo estava criado e a suite
passava. O agy trabalha no disco; a resposta e so o relatorio. **Decida pelo efeito verificado**
(testes, `git status`, arquivo), nunca pelo `status`.

## Gate mecanico antes de qualquer revisao humana

1. arquivos de teste com **hash inalterado**;
2. `git status --porcelain` so com os arquivos permitidos;
3. `HEAD` inalterado (nao commitou, resetou nem trocou de branch);
4. **suite completa** verde, nao so os testes do alvo;
5. lint/format limpos.

Falhou um -> devolve sem gastar atencao. Terceira rodada sem verde: assuma e escreva voce.
O gate pega trapaca e quebra, **nao** o que o teste nao sabia perguntar (caso real: 16/16 verdes e
ainda um *check-then-act* sem lock entre duas threads). Gate e revisao humana sao camadas distintas.

## Isolamento: detectar, nao confinar

`--sandbox` **nao** impede escrita fora do `cwd` (medido: com e sem a flag, criou arquivo em caminho
absoluto fora do diretorio). `cwd` e `--add-dir` sao contexto, nao fronteira.

- **Investigar** -> numa copia descartavel, conferida por hash depois.
- **Implementar** -> no repo real, com git como rede: o gate cobre o repositorio inteiro.
- Fora do repo nao ha defesa real senao container/VM.
- Ele sobe servidores MCP proprios e tem ferramental externo: conteudo malicioso num arquivo lido
  pode instrui-lo. A contencao e *o que ele alcanca* — e por isso dado sigiloso nunca vai para ele.

## Modelo por tipo de tarefa

| Tarefa | Modelo | Por que |
|---|---|---|
| Implementar sob testes | `Gemini 3.8 Flash (High)` | rapido; o teste e o juiz |
| Extrair/medir dado de log | `Gemini 3.1 Pro (High)` | volume grande — confira as conclusoes |
| Tarefa longa de codigo | Flash, e **fatie** | Pro High estourou 300 s numa tarefa media |

Suba o `timeout` junto com o tier. Prefira `call_agy_handoff` para tarefa de codigo: o contrato
devolve `changed_files`, `tests_run` e `next_action`, que alimentam o gate direto.
