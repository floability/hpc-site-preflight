# Preflight case-study compute specification

This synthetic Floability compute specification requests workers with 120 CPU cores and
1,200,000 MB of memory. It is deliberately shaped to fit Stampede3's measured `amd-rtx`
partition but not any measured Anvil partition.

It is an input fixture only; preflight never submits the generated command.
