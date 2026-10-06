# Accounting Review Note Evidence V1

公开侧只按 private request 中已经存在的 CNINFO 文档身份与附注编号，从同一官方财报中提取精确附注区段。该 rail 不解释 operating / financing 语义，不使用 outcome，不产生 classification resolution；manifest 固定 request hash、document provenance、section hash 与 parser identity。

附注捕获只解决“原始 source note 是否存在且可复核”。private qualification 仍须证明 `operating_cny + financing_cny = raw value`，仅凭 review item 名称或模糊文本不得自动分类。
