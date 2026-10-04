# DDR2 memory identification and compatibility

The operator identifies the installed memory as `IS43DR16640C`. The full
speed/package/temperature suffix remains unreadable. The saved MIG preset
is `MT47H64M16HR-25E`; its name describes the configured timing/model, not
the manufacturer of the installed device.

The [platform manifest](../platform/nexys_a7/manifest.json) records this
distinction. The previous cold-start diagnostic remains evidence for the
tested board and workload, not proof of every memory timing or address.

## Configuration comparison

ISSI's revision C1 datasheet, August 11, 2023, lists -3D and -25D x16
ordering options, including commercial and industrial parts. Both support
5 ns CK at CL3. Pages 1, 8, 16, 19-20 and 47 provide the relevant geometry,
termination, timing and ordering tables.
[ISSI datasheet, manufacturer-authored copy](https://www.mouser.com/datasheet/3/3722/1/43_46DR81280C_16640C.pdf).

| Property | Current generated MIG | IS43DR16640C requirements | Assessment |
| --- | --- | --- | --- |
| Organization | 1 Gib, x16, 8 banks; 13 row and 10 column bits | Same | Agrees |
| Supply/interface | 1.8 V DDR2, differential strobes | 1.8 V +/- 0.1 V, SSTL18, 84-ball x16 | Agrees at family level |
| Operating mode | 5 ns CK, CL3, AL0, burst8 | Supported by -3D and -25D | Agrees |
| tRCD / tRP | 15 / 12.5 ns | 15 / 15 ns for -3D; 12.5 / 12.5 ns for -25D | Effective counts both meet 15 ns |
| tRAS / tRC | 40 ns; tRC follows bank sequencing | 40 / 55 ns | No nominal discrepancy; not a measured pin-level bound |
| x16 tFAW | 45 ns | 50 ns for -3D; 45 ns for -25D | Same generated counter for both requirements |
| tRRD / tWR | 10 / 15 ns | 10 / 15 ns | Agree |
| tWTR / tRTP | 7.5 / 7.5 ns | 7.5 / 7.5 ns | Both become 2 CK = 10 ns |
| tRFC / tREFI | 127.5 ns / 7.8 us | Same through 85 C case temperature | Higher-temperature refresh requires separate settings |
| DRAM ODT | 75 ohms | Supported | See termination distinction below |

## Effective controller timing

The generated MIG 4.2 controller uses `tCK=5000 ps`, `nCK_PER_CLK=4`:
one UI cycle is 20 ns. The following conclusions come from the generated
RTL, not from rounding every timing parameter to an assumed UI period.

- `mig_7series_v4_2_mc.v` converts tRP and tRCD with ceiling division by
  tCK. Both 12.5 ns and 15 ns produce 3 CK. Thus the apparently shorter
  configured tRP does not create a shorter command count than 15 ns.
- `mig_7series_v4_2_rank_cntrl.v` uses nFAW only through
  `ceil(nFAW/4)`. The configured 45 ns becomes 9 CK; 50 ns becomes 10 CK.
  Both produce 3 UI cycles and the same SRL depth and inhibit logic.
  This establishes parameter equivalence for tFAW, without asserting an
  independently measured minimum ACT spacing.
- `mig_7series_v4_2_bank_mach.v` passes `ceil(nRAS/4)=2` into the
  bank-state timer for the configured 40 ns tRAS, matching both speed bins.
- The configured tCKE value is 7.5 ns, whereas the C datasheet requires
  a 3-CK pulse. It is not sufficient to compare those numbers alone.
  The generated wrapper ties self-refresh requests low; normal operation
  holds CKE high. Production initialization holds CKE low through its
  power-up delay. Power-down/self-refresh and warm-reset pulse timing are
  not qualified by this review.

## Termination and simulation limits

The ODT table on page 8 defines EMR(1)[A6,A2]=0,1 as nominal
75 ohms (60-90 ohms effective). This is a legal memory setting, but the
[Digilent manual, page 11](https://digilent.com/reference/_media/reference/programmable-logic/nexys-a7/nexys-a7_rm.pdf)
recommends 50-ohm DRAM ODT and 50-ohm FPGA internal termination. Those
are separate settings. No signal-integrity optimization is claimed for
the retained 75-ohm choice.

The generated simulation uses an unmodified Micron `x1Gb`, `x16`, `sg25E`
model. The generated simulation wrapper selects FAST calibration; the
production wrapper selects OFF. MIG's FAST path skips the power-up delay
and accelerates calibration across DQS groups. It does not verify the
physical power ramp, full production startup or warm reset.

The -3D timing has different DQ/DQS skew limits from the
configured Micron speed grade. Matching command counts does not establish
the complete electrical timing budget. The model test therefore remains
a functional integration test of its recorded configuration.

## Next physical check

The confirmed family and reviewed settings support proceeding with a
bounded cold-start test after the image passes simulation and routed
timing, at the retained 200 MHz DDR clock. Use fresh power-off/on and
compare every requested output. The
earlier diagnostic already exercised this board with the retained preset.
The remaining practical risk is calibration or data-integrity failure;
this review found no identified damage mechanism from retaining the board's
existing 1.8 V interface, pin assignment, legal mode and termination.

Record the unread suffix as an open identity/electrical-margin item.
A passing test covers its image, conditions and workload; it does not
close full-memory, temperature, startup-margin or warm-reset qualification.
No official Digilent ISSI substitution notice was found; the
[D.3 schematic](https://digilent.com/reference/_media/programmable-logic/nexys-a7/nexys-a7-d3-sch.pdf)
still labels a Micron device.
