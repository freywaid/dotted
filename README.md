# Dotted

Sometimes you want to fetch data from a deeply nested data structure. Dotted
notation helps you do that.

Let's say you have a dictionary containing a dictionary containing a list and
you wish to fetch the ith value from that nested list.

    >>> import dotted
    >>> d = {'hi': {'there': [1, 2, 3]}}
    >>> dotted.get(d, 'hi.there[1]')
    2

## Grammar

Dotted notation looks similar to python. Both _dot_ fields and _slot_ fields,
e.g. brackets, call `__getitem__` internally.  A _dot_ field expects to see
a dictionary-like object.  A _slot_ field is biased towards sequences (like
lists, tuples, and strs) but can act on dicts as well. Dotted also supports
slicing notation as well as transforms discussed below.

## Patterns

You can use dotted for pattern matching. You can match to wildcards or regular
expressions.

    >>> import dotted
    >>> d = {'hi': {'there': [1, 2, 3]}, 'bye': {'there': [4, 5, 6]}}
    >>> dotted.get(d, '*.there[2]')
    (3, 6)
    >>> dotted.get(d, '/h.*/.*')
    ([1, 2, 3],)

Dotted will return all values that match the pattern(s).

## Slicing

Slicing is also supported. Dotted slicing works like python slicing and all
that entails.

    >>> import dotted
    >>> d = {'hi': {'there': [1, 2, 3]}, 'bye': {'there': [4, 5, 6]}}
    >>> dotted.get(d, 'hi.there[::2]')
    [1, 3]
    >>> dotted.get(d, '*.there[1:]')
    ([2, 3], [5, 6])

## The '+' operator

Both slots and slices support the '+' operator which refers to the end of
sequence. You may append an item or slice to the end a sequence.

    >>> import dotted
    >>> d = {'hi': {'there': [1, 2, 3]}, 'bye': {'there': [4, 5, 6]}}
    >>> dotted.update(d, '*.there[+]', 8)
    {'hi': {'there': [1, 2, 3, 8]}, 'bye': {'there': [4, 5, 6, 8]}}
    >>> dotted.update(d, '*.there[+:]', [999])
    {'hi': {'there': [1, 2, 3, 8, 999]}, 'bye': {'there': [4, 5, 6, 8, 999]}}

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
