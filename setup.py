"""
Build script. Everything about the package is declared in pyproject.toml;
this file only adds the optional compiled engine.

The engine modules are ordinary Python. When Cython is installed at build
time they are also compiled to C, and Python then imports the compiled
form in preference to the .py file beside it. Without Cython, or with
DOTTED_PURE set, or if a module fails to compile, nothing is built and the
same files run as Python.
"""
import os

from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext as _build_ext

# The engine and the ops it runs. grammar.py, the parser, is left out on
# purpose: pyparsing inspects its parse actions as Python functions.
MODULES = (
    'access',
    'api',
    'base',
    'containers',
    'engine',
    'filters',
    'groups',
    'matchers',
    'predicates',
    'recursive',
    'results',
    'transforms',
    'utils',
    'utypes',
    'wrappers',
)


def extensions():
    """
    The engine modules as optional extensions, or nothing when they are not
    to be compiled.
    """
    if os.environ.get('DOTTED_PURE'):
        return []
    try:
        from Cython.Build import cythonize
    except ImportError:
        return []
    return cythonize(
        [Extension('dotted.' + name, ['dotted/' + name + '.py'], optional=True) for name in MODULES],
        compiler_directives={'language_level': 3},
        nthreads=os.cpu_count() or 1,
    )


class build_ext(_build_ext):
    """
    Compile the extensions one per core. setuptools builds them one after
    another unless -j is given; the modules are independent, so a release
    wheel compiles in a fraction of the time on a multi-core runner.
    """
    def finalize_options(self):
        super().finalize_options()
        if self.parallel is None:
            self.parallel = os.cpu_count() or 1


setup(ext_modules=extensions(), cmdclass={'build_ext': build_ext})
