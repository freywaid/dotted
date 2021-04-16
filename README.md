# Dotted

Sometimes you want to fetch data from a deeply nested data structure. Dotted notation
helps you do that.

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

You may with to _expand_ all fields that match a pattern in an object.

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

Dotted notation looks similar to python. Both _dot_ fields and _bracketed_ fields
use `keys()` and `__getitem__()` internally.  A _dot_ field expects to see a dictionary-like object.
A _slot_ field is biased towards sequences (like lists, tuples, and strs) but can act on
dicts as well. Dotted also supports slicing notation as well as transforms discussed
below.

### Key fields

A key field is expressed as `a` or part of a dotted expression, such as `a.b`.  The
grammar parser is pretty permissive for what can be in a key field.  Pretty much any
non-reserved char will match.  Note that key fields will only work on objects that have
a `keys()` method.  Basically, they work with dictionary or dictionary-like objects

    >>> import dotted
    >>> dotted.get({'a': {'b': 'hello'}}, 'a.b')
    'hello'

If the key field starts with a space or `-`, you should either quote it OR you may use
a `\` as the first char.

### Bracketed fields

You may also use bracket notation, such as `a[0]` which does a `__getitem__` at key 0.
The parser prefers numeric types over string types (if you wish to look  up a non-numeric
field using brackets be sure to quote it).  Bracketed fields will work with pretty much
any object that can be looked up via `__getitem__`.

    >>> import dotted
    >>> dotted.get({'a': ['first', 'second', 'third']}, 'a[0]')
    'first'
    >>> dotted.get({'a': {'b': 'hello'}}, 'a["b"]')
    'first'

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

### Patterns

You can use dotted for pattern matching. You can match to wildcards or regular
expressions.  You'll note that patterns always return a tuple of matches.

    >>> import dotted
    >>> d = {'hi': {'there': [1, 2, 3]}, 'bye': {'there': [4, 5, 6]}}
    >>> dotted.get(d, '*.there[2]')
    (3, 6)
    >>> dotted.get(d, '/h.*/.*')
    ([1, 2, 3],)

Dotted will return all values that match the pattern(s).

### The match-first operatoer

You can also postfix the wildcard or regex with a `?`.  This will return only
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
