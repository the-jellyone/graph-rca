# Exact stats analysis revision

The v3 physical cohort completed all 440 accepted captures on October 18. Its
split is frozen with 20 training, 10 validation and 10 test sessions. The first
single-scope encoder fit failed before fitting an artifact: strict coverage
validation found two identical payment stats messages at the same timestamp in
training incident `dec4733e-ad52-4764-b8cd-d809569cf483`.

The duplicate rows have identical fields and values. No payment or carts sample
in that training inspection was outside its observation window. Removing a
repeated message from an in-memory analysis view avoids counting the same
observation twice. Raw capture files and their checksums remain unchanged.

`measurements_v3.unique_stats` declares this narrow projection. A repeated source
and timestamp with different content still raises an error. Window and coverage
validation remain strict. V3 encoder fitting and evidence replay apply the same
projection. Legacy v1/v2 measurement code is unchanged.

`experiments/v3/analysis-revision-exact-stats-v1.json` links the unchanged physical
source, complete capture schedule and raw split to the new analysis source.
The discovery used training data only. No reserved outcomes or fitted results
informed the rule. The physical collection source and its software proof remain
under `experiments/v3/revisions/<collection-runtime>/`.

All 32 focused tests passed. The full software validation must pass before the
controller continues analysis. Its source transition must preserve the previous
controller history, freeze the new analysis source, and retain only verified
completed collection and split stages. Physical collection must not restart
under the changed analysis source. No capture, label, split or acceptance gate is
changed by this revision.

## Scope correction

The first revision then failed because its projection checked unused shipping
and user stats streams in the same training capture. Those streams contain
different values with one repeated timestamp. Neither is a declared CPU or
memory evidence source. V3 CPU observes payment; v3 memory observes carts.

The separately declared v2 analysis revision scopes projection to the sources
actually consumed by these tools. It preserves unused rows unchanged and still
rejects conflicting messages in a selected source. This matches the existing
fitting and replay action scope. Both training-only encoder preflights pass,
along with 34 focused tests. Preflight encoders are separate engineering outputs
and are not promoted to fitted experiment artifacts. Full software validation
must pass before the corrected analysis source is frozen and the controller
resumes. The v1 declaration, source and software proof remain preserved.
