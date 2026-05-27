# testdata/

Reserved for fixture data. The repo pack seeds its sqlite database at
boot time (see `internal/store/Open`) so we don't ship a binary `.db`
file here — those produce noisy and meaningless diffs whenever a row
is added.

If a future mission needs a frozen-on-disk fixture, add it here and
reference it from the mission's `hidden_tests/runner.sh`.
