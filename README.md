# Dotted

Sometimes you want to fetch data from a deeply nested data structure. Dotted notation
helps you do that.

## Table of Contents

- [Safe Traversal (Optional Chaining)](#safe-traversal-optional-chaining)
- [Why Dotted?](#why-dotted)
- [Breaking Changes](#breaking-changes)
- [API](#api)
  - [Get](#get)
  - [Update](#update)
  - [Remove](#remove)
  - [Match](#match)
  - [Expand](#expand)
  - [Has](#has)
  - [Mutable](#mutable)
  - [Setdefault](#setdefault)
  - [Pluck](#pluck)
  - [Build](#build)
  - [Apply](#apply)
  - [Assemble](#assemble)
  - [Quote](#quote)
  - [Multi Operations](#multi-operations)
- [Paths](#paths)
  - [Key fields](#key-fields)
  - [Bracketed fields](#bracketed-fields)
  - [Attr fields](#attr-fields)
  - [Slicing](#slicing)
  - [Dot notation for sequence indexing](#dot-notation-for-sequence-indexing)
  - [Empty path (root access)](#empty-path-root-access)
- [Typing & Quoting](#typing--quoting)
  - [Numeric types](#numeric-types)
  - [Quoting](#quoting)
  - [The numericize `#` operator](#the-numericize--operator)
- [Patterns](#patterns)
  - [Wildcards](#wildcards)
  - [Regular expressions](#regular-expressions)
  - [The match-first operator](#the-match-first-operator)
  - [Slicing vs Patterns](#slicing-vs-patterns)
- [Recursive Traversal](#recursive-traversal)
  - [The recursive operator `*`](#the-recursive-operator-)
  - [Recursive wildcard `**`](#recursive-wildcard-)
  - [Depth slicing](#depth-slicing)
  - [Recursive with value guard](#recursive-with-value-guard)
  - [Recursive update and remove](#recursive-update-and-remove)
  - [Recursive match](#recursive-match)
- [Grouping](#grouping)
  - [Operation grouping](#operation-grouping)
  - [Path grouping](#path-grouping)
- [Operators](#operators)
  - [The append `+` operator](#the-append--operator)
  - [The append-unique `+?` operator](#the-append-unique--operator)
  - [The invert `-` operator](#the-invert---operator)
  - [The NOP `~` operator](#the-nop--operator)
  - [The cut `#` operator](#the-cut--operator)
  - [The numericize `#` operator](#the-numericize--operator-1)
- [Filters](#filters)
  - [The key-value filter](#the-key-value-filter)
  - [The key-value first filter](#the-key-value-first-filter)
  - [Conjunction vs disjunction](#conjunction-vs-disjunction)
  - [Grouping with parentheses](#grouping-with-parentheses)
  - [Filter negation and not-equals](#filter-negation-and-not-equals)
  - [Boolean and None filter values](#boolean-and-none-filter-values)
  - [Value guard](#value-guard)
  - [Dotted filter keys](#dotted-filter-keys)
  - [Slice notation in filter keys](#slice-notation-in-filter-keys)
- [Transforms](#transforms)
  - [Built-in Transforms](#built-in-transforms)
  - [Custom Transforms](#custom-transforms)
- [Constants and Exceptions](#constants-and-exceptions)
- [FAQ](#faq)
  - [Why do I get a tuple for my get?](#why-do-i-get-a-tuple-for-my-get)
  - [How do I craft an efficient path?](#how-do-i-craft-an-efficient-path)
  - [Why do I get a RuntimeError when updating with a slice filter?](#why-do-i-get-a-runtimeerror-when-updating-with-a-slice-filter)

<a id="safe-traversal-optional-chaining"></a>
## Safe Traversal (Optional Chaining)

Like JavaScript's optional chaining operator (`?.`), dotted safely handles missing paths.
If any part of the path doesn't exist, `get` returns `None` (or a specified default)
instead of raising an exception:

    >>> import dotted
    >>> d = {'a': {'b': 1}}
    >>> dotted.get(d, 'a.b.c.d.e')  # path doesn't exist
    None
    >>> dotted.get(d, 'a.b.c.d.e', 'default')  # with default
    'default'
    >>> dotted.get(d, 'x.y.z', 42)  # missing from the start
    42

This makes dotted ideal for safely navigating deeply nested or uncertain data structures
without defensive coding or try/except blocks.

<a id="why-dotted"></a>
## Why Dotted?

Several Python libraries handle nested data access. Here's how dotted compares:

| Feature | dotted | glom | jmespath | pydash |
|---------|--------|------|----------|--------|
| Safe traversal (no exceptions) | ✅ | ✅ | ✅ | ✅ |
| Familiar dot notation | ✅ | ❌ (custom spec) | ❌ (JSON syntax) | ✅ |
| Pattern matching (wildcards) | ✅ | ❌ | ✅ | ❌ |
| Regex patterns | ✅ | ❌ | ❌ | ❌ |
| In-place mutation | ✅ | ✅ | ❌ (read-only) | ✅ |
| Attribute access (`@attr`) | ✅ | ✅ | ❌ | ❌ |
| Transforms/coercion | ✅ | ✅ | ❌ | ✅ |
| Slicing | ✅ | ❌ | ✅ | ❌ |
| Filters | ✅ | ❌ | ✅ | ❌ |
| AND/OR/NOT filters | ✅ | ❌ | ✅ | ❌ |
| Path grouping `(a,b)` | ✅ | ❌ | ❌ | ❌ |
| Operation grouping `(.a,.b)` | ✅ | ❌ | ❌ | ❌ |
| NOP (~) match but don't update | ✅ | ❌ | ❌ | ❌ |
| Cut (#) short-circuit disjunction | ✅ | ❌ | ❌ | ❌ |
| Zero dependencies | ❌ (pyparsing) | ❌ | ✅ | ❌ |

**Choose dotted if you want:**
- Intuitive `a.b[0].c` syntax that looks like Python
- Pattern matching with wildcards (`*`) and regex (`/pattern/`)
- Both read and write operations on nested structures
- Transforms to coerce types inline (`path|int`, `path|str:fmt`)
- Path grouping `(a,b).c` and operation grouping `prefix(.a,.b)` for multi-access
- **Cut (`#`) in disjunction**—first matching branch wins; e.g. `(a#, b)` or `emails[(*&email="x"#, +)]` for "update if exists, else append"
- NOP (`~`) to match without updating—e.g. `(name.~first#, name.first)` for conditional updates

<a id="breaking-changes"></a>
## Breaking Changes

### v0.28.0
- **`[*=value]` on primitive lists no longer works** — use `[*]=value` (value guard) instead.
  `[*=value]` is a SliceFilter that tests *keys* of dict-like items; primitives have no keys,
  so it now correctly returns `[]`.
- **`[!*=value]` on primitive lists no longer works** — use `[*]!=value` instead.
- **`*&*=value` no longer matches primitives** — use `*=value` (value guard) instead.
- Existing `[*=value]` on dicts/objects is unchanged.
- Existing `&` filter behavior on dict-like nodes is unchanged.

### v0.13.0
- **Filter conjunction operator changed from `.` to `&`**: The conjunction operator for
  chaining multiple filters has changed. Previously, `*.id=1.name="alice"` was used for
  conjunctive (AND) filtering. Now use `*&id=1&name="alice"`. This change enables support
  for dotted paths within filter keys (e.g., `items[user.id=1]` to filter on nested fields).

<a id="api"></a>
## API

Probably the easiest thing to do is pydoc the api layer.

    $ pydoc dotted.api

Parsed dotted paths are LRU-cached (after the first parse of a given path string), so repeated use of the same path string is cheap.

<a id="get"></a>
### Get

See the Paths, Patterns, and Operators sections below for the full notation.

    >>> import dotted
    >>> dotted.get({'a': {'b': {'c': {'d': 'nested'}}}}, 'a.b.c.d')
    'nested'

<a id="update"></a>
### Update

Update will mutate the object if it can.  It always returns the changed object though. If
it's not mutable, then get via the return.

    >>> import dotted
    >>> l = []
    >>> t = ()
    >>> dotted.update(l, '[0]', 'hello')
    ['hello']
    >>> l
    ['hello']
    >>> dotted.update(t, '[0]', 'hello')
    ('hello',)
    >>> t
    ()

#### Update via pattern

You can update all fields that match pattern given by either a wildcard OR regex.

    >>> import dotted
    >>> d = {'a': 'hello', 'b': 'bye'}
    >>> dotted.update(d, '*', 'me')
    {'a': 'me', 'b': 'me'}

#### Immutable updates

Use `mutable=False` to prevent mutation of the original object:

    >>> import dotted
    >>> data = {'a': 1, 'b': 2}
    >>> result = dotted.update(data, 'a', 99, mutable=False)
    >>> data
    {'a': 1, 'b': 2}
    >>> result
    {'a': 99, 'b': 2}

This works for `remove` as well:

    >>> data = {'a': 1, 'b': 2}
    >>> result = dotted.remove(data, 'a', mutable=False)
    >>> data
    {'a': 1, 'b': 2}
    >>> result
    {'b': 2}

When `mutable=False` is specified and the root object is mutable, `copy.deepcopy()`
is called first. This ensures no mutation occurs even when updating through nested
immutable containers (e.g., a tuple inside a dict).

#### Update if

`update_if` updates only when the path is missing or when `pred(current_value)` is true.
It always updates when there is nothing at the key; the predicate only gates updates
when the path exists. Default pred is `lambda val: val is None` (fill missing or None
slots, don't overwrite existing non-None). Use `pred=None` for unconditional update
(same as `update`):

    >>> import dotted
    >>> dotted.update_if({'name': {}}, 'name.first', 'hello')
    {'name': {'first': 'hello'}}
    >>> dotted.update_if({'name': {'first': 'Alice'}}, 'name.first', 'hello')  # no change
    {'name': {'first': 'Alice'}}
    >>> dotted.update_if({'name': {'first': None}}, 'name.first', 'hello')
    {'name': {'first': 'hello'}}

The same behavior can be achieved with path expressions using the NOP operator (see below).
Use `update_if_multi` for batch updates with per-item `(key, val)` or `(key, val, pred)`.

#### Update with NOP (~)

The NOP operator `~` means "match but don't update." Use it when some matches should
be left unchanged. Combine with cut (`#`) for conditional updates:

    >>> import dotted
    >>> data = {'name': {'first': 'hello'}}
    >>> dotted.update(data, '(name.~first#, name.first)', 'world')  # first exists, NOP + cut
    {'name': {'first': 'hello'}}
    >>> data = {'name': {}}
    >>> dotted.update(data, '(name.~first#, name.first)', 'world')  # first missing, update
    {'name': {'first': 'world'}}

<a id="remove"></a>
### Remove

You can remove a field or do so only if it matches value.  For example,

    >>> import dotted
    >>> d = {'a': 'hello', 'b': 'bye'}
    >>> dotted.remove(d, 'b')
    {'a': 'hello'}
    >>> dotted.remove(d, 'a', 'bye')
    {'a': 'hello'}

#### Remove via pattern

Similar to update, all patterns that match will be removed.  If you provide a value as
well, only the matched patterns that also match the value will be removed.

#### Remove if

`remove_if` removes only when the path is missing or when `pred(current_value)` is true.
Default pred is `lambda val: val is None` (remove only when value is missing or None).
Use `pred=None` for unconditional remove (same as `remove`):

    >>> import dotted
    >>> dotted.remove_if({'a': 1, 'b': None, 'c': 2}, 'b')
    {'a': 1, 'c': 2}
    >>> dotted.remove_if({'a': 1, 'b': 2, 'c': 3}, 'b')  # no change
    {'a': 1, 'b': 2, 'c': 3}

Use `remove_if_multi` for batch removal with per-item pred or `(key, val, pred)`.

<a id="match"></a>
### Match

Use to match a dotted-style pattern to a field.  Partial matching is on by default.  You
can match via wildcard OR via regex.  Here's a regex example:

    >>> import dotted
    >>> dotted.match('/a.+/', 'abced.b')
    'abced.b'
    >>> dotted.match('/a.+/', 'abced.b', partial=False)

With the `groups=True` parameter, you'll see how it was matched:

    >>> import dotted
    >>> dotted.match('hello.*', 'hello.there.bye', groups=True)
    ('hello.there.bye', ('hello', 'there.bye'))

In the above example, `hello` matched to `hello` and `*` matched to `there.bye` (partial
matching is enabled by default).

<a id="expand"></a>
### Expand

You may wish to _expand_ all fields that match a pattern in an object.

    >>> import dotted
    >>> d = {'hello': {'there': [1, 2, 3]}, 'bye': 7}
    >>> dotted.expand(d, '*')
    ('hello', 'bye')
    >>> dotted.expand(d, '*.*')
    ('hello.there',)
    >>> dotted.expand(d, '*.*[*]')
    ('hello.there[0]', 'hello.there[1]', 'hello.there[2]')
    >>> dotted.expand(d, '*.*[1:]')
    ('hello.there[1:]',)

<a id="has"></a>
### Has

Check if a key or pattern exists in an object.

    >>> import dotted
    >>> d = {'a': {'b': 1}}
    >>> dotted.has(d, 'a.b')
    True
    >>> dotted.has(d, 'a.c')
    False
    >>> dotted.has(d, 'a.*')
    True

<a id="mutable"></a>
### Mutable

Check if `update(obj, key, val)` would mutate `obj` in place. Returns `False` for
empty paths (root replacement) or when the object or any container in the path
is immutable.

    >>> import dotted
    >>> dotted.mutable({'a': 1}, 'a')
    True
    >>> dotted.mutable({'a': 1}, '')           # empty path
    False
    >>> dotted.mutable((1, 2), '[0]')          # tuple is immutable
    False
    >>> dotted.mutable({'a': (1, 2)}, 'a[0]')  # nested tuple
    False

This is useful when you need to know whether to use the return value:

    >>> data = {'a': 1}
    >>> if dotted.mutable(data, 'a'):
    ...     dotted.update(data, 'a', 2)  # mutates in place
    ... else:
    ...     data = dotted.update(data, 'a', 2)  # use return value

<a id="setdefault"></a>
### Setdefault

Set a value only if the key doesn't already exist. Creates nested structures as needed.

    >>> import dotted
    >>> d = {'a': 1}
    >>> dotted.setdefault(d, 'a', 999)  # key exists, no change; returns value
    1
    >>> dotted.setdefault(d, 'b', 2)    # key missing, sets value; returns it
    2
    >>> dotted.setdefault({}, 'a.b.c', 7)  # creates nested structure; returns value
    7

<a id="pluck"></a>
### Pluck

Extract (key, value) pairs from an object matching a pattern.

    >>> import dotted
    >>> d = {'a': 1, 'b': 2, 'nested': {'x': 10}}
    >>> dotted.pluck(d, 'a')
    ('a', 1)
    >>> dotted.pluck(d, '*')
    (('a', 1), ('b', 2), ('nested', {'x': 10}))
    >>> dotted.pluck(d, 'nested.*')
    (('nested.x', 10),)

<a id="build"></a>
### Build

Create a default nested structure for a dotted key.

    >>> import dotted
    >>> dotted.build({}, 'a.b.c')
    {'a': {'b': {'c': None}}}
    >>> dotted.build({}, 'items[]')
    {'items': []}
    >>> dotted.build({}, 'items[0]')
    {'items': [None]}

<a id="apply"></a>
### Apply

Apply transforms to values in an object in-place.

    >>> import dotted
    >>> d = {'price': '99.99', 'quantity': '5'}
    >>> dotted.apply(d, 'price|float')
    {'price': 99.99, 'quantity': '5'}
    >>> dotted.apply(d, '*|int')
    {'price': 99, 'quantity': 5}

<a id="assemble"></a>
### Assemble

Build a dotted notation string from a list of keys.

    >>> import dotted
    >>> dotted.assemble(['a', 'b', 'c'])
    'a.b.c'
    >>> dotted.assemble(['items', '[0]', 'name'])
    'items[0].name'
    >>> dotted.assemble([7, 'hello'])
    '7.hello'

<a id="quote"></a>
### Quote

Properly quote a key for use in dotted notation.

    >>> import dotted
    >>> dotted.quote('hello')
    'hello'
    >>> dotted.quote('has.dot')
    '"has.dot"'
    >>> dotted.quote(7.5)
    "#'7.5'"

<a id="multi-operations"></a>
### Multi Operations

Most operations have `*_multi` variants for batch processing:

**Note:** `get_multi` returns a generator (not a list or tuple). That distinguishes it from a pattern `get`, which returns a tuple of matches. It also keeps input and output in the same style when you pass an iterator or generator of paths—lazy in, lazy out.

    >>> import dotted
    >>> d = {'a': 1, 'b': 2, 'c': 3}
    >>> list(dotted.get_multi(d, ['a', 'b']))
    [1, 2]
    >>> dotted.update_multi({}, [('a.b', 1), ('c.d', 2)])
    {'a': {'b': 1}, 'c': {'d': 2}}
    >>> dotted.remove_multi(d, ['a', 'c'])
    {'b': 2}
    >>> d = {'a': 1}; list(dotted.setdefault_multi(d, [('a', 999), ('b', 2)]))
    [1, 2]
    >>> d
    {'a': 1, 'b': 2}
    >>> dotted.update_if_multi({'a': 1}, [('a', 99, lambda v: v == 1), ('b', 2)])  # (key, val) or (key, val, pred)
    {'a': 99, 'b': 2}
    >>> dotted.remove_if_multi({'a': 1, 'b': None, 'c': 2}, ['b'])  # keys_only=True, default pred
    {'a': 1, 'c': 2}

Available multi operations: `get_multi`, `update_multi`, `update_if_multi`, `remove_multi`,
`remove_if_multi`, `setdefault_multi`, `match_multi`, `expand_multi`, `apply_multi`,
`build_multi`, `pluck_multi`, `assemble_multi`.

<a id="paths"></a>
## Paths

Dotted notation shares similarities with python. A _dot_ `.` field expects to see a
dictionary-like object (using `keys` and `__getitem__` internally).  A _bracket_ `[]`
field is biased towards sequences (like lists or strs) but can also act on dicts.  A
_attr_ `@` field uses `getattr/setattr/delattr`.

<a id="key-fields"></a>
### Key fields

A key field is expressed as `a` or part of a dotted expression, such as `a.b`.  The
grammar parser is permissive for what can be in a key field.  Pretty much any non-reserved
char will match.  Note that key fields will only work on objects that have a `keys`
method.  Basically, they work with dictionary or dictionary-like objects.

    >>> import dotted
    >>> dotted.get({'a': {'b': 'hello'}}, 'a.b')
    'hello'

If the key field starts with a space or `-`, you should either quote it OR you may use
a `\` as the first char.

<a id="bracketed-fields"></a>
### Bracketed fields

You may also use bracket notation, such as `a[0]` which does a `__getitem__` at key 0.
The parser prefers numeric types over string types (if you wish to look up a non-numeric
field using brackets be sure to quote it).  Bracketed fields will work with pretty much
any object that can be looked up via `__getitem__`.

    >>> import dotted
    >>> dotted.get({'a': ['first', 'second', 'third']}, 'a[0]')
    'first'
    >>> dotted.get({'a': {'b': 'hello'}}, 'a["b"]')
    'hello'

<a id="attr-fields"></a>
### Attr fields

An attr field is expressed by prefixing with `@`. This will fetch data at that attribute.
You may wonder why have this when you can just as easily use standard python to access.
Two important reasons: nested expressions and patterns.

    >>> import dotted, types
    >>> ns = types.SimpleNamespace()
    >>> ns.hello = {'me': 'goodbye'}
    >>> dotted.get(ns, '@hello.me')
    'goodbye'

<a id="slicing"></a>
### Slicing

Dotted slicing works like python slicing and all that entails.

    >>> import dotted
    >>> d = {'hi': {'there': [1, 2, 3]}, 'bye': {'there': [4, 5, 6]}}
    >>> dotted.get(d, 'hi.there[::2]')
    [1, 3]
    >>> dotted.get(d, '*.there[1:]')
    ([2, 3], [5, 6])

<a id="dot-notation-for-sequence-indexing"></a>
### Dot notation for sequence indexing

Numeric keys work as indices when accessing sequences (lists, tuples, strings):

    >>> import dotted
    >>> data = {'items': [10, 20, 30]}
    >>> dotted.get(data, 'items.0')
    10
    >>> dotted.get(data, 'items.-1')  # negative index
    30

This is equivalent to bracket notation for existing sequences:

    >>> dotted.get(data, 'items[0]')  # same result
    10

Chaining works naturally:

    >>> data = {'users': [{'name': 'alice'}, {'name': 'bob'}]}
    >>> dotted.get(data, 'users.0.name')
    'alice'

Updates and removes also work:

    >>> dotted.update(data, 'users.0.name', 'ALICE')
    >>> dotted.get(data, 'users.0.name')
    'ALICE'

**Note**: When _creating_ structures, use bracket notation for lists:

    >>> dotted.build({}, 'items.0')    # creates dict: {'items': {0: None}}
    >>> dotted.build({}, 'items[0]')   # creates list: {'items': [None]}

<a id="empty-path-root-access"></a>
### Empty path (root access)

An empty string `''` refers to the root of the data structure itself:

    >>> import dotted
    >>> data = {'a': 1, 'b': 2}
    >>> dotted.get(data, '')
    {'a': 1, 'b': 2}

Unlike normal paths which mutate in place, `update` with an empty path is non-mutating
since Python cannot rebind the caller's variable:

    >>> data = {'a': 1, 'b': 2}
    >>> result = dotted.update(data, '', {'replaced': True})
    >>> result
    {'replaced': True}
    >>> data
    {'a': 1, 'b': 2}

Compare with a normal path which mutates:

    >>> data = {'a': 1, 'b': 2}
    >>> dotted.update(data, 'a', 99)
    {'a': 99, 'b': 2}
    >>> data
    {'a': 99, 'b': 2}

Other empty path operations:

    >>> data = {'a': 1, 'b': 2}
    >>> dotted.remove(data, '')
    None
    >>> dotted.expand(data, '')
    ('',)
    >>> dotted.pluck(data, '')
    ('', {'a': 1, 'b': 2})

<a id="typing--quoting"></a>
## Typing & Quoting

<a id="numeric-types"></a>
### Numeric types

The parser will attempt to interpret a field numerically if it can, such as `field.1`
will interpret the `1` part numerically.

    >>> import dotted
    >>> dotted.get({'7': 'me', 7: 'you'}, '7')
    'you'

<a id="quoting"></a>
### Quoting

Sometimes you need to quote a field which you can do by just putting the field in quotes.

    >>> import dotted
    >>> dotted.get({'has . in it': 7}, '"has . in it"')
    7

<a id="the-numericize--operator"></a>
### The numericize `#` operator

Non-integer numeric fields may be interpreted incorrectly if they have decimal point. To
solve, use the numerize operator `#` at the front of a quoted field, such as `#'123.45'`.
This will coerce to a numeric type (e.g. float).

    >>> import dotted
    >>> d = {'a': {1.2: 'hello', 1: {2: 'fooled you'}}}
    >>> dotted.get(d, 'a.1.2')
    'fooled you'
    >>> dotted.get(d, 'a.#"1.2"')
    'hello'

<a id="patterns"></a>
## Patterns

You may use dotted for pattern matching. You can match to wildcards or regular
expressions.  You'll note that patterns always return a tuple of matches.

    >>> import dotted
    >>> d = {'hi': {'there': [1, 2, 3]}, 'bye': {'there': [4, 5, 6]}}
    >>> dotted.get(d, '*.there[2]')
    (3, 6)
    >>> dotted.get(d, '/h.*/.*')
    ([1, 2, 3],)

Dotted will return all values that match the pattern(s).

<a id="wildcards"></a>
### Wildcards

The wildcard pattern is `*`.  It will match anything.

<a id="regular-expressions"></a>
### Regular expressions

The regex pattern is enclosed in slashes: `/regex/`. Note that if the field is a non-str,
the regex pattern will internally match to its str representation.

<a id="the-match-first-operator"></a>
### The match-first operator

You can also postfix any pattern with a `?`.  This will return only
the first match.

    >>> import dotted
    >>> d = {'hi': {'there': [1, 2, 3]}, 'bye': {'there': [4, 5, 6]}}
    >>> dotted.get(d, '*?.there[2]')
    (3,)

<a id="slicing-vs-patterns"></a>
### Slicing vs Patterns

Slicing a sequence produces a sequence and a filter on a sequence is a special
type of slice operation. Whereas, patterns _iterate_ through items:

    >>> import dotted
    >>> data = [{'name': 'alice'}, {'name': 'bob'}, {'name': 'alice'}]
    >>> dotted.get(data, '[1:3]')
    [{'name': 'bob'}, {'name': 'alice'}]
    >>> dotted.get(data, '[name="alice"]')
    [{'name': 'alice'}, {'name': 'alice'}]
    >>> dotted.get(data, '[*]')
    ({'name': 'alice'}, {'name': 'bob'}, {'name': 'alice'})

Chaining after a slice accesses the result itself, not the items within it:

    >>> dotted.get(data, '[1:3].name')           # accessing .name on the list
    None
    >>> dotted.get(data, '[name="alice"].name')  # also accessing .name on the list
    None
    >>> dotted.get(data, '[].name')              # .name on a raw list
    None

To chain through the items, use a pattern instead:

    >>> dotted.get(data, '[*].name')
    ('alice', 'bob', 'alice')
    >>> dotted.get(data, '[*&name="alice"]')
    ({'name': 'alice'}, {'name': 'alice'})

<a id="recursive-traversal"></a>
## Recursive Traversal

The recursive operator `*` traverses nested data structures by following keys
that match a pattern at successive levels.

<a id="the-recursive-operator-"></a>
### The recursive operator `*`

`*pattern` recurses into values whose keys match the pattern. It follows chains
of matching keys — at each level, if a key matches, its value is yielded and
the traversal continues into that value:

    >>> import dotted
    >>> d = {'b': {'b': {'c': 1}}}
    >>> dotted.get(d, '*b')
    ({'b': {'c': 1}}, {'c': 1})
    >>> dotted.get(d, '*b.c')
    (1,)

The chain stops when the key no longer matches:

    >>> d = {'a': {'b': {'c': 1}}}
    >>> dotted.get(d, '*b')
    ()

The inner pattern can be any key pattern — a literal key, a wildcard, or a regex:

    >>> d = {'x1': {'x2': 1}, 'y': 2}
    >>> dotted.get(d, '*/x.*/')
    ({'x2': 1}, 1)

<a id="recursive-wildcard-"></a>
### Recursive wildcard `**`

`**` is shorthand for `*` with a wildcard inner — it matches all keys and visits
every value at every depth:

    >>> d = {'a': {'b': {'c': 1}}, 'x': {'y': 2}}
    >>> dotted.get(d, '**')
    ({'b': {'c': 1}}, {'c': 1}, 1, {'y': 2}, 2)

Use `**` with continuation to find a key at any depth:

    >>> dotted.get(d, '**.c')
    (1,)

Use `**?` to get only the first match:

    >>> dotted.get(d, '**?')
    ({'b': {'c': 1}},)

<a id="depth-slicing"></a>
### Depth slicing

Control which depths are visited using slice notation. Depth 0 is the values of
the first-level keys. Lists increment depth (their elements are one level deeper).

    >>> d = {'a': {'x': 1}, 'b': {'y': {'z': 2}}}
    >>> dotted.get(d, '**:0')
    ({'x': 1}, {'y': {'z': 2}})
    >>> dotted.get(d, '**:1')
    (1, {'z': 2})

Use negative indices to count from the leaf. `**:-1` returns leaves only,
`**:-2` returns the penultimate level:

    >>> dotted.get(d, '**:-1')
    (1, 2)

Range slicing works like Python slices: `**:start:stop` and `**:::step`:

    >>> dotted.get(d, '**:0:1')
    ({'x': 1}, 1, {'y': {'z': 2}}, {'z': 2})

<a id="recursive-with-value-guard"></a>
### Recursive with value guard

Combine `**` with value guards to find specific values at any depth:

    >>> d = {'a': {'b': 7, 'c': 3}, 'd': {'e': 7}}
    >>> dotted.get(d, '**=7')
    (7, 7)
    >>> dotted.get(d, '**!=7')
    ({'b': 7, 'c': 3}, 3, {'e': 7})

<a id="recursive-update-and-remove"></a>
### Recursive update and remove

Recursive operators work with `update` and `remove`:

    >>> d = {'a': {'b': 7, 'c': 3}, 'd': 7}
    >>> dotted.update(d, '**=7', 99)
    {'a': {'b': 99, 'c': 3}, 'd': 99}

    >>> d = {'a': {'b': 7, 'c': 3}, 'd': 7}
    >>> dotted.remove(d, '**=7')
    {'a': {'c': 3}}

<a id="recursive-match"></a>
### Recursive match

Recursive patterns work with `match`. `**` matches any key path, `*key` matches
chains of a specific key:

    >>> dotted.match('**.c', 'a.b.c')
    'a.b.c'
    >>> dotted.match('*b', 'b.b.b')
    'b.b.b'
    >>> dotted.match('*b', 'a.b.c') is None
    True

<a id="grouping"></a>
## Grouping

<a id="operation-grouping"></a>
### Operation grouping

Use parentheses to group **operation sequences** that diverge from a common point.
Each branch is a full operation chain including dots, brackets, and attrs:

    >>> import dotted

    # Mix different operation types from a common prefix
    >>> d = {'items': [10, 20, 30]}
    >>> dotted.get(d, 'items(.0,[])')
    (10, [10, 20, 30])

    # Nested paths in branches
    >>> d = {'x': {'a': {'i': 1}, 'b': {'k': 3}}}
    >>> dotted.get(d, 'x(.a.i,.b.k)')
    (1, 3)

Operation groups support these operators:

| Syntax | Meaning | Behavior |
|--------|---------|----------|
| `(.a,.b)` | Disjunction (OR) | Returns all values that exist |
| `(.a&.b)` | Conjunction (AND) | Returns values only if ALL branches exist |
| `(!.a)` | Negation (NOT) | Returns values for keys NOT matching |

#### Disjunction (OR)

Comma separates branches. Returns all matches that exist. Disjunction doesn't
short-circuit—when updating, all matching branches get the update. Using the
match-first operator (`?`) is probably what you want when updating.

    >>> d = {'a': {'x': 1, 'y': 2}}
    >>> dotted.get(d, 'a(.x,.y)')
    (1, 2)
    >>> dotted.get(d, 'a(.x,.z)')     # z missing, x still returned
    (1,)

Updates apply to all matching branches. When nothing matches, the first
concrete path (scanning last to first) is created:

    >>> d = {'a': {'x': 1, 'y': 2}}
    >>> dotted.update(d, 'a(.x,.y)', 99)
    {'a': {'x': 99, 'y': 99}}
    >>> dotted.update({'a': {}}, 'a(.x,.y)', 99)   # nothing matches → creates last (.y)
    {'a': {'y': 99}}

#### Cut (`#`) in disjunction

Suffix a branch with `#` so that if it matches, only that branch is used
(get/update/remove); later branches are not tried. Useful for "update if exists,
else append" in lists. Example with slot grouping:

    >>> data = {'emails': [{'email': 'alice@x.com', 'verified': False}]}
    >>> dotted.update(data, 'emails[(*&email="alice@x.com"#, +)]', {'email': 'alice@x.com', 'verified': True})
    {'emails': [{'email': 'alice@x.com', 'verified': True}]}
    >>> data = {'emails': [{'email': 'other@x.com'}]}
    >>> dotted.update(data, 'emails[(*&email="alice@x.com"#, +)]', {'email': 'alice@x.com', 'verified': True})
    {'emails': [{'email': 'other@x.com'}, {'email': 'alice@x.com', 'verified': True}]}

First branch matches items where `email="alice@x.com"` and updates them (then cut);
if none match, the `+` branch appends the new dict.

#### Conjunction (AND)

Use `&` for all-or-nothing behavior. Returns values only if ALL branches exist:

    >>> d = {'a': {'x': 1, 'y': 2}}
    >>> dotted.get(d, 'a(.x&.y)')
    (1, 2)
    >>> dotted.get(d, 'a(.x&.z)')     # z missing, fails entirely
    ()

Updates all branches so the conjunction eval as true—creates missing paths.
If a filter or NOP prevents a branch, no update:

    >>> dotted.update({'a': {'x': 1, 'y': 2}}, 'a(.x&.y)', 99)
    {'a': {'x': 99, 'y': 99}}
    >>> dotted.update({'a': {'x': 1}}, 'a(.x&.y)', 99)    # y missing → creates it
    {'a': {'x': 99, 'y': 99}}

#### First match

Use `?` suffix to return only the first match. When nothing matches, same
fallback as disjunction—first concrete path (last to first):

    >>> d = {'a': {'x': 1, 'y': 2}}
    >>> dotted.get(d, 'a(.z,.x,.y)?')    # first that exists
    (1,)
    >>> dotted.update({'a': {}}, 'a(.x,.y)?', 99)    # nothing matches → creates last (.y)
    {'a': {'y': 99}}

#### Negation (NOT)

Use `!` prefix to exclude keys matching a pattern:

    >>> import dotted

    # Exclude single key - get user fields except password
    >>> user = {'email': 'a@x.com', 'name': 'alice', 'password': 'secret'}
    >>> sorted(dotted.get({'user': user}, 'user(!.password)'))
    ['a@x.com', 'alice']

    # Works with lists too
    >>> dotted.get({'items': [10, 20, 30]}, 'items(![0])')
    (20, 30)

Updates and removes apply to all non-matching keys:

    >>> d = {'a': {'x': 1, 'y': 2, 'z': 3}}
    >>> dotted.update(d, 'a(!.x)', 99)
    {'a': {'x': 1, 'y': 99, 'z': 99}}
    >>> dotted.remove(d, 'a(!.x)')
    {'a': {'x': 1}}

**Note**: For De Morgan's law with filter expressions, see the Filters section below.

<a id="path-grouping"></a>
### Path grouping

Path grouping is syntactic sugar for operation grouping where each branch is a
single key. `(a,b).c` is equivalent to `(.a,.b).c`.

    >>> import dotted
    >>> d = {'a': 1, 'b': 2, 'c': 3}

    # Group keys
    >>> dotted.get(d, '(a,b)')
    (1, 2)

    # With a shared suffix
    >>> d = {'x': {'val': 1}, 'y': {'val': 2}}
    >>> dotted.get(d, '(x,y).val')
    (1, 2)

Path groups support the same operators as operation groups:

| Syntax | Meaning | Behavior |
|--------|---------|----------|
| `(a,b)` | Disjunction (OR) | Returns all values that exist |
| `(a#, b)` | Disjunction with **cut** | First branch that matches wins; later branches not tried |
| `(a&b)` | Conjunction (AND) | Returns values only if ALL keys exist |
| `(!a)` | Negation (NOT) | Returns values for keys NOT matching |

    >>> d = {'a': 1, 'b': 2, 'c': 3}
    >>> dotted.get(d, '(a,b)')      # OR: both
    (1, 2)
    >>> dotted.get(d, '(a&b)')      # AND: both must exist
    (1, 2)
    >>> dotted.get(d, '(a&x)')      # AND: x missing, fails
    ()
    >>> sorted(dotted.get(d, '(!a)'))  # NOT: all except a
    [2, 3]

Use `?` suffix for first-match:

    >>> dotted.get(d, '(x,a,b)?')   # first that exists
    (1,)

#### Cut (`#`) in disjunction

Suffix a branch with `#` to commit to it—if that branch matches, its results are
returned and later branches are not tried. If it doesn't match, the next branch
is tried. Example: ``(a#, b)`` returns ``(1,)`` when ``a`` exists; when ``a`` is
missing, it tries ``b`` and returns ``(2,)``.

    >>> dotted.get({'a': 1, 'b': 2}, '(a#, b)')
    (1,)
    >>> dotted.get({'b': 2}, '(a#, b)')
    (2,)

<a id="operators"></a>
## Operators

<a id="the-append--operator"></a>
### The append `+` operator

Both bracketed fields and slices support the '+' operator which refers to the end of
sequence. You may append an item or slice to the end a sequence.

    >>> import dotted
    >>> d = {'hi': {'there': [1, 2, 3]}, 'bye': {'there': [4, 5, 6]}}
    >>> dotted.update(d, '*.there[+]', 8)
    {'hi': {'there': [1, 2, 3, 8]}, 'bye': {'there': [4, 5, 6, 8]}}
    >>> dotted.update(d, '*.there[+:]', [999])
    {'hi': {'there': [1, 2, 3, 8, 999]}, 'bye': {'there': [4, 5, 6, 8, 999]}}

<a id="the-append-unique--operator"></a>
### The append-unique `+?` operator

If you want to update only _unique_ items to a list, you can use the `?`
postfix.  This will ensure that it's only added once (see match-first below).

    >>> import dotted
    >>> items = [1, 2]
    >>> dotted.update(items, '[+?]', 3)
    [1, 2, 3]
    >>> dotted.update(items, '[+?]', 3)
    [1, 2, 3]

<a id="the-invert---operator"></a>
### The invert `-` operator

You can invert the meaning of the notation by prefixing a `-`.  For example,
to remove an item using `update`:

    >>> import dotted
    >>> d = {'a': 'hello', 'b': 'bye'}
    >>> dotted.update(d, '-b', dotted.ANY)
    {'a': 'hello'}
    >>> dotted.remove(d, '-b', 'bye again')
    {'a': 'hello', 'b': 'bye again'}

<a id="the-nop--operator"></a>
### The NOP `~` operator

The NOP operator means "match but don't update." At `update` and `remove` time, paths
marked with `~` are matched for traversal but the mutation is skipped at that segment.
NOP applies only to the segment it wraps; child segments are unaffected.

| Syntax | Meaning |
|--------|---------|
| `~a.b` | NOP at `a`, then `.b` |
| `a.~b` | NOP at `b` (dot segment) |
| `~(name.first)` | NOP on grouped path |
| `[~*]` or `~[*]` | NOP on slot (canonical: `[~stuff]`) |
| `@~attr` or `~@attr` | NOP on attr |

    >>> data = {'a': {'b': 1}}
    >>> dotted.update(data, '~a.b', 2)   # NOP at a, update .b
    {'a': {'b': 2}}
    >>> dotted.update(data, 'a.~b', 2)   # NOP at b, no change
    {'a': {'b': 1}}

Combine NOP with cut (`#`) for "update only if missing" semantics—if the NOP
branch matches, cut commits to it and skips the remaining branches:

    >>> dotted.update({'name': {'first': 'alice'}}, '(name.~first#, name.first)', 'bob')
    {'name': {'first': 'alice'}}   # first existed, NOP branch matched and cut
    >>> dotted.update({'name': {}}, '(name.~first#, name.first)', 'bob')
    {'name': {'first': 'bob'}}     # first missing, fell through to update branch

<a id="the-cut--operator"></a>
### The cut `#` operator

Think of cut as an OR/disjunction short-circuit. Suffix a branch with `#`
so that if it matches, only that branch is used and later branches are not tried.
If it doesn't match, evaluation falls through to the next branch.

    >>> import dotted
    >>> dotted.get({'a': 1, 'b': 2}, '(a#, b)')
    (1,)
    >>> dotted.get({'b': 2}, '(a#, b)')
    (2,)

This is especially useful for "update if exists, else create" patterns:

    >>> data = {'emails': [{'email': 'alice@x.com', 'verified': False}]}
    >>> dotted.update(data, 'emails[(*&email="alice@x.com"#, +)]', {'email': 'alice@x.com', 'verified': True})
    {'emails': [{'email': 'alice@x.com', 'verified': True}]}
    >>> data = {'emails': [{'email': 'other@x.com'}]}
    >>> dotted.update(data, 'emails[(*&email="alice@x.com"#, +)]', {'email': 'alice@x.com', 'verified': True})
    {'emails': [{'email': 'other@x.com'}, {'email': 'alice@x.com', 'verified': True}]}

<a id="the-numericize--operator-1"></a>
### The numericize `#` operator

The `#` prefix is also used to coerce a quoted field to a numeric type (e.g. float).
This is needed when a non-integer numeric key contains a decimal point that would
otherwise be parsed as a path separator. See [Typing & Quoting](#the-numericize--operator)
for full details.

    >>> import dotted
    >>> d = {'a': {1.2: 'hello', 1: {2: 'fooled you'}}}
    >>> dotted.get(d, 'a.1.2')
    'fooled you'
    >>> dotted.get(d, 'a.#"1.2"')
    'hello'

<a id="filters"></a>
## Filters

<a id="the-key-value-filter"></a>
### The key-value filter

You may filter by key-value to narrow your result set. Use `key=value` for equality and
`key!=value` for not-equals (syntactic sugar for `!(key=value)`). Filter keys can be
dotted paths and may include slice notation (e.g. `name[:5]="hello"`, `file[-3:]=".py"`).
You may use with __key__ or __bracketed__ fields. Key-value fields may be disjunctively (OR)
specified via the `,` delimiter.

A key-value field on __key__ field looks like: `keyfield&key1=value1,key2=value2...`.
This will return all key-value matches on a subordinate dict-like object.  For example,

    >>> d = {
    ...    'a': {
    ...         'id': 1,
    ...         'hello': 'there',
    ...     },
    ...     'b': {
    ...         'id': 2,
    ...         'hello': 'there',
    ...     },
    ... }
    >>> dotted.get(d, '*&id=1')
    ({'id': 1, 'hello': 'there'},)
    >>> dotted.get(d, '*&id=*')
    ({'id': 1, 'hello': 'there'}, {'id': 2, 'hello': 'there'})

A key-value field on a __bracketed__ field looks like: `[key1=value1,key2=value2...]`.
This will return all items in a list that match key-value filter.  For example,

    >>> d = {
    ...     'a': [{'id': 1, 'hello': 'there'}, {'id': 2, 'hello': 'there'}],
    ...     'b': [{'id': 3, 'hello': 'there'}, {'id': 4, 'hello': 'bye'}],
    ... }
    >>> dotted.get(d, 'a[hello="there"][*].id')
    (1, 2)
    >>> dotted.get(d, '*[hello="there"][*].id')
    (1, 2, 3)

<a id="the-key-value-first-filter"></a>
### The key-value first filter

You can have it match first by appending a `?` to the end of the filter.

    >>> d = {
    ...     'a': [{'id': 1, 'hello': 'there'}, {'id': 2, 'hello': 'there'}],
    ...     'b': [{'id': 3, 'hello': 'there'}, {'id': 4, 'hello': 'bye'}],
    ... }
    >>> dotted.get(d, 'a[hello="there"?]')
    [{'id': 1, 'hello': 'there'}]

<a id="conjunction-vs-disjunction"></a>
### Conjunction vs disjunction

To _conjunctively_ connect filters use the `&` operator. Filters offer the ability to act
_disjunctively_ as well by using the `,` operator.

For example, given
`*&key1=value1,key2=value2&key3=value3`. This will filter
(`key1=value1` OR `key2=value2`) AND `key3=value3`.

Note that this gives you the ability to have a key filter multiple values, such as:
`*&key1=value1,key2=value2`.

<a id="grouping-with-parentheses"></a>
### Grouping with parentheses

Use parentheses to control precedence in complex filter expressions:

    >>> data = [
    ...     {'id': 1, 'type': 'a', 'active': True},
    ...     {'id': 2, 'type': 'b', 'active': True},
    ...     {'id': 3, 'type': 'a', 'active': False},
    ... ]

    # (id=1 OR id=2) AND active=True
    >>> dotted.get(data, '[(id=1,id=2)&active=True]')
    [{'id': 1, 'type': 'a', 'active': True}, {'id': 2, 'type': 'b', 'active': True}]

    # id=1 OR (id=3 AND active=False)
    >>> dotted.get(data, '[id=1,(id=3&active=False)]')
    [{'id': 1, 'type': 'a', 'active': True}, {'id': 3, 'type': 'a', 'active': False}]

Groups can be nested for complex logic:

    # ((id=1 OR id=2) AND type='a') OR id=4
    >>> dotted.get(data, '[((id=1,id=2)&type="a"),id=4]')

Precedence: `&` (AND) binds tighter than `,` (OR). Use parentheses when you need
OR groups inside AND expressions.

To use literal parentheses in keys, quote them: `"(key)"`.

<a id="filter-negation-and-not-equals"></a>
### Filter negation and not-equals

Use `!` to negate filter conditions, or `!=` as syntactic sugar for not-equals (`key!=value` ≡ `!(key=value)`):

    >>> data = [
    ...     {'status': 'active', 'role': 'admin'},
    ...     {'status': 'inactive', 'role': 'user'},
    ...     {'status': 'active', 'role': 'user'},
    ... ]

    # Not-equals: items where status != "active"
    >>> dotted.get(data, '[status!="active"]')
    [{'status': 'inactive', 'role': 'user'}]

    # Equivalent using negation
    >>> dotted.get(data, '[!status="active"]')
    [{'status': 'inactive', 'role': 'user'}]

    # Negate grouped expression - NOT (active AND admin)
    >>> dotted.get(data, '[!(status="active"&role="admin")]')
    [{'status': 'inactive', 'role': 'user'}, {'status': 'active', 'role': 'user'}]

    # Combine negation with AND - active non-admins
    >>> dotted.get(data, '[status="active"&!role="admin"]')
    [{'status': 'active', 'role': 'user'}]

Precedence: `!` binds tighter than `&` and `,`:

    [!a=1&b=2]    →  [(!a=1) & b=2]
    [!(a=1&b=2)]  →  negate the whole group

#### Filtering for missing fields

Use `!field=*` to filter for items where a field is missing entirely (vs exists with
value `None`):

    >>> data = [
    ...     {'name': 'alice', 'email': 'alice@example.com'},
    ...     {'name': 'bob'},  # no email field
    ...     {'name': 'charlie', 'email': None},  # email exists but is None
    ... ]

    # Field missing (doesn't exist)
    >>> dotted.get(data, '[!email=*]')
    [{'name': 'bob'}]

    # Field exists with value None
    >>> dotted.get(data, '[email=None]')
    [{'name': 'charlie', 'email': None}]

This works because `email=*` matches any value when the field exists, so `!email=*`
only passes when the field is missing.

<a id="boolean-and-none-filter-values"></a>
### Boolean and None filter values

Filters support `True`, `False`, and `None` as values:

    >>> data = [
    ...     {'name': 'alice', 'active': True, 'score': None},
    ...     {'name': 'bob', 'active': False, 'score': 100},
    ... ]
    >>> dotted.get(data, '[active=True]')
    [{'name': 'alice', 'active': True, 'score': None}]
    >>> dotted.get(data, '[score=None]')
    [{'name': 'alice', 'active': True, 'score': None}]

<a id="value-guard"></a>
### Value guard

A **value guard** tests the value at a path and yields it only if it matches.
Use `key=value` or `[slot]=value` after accessing a field:

    >>> d = {'first': 7, 'last': 3}
    >>> dotted.get(d, 'first=7')
    7
    >>> dotted.get(d, 'first=3')   # no match
    >>> dotted.get(d, '*=7')
    (7,)
    >>> dotted.pluck(d, '*=7')
    (('first', 7),)

For lists of primitive values, use `[*]=value`:

    >>> data = [1, 7, 3, 7]
    >>> dotted.get(data, '[*]=7')
    (7, 7)
    >>> dotted.get(data, '[0]=1')
    1

Guards support all value types: numbers, `None`, `True`/`False`, strings, regex, and `*`:

    >>> dotted.get([None, 1, 2], '[*]=None')
    (None,)
    >>> dotted.get(['hello', 'world', 'help'], '[*]="hello"')
    ('hello',)
    >>> dotted.get(['hello', 'world', 'help'], '[*]=/hel.*/')
    ('hello', 'help')

Use `!=` for negation:

    >>> dotted.get([True, False, None, 1, 2], '[*]!=True')
    (False, None, 2)
    >>> dotted.get({'a': 7, 'b': 3}, '*!=7')
    (3,)

Guards compose with continuation (dot paths):

    >>> dotted.get({'a': {'first': 7}}, 'a.first=7')
    7

**Note**: `[*=value]` (equals inside brackets) is a SliceFilter — it tests *keys* of each
dict-like list item. `[*]=value` (equals outside brackets) is a value guard — it tests
the item values directly. For primitive lists, use `[*]=value`.

**Note**: Python equality applies, so `1 == True` and `0 == False`:

    >>> dotted.get([True, 1, False, 0], '[*]=True')
    (True, True, 1)

<a id="dotted-filter-keys"></a>
### Dotted filter keys

Filter keys can contain dotted paths to filter on nested fields:

    >>> d = {
    ...     'items': [
    ...         {'user': {'id': 1, 'name': 'alice'}, 'value': 100},
    ...         {'user': {'id': 2, 'name': 'bob'}, 'value': 200},
    ...     ]
    ... }
    >>> dotted.get(d, 'items[user.id=1]')
    [{'user': {'id': 1, 'name': 'alice'}, 'value': 100}]
    >>> dotted.get(d, 'items[user.name="bob"][0].value')
    200

<a id="slice-notation-in-filter-keys"></a>
### Slice notation in filter keys

Filter keys can include slice notation so the comparison applies to a slice of the field value (prefix, suffix, or any slice). Use the same slice syntax as in paths: integers and `+` for start/stop/step.

    >>> data = [
    ...     {'name': 'hello world', 'file': 'app.py'},
    ...     {'name': 'hi', 'file': 'readme.md'},
    ...     {'name': 'hello', 'file': 'x.py'},
    ... ]
    >>> dotted.get(data, '[*&name[:5]="hello"]')
    [{'name': 'hello world', 'file': 'app.py'}, {'name': 'hello', 'file': 'x.py'}]
    >>> dotted.get(data, '[*&file[-3:]=".py"]')
    [{'name': 'hello world', 'file': 'app.py'}, {'name': 'hello', 'file': 'x.py'}]

<a id="transforms"></a>
## Transforms

You can optionally add transforms to the end of dotted notation. These will
be applied on `get` and `update`. Transforms are separated by the `|` operator
and multiple may be chained together. Transforms may be parameterized using
the `:` operator.

    >>> import dotted
    >>> d = [1, '2', 3]
    >>> dotted.get(d, '[1]')
    '2'
    >>> dotted.get(d, '[1]|int')
    2
    >>> dotted.get(d, '[0]|str:number=%d')
    'number=1'

You may register new transforms via either `register` or the `@transform`
decorator.

<a id="built-in-transforms"></a>
### Built-in Transforms

| Transform | Parameters | Description |
|-----------|------------|-------------|
| `str` | `fmt`, `raises` | Convert to string. Optional format: `\|str:Hello %s` |
| `int` | `base`, `raises` | Convert to int. Optional base: `\|int:16` for hex |
| `float` | `raises` | Convert to float |
| `decimal` | `raises` | Convert to `Decimal` |
| `none` | values... | Return `None` if falsy or matches values: `\|none::null:empty` |
| `strip` | `chars`, `raises` | Strip whitespace or specified chars |
| `len` | `default` | Get length. Optional default if not sized: `\|len:0` |
| `lowercase` | `raises` | Convert string to lowercase |
| `uppercase` | `raises` | Convert string to uppercase |
| `add` | `rhs` | Add value: `\|add:10` |
| `list` | `raises` | Convert to list |
| `tuple` | `raises` | Convert to tuple |
| `set` | `raises` | Convert to set |

The `raises` parameter causes the transform to raise an exception on failure instead of
returning the original value:

    >>> import dotted
    >>> dotted.get({'n': 'hello'}, 'n|int')      # fails silently
    'hello'
    >>> dotted.get({'n': 'hello'}, 'n|int::raises')  # raises ValueError
    Traceback (most recent call last):
    ...
    ValueError: invalid literal for int() with base 10: 'hello'

<a id="custom-transforms"></a>
### Custom Transforms

Register custom transforms using `register` or the `@transform` decorator:

    >>> import dotted
    >>> @dotted.transform('double')
    ... def double(val):
    ...     return val * 2
    >>> dotted.get({'n': 5}, 'n|double')
    10

View all registered transforms with `dotted.registry()`.

<a id="constants-and-exceptions"></a>
## Constants and Exceptions

### ANY

The `ANY` constant is used with `remove` and `update` to match any value:

    >>> import dotted
    >>> d = {'a': 1, 'b': 2}
    >>> dotted.remove(d, 'a', dotted.ANY)  # remove regardless of value
    {'b': 2}
    >>> dotted.update(d, '-b', dotted.ANY)  # inverted update = remove
    {}

### ParseError

Raised when dotted notation cannot be parsed:

    >>> import dotted
    >>> dotted.get({}, '[invalid')
    Traceback (most recent call last):
    ...
    dotted.api.ParseError: Expected ']' at pos 8: '[invalid'

<a id="faq"></a>
## FAQ

### Why do I get a tuple for my get?

`get()` returns a **single value** for a non-pattern path and a **tuple of values** for a pattern path. A path is a pattern if:

- Any path segment is a pattern (e.g. wildcard `*`, regex), or
- The path uses operation or path grouping: disjunction `(.a,.b)` / `(a,b)`, conjunction `(.a&.b)` / `(a&b)`, or negation `(!.a)` / `(!a)`.

Filters (e.g. `key=value`, `*=None`) can use patterns but do **not** make the path a pattern; only the path segments and path-level operators do. So `name.first&first=None` is non-pattern (single value), while `name.*&first=None` is pattern (tuple), even though both can express "when name.first is None." Value guards (e.g. `name.first=None`) also preserve the pattern/non-pattern status of the underlying path segment.

For `update` and `remove` you usually don't care: the result is the (possibly mutated) object either way. For `get`, the return shape depends on pattern vs non-pattern. Use `dotted.is_pattern(path)` if you need to branch on it.

### How do I craft an efficient path?

Same intent can be expressed in more or less efficient ways. Example: "match when name.first is None"

- **Inefficient:** `name.*&first=None` — pattern path; iterates every key under `name`, then filters. No short-circuit.
- **Better:** `name.*&first=None?` — same path with first-match `?`; stops after one match.
- **Even better:** `name.first=None` — value guard; non-pattern path; goes straight to `name.first` and tests the value directly.
- **Also good:** `name.first&first=None` — non-pattern path with a concrete filter key.

Prefer a concrete path when it expresses what you want; use pattern + `?` when you need multiple candidates but only care about the first match.

### Why do I get a RuntimeError when updating with a slice filter?

A slice filter like `[id=1]` returns the **filtered sublist** as a single value — it operates on the list itself, not on individual items. Updating that sublist in place is ambiguous: the matching items may be at non-contiguous indices, so there's no clean way to splice a replacement back into the original.

But you probably don't want to update a slice filter anyway — instead, use a pattern like `[*&id=1]` which walks the items individually and updates each match in place.
