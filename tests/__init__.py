"""Test package.

Putting the repository ``src`` directory on ``sys.path`` here lets every
test import ``ai_signal`` without installing the package, regardless of
whether it is run via ``python -m unittest discover`` (which imports this
package) or directly as a script.
"""

import pathlib
import sys

_SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
