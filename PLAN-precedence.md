# Precedence: `!` above guards, filters and transforms

Agreed 2026-10-09. Tightest first.

| Level | Operators | Example |
|---|---|---|
| 1 | access ops `.` `@` `[]`, `:type`, parentheses | `!a.b:int` is `!(a.b:int)` |
| 2 | `!` negation | `!a=5` is `(!a)=5` |
| 3 | `\|xform`, then guard `=value` and filter `&key=value` | `!a\|int=5` compares `int` of each `(!a)` value, yields the raw value |
| 4 | `&` conjunction | `!a=5&b` is `((!a)=5)&b` |
| 5 | `,` disjunction | `a,b\|int` is `a,(b\|int)`; `(a,b)\|int` transforms both |

Rules:

- A guard, filter or transform after a parenthesized `,` or `&` group is
  shorthand for writing it on every branch: `(a,b)=5` is `(a=5,b=5)` and
  `(first&last)>5` is `(first>5&last>5)`, so it yields nothing when `last`
  fails. Nested groups distribute recursively; cut markers pass through.
- After a `!` expression it filters what the negation yields: `!a=5` is
  `(!a)=5`, the non-`a` values equal to 5, and `!(a,b)=5` is `(!(a,b))=5`.
- `|xform` followed by a guard compares the transformed value and yields
  the raw one, as `a|int=5` does today. Without a guard it transforms the
  yield.
- `:` after a transform name is always a parameter: `a|int:16`. To type a
  transformed value, parenthesize: `(a|foo):int`.
- `!(a=5)` keeps today's meaning of `!a=5`; parentheses are required.

Data model: every `Op` owns its transforms (a `transforms` tuple, usually
empty, applied where that op yields; on `update` the value is transformed
before it is placed, as `a|int` does today). `a.b|int` puts `(int,)` on
`b`; `(a,b)|int` puts it on each branch's last op. `Dotted.transforms` goes
away; `assemble`, `expand`, `pluck`, `apply`, the CLI and sqlize read the
transforms from the ops. The canonical form is the distributed one:
`(a,b)|int` assembles as `(a|int,b|int)`. `ValueGuard`'s existing
match-only transforms (`a|int=5`) keep a distinct name.

Changes from today:

- `!a=5`, `!a&k=v`, `!a|int` move from `!(…)` to `(!a)…`.
- `a,b|int` transforms only `b`.
- Guards, filters and transforms may follow a parenthesized group.

Docs: README's existing [Precedence](#precedence) table (access ops, `!`,
`&`, `,`) is replaced by the five-level table above, with the `!(a=5)`
note.

Implementation (2026-10-09): an op owns its transforms through a
`TransformWrap` around it, the same way `ValueGuard` and `FilterWrap` put a
guard or filter on an op, so `resolve`, `assemble`, `concrete` and the
engine see one more wrap rather than a field on every op. The grammar's
level 3 is one `_postfix` rule applied after an op_seq, a negation or a
group; the per-op guarded rules (`_keycmd_guarded_all`, slot, attr,
recursive and the `_guard_la` lookahead) are gone.

Decided 2026-10-10: `:type` tests the yield, not the node. `x:int` is "x,
when its value is an int", a type guard at level 3 beside `=value`, and
`(a|foo):int` types the transformed value. Today it tests the node being
accessed (`x:int` on `{'x': 3}` is `None`, since the root is a dict). The
old form rewrites one segment earlier: old `a.b:dict` is new `a:dict.b`;
only typing the root itself has no equivalent, and nobody needs it.
Implemented: `TypeRestriction` is a `ValueGuard` whose test is
`isinstance`; `:type` is parsed in the postfix, and on a segment when the
path continues past it, `a:dict.b`. The one place the old container test
did real work, `*[*]:!(str, bytes)` keeping bytes from decomposing into
ints, is written `*:!(str, bytes)[*]`.

Not in scope: `modfin/api` uses no path-level `!`, so nothing there changes.
