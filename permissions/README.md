# permissions/

`permissions/*.yaml` files contribute ordered rules `{permission, pattern, action}` and
`floors` (shell deny-globs that no mode can bypass). All files load; layers are applied
bundled → project → runtime → agent → session → approved, and the **last matching rule
wins**. A tool's `permission` + the value of its `pattern_arg` are matched with `*`
wildcards (`\*` for a literal star).
