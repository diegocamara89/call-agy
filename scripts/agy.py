"""
agy.py - FONTE DA VERDADE para chamar o agy (Antigravity CLI do Google) de forma confiavel.

Tres capacidades + um helper de composicao:
    1. call_agy / call_agy_result   -> chamada unica robusta (transporte ConPTY)
    2. call_agy_parallel            -> N jobs concorrentes (cap + retry/backoff, ordem preservada)
    3. pipeline                     -> encadeamento sequencial (saida de A vira entrada de B)
    +. fanout_synthesize            -> fan-out (N modelos no mesmo prompt) -> reduce/sintese

POR QUE ConPTY (bug TTY #76):
    `agy -p "prompt"` retorna rc=0 e 0 BYTES quando o stdout NAO e um TTY real
    (pipe / redirect / subprocess comum). Bug confirmado:
    github.com/google-antigravity/antigravity-cli/issues/76.
    SOLUCAO: rodar o agy dentro de um ConPTY (pseudo-terminal do Windows) via pywinpty.
    O ConPTY engana o agy (ele "ve" um terminal real) e a saida volta normalmente; depois
    limpamos as sequencias ANSI/CSI/OSC e descartamos as frames de spinner (strip CR-aware).

    NUNCA capture o agy por pipe (`agy -p ... | cat` volta vazio). SEMPRE passe por este modulo.

Requisito:
    pip install pywinpty   (testado: Python 3.12.9 + pywinpty 3.0.5, Windows 11, somente Windows).

CLI:
    python agy.py single   -p "prompt" [--model "ID"] [--timeout N] [--no-validate]
    python agy.py parallel --jobs jobs.json [--max-concurrency 4] [--retries 2] [--timeout 180]
    python agy.py pipeline --steps steps.json [--timeout 180] [--no-fail-fast]
    python agy.py fanout   -p "prompt" --models "A;B;C" [--synth-model "ID"] [--timeout 180]
    python agy.py models   [--refresh]
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import re
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

# --------------------------------------------------------------------------- Constantes publicas

# Os 8 IDs literais aceitos por --model. Usados para validacao PRE-CALL: o agy faz FALLBACK
# SILENCIOSO para o default em modelo invalido (rc=0, sem sinal de erro), entao um typo rodaria
# Opus caro achando que e Flash e quebraria o council. Validar antes de spawnar e obrigatorio.
KNOWN_MODELS: tuple[str, ...] = (
    "Gemini 3.5 Flash (Low)",
    "Gemini 3.5 Flash (Medium)",
    "Gemini 3.5 Flash (High)",
    "Gemini 3.1 Pro (Low)",
    "Gemini 3.1 Pro (High)",
    "Claude Sonnet 4.6 (Thinking)",
    "Claude Opus 4.6 (Thinking)",
    "GPT-OSS 120B (Medium)",
)

# Default do settings.json (~/.gemini/antigravity-cli/settings.json). So documentacao: para usar
# o default NAO passe --model (omitir e diferente de passar o ID).
DEFAULT_MODEL = "Claude Opus 4.6 (Thinking)"
# Modelo rapido/terse para probes e triagem (sem spinner, ~13s).
PROBE_MODEL = "Gemini 3.5 Flash (Low)"

DEFAULT_TIMEOUT = 180
FLASH_TIMEOUT = 90    # tier rapido (Flash Low/Medium)
THINK_TIMEOUT = 300   # tier lento (Pro High, Sonnet/Opus Thinking)

# Guardas de seguranca do reader (defesa em profundidade; nunca atingidos em uso normal).
_MAX_OUTPUT_BYTES = 16 * 1024 * 1024  # cap de memoria p/ chunks (agy.exe em loop / bug de MCP)
_MAX_READER_ERRORS = 5                # erros de read() consecutivos antes de desistir (PTY zumbi)

# Logger do modulo (sem handler proprio -> herda a config do app; warning vai p/ stderr).
_LOG = logging.getLogger("agy")

# Cache opcional populado por known_models(refresh=True) via `agy models`.
_MODELS_CACHE: tuple[str, ...] | None = None

# Linhas de "chrome"/ruido do agy que nao fazem parte da resposta do modelo.
_NOISE_RE = re.compile(
    r"No hook installed|Fetching available|exec @upstash|context7-mcp|^\s*$",
    re.IGNORECASE,
)

# Regex de sintomas transitorios (rate-limit / sobrecarga) -> dispara retry.
_RATE_RE = re.compile(
    r"\b(429|rate.?limit|too many requests|quota|overloaded|timeout)\b",
    re.IGNORECASE,
)

# Heuristica de falta de autenticacao. Pode ser TRANSITORIO no Opus (rate-limit/cota mascarado):
# o mesmo prompt funcionou na tentativa seguinte apos AUTH_ERROR (teste real, 2026-06-20).
_AUTH_RE = re.compile(r"login|auth|unauthorized|sign in|not logged", re.IGNORECASE)


class AgyError(RuntimeError):
    """Falha ao chamar o agy: spawn falhou, modelo invalido, ou erro fatal."""


@dataclass
class CallResult:
    """Resultado estruturado de uma chamada ao agy. `text` so e confiavel quando ok and status=='OK'."""

    ok: bool
    text: str
    model: str | None
    status: str  # "OK" | "EMPTY" | "INVALID_MODEL" | "AUTH_ERROR" | "TIMEOUT" | "ERROR"
    error: str | None
    elapsed_s: float
    attempts: int = 1
    raw_len: int = 0  # len(raw) para diagnostico: distingue EMPTY legitimo de bug-TTY 0-byte


# --------------------------------------------------------------------------- Localizacao + limpeza


def _find_agy() -> str:
    """Localiza o binario do agy no PATH ou no caminho padrao do instalador Windows."""
    path = shutil.which("agy") or shutil.which("agy.exe")
    if path:
        return path
    default = Path.home() / "AppData" / "Local" / "agy" / "bin" / "agy.exe"
    if default.exists():
        return str(default)
    raise FileNotFoundError(
        "agy.exe nao encontrado no PATH nem em AppData/Local/agy/bin. "
        "Instale em: https://antigravity.google/cli"
    )


def _strip_ansi(text: str) -> str:
    """
    Remove ANSI/CSI/OSC e descarta as frames de spinner de forma CR-AWARE.

    O strip ingenuo `text.replace('\\r','')` CONCATENA todas as frames do spinner Braille
    ('...Fetching available models...') numa linha so e GRUDA no primeiro conteudo real, comendo
    texto em modelos High/Thinking. Ordem correta:
      1) remover OSC (ESC ] ... BEL | ESC backslash);
      2) remover CSI (ESC [ ... letra) -> isso ja apaga \\x1b[K;
      3) remover outros ESC + caractere de controle;
      4) SO ENTAO, por LINHA FISICA (split em '\\n'), aplicar o CR: line.split('\\r') e ficar com o
         ULTIMO segmento NAO-vazio (nao o ultimo absoluto: um \\r final esvaziaria a linha). Isso
         descarta as frames intermediarias do spinner e preserva so o texto final daquela linha.
    """
    # 1) OSC: ESC ] ... BEL  ou  ESC ] ... ESC backslash
    text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", text)
    # 2) CSI: ESC [ ... letra (apaga tambem \x1b[K, \x1b[2J, etc.)
    text = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", text)
    # 3) Outros ESC + caractere de controle
    text = re.sub(r"\x1b[@-Z\\-_]", "", text)

    # 4) CR-aware, por linha fisica.
    out_lines: list[str] = []
    for line in text.split("\n"):
        if "\r" not in line:
            out_lines.append(line)
            continue
        segments = line.split("\r")
        last_non_empty = ""
        for seg in segments:
            if seg.strip():
                last_non_empty = seg
        out_lines.append(last_non_empty)
    return "\n".join(out_lines)


def _clean_text(raw: str) -> str:
    """strip ANSI/CR -> descarta linhas vazias e de chrome (_NOISE_RE) -> junta o texto do modelo."""
    clean = _strip_ansi(raw)
    lines = [ln for ln in clean.splitlines() if ln.strip() and not _NOISE_RE.search(ln)]
    return "\n".join(lines).strip()


def _looks_rate_limited(text: str | None) -> bool:
    """True se o texto/erro casar com sintoma transitorio de rate-limit/sobrecarga."""
    return bool(text) and bool(_RATE_RE.search(text))


def _is_auth_error(raw: str | None) -> bool:
    """True se a saida bruta sugerir erro de autenticacao (pode ser transitorio — ver _AUTH_RE)."""
    return bool(raw) and bool(_AUTH_RE.search(raw.lower()))


def _classify(
    raw: str,
    clean: str,
    finished: bool,
    alive_after: bool,
    rc: int | None,
    dur: float,
    timeout: float,
) -> str:
    """
    Classifica o resultado de UMA chamada (o exit code do agy e SEMPRE 0 e inutil para isto).

    INVALID_MODEL e tratado PRE-CALL (fora daqui). Ordem: TIMEOUT -> AUTH_ERROR -> EMPTY -> OK.
        TIMEOUT    = (not finished) or alive_after or (rc is None and dur>=timeout)
        AUTH_ERROR = heuristica sobre o raw (login/auth/unauthorized/sign in/not logged)
        EMPTY      = texto limpo == '' (bug TTY #76 / falha silenciosa / spinner so)
        OK         = texto limpo nao-vazio com EOF limpo
    """
    if (not finished) or alive_after or (rc is None and dur >= timeout):
        return "TIMEOUT"
    if _is_auth_error(raw):
        return "AUTH_ERROR"
    if clean == "":
        return "EMPTY"
    return "OK"


# --------------------------------------------------------------------------- Nucleo (transporte)


def call_agy_result(
    prompt: str,
    model: str | None = None,
    timeout: int = 180,
    *,
    validate_model: bool = True,
    cwd: str | None = None,
    _pty_spawner=None,
) -> CallResult:
    """
    Superficie ESTRUTURADA (usada por parallel/pipeline). NUNCA levanta por EMPTY/TIMEOUT/AUTH:
    devolve CallResult(ok=False, ...). Levanta AgyError so em INVALID_MODEL e falha de spawn.

    Args:
        prompt: prompt enviado ao agy.
        model: ID literal (ver KNOWN_MODELS). None usa o default do settings.json.
        timeout: tempo maximo de espera em segundos (use o tier do modelo; nunca <60s).
        validate_model: se True e model not in KNOWN_MODELS -> AgyError (pre-call, sem spawn).
        cwd: diretorio de trabalho do agy. Default = str(Path.home()) (workspace confiavel).
        _pty_spawner: SEAM DE TESTE (interno). Objeto com .spawn(cmd, dimensions, cwd) que
            substitui winpty.PtyProcess -> permite mockar o ConPTY em CI/Linux sem agy real.
            None (default) usa o winpty real. Nao e API publica.

    Returns:
        CallResult com status/ok/text/elapsed_s/raw_len para diagnostico.

    Raises:
        AgyError: modelo invalido (validate_model=True) ou falha de spawn do ConPTY.
        ImportError: pywinpty nao instalado.
    """
    if validate_model and model is not None and model not in KNOWN_MODELS:
        raise AgyError(
            f"Modelo desconhecido: {model!r}. O agy faz FALLBACK SILENCIOSO para o default em "
            f"modelo invalido. IDs validos: {', '.join(KNOWN_MODELS)}. "
            "Passe validate_model=False para forcar (nao recomendado)."
        )

    spawner = _pty_spawner
    if spawner is None:
        try:
            import winpty  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise ImportError("pywinpty nao instalado. Execute: pip install pywinpty") from exc
        spawner = winpty.PtyProcess

    agy_exe = _find_agy()
    # ARGV COMO LISTA (NUNCA string de shell): IDs de modelo tem parenteses/espacos e o shell
    # corromperia. winpty.spawn aceita lista e nao passa pelo parsing de cmd.exe/PowerShell.
    cmd = [agy_exe, "-p", prompt]
    if model:
        cmd += ["--model", model]

    workdir = cwd if cwd is not None else str(Path.home())

    start = time.monotonic()
    try:
        p = spawner.spawn(cmd, dimensions=(50, 220), cwd=workdir)
    except Exception as exc:  # spawn falhou -> fatal
        raise AgyError(f"Falha ao spawnar o agy via ConPTY: {exc}") from exc

    chunks: list[str] = []
    done = threading.Event()

    def _reader() -> None:
        total = 0
        errors = 0
        while True:
            try:
                chunk = p.read(4096)
                errors = 0  # reset apos leitura bem-sucedida
                if chunk:
                    chunks.append(chunk)
                    total += len(chunk)
                    # C12: cap de memoria. A saida real do agy nunca chega perto disto; um
                    # agy.exe em loop / bug de MCP poderia crescer `chunks` ate exaurir a RAM.
                    if total > _MAX_OUTPUT_BYTES:
                        break
            except EOFError:
                break
            except Exception:
                # C11: cap de erros consecutivos. Se read() falhar repetidamente com o processo
                # ainda "vivo" (zumbi de PTY no Windows), a thread giraria queimando CPU ate o
                # timeout da main. Apos N falhas seguidas, desiste (a main fecha o ConPTY).
                errors += 1
                if errors >= _MAX_READER_ERRORS or not p.isalive():
                    break
                time.sleep(0.05)
        done.set()

    # C2: o try/finally cobre thread+wait. Se threading.Thread().start() falhar (RuntimeError
    # "can't start new thread" por exaustao de recursos do SO), o ConPTY ja vivo NAO pode ficar
    # orfao -> o finally garante p.close(force=True) e o join do reader.
    finished = False
    alive_after = False
    rc: int | None = None
    t: threading.Thread | None = None
    try:
        t = threading.Thread(target=_reader, daemon=True)
        t.start()

        # 3 sinais de fim (nao so o relogio): done.wait + isalive + exitstatus.
        finished = done.wait(timeout=timeout)
        try:
            alive_after = p.isalive()
            rc = p.exitstatus
        except Exception:
            pass
    finally:
        try:
            # force=True manda SIGKILL ao child se ele ignorar o terminate suave. Sem force,
            # close() chama terminate(False) e levanta IOError se o agy travado (spinner/MCP hang)
            # ignorar o sinal -> o processo VAZARIA. O try/except so tolera segunda chamada.
            p.close(force=True)
        except Exception:
            pass
        # C1: junta o reader ANTES de consumir `chunks`. No caminho de TIMEOUT o reader pode
        # seguir vivo (except -> sleep -> continue); ler `chunks` enquanto ele faz append da uma
        # view truncada. Apos o close, o read() cai em EOF/Exception e ele encerra rapido.
        if t is not None:
            t.join(timeout=0.5)

    # rc pode so estabilizar apos o close.
    if rc is None:
        try:
            rc = p.exitstatus
        except Exception:
            rc = None

    dur = time.monotonic() - start
    raw = "".join(chunks)
    clean = _clean_text(raw)
    status = _classify(raw, clean, finished, alive_after, rc, dur, timeout)

    if status == "OK":
        return CallResult(True, clean, model, "OK", None, dur, 1, len(raw))
    if status == "TIMEOUT":
        return CallResult(
            False, clean, model, "TIMEOUT",
            f"Timeout de {timeout}s (finished={finished}, alive={alive_after}, rc={rc}).",
            dur, 1, len(raw),
        )
    if status == "AUTH_ERROR":
        return CallResult(
            False, "", model, "AUTH_ERROR",
            "Saida sugere falta de autenticacao (login/auth). Rode `agy` interativo p/ logar.",
            dur, 1, len(raw),
        )
    # EMPTY: raw_len ajuda a distinguir bug-TTY (raw_len==0) de auth/quota mascarado. C14h: o
    # raw_prefix mostra QUE bytes vieram (ANSI/aviso truncado) quando raw_len>0 mas a limpa zerou.
    raw_prefix = repr(raw[:80]) if raw else "''"
    return CallResult(
        False, "", model, "EMPTY",
        f"Saida limpa vazia (raw_len={len(raw)}, raw_prefix={raw_prefix}). "
        "Bug TTY #76, modelo invalido ou quota silenciosa.",
        dur, 1, len(raw),
    )


def call_agy(
    prompt: str,
    model: str | None = None,
    timeout: int = 180,
    *,
    validate_model: bool = True,
    raise_on_empty: bool = False,
    cwd: str | None = None,
) -> str:
    """
    Superficie SIMPLES (retrocompat e ergonomia). Wrapper fino sobre call_agy_result.

    Levanta AgyError em modelo invalido (validate_model=True) e em TIMEOUT (mesmo com texto
    PARCIAL — ver C3: nao devolve resposta truncada como se fosse completa). Saida vazia
    "legitima" volta "" (sem raise) salvo raise_on_empty=True.

    Args:
        prompt: prompt enviado ao agy.
        model: ID literal (ver KNOWN_MODELS). None usa o default do settings.json.
        timeout: tempo maximo de espera em segundos.
        validate_model: valida o ID pre-call (recomendado por causa do fallback silencioso).
        raise_on_empty: se True, EMPTY/AUTH_ERROR/TIMEOUT levantam AgyError.
        cwd: diretorio de trabalho (default = home).

    Returns:
        Texto limpo da resposta (pode ser "" em EMPTY legitimo quando raise_on_empty=False).

    Raises:
        AgyError: INVALID_MODEL; TIMEOUT sem texto; ou EMPTY/AUTH/TIMEOUT se raise_on_empty=True.
    """
    r = call_agy_result(prompt, model=model, timeout=timeout,
                        validate_model=validate_model, cwd=cwd)
    if r.status == "INVALID_MODEL":  # nao ocorre (já vira AgyError em call_agy_result), defensivo
        raise AgyError(r.error or "Modelo invalido.")
    if raise_on_empty and r.status in {"EMPTY", "AUTH_ERROR", "TIMEOUT"}:
        raise AgyError(r.error or f"agy retornou {r.status}.")
    # C3: TIMEOUT sempre levanta — devolver texto PARCIAL silenciosamente como se fosse a
    # resposta completa corrompe pipelines downstream. Quem quiser o parcial usa call_agy_result.
    if r.status == "TIMEOUT":
        parcial = f" (texto parcial de {len(r.text)} chars descartado)" if r.text else ""
        raise AgyError(r.error or f"Timeout de {timeout}s sem resposta do agy.{parcial}")
    return r.text


# --------------------------------------------------------------------------- (2) Paralelo


def _normalize_job(job: dict | tuple) -> dict:
    """Normaliza um job tupla (prompt, model[, timeout]) ou dict para dict {prompt, model, timeout?}."""
    if isinstance(job, dict):
        d = {"prompt": job["prompt"], "model": job.get("model")}
        if "timeout" in job and job["timeout"] is not None:
            d["timeout"] = job["timeout"]
        return d
    # tupla / lista
    seq = list(job)
    d = {"prompt": seq[0], "model": seq[1] if len(seq) > 1 else None}
    if len(seq) > 2 and seq[2] is not None:
        d["timeout"] = seq[2]
    return d


def call_agy_parallel(
    jobs: list[dict | tuple],
    max_concurrency: int = 4,
    retries: int = 2,
    timeout: int = 180,
    *,
    retry_backoff: float = 2.0,
    cwd: str | None = None,
    validate_model: bool = True,
) -> list[CallResult]:
    """
    Roda N jobs do agy em paralelo. Cada call_agy spawna um agy.exe isolado via ConPTY; o
    paralelismo real esta nos processos e o read() bloqueante do PTY libera o GIL -> threads bastam.

    Modelo de execucao: ThreadPoolExecutor(max_workers=cap). cap default 4 (max recomendado 6 nesta
    maquina; o gargalo e a RAM/CPU local, nao o backend). Para council de 5, suba para 5.

    Retorno ALINHADO 1:1 e NA ORDEM dos jobs de entrada (results[i] <-> jobs[i]). Falha parcial
    NUNCA aborta o lote. Por job, independente:
        retry  -> status in {EMPTY, TIMEOUT} OU saida/erro casa _looks_rate_limited.
        fatal  -> INVALID_MODEL e AUTH_ERROR (sem retry).
        backoff = retry_backoff * attempt + jitter(0..1s).

    Args:
        jobs: lista de dict {"prompt", "model"?, "timeout"?} OU tupla (prompt, model[, timeout]).
        max_concurrency: cap de chamadas simultaneas (default 4).
        retries: tentativas EXTRAS por job em sintoma transitorio (default 2).
        timeout: timeout por job (sobrescrito por job["timeout"]).
        retry_backoff: base do backoff exponencial linear.
        cwd: diretorio de trabalho de TODOS os jobs (default = home).
        validate_model: valida o ID de cada job pre-call (default True; repassado a call_agy_result).

    Returns:
        list[CallResult] alinhada a `jobs`.
    """
    results: list[CallResult | None] = [None] * len(jobs)

    def _run_one(raw_job: dict | tuple) -> CallResult:
        # Normalizacao TOLERANTE: um job malformado (KeyError/IndexError em _normalize_job)
        # vira um CallResult de erro, nunca propaga e aborta o lote.
        try:
            job = _normalize_job(raw_job)
        except Exception as exc:  # job invalido (sem 'prompt', tupla vazia, etc.)
            return CallResult(False, "", None, "ERROR",
                              f"Job invalido: {exc!r} (job={raw_job!r})", 0.0, 1, 0)
        prompt = job["prompt"]
        model = job.get("model")
        job_timeout = job.get("timeout", timeout)
        attempt = 0
        last: CallResult | None = None
        try:
            while attempt <= retries:
                attempt += 1
                try:
                    r = call_agy_result(prompt, model=model, timeout=job_timeout, cwd=cwd,
                                        validate_model=validate_model)
                except AgyError as exc:  # fatal, sem retry
                    # C8: distingue modelo invalido ("Modelo desconhecido") de falha de spawn do
                    # ConPTY -> antes ambos viravam INVALID_MODEL e distorciam a telemetria do lote.
                    st = "INVALID_MODEL" if "Modelo desconhecido" in str(exc) else "ERROR"
                    return CallResult(False, "", model, st, str(exc), 0.0, attempt, 0)
                r.attempts = attempt
                last = r
                if r.status == "OK":
                    return r
                # AUTH_ERROR é incluído no conjunto retryável: testes reais (2026-06-20)
                # mostraram que o Opus retorna AUTH_ERROR por cota/rate-limit transitório e
                # responde normalmente na tentativa seguinte. Se for auth genuíno, esgota os
                # retries e retorna AUTH_ERROR — comportamento correto.
                transient = r.status in {"EMPTY", "TIMEOUT", "AUTH_ERROR"} \
                    or _looks_rate_limited(r.error) or _looks_rate_limited(r.text)
                if transient and attempt <= retries:
                    time.sleep(retry_backoff * attempt + random.random())
                    continue
                return r
            return last if last is not None else CallResult(
                False, "", model, "ERROR", "sem tentativas", 0.0, attempt, 0
            )
        except Exception as exc:  # GARANTIA: nenhuma excecao inesperada aborta o lote
            return CallResult(False, "", model, "ERROR",
                              f"Erro inesperado: {exc!r}", 0.0, max(1, attempt), 0)

    with ThreadPoolExecutor(max_workers=max(1, max_concurrency)) as pool:
        futures = {pool.submit(_run_one, job): i for i, job in enumerate(jobs)}
        for fut in futures:
            idx = futures[fut]
            try:
                results[idx] = fut.result()  # ORDEM vem do indice, nao da ordem de conclusao
            except Exception as exc:  # ultima rede de seguranca: jamais deixar slot None
                results[idx] = CallResult(False, "", None, "ERROR",
                                          f"Future falhou: {exc!r}", 0.0, 1, 0)

    # GARANTIA 1:1 NA ORDEM: nenhum slot pode ficar None (defensivo; _run_one sempre retorna).
    return [
        r if r is not None else CallResult(False, "", None, "ERROR", "sem resultado", 0.0, 1, 0)
        for r in results
    ]


# --------------------------------------------------------------------------- (3) Pipeline


# Tokens reconhecidos no template: {prev}, {all}, {step_0}, {step_1}, ... So estes sao
# substituidos. Qualquer outro '{...}' (JSON, codigo, LaTeX) fica LITERAL e NUNCA levanta.
_TEMPLATE_TOKEN_RE = re.compile(r"\{(prev|all|step_\d+)\}")


def _render_template(fmt: str, prev_outputs: list[CallResult]) -> str:
    """
    Renderiza um template de prompt: {prev}=ultimo.text, {step_i}=prev[i].text, {all}=join.

    Substituicao LITERAL e segura: SO os tokens conhecidos ({prev}/{all}/{step_N}) sao trocados
    via regex. Chaves '{'/'}' literais no template (ex.: pedir JSON '{"k":1}', codigo, LaTeX)
    ficam intactas e NUNCA levantam (diferente de str.format, que quebraria com ValueError).
    Token {step_N} fora do range vira "" (chave ausente fica vazia, nunca levanta).
    """
    ctx: dict[str, str] = {}
    ctx["prev"] = prev_outputs[-1].text if prev_outputs else ""
    for i, r in enumerate(prev_outputs):
        ctx[f"step_{i}"] = r.text
    ctx["all"] = "\n\n".join(r.text for r in prev_outputs)

    def _sub(m: re.Match) -> str:
        return ctx.get(m.group(1), "")

    return _TEMPLATE_TOKEN_RE.sub(_sub, fmt)


def template(fmt: str) -> Callable[[list[CallResult]], str]:
    """
    Acucar: retorna um builder que renderiza `fmt` substituindo SO {prev}/{step_i}/{all}.

    Seguro com chaves '{'/'}' literais (codigo/JSON/LaTeX) tanto no template quanto nas saidas
    anteriores: a substituicao e por regex de tokens conhecidos, nao str.format. Para logica de
    composicao mais rica (anonimizar/embaralhar/concatenar N saidas), escreva um builder Callable.
    """
    def _builder(prev_outputs: list[CallResult]) -> str:
        return _render_template(fmt, prev_outputs)

    return _builder


def pipeline(
    steps: list[dict],
    initial: str | None = None,
    timeout: int = 180,
    *,
    fail_fast: bool = True,
    cwd: str | None = None,
) -> dict:
    """
    Encadeamento sequencial: executa os steps em ordem acumulando CallResult em prev_outputs.

    Cada step e um dict com (model, timeout?) + EXATAMENTE UM de:
        "builder": Callable[[list[CallResult]], str]  -> recebe TODAS as saidas anteriores
                   (nao so a imediata) e devolve o prompt do proximo agy. Contrato canonico:
                   permite anonimizar/embaralhar/concatenar N saidas e nao sofre KeyError com
                   '{'/'}' literais na saida.
        "prompt":  str com placeholders {prev}/{step_i}/{all} -> acucar para casos triviais.

    initial != None vira um CallResult sintetico {ok:True, text:initial, status:"OK"} no indice 0
    do historico (prev_outputs[0]), antes do primeiro step.

    fail_fast=True (default): no 1o step nao-ok, PARA e retorna failed_step. fail_fast=False: segue
    (o proximo builder decide o que fazer com o CallResult nao-ok; ok e load-bearing).

    Returns:
        {"ok": bool, "results": list[CallResult], "final": str, "failed_step": int|None}
    """
    # Validacao: exatamente UM de builder/prompt por step.
    for i, step in enumerate(steps):
        has_builder = "builder" in step and step["builder"] is not None
        has_prompt = "prompt" in step and step["prompt"] is not None
        if has_builder == has_prompt:  # ambos ou nenhum
            raise AgyError(
                f"Step {i}: forneca EXATAMENTE UM de 'builder' (Callable) ou 'prompt' (str template)."
            )

    prev_outputs: list[CallResult] = []
    if initial is not None:
        prev_outputs.append(CallResult(True, initial, None, "OK", None, 0.0, 1, len(initial)))

    for i, step in enumerate(steps):
        if "builder" in step and step["builder"] is not None:
            prompt = step["builder"](prev_outputs)
        else:
            prompt = _render_template(step["prompt"], prev_outputs)

        try:
            r = call_agy_result(prompt, model=step.get("model"),
                                timeout=step.get("timeout", timeout), cwd=cwd)
        except AgyError as exc:  # C10: modelo invalido / falha de spawn vira CallResult de erro,
            # nao aborta o pipeline com excecao crua (consistente com call_agy_parallel).
            st = "INVALID_MODEL" if "Modelo desconhecido" in str(exc) else "ERROR"
            r = CallResult(False, "", step.get("model"), st, str(exc), 0.0, 1, 0)
        prev_outputs.append(r)

        if not r.ok and fail_fast:
            return {"ok": False, "results": prev_outputs, "final": "", "failed_step": i}

    ok = bool(prev_outputs) and prev_outputs[-1].ok
    final = prev_outputs[-1].text if ok else ""
    return {"ok": ok, "results": prev_outputs, "final": final, "failed_step": None}


# --------------------------------------------------------------------------- Fan-out -> reduce


def _default_synth_builder(seed: int | None) -> Callable[[str, list[CallResult]], str]:
    """Builder padrao do chairman: anonimiza/embaralha as respostas ok como 'Response A..N'."""

    def _builder(question: str, advisor_results: list[CallResult]) -> str:
        good = [r for r in advisor_results if r.ok and r.text.strip()]
        rng = random.Random(seed)
        shuffled = good[:]
        rng.shuffle(shuffled)
        blocks = []
        for i, r in enumerate(shuffled):
            letter = chr(ord("A") + i)
            blocks.append(f"Response {letter}:\n{r.text}")
        anon = "\n\n".join(blocks)
        return (
            "You are the chairman of an advisory council. Several advisors independently answered "
            "the question below. Their answers are anonymized as Response A..N (order shuffled).\n\n"
            f"QUESTION:\n{question}\n\n"
            f"ANONYMIZED RESPONSES:\n{anon}\n\n"
            "Synthesize a single, decisive verdict: where they agree, where they clash, blind spots, "
            "a clear recommendation, and the one thing to do first. Be direct; do not hedge."
        )

    return _builder


def fanout_synthesize(
    prompt: str,
    models: list[str],
    synth_model: str | None = "Claude Opus 4.6 (Thinking)",
    synth_prompt_builder: Callable[[str, list[CallResult]], str] | None = None,
    *,
    max_concurrency: int = 5,
    retries: int = 2,
    timeout: int = 180,
    seed: int | None = None,
    cwd: str | None = None,
) -> CallResult:
    """
    Helper de fan-out + reduce (motor base do council). Roda os N models em paralelo sobre o MESMO
    prompt, depois faz UMA chamada de sintese (chairman).

    synth_prompt_builder(question, advisor_results) -> str constroi o prompt do chairman. Default:
    anonimiza/embaralha as respostas ok como 'Response A..N' (seed opcional p/ shuffle reproduzivel).

    Esta skill NAO implementa as 5 personas/peer-review do llm-council; fornece so o fan-out->reduce.

    Returns:
        CallResult da sintese (a saida do chairman).
    """
    builder = synth_prompt_builder or _default_synth_builder(seed)
    jobs: list[dict | tuple] = [{"prompt": prompt, "model": m} for m in models]
    advisors = call_agy_parallel(
        jobs, max_concurrency=max_concurrency, retries=retries, timeout=timeout, cwd=cwd
    )
    synth_prompt = builder(prompt, advisors)
    # Chairman com retry: via call_agy_parallel (1 job) para herdar AUTH_ERROR retryável
    # e backoff — evita o vazio transitório observado no teste real de 2026-06-20.
    [chairman] = call_agy_parallel(
        [{"prompt": synth_prompt, "model": synth_model}],
        max_concurrency=1, retries=retries, timeout=timeout, cwd=cwd,
    )
    return chairman


# --------------------------------------------------------------------------- known_models (cache)


def known_models(refresh: bool = False) -> tuple[str, ...]:
    """
    Devolve os IDs de modelo conhecidos. Por padrao usa a tupla estatica KNOWN_MODELS.

    Com refresh=True, roda `agy models` via ConPTY, parseia as linhas limpas e cacheia. Se o parse
    falhar (ou agy indisponivel), faz fallback para KNOWN_MODELS sem levantar.
    """
    global _MODELS_CACHE
    if not refresh:
        return _MODELS_CACHE or KNOWN_MODELS
    try:
        import winpty  # noqa: PLC0415

        agy_exe = _find_agy()
        p = winpty.PtyProcess.spawn([agy_exe, "models"], dimensions=(50, 220),
                                    cwd=str(Path.home()))
        chunks: list[str] = []
        done = threading.Event()

        def _reader() -> None:
            while True:
                try:
                    c = p.read(4096)
                    if c:
                        chunks.append(c)
                except EOFError:
                    break
                except Exception:
                    if not p.isalive():
                        break
                    time.sleep(0.05)
            done.set()

        # C2: thread+wait em try/finally -> close garantido mesmo se t.start() falhar (sem orfao).
        t: threading.Thread | None = None
        try:
            t = threading.Thread(target=_reader, daemon=True)
            t.start()
            done.wait(timeout=FLASH_TIMEOUT)
        finally:
            try:
                p.close(force=True)  # SIGKILL se o child ignorar o terminate suave (evita vazar)
            except Exception:
                pass
            if t is not None:
                t.join(timeout=0.5)  # C1: junta o reader antes de ler `chunks`
        clean = _clean_text("".join(chunks))
        parsed = tuple(ln.strip() for ln in clean.splitlines() if ln.strip())
        if parsed:
            _MODELS_CACHE = parsed
            return parsed
        # C14c: parse vazio nao deve ser silencioso -> sinaliza o fallback ao operador.
        _LOG.warning("known_models(refresh=True): parse de `agy models` vazio; usando fallback.")
    except Exception as exc:
        _LOG.warning("known_models(refresh=True) falhou (%s); usando fallback.", exc)
    return _MODELS_CACHE or KNOWN_MODELS


# --------------------------------------------------------------------------- CLI


def _serialize(obj) -> str:
    if isinstance(obj, CallResult):
        return json.dumps(asdict(obj), ensure_ascii=False, indent=2)
    if isinstance(obj, list):
        return json.dumps([asdict(o) for o in obj], ensure_ascii=False, indent=2)
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _cmd_single(args: argparse.Namespace) -> int:
    out = call_agy(args.prompt, model=args.model, timeout=args.timeout,
                  validate_model=not args.no_validate)
    print(out)
    return 0


def _cmd_parallel(args: argparse.Namespace) -> int:
    jobs = json.loads(Path(args.jobs).read_text(encoding="utf-8"))
    results = call_agy_parallel(jobs, max_concurrency=args.max_concurrency,
                                retries=args.retries, timeout=args.timeout)
    print(_serialize(results))
    return 0 if all(r.ok for r in results) else 1


def _cmd_pipeline(args: argparse.Namespace) -> int:
    # Via CLI so suportamos 'prompt'/template; builder Callable e exclusivo do import Python.
    steps = json.loads(Path(args.steps).read_text(encoding="utf-8"))
    result = pipeline(steps, timeout=args.timeout, fail_fast=not args.no_fail_fast)
    payload = {
        "ok": result["ok"],
        "final": result["final"],
        "failed_step": result["failed_step"],
        "results": [asdict(r) for r in result["results"]],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


def _cmd_fanout(args: argparse.Namespace) -> int:
    models = [m.strip() for m in args.models.split(";") if m.strip()]
    r = fanout_synthesize(args.prompt, models, synth_model=args.synth_model,
                          timeout=args.timeout)
    print(_serialize(r))
    return 0 if r.ok else 1


def _cmd_models(args: argparse.Namespace) -> int:
    for m in known_models(refresh=args.refresh):
        print(m)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agy.py",
        description="Chama o agy (Antigravity CLI) via ConPTY: single / parallel / pipeline / fanout / models.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("single", help="Uma chamada do agy (imprime texto cru).")
    sp.add_argument("-p", "--prompt", required=True, help="Prompt a enviar ao agy.")
    sp.add_argument("--model", default=None, help='ID literal (ex: "Gemini 3.5 Flash (Low)").')
    sp.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    sp.add_argument("--no-validate", action="store_true", help="Nao validar o ID do modelo.")
    sp.set_defaults(func=_cmd_single)

    pp = sub.add_parser("parallel", help="N jobs do agy em paralelo (JSON; imprime CallResult[]).")
    pp.add_argument("--jobs", required=True, help="JSON: [{prompt, model?, timeout?}, ...]")
    pp.add_argument("--max-concurrency", type=int, default=4)
    pp.add_argument("--retries", type=int, default=2)
    pp.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    pp.set_defaults(func=_cmd_parallel)

    cp = sub.add_parser("pipeline", help="Encadeia passos do agy (JSON; so 'prompt'/template via CLI).")
    cp.add_argument("--steps", required=True, help="JSON: [{prompt, model?, timeout?}, ...]")
    cp.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    cp.add_argument("--no-fail-fast", action="store_true", help="Nao parar no 1o step nao-ok.")
    cp.set_defaults(func=_cmd_pipeline)

    fp = sub.add_parser("fanout", help="Fan-out N modelos no mesmo prompt -> sintese (CallResult).")
    fp.add_argument("-p", "--prompt", required=True, help="Pergunta enviada a todos os modelos.")
    fp.add_argument("--models", required=True, help='IDs separados por ";" (ex: "A;B;C").')
    fp.add_argument("--synth-model", default=DEFAULT_MODEL, help="Modelo do chairman/sintese.")
    fp.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    fp.set_defaults(func=_cmd_fanout)

    mp = sub.add_parser("models", help="Lista KNOWN_MODELS (com --refresh roda `agy models`).")
    mp.add_argument("--refresh", action="store_true", help="Atualiza via `agy models` (ConPTY).")
    mp.set_defaults(func=_cmd_models)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (AgyError, FileNotFoundError, ImportError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
