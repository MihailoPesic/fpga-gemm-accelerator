# Third-party material

The GEMM datapath, banking, DMA, controllers, host package and verification
environment are project sources. External board references, vendor IP and
installed tools retain their own terms; a license for this project's custom
code does not replace those terms.

## Digilent board constraints

The inactive reference file
[Nexys-A7-50T-Master.xdc](platform/nexys_a7/reference/Nexys-A7-50T-Master.xdc)
comes from [Digilent/digilent-xdc](https://github.com/Digilent/digilent-xdc).
On 2026-10-09, its 19,613 bytes matched the
[upstream file at commit c9d45863c04e8650999a9705c13b3ee941aef3fd](https://github.com/Digilent/digilent-xdc/blob/c9d45863c04e8650999a9705c13b3ee941aef3fd/Nexys-A7-50T-Master.xdc)
exactly. That file revision is dated 2022-05-31.

```text
SHA-256 7c395ac9bb104fdbd805d805d8e3cf7f2b7674cd4aa02dcc511a64ad831677f4
```

Digilent distributes it under the
[MIT license](https://github.com/Digilent/digilent-xdc/blob/c9d45863c04e8650999a9705c13b3ee941aef3fd/License.txt).
The complete upstream notice is retained below. The reference file is for pin
lookup; active project constraints are selected separately under
`platform/nexys_a7/`. This content match does not identify the board-store
revision used by the historical native project.

## AMD/Xilinx IP and configuration

The retained native project includes Vivado-generated configuration:

| File under `accelerator nexys.srcs/sources_1/ip/` | Recorded IP |
| --- | --- |
| `clk_wiz_0/clk_wiz_0.xci` | Clocking Wizard 6.0, revision 19 |
| `mig_7series_0/mig_7series_0.xci` | MIG 7 Series 4.2, revision 2 |
| `mig_7series_0/mig_a.prj` | MIG 4.2 memory/pin settings |

These files preserve configuration, rather than a project-owned DDR controller
or clocking implementation. The current AXI platform generator also instantiates
SmartConnect 1.0, Processor System Reset 5.0, Utility Vector Logic 2.0 and
Constant 1.1. See [create_axi_platform.tcl](scripts/create_axi_platform.tcl).

Vivado generates their implementation and simulation products locally under
ignored `build/`. The Micron DDR2 model supplied with the generated MIG example
is used unchanged there; its source is not vendored in this repository.
Published evidence retains selected reports, configuration and generated-file
identities, without granting a new license to vendor IP or models.

AMD's [product licensing page](https://www.amd.com/en/products/adaptive-socs-and-fpgas/intellectual-property/license.html)
identifies the applicable vendor agreements. Its
[Vivado IP configuration guide](https://docs.amd.com/r/en-US/ug892-vivado-design-flows-overview/Configuring-IP)
describes `.xci` customization and generated output products. Use the agreements
and notices supplied with the installed Vivado/IP release; included IP is not
relicensed by this repository. The native `.xpr` retains AMD/Xilinx notices.

## Installed verification and host tools

Python packages are installed from [test](requirements-test.txt),
[host](requirements-host.txt), [formal](requirements-formal.txt) and
[benchmark](requirements-benchmark.txt) requirement files. Icarus Verilog,
Yosys and Vivado are external tools. Their implementations and installed
environments are not vendored; their own licenses and dependency notices apply.

Gemmini is an architectural reference in the
[specification](docs/specification.md), not a port or a redistributed RTL
dependency. Sealed files under `results/` preserve their original bytes and
notices; this provenance record does not modify historical evidence.

## Digilent MIT notice

```text
MIT License

Copyright (c) 2017 Digilent

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
