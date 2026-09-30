# Imagem com o agy

`generate_image` (em `scripts/agy.py`) e o caminho recomendado. Este arquivo e o detalhe.

## Gabarito de prompt

Padrao que funciona em uso real (pipeline de imagens para rede social):

```
Generate ONE photorealistic image.
SCENE: <onde, hora do dia, clima>
SUBJECT: <quem/o que, pose, roupa ou material>
CAMERA: <lente/distancia, ex. 35mm, meio corpo, ao nivel dos olhos>
LIGHT: <fonte e direcao da luz>
FRAMING: <proporcao desejada, ex. portrait 4:5>
NEGATIVE: no text, no watermark, no extra fingers, no logos
```

- `generate_image` ja acrescenta "Use the generate_image tool... Save the generated image as a
  PNG." — nao repita.
- Quer legenda junto? Peca uma linha `CAPTION: ...` no fim da resposta e leia de `r.call.text`.
- Varie o prompt a cada execucao de um lote (cenario, luz, enquadramento): prompt identico tende a
  imagens quase iguais.
- Palavras de conteudo sensivel (violencia, nudez, pessoas reais famosas, marcas) disparam recusa:
  o resultado e `NO_IMAGE` com a explicacao em `r.call.text`.

## Imagem de referencia (entrada)

- Passe em `refs=[...]`: o helper poe o caminho absoluto no texto do prompt e a pasta dela em
  `--add-dir`. O agy le o arquivo do disco.
- Uma folha unica com varios angulos da mesma pessoa/objeto funciona melhor que varias imagens
  soltas.
- A referencia **influencia** a identidade, **nao a garante**: confira o resultado a olho.
- A copia que o agy faz da referencia fica em `brain/<id>/.user_uploaded/` — por isso a varredura
  ignora essa pasta.

## Onde a imagem cai

Tudo em `~/.gemini/antigravity-cli/brain/<conversation_id>/`:

| Arquivo | Quem escreve | Nome |
|---|---|---|
| saida crua | a tool `generate_image` | `<nome>_<epoch_ms>.jpg` |
| artefato pedido | o agy, porque o prompt mandou salvar em PNG | `<nome>.png` |

Subpastas que **nao** sao saida: `.tempmediaStorage/` (copia de trabalho), `.user_uploaded/`
(entradas), `.system_generated/`, `scratch/`, e arquivos `reference_image*`.

Por que amarrar pelo `conversation_id` e nao "o mais novo do brain": com duas geracoes
simultaneas, a que termina depois leva a imagem da outra e as duas reportam sucesso. Sem id no
envelope, o helper usa o snapshot (so a pasta de conversa que surgiu durante a chamada); se
surgirem varias, nao chuta.

## Tamanho

Nao ha flag de tamanho nem de proporcao. Medido em 2026-09-30 (agy 1.2.14): pedido "portrait 4:5"
saiu 896x1200 (3:4). `generate_image` devolve `width`/`height`: se a proporcao importar, recorte
localmente.

## Falhas

| `status` | O que foi | O que fazer |
|---|---|---|
| `QUOTA_EXHAUSTED` | cota da conta esgotada | parar o lote e avisar o usuario |
| `NO_IMAGE` (chamada ok) | recusa ou o modelo nao chamou a tool | ler `r.call.text`, reescrever o prompt |
| `RAW_ONLY` | gerou, mas nao salvou o PNG pedido | `r.raw_path` tem a crua; decida voce se serve |
| `TIMEOUT` | passou de 300 s | a pasta ja foi varrida; se nao achou, repita uma vez |
| 429 / limite por minuto | excesso de chamadas | espere e repita; nao e cota |

Custo medido: ~45 s e ~100 mil tokens por imagem (65 mil de cache). Paralelo nao foi medido para
imagem: prefira sequencial ate medir.
