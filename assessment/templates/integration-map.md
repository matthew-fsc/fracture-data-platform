# Integration map

- Client:
- Pack: wealth | services

Draw the path a number takes. The platform refuses to skip a layer: the file
lands in object storage before a row is loaded, raw is append-only, canon is
bitemporal, and a mart is pinned to the pack's system time.

```
source system
  -> adapter extract (read-only)
  -> artifact, SHA-256 recorded
  -> raw.<source>__<stream>
  -> adapter map
  -> canon.<entity>
  -> mart.<metric> at a system time
  -> pack figure
```

| Source | Stream | Canonical entity | Natural key | Notes |
|---|---|---|---|---|
| | | | | |

## When two systems disagree

Precedence is per entity, recorded on the pack, and the lower-ranked source
does not overwrite the higher one. For the wealth pack the usual order, first
wins, is `generic_csv`, then the ledger or custodian, then the CRM. The
engagement's actual order is the one in the pack manifest, not a default
remembered from the last client.

Producer identity is three keys: the CRM user, the custodian rep code, and the
payroll employee. They meet in `producer_crosswalk`, which a person reviews.
They are not fuzzy-matched at query time.

## Read-only

No adapter writes back to a source system. Credentials are a path in the
secret store. They do not appear on this map.
