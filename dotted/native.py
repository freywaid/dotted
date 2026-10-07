"""
Native or not: which form of the engine this process runs.

The engine modules are plain Python. Where they have also been compiled to
C, the compiled file sits next to the .py file and Python imports it in
preference. That is the native engine, and it is used whenever it is there.

Set DOTTED_NATIVE=0 in the environment to run from the .py source instead.
The choice is made once, when dotted is first imported, and holds for the
life of the process.

This module is never compiled itself.
"""
import importlib.util
import os
import sys

OFF = ('0', 'false', 'no', 'off', '')


def wanted():
    """
    True unless DOTTED_NATIVE says to run from source.
    """
    return os.environ.get('DOTTED_NATIVE', '1').strip().lower() not in OFF


def active():
    """
    True if the engine this process loaded is the compiled one.
    """
    engine = sys.modules.get(__package__ + '.engine')
    if engine is None:
        return False
    return not engine.__file__.endswith(('.py', '.pyc'))


class SourceFinder:
    """
    Finds dotted's own modules as .py source, ahead of any compiled form.
    """
    @staticmethod
    def find_spec(name, path=None, target=None):
        package, _, module = name.rpartition('.')
        if package != __package__:
            return None
        source = os.path.join(os.path.dirname(os.path.abspath(__file__)), module + '.py')
        if not os.path.exists(source):
            return None
        return importlib.util.spec_from_file_location(name, source)


def use_source():
    """
    Have the rest of dotted import from .py source. Call before importing
    any engine module.
    """
    if SourceFinder not in sys.meta_path:
        sys.meta_path.insert(0, SourceFinder)
