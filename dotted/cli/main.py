"""
CLI entry point for dq — dotted notation query tool.
"""
import json
import signal
import sys

import dotted
from dotted.api import ParseError

from .formats import READERS, WRITERS, validate_reader, validate_writer


OPERATIONS = {'get', 'update', 'remove'}


def parse_value(s):
    """
    Parse a string as JSON, falling back to plain string.
    """
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return s


def read_path_file(path, operation):
    """
    Read paths (and values for update/remove) from a file.
    Lines starting with # and blank lines are skipped.
    For get: one path per line.
    For update: path and value separated by whitespace.
    For remove: path per line, or path and value separated by whitespace.
    """
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if operation == 'update':
                parts = line.split(None, 1)
                if len(parts) != 2:
                    raise SystemExit(
                        f"Path file line for update must have path and value: {line!r}"
                    )
                entries.append((parts[0], parse_value(parts[1])))
            elif operation == 'remove':
                parts = line.split(None, 1)
                if len(parts) == 2:
                    entries.append((parts[0], parse_value(parts[1])))
                else:
                    entries.append(parts[0])
            else:
                entries.append(line)
    return entries


def collect_entries(args):
    """
    Collect paths (and values for update) from all sources.
    Returns a list of paths for get/remove, or (path, value) tuples for update.
    """
    entries = []

    # Positional path (shorthand, get only)
    if args.positional_path:
        if args.operation == 'update':
            raise SystemExit("Use -p PATH VALUE for update, not positional path")
        entries.append(args.positional_path)

    # -p / --path flags
    if args.paths:
        for group in args.paths:
            if args.operation == 'update':
                if len(group) != 2:
                    raise SystemExit(
                        f"update requires -p PATH VALUE, got: {' '.join(group)}"
                    )
                entries.append((group[0], parse_value(group[1])))
            elif args.operation == 'remove':
                if len(group) == 1:
                    entries.append(group[0])
                elif len(group) == 2:
                    entries.append((group[0], parse_value(group[1])))
                else:
                    raise SystemExit(
                        f"remove requires -p PATH [VALUE], got: {' '.join(group)}"
                    )
            else:
                if len(group) > 1:
                    print(
                        f"dq: warning: ignoring value for get path: {' '.join(group[1:])}",
                        file=sys.stderr,
                    )
                entries.append(group[0])

    # -pf / --path-file flags
    if args.path_files:
        for pf in args.path_files:
            entries.extend(read_path_file(pf, args.operation))

    if not entries:
        if args.operation in ('update', 'remove'):
            raise SystemExit(f"{args.operation} requires at least one path (-p or -pf)")
        return None
    return entries


def process_get(doc, paths):
    """
    Project a document to the given paths.
    """
    if len(paths) == 1:
        return dotted.get(doc, paths[0])
    pairs = dotted.pluck_multi(doc, paths)
    return dotted.update_multi(dotted.AUTO, pairs)


def process_update(doc, entries):
    """
    Apply (path, value) updates sequentially.
    """
    return dotted.update_multi(doc, entries)


def process_remove(doc, entries):
    """
    Remove each path sequentially. Entries are either plain paths
    or (path, value) tuples for conditional removal.
    """
    for entry in entries:
        if isinstance(entry, tuple):
            doc = dotted.remove(doc, entry[0], val=entry[1])
        else:
            doc = dotted.remove(doc, entry)
    return doc


def _get_version():
    """
    Get the installed package version.
    """
    try:
        from importlib.metadata import version
        return version('dotted_notation')
    except Exception:
        return 'unknown'


def build_parser():
    """
    Build the argument parser.
    """
    import argparse
    parser = argparse.ArgumentParser(
        prog='dq',
        description='Query and transform nested data with dotted notation.',
    )
    parser.add_argument(
        '-V', '--version',
        action='version',
        version=f'%(prog)s {_get_version()}',
    )
    parser.add_argument(
        '-i', '--input',
        choices=sorted(READERS),
        default=None,
        dest='input_format',
        help='Input format (default: auto from -f extension, or json)',
    )
    parser.add_argument(
        '-o', '--output',
        choices=sorted(WRITERS),
        default=None,
        dest='output_format',
        help='Output format (default: same as input)',
    )
    parser.add_argument(
        '-p', '--path',
        action='append',
        nargs='+',
        dest='paths',
        metavar='ARG',
        help='Dotted path (get/remove: -p PATH, update: -p PATH VALUE)',
    )
    parser.add_argument(
        '-pf', '--path-file',
        action='append',
        dest='path_files',
        metavar='FILE',
        help='File of paths, one per line (update: path value per line)',
    )
    parser.add_argument(
        '--unpack',
        action='store_true',
        default=False,
        help='Flatten result to dotted normal form',
    )
    parser.add_argument(
        '--unpack-attrs',
        nargs='+',
        choices=[e.value for e in dotted.Attrs],
        default=None,
        metavar='KIND',
        help='Include object attrs in unpack: standard, special, or both',
    )
    parser.add_argument(
        '--pack',
        action='store_true',
        default=False,
        help='Rebuild nested structure from dotted normal form',
    )
    parser.add_argument(
        '-f', '--file',
        default=None,
        dest='input_file',
        metavar='FILE',
        help='Read input from FILE instead of stdin',
    )
    parser.add_argument(
        'operation',
        nargs='?',
        default=None,
        help='Operation: get (default), update, remove',
    )
    parser.add_argument(
        'positional_path',
        nargs='?',
        default=None,
        help='Dotted path (shorthand for -p)',
    )
    return parser


_EXT_FORMATS = {
    '.json': 'json',
    '.jsonl': 'jsonl',
    '.ndjson': 'jsonl',
    '.csv': 'csv',
    '.yaml': 'yaml',
    '.yml': 'yaml',
    '.toml': 'toml',
}


def parse_args(argv=None):
    """
    Parse CLI arguments, handling implicit get operation.
    """
    import os
    parser = build_parser()
    args = parser.parse_args(argv)

    # Disambiguate: if 'operation' is not a known op, it's actually the path
    if args.operation is not None and args.operation not in OPERATIONS:
        args.positional_path = args.operation
        args.operation = 'get'

    if args.operation is None:
        args.operation = 'get'

    # Auto-detect input format from file extension
    if args.input_format is None:
        if args.input_file:
            ext = os.path.splitext(args.input_file)[1].lower()
            args.input_format = _EXT_FORMATS.get(ext, 'json')
        else:
            args.input_format = 'json'

    if args.output_format is None:
        args.output_format = args.input_format

    return args


def main(argv=None):
    """
    CLI entry point.
    """
    # Handle SIGPIPE gracefully on Unix
    try:
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except AttributeError:
        pass  # Windows

    args = parse_args(argv)

    # Validate format dependencies before blocking on stdin
    validate_reader(args.input_format)
    validate_writer(args.output_format)

    try:
        entries = collect_entries(args)
    except SystemExit:
        raise
    except Exception as e:
        print(f"dq: {e}", file=sys.stderr)
        sys.exit(1)

    reader = READERS[args.input_format]
    writer_cls = WRITERS[args.output_format]
    writer = writer_cls(sys.stdout)

    input_file = None
    if args.input_file:
        try:
            input_file = open(args.input_file)
        except OSError as e:
            print(f"dq: {e}", file=sys.stderr)
            sys.exit(1)
    stream = input_file or sys.stdin

    try:
        for doc in reader(stream):
            if args.pack and isinstance(doc, dict):
                doc = dotted.update_multi(dotted.AUTO, doc.items())
            if entries is None:
                result = doc
            elif args.operation == 'get':
                result = process_get(doc, entries)
            elif args.operation == 'update':
                result = process_update(doc, entries)
            elif args.operation == 'remove':
                result = process_remove(doc, entries)
            if args.unpack:
                attrs = [dotted.Attrs(a) for a in args.unpack_attrs] if args.unpack_attrs else None
                result = dict(dotted.unpack(result, attrs=attrs))
            writer.write(result)
    except ParseError as e:
        print(f"dq: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        pass
    except BrokenPipeError:
        pass
    finally:
        try:
            writer.finish()
        except BrokenPipeError:
            pass
        if input_file:
            input_file.close()


if __name__ == '__main__':
    main()
