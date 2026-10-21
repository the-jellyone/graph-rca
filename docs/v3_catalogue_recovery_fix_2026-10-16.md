# Catalogue recovery fix

The primary v3 controller stopped during catalogue database recovery. The
catalogue returns HTTP 200 with a JSON error object while MySQL reconnects.
Inherited v2 preparation iterated that object as products and indexed its string
keys, raising `TypeError`. The bounded recovery loop catches preparation and
request failures, so this exception escaped and left the lab blocked.

V3 preparation now validates the catalogue list, product IDs and finite prices
before selecting an affordable product. It validates basket response shapes and
resets preparation flags before every attempt. Invalid responses raise
`PreparationFailed`, which follows the existing bounded retry path. Authentication,
checkout observations, timing, rewards and the physical evaluation protocol stay
the same. V1/v2 preparation code is unchanged.

Docker was available when investigated, but all existing lab containers had
exited. Starting those same containers restored the declared identities and image
IDs. Authentication, checkout and shipping checks passed before the recovery
journal was cleared.

Eighteen added regression cases cover malformed catalogue and basket responses
and retrying the same workload without stale preparation flags. All 22 workload
tests pass, and the full regression passed all 250 tests. The engineering script
`scripts/verify_v3_catalogue_recovery.py` also
completed three real catalogue database stop/restart cycles. Every cycle observed
an HTTP 200 JSON object during recovery, then a product list and successful auth,
checkout and shipping operations. See `experiments/v3/catalogue-recovery-proof.json`.
The complete software validation result is recorded separately in
`experiments/v3/software-validation.json` after it passes.

The old primary cohort contained 22 accepted captures from two training sessions.
Every bundle checksum and accepted recovery receipt was verified before and after
archiving. Those captures, their schedule, controller records and frozen source
remain under `runs/adaptive_v3_multi/cohort-revisions/<previous-runtime>/`.
The prior source snapshot and validation proofs also remain under
`experiments/v3/revisions/<previous-runtime>/`.

The corrected cohort starts fresh at `datasets/raw_v3`, with the same declared
440 slots and train/validation/test allocation. Old captures are audit records;
none enter the corrected cohort. The explicit revision and archived bundle hashes
are in `experiments/v3/catalogue-recovery-revision.json`. The 30 accepted
engineering pilots remain separate, with a declared software revision and their
original receipts preserved.
