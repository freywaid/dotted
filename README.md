# Dotted

Sometimes you want to fetch data from a deeply nested data structure. Dotted
notation helps you do that.

Let's say you have a dictionary containing a dictionary containing a list and
you wish to fetch the ith value from that nested list:

    >>> import dotted
    >>> d = {'hi': {'there': [1, 2, 3]}}
    >>> dotted.get(d, 'hi.there[1]')
    2

## Grammar

Dotted notation looks similar to python. Both _dot_ fields and _slot_ fields,
e.g. brackets, call `__getitem__` internally.  A _dot_ field expects to see
a dictionary-like object.  A _slot_ field is biased towards sequences (like lists,
tuples, and strs) but can act on dicts as well. Dotted also supports slicing
notation.

## Patterns

You can use dotted for pattern matching.  You can match to wildcards or regular
expressions:

    >>> import dotted
    >>> d = {'hi': {'there': [1, 2, 3]}, 'bye': {'there': [4, 5, 6]}}
    >>> dotted.get(d, '*.there[2]')
    (3, 6)
    >>> dotted.get(d, '/h.*/.*')

Dotted will return all values that match the pattern(s).


## Appending with the '+' operator

Both slots and slices support the '+' operator which will append to the end
of a sequence. Given the example above:

    >>> dotted.update(d, '*.there[+]', 8)
    {'hi': {'there': [1, 2, 3, 8]}, 'bye': {'there': [4, 5, 6, 8]}}
