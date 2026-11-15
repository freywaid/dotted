"""
README links within the page land on PyPI as well as on GitHub.

GitHub follows a #link to an explicit <a id> or to a heading's slug.
PyPI rewrites every #link to #user-content-<name> but prefixes only the
headings' ids, so there a link lands only on a heading's slug. A link
must name a heading's slug, and an explicit anchor must say the same.
"""
import pathlib
import re


README = pathlib.Path(__file__).resolve().parent.parent / 'README.md'

_HEADING = re.compile(r'(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$')
_ANCHOR = re.compile(r'<a id="([^"]+)"></a>\s*$')
_LINK = re.compile(r'\]\(#([^)\s]+)\)')
# raw HTML that could be a link or a link target: an <a> tag, or any tag with an id or name
_TARGET_TAG = re.compile(r'<a[\s>/]|<[A-Za-z][^>]*\s(?:id|name)\s*=', re.IGNORECASE)
_CODE_SPAN = re.compile(r'(`+).*?\1')


def _slugify(text):
    """
    A heading's slug as GitHub and PyPI make it: its text, lowercased,
    with punctuation dropped and each space a hyphen.
    """
    text = re.sub(r'`([^`]*)`', r'\1', text)
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'[^\w\- ]', '', text.strip().lower())
    return text.replace(' ', '-')


def _headings(lines):
    """
    (line number, slug) of each heading; a repeated slug takes -1, -2, ...
    """
    seen = {}
    out = []
    for n, line in enumerate(lines, 1):
        m = _HEADING.match(line)
        if not m:
            continue
        base = _slugify(m.group(2))
        slug = base
        while slug in seen:
            seen[base] += 1
            slug = f'{base}-{seen[base]}'
        seen[slug] = 0
        out.append((n, slug))
    return out


def _lines():
    return README.read_text(encoding='utf-8').splitlines()


def test_readme_links_name_a_heading():
    """
    Each #link names a heading's slug.
    """
    lines = _lines()
    slugs = {slug for _, slug in _headings(lines)}
    bad = [
        f'README.md:{n}: #{target}'
        for n, line in enumerate(lines, 1)
        for target in _LINK.findall(line)
        if target not in slugs
    ]
    assert not bad, 'links to no heading:\n' + '\n'.join(bad)


def test_readme_anchors_match_their_heading():
    """
    An explicit <a id> sits on the line above a heading and names that
    heading's slug, so it and the heading agree on every renderer.
    """
    lines = _lines()
    slug_at = dict(_headings(lines))
    bad = []
    for n, line in enumerate(lines, 1):
        m = _ANCHOR.match(line)
        if not m:
            continue
        slug = slug_at.get(n + 1)
        if slug != m.group(1):
            bad.append(f'README.md:{n}: id="{m.group(1)}", heading slug {slug!r}')
    assert not bad, 'anchors that differ from their heading:\n' + '\n'.join(bad)


def test_readme_anchors_are_checkable():
    """
    Raw HTML that could be a link or a link target is only ever an anchor
    the test above checks: <a id="..."></a> on a line of its own.  Any
    other form, <a name>, <a href>, single quotes, an id on another tag,
    an anchor mid-line, would go unchecked, so it fails here.  Code is
    skipped: an indented block, a code span.
    """
    bad = []
    for n, line in enumerate(_lines(), 1):
        if line.startswith(('    ', '\t')):
            continue
        if _ANCHOR.match(line):
            continue
        if _TARGET_TAG.search(_CODE_SPAN.sub('', line)):
            bad.append(f'README.md:{n}: {line.strip()}')
    assert not bad, 'anchors in a form the test cannot check:\n' + '\n'.join(bad)
