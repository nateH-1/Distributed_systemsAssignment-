# Storage and replication

Python 3.13, standard library only. Run the same repository on both storage
nodes. From `distributed_mailbox`, on **node-2**:

```bash
python3 storage.py --node-id node-2 --db data/node-2.sqlite3 --peer http://node-3:9000
```

On **node-3**:

```bash
python3 storage.py --node-id node-3 --db data/node-3.sqlite3 --peer http://node-2:9000
```

Keep the database path and node ID unchanged after restart. SQLite WAL and FULL
synchronous transactions persist each accepted mutation before returning 200.
Only these storage nodes hold mailbox data. The existing node-1 configuration
uses `MAIL_STORAGE_NODES=node-2,node-3` and `MAIL_STORAGE_PORT=9000`.

The endpoints in `storage_client.py` are implemented without changing the public
API. UUID message IDs come from the existing front end. Repeating a store with
the same ID/content succeeds without resetting its read flag; conflicting
content or reuse of a deleted ID returns 409. Missing messages return 404.

## Replication protocol and consistency

Every half second each node POSTs its durable operation log as
`{"operations":[...]}` to the peer's `/internal/sync`. The receiver commits the
union and responds with its log. The caller commits the returned union.
Each operation contains a UUID operation ID, Lamport counter, node ID, kind,
user, message ID and value. Duplicate delivery is harmless. Counters advance
beyond all locally known operations. The total order is counter, node ID,
operation ID. Concurrent read updates resolve to the last operation in that
order. Deletions permanently dominate that message ID, preventing resurrection
when an old node reconnects. Operation records and tombstones are retained.

**Acknowledgment is after one local durable commit**, with asynchronous peer
replication. If either node stops, the other accepts all operations. Restarted
nodes exchange missed operations automatically. Replication is eventually
consistent: a just-restarted replica may temporarily serve stale data until
catch-up completes. A successful write can be unavailable if its sole up-to-date
replica fails before replication; it returns on that replica's recovery unless
its disk is lost. This design does not claim linearizability or quorum durability.

Peer calls time out after 0.7 seconds and retry in the background; no sync call
is made while holding the database lock. SQLite serializes local mutations.
Sync errors retain local data for the next retry. Both storage services are
internal to one site and must not be exposed beyond the course's site network.
`GET /internal/status` reports node ID and operation count.

## Verification

```bash
python3 -m unittest discover -s tests -v
```

Tests use real local HTTP storage servers and the existing front-end application
to exercise delivery, replica convergence, node-2 outage, writes on node-3,
node-2 recovery, node-3 outage, read updates and deletion. Separate durable-store
tests verify database reopen, concurrent read resolution and tombstone replay.
Run the assignment's restart and failure sequence on the actual course nodes
as well; local test timings are not course-node measurements.

Development validation: durable-store and front-end unit tests pass. The HTTP
integration test is skipped when the host forbids loopback sockets (WinError
10013). Actual-node deployment and HTTP/failure verification remain pending;
the code has not been verified on the course nodes in this session.

Limitations: whole-log exchange and replay favor simplicity over scale. Sync
requests are limited to 16 MiB; large deployments need pagination/compaction.
Two replicas cannot guarantee availability and strong consistency through every
partition. A returning replica needs time to catch up before a planned failover.
