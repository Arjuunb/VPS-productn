# Price Action journal revision recovery

## Incident and fixed write path

`PriceActionJournalStore.capture()` has one runtime caller:
`PriceActionPaperAccount.synchronize_strategy()`. The closed-candle stream calls
that method after every accepted candle. Before this repair, the journal hash
covered the full response record, including the current bid/ask, feed reason,
and a dataset fingerprint derived from the rolling candle window. Those fields
changed without a setup lifecycle change and produced a full JSON revision on
nearly every refresh.

The runtime now hashes a material projection. It includes immutable decision
evidence, setup lifecycle transitions, order/fill evidence, funding, and final
outcome. It excludes display bid/ask, display spread, feed explanation text,
heartbeats, and the rolling candle collection. The dataset fingerprint is made
from the setup's frozen context, trigger, pattern, and proposal evidence.
Existing revisions are never updated or deleted by application startup.

All connections to the PA paper database use WAL, `synchronous=NORMAL`, and the
same 10-second busy timeout. Journal writes use a serialized, short
`BEGIN IMMEDIATE` transaction. A persistent lock is reported as
`PERSISTENCE_BLOCKED`; it pauses new entries but does not turn the public market
stream into `DISCONNECTED` or prevent protective paper processing.

## Production recovery sequence

1. Keep live execution disabled. Deploy the repair before compacting anything.
2. Verify revision growth has stopped for unchanged setups:

   ```sql
   SELECT count(*) FROM pa_journal_revisions;
   ```

   Repeat after several dashboard refreshes and at least two closed candles
   without a PA lifecycle change. The count must remain unchanged.
3. Create a consistent backup. Do not use `cp` on the active database. Use the
   existing backup service (Python SQLite online backup API), or stop the app
   cleanly and run:

   ```sql
   VACUUM INTO '/var/lib/tradexa/backups/price_action_paper-consistent.db';
   ```

   Ensure the destination is on a filesystem with enough free space. Hash the
   result and retain the source database until verification is complete.
4. Perform compaction offline into a new database, never in place. Preserve:

   - every `pa_journal_entries` row;
   - the first revision for every journal entry;
   - every `RESEARCHER_ANNOTATION` revision;
   - each revision whose material projection differs from the preceding kept
     revision, including lifecycle, execution fill, funding, and close outcome;
   - original revision IDs, timestamps, initiators, payload hashes, and payload
     JSON bytes for all retained evidence.

   Produce a manifest containing the source backup hash, source and destination
   row counts, every retained revision ID/hash, and every omitted duplicate
   revision ID/hash mapped to its equivalent retained material revision.
5. Validate the candidate database offline with `PRAGMA integrity_check`, foreign
   ownership checks, one open setup/order/position invariants, and sampled JSON
   hash verification. Compare journal summaries before and after compaction.
6. Stop the app, checkpoint WAL, take one final online backup, and atomically
   rename the verified candidate into place. Keep the original and manifests as
   rollback evidence. Start the app and verify `/paper`, `/sessions`, and
   `/bot-status` before re-arming automatic paper mode.

No automatic retention, `DELETE`, `VACUUM`, or history reset is part of the
runtime repair. Compaction requires an explicit operator-reviewed maintenance
window because the existing payloads are audit evidence.
