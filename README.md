# call-agy

Skill para o Claude Code chamar o **agy** (Antigravity CLI do Google) a partir de codigo, de forma
confiavel: chamada unica, lotes em paralelo, encadeamento, fan-out com sintese, handoff estruturado
e geracao de imagem.

Para que serve na pratica: delegar ao agy o que ele faz bem e o Claude nao deveria gastar cota
fazendo — segunda opiniao com outra familia de modelo, council, lote grande de extracao, gerar
imagem, ver video — com os cuidados medidos (ele coleta bem e conclui mal; `--sandbox` nao confina;
cota esgotada nao se resolve tentando de novo).

## Instalar

1. Instale o agy: <https://antigravity.google/cli> e faca login.
2. Clone este repositorio em `~/.claude/skills/call-agy`.
3. Nada a instalar em Python: so a biblioteca padrao. (`pywinpty` e opcional, apenas para agy muito
   antigo.)

## Usar

```python
import os, sys
sys.path.insert(0, os.path.expanduser("~/.claude/skills/call-agy/scripts"))
from agy import call_agy, call_agy_parallel, generate_image

print(call_agy("Quanto e 17*23? So o numero.", model="Gemini 3.8 Flash (Low)"))   # sem model: Gemini 3.8 Flash (High)
r = generate_image("A red ceramic mug on a wooden desk, soft daylight.", "caneca.png")
```

```bash
python scripts/agy.py single -p "Oi" --model "Gemini 3.8 Flash (Low)"
python scripts/agy.py image -p "A red ceramic mug" --dest caneca.png
```

## Modelos (catalogo verificado em 18/11/2026)

Sem assinatura do Gemini (cancelada em 18/11/2026) o agy ficou com 6 modelos: Gemini 3.8 Flash (High, Medium, Low), Claude
Sonnet 4.6 e Opus 4.6 (saem em breve) e GPT-OSS. Padrao (`DEFAULT_MODEL`) e imagem (`IMAGE_MODEL`): **Gemini 3.8 Flash (High)**,
que gera imagem de 1024x1024 em cerca de 228 s no plano gratuito. Os Gemini 4 (Hydrogen, Carbon, Argon), que valiam para
busca e texto, sairam do catalogo. Use o agy para **imagem**; texto, busca e lote vao para outros executores.
Nao ha ferramenta nativa de video nem de audio. A tabela completa esta no [SKILL.md](SKILL.md).

A documentacao completa (regras de modelo, cota, flags, erros comuns) esta no
[SKILL.md](SKILL.md); exemplos em [examples.md](examples.md); detalhes em [references/](references).

## Testes

```bash
SKIP_LIVE=1 python tests/test_agy.py   # puros, offline
python tests/test_agy.py               # + vivos (chamam o agy e gastam cota)
```

## Licenca

MIT — ver [LICENSE](LICENSE).
