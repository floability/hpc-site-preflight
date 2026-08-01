# Preflight case-study compute specification

This synthetic Floability compute specification requests workers with 112 CPU cores,
128,000 MB of memory, and two GPUs. It is deliberately shaped to fit Anvil's measured
`gpu` partition but not Stampede3's measured GPU partitions, which expose 96 CPU cores
per node.

It is an input fixture only; preflight never submits the generated command.
