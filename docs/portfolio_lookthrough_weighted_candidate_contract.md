# Portfolio Look-through Weighted Candidate Contract

Status: `PUBLIC_CANDIDATE_ONLY / NO_PIT_QUALIFICATION / NO_TRADING_AUTHORITY`

This contract extends the existing EastMoney/Tiantian fund-holdings parser with a parallel weighted-candidate interface for Portfolio Look-through engineering.

## Boundary

The public parser may extract a stock code and an unambiguous single percentage cell from a disclosed fund-holdings row. The extracted percentage is stored only as `weight_within_parent_candidate`.

The parser must not emit the authoritative private field `weight_within_parent` and must always emit:

- `source_published_on = null`;
- `pit_qualified = false`;
- `qualification_state = CANDIDATE_ONLY_PUBLICATION_DATE_UNVERIFIED`;
- `qualification_blocker = PUBLICATION_DATE_NOT_ESTABLISHED`.

A fund report date is not a publication date. Public reachability at fetch time does not prove that a row was available at an earlier portfolio decision date.

## Parsing discipline

A row receives a candidate weight only when:

1. a single six-digit stock code can be identified; and
2. exactly one unambiguous percentage cell is present; and
3. the percentage is within `(0, 100]`.

Rows with zero or multiple percentage cells remain part of the symbol candidate set but receive no weight. Duplicate weighted stock identities inside one disclosure fail closed.

Existing `FundHoldingBatch` behavior is unchanged so historical evidence/research artifacts retain their prior schema and semantics.

## Promotion gate

Promotion into private `Portfolio Look-through V1` requires an independent private provenance gate to establish the actual publication date and map the public fund code to an active private pooled position. Only then may a candidate value be copied into `weight_within_parent` with `pit_qualified=true`.

This public contract does not change model evidence, Production status, portfolio actions, automatic rebalancing, or trading authority.
