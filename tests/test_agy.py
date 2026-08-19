"""
Testes da skill call-agy.

Divididos em dois grupos:
  - PUROS: rodam offline, sem agy instalado (extract_json, _normalize_job, template, _build_argv).
  - VIVOS: chamam o agy de verdade (custam segundos + inferencia). Pule com SKIP_LIVE=1.

Rodar:
    python tests/test_agy.py            # tudo
    SKIP_LIVE=1 python tests/test_agy.py  # so os puros
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from agy import (  # noqa: E402
    HANDOFF_SCHEMA,
    AgyError,
    CallResult,
    _build_argv,
    _normalize_job,
    call_agy_handoff,
    call_agy_parallel,
    call_agy_result,
    extract_json,
    known_models,
    pipeline,
    template,
)

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        FAILURES.append(name)


# --------------------------------------------------------------------------- Puros


def test_extract_json() -> None:
    print("\n[extract_json]")
    check("texto puro", extract_json('{"a": 1}') == {"a": 1})
    check("bloco markdown",
          extract_json('bla bla\n```json\n{"a": 2}\n```\nfim') == {"a": 2})
    check("bloco sem tag json",
          extract_json('x\n```\n{"a": 3}\n```') == {"a": 3})
    check("prosa + json no fim",
          extract_json('Aqui esta a analise.\n{"status":"OK","n":4}') == {"status": "OK", "n": 4})
    # O caso que o `grep -oP '{.*}'` guloso erra: dois objetos no texto.
    check("nao e guloso com 2 objetos",
          extract_json('primeiro {"a": 1} depois {"b": 2}') == {"a": 1})
    check("chaves dentro de string nao confundem",
          extract_json('{"code": "if (x) { y }"}') == {"code": "if (x) { y }"})
    check("escape de aspas", extract_json(r'{"q": "diz \"oi\""}') == {"q": 'diz "oi"'})
    check("array", extract_json('lixo [1, 2, 3] lixo') == [1, 2, 3])
    check("nada -> None", extract_json("sem json aqui") is None)
    check("vazio -> None", extract_json("") is None)


def test_normalize_job() -> None:
    print("\n[_normalize_job]")
    check("tupla prompt+model",
          _normalize_job(("oi", "M")) == {"prompt": "oi", "model": "M"})
    check("tupla com timeout",
          _normalize_job(("oi", "M", 90)) == {"prompt": "oi", "model": "M", "timeout": 90})
    check("tupla so prompt", _normalize_job(("oi",)) == {"prompt": "oi"})
    check("dict passa kwargs novos",
          _normalize_job({"prompt": "oi", "effort": "high", "conversation": "c1"})
          == {"prompt": "oi", "effort": "high", "conversation": "c1"})
    check("dict descarta None",
          _normalize_job({"prompt": "oi", "model": None}) == {"prompt": "oi"})
    try:
        _normalize_job({"model": "M"})
        check("dict sem prompt levanta", False)
    except KeyError:
        check("dict sem prompt levanta", True)


def test_build_argv() -> None:
    print("\n[_build_argv]")
    argv = _build_argv("agy.exe", "meu prompt", model="M", effort="high", conversation=None,
                       continue_last=False, schema_path=None, skip_permissions=False,
                       sandbox=False, mode=None, add_dirs=None, agent=None, print_timeout=180)
    check("prompt e um elemento unico do argv", "meu prompt" in argv)
    check("output-format json sempre presente",
          "--output-format" in argv and argv[argv.index("--output-format") + 1] == "json")
    check("effort repassado", "--effort" in argv and argv[argv.index("--effort") + 1] == "high")
    check("print-timeout alinhado ao nosso", "180s" in argv)

    argv2 = _build_argv("agy.exe", "p", model=None, effort=None, conversation="C1",
                        continue_last=True, schema_path="s.json", skip_permissions=True,
                        sandbox=True, mode="plan", add_dirs=["/a", "/b"], agent="ag",
                        print_timeout=90)
    check("conversation vence continue_last",
          "--conversation" in argv2 and "--continue" not in argv2)
    check("add-dir repetido", argv2.count("--add-dir") == 2)
    check("mode/sandbox/skip-permissions",
          "--mode" in argv2 and "--sandbox" in argv2 and "--dangerously-skip-permissions" in argv2)

    # Caracteres que o cmd.exe corromperia — aqui vao intactos porque argv e lista (shell=False).
    nasty = '{"a":1} | 50% & <x> `y` $HOME'
    argv3 = _build_argv("agy.exe", nasty, model=None, effort=None, conversation=None,
                        continue_last=False, schema_path=None, skip_permissions=False,
                        sandbox=False, mode=None, add_dirs=None, agent=None, print_timeout=180)
    check("prompt hostil preservado no argv", nasty in argv3)


def test_template() -> None:
    print("\n[template]")
    prev = [CallResult(True, "AAA", None, "OK", None, 0.0),
            CallResult(True, "BBB", None, "OK", None, 0.0)]
    check("{prev} = ultimo", template("<{prev}>")(prev) == "<BBB>")
    check("{step_0}", template("{step_0}")(prev) == "AAA")
    check("{all}", template("{all}")(prev) == "AAA\n\nBBB")
    check("chave literal nao quebra", template('peca {"k":1} e {prev}')(prev)
          == 'peca {"k":1} e BBB')
    check("token inexistente vira literal", template("{nao_existe}")(prev) == "{nao_existe}")
    check("step fora do range vira vazio", template("[{step_9}]")(prev) == "[]")


def test_pipeline_validation() -> None:
    print("\n[pipeline - validacao]")
    for bad, label in (([{"model": "M"}], "sem builder nem prompt"),
                       ([{"prompt": "p", "builder": lambda _: "x"}], "com os dois")):
        try:
            pipeline(bad)
            check(f"step {label} levanta", False)
        except AgyError:
            check(f"step {label} levanta", True)


def test_transport_validation() -> None:
    print("\n[transport - validacao]")
    try:
        call_agy_result("x", transport="conpty")
        check("transport invalido levanta", False)
    except AgyError:
        check("transport invalido levanta", True)
    try:
        call_agy_result("x", model="Modelo Que Nao Existe 42")
        check("modelo invalido levanta pre-call", False)
    except AgyError:
        check("modelo invalido levanta pre-call", True)


# --------------------------------------------------------------------------- Vivos


def test_live_single() -> None:
    print("\n[LIVE single]")
    r = call_agy_result("Responda apenas com o numero: 17*23",
                        model="Gemini 3.7 Flash (Low)", timeout=90)
    check("ok", r.ok, f"status={r.status} err={r.error}")
    check("resposta correta", "391" in r.text, f"text={r.text!r}")
    check("conversation_id presente", bool(r.conversation_id))
    check("usage preenchido", r.usage.get("total_tokens", 0) > 0)
    check("transport json", r.transport == "json")


def test_live_invalid_model() -> None:
    print("\n[LIVE modelo invalido - o agy erra explicitamente, sem fallback silencioso]")
    r = call_agy_result("oi", model="Gemini 9.9 Turbo", validate_model=False, timeout=60)
    check("status INVALID_MODEL", r.status == "INVALID_MODEL", f"status={r.status}")
    check("erro lista os modelos validos", "Available models:" in (r.error or ""))
    check("nao gastou tokens", r.usage.get("total_tokens", 0) == 0)


def test_live_conversation() -> None:
    print("\n[LIVE continuidade de conversa]")
    r1 = call_agy_result("Meu numero secreto e 4271. Responda so: ok",
                         model="Gemini 3.7 Flash (Low)", timeout=90)
    check("primeira chamada ok", r1.ok, f"{r1.status} {r1.error}")
    r2 = call_agy_result("Qual era meu numero secreto? Responda so o numero.",
                         model="Gemini 3.7 Flash (Low)", timeout=90,
                         conversation=r1.conversation_id)
    check("lembrou do contexto", "4271" in r2.text, f"text={r2.text!r}")
    check("mesmo conversation_id", r2.conversation_id == r1.conversation_id)


def test_live_handoff() -> None:
    print("\n[LIVE handoff estruturado]")
    r = call_agy_handoff(
        "Analise (sem editar nada) o risco de fazer deploy numa sexta as 18h.",
        model="Gemini 3.7 Flash (Low)", timeout=120,
    )
    check("ok", r.ok, f"{r.status} {r.error}")
    check("structured e dict", isinstance(r.structured, dict), f"{type(r.structured)}")
    if isinstance(r.structured, dict):
        req = HANDOFF_SCHEMA["required"]
        check(f"campos obrigatorios {req}", all(k in r.structured for k in req),
              f"keys={list(r.structured)}")
        check("next_action valido",
              r.structured.get("next_action") in
              HANDOFF_SCHEMA["properties"]["next_action"]["enum"],
              f"next_action={r.structured.get('next_action')!r}")


def test_live_parallel() -> None:
    print("\n[LIVE parallel - ordem e isolamento de falha]")
    jobs = [
        {"prompt": "Responda so: alpha", "model": "Gemini 3.7 Flash (Low)"},
        {"prompt": "Responda so: beta", "model": "Modelo Fantasma"},   # falha isolada
        {"prompt": "Responda so: gamma", "model": "Gemini 3.6 Flash (Low)"},
    ]
    res = call_agy_parallel(jobs, max_concurrency=3, retries=0, timeout=90,
                            validate_model=False)
    check("3 resultados", len(res) == 3)
    check("ordem preservada [0]=alpha", "alpha" in res[0].text.lower(), f"{res[0].text!r}")
    check("falha isolada no [1]", res[1].status == "INVALID_MODEL", f"{res[1].status}")
    check("ordem preservada [2]=gamma", "gamma" in res[2].text.lower(), f"{res[2].text!r}")
    check("lote nao abortou", res[0].ok and res[2].ok)


def test_live_models_refresh() -> None:
    print("\n[LIVE known_models(refresh=True)]")
    models = known_models(refresh=True)
    check("14+ modelos", len(models) >= 14, f"n={len(models)}")
    check("contem o Flash atual", "Gemini 3.7 Flash (Low)" in models)
    check("contem o synth", "Claude Opus 4.6 (Thinking)" in models)


def test_live_pipeline_chain() -> None:
    print("\n[LIVE pipeline com chain_conversation]")
    res = pipeline(
        [
            {"prompt": "Escolha um numero primo entre 50 e 60 e responda so o numero.",
             "model": "Gemini 3.7 Flash (Low)"},
            {"builder": lambda prev: "Qual numero voce acabou de escolher? Responda so o numero.",
             "model": "Gemini 3.7 Flash (Low)"},
        ],
        timeout=90, chain_conversation=True,
    )
    check("pipeline ok", res["ok"], f"failed_step={res['failed_step']}")
    if res["ok"]:
        escolhido = res["results"][0].text.strip()
        check("step 2 lembra do step 1 (sessao unica)",
              escolhido in res["results"][1].text,
              f"step0={escolhido!r} step1={res['results'][1].text!r}")


def main() -> int:
    print("=" * 70)
    print("TESTES PUROS (offline)")
    print("=" * 70)
    test_extract_json()
    test_normalize_job()
    test_build_argv()
    test_template()
    test_pipeline_validation()
    test_transport_validation()

    if os.environ.get("SKIP_LIVE"):
        print("\n(LIVE pulado: SKIP_LIVE=1)")
    else:
        print("\n" + "=" * 70)
        print("TESTES VIVOS (chamam o agy de verdade)")
        print("=" * 70)
        test_live_single()
        test_live_invalid_model()
        test_live_conversation()
        test_live_handoff()
        test_live_parallel()
        test_live_models_refresh()
        test_live_pipeline_chain()

    print("\n" + "=" * 70)
    if FAILURES:
        print(f"FALHAS ({len(FAILURES)}): {', '.join(FAILURES)}")
        return 1
    print("TODOS OS TESTES PASSARAM")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
