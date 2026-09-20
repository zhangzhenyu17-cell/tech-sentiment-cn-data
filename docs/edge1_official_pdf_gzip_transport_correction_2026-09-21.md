# EDGE-1 official PDF gzip transport correction — 2026-09-21

状态：`MECHANICAL_TRANSPORT_FIX / EVIDENCE_SEMANTICS_UNCHANGED`

## 现象

EDGE-1 earnings evidence qualification Run `35524940504` 在读取 SSE/SZSE 官方附件时出现：

- `invalid pdf header: b'\\x1f\\x8b\\x08...'`
- `EOF marker not found`

`1f 8b 08` 是 RFC 1952 gzip magic，而不是 PDF 的 `%PDF-` header。说明官方 HTTPS endpoint 返回了 gzip-wrapped entity body，当前 urllib transport 没有先做 transport decoding 就把 bytes 交给 PDF parser。

## 修复

仅修改 transport 层：

1. 官方附件请求增加 `Accept-Encoding: identity`；
2. 若响应 bytes 仍以 gzip magic 开头，使用标准 gzip container decoding；
3. parser 只消费解码后的 exact official document bytes；
4. provenance 同时保留：
   - `transport_sha256`：原始 HTTP entity body；
   - `document_sha256`：解码后交给 parser 的官方文档；
   - `transport_encoding=gzip`；
5. 非 gzip payload 原样通过，仍由既有 PDF parser fail closed。

## 不变项

- canonical source URL / provider 不变；
- classifier 仍是 `issuer-explicit-guidance-v1`；
- classifier token 不变；
- 80% coverage threshold 不变；
- STAR50 / ChiNext50 universe 不变；
- event / available date 不变；
- UNKNOWN 仍为 UNKNOWN；
- 不读取 Low/High outcome；
- 不改变 model / Production / trading authority。

本修复只解决传输编码与 PDF parser 之间的机械不兼容，不改变任何 evidence 语义或资格规则。
