"""
call_agy.py - shim de RETROCOMPATIBILIDADE.

Preserva a interface ORIGINAL do usuario (ordem antiga dos kwargs e modo permissivo):
    from call_agy import call_agy
    call_agy(prompt, timeout=180, model=None) -> str          # ORDEM antiga: (prompt, timeout, model)

    CLI antiga:
        python call_agy.py "seu prompt aqui"
        python call_agy.py "seu prompt aqui" --timeout 180
        python call_agy.py "seu prompt aqui" --model "Claude Opus 4.6 (Thinking)"

A logica real (ConPTY, strip CR-aware, paralelo, pipeline, fanout) vive em `agy.py`, no mesmo
diretorio. Este arquivo so reexporta para nao duplicar codigo. Codigo NOVO deve importar de `agy.py`.

DIFERENCA DE ORDEM DE KWARGS (importante):
    shim (aqui):   call_agy(prompt, timeout=180, model=None)     # historico do usuario
    core (agy.py): call_agy(prompt, model=None, timeout=180, ...) # contrato novo
Chamadas POSICIONAIS antigas tipo `call_agy(prompt, 120)` (timeout em 2o lugar) continuam validas.
O shim usa validate_model=False (permissivo) para nao quebrar chamadas legadas com ID nao-validado.
"""
from __future__ import annotations

import argparse
import os
import sys

# Garante que agy.py (mesmo diretorio) seja importavel mesmo se o cwd for outro.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agy import call_agy as _core  # noqa: E402
from agy import (  # noqa: E402,F401  (reexport para compat de quem importava daqui)
    AgyError,
    KNOWN_MODELS,
    _find_agy,
    _strip_ansi,
    call_agy_parallel,
    pipeline,
)

DEFAULT_TIMEOUT = 180


def call_agy(prompt: str, timeout: int = 180, model: str | None = None) -> str:
    """
    Interface ORIGINAL (prompt, timeout, model). Delega para agy.call_agy com modo permissivo
    (validate_model=False), preservando o comportamento legado de nao validar o ID do modelo.
    """
    return _core(prompt, model=model, timeout=timeout, validate_model=False)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Chama agy (Antigravity CLI) via ConPTY e imprime a resposta (interface antiga)."
    )
    parser.add_argument("prompt", help="Prompt a enviar ao agy")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="Timeout em segundos")
    parser.add_argument(
        "--model", default=None,
        help='Substitui o modelo do settings.json. Use o ID exato (ex: "Claude Opus 4.6 '
             '(Thinking)"). Omita para usar o modelo configurado em '
             "~/.gemini/antigravity-cli/settings.json."
    )
    args = parser.parse_args()
    print(call_agy(args.prompt, timeout=args.timeout, model=args.model))


if __name__ == "__main__":
    main()
