# Dotted

Sometimes you want to fetch data from a deeply nested data structure. Dotted notation
helps you do that.

## Breaking Changes

### v0.13.0
- **Filter conjunction operator changed from `.` to `&`**: The conjunction operator for
  chaining multiple filters has changed. Previously, `*.id=1.name="alice"` was used for
  conjunctive (AND) filtering. Now use `*&id=1&name="alice"`. This change enables support
  for dotted paths within filter keys (e.g., `items[user.id=1]` to filter on nested fields).

Let's say you have a dictionary containing a dictionary containing a list and you wish
to fetch the ith value from that nested list.

    >>> import dotted
    >>> d = {'hi': {'there': [1, 2, 3]}}
    >>> dotted.get(d, 'hi.there[1]')
    2

## API

Probably the easiest thing to do is pydoc the api layer.

    $ pydoc dotted.api

### Get

See grammar discussion below about things you can do get data via dotted.

    >>> import dotted
    >>> dotted.get({'a': {'b': {'c': {'d': 'nested'}}}}, 'a.b.c.d')
    'nested'

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
    ```
#### Update via pattern

You can update all fields that match pattern given by either a wildcard OR regex.

    >>> import dotted
    >>> d = {'a': 'hello', 'b': {'bye'}}
    >>> dotted.update(d, '*', 'me')
    {'a': 'me', 'b': 'me'}

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

### Match

Use to match a dotted-style pattern to a field.  Partial matching is on by default.  You
can match via wildcard OR via regex.  Here's a regex example:

    >>> import dotted
    >>> dotted.match('/a.+/', 'abced.b')
    'abced.b'
    >>> dotted.match('/a.+/', 'abced.b', partial=False)

With the `groups=True` parameter, you'll see how it was matched:

    >>> import dotted
    >>> match('hello.*', 'hello.there.bye', groups=True)
    ('hello.there.bye', ('hello', 'there.bye'))

In the above example, `hello` matched to `hello` and `*` matched to `there.bye` (partial
matching is enabled by default).

### Expand

You may wish to _expand_ all fields that match a pattern in an object.

    >>> import dotted
    >>> d = {'hello': {'there': [1, 2, 3]}, 'bye': 7}
    >>> dotted.expand(d, '*')
    ('hello', 'bye')
    >>> dotted.expand(d, '*.*')
    ('hello.there',)
    >>> dptted.expand(d, '*.*[*]')
    ('hello.there[0]', 'hello.there[1]', 'hello.there[2]')
    >>> dotted.expand(d, '*.*[1:]')
    ('hello.there[1:]',)

## Grammar

Dotted notation shares similarities with python. A _dot_ `.` field expects to see a
dictionary-like object (using `keys` and `__getitem__` internally.  A _bracket_ `[]`
field is biased towards sequences (like lists or strs) but can also act on dicts.  A
_attr_ `@` field uses `getattr/setattr/delattr`.  Dotted also support slicing notation
as well as transforms discussed below.

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

### Bracketed fields

You may also use bracket notation, such as `a[0]` which does a `__getitem__` at key 0.
The parser prefers numeric types over string types (if you wish to look up a non-numeric
field using brackets be sure to quote it).  Bracketed fields will work with pretty much
any object that can be looked up via `__getitem__`.

    >>> import dotted
    >>> dotted.get({'a': ['first', 'second', 'third']}, 'a[0]')
    'first'
    >>> dotted.get({'a': {'b': 'hello'}}, 'a["b"]')
    'first'

### Attr fields

An attr field is expressed by prefixing with `@`. This will fetch data at that attribute.
You may wonder why have this when you can just as easily use standard python to access.
Two important reasons: nested expressions and patterns.

    >>> import dotted, types
    >>> ns = types.SimpleNamespace
    >>> ns.hello = {'me': 'goodbye'}
    >>> dotted.get(ns, '@hello.me')
    'goodbye'

### Numeric types

The parser will attempt to interpret a field numerically if it can, such as `field.1`
will interpret the `1` part numerically.

    >>> import dotted
    >>> dotted.get({'7': 'me', 7: 'you'}, '7')
    'you'

### Quoting

Sometimes you need to quote a field which you can do by just putting the field in quotes.

    >>> import dotted
    >>> dotted.get({'has . in it': 7}, '"has . in it"')
    7

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

### Slicing

Dotted slicing works like python slicing and all that entails.

    >>> import dotted
    >>> d = {'hi': {'there': [1, 2, 3]}, 'bye': {'there': [4, 5, 6]}}
    >>> dotted.get(d, 'hi.there[::2]')
    [1, 3]
    >>> dotted.get(d, '*.there[1:]')
    ([2, 3], [5, 6])

### The append `+` operator

Both bracketed fileds and slices support the '+' operator which refers to the end of
sequence. You may append an item or slice to the end a sequence.

    >>> import dotted
    >>> d = {'hi': {'there': [1, 2, 3]}, 'bye': {'there': [4, 5, 6]}}
    >>> dotted.update(d, '*.there[+]', 8)
    {'hi': {'there': [1, 2, 3, 8]}, 'bye': {'there': [4, 5, 6, 8]}}
    >>> dotted.update(d, '*.there[+:]', [999])
    {'hi': {'there': [1, 2, 3, 8, 999]}, 'bye': {'there': [4, 5, 6, 8, 999]}}

### The append-unique `+?` operator

If you want to update only _unique_ items to a list, you can use the `?`
postfix.  This will ensure that it's only added once (see match-first below).

    >>> import dotted
    >>> items = [1, 2]
    >>> dotted.update(items, '[+?]', 3)
    [1, 2, 3]
    >>> dotted.update(items, '[+?]', 3)
    [1, 2, 3]

### The invert `-` operator

You can invert the meaning of the notation by prefixing a `-`.  For example,
to remove an item using `update`:

    >>> import dotted
    >>> d = {'a': 'hello', 'b': 'bye'}
    >>> dotted.update(d, '-b', dotted.ANY)
    {'a': 'hello'}
    >>> dotted.remove(d, '-b', 'bye again')
    {'a': 'hello', 'b': 'bye again'}

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

### Wildcards

The wildcard pattern is `*`.  It will match anything.

### Regular expressions

The regex pattern is enclosed in slashes: `/regex/`. Note that if the field is a non-str,
the regex pattern will internally match to its str representation.

### The match-first operatoer

You can also postfix any pattern with a `?`.  This will return only
the first match.

    >>> import dotted
    >>> d = {'hi': {'there': [1, 2, 3]}, 'bye': {'there': [4, 5, 6]}}
    >>> dotted.get(d, '*?.there[2]')
    (3,)

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
decorator. Look at transforms.py for preregistered.

## Filters

### The key-value filter

You may filter by key-value to narrow your result set.  You may use with __key__ or
__bracketed__ fields.  Key-value fields may be disjunctively (OR) specified via the `,`
delimiter.

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
    r == (1, 2, 3)

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

### The key-value first filter

You can have it match first by appending a `?` to the end of the filter.

    >>> d = {
    ...     'a': [{'id': 1, 'hello': 'there'}, {'id': 2, 'hello': 'there'}],
    ...     'b': [{'id': 3, 'hello': 'there'}, {'id': 4, 'hello': 'bye'}],
    ... }
    >>> dotted.get(d, 'a[hello="there"?]')
    return [{'id': 1, 'hello': 'there'}]

### Conjunction vs disjunction

To _conjunctively_ connect filters use the `&` operator. Filters offer the ability to act
_disjunctively_ as well by using the `,` operator.

For example, given
`*&key1=value1,key2=value2&key3=value3`. This will filter
(`key1=value1` OR `key2=value2`) AND `key3=value3`.

Note that this gives you the abilty to have a key filter multiple values, such as:
`*&key1=value1,key2=value2`.
