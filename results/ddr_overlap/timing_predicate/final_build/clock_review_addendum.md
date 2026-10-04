# Clock-interaction count correction

Full enumeration of the [saved clock-interaction table](routed/review/clock_interaction.txt)
on 4 October 2026 gives 24 clock pairs: 20 Clean and four Ignored. The derived
count in the original [manual review](routed/manual_review.json) omitted one
row and reported 23 pairs with three Ignored. The complete table includes
generated-clock edge relationships and the `mem_refclk` false-path row.

This corrects the interpretation of the recorded table. The raw reports,
exceptions, checkpoint, bitstream and their original seals are unchanged.
It introduces no new crossing or timing exception and does not qualify warm
reset or physical DDR timing.
