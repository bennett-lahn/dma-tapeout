# Timing Analysis

Post-RTL / Phase 3 physical timing checklist, after feature-complete RTL and before shuttle freeze. Full `T-*` constraints, evidence, pre-checks, and status: [`../../llm/11-timing-analysis.md`](../../llm/11-timing-analysis.md).

## Scope

- Confirm APS6404L QSPI AC timing at **66 MHz `clk` maximum / approximately 33 MHz SCK** with SCK = clk/2 and **rising-edge RX** under D16 and D27.
- Confirm **IHP SG13G2** pad and TT mux budgets. There is no published binding IHP pad MHz rating, and sky130 66/33 MHz GPIO figures do not apply.
- Keep simulation behavior checks separate from physical closure. Simulation owns `Q-*`; STA and the demoboard close `T-*`.
- Under D26, the ASIC parks CS high and SCK low whenever `~BUS_GNT`; board 10 kOhm CS pull-ups cover reset and pre-enable under `T-PARK`.

## Venue split

| Venue | Focus |
|---|---|
| RTL and delay-annotated simulation | Close the `Q-*` protocol and modeled-edge checks in [`../../llm/verification/04-timing-in-sim.md`](../../llm/verification/04-timing-in-sim.md). This is an approximate pre-check, not physical closure. |
| STA and implementation reports | Close internal, pad, TT mux, setup/hold, duty, transition, and capacitance paths for `T-*`. Qualified SDF may support diagnosis but does not replace STA. |
| Demoboard | Validate real TT silicon, package, PMOD, board load and flight, edge quality, APS6404L behavior, and long copies at 66 MHz `clk` / approximately 33 MHz SCK. |

Engine SCK is a registered `clk/2` toggle, not a combinationally gated `clk`. CE# is asserted before the first rise, held after the last rise, and left high long enough to meet `tCPH`.

M7 FPGA hardware validation (see [`verification/strategy.md`](../verification/strategy.md)) is a separate pre-shuttle checkpoint: an FPGA stands in for the ASIC on the same carrier board and MCU, running real firmware against real PSRAM devices. It closes no `T-*` row, since FPGA I/O electrical characteristics differ from IHP SG13G2 pads.

## Simulation prerequisites

The full `Q-*` definitions live only in [`../../llm/verification/04-timing-in-sim.md`](../../llm/verification/04-timing-in-sim.md). In particular:

- `Q-LAUNCH` checks that driven SIO and OE changes obey the low-SCK launch policy and modeled setup/hold windows. It must pass before physical `T-SP-HD` closure.
- `Q-RXEDGE` reconciles each falling-edge PSRAM launch with exactly one following rising-edge DUT capture. It must pass before physical `T-ACLK` closure.
- `Q-SIO-OWN` / `CHK-PIN-SIO-OWN` require that the ASIC and a PSRAM/SPI device never drive the same bidirectional SIO bit at once. It must pass before physical `T-HZ` turnaround closure.

Passing either prerequisite only validates modeled behavior. It does not close routed IHP/TT paths or board timing.

## Physical closure

| Group | `T-*` items | Required evidence |
|---|---|---|
| PSRAM data timing | `T-ACLK`, `T-SP-HD`, `T-HZ` | STA path budgets plus board flight and device timing, with delay-annotated simulation as an approximate pre-check |
| Clock quality and target | `T-CLKQ`, `T-66` | STA duty and constraints plus measured loaded SCK and long-copy operation |
| Bus parking | `T-PARK` | Digital OE checks plus reset and ownership handoff on the board |
| IHP and TT I/O | `T-GPIO-IN`, `T-GPIO-OUT`, `T-GPIO-LIB` | Final pad and TT mux STA, liberty load checks, and demoboard behavior |

## Related

- Limits: [`limitations.md`](limitations.md)
- QSPI engine: [`blocks/qspi-engine.md`](blocks/qspi-engine.md)
- Simulation timing checks: [`../../llm/verification/04-timing-in-sim.md`](../../llm/verification/04-timing-in-sim.md)
- Gate-level and SDF boundary: [`../../llm/verification/09-gate-level-and-x.md`](../../llm/verification/09-gate-level-and-x.md)
- Roadmap Phase 3: [`../roadmap.md`](../roadmap.md)
