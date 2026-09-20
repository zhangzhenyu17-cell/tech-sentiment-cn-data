# EDGE-1 official attachment HTML fallback correction — 2026-09-21

状态：`MECHANICAL_TRANSPORT_FIX / SAME_PROVIDER_ONLY / EVIDENCE_SEMANTICS_UNCHANGED`

## 现象

修复 gzip transport 后，EDGE-1 earnings evidence qualification Run `35525808045`
在部分 SSE/SZSE 官方附件上继续出现：

- `invalid pdf header: b'<html'`
- `EOF marker not found`

这说明 canonical PDF URL 的普通 HTTPS 请求返回了 HTML challenge/landing body，
而不是 PDF 文档本体。

## 修复

只增加同一官方 provider 内的 transport fallback：

1. 普通 HTTPS 仍是第一路径；
2. gzip transport decoding 仍按既有逻辑执行；
3. 如果解码后的 body 明确是 HTML，且 canonical host 属于 SSE/SZSE：
   - 使用 browser-fingerprint HTTPS session；
   - 只访问同一交易所官方 disclosure bootstrap；
   - 再请求**完全相同的 canonical attachment URL**；
   - redirect 必须留在同一交易所 provider host family；
4. 若 fallback 后仍是 HTML，则立即 fail closed；
5. provenance 记录 `transport_method=same_provider_browser`，并继续保留
   `transport_sha256` / `document_sha256`。

## 严格不变

- 不搜索替代文档；
- 不更换 document identity；
- 不跨 provider；
- 不降级 HTTP；
- classifier/token 不变；
- 80% earnings coverage gate 不变；
- universe/scope 不变；
- event / evidence available date 不变；
- UNKNOWN 仍是 UNKNOWN；
- 不读取 Low/High outcome；
- 不改变 Evidence rule / Production / trading authority。

这是 hosted-runner 与官方静态附件 endpoint 的机械 transport compatibility 修复。
