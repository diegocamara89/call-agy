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

import agy as _agy  # noqa: E402
from agy import (  # noqa: E402
    CATALOG_CHECKED,
    DEFAULT_MODEL,
    HANDOFF_SCHEMA,
    PROBE_MODEL,
    _find_generated_image,
    _image_size,
    generate_image,
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


def _fake_run(stdout: str, stderr: str = "", rc: int = 0):
    """Troca o transporte por um falso: devolve a saida dada, sem chamar o agy."""
    chamadas: list = []

    def run(argv, timeout, cwd, env):
        chamadas.append(argv)
        return rc, stdout, stderr, False
    return run, chamadas


def test_default_model_e_cota() -> None:
    print("\n[modelo padrao e cota esgotada - transporte falso]")
    orig_run, orig_find = _agy._run_agy, _agy._find_agy
    _agy._find_agy = lambda: "agy.exe"
    try:
        _agy._run_agy, ch = _fake_run('{"status":"SUCCESS","response":"oi","conversation_id":"c1"}')
        r = call_agy_result("p")
        check("sem model vai DEFAULT_MODEL no argv (nunca o settings.json)",
              "--model" in ch[0] and ch[0][ch[0].index("--model") + 1] == DEFAULT_MODEL, str(ch[0]))
        check("CallResult.model reporta o modelo usado", r.model == DEFAULT_MODEL)

        cota = ('{"status":"ERROR","error":"Individual quota reached for Gemini models. '
                'Resets in 4h12m."}')
        _agy._run_agy, ch = _fake_run(cota, rc=1)
        r = call_agy_result("p", model=PROBE_MODEL)
        check("cota vira QUOTA_EXHAUSTED", r.status == "QUOTA_EXHAUSTED", r.status)
        try:
            _agy.call_agy("p", model=PROBE_MODEL)
            check("call_agy levanta em cota", False)
        except AgyError as exc:
            check("call_agy levanta em cota pedindo para avisar", "avise" in str(exc))

        _agy._run_agy, ch = _fake_run(cota, rc=1)
        res = call_agy_parallel([("a", PROBE_MODEL)] * 5, max_concurrency=1, retries=3,
                                retry_backoff=0)
        check("lote: todos QUOTA_EXHAUSTED", all(x.status == "QUOTA_EXHAUSTED" for x in res),
              str([x.status for x in res]))
        check("lote: cota nao e retentada e para o resto (1 chamada so)", len(ch) == 1,
              f"chamadas={len(ch)}")

        _agy._run_agy, ch = _fake_run('{"status":"ERROR","error":"429 Too Many Requests"}', rc=1)
        call_agy_parallel([("a", PROBE_MODEL)], retries=2, retry_backoff=0)
        check("429 continua sendo retentado", len(ch) == 3, f"chamadas={len(ch)}")
    finally:
        _agy._run_agy, _agy._find_agy = orig_run, orig_find


def test_imagem_puro() -> None:
    import struct
    import tempfile
    print("\n[imagem - varredura do brain e tamanho, sem agy]")
    png_hdr = (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
               + struct.pack(">II", 800, 1000) + b"\x08\x02\x00\x00\x00")
    jpg = (b"\xff\xd8\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
           + b"\xff\xc0" + struct.pack(">HBHH", 17, 8, 600, 400) + b"\x00" * 10)
    with tempfile.TemporaryDirectory() as tmp:
        brain = Path(tmp) / "brain"
        conv = brain / "conv1"
        (conv / ".user_uploaded").mkdir(parents=True)
        (conv / ".tempmediaStorage").mkdir()
        (conv / ".user_uploaded" / "entrada.png").write_bytes(png_hdr)   # entrada, nao saida
        (conv / ".tempmediaStorage" / "tmp.png").write_bytes(png_hdr)
        (conv / "reference_image_1.png").write_bytes(png_hdr)
        (conv / "gato_1789376606563.jpg").write_bytes(jpg)
        png, raw = _find_generated_image(conv)
        check("ignora .user_uploaded/.tempmediaStorage/reference_image", png is None)
        check("acha a saida crua", raw is not None and raw.suffix == ".jpg")
        (conv / "gato.png").write_bytes(png_hdr)
        png, raw = _find_generated_image(conv)
        check("acha o PNG nomeado", png is not None and png.name == "gato.png")
        check("tamanho PNG pelo cabecalho", _image_size(png) == (800, 1000), str(_image_size(png)))
        check("tamanho JPEG pelo SOF", _image_size(raw) == (400, 600), str(_image_size(raw)))

        orig_call = _agy.call_agy_result
        try:
            _agy.call_agy_result = lambda *a, **k: CallResult(
                True, "feito", PROBE_MODEL, "OK", None, 1.0, conversation_id="conv1")
            destino = Path(tmp) / "out" / "final.png"
            r = generate_image("um gato", destino, brain=brain)
            check("generate_image copia o PNG da conversa certa", r.ok and destino.exists(), str(r))
            check("generate_image mede o tamanho", (r.width, r.height) == (800, 1000))
            (conv / "gato.png").unlink()
            r = generate_image("um gato", destino, brain=brain)
            check("so JPG cru -> RAW_ONLY (erro alto)", r.status == "RAW_ONLY" and not r.ok, r.status)
            _agy.call_agy_result = lambda *a, **k: CallResult(
                False, "", PROBE_MODEL, "QUOTA_EXHAUSTED", "cota", 1.0)
            r = generate_image("um gato", destino, brain=brain)
            check("cota -> QUOTA_EXHAUSTED", r.status == "QUOTA_EXHAUSTED")
        finally:
            _agy.call_agy_result = orig_call


def test_cota_refinada() -> None:
    import threading
    print("\n[cota: janela curta, balde por familia, effort, pipeline, fanout, JPEG truncado]")
    orig_run, orig_find = _agy._run_agy, _agy._find_agy
    _agy._find_agy = lambda: "agy.exe"
    try:
        curta = '{"status":"ERROR","error":"429 RESOURCE_EXHAUSTED: Individual quota reached. Resets in 1s."}'
        _agy._run_agy, ch = _fake_run(curta, rc=1)
        r = call_agy_result("p", model=PROBE_MODEL)
        check("janela curta NAO e QUOTA_EXHAUSTED", r.status != "QUOTA_EXHAUSTED", r.status)
        call_agy_parallel([("a", PROBE_MODEL)], retries=1, retry_backoff=0)
        check("janela curta e retentada", len(ch) == 3, f"chamadas={len(ch)}")

        longa = '{"status":"ERROR","error":"Individual quota reached. Resets in 3h5m."}'

        def run_misto(argv, timeout, cwd, env):
            modelo = argv[argv.index("--model") + 1]
            if modelo.startswith("Gemini"):
                return 1, longa, "", False
            return 0, '{"status":"SUCCESS","response":"ok claude"}', "", False
        _agy._run_agy = run_misto
        res = call_agy_parallel([("a", PROBE_MODEL), ("b", "Claude Sonnet 4.6 (Thinking)"),
                                 ("c", PROBE_MODEL)], max_concurrency=1, retry_backoff=0)
        check("balde Gemini esgotado nao derruba o Claude",
              [x.status for x in res] == ["QUOTA_EXHAUSTED", "OK", "QUOTA_EXHAUSTED"], str([x.status for x in res]))

        _agy._run_agy, ch = _fake_run(longa, rc=1)
        res = _agy.pipeline([{"prompt": "a", "model": PROBE_MODEL}, {"prompt": "b", "model": PROBE_MODEL}],
                            fail_fast=False)
        check("pipeline para na cota mesmo com fail_fast=False", len(ch) == 1 and res["failed_step"] == 0,
              f"chamadas={len(ch)}")
        _agy._run_agy, ch = _fake_run(longa, rc=1)
        v = _agy.fanout_synthesize("q", [PROBE_MODEL, PROBE_MODEL], max_concurrency=1, retries=0)
        check("fanout sem advisor por cota nao chama o chairman", v.status == "QUOTA_EXHAUSTED" and len(ch) == 1,
              f"{v.status} chamadas={len(ch)}")
    finally:
        _agy._run_agy, _agy._find_agy = orig_run, orig_find
    try:
        call_agy_result("p", model="Gemini 3.8 Flash (High)", effort="max")
        check("effort com modelo de tier levanta antes de chamar", False)
    except AgyError:
        check("effort com modelo de tier levanta antes de chamar", True)

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        casos = {"fim": b"\xff\xd8\xff\xd9", "app1": b"\xff\xd8\xff\xe1", "sos": b"\xff\xd8\xff\xda\x00\x08abc"}
        for nome, dados in casos.items():
            f = Path(tmp) / f"{nome}.jpg"
            f.write_bytes(dados)
            saida = []
            t = threading.Thread(target=lambda: saida.append(_image_size(f)), daemon=True)
            t.start(); t.join(3)
            check(f"JPEG truncado ({nome}) nao trava", not t.is_alive() and saida == [None], str(saida))


def test_exemplos_compilam() -> None:
    import re
    print("\n[examples.md e SKILL.md: todo bloco python compila]")
    raiz = Path(__file__).resolve().parent.parent
    for doc in ("examples.md", "SKILL.md", "README.md"):
        # cerca de fechamento so no inicio da linha (``` dentro de string nao fecha o bloco)
        blocos = re.findall(r"^```python\n(.*?)^```", (raiz / doc).read_text(encoding="utf-8"), re.S | re.M)
        for i, b in enumerate(blocos):
            try:
                compile(b, f"{doc}[{i}]", "exec")
                ok = True
            except SyntaxError as exc:
                ok = False
                print("      ", exc)
            check(f"{doc} bloco {i} compila", ok)


def test_catalogo_sincronizado() -> None:
    print("\n[catalogo: data do agy.py = data do SKILL.md]")
    skill = (Path(__file__).resolve().parent.parent / "SKILL.md").read_text(encoding="utf-8")
    check("SKILL.md cita o CATALOG_CHECKED do agy.py", CATALOG_CHECKED in skill, CATALOG_CHECKED)


# --------------------------------------------------------------------------- Vivos


def test_live_single() -> None:
    print("\n[LIVE single]")
    r = call_agy_result("Responda apenas com o numero: 17*23",
                        model=PROBE_MODEL, timeout=90)
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
                         model=PROBE_MODEL, timeout=90)
    check("primeira chamada ok", r1.ok, f"{r1.status} {r1.error}")
    r2 = call_agy_result("Qual era meu numero secreto? Responda so o numero.",
                         model=PROBE_MODEL, timeout=90,
                         conversation=r1.conversation_id)
    check("lembrou do contexto", "4271" in r2.text, f"text={r2.text!r}")
    check("mesmo conversation_id", r2.conversation_id == r1.conversation_id)


def test_live_handoff() -> None:
    print("\n[LIVE handoff estruturado]")
    r = call_agy_handoff(
        "Analise (sem editar nada) o risco de fazer deploy numa sexta as 18h.",
        model=PROBE_MODEL, timeout=120,
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
        {"prompt": "Responda so: alpha", "model": PROBE_MODEL},
        {"prompt": "Responda so: beta", "model": "Modelo Fantasma"},   # falha isolada
        {"prompt": "Responda so: gamma", "model": PROBE_MODEL},
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
    check("contem o Flash atual", "Gemini 3.8 Flash (Low)" in models)
    check("contem o synth", "Claude Opus 4.6 (Thinking)" in models)


def test_live_pipeline_chain() -> None:
    print("\n[LIVE pipeline com chain_conversation]")
    res = pipeline(
        [
            {"prompt": "Escolha um numero primo entre 50 e 60 e responda so o numero.",
             "model": PROBE_MODEL},
            {"builder": lambda prev: "Qual numero voce acabou de escolher? Responda so o numero.",
             "model": PROBE_MODEL},
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
    test_default_model_e_cota()
    test_imagem_puro()
    test_cota_refinada()
    test_exemplos_compilam()
    test_catalogo_sincronizado()

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
