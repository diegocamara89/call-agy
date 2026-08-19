# Probes exploratorios do ConPTY (legado)

Scripts ad-hoc usados para investigar o comportamento do `pywinpty` (EOF, isalive, close) na epoca
em que o ConPTY era o unico transporte possivel — bug TTY #76 do `agy -p`.

Nao sao testes com assercoes e nao rodam no `tests/test_agy.py`. Continuam aqui porque o
transporte `transport="pty"` ainda existe como fallback para versoes antigas do agy.

Desde o agy 1.1.13 o print mode funciona por pipe/redirect e o transporte padrao e
subprocess + `--output-format json`.
