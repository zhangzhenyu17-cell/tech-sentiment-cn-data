# V4-A Official Filing Redundancy Design

Status: DESIGN ONLY / NOT ENABLED

This document records a reliability design for official filing acquisition. It does not change the current canonical evidence source, qualification semantics, evidence eligibility, PIT/no-lookahead rules, or production permissions.

## Goal

Reduce single-provider transport risk while preserving exact official-document identity and fail-closed behavior.

## Proposed source topology

For Shanghai-listed issuers:

1. Exchange-native SSE official archive and official PDF transport.
2. CNINFO official archive as an independent official fallback.

For Shenzhen-listed issuers:

1. Exchange-native SZSE official archive and official PDF transport.
2. CNINFO official archive as an independent official fallback.

Exchange XBRL/structured disclosures may be used only as a fact-level cross-check unless separately qualified as canonical evidence.

Third-party aggregators, market-data portals and search-engine copies are diagnostic-only and are not canonical evidence.

## Identity and provenance requirements

Any future redundant path must preserve:

- issuer/entity identity;
- report period;
- official publication timestamp or the existing conservative availability rule;
- immutable official document identity;
- canonical official HTTPS URL;
- exact downloaded bytes and SHA-256;
- parser version;
- acquisition provider and retrieval endpoint;
- no-lookahead availability semantics.

A transport fallback must never silently substitute another report, another revision, another issuer, or an unofficial mirror.

## Cross-source agreement

If two official providers expose the same filing, the redundancy layer should compare at minimum:

- issuer and report period;
- publication date/timestamp where available;
- report title/type;
- selected critical facts;
- document/revision identity when the providers expose compatible native identifiers.

A material disagreement must fail closed and produce a diagnostic artifact. It must not select whichever provider makes qualification pass.

## Qualification boundary

No source is added to canonical qualification merely because it is reachable.

Before activation, a separate authorized change must specify and validate:

- source-specific historical coverage;
- point-in-time availability semantics;
- revision handling;
- pagination completeness;
- document identity stability;
- fact parser compatibility;
- cross-source disagreement policy;
- migration effects on existing receipts and evidence identities.

Until such authorization is completed, the current qualification source rules remain unchanged.

## Engineering use allowed now

The following can be implemented without activating the design:

- source-specific connectivity diagnostics;
- protocol adapters that write no canonical evidence;
- fixture and parser compatibility tests;
- cross-source comparison reports marked diagnostic-only.

No research, evidence promotion, source-eligibility change, production promotion, or trading permission is implied by this design.
