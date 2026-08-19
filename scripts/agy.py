"""
agy.py - FONTE DA VERDADE para chamar o agy (Antigravity CLI do Google) de forma confiavel.

Tres capacidades + dois helpers de composicao:
    1. call_agy / call_agy_result   -> chamada unica robusta (transporte JSON)
    2. call_agy_parallel            -> N jobs concorrentes (cap + retry/backoff, ordem preservada)
    3. pipeline                     -> encadeamento sequencial (saida de A vira entrada de B)
    +. fanout_synthesize            -> fan-out (N modelos no mesmo prompt) -> reduce/sintese
    +. call_agy_handoff             -> handoff JSON estruturado (contrato da skill `orchestrate`)

TRANSPORTE (verificado em 2026-08-15 contra agy 1.1.13):
    `agy -p "prompt" --output-format json` funciona por pipe, redirect e subprocess comum.
    O bug TTY #76 (0 bytes fora de TTY) foi corrigido no PRINT MODE. Nao ha mais necessidade de
    ConPTY/pywinpty no caminho normal. O envelope JSON entrega, alem do texto:
        conversation_id, status (SUCCESS|ERROR), error, duration_seconds, num_turns,
        usage{input/output/thinking/cache_read/total_tokens}, structured_output (com --json-schema)

    ATENCAO - o bug #76 PERSISTE no subcomando `agy models`: ele TRAVA com 0 bytes fora de um TTY
    (medido: rc=124 em timeout de 45s). Por isso known_models(refresh=True) NAO usa `agy models`;
    usa o probe de modelo invalido (ver _probe_model_catalog), que responde em ~3.6s e custa
    ZERO tokens.

    Fallback ConPTY: transport="pty" (ou auto-heal quando o JSON volta 0 bytes) mantem o caminho
    antigo via pywinpty, para quem estiver preso a uma versao antiga do agy.

PROMPT VIA ARGV (seguro):
    Passamos argv como LISTA com shell=False -> o cmd.exe nunca ve o prompt. Verificado: chaves,
    pipes, `%`, `&`, `<>`, crases e `$HOME` chegam intactos ao modelo. A "regra de ouro" da skill
    `orchestrate` (nao usar -p com {}|% no Windows) vale para chamada VIA SHELL; nao se aplica
    aqui. Limite pratico: 32767 chars por argv (CreateProcess), nao os 8191 do cmd.exe.

Requisito:
    Nenhum no caminho padrao (so a stdlib). pywinpty e OPCIONAL, apenas para transport="pty".

CLI:
    python agy.py single   -p "prompt" [--model "ID"] [--effort low|medium|high] [--timeout N]
    python agy.py parallel --jobs jobs.json [--max-concurrency 4] [--retries 2] [--timeout 180]
    python agy.py pipeline --steps steps.json [--timeout 180] [--no-fail-fast]
    python agy.py fanout   -p "prompt" --models "A;B;C" [--synth-model "ID"] [--timeout 180]
    python agy.py handoff  -p "prompt" [--model "ID"]        # contrato JSON do `orchestrate`
    python agy.py models   [--refresh]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

# --------------------------------------------------------------------------- Constantes publicas

# Os 14 IDs literais aceitos por --model, na ordem em que o agy os lista (mais novo primeiro).
#
# PAPEL MUDOU: esta tupla NAO e mais a unica defesa contra modelo errado. Com --output-format json
# o agy REJEITA modelo invalido explicitamente (rc=1, status="ERROR", error com a lista completa
# dos IDs validos) — nao ha mais fallback silencioso. A validacao pre-call sobreviveu por um motivo
# menor: economizar o round-trip de ~3.6s por job num fanout com typo. Se a tupla ficar velha,
# o pior caso hoje e um falso negativo local, nao um council rodando o modelo errado em silencio.
#
# Fonte da verdade = known_models(refresh=True). Ver "Manutencao do catalogo" no SKILL.md.
KNOWN_MODELS: tuple[str, ...] = (
    "Gemini 3.7 Flash (High)",
    "Gemini 3.7 Flash (Medium)",
    "Gemini 3.7 Flash (Low)",
    "Gemini 3.6 Flash (High)",
    "Gemini 3.6 Flash (Medium)",
    "Gemini 3.6 Flash (Low)",
    "Gemini 3.5 Flash (High)",
    "Gemini 3.5 Flash (Medium)",
    "Gemini 3.5 Flash (Low)",
    "Gemini 3.1 Pro (High)",
    "Gemini 3.1 Pro (Low)",
    "Claude Sonnet 4.6 (Thinking)",
    "Claude Opus 4.6 (Thinking)",
    "GPT-OSS 120B (Medium)",
)

# Data (ISO) da ultima verificacao do catalogo, e a janela de revalidacao.
# Sem historico: sobrescreva a data a cada checagem, mude ou nao a lista.
CATALOG_CHECKED = "2026-08-15"
CATALOG_RECHECK_DAYS = 15

# Default do settings.json (~/.gemini/antigravity-cli/settings.json). So documentacao: para usar
# o default NAO passe --model (omitir e diferente de passar o ID).
DEFAULT_MODEL = "Gemini 3.7 Flash (High)"
# Chairman/sintese: tier de raciocinio, NAO segue o default do settings.json de proposito
# (rebaixar a sintese para um Flash degradaria o fanout/council).
SYNTH_MODEL = "Claude Opus 4.6 (Thinking)"
# Modelo rapido/terse para probes e triagem.
PROBE_MODEL = "Gemini 3.7 Flash (Low)"

DEFAULT_TIMEOUT = 180
FLASH_TIMEOUT = 90    # tier rapido (Flash Low/Medium)
THINK_TIMEOUT = 300   # tier lento (Pro High, Sonnet/Opus Thinking)

# ID sentinela usado so para arrancar do agy a lista oficial de modelos (ele responde rc=1 com
# "Available models:" e a lista). Custa 0 tokens e ~3.6s — nao consome inferencia.
_CATALOG_PROBE_ID = "__agy_py_catalog_probe__"

# Guarda de memoria do leitor (defesa em profundidade; nunca atingida em uso normal).
_MAX_OUTPUT_BYTES = 16 * 1024 * 1024

# Logger do modulo (sem handler proprio -> herda a config do app; warning vai p/ stderr).
_LOG = logging.getLogger("agy")

# Cache opcional populado por known_models(refresh=True).
_MODELS_CACHE: tuple[str, ...] | None = None

# Linhas de "chrome"/ruido (usado so no fallback ConPTY, que ainda captura a TUI inteira).
_NOISE_RE = re.compile(
    r"No hook installed|Fetching available|exec @upstash|context7-mcp|^\s*$",
    re.IGNORECASE,
)

# Ruido INOFENSIVO no stderr — nunca trate stderr como sinal de falha por si so
# (padrao herdado da skill `orchestrate`, secao "FILTRO DE STDERR").
_STDERR_NOISE_RE = re.compile(
    r"IDEClient|cached credentials|companion extension|mcp:|No hook installed",
    re.IGNORECASE,
)

# Sintomas transitorios (rate-limit / sobrecarga) -> dispara retry.
_RATE_RE = re.compile(
    r"\b(429|rate.?limit|too many requests|quota|overloaded|timeout)\b",
    re.IGNORECASE,
)

# Heuristica de falta de autenticacao. Pode ser TRANSITORIO (cota mascarada de auth).
_AUTH_RE = re.compile(r"login|auth|unauthorized|sign in|not logged", re.IGNORECASE)

# Assinatura do erro de modelo invalido no envelope JSON do agy.
_INVALID_MODEL_RE = re.compile(r"invalid model selection|is not recognized as a known model",
                               re.IGNORECASE)

# Contrato de handoff da skill `orchestrate`: o JSON que um executor devolve ao planejador.
# Usado com --json-schema para que o agy PREENCHA o contrato em vez de a gente extrair na marra.
HANDOFF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["OK", "ERRO", "PARCIAL"]},
        "task_summary": {"type": "string"},
        "changed_files": {"type": "array", "items": {"type": "string"}},
        "tests_run": {"type": "boolean"},
        "risks": {"type": "array", "items": {"type": "string"}},
        "analyst_summary": {"type": "string"},
        "next_action": {
            "type": "string",
            "enum": ["DONE", "NEEDS_VALIDATION", "NEEDS_RETRY", "ESCALATE"],
        },
    },
    "required": ["status", "task_summary", "next_action"],
}


class AgyError(RuntimeError):
    """Falha ao chamar o agy: spawn falhou, modelo invalido, ou erro fatal."""


@dataclass
class CallResult:
    """
    Resultado estruturado de UMA chamada. `text` so e confiavel quando ok and status == "OK".

    Campos vindos do envelope JSON do agy (ausentes/zerados no fallback ConPTY):
        conversation_id  -> reaproveite em `conversation=` para continuar a mesma sessao
        structured       -> dict ja parseado quando a chamada usou json_schema (nao precisa regex)
        usage            -> {input_tokens, output_tokens, thinking_tokens, cache_read_tokens, total_tokens}
        num_turns        -> quantos turnos o agy gastou internamente
        agy_duration_s   -> duracao medida PELO agy (elapsed_s e a medida por nos, inclui spawn)
    """

    ok: bool
    text: str
    model: str | None
    status: str  # "OK" | "EMPTY" | "INVALID_MODEL" | "AUTH_ERROR" | "TIMEOUT" | "ERROR"
    error: str | None
    elapsed_s: float
    attempts: int = 1
    raw_len: int = 0
    conversation_id: str | None = None
    structured: dict | None = None
    usage: dict = field(default_factory=dict)
    num_turns: int = 0
    agy_duration_s: float = 0.0
    transport: str = "json"  # "json" | "pty"


# --------------------------------------------------------------------------- Localizacao


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


def _looks_rate_limited(text: str | None) -> bool:
    """True se o texto/erro casar com sintoma transitorio de rate-limit/sobrecarga."""
    return bool(text) and bool(_RATE_RE.search(text))


def _is_auth_error(raw: str | None) -> bool:
    """True se a saida sugerir erro de autenticacao (pode ser transitorio — ver _AUTH_RE)."""
    return bool(raw) and bool(_AUTH_RE.search(raw.lower()))


def _clean_stderr(stderr: str) -> str:
    """Remove o ruido inofensivo do stderr; o que sobrar e sinal de verdade."""
    lines = [ln for ln in (stderr or "").splitlines()
             if ln.strip() and not _STDERR_NOISE_RE.search(ln)]
    return "\n".join(lines).strip()


# --------------------------------------------------------------------------- Extracao de JSON


def extract_json(text: str) -> dict | list | None:
    """
    Extrai um objeto/array JSON de texto misto, em 3 niveis (padrao da skill `orchestrate`).

    PREFIRA `json_schema=` a esta funcao: com schema, o agy devolve `structured_output` ja
    parseado e esta extracao vira desnecessaria. Use aqui so quando a resposta e prosa+JSON
    e voce nao pode/quer impor schema.

    Niveis:
        1. json.loads no texto inteiro (resposta ja limpa);
        2. bloco markdown ```json ... ``` (ou ``` ... ```);
        3. varredura caracter a caracter com contador de profundidade, respeitando strings e
           escapes. Isto e o que um `grep -oP '\\{.*\\}'` GULOSO erra: ele casa do primeiro '{'
           ao ultimo '}' do texto inteiro e devolve lixo.

    Returns:
        dict | list decodificado, ou None se nada valido for encontrado.
    """
    if not text:
        return None

    # Nivel 1: texto puro.
    try:
        return json.loads(text.strip())
    except (json.JSONDecodeError, ValueError):
        pass

    # Nivel 2: bloco markdown cercado.
    fence = re.search(r"```(?:json)?\s*\n(.*?)\n\s*```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            pass

    # Nivel 3: varredura balanceada. Retorna o PRIMEIRO bloco que decodifica.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        while start != -1:
            depth = 0
            in_string = False
            escaped = False
            for i in range(start, len(text)):
                ch = text[i]
                if escaped:
                    escaped = False
                    continue
                if ch == "\\":
                    escaped = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except (json.JSONDecodeError, ValueError):
                            break  # bloco balanceado mas invalido -> tenta o proximo opener
            start = text.find(opener, start + 1)
    return None


# --------------------------------------------------------------------------- Transporte (subprocess)


def _build_argv(
    agy_exe: str,
    prompt: str,
    *,
    model: str | None,
    effort: str | None,
    conversation: str | None,
    continue_last: bool,
    schema_path: str | None,
    skip_permissions: bool,
    sandbox: bool,
    mode: str | None,
    add_dirs: list[str] | None,
    agent: str | None,
    print_timeout: int,
) -> list[str]:
    """Monta o argv do agy. LISTA, nunca string de shell (o prompt nunca passa pelo cmd.exe)."""
    argv = [agy_exe, "-p", prompt, "--output-format", "json"]
    if model:
        argv += ["--model", model]
    if effort:
        argv += ["--effort", effort]
    if conversation:
        argv += ["--conversation", conversation]
    elif continue_last:
        argv += ["--continue"]
    if schema_path:
        argv += ["--json-schema", schema_path]
    if skip_permissions:
        argv += ["--dangerously-skip-permissions"]
    if sandbox:
        argv += ["--sandbox"]
    if mode:
        argv += ["--mode", mode]
    for d in add_dirs or []:
        argv += ["--add-dir", d]
    if agent:
        argv += ["--agent", agent]
    # Mantem o timeout interno do agy alinhado ao nosso (default do agy e 5m).
    argv += ["--print-timeout", f"{print_timeout}s"]
    return argv


def _kill_tree(proc: subprocess.Popen) -> None:
    """
    Mata a ARVORE de processos, nao so o filho direto.

    Padrao herdado da skill `orchestrate` ("WINDOWS: KILL DE ARVORE DE PROCESSO"): no Windows
    `proc.kill()` mata apenas o pai; netos (servidores MCP que o agy sobe) sobrevivem, seguram os
    pipes e um timeout de 120s vira 279s+. `taskkill /F /T` resolve a arvore inteira.
    """
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=15)
        else:
            proc.kill()
    except Exception as exc:  # pragma: no cover - best effort
        _LOG.warning("kill da arvore de processos falhou (pid=%s): %s", proc.pid, exc)
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


def _run_agy(argv: list[str], timeout: int, cwd: str, env: dict | None) -> tuple[int | None, str, str, bool]:
    """
    Executa o agy e devolve (returncode, stdout, stderr, timed_out).

    Popen + CREATE_NEW_PROCESS_GROUP (Windows) para que o kill da arvore alcance os netos.
    Nunca levanta TimeoutExpired: sinaliza via timed_out e devolve o que ja saiu dos pipes.
    """
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    full_env = None
    if env:
        full_env = {**os.environ, **env}

    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,   # agy nunca deve esperar input em modo print
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        env=full_env,
        creationflags=creationflags,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out or "", err or "", False
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=10)
        except Exception:
            out, err = "", ""
        return proc.returncode, out or "", err or "", True


def _parse_envelope(stdout: str) -> dict | None:
    """
    Decodifica o envelope JSON do print mode. Tolerante a lixo de TUI antes/depois do JSON:
    cai no extract_json (varredura balanceada) se o json.loads direto falhar.
    """
    if not stdout.strip():
        return None
    try:
        obj = json.loads(stdout.strip())
    except (json.JSONDecodeError, ValueError):
        obj = extract_json(stdout)
    return obj if isinstance(obj, dict) and "status" in obj else None


# --------------------------------------------------------------------------- Fallback ConPTY (legado)


def _strip_ansi(text: str) -> str:
    """
    Remove ANSI/CSI/OSC e descarta frames de spinner de forma CR-AWARE. So usado no fallback PTY.

    O strip ingenuo `text.replace('\\r','')` CONCATENA as frames do spinner Braille numa linha so e
    GRUDA no primeiro conteudo real. Ordem correta: OSC -> CSI -> outros ESC -> e SO ENTAO, por
    LINHA FISICA, `line.split('\\r')` ficando com o ULTIMO segmento NAO-vazio.
    """
    text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", text)
    text = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"\x1b[@-Z\\-_]", "", text)

    out_lines: list[str] = []
    for line in text.split("\n"):
        if "\r" not in line:
            out_lines.append(line)
            continue
        last_non_empty = ""
        for seg in line.split("\r"):
            if seg.strip():
                last_non_empty = seg
        out_lines.append(last_non_empty)
    return "\n".join(out_lines)


def _clean_text(raw: str) -> str:
    """strip ANSI/CR -> descarta linhas vazias e de chrome -> junta o texto do modelo."""
    clean = _strip_ansi(raw)
    lines = [ln for ln in clean.splitlines() if ln.strip() and not _NOISE_RE.search(ln)]
    return "\n".join(lines).strip()


def _call_via_pty(prompt: str, model: str | None, timeout: int, cwd: str) -> CallResult:
    """
    Transporte LEGADO via ConPTY (pywinpty), para agy antigo com o bug TTY #76 no print mode.

    Nao devolve envelope: sem conversation_id, usage nem structured. Use so como fallback.
    """
    import threading  # local: so o caminho legado precisa

    try:
        import winpty  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(
            "transport='pty' exige pywinpty (pip install pywinpty). "
            "O transporte padrao ('json') nao precisa dele."
        ) from exc

    agy_exe = _find_agy()
    cmd = [agy_exe, "-p", prompt]
    if model:
        cmd += ["--model", model]

    start = time.monotonic()
    try:
        p = winpty.PtyProcess.spawn(cmd, dimensions=(50, 220), cwd=cwd)
    except Exception as exc:
        raise AgyError(f"Falha ao spawnar o agy via ConPTY: {exc}") from exc

    chunks: list[str] = []
    done = threading.Event()

    def _reader() -> None:
        total = 0
        errors = 0
        while True:
            try:
                chunk = p.read(4096)
                errors = 0
                if chunk:
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > _MAX_OUTPUT_BYTES:
                        break
            except EOFError:
                break
            except Exception:
                errors += 1
                if errors >= 5 or not p.isalive():
                    break
                time.sleep(0.05)
        done.set()

    finished = False
    alive_after = False
    t: threading.Thread | None = None
    try:
        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        finished = done.wait(timeout=timeout)
        try:
            alive_after = p.isalive()
        except Exception:
            pass
    finally:
        try:
            p.close(force=True)
        except Exception:
            pass
        if t is not None:
            t.join(timeout=0.5)

    dur = time.monotonic() - start
    raw = "".join(chunks)
    clean = _clean_text(raw)

    if (not finished) or alive_after:
        return CallResult(False, clean, model, "TIMEOUT",
                          f"Timeout de {timeout}s no transporte PTY.", dur, 1, len(raw),
                          transport="pty")
    if _is_auth_error(raw):
        return CallResult(False, "", model, "AUTH_ERROR",
                          "Saida sugere falta de autenticacao. Rode `agy` interativo p/ logar.",
                          dur, 1, len(raw), transport="pty")
    if not clean:
        return CallResult(False, "", model, "EMPTY",
                          f"Saida limpa vazia no transporte PTY (raw_len={len(raw)}).",
                          dur, 1, len(raw), transport="pty")
    return CallResult(True, clean, model, "OK", None, dur, 1, len(raw), transport="pty")


# --------------------------------------------------------------------------- Nucleo


def call_agy_result(
    prompt: str,
    model: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    *,
    validate_model: bool = True,
    cwd: str | None = None,
    effort: str | None = None,
    conversation: str | None = None,
    continue_last: bool = False,
    json_schema: dict | str | None = None,
    skip_permissions: bool = False,
    sandbox: bool = False,
    mode: str | None = None,
    add_dirs: list[str] | None = None,
    agent: str | None = None,
    transport: str = "auto",
    env: dict | None = None,
) -> CallResult:
    """
    Superficie ESTRUTURADA (usada por parallel/pipeline). NUNCA levanta por EMPTY/TIMEOUT/AUTH/
    INVALID_MODEL vindos do agy: devolve CallResult(ok=False, ...). Levanta AgyError so em
    validacao pre-call e falha de spawn.

    Args:
        prompt: prompt enviado ao agy (vai por argv, sem passar pelo shell).
        model: ID literal (ver KNOWN_MODELS). None usa o default do settings.json.
        timeout: tempo maximo em segundos (use o tier do modelo; nunca <60s).
        validate_model: checa o ID contra KNOWN_MODELS antes de gastar o round-trip.
        cwd: diretorio de trabalho do agy. Default = home (workspace confiavel).
        effort: "low" | "medium" | "high" — esforco de raciocinio da sessao.
        conversation: conversation_id de uma chamada anterior -> continua AQUELA sessao.
        continue_last: usa --continue (ultima conversa). Ignorado se `conversation` for dado.
        json_schema: dict (serializado p/ arquivo temporario) ou caminho de arquivo .json.
            Forca saida estruturada -> CallResult.structured vem parseado.
        skip_permissions: --dangerously-skip-permissions (auto-aprova tool calls).
        sandbox: --sandbox (restricoes de terminal).
        mode: "accept-edits" | "plan".
        add_dirs: diretorios extras no workspace (--add-dir, repetivel).
        agent: nome do agente (--agent).
        transport: "auto" (json, com auto-heal p/ pty em 0 bytes) | "json" | "pty".
        env: variaveis extras de ambiente (mescladas sobre os.environ).

    Returns:
        CallResult com status/ok/text/structured/usage/conversation_id para diagnostico.

    Raises:
        AgyError: modelo invalido na validacao pre-call, transport desconhecido, ou falha de spawn.
        FileNotFoundError: agy nao encontrado.
    """
    if transport not in {"auto", "json", "pty"}:
        raise AgyError(f"transport invalido: {transport!r}. Use 'auto', 'json' ou 'pty'.")

    if validate_model and model is not None and model not in KNOWN_MODELS:
        raise AgyError(
            f"Modelo desconhecido: {model!r}. IDs validos: {', '.join(KNOWN_MODELS)}. "
            "Rode known_models(refresh=True) se o catalogo estiver velho, ou passe "
            "validate_model=False para deixar o proprio agy validar."
        )

    workdir = cwd if cwd is not None else str(Path.home())

    if transport == "pty":
        return _call_via_pty(prompt, model, timeout, workdir)

    agy_exe = _find_agy()

    # json_schema: dict -> arquivo temporario; str -> caminho ja existente.
    schema_path: str | None = None
    tmp_schema: Path | None = None
    if json_schema is not None:
        if isinstance(json_schema, dict):
            import tempfile  # local: so este caminho precisa

            fd, name = tempfile.mkstemp(suffix=".json", prefix="agy_schema_")
            os.close(fd)
            tmp_schema = Path(name)
            tmp_schema.write_text(json.dumps(json_schema, ensure_ascii=False), encoding="utf-8")
            schema_path = str(tmp_schema)
        else:
            schema_path = str(json_schema)

    argv = _build_argv(
        agy_exe, prompt,
        model=model, effort=effort, conversation=conversation, continue_last=continue_last,
        schema_path=schema_path, skip_permissions=skip_permissions, sandbox=sandbox,
        mode=mode, add_dirs=add_dirs, agent=agent, print_timeout=timeout,
    )

    start = time.monotonic()
    try:
        rc, stdout, stderr, timed_out = _run_agy(argv, timeout, workdir, env)
    except FileNotFoundError:
        raise
    except Exception as exc:
        raise AgyError(f"Falha ao executar o agy: {exc}") from exc
    finally:
        if tmp_schema is not None:
            try:
                tmp_schema.unlink()
            except OSError:
                pass

    dur = time.monotonic() - start
    raw_len = len(stdout)
    env_json = _parse_envelope(stdout)
    stderr_clean = _clean_stderr(stderr)

    def _mk(ok, text, status, error, **kw) -> CallResult:
        return CallResult(ok, text, model, status, error, dur, 1, raw_len, **kw)

    if timed_out:
        return _mk(False, "", "TIMEOUT",
                   f"Timeout de {timeout}s (arvore de processos encerrada com kill /T).")

    # Envelope ausente -> o agy nao chegou a produzir JSON.
    if env_json is None:
        if raw_len == 0 and transport == "auto":
            # Auto-heal: agy antigo com o bug TTY #76 no print mode. Tenta o ConPTY uma vez.
            _LOG.warning(
                "agy devolveu 0 bytes no transporte JSON (agy antigo com bug TTY #76?). "
                "Tentando fallback ConPTY. Considere atualizar o agy: `agy update`."
            )
            try:
                return _call_via_pty(prompt, model, timeout, workdir)
            except (ImportError, AgyError) as exc:
                return _mk(False, "", "EMPTY",
                           f"Saida vazia e fallback PTY indisponivel: {exc}")
        detail = stderr_clean or repr(stdout[:200])
        return _mk(False, "", "EMPTY" if raw_len == 0 else "ERROR",
                   f"Envelope JSON ausente (rc={rc}, raw_len={raw_len}). Saida: {detail}")

    status_field = str(env_json.get("status", "")).upper()
    response = env_json.get("response") or ""
    structured = env_json.get("structured_output")
    conv_id = env_json.get("conversation_id") or None
    usage = env_json.get("usage") or {}
    num_turns = int(env_json.get("num_turns") or 0)
    agy_dur = float(env_json.get("duration_seconds") or 0.0)
    err_field = env_json.get("error") or ""

    extra = {
        "conversation_id": conv_id,
        "structured": structured if isinstance(structured, dict) else None,
        "usage": usage if isinstance(usage, dict) else {},
        "num_turns": num_turns,
        "agy_duration_s": agy_dur,
    }

    if status_field == "ERROR" or (rc not in (0, None) and err_field):
        # O agy AGORA erra explicitamente em modelo invalido — sem fallback silencioso.
        if _INVALID_MODEL_RE.search(err_field):
            return _mk(False, "", "INVALID_MODEL", err_field.strip(), **extra)
        if _is_auth_error(err_field):
            return _mk(False, "", "AUTH_ERROR", err_field.strip(), **extra)
        return _mk(False, "", "ERROR", (err_field or stderr_clean or "erro sem detalhe").strip(),
                   **extra)

    text = response.strip()
    if not text and not extra["structured"]:
        return _mk(False, "", "EMPTY",
                   f"status={status_field} mas response vazio (raw_len={raw_len}).", **extra)

    return _mk(True, text, "OK", None, **extra)


def call_agy(
    prompt: str,
    model: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    *,
    validate_model: bool = True,
    raise_on_empty: bool = False,
    cwd: str | None = None,
    **kwargs,
) -> str:
    """
    Superficie SIMPLES. Wrapper fino sobre call_agy_result; aceita os mesmos kwargs extras
    (effort, conversation, json_schema, transport, ...).

    Levanta AgyError em INVALID_MODEL e em TIMEOUT (mesmo com texto PARCIAL: devolver resposta
    truncada como se fosse completa corrompe pipelines downstream). Saida vazia "legitima" volta
    "" salvo raise_on_empty=True.
    """
    r = call_agy_result(prompt, model=model, timeout=timeout,
                        validate_model=validate_model, cwd=cwd, **kwargs)
    if r.status == "INVALID_MODEL":
        raise AgyError(r.error or "Modelo invalido.")
    if raise_on_empty and r.status in {"EMPTY", "AUTH_ERROR", "TIMEOUT", "ERROR"}:
        raise AgyError(r.error or f"agy retornou {r.status}.")
    if r.status == "TIMEOUT":
        parcial = f" (texto parcial de {len(r.text)} chars descartado)" if r.text else ""
        raise AgyError(r.error or f"Timeout de {timeout}s sem resposta do agy.{parcial}")
    return r.text


# --------------------------------------------------------------------------- (2) Paralelo


# Chaves de job repassadas direto a call_agy_result.
_JOB_KEYS = (
    "model", "timeout", "effort", "conversation", "continue_last", "json_schema",
    "skip_permissions", "sandbox", "mode", "add_dirs", "agent", "transport", "cwd", "env",
)


def _normalize_job(job: dict | tuple) -> dict:
    """Normaliza um job tupla (prompt, model[, timeout]) ou dict para dict."""
    if isinstance(job, dict):
        if "prompt" not in job:
            raise KeyError("prompt")
        d: dict = {"prompt": job["prompt"]}
        for k in _JOB_KEYS:
            if job.get(k) is not None:
                d[k] = job[k]
        return d
    seq = list(job)
    d = {"prompt": seq[0]}
    if len(seq) > 1 and seq[1] is not None:
        d["model"] = seq[1]
    if len(seq) > 2 and seq[2] is not None:
        d["timeout"] = seq[2]
    return d


def call_agy_parallel(
    jobs: list[dict | tuple],
    max_concurrency: int = 4,
    retries: int = 2,
    timeout: int = DEFAULT_TIMEOUT,
    *,
    retry_backoff: float = 2.0,
    cwd: str | None = None,
    validate_model: bool = True,
) -> list[CallResult]:
    """
    Roda N jobs do agy em paralelo. Cada chamada e um processo agy.exe isolado; o
    communicate() bloqueante libera o GIL -> threads bastam.

    Retorno ALINHADO 1:1 e NA ORDEM dos jobs de entrada. Falha parcial NUNCA aborta o lote.
    Por job, independente:
        retry  -> status in {EMPTY, TIMEOUT, AUTH_ERROR} OU erro/texto casa _looks_rate_limited.
        fatal  -> INVALID_MODEL (o agy agora reporta explicitamente; retentar so queima tempo).
        backoff = retry_backoff * attempt + jitter(0..1s).

    Args:
        jobs: dict {"prompt", + qualquer kwarg de call_agy_result} OU tupla (prompt, model[, timeout]).
        max_concurrency: cap de chamadas simultaneas (default 4; ver SKILL.md para o teto medido).
        retries: tentativas EXTRAS por job em sintoma transitorio.
        timeout: timeout por job (sobrescrito por job["timeout"]).
        retry_backoff: base do backoff linear.
        cwd: diretorio de trabalho default de todos os jobs.
        validate_model: valida o ID de cada job pre-call.

    Returns:
        list[CallResult] alinhada a `jobs`.
    """
    results: list[CallResult | None] = [None] * len(jobs)

    def _run_one(raw_job: dict | tuple) -> CallResult:
        try:
            job = _normalize_job(raw_job)
        except Exception as exc:
            return CallResult(False, "", None, "ERROR",
                              f"Job invalido: {exc!r} (job={raw_job!r})", 0.0, 1, 0)
        prompt = job.pop("prompt")
        model = job.get("model")
        job.setdefault("timeout", timeout)
        job.setdefault("cwd", cwd)
        attempt = 0
        last: CallResult | None = None
        try:
            while attempt <= retries:
                attempt += 1
                try:
                    r = call_agy_result(prompt, validate_model=validate_model, **job)
                except AgyError as exc:
                    st = "INVALID_MODEL" if "Modelo desconhecido" in str(exc) else "ERROR"
                    return CallResult(False, "", model, st, str(exc), 0.0, attempt, 0)
                except FileNotFoundError as exc:
                    return CallResult(False, "", model, "ERROR", str(exc), 0.0, attempt, 0)
                r.attempts = attempt
                last = r
                if r.status == "OK":
                    return r
                # AUTH_ERROR entra no conjunto retryavel: cota/rate-limit transitorio se
                # mascara de auth. Se for auth genuino, esgota os retries e retorna AUTH_ERROR.
                transient = r.status in {"EMPTY", "TIMEOUT", "AUTH_ERROR"} \
                    or _looks_rate_limited(r.error) or _looks_rate_limited(r.text)
                if transient and attempt <= retries:
                    time.sleep(retry_backoff * attempt + random.random())
                    continue
                return r
            return last if last is not None else CallResult(
                False, "", model, "ERROR", "sem tentativas", 0.0, attempt, 0
            )
        except Exception as exc:
            return CallResult(False, "", model, "ERROR",
                              f"Erro inesperado: {exc!r}", 0.0, max(1, attempt), 0)

    with ThreadPoolExecutor(max_workers=max(1, max_concurrency)) as pool:
        futures = {pool.submit(_run_one, job): i for i, job in enumerate(jobs)}
        for fut in futures:
            idx = futures[fut]
            try:
                results[idx] = fut.result()  # ORDEM vem do indice, nao da conclusao
            except Exception as exc:
                results[idx] = CallResult(False, "", None, "ERROR",
                                          f"Future falhou: {exc!r}", 0.0, 1, 0)

    return [
        r if r is not None else CallResult(False, "", None, "ERROR", "sem resultado", 0.0, 1, 0)
        for r in results
    ]


# --------------------------------------------------------------------------- (3) Pipeline


_TEMPLATE_TOKEN_RE = re.compile(r"\{(prev|all|step_\d+)\}")


def _render_template(fmt: str, prev_outputs: list[CallResult]) -> str:
    """
    Renderiza {prev}=ultimo.text, {step_i}=prev[i].text, {all}=join.

    Substituicao LITERAL e segura: SO os tokens conhecidos sao trocados via regex. Chaves '{'/'}'
    literais (JSON, codigo, LaTeX) ficam intactas e NUNCA levantam (str.format quebraria).
    """
    ctx: dict[str, str] = {"prev": prev_outputs[-1].text if prev_outputs else ""}
    for i, r in enumerate(prev_outputs):
        ctx[f"step_{i}"] = r.text
    ctx["all"] = "\n\n".join(r.text for r in prev_outputs)
    return _TEMPLATE_TOKEN_RE.sub(lambda m: ctx.get(m.group(1), ""), fmt)


def template(fmt: str) -> Callable[[list[CallResult]], str]:
    """Acucar: builder que renderiza `fmt` substituindo SO {prev}/{step_i}/{all}."""
    def _builder(prev_outputs: list[CallResult]) -> str:
        return _render_template(fmt, prev_outputs)

    return _builder


def pipeline(
    steps: list[dict],
    initial: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    *,
    fail_fast: bool = True,
    cwd: str | None = None,
    chain_conversation: bool = False,
) -> dict:
    """
    Encadeamento sequencial: executa os steps em ordem acumulando CallResult em prev_outputs.

    Cada step e um dict com kwargs de call_agy_result (model, timeout, effort, json_schema, ...)
    + EXATAMENTE UM de:
        "builder": Callable[[list[CallResult]], str] -> recebe TODAS as saidas anteriores e devolve
                   o prompt. Contrato canonico: robusto a '{'/'}' literais e permite
                   anonimizar/concatenar N saidas.
        "prompt":  str com placeholders {prev}/{step_i}/{all} (acucar p/ casos triviais).

    chain_conversation=True propaga o conversation_id do step anterior, mantendo UMA sessao viva no
    agy em vez de N sessoes independentes — o modelo lembra dos turnos anteriores sem voce reenviar
    o texto no prompt. So faz sentido quando todos os steps usam o MESMO modelo.

    initial != None vira um CallResult sintetico {ok:True, text:initial} no indice 0 do historico.
    fail_fast=True (default): para no 1o step nao-ok. fail_fast=False: segue e o builder decide
    (CallResult.ok e load-bearing: nunca alimente texto de erro como resposta valida).

    Returns:
        {"ok": bool, "results": list[CallResult], "final": str, "failed_step": int|None}
    """
    for i, step in enumerate(steps):
        has_builder = step.get("builder") is not None
        has_prompt = step.get("prompt") is not None
        if has_builder == has_prompt:
            raise AgyError(
                f"Step {i}: forneca EXATAMENTE UM de 'builder' (Callable) ou 'prompt' (str template)."
            )

    prev_outputs: list[CallResult] = []
    if initial is not None:
        prev_outputs.append(CallResult(True, initial, None, "OK", None, 0.0, 1, len(initial)))

    last_conv: str | None = None
    for i, step in enumerate(steps):
        if step.get("builder") is not None:
            prompt = step["builder"](prev_outputs)
        else:
            prompt = _render_template(step["prompt"], prev_outputs)

        kwargs = {k: v for k, v in step.items()
                  if k in _JOB_KEYS and k not in ("timeout", "cwd") and v is not None}
        if chain_conversation and last_conv and "conversation" not in kwargs:
            kwargs["conversation"] = last_conv

        try:
            r = call_agy_result(prompt, timeout=step.get("timeout", timeout),
                                cwd=step.get("cwd", cwd), **kwargs)
        except AgyError as exc:
            # Modelo invalido / falha de spawn vira CallResult de erro, nao excecao crua
            # (consistente com call_agy_parallel).
            st = "INVALID_MODEL" if "Modelo desconhecido" in str(exc) else "ERROR"
            r = CallResult(False, "", step.get("model"), st, str(exc), 0.0, 1, 0)

        prev_outputs.append(r)
        if r.conversation_id:
            last_conv = r.conversation_id

        if not r.ok and fail_fast:
            return {"ok": False, "results": prev_outputs, "final": "", "failed_step": i}

    ok = bool(prev_outputs) and prev_outputs[-1].ok
    return {"ok": ok, "results": prev_outputs,
            "final": prev_outputs[-1].text if ok else "", "failed_step": None}


# --------------------------------------------------------------------------- Fan-out -> reduce


def _default_synth_builder(seed: int | None) -> Callable[[str, list[CallResult]], str]:
    """Builder padrao do chairman: anonimiza/embaralha as respostas ok como 'Response A..N'."""

    def _builder(question: str, advisor_results: list[CallResult]) -> str:
        good = [r for r in advisor_results if r.ok and r.text.strip()]
        shuffled = good[:]
        random.Random(seed).shuffle(shuffled)
        anon = "\n\n".join(
            f"Response {chr(ord('A') + i)}:\n{r.text}" for i, r in enumerate(shuffled)
        )
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
    synth_model: str | None = SYNTH_MODEL,
    synth_prompt_builder: Callable[[str, list[CallResult]], str] | None = None,
    *,
    max_concurrency: int = 5,
    retries: int = 2,
    timeout: int = DEFAULT_TIMEOUT,
    seed: int | None = None,
    cwd: str | None = None,
) -> CallResult:
    """
    Fan-out + reduce (motor base do council). Roda os N models em paralelo sobre o MESMO prompt,
    depois faz UMA chamada de sintese (chairman).

    Esta skill NAO implementa as 5 personas/peer-review do `llm-council`; fornece so o motor.

    Returns:
        CallResult da sintese (a saida do chairman).
    """
    builder = synth_prompt_builder or _default_synth_builder(seed)
    jobs: list[dict | tuple] = [{"prompt": prompt, "model": m} for m in models]
    advisors = call_agy_parallel(
        jobs, max_concurrency=max_concurrency, retries=retries, timeout=timeout, cwd=cwd
    )
    synth_prompt = builder(prompt, advisors)
    # Chairman via call_agy_parallel (1 job) para herdar retry/backoff.
    [chairman] = call_agy_parallel(
        [{"prompt": synth_prompt, "model": synth_model}],
        max_concurrency=1, retries=retries, timeout=timeout, cwd=cwd,
    )
    return chairman


# --------------------------------------------------------------------------- Handoff (orchestrate)


def call_agy_handoff(
    prompt: str,
    model: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    *,
    schema: dict | None = None,
    **kwargs,
) -> CallResult:
    """
    Executa uma tarefa e devolve o HANDOFF JSON do contrato da skill `orchestrate`
    (status / task_summary / changed_files / tests_run / risks / analyst_summary / next_action).

    Por que via --json-schema e nao "peca JSON no prompt": o agy preenche `structured_output` no
    envelope, ja parseado. Isso elimina de vez a classe de bug que o `orchestrate` documenta —
    preambulo antes do JSON, resposta embrulhada em ```json, e o `grep -oP '{.*}'` guloso.
    O campo `.text` continua trazendo a prosa do modelo; o contrato vive em `.structured`.

    Returns:
        CallResult com .structured preenchido conforme HANDOFF_SCHEMA (ou ok=False).
    """
    r = call_agy_result(
        prompt + "\n\nAo terminar, preencha o schema de handoff exigido.",
        model=model, timeout=timeout, json_schema=schema or HANDOFF_SCHEMA, **kwargs,
    )
    # Rede de seguranca: se o agy nao preencheu structured_output, tenta extrair do texto.
    if r.ok and r.structured is None:
        extracted = extract_json(r.text)
        if isinstance(extracted, dict):
            r.structured = extracted
        else:
            r.ok = False
            r.status = "ERROR"
            r.error = "Resposta sem structured_output e sem JSON extraivel do texto."
    return r


# --------------------------------------------------------------------------- Catalogo de modelos


def _probe_model_catalog(timeout: int = 60) -> tuple[str, ...]:
    """
    Arranca do agy a lista oficial de modelos SEM gastar inferencia.

    Truque: `--model <id-inexistente>` faz o agy responder rc=1 com
    status="ERROR" e um `error` que lista os IDs validos apos "Available models:".
    Medido: ~3.6s, usage.total_tokens == 0.

    Por que nao `agy models`: aquele subcomando AINDA sofre o bug TTY #76 — trava com 0 bytes
    fora de um TTY (rc=124 em timeout de 45s). Este probe usa o print mode, que foi corrigido.
    """
    agy_exe = _find_agy()
    argv = [agy_exe, "-p", "x", "--model", _CATALOG_PROBE_ID, "--output-format", "json"]
    _rc, stdout, _stderr, timed_out = _run_agy(argv, timeout, str(Path.home()), None)
    if timed_out:
        raise AgyError(f"Probe de catalogo estourou {timeout}s.")
    env_json = _parse_envelope(stdout)
    if not env_json:
        raise AgyError(f"Probe de catalogo sem envelope JSON (raw_len={len(stdout)}).")
    err = env_json.get("error") or ""
    if "Available models:" not in err:
        raise AgyError(f"Probe de catalogo sem lista de modelos. error={err[:200]!r}")
    block = err.split("Available models:", 1)[1]
    models = tuple(
        dict.fromkeys(ln.strip() for ln in block.splitlines() if ln.strip())
    )
    if not models:
        raise AgyError("Probe de catalogo devolveu lista vazia.")
    return models


def known_models(refresh: bool = False) -> tuple[str, ...]:
    """
    Devolve os IDs de modelo conhecidos. Por padrao usa a tupla estatica KNOWN_MODELS.

    Com refresh=True, consulta o agy via _probe_model_catalog e cacheia. Se falhar, faz fallback
    para KNOWN_MODELS com um warning (nunca levanta).
    """
    global _MODELS_CACHE
    if not refresh:
        return _MODELS_CACHE or KNOWN_MODELS
    try:
        models = _probe_model_catalog()
        _MODELS_CACHE = models
        return models
    except Exception as exc:
        _LOG.warning("known_models(refresh=True) falhou (%s); usando fallback estatico.", exc)
    return _MODELS_CACHE or KNOWN_MODELS


# --------------------------------------------------------------------------- CLI


def _serialize(obj) -> str:
    if isinstance(obj, CallResult):
        return json.dumps(asdict(obj), ensure_ascii=False, indent=2)
    if isinstance(obj, list):
        return json.dumps([asdict(o) for o in obj], ensure_ascii=False, indent=2)
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _common_call_kwargs(args: argparse.Namespace) -> dict:
    return {
        "effort": getattr(args, "effort", None),
        "conversation": getattr(args, "conversation", None),
        "transport": getattr(args, "transport", "auto"),
    }


def _cmd_single(args: argparse.Namespace) -> int:
    r = call_agy_result(args.prompt, model=args.model, timeout=args.timeout,
                        validate_model=not args.no_validate, **_common_call_kwargs(args))
    if args.json:
        print(_serialize(r))
    else:
        if not r.ok:
            print(f"ERRO ({r.status}): {r.error}", file=sys.stderr)
        print(r.text)
    return 0 if r.ok else 1


def _cmd_parallel(args: argparse.Namespace) -> int:
    jobs = json.loads(Path(args.jobs).read_text(encoding="utf-8"))
    results = call_agy_parallel(jobs, max_concurrency=args.max_concurrency,
                                retries=args.retries, timeout=args.timeout)
    print(_serialize(results))
    return 0 if all(r.ok for r in results) else 1


def _cmd_pipeline(args: argparse.Namespace) -> int:
    steps = json.loads(Path(args.steps).read_text(encoding="utf-8"))
    result = pipeline(steps, timeout=args.timeout, fail_fast=not args.no_fail_fast,
                      chain_conversation=args.chain_conversation)
    print(json.dumps({
        "ok": result["ok"], "final": result["final"], "failed_step": result["failed_step"],
        "results": [asdict(r) for r in result["results"]],
    }, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


def _cmd_fanout(args: argparse.Namespace) -> int:
    models = [m.strip() for m in args.models.split(";") if m.strip()]
    r = fanout_synthesize(args.prompt, models, synth_model=args.synth_model, timeout=args.timeout)
    print(_serialize(r))
    return 0 if r.ok else 1


def _cmd_handoff(args: argparse.Namespace) -> int:
    r = call_agy_handoff(args.prompt, model=args.model, timeout=args.timeout)
    # Stdout comeca com '{' e termina com '}' -> parse direto via jq/Python, como manda o
    # contrato de handoff do `orchestrate`.
    print(json.dumps(r.structured if r.ok and r.structured else {
        "status": "ERRO", "task_summary": r.error or "falha ao chamar o agy",
        "changed_files": [], "tests_run": False, "risks": [],
        "analyst_summary": r.status, "next_action": "ESCALATE",
    }, ensure_ascii=False, indent=2))
    return 0 if r.ok else 1


def _cmd_models(args: argparse.Namespace) -> int:
    for m in known_models(refresh=args.refresh):
        print(m)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agy.py",
        description="Chama o agy (Antigravity CLI): single / parallel / pipeline / fanout / handoff / models.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("single", help="Uma chamada do agy.")
    sp.add_argument("-p", "--prompt", required=True)
    sp.add_argument("--model", default=None, help='ID literal (ex: "Gemini 3.7 Flash (Low)").')
    sp.add_argument("--effort", default=None, choices=["low", "medium", "high"])
    sp.add_argument("--conversation", default=None, help="conversation_id para continuar a sessao.")
    sp.add_argument("--transport", default="auto", choices=["auto", "json", "pty"])
    sp.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    sp.add_argument("--no-validate", action="store_true", help="Nao validar o ID do modelo.")
    sp.add_argument("--json", action="store_true", help="Imprime o CallResult completo.")
    sp.set_defaults(func=_cmd_single)

    pp = sub.add_parser("parallel", help="N jobs em paralelo (JSON; imprime CallResult[]).")
    pp.add_argument("--jobs", required=True, help="JSON: [{prompt, model?, timeout?, ...}, ...]")
    pp.add_argument("--max-concurrency", type=int, default=4)
    pp.add_argument("--retries", type=int, default=2)
    pp.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    pp.set_defaults(func=_cmd_parallel)

    cp = sub.add_parser("pipeline", help="Encadeia passos (JSON; so 'prompt'/template via CLI).")
    cp.add_argument("--steps", required=True)
    cp.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    cp.add_argument("--no-fail-fast", action="store_true")
    cp.add_argument("--chain-conversation", action="store_true",
                    help="Propaga o conversation_id entre os steps (uma sessao so).")
    cp.set_defaults(func=_cmd_pipeline)

    fp = sub.add_parser("fanout", help="Fan-out N modelos no mesmo prompt -> sintese.")
    fp.add_argument("-p", "--prompt", required=True)
    fp.add_argument("--models", required=True, help='IDs separados por ";".')
    fp.add_argument("--synth-model", default=SYNTH_MODEL)
    fp.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    fp.set_defaults(func=_cmd_fanout)

    hp = sub.add_parser("handoff", help="Executa e imprime o handoff JSON do `orchestrate`.")
    hp.add_argument("-p", "--prompt", required=True)
    hp.add_argument("--model", default=None)
    hp.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    hp.set_defaults(func=_cmd_handoff)

    mp = sub.add_parser("models", help="Lista KNOWN_MODELS (--refresh consulta o agy).")
    mp.add_argument("--refresh", action="store_true")
    mp.set_defaults(func=_cmd_models)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (AgyError, FileNotFoundError, ImportError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
