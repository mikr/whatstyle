#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (c) 2016 Michael Krause ( http://krause-software.com/ ).
#
# You are free to use this code under the MIT license:
# http://opensource.org/licenses/MIT

# This program looks at source code and generates a style definition for code formatters
# like clang-format, indent, etc. so that the reformatted source matches its current
# formatting as closely as possible.
# It should help programmers to begin using a formatting tool right away
# without the need to invest hours of reading the formatting tool
# documentation while still preserving as much of their existing formatting
# style as possible.
#
# The currently supported formatters are clang-format, YAPF, HTML tidy,
# indent, Artistic Style and uncrustify.
#
# The program basically works by reformatting the source with many
# combinations of options and running a diff between the original source
# and the reformatted source code.
# The program chooses the options resulting in the least amount of changes
# between the source and the reformatted version.
# Among a number of candidate styles with the same diff quality
# the one with the least number of explicit options is chosen
# to keep the style definition brief.
r"""
whatstyle can help you with various tasks regarding source code formatting styles.

Here is a list of examples that you can try out to become familiar with whatstyle, you need
the directory tests/examples in addition to whatstyle.py if you want to try the examples.
Substitute the example sources with your own sources when you are ready.

Basic usage: Find the best style for a source file using a suitable formatter that is
installed on your machine:

    $ ./whatstyle.py tests/examples/xv6/printf.c

Show which formatters are installed on your machine:

    $ ./whatstyle.py --supported          # show all formatters
    $ ./whatstyle.py --supported .m .cpp  # show formatters that support certain extensions

Choosing a specific formatter:

    $ ./whatstyle.py --formatter uncrustify tests/examples/xv6/printf.c

Make the style more resilient against formatting variations, this is advised when the best
style is actually going to be used in a project:

    $ ./whatstyle.py --formatter clang-format --mode resilient tests/examples/lua/lstate.[ch]

Generate an overview of the effects of every option value variation. This shows style
option differences together with the source code differences that they cause.

    $ ./whatstyle.py --variants tests/examples/lua/lstate.[ch]

Remove uninteresting options from the variants:

    $ ./whatstyle.py --variants --ignoreopts ColumnLimit,IndentWidth,UseTab \
                     tests/examples/lua/lstate.[ch]

Show more differences per style variant:

    $ ./whatstyle.py --variants --numhunks 5 tests/examples/lua/lstate.[ch]

Show the variants in a browser:

    $ ./whatstyle.py --variants --html tests/examples/lua/lstate.[ch]

Show the variants with a dark theme in a browser resembling the ANSI terminal output:

    $ ./whatstyle.py --variants --ansihtml tests/examples/lua/lstate.[ch]

You are looking for a specific formatter option without knowing its name. Create a small
file with a code fragment that your current formatter produces but you are unhappy with.
Then create a modified copy (e.g. hello_ref.c) that looks exactly how you would prefer it.
The options below cause whatstyle to find the differences between the style in hello.c
and hello_ref.c that you can add to your style configuration:

    $ ./whatstyle.py --references --mode stylediff tests/examples/ref_space_brace/hello.c \
                                                   tests/examples/ref_space_brace/hello_ref.c

Show the differences between your current sources and these sources reformatted after
computing their best matching style:

    $ ./whatstyle.py --diff tests/examples/xv6/printf.c

You have a code formatter (e.g. clang-format) that supports commonly known styles
(e.g. Mozilla or WebKit) and you'd prefer the closest common style instead of many
individual options:

    $ ./whatstyle.py --maxrounds 1 tests/examples/xv6/printf.c

If you want to help the code formatter developers to reproduce formatter crashes, you could
run several formatters and search for negative return codes, the option '--debug popenio'
tells whatstyle to log the interaction with the formatter processes:

    $ for f in $(./whatstyle.py --supported .c)
        do
          python whatstyle.py --formatter "$f" --mode resilient --cache memory \
            --keeptempfiles --debug popenio tests/examples/xv6/printf.c >> dump.txt
        done ; grep -C 2 "returncode:-" dump.txt

You think 'git diff' can produce superior diffs for the optimization:

    $ ./whatstyle.py --difftool gitdiff tests/examples/xv6/printf.c

"""

from __future__ import print_function

__version__ = '0.1.8'

import sys

if (((sys.version_info[0] == 2) and (sys.version_info[1] < 7)) or (
    (sys.version_info[0] == 3) and (sys.version_info[1] < 2))):
    sys.stderr.write('Error: Python 2.7 or when running on Python 3 at least Python 3.2'
                     ' is required to run whatstyle\n')
    sys.exit(1)

import argparse
import cgi
import codecs
import copy
import difflib
import errno
import hashlib
import heapq
import itertools
import json
try:
    import multiprocessing.pool  # type: ignore
except ImportError:
    multiprocessing = None  # type: Optional[module]

import operator
import os
import re
import signal
import shutil

try:
    import sqlite3
except ImportError:
    sqlite3 = None  # type: ignore

import subprocess
import tempfile
import threading
import time

try:
    from urlparse import urljoin
    from urllib import pathname2url  # type: ignore
except ImportError:
    from urllib.parse import urljoin  # type: ignore
    from urllib.request import pathname2url
import traceback
import types
import warnings
import webbrowser
import zlib

try:
    import xml.etree.cElementTree as ETree
except ImportError:
    import xml.etree.ElementTree as ETree  # type: ignore

from contextlib import contextmanager
from collections import Counter, OrderedDict, defaultdict, namedtuple
from io import BytesIO

try:
    from itertools import izip  # type: ignore
    from itertools import izip_longest  # type: ignore
except ImportError:
    from itertools import zip_longest as izip_longest  # type: ignore
    izip = zip

try:
    from typing import TypeVar
    from typing import Any, AnyStr, Callable, Dict, Generator, Iterator, Iterable, List
    from typing import Optional, Sequence, Text, Tuple, Union, Match, Pattern
    from typing import IO
    TextPair = Tuple[str, str]
    BytesPair = Tuple[bytes, bytes]
    OptionValue = Union[str, bool, int, 'Style']
    Option = Tuple[str, str, List[OptionValue], Optional['StyleDef']]
    StyleDist = Tuple[Optional['Style'], Optional[Sequence[int]]]
    CallArgs = Tuple[Sequence[Any], Dict[Any, Any]]
except ImportError:
    pass

from pprint import pprint

WINOS = os.getenv('OS') == 'Windows_NT'

if WINOS:
    # Enable utf-8 output on Windows
    def codec_search(name):
        if name == 'cp65001':
            return codecs.lookup('utf-8')
        return None

    codecs.register(codec_search)

MAX_FILESIZE_FOR_MULTIPROCESSING = 256 * 1024
TIMEOUT_SECONDS = 30
CONTEXTLINES = 2

LOWER_COLUMN_LIMIT = 79
UPPER_COLUMN_LIMIT = 120

HUGE_DISTANCE = 2**31 - 1
UNLIMITED = -1

USE_THREADS = False
HASHFUNC = hashlib.sha1

OK = 0
ERROR = 1
PARSING_FAILED = 1

STDERR_OUTPUT = False

CEXTS = '.c .h'
CPPEXTS = '.c++ .h++ .cxx .hxx .cpp .hpp .cc .hh'
CPPCEXTS = CEXTS + ' ' + CPPEXTS
SCALAEXTS = '.sc .scala'
REXTS = '.r .R .RData .rds .rda'
RUSTEXTS = '.rs'
SUPPORTED_EXTS = [
    ['clang-format', '.m .mm .java .js .ts .proto .protodevel .td ' + CPPCEXTS],
    ['yapf', '.py'],
    ['uncrustify', '.cs .m .mm .d .java .p .pawn .sma .vala .sqc ' + CPPCEXTS],
    ['astyle', '.m .java ' + CPPCEXTS],
    ['indent', '.c .h'],
    ['tidy', '.html .htm'],
    ['scalariform', SCALAEXTS],
    ['scalafmt', SCALAEXTS],
    ['rfmt', REXTS],
    ['rustfmt', RUSTEXTS],
]

FILENAME_SUBST = '#FILENAME#'
DIFF_SPECS = [
    # difftoolname, executable, command line arguments
    ('difflib', sys.executable, ['-u', __file__, '--stdindiff', '--', FILENAME_SUBST]),
    ('diff', 'diff', ['--text', '--unified=0', '--', FILENAME_SUBST, '-']),
    ('gitdiff', 'git', ['--no-pager', 'diff', '--text', '--no-ext-diff', '--no-index',
                        '--unified=0', '--', FILENAME_SUBST, '-']),
]
BUILTIN_DIFF = DIFF_SPECS[-1]

PAGER_SPECS = [('less', ['-F', '-r', '-S', '-X']),
               ('more', [])]  # type: List[Tuple[str, List[str]]]

STTY_CMD = '/bin/stty'

# We use a "Hello, World!" source to which we apply some modifications
# to check for the presence of a usable diff tool.
HELLOWORLD = """\
#include <stdio.h>
int main(int argc, char *argv[]) {
    printf("Hello, World!\n");
    return 0;
}
"""

LANG_OBJECTIVE_C = 'Objective-C'

OPTION_PRESENT = '<True>'

MODE_NORMAL = 'normal'
MODE_MAXDIFF = 'maxdiff'
MODE_RESILIENT = 'resilient'
MODE_STYLEDIFF = 'stylediff'
MODE_MINIMIZE = 'minimize'
MODE_MAXIMIZE = 'maximize'

METRIC_MINDIFF = 0
METRIC_MAXDIFF = 1
METRIC_MIN = 2
METRIC_MAX = 3

CC_OFF = 'off'
CC_THREADS = 'threads'
CC_PROCESSES = 'processes'

GLOBALTMP = 1
LOCALTMP = 0

args_info = set()  # type: Set[str]
args_debug = set()  # type: Set[str]
args_verbose = set()  # type: Set[str]

LOGFILE = None  # type: Optional[str]
LOGFILEFP = None  # type: Optional[IO[Any]]
LOGSPLITDIR = None  # type: Optional[str]
MESSAGE_CATEGORY_FILES = None  # type: Optional[Dict[str, IO[Any]]]

NO_PROGRESS = False

# ----------------------------------------------------------------------

INFO_RESULT = 'result'
INFO_USER = 'user'
INFO_HEURISTICS = 'heuristics'
INFO_TIME = 'time'
INFO_PERF = 'perf'
INFO_ATTEMPT = 'attempt'
INFO_SKIP = 'skip'
INFO_INVALIDS = 'invalids'
INFO_PROCERRORS = 'procerrors'
INFO_PROCEXCEPTIONS = 'procexc'
# INFO_TIME is not included in INFO_ALL to produce easily diffable debug output
# between different runs of whatstyle.
INFO_ALL = [INFO_RESULT, INFO_USER, INFO_HEURISTICS, INFO_PERF, INFO_ATTEMPT, INFO_SKIP,
            INFO_INVALIDS, INFO_PROCERRORS, INFO_PROCEXCEPTIONS]
INFO_IMPLIES_DICT = {}  # type: Dict[str, List[str]]

DEBUG_OPTIONS = 'options'
DEBUG_STYLEDEF = 'styledef'
DEBUG_POPEN = 'popen'
DEBUG_POPENIO = 'popenio'
DEBUG_ALL = [DEBUG_OPTIONS, DEBUG_STYLEDEF, DEBUG_POPEN, DEBUG_POPENIO]
DEBUG_RUNTIME = 'runtime'

DEBUG_IMPLIES_DICT = {DEBUG_POPENIO: [DEBUG_POPEN]}  # type: Dict[str, List[str]]

verbose_categories = {0: [INFO_USER],
                      1: [INFO_HEURISTICS, INFO_TIME, INFO_PERF],
                      2: [INFO_ATTEMPT, INFO_SKIP, INFO_PROCERRORS, INFO_PROCEXCEPTIONS],
                      3: [DEBUG_OPTIONS, DEBUG_STYLEDEF],
                      4: [DEBUG_POPEN]}

# ----------------------------------------------------------------------
# yapf: disable
# ----------------------------------------------------------------------
# Some functions from the MIT licensed six (https://pypi.python.org/pypi/six/)
# Copyright (c) 2010-2016 Benjamin Peterson <benjamin@python.org>

PY2 = sys.version_info[0] == 2
PY3 = sys.version_info[0] == 3

if PY3:
    string_types = str,
    integer_types = int,
    class_types = type,
    text_type = str
    binary_type = bytes
else:
    string_types = basestring,
    integer_types = (int, long)
    class_types = (type, types.ClassType)
    text_type = unicode
    binary_type = str

if PY3:
    import builtins
    exec_ = getattr(builtins, "exec")

    def reraise(tp, value, tb=None):
        try:
            if value is None:
                value = tp()
            if value.__traceback__ is not tb:
                raise value.with_traceback(tb)
            raise value
        finally:
            value = None
            tb = None

else:

    def exec_(_code_, _globs_=None, _locs_=None):
        """Execute code in a namespace."""
        if _globs_ is None:
            frame = sys._getframe(1)
            _globs_ = frame.f_globals
            if _locs_ is None:
                _locs_ = frame.f_locals
            del frame
        elif _locs_ is None:
            _locs_ = _globs_
        exec ("""exec _code_ in _globs_, _locs_""")

    exec_("""def reraise(tp, value, tb=None):
    try:
        raise tp, value, tb
    finally:
        tb = None
""")

if sys.version_info[:2] == (3, 2):
    exec_("""def raise_from(value, from_value):
    try:
        if from_value is None:
            raise value
        raise value from from_value
    finally:
        value = None
""")
elif sys.version_info[:2] > (3, 2):
    exec_("""def raise_from(value, from_value):
    try:
        raise value from from_value
    finally:
        value = None
""")
else:

    def raise_from(value, from_value):
        raise value


if PY3:
    unichr = chr
    import struct
    int2byte = struct.Struct(">B").pack
else:
    unichr = unichr
    int2byte = chr

# yapf: enable
# ----------------------------------------------------------------------

try:
    NARROW_BUILD = len(unichr(0x1f300)) == 2
except ValueError:
    NARROW_BUILD = True


def safe_unichr(codepoint):
    # type: (int) -> str
    if not NARROW_BUILD or codepoint < 0x10000:
        return unichr(codepoint)
    # create a surrogate pair
    codepoint -= 0x10000
    return unichr(0xd800 + (codepoint >> 10)) + unichr(0xdc00 + (codepoint & 0x3ff))


HAS_FSCODEC = hasattr(os, 'fsdecode')


def unistr(text, errors='strict'):
    # type: (Union[str, bytes], str) -> str
    if isinstance(text, text_type):
        return text
    try:
        return text.decode('utf-8', errors=errors)
    except UnicodeDecodeError:
        if HAS_FSCODEC:
            return os.fsdecode(text)
        raise


def bytestr(text):
    # type: (Union[str, bytes]) -> bytes
    if isinstance(text, binary_type):
        return text
    try:
        return text.encode('utf-8')
    except UnicodeEncodeError:
        if HAS_FSCODEC:
            return os.fsencode(text)
        raise


def unifilename(filename):
    # type: (Union[str, bytes]) -> str
    if isinstance(filename, text_type):
        return filename
    try:
        return filename.decode(sys.getfilesystemencoding())
    except UnicodeDecodeError:
        try:
            return filename.decode('utf-8')
        except UnicodeDecodeError:
            if HAS_FSCODEC:
                return os.fsdecode(filename)
            else:
                raise


def sysfilename(filename):
    # type: (Union[str, bytes]) -> bytes
    if not isinstance(filename, text_type):
        return filename
    try:
        return filename.encode(sys.getfilesystemencoding())
    except UnicodeEncodeError:
        try:
            return filename.encode('utf-8')
        except UnicodeEncodeError:
            if HAS_FSCODEC:
                return os.fsencode(filename)
            else:
                raise


if os.sep != '/':

    def normsep(s):
        return s.replace('/', os.sep)
else:

    def normsep(s):
        return s


if NARROW_BUILD:
    # When we are running on a narrow Python build
    # we have to deal with surrogate pairs ourselves.
    def iterchars(text):
        # type: (str) -> Sequence[str]
        idx = 0
        chars = []
        while idx < len(text):
            c = text[idx]
            if ord(c) >= 0x100:
                highchar = True
                if ((0xD800 <= ord(c) <= 0xDBFF) and (idx < len(text) - 1) and
                    (0xDC00 <= ord(text[idx + 1]) <= 0xDFFF)):
                    c = text[idx:idx + 2]
                    # Skip the other half of the lead and trail surrogate
                    idx += 1
            else:
                highchar = False
            idx += 1
            # Add every character except only one half of a surrogate pair.
            if not (highchar and len(c) == 1 and 0xD800 <= ord(c) <= 0xDFFF):
                chars.append(c)
        return chars

    def unilen(text):
        # type: (str) -> int
        return len(iterchars(text))

else:

    def iterchars(text):
        # type: (str) -> Sequence[str]
        return text

    def unilen(text):
        # type: (str) -> int
        return len(text)


if PY3:
    uniord = ord
else:

    def uniord(s):
        """ord that also works on surrogate pairs.
        """
        if not isinstance(s, text_type):
            raise TypeError('uniord() expected a unicode character or string of length 2,'
                            ' but %s found' % type(s).__name__)
        try:
            return ord(s)
        except TypeError:
            if len(s) != 2:
                raise TypeError('uniord() expected a character or string of length 2,'
                                ' but string of length %d found' % len(s))
        return 0x10000 + ((ord(s[0]) - 0xd800) << 10) | (ord(s[1]) - 0xdc00)

# ----------------------------------------------------------------------
# win32_unicode_argv.py
# Importing this will replace sys.argv with a full Unicode form.
# Windows only.
# From this site, with adaptations:
#     http://stackoverflow.com/questions/846850/read-unicode-characters-from-command-line-arguments-in-python-2-x-on-windows
#     http://code.activestate.com/recipes/572200/
# License:
#     'New BSD license'
#     http://community.activestate.com/faq/what-license-code-languag


def win32_unicode_argv():
    # type: () -> List[str]
    """Uses shell32.GetCommandLineArgvW to get sys.argv as a list of Unicode
    strings.
    Versions 2.x of Python don't support Unicode in sys.argv on
    Windows, with the underlying Windows API instead replacing multi-byte
    characters with '?'.
    """
    from ctypes import POINTER, byref, cdll, c_int, windll
    from ctypes.wintypes import LPCWSTR, LPWSTR

    GetCommandLineW = cdll.kernel32.GetCommandLineW
    GetCommandLineW.argtypes = []
    GetCommandLineW.restype = LPCWSTR

    CommandLineToArgvW = windll.shell32.CommandLineToArgvW
    CommandLineToArgvW.argtypes = [LPCWSTR, POINTER(c_int)]
    CommandLineToArgvW.restype = POINTER(LPWSTR)

    cmd = GetCommandLineW()
    argc = c_int(0)
    argv = CommandLineToArgvW(cmd, byref(argc))
    if argc.value > 0:
        # Remove Python executable and commands if present
        start = argc.value - len(sys.argv)
        return [argv[i] for i in range(start, argc.value)]

# ----------------------------------------------------------------------

re_keyvalue = re.compile(r'(\w+)\s*=\s*(.*)')


def parse_keyvalue_pairs(styledump):
    for line in styledump.splitlines():
        if line.startswith('#'):
            continue
        m = re_keyvalue.match(line)
        if m:
            k, v = m.groups()
            yield k, v.rstrip()

# ----------------------------------------------------------------------

ExcInfo = namedtuple('ExcInfo', 'exc_info')
FileMeta = namedtuple('FileMeta', ['filename', 'size', 'mtime', 'sha', 'version'])
ExeCall = namedtuple('ExeCall', ['exe', 'cmdargs', 'stdindata', 'depfiles'])
ExeResult = namedtuple('ExeResult', ['returncode', 'stdout', 'stderr', 'runtime', 'timedout',
                                     'error'])


def make_execall(exe, cmdargs, stdindata=None, depfiles=()):
    # type: (str, Sequence[str], Optional[bytes], Sequence[str]) -> ExeCall
    """If the result of a subprocess call depends on the content of files, their
    filenames must be specified as depfiles to prevent stale cache results.
    """
    return ExeCall(exe, cmdargs, stdindata, depfiles)


def make_exeresult(returncode=0,    # type: int
                   stdout=None,     # type: bytes
                   stderr=None,     # type: bytes
                   runtime=0.0,     # type: float
                   timedout=False,  # type: bool
                   error=None       # type: Optional[str]
                   ):
    # type: (...) -> ExeResult
    return ExeResult(returncode, stdout, stderr, runtime, timedout, error)


ParameterSet = namedtuple('ParameterSet', ['formatter', 'difftool', 'mode', 'sourcefactor',
                                           'variantsfactor', 'references', 'maxrounds',
                                           'ignoreopts', 'bestofround', 'concat', 'ccmode'])

FormatOption = namedtuple('FormatOption', ['opts'])

FormatStyleAttempt = namedtuple('FormatStyleAttempt',
                                ['formatstyle', 'newoptions', 'prevdist'])
AttemptResult = namedtuple('AttemptResult', ['distance', 'formatstyle'])

# ----------------------------------------------------------------------
# If we would use any Python packages outside the standard library we could
# use colorama as well. But we don't so we include limited ANSI color support here.

COLOR_SUPPORT = None  # type: Optional[bool]

BLACK, RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, WHITE = range(30, 38)
BACKGROUNDCOLOR_OFFSET = 10


def supports_color():
    # type: () -> bool
    global COLOR_SUPPORT
    if COLOR_SUPPORT is None:
        # On Windows colors are only supported in ConEmu or similar.
        if WINOS and os.getenv('ANSICON') is None:
            COLOR_SUPPORT = False
        else:
            COLOR_SUPPORT = True
    return COLOR_SUPPORT


def ansicolor(col, text):
    # type: (int, str) -> str
    if not text:
        return text
    return '\x1b[%sm%s\x1b[m' % (col, text)


def color(col, text):
    # type: (int, str) -> str
    if not text or not supports_color():
        return text
    return ansicolor(col, text)


def bgcolor(col, text):
    # type: (int, str) -> str
    return color(col + BACKGROUNDCOLOR_OFFSET, text)


def red(text):
    # type: (str) -> str
    return color(RED, text)


def green(text):
    # type: (str) -> str
    return color(GREEN, text)


def yellow(text):
    # type: (str) -> str
    return color(YELLOW, text)


def blue(text):
    # type: (str) -> str
    return color(BLUE, text)


def magenta(text):
    # type: (str) -> str
    return color(MAGENTA, text)


def cyan(text):
    # type: (str) -> str
    return color(CYAN, text)


def white(text):
    # type: (str) -> str
    return color(WHITE, text)

# ----------------------------------------------------------------------


def progresspair(num, total):
    # type: (int, int) -> str
    totaltext = text_type(total)
    numtext = text_type(num)
    spaces = len(totaltext) - len(numtext)
    text = ' ' * spaces + numtext + '/' + totaltext
    if num == total:
        text = green(text)
    else:
        text = yellow(text)
    return text


def reporting_progress():
    # type: () -> bool
    return INFO_USER in args_info and sys.stdout.isatty() and not NO_PROGRESS


def report_text(text, prev=''):
    # type: (str, str) -> str
    if not reporting_progress():
        return ''
    newwidth = len(text)
    widthdiff = len(prev) - newwidth
    if widthdiff > 0:
        text += ' ' * widthdiff
    write(text)
    sys.stdout.flush()
    return text


def report_progress(text, fmtnr=0, fmtcount=0, diffnr=None, diffcount=None, prev=''):
    # type: (str, int, int, Optional[int], Optional[int], str) -> str
    if not reporting_progress():
        return ''
    text = '\r%s#formatted %s' % (text, progresspair(fmtnr, fmtcount))
    if diffnr is not None and diffcount is not None:
        text += '  #compared %s' % progresspair(diffnr, diffcount)
    return report_text(text, prev=prev)

# ----------------------------------------------------------------------


def copyfile(src, dst):
    try:
        os.makedirs(os.path.dirname(dst))
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise
    shutil.copyfile(src, dst)


def rawstream(fp):
    # type: (IO[Any]) -> IO[bytes]
    if PY3:
        try:
            return fp.buffer  # type: ignore
        except AttributeError:
            # There might be a BytesIO behind fp.
            pass
    return fp  # type: Optional[IO[bytes]]


def write(s, fp=None):
    # type: (Union[str, bytes], Optional[IO[Any]]) -> None
    """Write s to the binary stream fp (default is stdout).
    """
    efp = fp if fp is not None else sys.stdout
    rawstream(efp).write(bytestr(s))


def outline(s=b'', end=b'\n', fp=None):
    # type: (Union[str, bytes], Union[str, bytes], Optional[IO]) -> None
    write(bytestr(s) + bytestr(end), fp=fp)


def category_print(categories, categorytype, category, s, prefix='', end='\n', fp=None):
    # type: (Set[str], str, str, Union[str, bytes], str, str, Optional[IO]) -> None
    if category not in categories:
        return
    if categorytype == 'info':
        msg = prefix
    else:
        msg = '%s%s_%s: ' % (prefix, categorytype, category)
    if MESSAGE_CATEGORY_FILES is not None:
        logfilename = 'whatstyle_%s_%s.log' % (categorytype, category)
        fp = MESSAGE_CATEGORY_FILES.get(logfilename)
        if fp is None:
            path = os.path.join(tempfile.gettempdir(), logfilename)
            fp = open(path, 'wb')
            MESSAGE_CATEGORY_FILES[logfilename] = fp
    if fp is None and LOGFILE:
        global LOGFILEFP
        if not LOGFILEFP:
            LOGFILEFP = open(LOGFILE, 'wb')
        fp = LOGFILEFP
    if fp is None:
        fp = rawstream(sys.stderr if STDERR_OUTPUT else sys.stdout)
    write(msg, fp=fp)
    write(s, fp=fp)
    if end:
        write(end, fp=fp)


def iprint(category, s, prefix='', end='\n', fp=None):
    # type: (str, AnyStr, str, str, Optional[IO[AnyStr]]) -> None
    category_print(args_info, 'info', category, s, prefix, end, fp=fp)


def dprint(category, s, prefix='', end='\n', fp=None):
    # type: (str, AnyStr, str, str, Optional[IO[AnyStr]]) -> None
    category_print(args_debug, 'debug', category, s, prefix, end, fp=fp)


reportmessage = outline


def reporterror(s, fp=None):
    # type: (str, Optional[IO[AnyStr]]) -> None
    if fp is None:
        fp = rawstream(sys.stderr)  # type: ignore
    reportmessage(s, fp=fp)


reportwarning = reporterror


def alignedblocks(left, right, separator='   ', color_left=None, color_right=None):
    # type: (str, str, str, Optional[int], Optional[int]) -> str
    """Create a single text block with a separator symbol between the two texts
    left and right.
    Both sides can be colored differently.
    """
    left_lines = left.splitlines()
    right_lines = right.splitlines()
    maxwidth = max(len(line) for line in left_lines)
    numlines = max(len(left_lines), len(right_lines))
    left_lines = left.splitlines()
    lines = []
    for i in range(numlines):
        l = left_lines.pop(0) if left_lines else ''
        r = right_lines.pop(0) if right_lines else ''
        spc = ' ' * (maxwidth - len(l))
        l += spc
        if color_left is not None:
            l = color(color_left, l)
        if color_right is not None:
            r = color(color_right, r)
        lines.append(l + separator + r)
    return '\n'.join(lines)

# ----------------------------------------------------------------------
# http://stackoverflow.com/questions/312443/
#        how-do-you-split-a-list-into-evenly-sized-chunks-in-python


def grouper(n, iterable, padvalue=None):
    # type: (int, Iterable[Any], Any) -> Iterable[Iterable[Any]]
    """grouper(3, 'abcdefg', 'x') --> ('a','b','c'), ('d','e','f'), ('g','x','x')"""
    return izip_longest(*[iter(iterable)] * n, fillvalue=padvalue)

# ----------------------------------------------------------------------
# Functions to find an executable in the PATH, from:
# http://stackoverflow.com/questions/377017/test-if-executable-exists-in-python


def exename(program):
    # type: (str) -> str
    if WINOS and not program.lower().endswith('.exe'):
        program += '.exe'
    return program


def is_executable(filename):
    # type: (str) -> bool
    return os.path.isfile(filename) and os.access(filename, os.X_OK)


def which(program):
    # type: (str) -> Optional[str]
    program = exename(program)
    fpath, _ = os.path.split(program)
    if fpath:
        if is_executable(program):
            return program
    else:
        for path in [os.path.abspath(os.curdir)] + os.environ['PATH'].split(os.pathsep):
            path = path.strip('"')
            exe_file = os.path.join(unifilename(path), unifilename(program))
            if is_executable(exe_file):
                return exe_file

    return None

# ----------------------------------------------------------------------


def systempager_cmdargs():
    # type: () -> List[str]
    for name, args in PAGER_SPECS:
        path = which(name)
        if path is not None:
            return [path] + list(args)
    return []

# ----------------------------------------------------------------------
#
# Functions to support multiprocessing
#


def per_process_init():
    # type: () -> None
    try:
        os.nice(19)
    except AttributeError:
        # nice is not available everywhere.
        pass
    except OSError:
        # When this program is already running on the nicest level (20) on OS X
        # it is not permitted to change the priority.
        pass
    # A keyboard interrupt disrupts the communication between a
    # Python script and its subprocesses when using multiprocessing.
    # The child can ignore SIGINT and is properly shut down
    # by a pool.terminate() call in case of a keyboard interrupt
    # or an early generator exit.
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def tracebackwrapper(func, args, kwargs):
    # type: (Callable[..., Any], List[Any], Dict[Any, Any]) -> Any
    try:
        return func(*args, **kwargs)
    except Exception as e:
        e.traceback = traceback.format_exc()  # type: ignore
        raise


def iter_parallel_report(func,  # type: Callable[..., Any]
                         args_lists,  # type: Sequence[CallArgs]
                         ccmode=CC_PROCESSES):
    # type: (...) -> Iterator[Union[ExeResult, ExcInfo]]
    if ccmode == CC_OFF or len(args_lists) <= 1 or not multiprocessing:
        for args, kwargs in args_lists:
            yield func(*args, **kwargs)
        return

    processes = min(len(args_lists), multiprocessing.cpu_count())
    if ccmode == CC_THREADS:
        pool = multiprocessing.pool.ThreadPool(processes=processes)
    else:
        pool = multiprocessing.Pool(processes=processes, initializer=per_process_init)
    try:
        async_results = [pool.apply_async(func, args=args, kwds=kwargs)
                         for args, kwargs in args_lists]
        pool.close()
        while async_results:
            try:
                asyncres = async_results.pop(0)
                yield asyncres.get()
            except (KeyboardInterrupt, GeneratorExit):
                raise
            except Exception as e:
                t, v, tb = sys.exc_info()
                try:
                    # Report the textual traceback of the subprocess rather
                    # than this local exception which was triggered
                    # by the other side.
                    tb = e.traceback  # type: ignore
                except AttributeError:
                    pass
                yield ExcInfo((t, v, tb))
    except GeneratorExit:
        pool.terminate()
    except KeyboardInterrupt:
        pool.terminate()
        raise
    finally:
        pool.join()


def iter_parallel(func,        # type: Callable
                  args_lists,  # type: Sequence[CallArgs]
                  ccmode=CC_PROCESSES):
    # type: (...) -> Iterator[Any]
    if not args_lists:
        return
    if ccmode != CC_OFF:
        args_lists = [((func, args, kwargs), {}) for args, kwargs in args_lists]
        wrappedfunc = tracebackwrapper
    else:
        wrappedfunc = func

    for result in iter_parallel_report(wrappedfunc, args_lists, ccmode=ccmode):
        if ccmode == CC_OFF:
            yield result
        else:
            tbtext = None
            try:
                if isinstance(result, ExcInfo):
                    t, v, tb = result.exc_info
                    if not isinstance(tb, types.TracebackType):
                        tbtext = tb
                        tb = None
                    reraise(t, v, tb)
                else:
                    yield result
            except Exception:
                if tbtext is not None:
                    raise Exception(tbtext)
                else:
                    traceback.print_exc()
                    raise

# ----------------------------------------------------------------------
# The data types option and style.


def option_make(optionname,      # type: AnyStr
                optiontype,      # type: AnyStr
                configs,         # type: Iterable[OptionValue]
                nestedopts=None  # type: Optional[StyleDef]
                ):
    # type: (...) -> Tuple[str, str, List[OptionValue], Optional[StyleDef]]
    configs = [typeconv(c) for c in configs]
    return unistr(optionname), unistr(optiontype), configs, nestedopts


def option_name(option):
    # type: (Option) -> str
    return option[0]


def option_type(option):
    # type: (Option) -> str
    return option[1]


def option_configs(option):
    # type: (Option) -> List[OptionValue]
    return option[2]


def option_nestedstyle(option):
    # type: (Option) -> Optional[StyleDef]
    return option[3]


class StyleDef(OrderedDict):
    pass


def styledef_make(options=None):
    # type: (Union[dict, Iterable[Option], None]) -> StyleDef
    if isinstance(options, dict):
        s = styledef_make()
        for _, option in sorted(options.items()):
            if isinstance(option, dict):
                option = styledef_make(option)
            styledef_add_option(option, s)
        return s
    if options is None:
        options = []
    return StyleDef((option_name(o), o) for o in options)


class Style(OrderedDict):
    pass


def style_make(options=None):
    # type: (Union[dict, List[Tuple[str, OptionValue]], None]) -> Style
    if options is None:
        return Style()
    if isinstance(options, dict):
        s = style_make()
        for k, v in sorted(options.items()):
            if isinstance(v, dict):
                v = style_make(v)
            set_option(s, k, v)
        return s
    raise TypeError('options must be a dict or None')


def styledef_copy(styledef):
    # type: (StyleDef) -> StyleDef
    return styledef_make(styledef_options(styledef))


def styledef_options(styledef):
    # type: (StyleDef) -> List[Option]
    return list(styledef.values())


def styledef_option(styledef, optionname):
    # type: (StyleDef, str) -> Optional[Option]
    if not isinstance(styledef, StyleDef):
        raise TypeError('styledef must be a StyleDef, got %r instead' % styledef)
    return styledef.get(optionname)


def styledef_add_option(option, styledef):
    # type: (Option, StyleDef) -> None
    styledef[option_name(option)] = option


def styledef_delete_option(optionname, styledef):
    # type: (str, StyleDef) -> None
    del styledef[optionname]

# ---------------------------------------------------------------------


def shadigest(data):
    # type: (bytes) -> bytes
    return HASHFUNC(data).digest()


def shahex(data):
    # type: (bytes) -> str
    return HASHFUNC(data).hexdigest()


def inclusiverange(start, stop):
    # type: (int, int) -> Iterable[int]
    return range(start, stop + 1)


def textrepr(value):
    # type: (Any) -> str
    if isinstance(value, type(True)):
        value = 'true' if value else 'false'
    return text_type(value)


def normrepr(style):
    # type: (Style) -> str
    """Return a YAML-like JSON representation of a style.

    This function is also used to get a hashable key from a style.

    >>> print(normrepr(style_make({'BraceWrapping': {'AfterClass': 'false'},\
                       'BreakBeforeBraces': 'Custom'})))
    {BraceWrapping: {AfterClass: false}, BreakBeforeBraces: Custom}
    """
    if not isinstance(style, Style):
        raise TypeError
    fragments = []
    for key in sorted(style.keys()):
        value = style[key]
        if isinstance(value, Style):
            value = normrepr(value)
        else:
            value = textrepr(value)
        fragments.append(key + ': ' + value)
    result = text_type('{' + ', '.join(fragments) + '}')
    return result


def stylevariant(optionname, value):
    # type: (str, OptionValue) -> Style
    return style_make({optionname: typeconv(value)})


def stylevariants(optionname, values):
    # type: (str, Iterable[OptionValue]) -> List[Style]
    return [stylevariant(optionname, v) for v in values]


re_number = re.compile(r'-?[0-9]+$')


def typeconv(obj):
    # type: (OptionValue) -> OptionValue
    """If obj is a string that looks like number or boolean value, its value is returned.
    Binary strings are returned as unicode strings, everything else is unchanged.
    """
    if not isinstance(obj, string_types):
        return obj
    if obj == 'true':
        return True
    if obj == 'false':
        return False
    if isinstance(obj, string_types):
        text = unistr(obj)  # type: str
        if re_number.match(text):
            return int(text)
    if isinstance(obj, binary_type):
        text = unistr(obj)
        return text
    return obj


def set_option(style, optionname, optionvalue):
    # type: (Style, str, OptionValue) -> None
    """Sets the normalized option value in style.
    """
    style[unistr(optionname)] = typeconv(optionvalue)


class CodeFormatter(object):
    """Baseclass for all formatters.
    """
    shortname = ''
    alternative_names = []  # type: List[str]
    _prefer_basestyle = False
    base_optionname = ''
    invalid_enums = {}  # type: Dict[str, Set[str]]
    columnlimitname = None  # type: str
    configfilename = None  # type: str
    styledump_argument = None  # type: str

    def __init__(self, exe, cache=None):
        # type: (str, Optional[Cache]) -> None
        if not os.path.isabs(exe):
            exe = which(exe)  # type: ignore
        self.exe = unifilename(exe)
        self.cache = cache
        self._styledefinition = styledef_make()
        self.allow_encoding_change = False
        self.languages = []  # type: List[str]

        self.initial_style = style_make()
        # The are deleted after one call to minimize_errors
        self.globaltempfiles = set()  # type: Set[str]
        # These are deleted after each round of attempts
        self.tempfiles = set()  # type: Set[str]
        self.keeptempfiles = False
        self.version_string = formatter_version(exe)

    def register_options(self):
        raise NotImplementedError

    @property
    def prefer_basestyle(self):
        # type: () -> bool
        return self._prefer_basestyle

    def identify_language(self, filenames=(), language=None):
        # type: (Sequence[str], Optional[str]) -> None
        pass

    @classmethod
    def executable_names(cls):
        # type: () -> List[str]
        return [cls.shortname] + cls.alternative_names

    @property
    def styledefinition(self):
        # type: () -> StyleDef
        return self._styledefinition

    @styledefinition.setter
    def styledefinition(self, styledef):
        # type: (StyleDef) -> None
        self._styledefinition = styledef
        if self._styledefinition is None:
            return
        if DEBUG_STYLEDEF not in args_debug:
            return
        dprint(DEBUG_STYLEDEF, '# Style definition for "%s"' % unifilename(self.exe))
        dprint(DEBUG_STYLEDEF, self.styledef_text(self.styledefinition))
        dprint(DEBUG_STYLEDEF, '##########')

    def use_startstyle(self, inlinestyletext):
        # type: (Optional[str]) -> None
        if inlinestyletext:
            # Transform YAML-like JSON
            # e.g. '{based_on_style: pep8, column_limit: 79}'
            # into '{"based_on_style": "pep8", "column_limit": 79}'
            # which can be parsed as JSON.
            inlinestyletext = re.sub(r'([a-zA-Z_]\w+)', r'"\1"', inlinestyletext)
            d = json.JSONDecoder().decode(inlinestyletext)  # type: ignore
            self.initial_style = style_make(d)

    def styledef_text(self, styledef):
        # type: (StyleDef) -> str
        options = styledef_options(styledef)
        fragments = []
        for option in options:
            optionname = option_name(option)
            optiontype = option_type(option)
            configs = option_configs(option)
            nested = option_nestedstyle(option)
            if isinstance(nested, StyleDef):
                fragments.append('%s %s' % (optionname, optiontype))
                text = self.styledef_text(nested)
                for line in text.splitlines():
                    fragments.append('    %s' % line)
            else:
                configtext = ''
                if configs:
                    configtext = ' [%s]' % ', '.join([textrepr(c) for c in configs])
                fragments.append('%s %s%s' % (optionname, optiontype, configtext))
        return '\n'.join(fragments) + '\n'

    def is_baseformat(self, name):
        # type: (str) -> bool
        return name.lower() == self.base_optionname.lower()

    def contains_major_style(self, optiongroup):
        # type: (Style) -> bool
        if not isinstance(optiongroup, Style):
            raise TypeError()
        if not self.prefer_basestyle:
            return False
        for optname in optiongroup:
            if self.is_baseformat(optname):
                return True
        return False

    def sorted_style(self, style):
        # type: (Style) -> Style
        """Return the same style with the major style option appearing first.
        """
        newstyle = style_make()
        items = sorted(style.items())
        for optionname, value in items:
            if self.prefer_basestyle and self.is_baseformat(optionname):
                set_option(newstyle, optionname, value)
        for optionname, value in items:
            if not (self.prefer_basestyle and self.is_baseformat(optionname)):
                set_option(newstyle, optionname, value)
        return newstyle

    def effective_style(self, style):
        # type: (Style) -> Style
        return style_make()

    def style_dump(self, style):
        # type: (Style) -> Optional[str]
        """Return a dump of style options from the formatter given style.
        """
        assert isinstance(style, Style)
        if self.styledump_argument is None:
            return None
        cmdargs = self.cmdargs_for_style(style)
        cmdargs.append(self.styledump_argument)
        res = run_executable(self.exe, cmdargs)
        if res.returncode != 0 or res.stderr:
            return None
        return unistr(res.stdout)

    def attempt_acceptible(self, roundnr, prevdist, newdist):
        # type: (int, List[int], List[int]) -> bool
        """Increasing the constant values results in more attempts and less local minima.
        """
        if roundnr >= 3 and newdist > prevdist:
            # Makes things worse
            return False
        if roundnr >= 3 and newdist >= prevdist:
            # Does not improve things
            return False
        return True

    def complexity(self, style, toplevel=True):
        # type: (Style, bool) -> int
        """The complexity is the number of style options times two.
        We prefer an explicit major style, e.g. {BasedOnStyle: LLVM} over the
        effectively equivalent style {}.
        """
        c = 0
        values = list(style.values())  # type: List[OptionValue]
        for value in values:
            c += 2
            if isinstance(value, Style):
                c += self.complexity(value, toplevel=False)
        if toplevel and self.prefer_basestyle and not self.contains_major_style(style):
            # Consider the absence of a major style to be more complex that its presence
            # but less complex than one with two added style options.
            c += 3
        return c

    def nested_derivations(self, style):
        # type: (Style) -> List[Style]
        return []

    def is_valid_enumvalue(self, optionname, enumvalue):
        # type: (str, Any) -> bool
        invalids = self.invalid_enums.get(optionname)
        return invalids is None or enumvalue not in invalids

    def remove_invalid_options(self, styledef):
        # type: (StyleDef) -> StyleDef
        if not self.invalid_enums:
            return styledef
        newstyle = styledef_make()
        for option in styledef_options(styledef):
            optionname = option_name(option)
            if optionname not in self.invalid_enums:
                styledef_add_option(option, newstyle)
            else:
                optiontype = option_type(option)
                configs = option_configs(option)
                invalid_enumvalues = self.invalid_enums[optionname]
                configs = [c for c in configs if c not in invalid_enumvalues]
                styledef_add_option(option_make(optionname, optiontype, configs), newstyle)
        return newstyle

    def reporterrors(self, job, jobres):
        # type: (ExeCall, ExeResult) -> None
        if not self.should_report_error(job, jobres):
            return
        category = INFO_PROCERRORS
        if jobres.error is not None:
            iprint(category, red("Error: calling %s caused this error: %s" % (job.exe,
                                                                              jobres.error)))
        else:
            iprint(category, red("Error: %s returned code %s" % (job.exe, jobres.returncode)))
        iprint(category, "  for these arguments: %s" % colored_cmdargs(job.cmdargs, RED))
        if jobres.stderr:
            text = jobres.stderr
            try:
                text = unistr(text)
            except UnicodeDecodeError:
                pass
            iprint(INFO_PROCERRORS, 'formatter stderr:"""\\\n%s"""' % red(text))

    def should_report_error(self, job, jobres):
        # type: (ExeCall, ExeResult) -> bool
        return jobres.error is not None or (jobres.returncode != 0) or jobres.stderr

    def valid_job_result(self, job, jobres):
        # type: (ExeCall, ExeResult) -> bool
        return jobres.error is None and jobres.returncode == 0 and not jobres.stderr

    def invalid_cmdline_option(self, job, jobres):
        # type: (ExeCall, ExeResult) -> bool
        return False

    def extra_penalty(self, style, complexity):
        # type: (Any, int) -> Tuple[int, int]
        return complexity, 0

    def variants_for(self, option):
        raise NotImplementedError

    @property
    def column_limit_candidates(self):
        r = set(list(inclusiverange(LOWER_COLUMN_LIMIT, UPPER_COLUMN_LIMIT)))
        r.add(0)
        r = sorted(list(r))
        return r

    def inlinestyletext(self, formatstyle):
        # type: (Style) -> str
        return normrepr(formatstyle)

    def tempfiles_for_mode(self, mode):
        # type: (int) -> Set[str]
        if mode == LOCALTMP:
            return self.tempfiles
        else:
            return self.globaltempfiles

    def add_tempfile(self, tmpfilename, mode=LOCALTMP):
        # type: (str, int) -> None
        self.tempfiles_for_mode(mode).add(tmpfilename)

    def remove_tempfiles(self, mode=None):
        # type: (Optional[int]) -> None
        if self.keeptempfiles:
            return
        if mode is None:
            self.remove_tempfiles(LOCALTMP)
            self.remove_tempfiles(GLOBALTMP)
            return
        files = self.tempfiles_for_mode(mode)
        dirs = set()
        while files:
            filename = files.pop()
            if os.path.isdir(filename):
                dirs.add(filename)
                continue
            try:
                os.remove(filename)
            except OSError as e:
                if e.errno != errno.ENOENT:
                    reporterror("Error({0}): {1}".format(e.errno, e.strerror))
        while dirs:
            d = dirs.pop()
            try:
                os.rmdir(d)
            except OSError as exc:
                reporterror('Error: cannot delete directory "%s": %s' % (d, str(exc)))

    def tempfile_exists(self, tmpfilename, mode=LOCALTMP):
        # type: (str, int) -> bool
        return tmpfilename in self.tempfiles_for_mode(mode)

    def cmdargs_for_style(self, formatstyle, filename=None):
        # type: (Style, Optional[str]) -> List[str]
        raise NotImplementedError

    def styletext(self, style):
        raise NotImplementedError

    def can_process_in_parallel(self, filenames):
        # type: (List[str]) -> bool
        """
        Returns False if one of the files is too large to be processed in parallel
        with another file.
        Returns True if all files are small enough.
        """
        result = True
        for filename in filenames:
            sourcedata = get_cached_file(filename)
            if len(sourcedata) > MAX_FILESIZE_FOR_MULTIPROCESSING:
                reportwarning('Warning: %s has a size of %s bytes.' % (filename,
                                                                       len(sourcedata)))
                reportwarning('  This may cause memory swapping so we only use'
                              ' a single processor core.')
                result = False
        return result

    def formatcode(self, formatstyle, sourcedata, filename=None):
        # type: (Style, bytes, Optional[str]) -> Optional[bytes]
        cmdargs = self.cmdargs_for_style(formatstyle, filename)
        # The formatter reads the sourcedata from standard input and only uses the filename
        # to identify the language from the its extension.
        # This is why filename is not listed in the depfiles argument of make_execall.
        jobs = [make_execall(self.exe, cmdargs, sourcedata)]
        jobresults = list(run_executables(jobs, self.cache, ccmode=CC_OFF))
        job, jobres = jobs[0], jobresults[0]
        self.reporterrors(job, jobres)
        valid_jobres = self.valid_job_result(job, jobres)
        if not valid_jobres:
            return None
        return jobres.stdout

    def reformat(self, sourcefile, destfile, configfile):
        # type: (str, str, str) -> None
        """Reformats sourcefile according to configfile and writes it to destfile.
        This method is only used for testing.
        """
        tmpdir = tempfile.mkdtemp(prefix='whatstyle_')
        cfg = os.path.join(tmpdir, self.configfilename)
        copyfile(configfile, cfg)
        tmpfilename = os.path.join(tmpdir, os.path.basename(sourcefile))
        copyfile(sourcefile, tmpfilename)
        cmdargs = [tmpfilename]
        exeresult = run_executable(self.exe, cmdargs)
        writebinary(destfile, exeresult.stdout)
        os.remove(tmpfilename)
        os.remove(cfg)
        os.rmdir(tmpdir)

    def __del__(self):
        # type: () -> None
        self.remove_tempfiles()

# ----------------------------------------------------------------------
# The CLANG_FORMAT_EVOLUTION is generated from a clang Git repository by
# tools/create_formatstyle_history.py.

CLANG_FORMAT_EVOLUTION = """\
# Clang 3.5
+ BasedOnStyle string
        LLVM
        Google
        Chromium
        Mozilla
        WebKit
+ AccessModifierOffset int
+ AlignEscapedNewlinesLeft bool
+ AlignTrailingComments bool
+ AllowAllParametersOfDeclarationOnNextLine bool
+ AllowShortFunctionsOnASingleLine bool
+ AllowShortIfStatementsOnASingleLine bool
+ AllowShortLoopsOnASingleLine bool
+ AlwaysBreakBeforeMultilineStrings bool
+ AlwaysBreakTemplateDeclarations bool
+ BinPackParameters bool
+ BreakBeforeBinaryOperators bool
+ BreakBeforeBraces BraceBreakingStyle
        Attach
        Linux
        Stroustrup
        Allman
+ BreakBeforeTernaryOperators bool
+ BreakConstructorInitializersBeforeComma bool
+ ColumnLimit unsigned
+ ConstructorInitializerAllOnOneLineOrOnePerLine bool
+ ConstructorInitializerIndentWidth unsigned
+ ContinuationIndentWidth unsigned
+ Cpp11BracedListStyle bool
+ DerivePointerBinding bool
+ ExperimentalAutoDetectBinPacking bool
+ IndentCaseLabels bool
+ IndentFunctionDeclarationAfterType bool
+ IndentWidth unsigned
+ MaxEmptyLinesToKeep unsigned
+ NamespaceIndentation NamespaceIndentationKind
        None
        Inner
        All
+ ObjCSpaceBeforeProtocolList bool
+ PenaltyBreakBeforeFirstCallParameter unsigned
+ PenaltyBreakComment unsigned
+ PenaltyBreakFirstLessLess unsigned
+ PenaltyBreakString unsigned
+ PenaltyExcessCharacter unsigned
+ PenaltyReturnTypeOnItsOwnLine unsigned
+ PointerBindsToType bool
+ SpaceAfterControlStatementKeyword bool
+ SpaceBeforeAssignmentOperators bool
+ SpaceInEmptyParentheses bool
+ SpacesBeforeTrailingComments unsigned
+ SpacesInAngles bool
+ SpacesInCStyleCastParentheses bool
+ SpacesInParentheses bool
+ Standard LanguageStandard
        Cpp03
        Cpp11
        Auto
+ TabWidth unsigned
+ UseTab UseTabStyle
        Never
        ForIndentation
        Always
# Clang 3.5
+ Language LanguageKind
        None
        Cpp
        JavaScript
# Clang 3.5
- SpaceAfterControlStatementKeyword bool
+ SpaceBeforeParens SpaceBeforeParensOptions
        Never
        ControlStatements
        Always
# Clang 3.5
+ BasedOnStyle string
        LLVM
        Google
        Chromium
        Mozilla
        WebKit
        GNU
+ IndentBlocks bool
# Clang 3.5
- IndentBlocks bool
# Clang 3.5
+ BreakBeforeBraces BraceBreakingStyle
        Attach
        Linux
        Stroustrup
        Allman
        GNU
# Clang 3.5
+ CommentPragmas std::string
# Clang 3.5
+ SpacesInContainerLiterals bool
# Clang 3.5
+ Language LanguageKind
        None
        Cpp
        JavaScript
        Proto
# Clang 3.5
+ ObjCSpaceAfterProperty bool
# Clang 3.5
+ KeepEmptyLinesAtTheStartOfBlocks bool
# Clang 3.5
+ ForEachMacros std::vector<std::string>
# Clang 3.5
+ AllowShortFunctionsOnASingleLine ShortFunctionStyle
        None
        Inline
        All
# Clang 3.5
+ AllowShortBlocksOnASingleLine bool
# Clang 3.5
+ DisableFormat bool
# Clang 3.5
- DerivePointerBinding bool
- PointerBindsToType bool
+ DerivePointerAlignment bool
+ PointerAlignment PointerAlignmentStyle
        Left
        Right
        Middle
# Clang 3.5
- IndentFunctionDeclarationAfterType bool
# Clang 3.5
+ IndentWrappedFunctionNames bool
# Clang 3.6
+ AlwaysBreakAfterDefinitionReturnType bool
# Clang 3.6
+ SpacesInSquareBrackets bool
# Clang 3.6
+ SpaceAfterCStyleCast bool
# Clang 3.6
+ AllowShortCaseLabelsOnASingleLine bool
# Clang 3.6
+ BreakBeforeBinaryOperators BinaryOperatorStyle
        None
        NonAssignment
        All
# Clang 3.6
+ Language LanguageKind
        None
        Cpp
        Java
        JavaScript
        Proto
# Clang 3.6
+ BinPackArguments bool
# Clang 3.6
+ ObjCBlockIndentWidth unsigned
# Clang 3.6
+ AlignAfterOpenBracket bool
# Clang 3.6
+ AllowShortFunctionsOnASingleLine ShortFunctionStyle
        None
        Inline
        Empty
        All
# Clang 3.6
+ AlignOperands bool
# Clang 3.7
+ AlignConsecutiveAssignments bool
# Clang 3.7
+ AllowShortFunctionsOnASingleLine ShortFunctionStyle
        None
        Empty
        Inline
        All
# Clang 3.7
+ AlwaysBreakAfterDefinitionReturnType DefinitionReturnTypeBreakingStyle
        None
        All
        TopLevel
# Clang 3.7
+ MacroBlockBegin std::string
+ MacroBlockEnd std::string
# Clang 3.7
+ BreakBeforeBraces BraceBreakingStyle
        Attach
        Linux
        Mozilla
        Stroustrup
        Allman
        GNU
# Clang 3.8
+ BreakBeforeBraces BraceBreakingStyle
        Attach
        Linux
        Mozilla
        Stroustrup
        Allman
        GNU
        WebKit
# Clang 3.8
+ IncludeCategories std::vector<std::pair<std::string, unsigned>>
# Clang 3.8
+ BraceWrapping BraceWrappingFlags
        bool AfterClass
        bool AfterControlStatement
        bool AfterEnum
        bool AfterFunction
        bool AfterNamespace
        bool AfterObjCDeclaration
        bool AfterStruct
        bool AfterUnion
        bool BeforeCatch
        bool BeforeElse
        bool IndentBraces
+ BreakBeforeBraces BraceBreakingStyle
        Attach
        Linux
        Mozilla
        Stroustrup
        Allman
        GNU
        WebKit
        Custom
# Clang 3.8
+ AlignConsecutiveDeclarations bool
# Clang 3.8
+ IncludeCategories std::vector<IncludeCategory>
# Clang 3.8
+ BreakAfterJavaFieldAnnotations bool
# Clang 3.8
+ AlignAfterOpenBracket BracketAlignmentStyle
        Align
        DontAlign
        AlwaysBreak
# Clang 3.8
+ SortIncludes bool
# Clang 3.8
+ ReflowComments bool
# Clang 3.8
+ AlwaysBreakAfterReturnType ReturnTypeBreakingStyle
        None
        All
        TopLevel
        AllDefinitions
        TopLevelDefinitions
# Clang 3.8
+ Language LanguageKind
        None
        Cpp
        Java
        JavaScript
        Proto
        TableGen
# Clang 3.9
+ BreakStringLiterals bool
# Clang 3.9
+ JavaScriptQuotes JavaScriptQuoteStyle
        Leave
        Single
        Double
# Clang 3.9
+ IncludeIsMainRegex std::string
# Clang 3.9
+ UseTab UseTabStyle
        Never
        ForIndentation
        ForContinuationAndIndentation
        Always
# Clang 3.9
+ JavaScriptWrapImports bool
# Clang 4.0
+ SpaceAfterTemplateKeyword bool
# Clang 4.0
+ Language LanguageKind
        None
        Cpp
        Java
        JavaScript
        ObjC
        Proto
        TableGen
# Clang 5
+ FixNamespaceComments bool
# Clang 5
+ BreakBeforeInheritanceComma bool
# Clang 5
- AlignEscapedNewlinesLeft bool
+ AlignEscapedNewlines EscapedNewlineAlignmentStyle
        DontAlign
        Left
        Right
# Clang 5
+ PenaltyBreakAssignment unsigned
# Clang 5
- BreakConstructorInitializersBeforeComma bool
+ AllowShortFunctionsOnASingleLine ShortFunctionStyle
        None
        InlineOnly
        Empty
        Inline
        All
+ BraceWrapping BraceWrappingFlags
        bool AfterClass
        bool AfterControlStatement
        bool AfterEnum
        bool AfterFunction
        bool AfterNamespace
        bool AfterObjCDeclaration
        bool AfterStruct
        bool AfterUnion
        bool BeforeCatch
        bool BeforeElse
        bool IndentBraces
        bool SplitEmptyFunctionBody
+ BreakConstructorInitializers BreakConstructorInitializersStyle
        BeforeColon
        BeforeComma
        AfterColon
+ CompactNamespaces bool
# Clang 5
+ SortUsingDeclarations bool
# Clang 5
+ BraceWrapping BraceWrappingFlags
        bool AfterClass
        bool AfterControlStatement
        bool AfterEnum
        bool AfterFunction
        bool AfterNamespace
        bool AfterObjCDeclaration
        bool AfterStruct
        bool AfterUnion
        bool BeforeCatch
        bool BeforeElse
        bool IndentBraces
        bool SplitEmptyFunction
        bool SplitEmptyRecord
        bool SplitEmptyNamespace
# Clang 5
+ Language LanguageKind
        None
        Cpp
        Java
        JavaScript
        ObjC
        Proto
        TableGen
        TextProto
# Clang 6
+ IndentPPDirectives PPDirectiveIndentStyle
        None
        AfterHash
# Clang 6
+ BraceWrapping BraceWrappingFlags
        bool AfterClass
        bool AfterControlStatement
        bool AfterEnum
        bool AfterFunction
        bool AfterNamespace
        bool AfterObjCDeclaration
        bool AfterStruct
        bool AfterUnion
        bool AfterExternBlock
        bool BeforeCatch
        bool BeforeElse
        bool IndentBraces
        bool SplitEmptyFunction
        bool SplitEmptyRecord
        bool SplitEmptyNamespace
# Clang 6
+ RawStringFormats std::vector<RawStringFormat>
# Clang 6
+ IncludeBlocks IncludeBlocksStyle
        Preserve
        Merge
        Regroup
# Clang 7
+ ObjCBinPackProtocolList BinPackStyle
        Auto
        Always
        Never
# Clang 7
+ SpaceBeforeCtorInitializerColon bool
+ SpaceBeforeInheritanceColon bool
+ SpaceBeforeRangeBasedForLoopColon bool
# Clang 7
- IncludeBlocks IncludeBlocksStyle
        Preserve
        Merge
        Regroup
- IncludeCategories std::vector<IncludeCategory>
- IncludeIsMainRegex std::string
# Clang 7
+ AlwaysBreakTemplateDeclarations BreakTemplateDeclarationsStyle
        No
        MultiLine
        Yes
+ PenaltyBreakTemplateDeclaration unsigned
# Clang 7
- BreakBeforeInheritanceComma bool
+ BreakInheritanceList BreakInheritanceListStyle
        BeforeColon
        BeforeComma
        AfterColon
# Clang 7
+ SpaceBeforeCpp11BracedList bool
"""


class ClangFormatter(CodeFormatter):
    """Formatter for:
    clang-format: A tool to format C/C++/Java/JavaScript/Objective-C/Protobuf code.
    (http://clang.llvm.org/docs/ClangFormat.html)
    """

    shortname = 'clang-format'
    _prefer_basestyle = True
    base_optionname = 'BasedOnStyle'
    invalid_enums = {'Language': {'None'}}
    columnlimitname = 'ColumnLimit'
    configfilename = '.clang-format'
    styledump_argument = '-dump-config'

    def __init__(self, exe, cache=None):
        # type: (str, Optional[Cache]) -> None
        super(ClangFormatter, self).__init__(exe, cache=cache)

    def register_options(self):
        # type: () -> None
        dump = self.style_dump(style_make())
        if dump is None:
            reporterror('Error: We could not get a proper dump-config from clang-format')
            return
        self.register_options_from_dump(dump)

    def register_options_from_dump(self, config_dump):
        # type: (str) -> None
        version, styledef = find_closest_clang_version(unistr(config_dump))
        self.styledefinition = self.remove_invalid_options(styledef)

    def nested_derivations(self, style):
        # type: (Style) -> List[Style]
        options = [('BreakBeforeBraces', 'Custom')]
        nstyles = []
        for optionname, value in options:
            optdef = styledef_option(self.styledefinition, optionname)
            # We can only use this nested option if the clang version in use supports it.
            if optdef is None:
                continue
            if value not in option_configs(optdef):
                continue
            if style.get(optionname) != value:
                nstyle = Style(copy.deepcopy(style))
                set_option(nstyle, optionname, value)
                nstyles.append(nstyle)
        return nstyles

    def styletext(self, style):
        # type: (Style) -> str
        if not isinstance(style, Style):
            raise TypeError()
        fragments = []
        for optionname, value in self.sorted_style(style).items():
            if isinstance(value, Style):
                text = self.styletext(value)
                fragments.append('%s:' % (optionname, ))
                for line in text.splitlines():
                    fragments.append('  %s' % line)
            else:
                fragments.append('%s: %s' % (optionname, textrepr(value)))
        return '\n'.join(fragments) + '\n'

    def cmdargs_for_style(self, formatstyle, filename=None):
        # type: (Style, Optional[str]) -> List[str]
        assert isinstance(formatstyle, Style)
        inlinestyle = self.inlinestyletext(formatstyle)
        cmdargs = ['-style=%s' % inlinestyle]
        if filename is not None:
            cmdargs.append('-assume-filename=' + filename)
        return cmdargs

    def effective_style(self, style):
        # type: (Style) -> Style
        dump = self.style_dump(style)
        if not dump:
            # The style is probably unsuitable
            return style_make()
        return style_make(parse_clang_dump_config(dump))

    def should_report_error(self, job, jobres):
        # type: (ExeCall, ExeResult) -> bool
        """We do not report known but uncritial errors
        """
        if jobres.error is not None:
            return True
        if jobres.returncode == 0:
            if jobres.stderr:
                if jobres.stderr.startswith(b'Error parsing -style: Unsuitable'):
                    return INFO_INVALIDS in args_info
                if (jobres.stderr.startswith(b'YAML:') and
                        b'Error parsing -style: Invalid ' in jobres.stderr):
                    return INFO_INVALIDS in args_info
        return super(ClangFormatter, self).should_report_error(job, jobres)

    def extra_penalty(self, style, complexity):
        # type: (Style, int) -> Tuple[int, int]
        """Trying longer and longer column limits
        without getting better results should be penalized to speed
        up the search.
        """
        standards = {'ColumnLimit': 80,
                     'MaxEmptyLinesToKeep': 2, }
        penalty = 0
        for optionname, value in standards.items():
            fvalue = style.get(optionname, value)
            if fvalue is not None and fvalue > value:
                penalty += fvalue - value
        if style.get('BreakBeforeBraces') == 'Custom':
            # Rate a commonly known brace breaking style
            # better than an equally performing custom style.
            penalty += 1
            # We would prefer an equally performing style even if we had to
            # add another 12 options.
            complexity += 12
        return complexity, penalty

    @staticmethod
    def additional_variants(stylename, configs, unextendedname, extendoptions):
        combos = []
        for c in configs:
            if c == unextendedname:
                combos.append(stylevariant(stylename, c))
            else:
                for addopt in extendoptions:
                    extopt = stylevariant(stylename, c)
                    extopt.update(addopt)
                    combos.append(extopt)
        return combos

    def variants_for(self, option):
        # type: (Option) -> List[Style]
        """Generates lists of possible values for this option.
        ('IndentCaseLabels', 'bool', ())
        -> [[('IndentCaseLabels', 'true')], [('IndentCaseLabels', 'false')]]

        ('Language', 'LanguageKind', ['Cpp', 'Java', 'JavaScript', 'Proto'])
        -> [[('Language', 'Cpp')], [('Language', 'Java')],
           [('Language', 'JavaScript')], [('Language', 'Proto')]]

        ('PointerAlignment', 'PointerAlignmentStyle',
         ('Left', 'Right', 'Middle'))
        ->
        [[('DerivePointerAlignment', 'false'), ('PointerAlignment', 'Left')],
         [('DerivePointerAlignment', 'false'), ('PointerAlignment', 'Right')],
         [('DerivePointerAlignment', 'false'), ('PointerAlignment', 'Middle')],
         [('DerivePointerAlignment', 'true')]]
        """

        def kvpairs(vs):
            # type: (Iterable[OptionValue]) -> List[Style]
            return stylevariants(stylename, vs)

        if option is None:
            return []

        stylename = option_name(option)
        styletype = option_type(option)
        configs = option_configs(option)

        nestedstyle = option_nestedstyle(option)
        if nestedstyle is not None:
            variants = []
            for nopt in styledef_options(nestedstyle):
                for nstylevariant in self.variants_for(nopt):
                    sty = stylevariant(stylename, nstylevariant)
                    if stylename == 'BraceWrapping':
                        set_option(sty, 'BreakBeforeBraces', 'Custom')
                    variants.append(sty)
            return variants

        if stylename == 'UseTab':
            return self.additional_variants(stylename, configs, 'Never',
                                            [stylevariant('TabWidth', i)
                                             for i in inclusiverange(1, 8)])
        if stylename == 'BreakBeforeBraces':
            # The custom variant is automatically added for the
            # BraceWrapping option.
            vs = kvpairs(configs)
            return [x for x in vs if x.get('BreakBeforeBraces') != 'Custom']

        if configs:
            if stylename == 'PointerAlignment':
                return self.additional_variants('DerivePointerAlignment', [False, True], True,
                                                stylevariants(stylename, configs))
            return kvpairs(configs)
        if styletype == 'bool':
            if stylename == 'DisableFormat':
                return kvpairs([False])
            if stylename == 'DerivePointerAlignment':
                return []
            return kvpairs([True, False])
        if styletype == 'int':
            return []
        if styletype == 'unsigned':
            if stylename == 'ColumnLimit':
                return kvpairs(self.column_limit_candidates)
            elif stylename == 'TabWidth':
                return kvpairs(inclusiverange(1, 8))
            elif stylename == 'IndentWidth':
                return kvpairs(inclusiverange(0, 8))
            elif stylename.startswith('Penalty'):
                # We avoid changing large integers whose purpose
                # is not exactly clear for the moment.
                return []
            else:
                return kvpairs(inclusiverange(0, 2))
        return []

# ----------------------------------------------------------------------


class IndentFormatter(CodeFormatter):
    """Formatter for:
    indent -- indent and format C program source
    (http://www.freebsd.org/cgi/man.cgi?query=indent)

    The options of the IndentFormatter are based on the union of
    options of indent versions for FreeBSD, OpenBSD, OS X and GNU indent.
    Many options will not be valid for the indent version that is used.
    The complains about unknown options are registered and the offending options won't be
    used for subsequent rounds of optimization.
    """

    shortname = 'indent'
    alternative_names = ['gindent']
    base_optionname = 'indent_base_style'
    columnlimitname = 'l'
    configfilename = '.indent.pro'
    # yapf: disable
    opts = [('bacc', 'enum', ('bacc', 'nbacc')),
            ('bad', 'enum', ('bad', 'nbad')),
            ('bap', 'enum', ('bap', 'nbap')),
            ('bbb', 'enum', ('bbb', 'nbbb')),
            ('bbo', 'enum', ('bbo', 'nbbo')),
            ('bc', 'enum', ('bc', 'nbc')),
            ('bfda', 'enum', ('bfda', 'nbfda')),
            ('bfde', 'enum', ('bfde', 'nbfde')),
            ('bli', 'int', ()),
            ('br', 'enum', ('br', 'bl')),
            ('brf', 'enum', ('brf', 'blf')),
            ('brs', 'enum', ('brs', 'bls')),
            ('bs', 'enum', ('bs',)),
            ('c', 'int', ()),
            ('cbi', 'int', ()),
            ('cd', 'int', ()),
            ('cdb', 'enum', ('cdb', 'ncdb')),
            ('cdw', 'enum', ('cdw', 'ncdw')),
            ('ce', 'enum', ('ce', 'nce')),
            ('ci', 'int', ()),
            ('cli', 'int', ()),
            ('cp', 'int', ()),
            ('cs', 'enum', ('cs', 'ncs')),
            ('d', 'int', ()),
            ('di', 'int', ()),
            ('dj', 'enum', ('dj', 'ndj')),
            ('djn', 'enum', ('djn', 'ndjn')),
            ('eei', 'enum', ('eei', 'neei')),
            ('ei', 'enum', ('ei', 'nei')),
            ('fbs', 'enum', ('fbs', 'nfbs')),
            ('fc1', 'enum', ('fc1', 'nfc1')),
            ('fca', 'enum', ('fca', 'nfca')),
            ('fcb', 'enum', ('fcb', 'nfcb')),
            ('hnl', 'enum', ('hnl', 'nhnl')),
            ('i', 'int', ()),
            ('il', 'int', ()),
            ('ip', 'int', ()),
            ('l', 'int', ()),
            ('lc', 'int', ()),
            ('ldi', 'int', ()),
            ('lp', 'enum', ('lp', 'nlp')),
            ('lps', 'enum', ('lps', 'nlps')),
            ('npro', 'enum', ('npro',)),
            ('pcs', 'enum', ('pcs', 'npcs')),
            ('pi', 'int', ()),
            ('ppi', 'int', ()),
            ('prs', 'enum', ('prs', 'nprs')),
            ('psl', 'enum', ('psl', 'npsl')),
            ('saf', 'enum', ('saf', 'nsaf')),
            ('sai', 'enum', ('sai', 'nsai')),
            ('saw', 'enum', ('saw', 'nsaw')),
            ('sbi', 'int', ()),
            ('sc', 'enum', ('sc', 'nsc')),
            ('sob', 'enum', ('sob', 'nsob')),
            ('ss', 'enum', ('ss', 'nss')),
            ('st', 'enum', ('st',)),
            ('ts', 'int', ()),
            ('ut', 'enum', ('ut', 'nut'))]

    # yapf: enable

    def __init__(self, exe, cache=None):
        super(IndentFormatter, self).__init__(exe, cache=cache)

    @classmethod
    def executable_names(cls):
        # Change the order to prefer gindent instead of indent if available.
        return cls.alternative_names + [cls.shortname]

    def register_options(self):
        styles = []
        gnu_ident = self.prefer_basestyle
        for optname, opttype, configs in self.opts:
            if not gnu_ident and optname == 'ip':
                # The BSD indent is not a numeric but a boolean option.
                opttype, configs = 'enum', ('ip', 'nip')
            styles.append(option_make(optname, opttype, configs))
        if self.prefer_basestyle:
            styles.append(option_make(self.base_optionname, 'enum', ('orig', 'linux', 'kr',
                                                                     'gnu')))
        self.styledefinition = styledef_make(styles)

    @property
    def prefer_basestyle(self):
        # type: () -> bool
        return self.version_string.startswith('GNU indent')

    def cmdlineopt(self, optionname, value):
        # type: (str, OptionValue) -> str
        option = self.styledefinition[optionname]
        styletype = option_type(option)
        configs = option_configs(option)
        if configs:
            return "-%s" % value
        if styletype == 'int':
            return "-%s%s" % (optionname, value)
        else:
            raise ValueError

    def styletext(self, styles):
        # type: (Style) -> str
        fragments = []
        for optionname, value in self.sorted_style(styles).items():
            fragments.append(self.cmdlineopt(optionname, value))
        return '\n'.join(fragments) + '\n'

    def cmdargs_for_style(self, formatstyle, filename=None):
        # type: (Style, Optional[str]) -> List[str]
        assert isinstance(formatstyle, Style)
        # -npro: ignore .indent.pro files
        # -st: read source from stdin, write result to stdout
        cmdargs = ['-npro', '-st']
        for optname, value in sorted(formatstyle.items()):
            cmdargs.append(self.cmdlineopt(optname, value))
        return cmdargs

    def should_report_error(self, job, jobres):
        # type: (ExeCall, ExeResult) -> bool
        if jobres.error is not None:
            return True
        if self.invalid_cmdline_option(job, jobres):
            return INFO_INVALIDS in args_info
        return jobres.returncode != 0 or bool(jobres.stderr)

    def valid_job_result(self, job, jobres):
        # type: (ExeCall, ExeResult) -> bool
        return jobres.error is None and jobres.returncode == 0 and not bool(jobres.stderr)

    def invalid_cmdline_option(self, job, jobres):
        # type: (ExeCall, ExeResult) -> bool
        if jobres.returncode != 1:
            return False
        # Handle the known error messages
        msg = unistr(jobres.stderr)
        if msg.startswith('command line: unknown option'):
            return True
        if msg.startswith('command line: option'):
            return True
        if msg.startswith('indent: bad font specification'):
            return True
        if msg.startswith('indent: ?: unknown parameter'):
            return True
        if msg.endswith('requires a parameter\n'):
            return True

        return False

    def variants_for(self, option):
        # type: (Option) -> List[Style]

        def kvpairs(vs):
            # type: (Iterable[OptionValue]) -> List[Style]
            return stylevariants(stylename, vs)

        stylename = option_name(option)
        styletype = option_type(option)
        configs = option_configs(option)

        if configs:
            return kvpairs(configs)
        if stylename == self.columnlimitname:
            return kvpairs(self.column_limit_candidates)
        if styletype == 'int':
            return kvpairs([0, 1, 2, 4, 8, 16])
        return []

    def reformat(self, sourcefile, destfile, configfile):
        # type: (str, str, str) -> None
        tmpdir = tempfile.mkdtemp(prefix='whatstyle_')
        cfg = os.path.join(tmpdir, self.configfilename)
        copyfile(configfile, cfg)
        data = readbinary(sourcefile)
        # -st: read source from stdin, write result to stdout
        exeresult = run_executable(self.exe, ['-st'], stdindata=data)
        writebinary(destfile, exeresult.stdout)
        os.remove(cfg)
        os.rmdir(tmpdir)

# ----------------------------------------------------------------------


class YapfFormatter(CodeFormatter):
    """Formatter for:
    yapf: Formatter for Python code.
    (https://github.com/google/yapf)
    """

    shortname = 'yapf'
    _prefer_basestyle = True
    base_optionname = 'based_on_style'
    columnlimitname = 'column_limit'
    base_styles = 'pep8 chromium google facebook'.split()
    configfilename = '.style.yapf'
    styledump_argument = '--style-help'

    def __init__(self, exe, cache=None):
        super(YapfFormatter, self).__init__(exe, cache=cache)

    @staticmethod
    def typefromvalue(optvalue):
        # type: (str) -> str
        if optvalue in ['true', 'false']:
            return 'bool'
        try:
            int(optvalue)
            return 'int'
        except ValueError:
            pass
        return 'string'

    def register_options(self):
        # type: () -> None
        """Parse options from text like this
        ALIGN_CLOSING_BRACKET_WITH_VISUAL_INDENT=True
           Align closing bracket with visual indentation.

        BLANK_LINE_BEFORE_NESTED_CLASS_OR_DEF=False
           Insert a blank line before a 'def' or 'class' immediately nested
        """
        styles = [option_make(self.base_optionname, 'string', self.base_styles)]
        for optname, optvalue in self.iter_options(style_make()):
            styles.append(option_make(optname, self.typefromvalue(optvalue), tuple()))
        self.styledefinition = styledef_make(styles)

    def effective_style(self, style):
        # type: (Style) -> Style
        stylevalues = style_make()
        for optname, optvalue in self.iter_options(style):
            set_option(stylevalues, optname, optvalue)
        return stylevalues

    def iter_options(self, style):
        # type: (Style) -> Iterator[TextPair]
        dump = self.style_dump(style)
        for optname, optvalue in parse_keyvalue_pairs(dump):
            optname = optname.lower()
            optvalue = optvalue.lower()
            yield optname, optvalue

    def styletext(self, styles):
        # type: (Style) -> str
        fragments = ['[style]']
        for optionname, value in self.sorted_style(styles).items():
            fragments.append('%s = %s' % (optionname, textrepr(value)))
        return '\n'.join(fragments) + '\n'

    def cmdargs_for_style(self, formatstyle, filename=None):
        # type: (Style, Optional[str]) -> List[str]
        assert isinstance(formatstyle, Style)
        inlinestyle = self.inlinestyletext(formatstyle)
        cmdargs = ['--no-local-style', '--style=%s' % inlinestyle]
        if filename is not None:
            cmdargs.append(filename)
        return cmdargs

    def should_report_error(self, job, jobres):
        # type: (ExeCall, ExeResult) -> bool
        # Yapf exits with code 2 when the reformatted output is different
        # from the input and with code 0 when the output is unchanged.
        if jobres.error is not None:
            return True
        return jobres.returncode not in [0, 2] or bool(jobres.stderr)

    def valid_job_result(self, job, jobres):
        # type: (ExeCall, ExeResult) -> bool
        return jobres.error is None and jobres.returncode in [0, 2] and not jobres.stderr

    def variants_for(self, option):
        # type: (Option) -> List[Style]

        def kvpairs(vs):
            # type: (Iterable[OptionValue]) -> List[Style]
            return stylevariants(stylename, vs)

        stylename = option_name(option)
        styletype = option_type(option)
        configs = option_configs(option)

        if configs:
            return kvpairs(configs)
        if styletype == 'bool':
            return kvpairs([True, False])
        if styletype == 'int':
            if stylename == 'column_limit':
                # Here we can get weird results, for example
                # in bottle_sqlalchemy.py is a constructor with
                # 8 arguments which are already split between two lines.
                # We find an optimum column limit of 126 because this
                # has less diff lines than putting each argument on a new
                # line. Maybe we should use a different diff metric.
                return kvpairs(self.column_limit_candidates)
            elif stylename == 'indent_width':
                return kvpairs([2, 4, 8])
            elif stylename == 'spaces_before_comment':
                return kvpairs(inclusiverange(1, 4))
            elif stylename.startswith('split_penalty'):
                # We avoid changing large integers whose purpose
                # is not exactly clear for the moment.
                pass
        return []

# ----------------------------------------------------------------------


class HtmlTidyFormatter(CodeFormatter):
    """Formatter for:
    Tidy - The granddaddy of HTML tools.
    (http://www.html-tidy.org)
    """

    shortname = 'tidy'
    columnlimitname = 'wrap'
    configfilename = 'tidy.conf'
    styledump_argument = '-show-config'

    def __init__(self, exe, cache=None):
        super(HtmlTidyFormatter, self).__init__(exe, cache=cache)
        style = style_make()
        set_option(style, 'indent', 'yes')
        self.initial_style = style

    def register_options(self):
        # type: () -> None
        """Parse options from XML like this:
        <?xml version="1.0"?>
        <config version="5.1.25">
         <option class="print">
          <name>indent-spaces</name>
          <type>Integer</type>
          <default>2</default>
          <example>0, 1, 2, ...</example>
          <description>This option specifies the number of spaces or tabs that
           Tidy uses to indent content when <code>indent</code> is enabled.
            <br/>Note that the default value for this option is dependent
             upon the value of <code>indent-with-tabs</code> (see also).
          </description>
          <seealso>indent</seealso>
         </option>
        </config>
        """
        exeresult = run_executable(self.exe, ['-xml-config'], cache=self.cache)
        buf = BytesIO(exeresult.stdout)

        optionname = None  # type: Optional[str]
        optiontype = None
        example = None
        options = []

        for event, elem in ETree.iterparse(buf, events=('start', 'end')):
            tag = elem.tag
            if event == 'end':
                if optionname is not None and tag == 'option':
                    # First ignore some options
                    if optionname.startswith('show-'):
                        continue
                    if optionname.startswith('new-'):
                        continue
                    if optionname == 'write-back':
                        continue
                    if optionname == 'char-encoding':
                        continue
                    if not self.allow_encoding_change and optionname.endswith('-encoding'):
                        continue
                    if optiontype in ['AutoBool', 'Boolean', 'Integer']:
                        options.append(option_make(optionname, optiontype, tuple()))
                    elif optiontype in ['String']:
                        continue
                    else:
                        # Remove comments from the option values
                        # e.g. 0 (Tidy Classic), 1 (Priority 1 Checks), ...
                        if example is not None:
                            example = re.sub(r'\s*\(.*?\)', '', example)
                            configs = example.split(', ')  # type: List[str]
                        else:
                            configs = []
                        if not configs:
                            continue
                        optvalues = [typeconv(c) for c in configs]
                        options.append(option_make(optionname, optiontype, optvalues))
                elif tag == 'name':
                    optionname = self.safeunistr(elem.text)
                elif tag == 'type':
                    optiontype = self.safeunistr(elem.text)
                elif tag == 'example':
                    example = self.safeunistr(elem.text)

        self.styledefinition = styledef_make(options)

    @staticmethod
    def safeunistr(text):
        # type: (Union[str, bytes, None]) -> Optional[str]
        if text is None:
            return None
        return unistr(text)

    def styletext(self, styles):
        # type: (Style) -> str
        fragments = []
        for optionname, value in self.sorted_style(styles).items():
            fragments.append('%s: %s' % (optionname, value))
        return '\n'.join(fragments) + '\n'

    @staticmethod
    def styleargs(style):
        # type: (Style) -> List[str]
        args = []
        for key in sorted(style.keys()):
            value = style[key]
            value = str(value)
            args.append('--' + key)
            args.append(value)
        return args

    def inlinestyletext(self, style):
        # type: (Style) -> str
        return ' '.join(HtmlTidyFormatter.styleargs(style))

    def cmdargs_for_style(self, formatstyle, filename=None):
        # type: (Style, Optional[str]) -> List[str]
        assert isinstance(formatstyle, Style)
        args = HtmlTidyFormatter.styleargs(formatstyle)
        cmdargs = ['--show-warnings', 'no'] + args
        return cmdargs

    def effective_style(self, style):
        # type: (Style) -> Style
        stylevalues = style_make()
        dump = self.style_dump(style)
        if dump is None:
            return stylevalues
        typepos = -1
        valuepos = -1
        for line in dump.splitlines():
            if typepos < 0:
                typepos = line.find('Type')
                valuepos = line.find('Current Value')
                continue
            if len(line) < typepos:
                break
            optname = line[:typepos].strip()
            optvalue = line[valuepos:].strip()
            pos = optvalue.find(' ')
            if pos >= 0:
                # Only keep the value up unto the first space.
                optvalue = optvalue[:pos]
            set_option(stylevalues, optname, optvalue)
        return stylevalues

    def should_report_error(self, job, jobres):
        # type: (ExeCall, ExeResult) -> bool
        # tidy exits with code 1 when there are warnings
        # and with code 0 if everything is fine.
        if jobres.error is not None:
            return True
        return jobres.returncode not in [0, 1]

    def valid_job_result(self, job, jobres):
        # type: (ExeCall, ExeResult) -> bool
        if jobres.error is not None:
            return False
        if jobres.returncode not in [0, 1]:
            return False
        if not jobres.stdout:
            # For some reason tidy sometimes returns with code 1 (warning)
            # but writes no output.
            return False
        return True

    def variants_for(self, option):
        # type: (Option) -> List[Style]

        stylename = option_name(option)
        styletype = option_type(option)
        configs = option_configs(option)

        def kvpairs(vs):
            # type: (Iterable[OptionValue]) -> List[Style]
            return stylevariants(stylename, vs)

        if configs:
            return kvpairs(configs)

        if stylename == 'indent':
            return kvpairs(['yes'])

        if stylename == 'wrap':
            return kvpairs([0])

        if stylename == 'indent-spaces':
            return kvpairs(inclusiverange(0, 8))

        if styletype == 'AutoBool':
            return kvpairs(['yes', 'no', 'auto'])

        if styletype == 'Boolean':
            return kvpairs(['yes', 'no'])
        return []

    def reformat(self, sourcefile, destfile, configfile):
        # type: (str, str, str) -> None
        run_executable(self.exe, ['-quiet', '--show-errors', '0', '--show-warnings', 'no',
                                  '-output', destfile, '-config', configfile, sourcefile])

# ----------------------------------------------------------------------


class UncrustifyFormatter(CodeFormatter):
    """Formatter for:
    uncrustify: Code beautifier.
    (https://github.com/uncrustify/uncrustify)

    uncrustify 0.63 tells us that:
        'There are currently 515 options and minimal documentation.
         Try UniversalIndentGUI and good luck.'
    Cross fingers.
    """

    shortname = 'uncrustify'
    columnlimitname = 'code_width'
    configfilename = 'uncrustify.cfg'
    styledump_argument = '--update-config'
    language_exts = [
        ['C', CEXTS], ['CPP', CPPEXTS], ['D', '.d'], ['CS', '.cs'], ['JAVA', '.java'],
        ['PAWN', '.p .pawn .sma'], ['OC', '.h .m'], ['OC+', '.h .mm'], ['VALA', '.vala']
    ]

    def __init__(self, exe, cache=None):
        super(UncrustifyFormatter, self).__init__(exe, cache=cache)

    def register_options(self):
        # type: () -> None
        """Parse options from text like this:
        # Uncrustify 0.63
        #
        # General options
        #

        newlines                                  { Auto, LF, CR, CRLF }
          The type of line endings

        input_tab_size                            Number
          The original size of tabs in the input

        indent_align_string                       { False, True }
          Whether to indent strings broken by '\' so that they line up

        # The format changed with uncrustify 0.68 as follows:
        # Uncrustify-0.69.0_f
        # The type of line endings.
        #
        # Default: auto
        newlines                        = auto     # lf/crlf/cr/auto

        # The original size of tabs in the input.
        #
        # Default: 8
        input_tab_size                  = 8        # unsigned number

        # Whether to indent strings broken by '\' so that they line up.
        indent_align_string             = false    # true/false
        """
        exeresult = run_executable(self.exe, ['--show-config'], cache=self.cache)
        options = []
        text = unistr(exeresult.stdout)
        for m in re.finditer(r'^(\w+)\s+(.*?)\s*$', text, re.MULTILINE):
            optionname, optiondesc = m.group(1), m.group(2)
            if optiondesc.startswith('{'):
                optiontype = 'Enum'
                configs = optiondesc[1:-1].strip().split(', ')
                configs = [c.lower() for c in configs]
            elif optiondesc in ['Number', 'String', 'Unsigned Number']:
                optiontype = optiondesc
                configs = []
            else:
                # New format >= uncrustify 0.68
                parts = optiondesc.split('#')
                if len(parts) <= 1:
                    continue
                configs = parts[-1].strip().split('/')
                if len(configs) >= 2:
                    # options as in lf/crlf/cr/auto for example.
                    optiontype = 'Enum'
                    configs = [c.lower() for c in configs]
                else:
                    # number, unsigned number or string
                    optiontype = configs[0].title()
                    configs = []
            options.append(option_make(optionname, optiontype, configs))
        self.styledefinition = styledef_make(options)

    def identify_language(self, filenames=(), language=None):
        # type: (Sequence[str], Optional[str]) -> None
        """Identify the languages from the filenames extensions.
        """
        if language is None:
            exts = set([os.path.splitext(f)[1] for f in filenames])
            for lang, extsdescription in UncrustifyFormatter.language_exts:
                langexts = set(extsdescription.split())
                if exts.issubset(langexts):
                    self.languages.append(lang)
        else:
            self.languages.append(language)

    def attempt_acceptible(self, roundnr, prevdist, newdist):
        # type: (int, Sequence[int], Sequence[int]) -> bool
        if roundnr >= 3 and tuple(newdist) > tuple(prevdist):
            # Makes things worse
            return False
        if roundnr >= 3 and tuple(newdist) >= tuple(prevdist):
            # Does not improve things
            return False
        return True

    def styletext(self, styles):
        # type: (Style) -> str
        fragments = []
        for optionname, value in self.sorted_style(styles).items():
            fragments.append('%s = %s' % (optionname, textrepr(value)))
        return '\n'.join(fragments) + '\n'

    def inlinestyletext(self, style):
        # type: (Style) -> str
        return self.styletext(style)

    def cmdargs_for_style(self, formatstyle, filename=None):
        # type: (Style, Optional[str]) -> List[str]
        assert isinstance(formatstyle, Style)
        configdata = bytestr(self.styletext(formatstyle))
        sha = shahex(configdata)
        cfg = os.path.join(tempfile.gettempdir(), 'whatstyle_uncrustify_%s.cfg' % sha)
        if not self.tempfile_exists(cfg):
            writebinary(cfg, configdata)
            self.add_tempfile(cfg)
        cmdargs = ['-c', cfg]
        # The filename extension might be ambiguous so we choose from the languages
        # registered in identify_language.
        if self.languages:
            lang = self.languages[0]
            cmdargs.extend(['-l', lang])
        return cmdargs

    def effective_style(self, style):
        # type: (Style) -> Style
        stylevalues = style_make()
        dump = self.style_dump(style)
        for optname, optvalue in parse_keyvalue_pairs(dump):
            set_option(stylevalues, optname, optvalue)
        return stylevalues

    def should_report_error(self, job, jobres):
        # type: (ExeCall, ExeResult) -> bool
        if jobres.error is not None:
            return True
        return jobres.returncode != 0

    def valid_job_result(self, job, jobres):
        # type: (ExeCall, ExeResult) -> bool
        if jobres.error is not None:
            return False
        if jobres.returncode != 0:
            return False
        if not jobres.stdout:
            # For some reason tidy returns with code 1 (warning)
            # but writes no output.
            return False
        return True

    # Some reasonable numeric values for various options.
    ir = inclusiverange
    num_tables = [
        ({'input_tab_size', 'output_tab_size'}, ir(1, 8)),
        ({'indent_continue', 'indent_var_def_blk'}, ir(-4, 4)),
        ({'indent_with_tabs', 'indent_brace', 'align_var_def_span',
          'align_var_def_star_style', 'align_var_def_amp_style', 'align_var_def_gap'},
         ir(0, 2)),
        ({'indent_namespace_level', 'indent_ctor_init_leading', 'indent_ctor_init',
          'indent_switch_case'}, ir(0, 4)),
        # These numeric options are rather enums
        ({'nl_remove_extra_newlines', 'cmt_reflow_mode', 'indent_paren_close'}, ir(0, 2)),
        ({'align_typedef_func', 'align_typedef_star_style', 'align_typedef_amp_style'},
         ir(0, 2)),
    ]  # type: List[Tuple[Set[str], Iterable[int]]]

    def variants_for(self, option):
        # type: (Option) -> List[Style]

        stylename = option_name(option)
        styletype = option_type(option)
        configs = option_configs(option)

        def kvpairs(vs):
            # type: (Iterable[OptionValue]) -> List[Style]
            return stylevariants(stylename, vs)

        if stylename == 'newlines':
            # Getting this wrong makes a big mess
            return kvpairs(['auto'])

        if configs:
            # ignore is the default which we don't need to specify.
            usefulvalues = [v for v in configs if v != 'ignore']
            return kvpairs(usefulvalues)

        # All of the following should be options of type number.
        if styletype not in ['Number', 'Unsigned Number']:
            return []

        for nameset, values in self.num_tables:
            if stylename in nameset:
                return kvpairs(values)

        # Some reasonable values according to the documentation
        if stylename == 'indent_columns':
            return kvpairs([2, 3, 4, 8])

        if stylename == 'indent_member':
            return kvpairs([0, 1, 2, 3, 4, 8])

        if stylename == 'indent_xml_string':
            return [style_make({'indent_align_string': 'True',
                                stylename: c}) for c in inclusiverange(0, 2)]

        #
        # Line Splitting options
        #
        if stylename == 'code_width':
            return kvpairs(self.column_limit_candidates)

        # It would be possible to try out unknown numerical options
        # but this would slow everything down,
        # so we don't simply return kvpairs(inclusiverange(-8, 8)).
        return []

    def reformat(self, sourcefile, destfile, configfile):
        # type: (str, str, str) -> None
        run_executable(self.exe, ['-c', configfile, '-f', sourcefile, '-o', destfile])

# ----------------------------------------------------------------------


class ArtisticStyleFormatter(CodeFormatter):
    """Formatter for:
    Artistic Style: A Free, Fast, and Small Automatic Formatter
                    for C, C++, C++/CLI, Objective‑C, C#, and Java Source Code.
    (http://astyle.sourceforge.net)
    """

    shortname = 'astyle'
    columnlimitname = 'max-code-length'
    configfilename = '.astylerc'

    def __init__(self, exe, cache=None):
        super(ArtisticStyleFormatter, self).__init__(exe, cache=cache)

    def register_options(self):
        # type: () -> None
        """Parse options from 'astyle --help' like:
        --style=allman  OR  --style=bsd  OR  --style=break  OR  -A1
        --indent=spaces=#  OR  -s#
        Other Options:
        --------------
            --suffix=####
        """
        exeresult = run_executable(self.exe, ['--help'], cache=self.cache)
        options = []
        text = unistr(exeresult.stdout)
        seen = set()  # type: Set[str]
        opts = OrderedDict()  # type: OrderedDict[str, List[OptionValue]]
        accept_options = True
        for line in text.splitlines():
            if line == 'Other Options:' or '--suffix=' in line:
                # No more useable option after this point.
                break
            if not line or line.startswith('---'):
                # Begin of a new option block or paragraph.
                accept_options = True
                continue
            if not accept_options:
                # Do not register options in the free form explanation of the options
                # described at the beginning of a paragraph.
                continue
            m = re.match(r'^\s+--(\S+).*$', line)
            if m:
                argument = m.group(1)
                if argument in seen:
                    continue
                seen.add(argument)
                pos = argument.find('=')
                if pos > 0:
                    optionname, value = argument[:pos], argument[pos + 1:]
                else:
                    optionname, value = argument, OPTION_PRESENT
                values = opts.get(optionname)  # type: List[OptionValue]
                if values is None:
                    values = []
                    opts[optionname] = values
                values.append(value)
            else:
                # Stop accepting options until the next paragraph begins.
                accept_options = False
        for optionname, configs in opts.items():
            options.append(option_make(optionname, 'enum', configs))
        self.styledefinition = styledef_make(options)

    def optionlist(self, styles):
        # type: (Style) -> List[str]
        options = []
        for optionname, value in self.sorted_style(styles).items():
            opt = '--' + optionname
            if value != OPTION_PRESENT:
                opt += '=' + str(value)
            options.append(opt)
        return options

    def styletext(self, style):
        # type: (Style) -> str
        return '\n'.join(self.optionlist(style)) + '\n'

    def cmdargs_for_style(self, formatstyle, filename=None):
        # type: (Style, Optional[str]) -> List[str]
        assert isinstance(formatstyle, Style)
        cmdargs = ['--options=none', '--quiet'] + self.optionlist(formatstyle)
        return cmdargs

    def variants_for(self, option):
        # type: (Option) -> List[Style]

        def kvpairs(vs):
            # type: (Iterable[OptionValue]) -> List[Style]
            return stylevariants(stylename, vs)

        def numreplace(configs, numvalues):
            # type: (List[OptionValue], Iterable[int]) -> List[OptionValue]
            extconfigs = []  # type: List[OptionValue]
            for c in configs:
                if isinstance(c, text_type) and '#' in c:
                    for n in numvalues:
                        num = str(n)
                        nc = c.replace('#', num)
                        extconfigs.append(nc)
                else:
                    extconfigs.append(c)
            return extconfigs

        stylename = option_name(option)
        configs = option_configs(option)

        if stylename == self.columnlimitname:
            candidates = self.column_limit_candidates
            candidates = [c for c in candidates if 50 <= c <= 200]
            return kvpairs(candidates)
        if stylename == 'indent':
            return kvpairs(numreplace(configs, [2, 4, 8]))
        if stylename == 'min-conditional-indent':
            return kvpairs(numreplace(configs, [0, 1, 2, 3]))
        if stylename == 'max-instatement-indent':
            return kvpairs(numreplace(configs, inclusiverange(40, 120)))
        if stylename == 'mode':
            return []
        if configs:
            return kvpairs(numreplace(configs, [1, 2, 4, 8]))
        return []

    def reformat(self, sourcefile, destfile, configfile):
        # type: (str, str, str) -> None
        formatstyle = style_make()
        with open(configfile) as fp:
            for line in fp.readlines():
                line = line.rstrip()
                if line.startswith('--'):
                    line = line[2:]
                    pos = line.find('=')
                    if pos > 0:
                        optionname, value = line[:pos], line[pos + 1:]
                    else:
                        optionname, value = line, OPTION_PRESENT
                    set_option(formatstyle, optionname, value)
        sourcedata = readbinary(sourcefile)
        data = self.formatcode(formatstyle, sourcedata, filename=sourcefile)
        if data is None:
            data = b''
        writebinary(destfile, data)

# ----------------------------------------------------------------------


class ScalariformFormatter(CodeFormatter):
    """Formatter for:
    Scalariform - a code formatter for Scala.
    (https://github.com/scala-ide/scalariform)
    """

    shortname = 'scalariform'
    configfilename = 'formatterPreferences.properties'
    language_exts = [['SCALA', SCALAEXTS]]

    def __init__(self, exe, cache=None):
        super(ScalariformFormatter, self).__init__(exe, cache=cache)

    def register_options(self):
        # type: () -> None
        """Parse options from text like this:
        Preferences:
          [+|-]alignArguments                                        Enable/disable ...
          ...
          [+|-]spacesWithinPatternBinders                            Enable/disable ...
          -alignSingleLineCaseStatements.maxArrowIndent=[1-100]      Set Maximum number ...
          -indentSpaces=[1-10]                                       Set Number of spaces ...
        """
        exeresult = run_executable(self.exe, ['--help'], cache=self.cache)
        options = []
        text = unistr(exeresult.stdout)
        for m in re.finditer(r'^  (\[\+\|-\]|-)([a-z][a-zA-Z.]+)(?:=\[(\d+)-(\d+)\])?', text,
                             re.MULTILINE):
            optionprefix, optionname, start, end = m.groups()
            if start is None:
                optiontype = 'bool'
                configs = [True, False]  # type: List[OptionValue]
            else:
                optiontype = 'int'
                configs = list(inclusiverange(int(start), int(end)))
            options.append(option_make(optionname, optiontype, configs))
        self.styledefinition = styledef_make(options)

    def styletext(self, style):
        # type: (Style) -> str
        fragments = []
        for optionname, value in self.sorted_style(style).items():
            fragments.append('%s=%s' % (optionname, textrepr(value)))
        return '\n'.join(fragments) + '\n'

    def inlinestyletext(self, style):
        # type: (Style) -> str
        return self.styletext(style)

    def cmdargs_for_style(self, formatstyle, filename=None):
        # type: (Style, Optional[str]) -> List[str]
        assert isinstance(formatstyle, Style)
        cmdargs = ['--stdin', '--stdout']
        for optname, value in sorted(formatstyle.items()):
            cmdargs.append(self.cmdlineopt(optname, value))
        return cmdargs

    def cmdlineopt(self, optionname, value):
        # type: (str, str, OptionValue) -> str
        option = self.styledefinition[optionname]
        styletype = option_type(option)
        if styletype == 'bool':
            prefix = '+' if value else '-'
            return prefix + optionname
        if styletype == 'int':
            return "-%s=%s" % (optionname, value)
        else:
            raise ValueError

    def should_report_error(self, job, jobres):
        # type: (ExeCall, ExeResult) -> bool
        if jobres.error is not None:
            return True
        return jobres.returncode != 0

    def valid_job_result(self, job, jobres):
        # type: (ExeCall, ExeResult) -> bool
        if jobres.error is not None:
            return False
        if jobres.returncode != 0:
            return False
        return True

    def variants_for(self, option):
        # type: (Option) -> List[Style]

        stylename = option_name(option)
        configs = option_configs(option)

        def kvpairs(vs):
            # type: (Iterable[OptionValue]) -> List[Style]
            return stylevariants(stylename, vs)

        if configs:
            return kvpairs(configs)
        return []

    def reformat(self, sourcefile, destfile, configfile):
        # type: (str, str, str) -> None
        data = readbinary(sourcefile)
        exeresult = run_executable(self.exe, ['--preferenceFile=' + unifilename(configfile),
                                              '--stdin'],
                                   stdindata=data)
        writebinary(destfile, exeresult.stdout)

# ----------------------------------------------------------------------

SCALAFMT_OPTIONS = """\
maxColumn = 80
project.git = false
align.openParenCallSite = true
align.openParenDefnSite = true
align.ifWhileOpenParen = true
align.arrowEnumeratorGenerator = false
continuationIndent.callSite = 2
continuationIndent.defnSite = 4
continuationIndent.extendSite = 4
optIn.breaksInsideChains = false
optIn.blankLineBeforeDocstring = false
optIn.selfAnnotationNewline = true
optIn.annotationNewlines = true
optIn.breakChainOnFirstMethodDot = true
optIn.configStyleArguments = true
assumeStandardLibraryStripMargin = false
newlines.alwaysBeforeMultilineDef = true
newlines.afterImplicitKWInVerticalMultiline = false
newlines.alwaysBeforeElseAfterCurlyIf = false
newlines.neverInResultType = false
newlines.sometimesBeforeColonInMethodReturnType = true
newlines.alwaysBeforeTopLevelStatements = false
newlines.afterCurlyLambda = never
newlines.penalizeSingleSelectMultiArgList = true
newlines.neverBeforeJsNative = false
newlines.alwaysBeforeCurlyBraceLambdaParams = false
newlines.beforeImplicitKWInVerticalMultiline = false
unindentTopLevelOperators = false
poorMansTrailingCommasInConfigStyle = false
docstrings = ScalaDoc
lineEndings = unix
rewrite.redundantBraces.methodBodies = true
rewrite.redundantBraces.stringInterpolation = false
rewrite.redundantBraces.generalExpressions = false
rewrite.redundantBraces.includeUnitMethods = true
danglingParentheses = false
includeCurlyBraceInSelectChains = true
binPack.unsafeCallSite = false
binPack.unsafeDefnSite = false
binPack.literalArgumentLists = true
binPack.parentConstructors = false
indentYieldKeyword = true
importSelectors = noBinPack
verticalMultilineAtDefinitionSite = false
spaces.inByNameTypes = true
spaces.afterTripleEquals = false
spaces.inImportCurlyBraces = false
spaces.inParentheses = false
spaces.afterKeywordBeforeParen = true
"""

class ScalafmtFormatter(CodeFormatter):
    """Formatter for:
    Scalafmt - code formatter for Scala.
    (https://github.com/olafurpg/scalafmt)
    """

    shortname = 'scalafmt'
    _prefer_basestyle = True
    base_optionname = 'style'
    base_styles = 'default IntelliJ Scala.js'.split()
    configfilename = '.scalafmt'
    language_exts = [['SCALA', SCALAEXTS]]

    def __init__(self, exe, cache=None):
        super(ScalafmtFormatter, self).__init__(exe, cache=cache)

    def register_options(self):
        # type: () -> None
        options = [option_make(self.base_optionname, 'string', self.base_styles)]
        for line in SCALAFMT_OPTIONS.splitlines():
            optionname, optvalue = re.split(r'\s*=\s*', line)
            if optionname == 'maxColumn':
                optiontype = 'int'
                configs = list(inclusiverange(80, 100))  # type: List[OptionValue]
            elif optionname.startswith('continuationIndent'):
                optiontype = 'int'
                configs = [2, 4, 8]
            elif optionname == 'docstrings':
                optiontype = 'enum'
                configs = ['ScalaDoc', 'JavaDoc']
            elif optionname == 'lineEndings':
                optiontype = 'enum'
                configs = ['preserve', 'unix', 'windows']
            elif optionname == 'importSelectors':
                optiontype = 'enum'
                configs = ['binPack', 'noBinPack', 'singleLine']
            elif optvalue in ['true', 'false']:
                optiontype = 'bool'
                configs = [True, False]
            else:
                continue
            options.append(option_make(optionname, optiontype, configs))
        self.styledefinition = styledef_make(options)

    def styletext(self, style):
        # type: (Style) -> str
        fragments = []
        for optionname, value in self.sorted_style(style).items():
            fragments.append('%s = %s' % (optionname, textrepr(value)))
        return '\n'.join(fragments) + '\n'

    def inlinestyletext(self, style):
        # type: (Style) -> str
        return self.styletext(style)

    def cmdargs_for_style(self, formatstyle, filename=None):
        # type: (Style, Optional[str]) -> List[str]
        assert isinstance(formatstyle, Style)
        cmdargs = []
        for optname, value in self.sorted_style(formatstyle).items():
            cmdargs.append('%s=%s' % (optname, textrepr(value)))
        config_str = '{%s}' % ','.join(cmdargs)
        cmdargs = ['--stdin', '--stdout', '--config-str', config_str]
        return cmdargs

        for optname, value in self.sorted_style(formatstyle).items():
            cmdargs.extend(self.cmdlineopts(optname, value))
        return cmdargs

    def should_report_error(self, job, jobres):
        # type: (ExeCall, ExeResult) -> bool
        if jobres.error is not None:
            return True
        return jobres.returncode != 0

    def valid_job_result(self, job, jobres):
        # type: (ExeCall, ExeResult) -> bool
        if jobres.error is not None:
            return False
        if jobres.returncode != 0:
            return False
        return True

    def variants_for(self, option):
        # type: (Option) -> List[Style]

        stylename = option_name(option)
        configs = option_configs(option)

        def kvpairs(vs):
            # type: (Iterable[OptionValue]) -> List[Style]
            return stylevariants(stylename, vs)

        if configs:
            return kvpairs(configs)
        return []

    def reformat(self, sourcefile, destfile, configfile):
        # type: (str, str, str) -> None
        data = readbinary(sourcefile)
        exeresult = run_executable(self.exe, ['--stdin', '--stdout', '--config', unifilename(configfile)],
                                   stdindata=data)
        writebinary(destfile, exeresult.stdout)

# ----------------------------------------------------------------------


class RfmtFormatter(CodeFormatter):
    """Formatter for:
    rfmt: A code formatter for R.
    (https://github.com/google/rfmt)
    """

    shortname = 'rfmt'
    configfilename = '.rfmtrc'
    language_exts = [['R', REXTS]]

    def __init__(self, exe, cache=None):
        super(RfmtFormatter, self).__init__(exe, cache=cache)

    def register_options(self):
        # type: () -> None
        options = []
        for optionname, optiontype, configs in [
            ('margin0', 'int', [0]),
            ('margin1', 'int', [80]),
            ('cost0', 'float', [0.01, 0.05, 0.1]),
            ('cost1', 'float', [10, 100, 300]),
            ('costb', 'float', [1, 2, 3, 4]),
            ('indent', 'int', [1, 2, 3, 4]),
            ('force_brace', 'bool', [True, False]),
            ('space_arg_eq', 'bool', [True, False]),
            ('adj_comment', 'float', [0.01, 0.5, 100]),
            ('adj_flow', 'float', [0.001, 0.3, 1000.0]),
            ('adj_call', 'float', [0.001, 0.01, 0.5, 1000]),
            ('adj_arg', 'float', [0.01, 1, 5, 10]),
            ('cpack', 'float', [0.0001, 0.001, 0.01, 25]),
        ]:
            options.append(option_make(optionname, optiontype, configs))
        self.styledefinition = styledef_make(options)

    def styletext(self, style):
        # type: (Style) -> str
        fragments = []
        for optionname, value in self.sorted_style(style).items():
            fragments.append('%s=%s' % (optionname, textrepr(value)))
        return '\n'.join(fragments) + '\n'

    def inlinestyletext(self, style):
        # type: (Style) -> str
        return self.styletext(style)

    def cmdargs_for_style(self, formatstyle, filename=None):
        # type: (Style, Optional[str]) -> List[str]
        assert isinstance(formatstyle, Style)
        cmdargs = ['--quiet', 'true']  # type: List[str]
        for optname, value in self.sorted_style(formatstyle).items():
            cmdargs.extend(self.cmdlineopts(optname, value))
        return cmdargs

    def cmdlineopts(self, optionname, value):
        # type: (str, str, OptionValue) -> List[str]
        return ['--' + optionname, textrepr(value)]

    def should_report_error(self, job, jobres):
        # type: (ExeCall, ExeResult) -> bool
        if jobres.error is not None:
            return True
        return jobres.returncode != 0

    def valid_job_result(self, job, jobres):
        # type: (ExeCall, ExeResult) -> bool
        if jobres.error is not None:
            return False
        if jobres.returncode != 0:
            return False
        return True

    def variants_for(self, option):
        # type: (Option) -> List[Style]

        stylename = option_name(option)
        configs = option_configs(option)

        def kvpairs(vs):
            # type: (Iterable[OptionValue]) -> List[Style]
            return stylevariants(stylename, vs)

        if configs:
            return kvpairs(configs)
        return []

    def reformat(self, sourcefile, destfile, configfile):
        # type: (str, str, str) -> None
        formatstyle = style_make()
        with open(configfile) as fp:
            for line in fp.readlines():
                line = line.rstrip()
                if line.startswith('#'):
                    continue
                parts = line.split('=')
                if len(parts) == 2:
                    optionname, value = parts
                    set_option(formatstyle, optionname, value)
        sourcedata = readbinary(sourcefile)
        data = self.formatcode(formatstyle, sourcedata, filename=sourcefile)
        if data is None:
            data = b''
        writebinary(destfile, data)

# ----------------------------------------------------------------------


class RustfmtFormatter(CodeFormatter):
    """Formatter for:
    rustfmt: A tool for formatting Rust code according to style guidelines.
    (https://github.com/rust-lang-nursery/rustfmt)
    """

    shortname = 'rustfmt'
    configfilename = 'rustfmt.toml'
    language_exts = [['Rust', RUSTEXTS]]

    def __init__(self, exe, cache=None):
        super(RustfmtFormatter, self).__init__(exe, cache=cache)

    def register_options(self):
        # type: () -> None
        """Parse options from text like this:
        Configuration Options:
                               verbose <boolean> Default: false
                                       Use verbose output

                         skip_children <boolean> Default: false
                                       Don't reformat out of line modules

                             max_width <unsigned integer> Default: 100
                                       Maximum width of each line
        """
        exeresult = run_executable(self.exe, ['--config-help'], cache=self.cache)
        options = []
        text = unistr(exeresult.stdout)
        for m in re.finditer(r'^\s*([a-z_]+)\s+(.*) Default: (\w+)', text, re.MULTILINE):
            optionname, typedesc, default = m.groups()
            configs = []  # type: ignore
            if optionname in ['verbose', 'report_todo', 'report_fixme']:
                continue
            if typedesc == '<boolean>':
                optiontype = 'bool'
                configs = [True, False]
            elif typedesc[:1] + typedesc[-1:] == '[]':
                optiontype = 'enum'
                configs = typedesc[1:-1].split('|')
            elif typedesc in ['<unsigned integer>', '<signed integer>']:
                optiontype = 'int'
                if optionname == 'ideal_width':
                    # Let's leave ideal_width (default 80) and only tweak max_width.
                    continue
                if optionname == 'max_width':
                    configs = list(inclusiverange(80, 100))
                elif optionname == 'tab_spaces':
                    configs = list(inclusiverange(1, 8))
                elif optionname == 'fn_call_width':
                    configs = list(inclusiverange(60, 90))
                elif optionname == 'struct_lit_width':
                    configs = list(inclusiverange(8, 20))
                elif optionname == 'closure_block_indent_threshold':
                    configs = [-1] + list(inclusiverange(1, 10))
            if not configs:
                continue
            options.append(option_make(optionname, optiontype, configs))
        self.styledefinition = styledef_make(options)

    def styletext(self, style):
        # type: (Style) -> str
        fragments = []
        for optionname, value in self.sorted_style(style).items():
            fragments.append('%s = %s' % (optionname, textrepr(value)))
        return '\n'.join(fragments) + '\n'

    def inlinestyletext(self, style):
        # type: (Style) -> str
        return self.styletext(style)

    def cmdargs_for_style(self, formatstyle, filename=None):
        # type: (Style, Optional[str]) -> List[str]
        assert isinstance(formatstyle, Style)
        configdata = bytestr(self.styletext(formatstyle))
        sha = shahex(configdata)
        cfg = os.path.join(tempfile.gettempdir(),
                           'whatstyle_rustfmt_%s/%s' % (sha, self.configfilename))
        try:
            dirpath = os.path.dirname(cfg)
            os.makedirs(dirpath)
            self.add_tempfile(dirpath)
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise
        if not self.tempfile_exists(cfg):
            writebinary(cfg, configdata)
            self.add_tempfile(cfg)
        cmdargs = ['--config-path', cfg]
        return cmdargs

    def should_report_error(self, job, jobres):
        # type: (ExeCall, ExeResult) -> bool
        if jobres.error is not None:
            return True
        return jobres.returncode != 0

    def valid_job_result(self, job, jobres):
        # type: (ExeCall, ExeResult) -> bool
        if jobres.error is not None:
            return False
        if jobres.returncode != 0:
            return False
        return True

    def variants_for(self, option):
        # type: (Option) -> List[Style]

        stylename = option_name(option)
        configs = option_configs(option)

        def kvpairs(vs):
            # type: (Iterable[OptionValue]) -> List[Style]
            return stylevariants(stylename, vs)

        if configs:
            return kvpairs(configs)
        return []

    def reformat(self, sourcefile, destfile, configfile):
        # type: (str, str, str) -> None
        formatstyle = style_make()
        with open(configfile) as fp:
            for line in fp.readlines():
                line = line.rstrip()
                if line.startswith('#'):
                    continue
                parts = re.split(r'\s+=\s+', line)
                if len(parts) == 2:
                    optionname, value = parts
                    set_option(formatstyle, optionname, value)
        sourcedata = readbinary(sourcefile)
        data = self.formatcode(formatstyle, sourcedata, filename=sourcefile)
        if data is None:
            data = b''
        writebinary(destfile, data)

# ----------------------------------------------------------------------
# Functions for the in-memory cache

FILECACHE = {}  # type: Dict[str, bytes]
NUMLINESCACHE = {}  # type: Dict[str, int]


def readbinary(filename):
    # type: (str) -> bytes
    with open(filename, 'rb') as fp:
        return fp.read()


def writebinary(filename, data):
    # type: (str, bytes) -> None
    with open(filename, 'wb') as fp:
        fp.write(data)


def get_cache_value(key, func, cachevar):
    # type: (str, Callable[[str], Any], Dict[str, Any]) -> Any
    data = cachevar.get(key)
    if data is None:
        data = func(key)
        cachevar[key] = data
    return data


def count_content_lines(data):
    # type: (bytes) -> int
    return len(list(data.splitlines()))


def count_lines(filename):
    # type: (str) -> int
    return count_content_lines(get_cached_file(filename))


def get_cached_file(filename):
    # type: (str) -> bytes
    return get_cache_value(filename, readbinary, FILECACHE)


def get_num_lines(filename):
    # type: (str) -> int
    return get_cache_value(filename, count_lines, NUMLINESCACHE)


def filesha(filename):
    # type: (str) -> bytes
    return shadigest(get_cached_file(filename))


def filemetadata(filename):
    # type: (str) -> Optional[FileMeta]
    p_filename = which(filename)
    if p_filename is None:
        return None
    filename = p_filename
    s = os.stat(filename)
    if filename != sys.executable:
        result = run_executable(filename, ['--version'])
        versionstring = result.stdout
    else:
        # filename is the Python interpreter itself
        versionstring = bytestr(sys.version)
    return FileMeta(filename, s.st_size, s.st_mtime, filesha(filename), versionstring)

# ----------------------------------------------------------------------


class KeyValueStore(object):

    def get(self, key):
        raise NotImplementedError

    def mget(self, keys):
        raise NotImplementedError

    def put(self, key, value):
        raise NotImplementedError

    def delete(self, key):
        raise NotImplementedError

    def close(self):
        raise NotImplementedError


class SqliteKeyValueStore(KeyValueStore):
    """A key-value store based on sqlite.
    """
    _sqcreate = """CREATE TABLE IF NOT EXISTS {tablename} (
                    {keycolumn} TEXT PRIMARY KEY,
                    {valuecolumn} BLOB%s)"""

    _sqtimestamp = """,
                    {timestampcolumn} DATETIME DEFAULT CURRENT_TIMESTAMP"""

    _sqget = 'SELECT {valuecolumn} FROM {tablename} WHERE {keycolumn} = ?'
    _sqmget = 'SELECT {keycolumn}, {valuecolumn} FROM {tablename} WHERE {keycolumn} IN (%s)'
    _sqput = 'REPLACE INTO {tablename} ({keycolumn}, {valuecolumn})' \
             ' VALUES (?, ?)'
    _sqdelete = 'DELETE FROM {tablename} WHERE {keycolumn} = ?'

    defaultprefix = 'kv'
    tabledescription = {
        'tablename': 'store',
        'keycolumn': 'key',
        'valuecolumn': 'value',
        'timestampcolumn': 'timestamp'
    }

    def __init__(self, database, tabledesc=None, timestamp=False):
        # type: (str, Optional[Dict[str, str]], bool) -> None
        self.database = database
        if tabledesc is None:
            tabledesc = self.prefixdesc()
        self.tabledesc = tabledesc
        timestampsql = self._sqtimestamp if timestamp else ''
        sqcreate = self._sqcreate % timestampsql
        self.kv_create = sqcreate.format(**tabledesc)
        self.kv_get = self._sqget.format(**tabledesc)
        self.kv_mget = self._sqmget.format(**tabledesc)
        self.kv_put = self._sqput.format(**tabledesc)
        self.kv_delete = self._sqdelete.format(**tabledesc)
        self._connection = None  # type: Optional[sqlite3.Connection]
        self.sqlite_limit_variable_number = 999
        self.support_mget = True

    @classmethod
    def prefixdesc(cls, desc=None, prefix=None):
        # type: (Optional[Dict[str, str]], Optional[str]) -> Dict[str, str]
        if desc is None:
            if prefix is None:
                prefix = cls.defaultprefix
            desc = cls.tabledescription
        eprefix = prefix if prefix is not None else ''
        return dict((k, eprefix + v) for k, v in desc.items())

    @property
    def conn(self):
        # type: () -> sqlite3.Connection
        if self._connection is not None:
            return self._connection
        connection = sqlite3.Connection(self.database)
        with connection:
            connection.execute(self.kv_create)
        self._connection = connection
        return connection

    def get(self, key):
        # type: (str) -> Optional[bytes]
        with self.conn as conn:
            row = conn.execute(self.kv_get, (key, )).fetchone()
            if row is not None:
                return row[0]
        return None

    __get = get

    def mget(self, keys):
        # type: (List[str]) -> List[Optional[bytes]]
        rows = []
        if self.support_mget:
            try:
                with self.conn as conn:
                    for somekeys in grouper(self.sqlite_limit_variable_number, keys):
                        keylist = list(somekeys)
                        questionmarks = ','.join(['?'] * len(keylist))
                        sql = self.kv_mget % questionmarks
                        for row in conn.execute(sql, keylist):
                            rows.append(row)
                resultdict = dict(rows)  # type: Dict[str, bytes]
                rget = resultdict.get
                return [rget(k) for k in keys]
            except sqlite3.OperationalError:
                self.support_mget = False
        return [self.__get(k) for k in keys]

    def put(self, key, value):
        # type: (str, bytes) -> None
        with self.conn as conn:
            conn.execute(self.kv_put, (key, sqlite3.Binary(value)))

    def delete(self, key):
        # type: (str) -> None
        with self.conn as conn:
            conn.execute(self.kv_delete, (key, ))

    def close(self):
        # type: () -> None
        self.conn.close()
        self._connection = None

# ----------------------------------------------------------------------


class DedupKeyValueStore(SqliteKeyValueStore):
    """A key-value store that deduplicates the values.
    """

    def __init__(self, database, tabledesc=None):
        # type: (str, Optional[Dict[str, str]]) -> None
        dedupdesc = self.prefixdesc(tabledesc, 'dds_')
        contentdesc = self.prefixdesc(tabledesc, 'ddc_')
        self.kvstore = SqliteKeyValueStore(database, tabledesc=contentdesc)
        super(DedupKeyValueStore, self).__init__(database, tabledesc=dedupdesc)

    def get(self, key):
        # type: (str) -> Optional[bytes]
        contentkey = super(DedupKeyValueStore, self).get(key)
        if contentkey is None:
            return None
        sha = binary_type(contentkey)
        return self.kvstore.get(unistr(sha))

    def mget(self, keys):
        # type: (List[str]) -> List[Optional[bytes]]
        if not keys:
            return []
        cached = []
        uncached = []  # type: List[Tuple[int, Optional[bytes]]]
        contentkeys = super(DedupKeyValueStore, self).mget(keys)
        for idx, contentkey in enumerate(contentkeys):
            if contentkey is None:
                uncached.append((idx, None))
            else:
                sha = binary_type(contentkey)
                cached.append((idx, unistr(sha)))
        if not cached:
            return [None for _, contentkey in uncached]
        indices, existing_keys = zip(*cached)
        existing_values = self.kvstore.mget(existing_keys)
        idx_value_pairs = sorted(uncached + list(zip(indices, existing_values)))
        return list([value for _, value in idx_value_pairs])

    def put(self, key, value):
        # type: (str, bytes) -> None
        sha = shahex(value)
        self.kvstore.put(sha, value)
        super(DedupKeyValueStore, self).put(key, bytestr(sha))

    def close(self):
        # type: () -> None
        self.kvstore.close()
        self.conn.close()

# ----------------------------------------------------------------------


class Cache(object):
    """A memory or on-disk cache backed an SqliteKeyValueStore.
    """
    ZLIB_COMPRESSION_LEVEL = 6

    def __init__(self, database, tabledesc=None):
        # type: (str, Optional[Dict[str, str]]) -> None
        self.cachefilename = database
        self.tabledesc = tabledesc
        self.exemetainfo = {}  # type: Dict[str, bytes]
        self.kvstore = KeyValueStore()
        self.open()

    def open(self):
        # type: () -> None
        self.kvstore = DedupKeyValueStore(self.cachefilename, tabledesc=self.tabledesc)

    def close(self):
        # type: () -> None
        self.kvstore.close()

    def get(self, key):
        # type: (str) -> Optional[bytes]
        return self.get_internal(key)

    def mget(self, keys):
        # type: (List[str]) -> List[Optional[bytes]]
        return self.mget_internal([k for k in keys])

    def set(self, key, value):
        # type: (str, bytes) -> None
        self.set_internal(key, value)

    def get_internal(self, key):
        # type: (str) -> Optional[bytes]
        value = self.kvstore.get(key)
        if value is not None:
            value = zlib.decompress(value)
        return value

    def mget_internal(self, keys):
        # type: (List[str]) -> List[Optional[bytes]]
        results = []
        values = self.kvstore.mget(keys)
        for value in values:
            if value is not None:
                value = zlib.decompress(value)
            results.append(value)
        return results

    def set_internal(self, key, value):
        # type: (str, bytes) -> None
        value = zlib.compress(value, self.ZLIB_COMPRESSION_LEVEL)
        self.kvstore.put(key, value)

    def digest_for_exe(self, exe):
        # type: (str) -> bytes
        digest = self.exemetainfo.get(exe)
        if digest is None:
            meta = filemetadata(exe)
            if meta is None:
                # Do not make repeated failed attempts to get the metadata.
                data = b''
            else:
                frags = [sysfilename(meta.filename), bytestr(str(meta.size)),
                         bytestr(str(meta.mtime)), meta.sha, meta.version]
                data = b'\x00'.join(frags)
            digest = shadigest(data)
            self.exemetainfo[exe] = digest
        return digest

    def mixtohash(self,
                  args=(),      # type: Sequence[AnyStr]
                  exe=None,     # type: Optional[str]
                  depfiles=(),  # type: Sequence[str]
                  hashobj=None  # type: Optional[Any]
                  ):
        # type: (...) -> Any
        if hashobj is None:
            hashobj = HASHFUNC()
        for filename in depfiles:
            hashobj.update(sysfilename(filename))
            hashobj.update(filesha(filename))
            hashobj.update(b'\x00')
        for arg in args:
            hashobj.update(sysfilename(arg))
            hashobj.update(b'\x00')
        if exe is not None:
            hashobj.update(self.digest_for_exe(exe))
        return hashobj

    @staticmethod
    def apply(func,         # type: Callable[..., bytes]
              args=(),      # type: Sequence[AnyStr]
              exe=None,     # type: Optional[str]
              depfiles=(),  # type: Sequence[str]
              cache=None    # type: Optional[Cache]
              ):
        """Applies func(*args) when the result is not present in the cache.
        The result of func(*args) must be bytes and must not be None which is used as
        cache-miss indicator. After evaluation of func the result is stored in the cache.
        """
        key, value = None, None
        if cache is not None:
            hashobj = cache.mixtohash(args, exe=exe, depfiles=depfiles)
            key = hashobj.hexdigest()
            value = cache.get(key)
        if value is None:
            value = func(*args)
            if key is not None:
                cache.set(key, value)
        return value

    def deletecache(self):
        # type: () -> int
        self.close()
        os.remove(self.cachefilename)
        return OK

# ----------------------------------------------------------------------


class HexKeyValueStore(KeyValueStore):
    """A key-value store that maps hexadecimal string keys to binary data.
    The data is stored in nested directories to limit the number of files
    per directory. The directory names are the first hex bytes of the key, e.g.:
    'c0/57/1e/c0571e55ca55e77eda7aba5e7001c011ec7ab1e5'
    """

    num_nibbles = int(len(shahex(b'')))
    re_hex = re.compile(r'[0-9a-z]{2}')
    re_sha = re.compile(r'[0-9a-z]{%d}' % num_nibbles)

    def __init__(self, cachedir, depth=3):
        # type: (str, int) -> None
        self.cachedir = cachedir
        self.depth = depth

    @staticmethod
    def hexkey_relpath(key, depth):
        # type: (str, int) -> str
        """Returns the relative path of the binary data file for a given key.
        >>> HexKeyValueStore.hexkey_relpath('c0571e55ca55e77eda7aba5e7001c011ec7ab1e5', 3)
        'c0/57/1e/c0571e55ca55e77eda7aba5e7001c011ec7ab1e5'
        """
        if not HexKeyValueStore.re_sha.match(key):
            key = shahex(bytestr(key))
        keybegin = key[:max(0, depth) * 2]
        return HexKeyValueStore.re_hex.sub(r'\g<0>/', keybegin, count=depth) + key

    def hexkey_filename(self, key):
        # type: (str) -> str
        """Returns the filename the binary data file for a given key.
        """
        relpath = normsep(unistr(self.hexkey_relpath(key, self.depth)))
        return os.path.join(self.cachedir, relpath)

    def get(self, key):
        # type: (str) -> Optional[bytes]
        filename = self.hexkey_filename(key)
        try:
            with open(filename, 'rb') as fp:
                return fp.read()
        except (IOError, OSError) as exc:
            if exc.errno != errno.ENOENT:
                raise
        return None

    def mget(self, keys):
        # type: (List[str]) -> List[Optional[bytes]]
        return [self.get(k) for k in keys]

    def put(self, key, value):
        # type: (str, bytes) -> None
        filename = self.hexkey_filename(key)
        dirpath = os.path.dirname(filename)
        try:
            os.makedirs(dirpath)
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise
        with open(filename, 'wb') as fp:
            fp.write(value)

    def delete(self, key):
        # type: (str) -> None
        filename = self.hexkey_filename(key)
        try:
            os.remove(filename)
        except (IOError, OSError) as exc:
            if exc.errno != errno.ENOENT:
                raise

    def close(self):
        # type: () -> None
        pass

    def deletedirs(self):
        # type: () -> int
        """Delete the cache directory recursively.
        """
        exit_code = OK
        for root, dirs, files in os.walk(self.cachedir, topdown=False):
            if 'whatstyle_cache' not in root:
                # Recursive deletion should be well rooted.
                return ERROR
            for f in files:
                if self.re_sha.match(f):
                    filename = os.path.join(root, f)
                    try:
                        os.remove(filename)
                    except OSError as exc:
                        reporterror('Error: cannot delete file "%s": %s' % (filename,
                                                                            str(exc)))
                        exit_code = ERROR
            for d in dirs:
                if self.re_hex.match(d):
                    directory = os.path.join(root, d)
                    try:
                        os.rmdir(directory)
                    except OSError as exc:
                        reporterror('Error: cannot delete directory "%s": %s' % (directory,
                                                                                 str(exc)))
                        exit_code = ERROR
        try:
            os.rmdir(self.cachedir)
        except OSError as exc:
            reporterror('Error: cannot delete cache directory "%s".' % self.cachedir)
            reporterror(str(exc))
            exit_code = ERROR
        return exit_code


class HexCache(Cache):
    """A cache backed an HexKeyValueStore.
    This is useful for Python interpreters like jython or some operating
    systems like FreeBSD that may not have an sqlite3 implementation.
    """

    def open(self):
        # type: () -> None
        self.kvstore = HexKeyValueStore(self.cachefilename)

    def deletecache(self):
        return self.kvstore.deletedirs()

# ----------------------------------------------------------------------


def default_cachepath():
    # type: () -> str
    return os.path.join(tempfile.gettempdir(), 'whatstyle_cache')

# ----------------------------------------------------------------------
# The SQLite based cache can store binary data and its main purpose
# is storing the return code, stdout and stderr of a process call,
# here we have two functions to transform between these triplets and single
# values.


def pack_exeresult(returncode, stdoutdata, stderrdata):
    # type: (int, bytes, bytes) -> bytes
    stdoutdata = stdoutdata or b''
    stderrdata = stderrdata or b''
    buf = bytestr('%d %d %d|' % (returncode, len(stdoutdata), len(stderrdata)))
    return buf + stdoutdata + stderrdata


def unpack_exeresult(buf):
    # type: (bytes) -> Tuple[int, bytes, bytes]

    def unpack_error():
        # type: () -> None
        raise ValueError('invalid buffer in unpack_exeresult')

    buf = binary_type(buf)
    pos = buf.find(b'|')
    if pos < 0:
        unpack_error()
    lengths, data = buf[:pos], buf[pos + 1:]
    try:
        numvalues = [int(s) for s in lengths.split()]
    except ValueError:
        numvalues = []
    if len(numvalues) != 3:
        unpack_error()
    returncode, outlen, errlen = numvalues
    if outlen + errlen != len(data):
        unpack_error()
    return returncode, data[:outlen], data[outlen:outlen + errlen]

# ----------------------------------------------------------------------


def colored_cmdargs(cmdargs, col):
    # type: (List[str], int) -> str
    return ' '.join([bgcolor(col, unifilename(arg)) for arg in cmdargs])


def log_popen(executable, cmdargs, stdindata, exeresult):
    # type: (str, List[str], bytes, ExeResult) -> None
    col = RED if exeresult.timedout or exeresult.error is not None else BLUE
    if DEBUG_POPEN:
        joinedargs = colored_cmdargs(cmdargs, col)
    else:
        joinedargs = ''
    dprint(DEBUG_POPEN, '%s %s' % (bgcolor(col, executable), joinedargs), prefix='\n')
    if exeresult.error is not None:
        iprint(INFO_PROCERRORS, 'info_procerrors: Calling %s %s' % (red(executable),
                                                                    joinedargs))
        iprint(INFO_PROCERRORS, red('  caused this error: %s' % exeresult.error))
    if exeresult.timedout:
        iprint(INFO_PROCERRORS, '%s %s' % (red(executable), joinedargs))
        iprint(INFO_PROCERRORS,
               red('  this process was killed after %.6f seconds' % (exeresult.runtime, )))
    stdinlen = len(stdindata) if stdindata is not None else 0
    if DEBUG_POPEN:
        runtime = ' runtime:%.6f' % exeresult.runtime if DEBUG_RUNTIME in args_debug else ''
        if exeresult.stdout is not None and exeresult.stderr is not None:
            dprint(DEBUG_POPEN, 'len(stdin):%d => returncode:%s len(stdout):%d'
                   ' len(stderr):%d%s' % (stdinlen, exeresult.returncode,
                                          len(exeresult.stdout), len(exeresult.stderr),
                                          runtime))
        for name, data in [('stdin', stdindata), ('stdout', exeresult.stdout),
                           ('stderr', exeresult.stderr)]:
            if data:
                bdata = bytestr(data)
                dprint(DEBUG_POPENIO, bytestr('%s:"""\\\n' % name) + bdata + b'"""')
        dprint(DEBUG_POPENIO, '------------')


def call_popen(exe, cmdargs, stdindata=None):
    # type: (str, List[str], Optional[bytes]) -> ExeResult

    def error_result(exc):
        try:
            errortext = exc.strerror
        except AttributeError:
            errortext = None
        errortext = errortext or str(exc)
        if WINOS:
            errortext = errortext.replace('%1', '"%s"' % exe)
        return make_exeresult(error=errortext)

    if stdindata is not None:
        stdindata = bytestr(stdindata)
    uniargs = [exe] + cmdargs
    if PY3 and WINOS:
        # subprocess.Popen on Windows uses list2cmdline which needs strings.
        args = [unifilename(c) for c in uniargs]  # type: List[Union[str, bytes]]
    else:
        args = [sysfilename(c) for c in uniargs]

    t0 = time.time()
    try:
        proc = subprocess.Popen(args,  # type: ignore
                                stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
    except (IOError, OSError) as exc:
        return error_result(exc)
    timed_out = []

    def terminate(p):
        # type: (subprocess.Popen) -> None
        timed_out.append(True)
        p.kill()

    timer = threading.Timer(TIMEOUT_SECONDS, terminate, [proc])
    timer.start()
    try:
        stdout, stderr = proc.communicate(input=stdindata)
    except (IOError, OSError) as exc:
        return error_result(exc)
    finally:
        timer.cancel()
    timer.join()
    runtime = time.time() - t0
    return make_exeresult(proc.returncode, stdout, stderr, runtime, bool(timed_out))


def run_executables(execalls, cache=None, ccmode=CC_PROCESSES):
    # type: (List[ExeCall], Optional[Cache], str) -> Iterator[ExeResult]
    """Run executables in parallel.

    Some of the results for the execalls may be found in the cache
    so we put these aside in cachedresults.

    Each result is yield as soon as available.
    """

    def c2exeresult(value):
        # type: (bytes) -> ExeResult
        returncode, stdout, stderr = unpack_exeresult(value)
        return make_exeresult(returncode, stdout, stderr)

    def exeresult2c(exeresult):
        # type: (ExeResult) -> bytes
        return pack_exeresult(exeresult.returncode, exeresult.stdout, exeresult.stderr)

    # Package the execalls for eventuall multiprocessing
    args_lists = [((ec.exe, ec.cmdargs), {'stdindata': ec.stdindata})
                  for ec in execalls]  # type: List[CallArgs]

    cachedresults = []
    jobs = []  # type: List[CallArgs]
    keys = []  # type: List[str]
    jobindices = []  # type: List[int]
    if cache is not None:
        qkeys = [execall_hash(ec, cache) for ec in execalls]
        qresults = cache.mget(qkeys)

        for idx, (arg, key, cvalue) in enumerate(izip(args_lists, qkeys, qresults)):
            if cvalue is not None:
                cachedresults.append((idx, c2exeresult(cvalue)))
            else:
                keys.append(key)
                jobs.append(arg)
                jobindices.append(idx)
    else:
        jobs = args_lists
        jobindices = list(range(len(jobs)))

    jobiter = iter_parallel(call_popen, jobs, ccmode=ccmode)

    def jobresultiter():
        # type: () -> Iterator[Tuple[int, ExeResult]]
        for idx, (jobidx, job, result) in enumerate(izip(jobindices, jobs, jobiter)):
            if cache is not None:
                cache.set(keys[idx], exeresult2c(result))
            (executable, cmdargs), kwargs = job
            log_popen(executable, cmdargs, kwargs['stdindata'], result)
            yield jobidx, result

    for idx, result in heapq.merge(iter(cachedresults), jobresultiter()):
        yield result


def run_executable(executable,      # type: str
                   cmdargs,         # type: Iterable[str]
                   stdindata=None,  # type: Optional[bytes]
                   depfiles=(),     # type: Optional[List[str]]
                   cache=None       # type: Optional[Cache]
                   ):
    # type: (...) -> ExeResult
    jobs = [make_execall(executable, cmdargs, stdindata=stdindata, depfiles=depfiles)]
    results = list(run_executables(jobs, cache=cache, ccmode=CC_OFF))
    return results[0]


def execall_hash(ec, cache):
    # type: (ExeCall, Cache) -> str
    stdindata = ec.stdindata or b''
    args = [sysfilename(ec.exe)]
    args.extend([sysfilename(a) for a in ec.cmdargs])
    args.append(stdindata)
    hashobj = cache.mixtohash(args, exe=ec.exe, depfiles=ec.depfiles)
    return hashobj.hexdigest()

# ----------------------------------------------------------------------


def iter_stylecombos(formatter, ignoreopts=()):
    # type: (CodeFormatter, Sequence[str]) -> Iterator[FormatOption]
    for option in styledef_options(formatter.styledefinition):
        if option_name(option) in ignoreopts:
            continue
        stylecombo = formatter.variants_for(option)
        if stylecombo:
            yield FormatOption(stylecombo)


def contains_all_options(optiongroup, parentstyle, matchvalues=False):
    # type: (Dict[Any, Any], Style, bool) -> bool
    """Returns true if all options in optiongroup are present in parentstyle.
    If matchvalues is True, the values in optiongroup must also match
    those in the parentstyle.
    """
    for optionname, value in optiongroup.items():
        if optionname not in parentstyle:
            return False
        if isinstance(value, dict):
            parent_suboptions = parentstyle[optionname]
            if not contains_all_options(value, parent_suboptions, matchvalues=matchvalues):
                return False
        elif matchvalues:
            pvalue = parentstyle.get(optionname)
            if type(pvalue) != type(value):
                return False
            if pvalue != value:
                return False
    return True


def deep_update(parentstyle, optiongroup):
    # type: (Style, Dict[Any, Any]) -> Style
    """Update the nested dict parentstyle with the nested dict optiongroup.
    """
    for optionname, value in optiongroup.items():
        if isinstance(value, dict):
            try:
                parent_suboptions = parentstyle[optionname]
                deep_update(parent_suboptions, value)
            except KeyError:
                parentstyle[optionname] = value
        else:
            parentstyle[optionname] = value
    return parentstyle


def copy_with_optgroup(style, optgroup):
    # type: (Style, Style) -> Style
    assert isinstance(style, Style)
    return deep_update(Style(copy.deepcopy(style)), optgroup)


def deep_difference(a, b):
    # type: (Dict[Any, Any], Dict[Any, Any]) -> Tuple[Dict[Any, Any], Dict[Any, Any]]
    """Returns a pair of dicts containing items that are unique to a and b respectively.

    >>> pprint(deep_difference({'left': 'l', 'common': 'c', 'right': 'r'}, \
                               {'left': 't', 'common': 'c'}))
    ({'left': 'l', 'right': 'r'}, {'left': 't'})
    >>> pprint(deep_difference({'nested': {'common': 'c', 'option1': True}, 'option2': 4}, \
                               {'nested': {'common': 'c', 'option1': False, \
                                           'option3': 'present'}}))
    ({'nested': {'option1': True}, 'option2': 4},
     {'nested': {'option1': False, 'option3': 'present'}})
    """
    au = {}
    bu = {}
    for k in set(list(a.keys()) + list(b.keys())):
        v = a.get(k)
        bv = b.get(k)
        if v is None:
            # Key not present in a
            bu[k] = bv
        elif bv is None:
            # Key not present in b
            au[k] = v
        elif type(v) != type(bv):
            au[k] = v
            bu[k] = bv
        elif isinstance(v, dict):
            # Recursively compare the two value dicts.
            nau, nbu = deep_difference(v, bv)
            if nau:
                au[k] = nau
            if nbu:
                bu[k] = nbu
        elif v != bv:
            # The value changed
            au[k] = v
            bu[k] = bv
    return au, bu


def distquality(distance):
    # type: (Sequence[int]) -> Tuple[int, ...]
    """Distance with last element (attemptnr) removed."""
    return tuple(distance[0:-1])


def distdifference(metric1, metric2):
    # type: (Sequence[int], Sequence[int]) -> Sequence[int]
    return tuple([m1 - m2 for m1, m2 in zip(metric1, metric2)])


def subst_filename(filename, args):
    # type: (str, Sequence[str]) -> List[str]
    """Replace occurrences of '#FILENAME#' in the strings of the list args with filename.
    """
    return [a.replace(FILENAME_SUBST, filename) for a in args]


def split_reffiles(references, filenames):
    # type: (bool, List[str]) -> Tuple[List[str], List[str]]
    """Splits [file1, reffile1, file2, reffile2] into [file1, file2], [reffile1, reffile2]
    when references is True.
    When references is False returns the pair (filenames, filenames).
    """
    if not references:
        return filenames, filenames
    assert len(filenames) % 2 == 0
    files = []
    refs = []
    for filename, reffilename in grouper(2, filenames):
        files.append(filename)
        refs.append(reffilename)
    return files, refs

# ----------------------------------------------------------------------
# Functions to convert ANSI text into HTML.

ANSIHTML_START = """\
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN"
          "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta http-equiv="Content-type" content="text/html; charset=utf-8" />
<title>whatstyle variants comparison</title>
<style type="text/css">
html, body, pre {
    background-color: #000;
    color: #fff;
    font: 16px monospace;
    line-height: 1.2em;
    margin: 0;
    padding: 0;
    border: 0;
}
span {
    display: inline-block;
}
.ansi30 { color: #000000; }
.ansi31 { color: #c91b00; }
.ansi32 { color: #00c200; }
.ansi33 { color: #c7c400; }
.ansi34 { color: #0225c7; }
.ansi35 { color: #ca30c7; }
.ansi36 { color: #00c5c7; }
.ansi37 { color: #c7c7c7; }
.ansi40 { background-color: #000000; }
.ansi41 { background-color: #c91b00; }
.ansi42 { background-color: #00c200; }
.ansi43 { background-color: #c7c400; }
.ansi44 { background-color: #0225c7; }
.ansi45 { background-color: #ca30c7; }
.ansi46 { background-color: #00c5c7; }
.ansi47 { background-color: #c7c7c7; }
</style>
</head>
<body>
<pre>
"""

ANSIHTML_END = """\
</pre>
</body>
</html>
"""

re_ansi = re.compile(r'\x1b\[(\d*(?:;\d+)*)m|\n', re.MULTILINE)


def generate_ansihtml(source):
    # type: (str) -> Iterable[str]
    foreground = None
    background = None
    start = None  # type: Optional[int]
    lastpos = 0

    def addhtml(textfragment):
        # type: (str) -> str
        if not textfragment:
            return textfragment
        classes = []  # type: List[str]
        if foreground:
            classes = [foreground]
        if background:
            classes.append(background)
        classtext = ' '.join(classes)
        if classtext:
            classtext = ' class="%s"' % classtext
        esctext = cgi.escape(textfragment)
        # Replace spaces with non-breaking spaces in whitespace-only texts.
        esctext = esctext.replace(' ', '&nbsp;')
        return '<span%s>%s</span>' % (classtext, esctext)

    for m in re_ansi.finditer(source):
        # Emit previous text
        start, end = m.span()
        ansicode = m.group(1)
        text = source[lastpos:start]
        if text:
            yield addhtml(text)
        if ansicode is None:
            yield '\n'
        else:
            for code in ansicode.split(';'):
                num = int(code or '0')
                if num == 0:
                    foreground = None
                    background = None
                elif 30 <= num <= 37:
                    foreground = 'ansi%s' % num
                elif 40 <= num <= 47:
                    background = 'ansi%s' % num
        lastpos = end
    if start is None:
        return
    text = source[lastpos:start]
    if text:
        yield addhtml(text)


def ansi2html(ansidata, enc='utf-8'):
    # type: (bytes, str) -> str
    source = surrdecode(ansidata, enc=enc)
    start = ANSIHTML_START.replace('charset=utf-8', 'charset=%s' % enc)
    parts = [unistr(start)]
    for fragment in generate_ansihtml(source):
        parts.append(fragment)
    parts.append(unistr(ANSIHTML_END))
    return ''.join(parts)

# ----------------------------------------------------------------------
# Functions to display style variants.


def show_variants(style,               # type: Style
                  formatter,           # type: CodeFormatter
                  filenames,           # type: List[str]
                  ignoreopts=(),       # type: Sequence[str]
                  display='ansi',      # type: str
                  enc='utf-8',         # type: str
                  condensed=True,      # type: bool
                  numhunks=1,          # type: int
                  numlines=2,          # type: int
                  wrapcolumn=0,        # type: int
                  linenumbers=False,   # type: bool
                  savehtml=False,      # type: int
                  outputfile=None,     # type: bool
                  fp=None,             # type: Optional[IO]
                  ccmode=CC_PROCESSES  # type: str
                  ):
    # type: (...) -> int
    """Reformats the files according to style with all option values. Then it groups the
    different results and generates an overview about what source code differences result from
    the different options.
    """
    ret = OK
    cache = formatter.cache

    varfiles = []
    for filename, (job, jobres) in zip(filenames,
                                       format_with_styles(formatter, [style],
                                                          filenames,
                                                          cache=cache,
                                                          ccmode=ccmode)):
        if formatter.valid_job_result(job, jobres):
            tmpfile = shatempfile(filename, jobres.stdout)
            formatter.add_tempfile(tmpfile, mode=GLOBALTMP)
            varfiles.append(tmpfile)

    normedfiles, normstyle = varfiles, style
    optstyles = [[normstyle]]
    for option in styledef_options(formatter.styledefinition):
        if option_name(option) in ignoreopts:
            continue
        stylecombo = formatter.variants_for(option)
        if stylecombo:
            styles = [copy_with_optgroup(normstyle, optgroup) for optgroup in stylecombo
                      if not formatter.contains_major_style(optgroup)]
            if len(styles) > 0:
                optstyles.append(styles)

    # Reformat the source with every values of every options.
    runs = format_with_styles(formatter,
                              itertools.chain.from_iterable(optstyles),
                              normedfiles,
                              cache=cache,
                              ccmode=ccmode)

    result = group_differences(normstyle, formatter, optstyles, runs, len(normedfiles),
                               numlines, condensed)
    all_stylediff_pairs, num_relevant_options = result

    normstyletext = formatter.styletext(normstyle)
    fmtheader = formatter.exe + '\n\n' + normstyletext
    iprint(INFO_USER, green('\n' + fmtheader), fp=fp)
    sys.stdout.flush()
    sys.stderr.flush()
    alltables = []
    htmldiffer = HtmlMultiDiff(tabsize=8, wrapcolumn=wrapcolumn)
    headerhtml = '<pre>' + cgi.escape(unistr(fmtheader)) + '</pre>'
    legend = display == 'html'
    prev_progress = ''
    prev_tidx = None
    for tables, tidx, total in iter_tables(all_stylediff_pairs,
                                           enc=enc,
                                           numhunks=numhunks,
                                           numlines=numlines,
                                           wrapcolumn=wrapcolumn,
                                           ccmode=ccmode):
        alltables.extend(tables)
        if display == 'ansi':
            html = make_custom_html(htmldiffer, headerhtml, tables, enc=enc, legend=legend)
            htmldiff2ansi(html, enc, linenumbers=linenumbers, fp=fp)
            sys.stdout.flush()
        else:
            if tidx != prev_tidx:
                prev_tidx = tidx
                ptext = progresspair(tidx + 1, total)
                if tidx + 1 == total:
                    ptext += '\n'
                prev_progress = report_text('\rCalculating style option differences %s' %
                                            ptext,
                                            prev=prev_progress)

    if display in ['html', 'ansihtml']:
        html = make_custom_html(htmldiffer, headerhtml, alltables, enc=enc, legend=legend)
        htmldata = unescape_ill_surrencode(html, enc=enc)
        htmldata = translate_non_sgml_chars(htmldata, enc=enc)
        if display == 'ansihtml':
            html = surrdecode(htmldata, enc=enc)
            htmldata = html2ansihtml(html, fmtheader + '\n', enc=enc, linenumbers=linenumbers)
        if not outputfile:
            fd, outputfile = tempfile.mkstemp(suffix='.html', prefix='whatstyle_')
            os.close(fd)
        else:
            outputfile = os.path.abspath(outputfile)
        writebinary(outputfile, htmldata)
        iprint(INFO_USER, 'The HTML was written to "%s"' % unifilename(outputfile), fp=fp)
        if display in ['html', 'ansihtml'] and not savehtml:
            url = urljoin('file:', pathname2url(outputfile))
            if not webbrowser.open(url):
                ret = ERROR

    iprint(INFO_USER, '\n' + green('Summary:'), fp=fp)
    iprint(INFO_USER,
           green('Found %s options where a different value changed the result.' %
                 num_relevant_options),
           fp=fp)
    iprint(INFO_USER,
           green('From these we generated %s style differences.' % len(alltables)),
           fp=fp)
    return ret


def html2ansihtml(html, header='', enc='utf-8', linenumbers=False):
    # type: (str, str, str, bool) -> bytes
    buf = BytesIO()
    htmldiff2ansi(html, enc, linenumbers=linenumbers, fp=buf)
    ansidata = buf.getvalue()
    if header:
        ansidata = bytestr(header) + buf.getvalue()
    resulthtml = ansi2html(ansidata, enc=enc)
    htmldata = surrencode(resulthtml, enc=enc)
    return htmldata


def make_custom_html(htmldiffer, headerhtml, tables, enc='utf-8', legend=True):
    # type: (HtmlMultiDiff, str, List[str], str, bool) -> str
    extracss = """
        html, body, pre {
            font: 16px monospace;
            line-height: 1.2em;
            margin: 0;
            padding: 0;
            border: 0;
        }
        th.diff_header {text-align:left; vertical-align: text-top;}
    """
    alltables = '<br/>\n'.join([''] + tables) + '\n'
    alltables = '%s\n%s' % (headerhtml, alltables)
    return make_html(htmldiffer, alltables, enc=enc, extracss=extracss, legend=legend)


def iter_tables(all_stylediff_pairs,  # type: List[StyleDiffSourcePairs]
                enc='utf-8',          # type: str
                numhunks=1,           # type: int
                numlines=2,           # type: int
                wrapcolumn=0,         # type: int
                ccmode=CC_PROCESSES   # type: str
                ):
    # type: (...) -> Iterator[Tuple[List[str], int, int]]

    def left_diff(sdp):
        # type: (StyleDiffSourcePairs) -> str
        return '\n'.join(set([sdtexts[1] for sdtexts in sdp.keys()]))

    def sdkeys(item):
        # type: (StyleDiffSourcePairs) -> List[bytes]
        return list(item.keys())

    idx = 0
    grouped_sdpairs = itertools.groupby(all_stylediff_pairs, left_diff)
    groups = []  # type: List[CallArgs]
    grouped_sdp = sorted([(key, list(pairs)) for key, pairs in grouped_sdpairs])
    for sdleft, stylediff_pairs in grouped_sdp:
        args_lists = []
        for sdpairs in sorted(stylediff_pairs, key=sdkeys):
            for from_to_texts, pairs in sorted(sdpairs.items()):
                args_lists.append((from_to_texts, pairs, numhunks, numlines, wrapcolumn, idx,
                                   enc))
                idx += 1
        grouparg = (args_lists, ), {}  # type: CallArgs
        groups.append(grouparg)
    for tidx, tables in enumerate(iter_parallel(calc_diff_groups, groups, ccmode=ccmode)):
        yield tables, tidx, len(groups)


def calc_diff_groups(args_lists):
    # type: (List[Tuple[TextPair, List[BytesPair], int, int, int, int, str]]) -> List[str]
    """Yields diff tables with the minimum number of unique diff hunks needed to show the
    difference between option values.

    Let us look at an example what this means.
    The best style uses the value Attach for the option BreakBeforeBraces.
    Changing this option to the 6 different values
    Linux, Stroustrup, WebKit, Mozilla, GNU, Allman for the test example file lstate.c
    only produces 4 different results: {Linux,Stroustrup,WebKit}, Mozilla, GNU, Allman.
    We would like to show only as many hunks as needed that the difference of these 4
    different option values between themselves is displayed clearly.

    A diff we get from HtmlDiff consists of a number of hunkpairs.
    First we iterate over all differences that has BreakBeforeBraces=Attach on the left
    side and register all style differences that contain a certain hunkpair.

    Let's say the 'Attach vs Allman' diff has the hunkpairs hunkA, hunkB, hunkC
    and 'Attach vs GNU' has the hunkpairs hunkA, hunkB, hunkD.
    When we iterate over the hunks for display, we show hunkA because this is new for the
    set([Allman, GNU]). We skip hunkB because it is the same set.
    We display hunkC which is new as set([Allman]) and hunkD as well set([GNU]).
    In this example 3-4 hunks are sufficient to display all unique sets of this option.
    """
    sdhunks = defaultdict(set)  # type: Dict[str, Set[TextPair]]
    all_htmldiffs = []
    tables = []
    r_diffparts = r'(<td nowrap="nowrap">.*?</td>)|(<tr>)|(<td class="diff_header">)'
    re_diffparts = re.compile(r_diffparts)
    for from_to_texts, pairs, numhunks, numlines, wrapcolumn, uniqueidx, enc in args_lists:
        htmldiffs = []
        fromtext = unifilename(from_to_texts[0])
        totext = unifilename(from_to_texts[1])
        for tablestart, tbody, tableend in table_iter(pairs,
                                                      uniqueidx,
                                                      enc=enc,
                                                      fromdesc=fromtext,
                                                      todesc=totext,
                                                      numlines=numlines,
                                                      wrapcolumn=wrapcolumn):
            # Extract the textual differences from the diff hunks.
            fragments = []
            for m in re_diffparts.finditer(tbody):
                fragments.append(m.group(0))
            relevantdiffs = ''.join(fragments)
            # Register the stylediffs that apply for each individual diff hunk.
            sdhunks[relevantdiffs].add(from_to_texts)
            htmldiffs.append((relevantdiffs, tablestart, tbody, tableend))
        all_htmldiffs.append((htmldiffs, numhunks))

    for htmldiffs, numhunks in all_htmldiffs:
        tbodies = []
        uniquesets = set()  # type: Set[Tuple[Tuple[str, str], ...]]
        bodyparts = []
        num_crucial = 0
        for relevantdiffs, start, tbody, end in htmldiffs:
            hunkstyles = tuple(sorted(sdhunks.get(relevantdiffs)))
            if hunkstyles not in uniquesets:
                uniquesets.add(hunkstyles)
                crucial = True
                num_crucial += 1
            else:
                crucial = False
            bodyparts.append((start, tbody, end, crucial))

        if numhunks < 0:
            numhunks = len(bodyparts)
        num_uncrucial = numhunks - num_crucial
        # We always show all crucial hunks and additionally as many uncrucial ones
        # as numhunks permits.
        start, tbody, end = '', '', ''
        for start, tbody, end, crucial in bodyparts:
            if crucial:
                tbodies.append(tbody)
            elif num_uncrucial > 0:
                tbodies.append(tbody)
                num_uncrucial -= 1
        if tbodies:
            table = ''.join([start] + tbodies + [end])
            tables.append(table)
    return tables


def table_iter(pairs,            # type: List[BytesPair]
               uniqueidx,        # type: int
               enc='utf-8',      # type: str
               fromdesc='',      # type: str
               todesc='',        # type: str
               numlines=2,       # type: int
               wrapcolumn=0      # type: int
               ):
    # type: (...) -> Iterator[Tuple[str, str, str]]
    htmldiffer = HtmlMultiDiff(tabsize=8, wrapcolumn=wrapcolumn)
    htmldiffer.uniqueidx = uniqueidx
    table = htmldiffer.table_from_pairs(pairs,
                                        enc,
                                        fromdesc=fromdesc,
                                        todesc=todesc,
                                        context=True,
                                        numlines=numlines)
    for tablestart, tbody, tableend in iter_tbodies(table):
        yield tablestart, tbody, tableend


def iter_tbodies(table):
    # type: (str) -> Iterator[Tuple[str, str, str]]
    fragments = re.split(r'</?tbody>', table, flags=re.MULTILINE)
    if len(fragments) <= 1:
        return
    tbodies = fragments[1:-1:2]
    tablestart, tableend = fragments[0], fragments[-1]
    for tbody in tbodies:
        yield tablestart, '<tbody>%s</tbody>\n        ' % tbody, tableend


def concat_sources(s, numlines=2):
    # type: (Iterable[bytes], int) -> bytes
    sep = b'\n\n' * numlines
    return sep.join(s)


def group_differences(normstyle,  # type: Style
                      formatter,  # type: CodeFormatter
                      optstyles,  # type: List[List[Style]]
                      runs,  # type: Iterable[Tuple[ExeCall, ExeResult]]
                      num_files,  # type: int
                      numlines,  # type: int
                      condensed  # type: bool
                      ):
    # type: (...) -> Tuple[List[StyleDiffSourcePairs], int]
    """Returns (all_stylediff_pairs, num_relevant_options).
    all_stylediff_pairs a list of StyleDiffSourcePairs where the key is the textual style
    difference between contentA and contentB and the values are a list of (contentA, contentB)
    pairs. num_relevant_options is the number of options where tweaking a value actually made
    a difference.
    """
    # Regroup the flat result list into the styleruns of each option.
    num_relevant_options = 0
    basesource = None
    all_stylediff_pairs = []
    all_option_style_runs = []
    for option_style_runs in iter_option_style_runs(formatter, optstyles, num_files, runs):
        variants = {}
        contentstyles = defaultdict(list)  # type: Dict[bytes, List[Style]]
        for _, osrs in itertools.groupby(option_style_runs, operator.itemgetter(0)):
            osrslist = list(osrs)
            fmtsources = [jobres.stdout
                          for _, _, _, (job, jobres) in osrslist]  # type: ignore
            fmtsource = concat_sources(fmtsources, numlines=numlines)
            styleindices = set()  # type: Set[int]
            for styleidx, rstyle, _, _ in osrslist:
                if styleidx in styleindices:
                    continue
                styleindices.add(styleidx)
                variants[fmtsource] = rstyle
                if rstyle == normstyle:
                    basesource = fmtsource
                contentstyles[fmtsource].append(rstyle)

        if len(variants) <= 1:
            # This option has either no valid runs or just one single variant.
            # Either way there is no difference to be shown.
            continue

        all_option_style_runs.append(contentstyles)

    assert basesource is not None
    for contentstyles in all_option_style_runs:
        num_relevant_options += 1
        stylediff_pairs = StyleDiffSourcePairs()
        lstyles = contentstyles.get(basesource, [])
        # Iterate over all content that made a difference compared with the original.
        for fmtsource, rstyles in contentstyles.items():
            if fmtsource == basesource:
                continue
            descs_from = OrderedDict()  # type: OrderedDict[str, Style]
            descs_to = OrderedDict()  # type: OrderedDict[str, Style]
            for rstyle in rstyles:
                # Record all style differences between two differently formatted files.
                for lstyle in lstyles:
                    unique_from, unique_to = deep_difference(lstyle, rstyle)
                    style_from = style_make(unique_from)
                    style_to = style_make(unique_to)
                    descs_from[formatter.styletext(style_from)] = style_from
                    descs_to[formatter.styletext(style_to)] = style_to
            if not descs_from and not descs_to:
                # No style difference
                continue
            a = basesource
            b = fmtsource
            stylediff_pairs.add_stylediff(formatter, a, b, descs_from.values(),
                                          descs_to.values(), condensed)
        all_stylediff_pairs.append(stylediff_pairs)
    return all_stylediff_pairs, num_relevant_options


class StyleDiffSourcePairs(OrderedDict):
    """An OrderedDict that maps style differences to lists of pairs (a, b) where a is the
    base source code and b a reformatted version of a.
    """

    def __init__(self, *args, **kwargs):
        # type: (*Any, **Any) -> None
        super(StyleDiffSourcePairs, self).__init__(*args, **kwargs)

    def add_stylediff(self, formatter, a, b, from_styles, to_styles, condensed):
        # type: (CodeFormatter, bytes, bytes, Iterable[Style], Iterable[Style], bool) -> None
        from_to_texts = (styles_to_html(formatter, from_styles, condensed),
                         styles_to_html(formatter, to_styles, condensed))
        sourcepairs = self.get(from_to_texts)
        if sourcepairs is None:
            sourcepairs = []
            self[from_to_texts] = sourcepairs
        sourcepairs.append((a, b))


def keypaths(style):
    # type: (Style) -> str
    """Returns a comma separated list of style keys.
    """
    fragments = []
    for key, value in sorted(style.items()):
        if isinstance(value, Style):
            fragments.append(key + '.' + keypaths(value))
        else:
            fragments.append(key)
    return text_type(', '.join(fragments))


def styles_to_html(formatter, styles, condensed):
    # type: (CodeFormatter, Iterable[Style], bool) -> str
    equivalents = condense_option_values(formatter, styles, condensed)
    styletexts = [formatter.styletext(s) for s in equivalents if s]
    fragments = [cgi.escape(unistr(e)) for e in styletexts]
    or_join = unistr("------------ or ------------\n").join
    html = '<pre>' + or_join(fragments).replace('\n', '<br/>') + '</pre>'
    return html


def condense_option_values(formatter, styles, condensed):
    # type: (CodeFormatter, Iterable[Style], bool) -> List[Style]
    # Use idx to prevent sorted to look at unorderable dicts.
    triplets = [(keypaths(style), idx, style) for idx, style in enumerate(styles)]
    triplets = sorted(triplets)
    pairs = [(kp, style) for kp, idx, style in triplets]
    if condensed:
        equivalents = []
        for kpaths, kp_styles in itertools.groupby(pairs, operator.itemgetter(0)):
            styles = [kps[1] for kps in kp_styles]
            for style in group_consecutive(formatter, styles, condensed):
                equivalents.append(style)
    else:
        equivalents = [style for _, style in pairs]
    return equivalents


def group_consecutive(formatter, styles, condensed):
    # type: (CodeFormatter, Iterable[Style], bool) -> List[Style]
    """Compress similar consecutive styles to a condensed version, e.g.
    ColumnLimit: 79-81
    or
    UseTab: ForIndentation, Never
    """
    prevstyle = None
    commonkeypath = ''
    results = []  # type: List[Tuple[str, Style, Any]]
    combined = []  # type: List[OptionValue]
    for style in styles:
        if prevstyle is not None:
            unique_from, unique_to = deep_difference(prevstyle, style)
            akeys, avalues = list(unique_from.keys()), list(unique_from.values())
            bkeys, bvalues = list(unique_to.keys()), list(unique_to.values())
            if akeys == bkeys and len(akeys) == 1 and OPTION_PRESENT not in avalues + bvalues:
                commonkeypath = akeys[0]
                if isinstance(avalues[0], dict):
                    # This is a nested option
                    nested = condense_option_values(formatter, [style_make(avalues[0]),
                                                                style_make(bvalues[0])],
                                                    condensed)
                    combined.extend(nested)
                else:
                    combined.append(avalues[0])
                    combined.append(bvalues[0])
            else:
                # This style cannot be combined with the previous style
                results.append((commonkeypath, prevstyle, combined))
                combined = []
        prevstyle = style

    results.append((commonkeypath, prevstyle, combined))  # type: ignore
    resultstyles = []
    for commonkeypath, style, combined in results:
        if combined:
            combined = group_ranges(combined)
            # The combined values might be in a nested option.
            path = commonkeypath.split('.')
            nstyle = style
            while len(path) > 1:
                nstyle = nstyle[path.pop(0)]
            if len(combined) > 1:
                values = ', '.join([text_type(s) for s in combined])
            else:
                # This could be a nested option
                values = combined[0]  # type: ignore
            nstyle[path[0]] = values
        resultstyles.append(style)
    return resultstyles


def group_ranges(elements):
    # type: (List[OptionValue]) -> List[OptionValue]
    """Group integer elements into ranges but keep the non-integer values as well, e.g.
    [78, 79, 80, 81, 'true', 'false'] => ['78-81', 'true', 'false']
    """
    start = None  # type: Optional[OptionValue]
    end = None  # type: Optional[OptionValue]
    groups = []  # type: List[OptionValue]
    prev = None  # type: Any
    for x in elements:
        if x == prev:
            # Ignore consecutive duplicates.
            continue
        prev = x
        if isinstance(x, int):
            if start is None:
                start, end = x, x
            elif isinstance(end, int) and x == end + 1:
                end = x
            else:
                _, _, groups = append_group(start, end, groups)
                start, end = x, x
        else:
            start, end, groups = append_group(start, end, groups)
            groups.append(x)
    _, _, groups = append_group(start, end, groups)
    return groups


def append_group(start,  # type: Optional[OptionValue]
                 end,  # type: Optional[OptionValue]
                 groups  # type: List[OptionValue]
                 ):
    # type: (...) -> Tuple[Optional[OptionValue], Optional[OptionValue], List[OptionValue]]
    if start is None:
        return start, end, groups
    if start == end:
        groups.append(textrepr(start))
    elif start == end - 1:
        groups.append(textrepr(start))
        groups.append(textrepr(end))
    else:
        groups.append("%s-%s" % (start, end))
    return None, None, groups


def iter_option_style_runs(formatter,  # type: CodeFormatter
                           optstyles,  # type: List[List[Style]]
                           num_files,  # type: int
                           runs  # type: Iterable[Tuple[ExeCall, ExeResult]]
                           ):
    # type: (...) -> Iterable[List[Tuple[int, Style, int, Tuple[ExeCall, ExeResult]]]]
    num_styles = sum(1 for _ in itertools.chain.from_iterable(optstyles))
    prev_progress = ''
    styleidx = 0
    runiter = iter(runs)
    runidx = 0
    if reporting_progress():
        outline()
    for styles in optstyles:
        option_style_runs = []
        for style in styles:
            for filenr, run in zip(range(num_files), runiter):
                if formatter.valid_job_result(*run):
                    option_style_runs.append((styleidx, style, filenr, run))
                prev_progress = report_progress('Tweaking all option values, ',
                                                runidx + 1,
                                                num_styles * num_files,
                                                prev=prev_progress)
                runidx += 1
            styleidx += 1
        if option_style_runs:
            yield option_style_runs


def make_html(htmldiffer, tables, enc='utf-8', extracss='', legend=True):
    # type: (HtmlMultiDiff, str, str, str, bool) -> str
    css = htmldiffer._styles  # type: ignore
    css = extracss + css
    file_template = htmldiffer._file_template  # type: ignore
    # Make it a valid XHTML document
    file_template = file_template.replace('<html>',
                                          '<html xmlns="http://www.w3.org/1999/xhtml">')
    # Support unicode in Python 2 as well.
    file_template = file_template.replace('charset=ISO-8859-1', 'charset=%(charset)s')
    if not legend:
        file_template = file_template.replace('%(legend)s', '')
    html = file_template % dict(charset=enc,
                                styles=css,
                                legend=htmldiffer._legend,  # type: ignore
                                table=tables)
    return html


class HtmlMultiDiff(difflib.HtmlDiff):
    """This class is needed to allow multiple calls to HtmlDiff.make_table without
    resetting ids between the calls.
    """

    def __init__(self, *args, **kwargs):
        # type: (*Any, **Any) -> None
        wrapcolumn = kwargs.get('wrapcolumn')
        if wrapcolumn == 0:
            kwargs = kwargs.copy()
            kwargs['wrapcolumn'] = None
        super(HtmlMultiDiff, self).__init__(*args, **kwargs)
        self._make_prefix_called = False
        self.uniqueidx = 0

    def _make_prefix(self):
        # type: () -> None
        fromprefix = "from%d_" % self.uniqueidx
        toprefix = "to%d_" % self.uniqueidx
        # store prefixes so line format method has access
        self._prefix = [fromprefix, toprefix]
        self._make_prefix_called = True

    def table_from_pairs(self, pairs, enc, **kwargs):
        # type: (List[BytesPair], str, **Any) -> str
        numlines = kwargs.get('numlines', CONTEXTLINES)
        a, b = self.lines_from_sourcepairs(pairs, numlines=numlines, enc=enc)
        table = self.make_table(a,
                                b,
                                fromdesc=kwargs.get('fromdesc', ''),
                                todesc=kwargs.get('todesc', ''),
                                context=True,
                                numlines=numlines)
        return table

    def make_table(self, *args, **kwargs):
        # type: (*Any, **Any) -> str
        with warnings.catch_warnings():
            # PendingDeprecationWarning acknowledged.
            warnings.simplefilter("ignore")
            table = super(HtmlMultiDiff, self).make_table(*args, **kwargs)

            def unescape_zeroonetwo(m):
                return unichr(ord(m.group(1)) - ord('0'))

            table = re.sub('\x02([012])', unescape_zeroonetwo, table)
            return table

    @staticmethod
    def lines_from_sourcepairs(pairs, numlines=2, enc='utf-8'):
        # type: (List[BytesPair], int, str) -> Tuple[List[str], List[str]]
        def safeunistr(s):
            # type: (bytes) -> str
            # The bytes 0 and 1 that appear in the intermediate result of
            # difflib.HtmlDiff.make_table are replaced by opening and closing span tags.
            # If the input to make_table already contains 0 and 1 bytes we get mismatched
            # span tags.
            # We use '\x02' as escape character and encode '\x00', '\x01', '\x02' as
            # '\x02' followed by the digit 0, 1, 2 respectively.
            def escape_zeroonetwo(m):
                return b'\x02' + int2byte(ord('0') + ord(m.group(0)))

            s = re.sub(b'[\x00-\x02]', escape_zeroonetwo, s)
            return surrdecode(s, enc=enc)

        a = [a for a, _ in pairs]
        b = [b for _, b in pairs]
        a = concat_sources(a, numlines=numlines).splitlines(True)
        b = concat_sources(b, numlines=numlines).splitlines(True)
        atext = list(map(safeunistr, a))
        btext = list(map(safeunistr, b))
        return atext, btext


# yapf: disable
# ----------------------------------------------------------------------
# http://stackoverflow.com/questions/1707890/
#           fast-way-to-filter-illegal-xml-unicode-chars-in-python
_illegal_unichrs = [(0x00, 0x08), (0x0B, 0x0C), (0x0E, 0x1F),
                    (0x7F, 0x84), (0x86, 0x9F),
                    (0xFDD0, 0xFDDF), (0xFFFE, 0xFFFF)]
if sys.maxunicode >= 0x10000:  # not narrow build
    _illegal_unichrs.extend([(0x1FFFE, 0x1FFFF), (0x2FFFE, 0x2FFFF),
                             (0x3FFFE, 0x3FFFF), (0x4FFFE, 0x4FFFF),
                             (0x5FFFE, 0x5FFFF), (0x6FFFE, 0x6FFFF),
                             (0x7FFFE, 0x7FFFF), (0x8FFFE, 0x8FFFF),
                             (0x9FFFE, 0x9FFFF), (0xAFFFE, 0xAFFFF),
                             (0xBFFFE, 0xBFFFF), (0xCFFFE, 0xCFFFF),
                             (0xDFFFE, 0xDFFFF), (0xEFFFE, 0xEFFFF),
                             (0xFFFFE, 0xFFFFF), (0x10FFFE, 0x10FFFF)])
# yapf: enable

_illegal_ranges = ["%s-%s" % (unichr(low), unichr(high)) for (low, high) in _illegal_unichrs]
_illegal_xml_chars_RE = re.compile(u'[%s]' % u''.join(_illegal_ranges))


def ill_cp_escaper(m):
    # type: (Match) -> str
    codepoint = ord(m.group(0))
    if codepoint < 0x100:
        return u"\\x%02x" % codepoint
    elif codepoint < 0x10000:
        return u"\\u%04x" % codepoint
    else:
        return u"\\U%06x" % codepoint


def ill_cp_unescaper(m):
    # type: (Match[str]) -> str
    return unichr(int(m.group(1)[1:], 16))


def escape_illegal_xmlchars(text):
    # type: (str) -> str
    r"""Escape illegal XML characters by \x, \u and \U followed by the hexadecial codepoint.
    """
    # First escape \x, \u and \U itself, they will later be unescaped together
    # with the illegal XML characters in unescape_illegal_xmlchars.
    text = re.sub(r'\\([xuU])', r'\\x5c\1', text)
    result = re.sub(_illegal_xml_chars_RE, ill_cp_escaper, text)
    return result


def unescape_illegal_xmlchars(text):
    # type: (str) -> str
    return re.sub(r'\\(x[0-9a-zA-Z]{2}|u[0-9a-zA-Z]{4}|U[0-9a-zA-Z]{6})', ill_cp_unescaper,
                  text)


def translate_non_sgml_chars(data, enc='utf-8'):
    # type: (bytes, str) -> bytes

    def replace_non_sgml(m):
        # type: (Match) -> str
        codepoint = ord(m.group(0))
        if 127 <= codepoint <= 159:
            try:
                return int2byte(codepoint).decode('windows-1252')
            except UnicodeDecodeError:
                pass
        # Unicode Character 'REPLACEMENT CHARACTER'
        return u'\ufffd'

    text = data.decode(enc, 'replace')
    text = re.sub(unistr(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]'), replace_non_sgml, text)
    return text.encode(enc, 'replace')


def surrdecode(s, enc='utf-8'):
    # type: (bytes, str) -> str
    return s.decode(enc, 'replace')


def surrencode(s, enc='utf-8'):
    # type: (str, str) -> bytes
    data = s.encode(enc, 'replace')
    return data


def tree_from_html(htmldata, enc='utf-8'):
    # type: (str, str) -> ETree.Element
    text = htmldata
    text = escape_illegal_xmlchars(text)
    if NARROW_BUILD:
        # Remove lonely surrogate halfs
        text = u''.join(iterchars(text))
    text = re.sub(' xmlns="[^"]+"', '', text, count=1)
    text = text.replace('&nbsp;', ' ')
    btext = bytestr('<?xml version="1.0" encoding="%s"?>\n' % enc) + surrencode(text, enc)
    tree = ETree.fromstring(btext)
    return tree

# The LINENRSEP must not be anything that appears in the line number column of the HTML
# generated by difflib which are digits and the line continuation character '>'.
LINENRSEP = '|'
LINENRSEP_LEN = len(LINENRSEP)


def htmldiff2ansi(htmldata, enc, linenumbers=False, fp=None):
    # type: (str, str, bool, Optional[IO[Any]]) -> None
    tree = tree_from_html(htmldata, enc=enc)
    difftype_colors = {
        'diff_add': GREEN + BACKGROUNDCOLOR_OFFSET,
        'diff_chg': CYAN + BACKGROUNDCOLOR_OFFSET,
        'diff_sub': RED + BACKGROUNDCOLOR_OFFSET,
    }

    def emit(text, colorvalue=None):
        # type: (Union[str, bytes, None], Optional[int]) -> None
        if text:
            if isinstance(text, binary_type):
                s = text.decode(enc, 'replace')  # type: str
            else:
                s = text
            # Prevent remaining newlines from messing up the side by side layout.
            s = s.replace('\n', '')
            # raw is only used for counting characters.
            rawline.append(unescape_ill_surrencode(s).decode(enc, 'replace'))
            if colorvalue is not None:
                s = ansicolor(colorvalue, s)
            line.append(s)

    line = []  # type: List[str]
    rawline = []  # type: List[str]
    for table in tree.findall('body/table'):
        if table.attrib.get('summary') == 'Legends':
            continue
        headers = [], []  # type: Tuple[List[str], List[str]]
        for sideidx, th in enumerate(table.findall("thead//th[@class='diff_header']")):
            line = []
            rawline = []
            for t in th.findall(".//*"):
                emit(t.text)
                emit(t.tail)
            headers[sideidx].extend(line)  # type: ignore

        # Equalize number of left and right header rows
        headerline_diff = len(headers[0]) - len(headers[1])
        if headerline_diff < 0:
            headers[0].extend([''] * -headerline_diff)
        elif headerline_diff > 0:
            headers[1].extend([''] * headerline_diff)

        # Display the style differencs before the diff hunks.
        # Every header line gets a LINENRSEP prefix to indicate that there is no line number.
        hunklines = [[(LINENRSEP + ansicolor(YELLOW, l), LINENRSEP + l) for l in side]
                     for side in headers]
        difflines = []
        tbodies = table.findall('tbody')
        for bodyidx, tbody in enumerate(tbodies):
            for tr in tbody.findall('tr'):
                for tdidx, td in enumerate(tr.findall('td')):
                    if td.attrib.get('class') == 'diff_header':
                        line, rawline = [], []
                        lnrcolumn = unistr(td.text or '')  # type: ignore
                        # Always display the line continuation character but the
                        # linenumber only if requested.
                        if lnrcolumn and (linenumbers or not re_number.match(lnrcolumn)):
                            emit(lnrcolumn)
                        # The LINENRSEP marks the end of the line number in the plain text.
                        emit(LINENRSEP)
                    if td.attrib.get('nowrap') == 'nowrap':
                        sideidx = 0 if tdidx < 3 else 1
                        emit(td.text)
                        for t in td.findall('span'):
                            cls = unistr(t.attrib.get('class'))
                            emit(t.text, difftype_colors.get(cls))
                            emit(t.tail)
                        hunklines[sideidx].append((''.join(line), ''.join(rawline)))
            difflines.append(hunklines)
            hunklines = [[], []]
        emit_hunks(difflines, enc, fp)
        outline(end=os.linesep, fp=fp)


def unescape_ill_surrencode(text, enc='utf-8'):
    # type: (str, str) -> bytes
    return surrencode(unescape_illegal_xmlchars(text), enc=enc)


def soutline(s='', enc='utf-8', fp=None):
    # type: (str, str, Optional[IO[Any]]) -> None
    data = unescape_ill_surrencode(s, enc=enc)
    write(data + b'\n', fp=fp)


def emit_hunks(all_difflines, enc='utf-8', fp=None):
    # type: (List[List[List[TextPair]]], str, Optional[IO[Any]]) -> None
    """Writes the diff lines to fp.
    all_difflines is a list of hunks.
    Each hunk is a pair (left, right) of lists of linepairs (ansicoloredline, rawline).
    """

    def lineheaderlen(text):
        # type: (str) -> int
        return text.find(LINENRSEP)

    def difflinelen(text):
        # type: (str) -> int
        lhlen = lineheaderlen(text)
        if lhlen >= 0:
            return unilen(text) - lhlen - LINENRSEP_LEN
        # Every line should contain a LINENRSEP character after the optional line number.
        # In case there isn't one, the normal length is used.
        return unilen(text)

    len_l = 0
    len_r = 0
    lhlen_l = 0
    lhlen_r = 0
    centerpos = 10
    for difflines in all_difflines:
        fromlines, tolines = difflines
        lhlen_l = max([lhlen_l] + [lineheaderlen(rawline) for line, rawline in fromlines])
        lhlen_r = max([lhlen_r] + [lineheaderlen(rawline) for line, rawline in tolines])
        len_l = max([len_l] + [difflinelen(rawline) for line, rawline in fromlines])
        len_r = max([len_r] + [difflinelen(rawline) for line, rawline in tolines])

    lhl_fmt = ' %%%ds ' % lhlen_l
    lhr_fmt = ' %%%ds ' % lhlen_r
    maxlen = max(centerpos, len_l)
    width = maxlen + len_r
    sepcolor = BACKGROUNDCOLOR_OFFSET + BLUE
    sep1 = ansicolor(sepcolor, ' ')
    sep_l = ansicolor(sepcolor, lhl_fmt % '')
    sep_r = ansicolor(sepcolor, lhr_fmt % '')
    diffseparator = ansicolor(sepcolor, ' ' * (width + 1) + lhl_fmt % '' + lhr_fmt % '')
    mgcol = BACKGROUNDCOLOR_OFFSET + MAGENTA
    hunkseparator = (
        sep_l + ansicolor(mgcol, ' ' * maxlen) + sep_r + ansicolor(mgcol, ' ' * len_r) + sep1)
    for hunkidx, difflines in enumerate(all_difflines):
        if hunkidx == 0:
            soutline(diffseparator, enc=enc, fp=fp)
        elif hunkidx >= 1:
            soutline(hunkseparator, enc=enc, fp=fp)
        for idx, ((f, f_raw), (t, t_raw)) in enumerate(izip(*difflines)):
            linelen = difflinelen(f_raw)
            padding_length = maxlen - linelen
            padding = ' ' * max(0, padding_length)
            rpad = ' ' * (width - maxlen - difflinelen(t_raw))
            lnrsep_pos = lineheaderlen(f)
            if lnrsep_pos >= 0:
                lnr_l = ansicolor(sepcolor, lhl_fmt % f[:lnrsep_pos])
                f = f[lnrsep_pos + LINENRSEP_LEN:]
            else:
                lnr_l = sep_l
            lnrsep_pos = lineheaderlen(t)
            if lnrsep_pos >= 0:
                lnr_r = ansicolor(sepcolor, lhr_fmt % t[:lnrsep_pos])
                t = t[lnrsep_pos + LINENRSEP_LEN:]
            else:
                lnr_r = sep_r
            soutline('%s%s%s%s%s%s%s' % (lnr_l, f, padding, lnr_r, t, rpad, sep1),
                     enc=enc,
                     fp=fp)
        if hunkidx == len(all_difflines) - 1:
            soutline(diffseparator, enc=enc, fp=fp)

# ----------------------------------------------------------------------


def find_style(params,        # type: ParameterSet
               filenames,     # type: List[str]
               language=None  # type: Optional[str]
               ):
    # type: (...) -> Union[StyleDist, Tuple[StyleDist, StyleDist]]
    formatter = params.formatter
    formatter.identify_language(filenames, language=language)
    try:
        return find_style_for_mode(params, filenames)
    finally:
        formatter.remove_tempfiles()


def concat_files(filenames, mode, references):
    # type: (Sequence[str], str, bool) -> List[str]
    if references:
        if mode == MODE_RESILIENT:
            # Transform the files n1, r1, n2, r2, min1, rmin1, min2, rmin2, max1, rmax1,
            #                         max2, rmax2
            # into n1+n2, r1+r2, min1+min2+max1+max2, rmin1+rmin2+rmax1+rmax2
            numinputfiles = int(len(filenames) / 3)
            normalfiles = concat_files(filenames[:numinputfiles], MODE_NORMAL, references)
            variantfiles = concat_files(filenames[numinputfiles:], MODE_NORMAL, references)
            return normalfiles + variantfiles
        else:
            # Transform the files normal1, ref1, normal2, ref2
            # into normal1+normal2, ref1+ref2
            inputs = [filenames[::2], filenames[1::2]]
    else:
        # Transform the files [normal1, normal2, normal3] into [normal1+normal2+normal3]
        inputs = [filenames]
    concatted_files = []
    for inputfiles in inputs:
        content = [get_cached_file(f) for f in inputfiles]
        lineterm = lineterminator(content[0])
        concatted = lineterm.join(content)
        tmpfile = shatempfile(inputfiles[0], concatted)
        concatted_files.append(tmpfile)
    return concatted_files


def lineterminator(content):
    # type: (bytes) -> bytes
    m = re.search(br'(\r\n)|\n|\r', content)
    if not m:
        return bytestr(os.linesep)
    return m.group(0)


def shatempfile(filename, content):
    # type: (str, bytes) -> str
    """Writes content to a temporary file whose name contains the basename of filename
    and a sha of content.
    """
    sha = shahex(content)
    base = os.path.basename(filename)
    tmpfile = os.path.join(tempfile.gettempdir(), 'whatstyle_%s_%s' % (sha, base))
    writebinary(tmpfile, content)
    return tmpfile


def create_variant_files(params, filenames, metric):
    # type: (ParameterSet, List[str], int) -> Tuple[List[str], Optional[Style]]
    """Finds the best style for the given parameters, reformats the input
    files in this style, writes the results to temporary files and returns the
    list of these temporary filenames and the style that was chosen.
    """
    style, bestdist = find_best_style(params, filenames, metric, additive=True)
    fromfiles, tofiles = split_reffiles(params.references, filenames)
    varfiles = []
    for filename in fromfiles:
        sourcedata = get_cached_file(filename)
        mincontent = params.formatter.formatcode(style, sourcedata, filename=filename)
        tmpfile = shatempfile(filename, mincontent)
        varfiles.append(tmpfile)
    return varfiles, style


def minimize_resilient(params, filenames):
    # type: (ParameterSet, List[str]) -> StyleDist
    """Makes the files as small as possible and as large as possible, then finds the style
    that best reproduces the files from the normal reformatted version firstly and from their
    extreme versions secondly. This finds a style that contains more options and is more
    robust when reformatting very out of shape versions of the files.
    """
    minfiles, _ = create_variant_files(
        params._replace(mode=MODE_NORMAL),  # type: ignore
        filenames, METRIC_MIN)
    maxfiles, _ = create_variant_files(
        params._replace(mode=MODE_NORMAL),  # type: ignore
        filenames, METRIC_MAX)
    for tmpfile in minfiles + maxfiles:
        params.formatter.add_tempfile(tmpfile, mode=GLOBALTMP)

    fromfiles, tofiles = split_reffiles(params.references, filenames)
    files = []
    for filefrom, fileto in (list(zip(fromfiles, tofiles)) + list(zip(minfiles, tofiles)) +
                             list(zip(maxfiles, tofiles))):
        files.append(filefrom)
        files.append(fileto)
    beststyle, bestdist = find_best_style(
        params._replace(references=True),  # type: ignore
        files, METRIC_MINDIFF,
        additive=True)
    return beststyle, bestdist


def find_style_for_mode(params,  # type: ParameterSet
                        filenames  # type: List[str]
                        ):
    # type: (...) -> Union[StyleDist, Tuple[StyleDist, StyleDist]]
    mode = params.mode
    if mode == MODE_RESILIENT:
        return minimize_resilient(params, filenames)

    simplemodes = [MODE_NORMAL, MODE_MAXDIFF, MODE_MINIMIZE, MODE_MAXIMIZE]

    if mode == MODE_MINIMIZE:
        metric = METRIC_MIN
    elif mode == MODE_MAXIMIZE:
        metric = METRIC_MAX
    elif mode == MODE_MAXDIFF:
        metric = METRIC_MAXDIFF
    else:
        metric = METRIC_MINDIFF

    fromfiles, tofiles = split_reffiles(params.references, filenames)
    beststyle, bestdist = find_best_style(
        params._replace(references=False),
        fromfiles, metric, additive=True)
    if mode in simplemodes or beststyle is None:
        return beststyle, bestdist

    formatter = params.formatter
    old_initial_style = formatter.initial_style
    formatter.initial_style = beststyle
    beststyle2, bestdist2 = find_best_style(
        params._replace(mode=MODE_NORMAL),
        filenames, metric,
        additive=False)
    formatter.initial_style = old_initial_style
    return (beststyle, bestdist), (beststyle2, bestdist2)


def format_with_styles(formatter,           # type: CodeFormatter
                       styles,              # type: List[Style]
                       filenames,           # type: List[str]
                       reporterrors=True,   # type: bool
                       cache=None,          # type: Optional[Cache]
                       ccmode=CC_PROCESSES  # type: str
                       ):
    # type: (...) -> Iterator[Tuple[ExeCall, ExeResult]]
    """Reformat all files with all styles and yield pairs
    (job, jobresult) of all reformat operations.
    """
    jobs = []
    sourcecodes = []
    for style, filename in itertools.product(styles, filenames):
        cmdargs = formatter.cmdargs_for_style(style, filename)
        sourcedata = get_cached_file(filename)
        jobs.append(make_execall(formatter.exe, cmdargs, sourcedata, depfiles=[filename]))
        sourcecodes.append(sourcedata)
    jobresults = run_executables(jobs, cache, ccmode=ccmode)
    for srcdata, job, jobres in izip(sourcecodes, jobs, jobresults):
        if reporterrors:
            formatter.reporterrors(job, jobres)
        # A formatter reporting a valid result for non-empty input while returning empty
        # output indicates that the effective result is the unchanged input.
        if not jobres.stdout and srcdata and formatter.valid_job_result(job, jobres):
            jobres = jobres._replace(stdout=srcdata)
        yield job, jobres


def find_best_style(params, filenames, metric, additive=True):
    # type: (ParameterSet, List[str], int, bool) -> StyleDist
    """This function does the actual optimization loop to find the most appropriate style.
    """
    formatter = params.formatter
    difftool = params.difftool
    mode = params.mode
    references = params.references
    maxrounds = params.maxrounds
    ccmode = params.ccmode

    cache = formatter.cache

    if params.concat:
        filenames = concat_files(filenames, mode, references)
        for f in filenames:
            formatter.add_tempfile(f, mode=GLOBALTMP)

    filenames, reffiles = split_reffiles(references, filenames)
    if MODE_RESILIENT:
        if params.concat:
            numinputfiles = 1
        else:
            numinputfiles = int(len(filenames) / 3)

    if ccmode != CC_OFF and not formatter.can_process_in_parallel(filenames):
        ccmode = CC_OFF

    distfunc, metricdesc = distfunc_for_metric(metric)
    bestdist = None

    allcombos = list(iter_stylecombos(formatter, params.ignoreopts))

    evaluations = []  # type: List[AttemptResult]

    initialstyle = formatter.initial_style
    attemptnr = 0
    heapq.heappush(evaluations, AttemptResult(
        (HUGE_DISTANCE, HUGE_DISTANCE, HUGE_DISTANCE, attemptnr), initialstyle))
    attemptnr += 1

    # We count how often a certain option makes things worse.
    global_worse_options = Counter()  # type: Counter[str]
    invalid_cmdline_options = set()  # type: Set[str]
    finished_styles = []  # type: List[AttemptResult]
    roundnr = -1
    numfiles = len(filenames)
    previous_attempts = set()  # type: Set[str]
    success = True
    num_reformats = 0
    num_failed_reformats = 0
    while True:
        prev_progress = ''
        roundnr += 1
        parentresult = evaluations[0]
        derivations = gather_attempts(allcombos,
                                      formatter,
                                      invalid_cmdline_options,
                                      global_worse_options,
                                      parentresult,
                                      roundnr,
                                      additive=additive)
        attempts = []
        for attempt in derivations:
            attempt_sig = normrepr(attempt.formatstyle)
            if attempt_sig in previous_attempts:
                # Don't evaluate the same style twice.
                continue
            previous_attempts.add(attempt_sig)
            attempts.append(attempt)

        num_runs = len(attempts) * len(filenames)
        styles = [a.formatstyle for a in attempts]
        fwsiter = format_with_styles(formatter,
                                     styles,
                                     filenames,
                                     reporterrors=True,
                                     cache=cache,
                                     ccmode=ccmode)
        runs = []
        for idx, run in enumerate(fwsiter):
            runs.append(run)
            prev_progress = report_progress('Round %d  ' % roundnr,
                                            idx + 1,
                                            num_runs,
                                            prev=prev_progress)
        vattempts = []
        diffargs = []
        for attempt, attemptruns in izip(attempts, grouper(numfiles, runs)):
            valid_attempt = True
            for idx, (job, jobresult) in enumerate(attemptruns):
                if formatter.invalid_cmdline_option(job, jobresult):
                    # Remember invalid command line options
                    invalid_cmdline_options.add(normrepr(attempt.newoptions))
                    # attemptvalid = False
                    num_failed_reformats += 1
                valid_jobres = formatter.valid_job_result(job, jobresult)
                if not valid_jobres and INFO_INVALIDS in args_info:
                    iprint(INFO_INVALIDS,
                           red('Invalid format attempt of '
                               '"%s" with style:' % filenames[idx]),
                           prefix='\n')
                    iprint(INFO_INVALIDS, red(formatter.inlinestyletext(attempt.formatstyle)))
                valid_attempt = valid_attempt and valid_jobres
            if not valid_attempt:
                continue
            for reffile, (job, jobresult) in izip(reffiles, attemptruns):
                diffargs.append((reffile, jobresult.stdout))
            vattempts.append(attempt)

        attempts = vattempts
        reformat_count = len(runs)
        num_reformats += reformat_count

        if roundnr == 0 and not attempts:
            success = False
            break

        # Evaluate the metric function,e.g. running diff and counting diff lines.
        alldiffs = []
        for idx, diff in enumerate(distfunc(difftool, diffargs, cache=cache, ccmode=ccmode)):
            alldiffs.append(diff)
            prev_progress = report_progress('Round %d  ' % roundnr,
                                            num_runs,
                                            num_runs,
                                            idx + 1,
                                            len(diffargs),
                                            prev=prev_progress)

        for attempt, diff in izip(attempts, grouper(numfiles, alldiffs)):
            if mode == MODE_RESILIENT and numfiles > 1:
                # The distances in MODE_RESILIENT are a flat list of the files
                # [normal1, normal2, min1, min2, max1, max2]
                distance = tuplesums(diff[:numinputfiles])
                variantsdist = tuplesums(diff[numinputfiles:numfiles])
                if params.sourcefactor is not None and params.sourcefactor is not None:
                    # The distance is the componentwise sum of the weighted distances of the
                    # sourcefiles and the minimized and maximized versions.
                    distance = tuple([s * params.sourcefactor + v * params.variantsfactor
                                      for s, v in zip(distance, variantsdist)])
                    variantdist = (0, ) * len(distance)
            else:
                distance = tuplesums(diff[:numfiles])
                variantsdist = (0, ) * len(distance)

            distance += variantsdist
            complexity, penalty = formatter.extra_penalty(
                attempt.formatstyle, formatter.complexity(attempt.formatstyle))
            newdistance = distance + (complexity, penalty, attemptnr)
            # The attemptnr as least important distance criterium gives
            # us a stable result order where earlier attempts are preferred.
            attemptnr += 1
            if INFO_ATTEMPT in args_info:
                distmsg = green('\nRound %d, distance: %s' % (roundnr, newdistance))
                inlsty = yellow(formatter.inlinestyletext(attempt.formatstyle))
                iprint(INFO_ATTEMPT, '%s %s' % (distmsg, inlsty))
            if not formatter.attempt_acceptible(roundnr, distquality(attempt.prevdist),
                                                distquality(newdistance)):
                global_worse_options[normrepr(attempt.newoptions)] += 1

            heapq.heappush(evaluations, AttemptResult(newdistance, attempt.formatstyle))

        formatter.remove_tempfiles(mode=LOCALTMP)
        done, nested_round, bestdist = update_evaluations(formatter, evaluations,
                                                          finished_styles, bestdist)
        if done:
            break
        if 0 <= maxrounds <= roundnr:
            iprint(INFO_USER, cyan('\nWe have reached the limit of %d rounds.' % maxrounds))
            break
        report_best_styles(formatter, finished_styles, evaluations, params.bestofround,
                           metricdesc, roundnr)
        if nested_round:
            global_worse_options.clear()
            iprint(INFO_USER, cyan('The style could not be further improved without '
                                   'using nested options which we will try now.'))
        sys.stdout.flush()
        sys.stderr.flush()

    if not success:
        return None, None
    attemptresult = heapq.heappop(finished_styles or evaluations)
    beststyle = attemptresult.formatstyle
    bestdist = attemptresult.distance
    iprint(INFO_USER, "\nThe formatter was run %d times of which %d runs "
           "were unusable.\n" % (num_reformats, num_failed_reformats))
    return beststyle, bestdist


def report_best_styles(formatter, finished_styles, evaluations, bestofround, metricdesc,
                       roundnr):
    # type: (CodeFormatter, List[AttemptResult], List[AttemptResult], int, str, int) -> None
    """Report the best style and its metric for the round.
    Also report the next best styles with their metrics relative to the best style.
    """
    attempts = finished_styles[:]
    bestofround = max(0, bestofround)
    for attempt in heapq.nsmallest(bestofround, evaluations):
        heapq.heappush(attempts, attempt)
    for idx, attemptresult in enumerate(heapq.nsmallest(bestofround, attempts)):
        if idx == 0:
            bestresult = attemptresult

            bestmsg = '\nBest distance %s round %d: %s' % (metricdesc, roundnr,
                                                           attemptresult.distance)
            iprint(INFO_USER, cyan(bestmsg))
            iprint(INFO_USER, formatter.styletext(attemptresult.formatstyle))
        else:
            place = '%d. ' % (idx + 1)
            m_diff = distdifference(attemptresult.distance, bestresult.distance)
            iprint(INFO_USER, yellow('\n%sbest differential distance %s round %d: %s' %
                                     (place, metricdesc, roundnr, m_diff)))
            unique_from, unique_to = deep_difference(bestresult.formatstyle,
                                                     attemptresult.formatstyle)
            text_from = formatter.styletext(style_make(unique_from))
            text_to = formatter.styletext(style_make(unique_to))
            separator = '  |  '
            block = alignedblocks(text_from, text_to, separator, color_right=YELLOW)
            iprint(INFO_USER, block)


def update_evaluations(formatter,  # type: CodeFormatter
                       evaluations,  # type: List[AttemptResult]
                       finished_styles,  # type: List[AttemptResult]
                       bestdist  # type: Sequence[int]
                       ):
    # type: (...) -> Tuple[bool, bool, Sequence[int]]
    attemptresult = heapq.heappop(evaluations)
    nested_round = False
    if bestdist is None or (distquality(attemptresult.distance) < distquality(bestdist)):
        bestdist = attemptresult.distance
        heapq.heappush(evaluations, attemptresult)
    else:
        # We found a style that could no longer be improved by adding a single option value.
        heapq.heappush(finished_styles, attemptresult)
        nested_styles = formatter.nested_derivations(attemptresult.formatstyle)
        if not nested_styles:
            # This formatstyle does not unlock more options.
            return True, nested_round, bestdist
        # Restart the optimization from scratch with the attemptresult augmented with
        # every nested option as seed styles.
        bestdist = None
        ndist = (HUGE_DISTANCE, HUGE_DISTANCE, HUGE_DISTANCE, HUGE_DISTANCE)
        evaluations[:] = [AttemptResult(ndist, s) for s in nested_styles]
        nested_round = True
    return False, nested_round, bestdist


def gather_attempts(allcombos,                # type: List[FormatOption]
                    formatter,                # type: CodeFormatter
                    invalid_cmdline_options,  # type: Set[str]
                    global_worse_options,     # type: Counter
                    parentresult,             # type: AttemptResult
                    roundnr,                  # type: int
                    additive=True             # type: bool
                    ):
    # type: (...) -> List[FormatStyleAttempt]
    """Returns the next attempts to improve the parent result.
    """
    if roundnr == 0:
        # First we only want to measure the initial style
        # without derivations.
        assert isinstance(parentresult.formatstyle, Style)
        return [FormatStyleAttempt(parentresult.formatstyle, style_make(),
                                   parentresult.distance)]
    attempts = []
    parentstyle = parentresult.formatstyle
    epstyle = formatter.effective_style(parentstyle)
    for fopt in allcombos:
        for optiongroup in fopt.opts:
            norm_optgroup = normrepr(optiongroup)
            if norm_optgroup in invalid_cmdline_options:
                # This option caused trouble in an earlier round.
                continue
            if global_worse_options[norm_optgroup] > 0:
                continue
            has_major_style = formatter.contains_major_style(optiongroup)
            if additive and roundnr == 1:
                # Ding! Ding! Round one!
                # Only major styles are allowed here
                if (formatter.prefer_basestyle and (not parentresult.formatstyle) and
                    (not has_major_style)):
                    # In round one we add only major styles to an empty
                    # parent style.
                    continue
            if not additive and has_major_style:
                # We use non-additive (changing existing options) in stylediff mode to find
                # the difference of two styles expressed in individual options.
                # So we prevent a major style change here.
                continue
            if additive and contains_all_options(optiongroup, parentstyle):
                # The parentstyle already contains this option
                continue

            # Evaluate each style that has no parent style
            if parentresult.distance == HUGE_DISTANCE:
                styles_equal = False
            else:
                # Adding an option with a default value does not change the
                # effective style. contains_all_options may return false
                # negatives whose consequences are only a few redundant
                # computations.
                styles_equal = contains_all_options(optiongroup, epstyle, matchvalues=True)
            if not styles_equal:
                fsa = FormatStyleAttempt(
                    copy_with_optgroup(parentstyle, optiongroup), optiongroup,
                    parentresult.distance)
                attempts.append(fsa)
            else:
                fst = formatter.inlinestyletext
                iprint(INFO_SKIP,
                       cyan('Skip adding %s to %s because the style would not change.' %
                            (fst(optiongroup), fst(parentstyle))))
    return attempts

# ----------------------------------------------------------------------


def parse_style_history():
    # type: () -> List[Tuple[str, StyleDef]]
    """Returns a list of pairs containing
       a number of ordered dicts with the styles for one version, e.g.:
      [('3.4',
       StyleDef([('BasedOnStyle', ('string',
                                     ('LLVM', 'Google', 'Chromium')))])),
      ('3.5',
       StyleDef([('BasedOnStyle', ('string',
                   ('LLVM', 'Google', 'Chromium', 'Mozilla', 'WebKit')))]))]
    """
    styledef = styledef_make()
    versionline = '# Clang '

    version = ''
    configs = []
    haveoptions = False
    versions = []  # type: List[Tuple[str, StyleDef]]

    lines = [l.strip() for l in CLANG_FORMAT_EVOLUTION.splitlines()]
    while True:
        # Do we have a version boundary?
        if not lines or lines[0].startswith(versionline):
            # Append a copy of the current style dict
            if haveoptions:
                versions.append((version, styledef_copy(styledef)))
                haveoptions = False
        if not lines:
            break
        line = lines.pop(0)
        if line.startswith(versionline):
            version = line[len(versionline):].strip()
        elif line[0] in '+-':
            action = line[0]
            typepos = line.find(' ', 2)
            optionname = line[1:typepos].strip()
            optiontype = line[typepos:].strip()
            # Collect the configurations if this is an enum type
            nestedstyle = styledef_make()
            while lines and lines[0][0] not in '#+-':
                enumvalue = lines.pop(0)
                if len(enumvalue.split(' ')) == 1:
                    configs.append(enumvalue)
                else:
                    # We have a nested subtype like option BraceWrapping
                    # with an enumvalue 'bool AfterControlStatement'
                    nopt_type, nopt_name = enumvalue.split(' ')
                    nopt = option_make(nopt_name, nopt_type, [])
                    styledef_add_option(nopt, nestedstyle)
            haveoptions = True
            if not styledef_options(nestedstyle):
                nestedstyle = None  # type: ignore
            if action == '+':
                option = option_make(optionname, optiontype, configs, nestedstyle)
                styledef_add_option(option, styledef)
            elif action == '-':
                styledef_delete_option(optionname, styledef)
            configs = []
    return versions


def parse_clang_dump_config(text):
    # type: (str) -> OrderedDict
    return parse_miniyaml(text)

# ----------------------------------------------------------------------
# Functions to parse just enough YAML that clang-format uses.

INDENT = '>>INDENT'
DEDENT = '<<DEDENT'
LISTELEM = '$$LISTELEM'
MAPPING = '$$MAPPING'

re_spc = re.compile(r'^(\s*)(.*)')
re_mapping = re.compile(r'^([a-zA-Z0-9]+):\s*(.*)')


def parse_miniyaml(text):
    # type: (str) -> Any
    text = unistr(text)
    indents = [0]
    lines = []

    def emit(s):
        # type: (str) -> None
        lines.append(s)

    for idx, line in enumerate(text.splitlines()):
        line = line.rstrip()
        if line.startswith('# BasedOnStyle'):
            line = line[2:]
        m = re_spc.match(line)
        spc, rest = m.groups()
        rest = rest.strip()
        spclen = len(spc)
        if spclen > indents[-1]:
            emit(INDENT)
            indents.append(spclen)
        elif spclen < indents[-1]:
            while spclen < indents[-1]:
                emit(DEDENT)
                indents.pop()
            if spclen != indents[-1]:
                raise Exception('Illegal indentation in line %d' % (idx + 1))
        if rest[:2] == '- ':
            emit(LISTELEM)
            emit(INDENT)
            indents.append(spclen + 2)
            rest = rest[2:]
        if rest.startswith('#'):
            continue
        m = re_mapping.match(rest)
        if m:
            key, rest = m.groups()
            emit(MAPPING)
            emit(key)
        if rest in ['---', '...']:
            continue
        if rest:
            if rest[0] == '[' and rest[-1] == ']':
                # flow style sequence
                for e in rest[1:-1].split(','):
                    emit(LISTELEM)
                    emit(INDENT)
                    emit(e.strip())
                    emit(DEDENT)
            else:
                emit(rest)
    for i in range(len(indents)-1):
        emit(DEDENT)
    value, tokens = parse_obj(lines)
    return value


def parse_obj(tokens):
    # type: (List[str]) -> Tuple[Any, List[str]]
    if not tokens:
        return None, tokens
    tok = tokens[0]
    if tok == INDENT:
        value, tokens2 = parse_obj(tokens[1:])
        if tokens2[0] != DEDENT:
            return None, tokens
        return value, tokens2[1:]
    if len(tok) >= 2 and tok[0] in '\"\'':
        # Parse a string
        c = tok[0]
        if tok[-1] == c:
            text = tok[1:-1]
            data = bytestr(text)
            s = codecs.decode(data, 'unicode_escape')
            return s, tokens[1:]
        else:
            raise Exception('Illegal string: %s' % tok)

    value = typeconv(tok)
    if not isinstance(value, string_types):
        # successful conversion
        return value, tokens[1:]

    melems = OrderedDict()  # type: OrderedDict[str, Any]
    mtokens = tokens
    while mtokens and mtokens[0] == MAPPING:
        key = mtokens[1]
        value2, mtokens2 = parse_obj(mtokens[2:])
        if value2 is not None:
            melems[key] = value2
            mtokens = mtokens2
        if not mtokens or mtokens[0] == DEDENT:
            return melems, mtokens

    seqelems = []  # type: List[Any]
    seqtokens = tokens
    while seqtokens and seqtokens[0] == LISTELEM:
        if seqtokens[1] != INDENT:
            return None, tokens
        value3, seqtokens2 = parse_obj(seqtokens[2:])
        if value3 is not None:
            seqelems.append(value3)
            seqtokens = seqtokens2
        if not seqtokens or seqtokens[0] != DEDENT:
            return None, tokens
        seqtokens = seqtokens[1:]
    if seqelems:
        return seqelems, seqtokens
    # scalar value
    return tokens[0], tokens[1:]

# ----------------------------------------------------------------------


def find_closest_clang_version(dump_config_text):
    # type: (str) -> Tuple[str, StyleDef]
    """Given the output of "clang-format -dump-config"
    this function tries to identify the closest clang-format version
    from the clang-format style option evolution.

    It does not register changes of enum values but still it should be
    good enough to support a range of clang-format versions.
    """
    dump_options = parse_clang_dump_config(dump_config_text)
    dumpstylenames = set(dump_options.keys())

    styleversions = parse_style_history()
    version = ''

    # The best maching versions might contain option values that the installed clang
    # does not support. Setting prefer_older_version to False would prefer the most recent
    # clang version that matches dump_config_text instead of the oldest which
    # is the conservative choice.
    prefer_older_version = True
    variants = []
    for idx, (version, styledef) in enumerate(styleversions):
        options = styledef_options(styledef)
        stylenames = set([option_name(option) for option in options])
        intersection = dumpstylenames.intersection(stylenames)
        num_known_styles = len(intersection)
        num_unknown_styles = len(stylenames - dumpstylenames)
        # The candidate versions are sorted by the number of common style names first
        # and by the negative number of unknown styles as secondary sort criteria.
        # This way we get the best possible match. The unique idx values are used to terminate
        # the comparison so that the unorderable styledef objects are never compared.
        if prefer_older_version:
            idx = -idx
        variants.append(((num_known_styles, -num_unknown_styles, idx), styledef))

    svariants = list(sorted(variants))
    # Pick one of the best matching styledef revisions.
    (known, unknown, idx), styledef = svariants[-1]
    # Record the best matching styledefs in order from oldest to newest.
    best_variants = [stydef for (k, u, _), stydef in variants if (k, u) == (known, unknown)]
    # Take the oldest best known style definition
    styledef = best_variants[0]
    # and add the options of all equally matching style definitions to it.
    for otherstyledef in best_variants[1:]:
        for option in styledef_options(otherstyledef):
            styledef_add_option(option, styledef)
    styledef = filter_valid_options(dump_options, styledef)
    return version, styledef


def filter_valid_options(dump_options, styledef):
    # type: (OrderedDict, StyleDef) -> StyleDef
    """Return a style definition with only those styles that are reported in dump_options.

    There still are possibilities that we try a configuration that is invalid
    for this version of clang-format. For example if a style with an enum type
    gets another member or changes its type.
    """
    dumpdict = dump_options
    valid_styles = []  # type: List[Option]
    for optionname, dumpvalue in dumpdict.items():
        knownoption = styledef_option(styledef, optionname)  # type: Optional[Option]
        if knownoption is None:
            reportwarning("Warning: We don't know about the option '%s' that your"
                          " clang-format supports." % optionname)
            if dumpvalue in [True, False]:
                reportwarning('... but it looks like a bool type and we might'
                              ' as well tweak it.')
                valid_styles.append(option_make(optionname, 'bool', []))
        else:
            valid_styles.append(knownoption)

    return styledef_make(valid_styles)

# ---------------------------------------------------------------------
# Functions for creating diffs and calculating metrics.


def tuplesums(tuplelist):
    # type: (List[Tuple[int, int]]) -> Tuple[int, ...]
    """[(a, b), (c, d), (e, f)] -> [sum(a, c, e), sum(b, d, f)]
    """
    return tuple(sum(x) for x in zip(*tuplelist))


def distfunc_for_metric(metric):
    # type: (int) -> Tuple[Callable, str]
    if metric == METRIC_MIN:
        return distances_mincontent, 'minimizing source code'
    elif metric == METRIC_MAX:
        return distances_maxcontent, 'maximizing source code'
    elif metric == METRIC_MAXDIFF:
        return negative_distances_from_diffs, 'maximizing differences'
    else:
        # METRIC_MINDIFF: This is the important one.
        return distances_from_diffs_avglen, 'minimizing differences'


re_hunk = re.compile(br'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', re.MULTILINE)


def metric_for_mindiff(diffoutput, numlines=None):
    # type: (bytes, Optional[int]) -> int
    """Counts the number of additions and deletions in a typical diff output, e.g.:
    --- tests/examples/gumbo-parser/utf8.c	2016-02-01 19:46:47.000000000 +0100
    +++ -	2016-02-21 19:05:40.000000000 +0100
    @@ -102 +102,2 @@
    -static void add_error(Utf8Iterator* iter, GumboErrorType type) {
    +static void
    +add_error(Utf8Iterator* iter, GumboErrorType type) {

    Please note that via per_file_overhead we count the '---' and '+++' at the top as well
    on purpose. This way a two line change in a single file (distance 6) has a lower distance
    than two single line changes in two different files that have a combined distance of 8.

    When the number of added and deleted lines in a hunk differ e.g. four removed lines but
    only one added line, this is probably not great regarding minimal differences so we add
    the absolute difference of additions and deletions, e.g. abs(-4 + 1) = 3 on top
    of the normal hunk result.

    For files that are very different there are pathological cases that for example a file
    with only a different line ending has a worse metric than simply comparing that file to an
    empty file. We add the absolute line number difference to these cases to prefer seemingly
    more reasonable diffs.
    This metric does not fulfill the triangle inequality which we do not rely on so that
    should be ok.
    """
    if not diffoutput:
        return 0
    additions = 0
    deletions = 0
    hunkdiffs = 0
    maxhunkdiff = 0
    for m in re_hunk.finditer(diffoutput):
        _, dels, _, adds = m.groups()
        if dels is None:
            numdels = 1
        else:
            numdels = int(dels)
        if adds is None:
            numadds = 1
        else:
            numadds = int(adds)
        additions += numadds
        deletions += numdels
        hunkdiff = abs(numadds - numdels)
        maxhunkdiff = max(hunkdiff, maxhunkdiff)
        hunkdiffs += hunkdiff

    per_file_overhead = 2
    num_changes = per_file_overhead + additions + deletions + hunkdiffs
    if numlines is not None and num_changes >= numlines:
        numlines_to = numlines + additions - deletions
        num_changes += abs(numlines - numlines_to)
    return num_changes


def negate_tuplelist(tl):
    # type: (Iterable[Sequence[int]]) -> Iterable[Sequence[int]]
    for m in tl:
        yield tuple(-x for x in m)


def negative_distances_from_diffs(difftool, diffargs, cache=None, ccmode=CC_PROCESSES):
    return negate_tuplelist(distances_from_diffs_avglen(
        difftool, diffargs, cache=cache, ccmode=ccmode))


def distances_from_diffs_avglen(difftool,            # type: Tuple[str, str, List[str]]
                                diffargs,            # type: List[Tuple[str, bytes]]
                                cache=None,          # type: Optional[Cache]
                                ccmode=CC_PROCESSES  # type: str
                                ):
    # type: (...) -> Iterator[Tuple[Sequence[int], Iterable[int]]]
    """Returns pairs of (m, l) where m is the diff metric and l is the average line length
    difference. This improves the metric just a tiny bit.
    """
    metrics = distances_from_diffs(difftool, diffargs, cache=cache, ccmode=ccmode)
    lldiffs = avg_linelength_diffs(diffargs)
    return izip(metrics, lldiffs)


def distances_from_diffs(difftool,            # type: Tuple[str, str, List[str]]
                         diffargs,            # type: List[Tuple[str, bytes]]
                         cache=None,          # type: Optional[Cache]
                         ccmode=CC_PROCESSES  # type: str
                         ):
    # type: (...) -> Iterable[int]
    """Runs diff on a number of (filename1, content2) pairs and yields distances.

    The value of distance correlates with the number of changes
    between the two files with 0 indicating identical file content.

    difftool is a tuple of (description, program name, program args), e.g.:
    ('git diff', 'git', ['diff', '--no-index', '--unified=0', '--',
                         '#FILENAME#', '-'])
    """
    diffjobs = []
    numlines = []

    if difftool[0] == 'difflib':
        # whatstyle can call itself with --stdindiff and act as a diff tool but on
        # Python interpreters with a slow startup this has a very bad performance even if the
        # diffs are run in parallel. So we just call unified_diff directly instead.
        def unidiff(filename1, content2):
            # type: (str, bytes) -> bytes
            exit_code, lines = unified_diff(filename1, content2)
            return bytestr('\n'.join(lines))

        def diffs_via_functioncall():
            # type: () -> Iterator[ExeResult]
            for filename1, content2 in diffargs:
                numlines.append(get_num_lines(filename1))
                diffdata = Cache.apply(unidiff, (filename1, content2),
                                       exe=sys.executable,
                                       depfiles=[filename1],
                                       cache=cache)
                yield make_exeresult(stdout=diffdata)

        diffs = diffs_via_functioncall()
    else:
        for filename1, content2 in diffargs:
            tooldesc, toolcmd, toolargs = difftool
            toolcmd = exename(toolcmd)
            toolargs = subst_filename(filename1, toolargs)
            diffjobs.append(make_execall(toolcmd, toolargs, content2, [filename1]))
            numlines.append(get_num_lines(filename1))
        diffs = run_executables(diffjobs, cache=cache, ccmode=ccmode)
    metriccache = {}  # type: Dict[Tuple[Optional[int], bytes], int]
    num_cached = 0
    num_uncached = 0
    for exeresult, n in izip(diffs, numlines):
        key = (n, exeresult.stdout)
        prevresult = metriccache.get(key)
        if prevresult is not None:
            yield prevresult
            num_cached += 1
        else:
            metric = metric_for_mindiff(exeresult.stdout, numlines=n)
            metriccache[key] = metric
            yield metric
            num_uncached += 1
    num_diffs = num_cached + num_uncached
    if num_diffs > 1:
        percent = 100.0 * float(num_cached) / num_diffs
        iprint(INFO_PERF, yellow('\n%.2f%% of the diffs were duplicates' % percent))


def avg_linelength_diffs(diffargs):
    # type: (List[Tuple[str, bytes]]) -> Iterable[int]
    """Returns the nudged absolute line length differences.
    """
    for filename1, content2 in diffargs:
        linelen1 = get_num_lines(filename1)
        filelen1 = len(get_cached_file(filename1))
        avg1 = 0.0
        if linelen1 > 0:
            avg1 = float(filelen1) / linelen1

        linelen2 = count_content_lines(content2)
        filelen2 = len(content2)
        avg2 = 0.0
        if linelen2 > 0:
            avg2 = float(filelen2) / linelen2

        yield int(abs(10000.0 * (avg1 - avg2)))


def distances_mincontent(difftool,            # type: Tuple[str, str, List[str]]
                         diffargs,            # type: List[Tuple[str, bytes]]
                         cache=None,          # type: Optional[Cache]
                         ccmode=CC_PROCESSES  # type: str
                        ):
    # type: (...) -> Iterable[Tuple[int, int]]
    """Given a list of (filename1, content2) in diffargs this function returns
    the number of characters plus the number of lines of content2 for each diffarg.

    This distance function prioritises the smallest file that has the fewest
    number of lines.
    """
    for _, content2 in diffargs:
        yield len(content2) + count_content_lines(content2), 0


def distances_maxcontent(difftool,            # type: Tuple[str, str, List[str]]
                         diffargs,            # type: List[Tuple[str, bytes]]
                         cache=None,          # type: Optional[Cache]
                         ccmode=CC_PROCESSES  # type: str
                         ):
    # type: (...) -> Iterable[Sequence[int]]
    """The opposite of distances_mincontent.
    """
    return negate_tuplelist(distances_mincontent(difftool, diffargs))


def diff_tools(preferred_tool='auto'):
    # type: (str) -> Iterator[Tuple[str, str, List[str]]]
    """Yields a number of installed and working diff tools that we can use for this program.

    We compare a "Hello, World!" program against itself with two different modifications and
    check if a diff tool returns the expected results.

    The diff tool must treat any kind of change in whitespace as a difference.
    Also it must be able to function with a bit of binary data.
    """
    source = '\n'.join(HELLOWORLD.splitlines()) + '\n'

    # Change leading whitespace.
    mod1 = source.replace('    printf', '  printf')
    # Insert two blank lines and trailing whitespace.
    tmp = mod1.replace('    return 0;', '\n\n    return 0;  ')
    # Replace last line feed with two CRLFs with a zero byte in between.
    mod2 = tmp[:-1] + '\r\n\000\r\n'
    expected_distances = [17, 4, 0]

    fd, tmpfilename = tempfile.mkstemp(suffix='.c', prefix='whatstyle_hello_')
    os.write(fd, bytestr(source))
    os.close(fd)
    try:
        for difftool in DIFF_SPECS:
            if preferred_tool != 'auto' and preferred_tool != difftool[0]:
                continue
            works = True
            try:
                for content2, expdist in zip([mod2, mod1, source], expected_distances):
                    [dist] = distances_from_diffs(difftool, [(tmpfilename, content2)])
                    if dist != expdist:
                        works = False
                        break
            except OSError as exc:
                if exc.errno in [errno.ENOENT, None]:
                    # The diff tool was not found
                    continue
                else:
                    raise
            if works:
                yield difftool
    finally:
        os.remove(tmpfilename)


def unified_diff(filename, content2=None):
    # type: (str, Optional[bytes]) -> Tuple[int, Iterable[str]]
    """This function prints a unified diff of the contents of
    filename and the standard input, when used from the command line
    as follows:
        echo 123 > d.txt ; echo 456 | ./whatstyle.py --stdindiff d.txt
    We get this result:
    ---
    +++
    @@ -1 +1 @@
    -123
    +456
    """
    use_stdin = content2 is None
    if content2 is None:
        # Read binary input stream
        stdin = rawstream(sys.stdin)
        econtent2 = bytestr(stdin.read())
    else:
        econtent2 = content2
    exit_code, diff = compute_unified_diff(filename, econtent2, lineterm='')
    if use_stdin:
        write('\n'.join(diff))
    return exit_code, diff


def compute_unified_diff(filename, content2, **kwargs):
    # type: (str, bytes, **Any) -> Tuple[int, Iterable[str]]
    diff = ()  # type: Iterable[str]
    exit_code = ERROR
    kw = kwargs.copy()
    if 'n' not in kwargs:
        # zero context lines
        kw['n'] = 0
    try:
        content1 = get_cached_file(filename)
        if PY3:
            c1 = unistr(content1)
            c2 = unistr(content2)
        else:
            c1 = content1
            c2 = content2
        diff = difflib.unified_diff(c1.splitlines(True), c2.splitlines(True), **kw)
        exit_code = OK
    finally:
        return exit_code, diff

# ---------------------------------------------------------------------
# Spare the user from specifying a formatter by finding a suitable one.


def formatterclass(fmtname):
    # type: (str) -> Any
    """Returns the class of a formatter given an executable name like
    'yapf' or a version string like 'clang-format version 3.9.0 (trunk)'.
    """
    fmtname = fmtname.lower()
    for prefix, fmtclass in [
        ('clang-format', ClangFormatter),
        ('LLVM', ClangFormatter),
        ('yapf', YapfFormatter),
        ('uncrustify', UncrustifyFormatter),
        ('indent', IndentFormatter),
        ('gindent', IndentFormatter),
        ('GNU indent', IndentFormatter),
        ('astyle', ArtisticStyleFormatter),
        ('Artistic Style', ArtisticStyleFormatter),
        ('tidy', HtmlTidyFormatter),
        ('HTML Tidy', HtmlTidyFormatter),
        ('scalariform', ScalariformFormatter),
        ('scalafmt', ScalafmtFormatter),
        ('rfmt', RfmtFormatter),
        ('rustfmt', RustfmtFormatter),
    ]:
        if fmtname.startswith(prefix.lower()):
            return fmtclass
    return None


class FormatterFailedError(OSError):
    pass


def formatter_version(fmtpath):
    # type: (str) -> str
    """Returns the formatter version, e.g. 'yapf 0.8.2'.
    """
    exeresult = run_executable(fmtpath, ['--version'])
    if exeresult.error is not None:
        raise FormatterFailedError(exeresult.error)
    version_string = unistr(exeresult.stdout).strip()
    orig_version_string = version_string
    if not version_string:
        version_string = unistr(exeresult.stderr).strip()
        if version_string.startswith('indent'):
            version_string = 'indent'
        elif version_string.startswith('usage: rfmt'):
            version_string = 'rfmt'
        else:
            version_string = ''

    if not version_string or re.match(r'^\d', version_string):
        # Call for help if we have no or only the version string without the formatter name.
        exeresult = run_executable(fmtpath, ['--help'])
        if exeresult.error is not None:
            raise FormatterFailedError(exeresult.error)
        version_string = unistr(exeresult.stdout).strip()
        if 'rustfmt' in version_string:
            version_string = 'rustfmt ' + orig_version_string
        else:
            version_string = ''
    return version_string


def choose_formatters(filenames, report=True):
    # type: (List[str], bool) -> List[str]
    """Returns a list of formatters that can be found in the users PATH that support
    every extension of the filenames.
    """
    formatters = formatters_for_files(filenames)
    if not formatters:
        if report:
            reporterror('Error: based on your file types none of the supported '
                        'formatters match your file types, please specify '
                        '--formatter')
            return formatters
    if report:
        iprint(INFO_USER, 'The following formatters support your '
               'file types: %s' % ', '.join(formatters))
    inpath = installed_formatters(formatters)
    if report:
        if inpath:
            iprint(INFO_USER, 'We found the following in your PATH:')
            for idx, fmtpath in enumerate(inpath):
                colorfunc = green if idx == 0 else yellow
                iprint(INFO_USER, colorfunc('    %s' % fmtpath))
        else:
            reporterror('Error: no suitable formatter could be found in your '
                        'PATH, please specify --formatter')
    return inpath


def formatters_for_files(filenames):
    # type: (List[str]) -> List[str]
    """Returns a list of formatter names that support every extension of these filenames.
    """
    exts = set()  # type: Set[str]
    for f in filenames:
        root, ext = os.path.splitext(f)
        ext = ext.lower()
        if not ext and root.startswith('.'):
            # Recognize extension-only filenames as well.
            ext = root.lower()
        exts.add(ext)
    supported = []
    for fmt, fmtexts in SUPPORTED_EXTS:
        fmt_exts = set(fmtexts.split())  # type: Set[str]
        if not exts or exts.issubset(fmt_exts):
            supported.append(fmt)
    return supported


def installed_formatters(formatters):
    # type: (List[str]) -> List[str]
    """Returns a list of absolute filenames of the formatters that can
    be found in the users PATH.
    """
    inpath = []
    for shortname in formatters:
        formatter = formatterclass(shortname)  # type: CodeFormatter
        for executable in formatter.executable_names():
            path = which(executable)
            if path is not None:
                inpath.append(path)
    return inpath

# ---------------------------------------------------------------------


@contextmanager
def pagercontext(usepager):
    # type: (bool) -> Generator
    """Yields a file object that is connected with 'more' or 'less' when this is possible
    and requested.
    """
    fp = None
    if not usepager or WINOS or not sys.stdout.isatty():
        pagercmdargs = []  # type: List[str]
    else:
        pagercmdargs = systempager_cmdargs()
    stty_settings = ''
    proc = None
    try:
        if pagercmdargs:
            if is_executable(STTY_CMD):
                sproc = subprocess.Popen([STTY_CMD, '-g'], stdout=subprocess.PIPE)
                stty_settings, _ = sproc.communicate()
                if sproc.returncode == 0:
                    stty_settings = unistr(stty_settings.strip())
            proc = subprocess.Popen(pagercmdargs, stdin=subprocess.PIPE)
            assert proc is not None
            fp = proc.stdin
        yield fp
        if proc is not None:
            proc.communicate()
    except KeyboardInterrupt:
        # Terminate the pager process
        if proc is not None:
            proc.terminate()
        # Restore stty settings otherwise the terminal might no longer echo input.
        if stty_settings:
            subprocess.Popen([STTY_CMD, stty_settings]).communicate()
        raise


def whatstyle(args, parser):
    # type: (argparse.Namespace, argparse.ArgumentParser) -> int
    """Parses the command line and executes the according actions.
    """
    global COLOR_SUPPORT
    global STDERR_OUTPUT
    global NO_PROGRESS
    global LOGFILE
    global LOGSPLITDIR
    global MESSAGE_CATEGORY_FILES

    if args.logfile:
        LOGFILE = args.logfile
    if args.logsplitdir:
        LOGSPLITDIR = args.logsplitdir
        MESSAGE_CATEGORY_FILES = {}

    if args.colors == 'on':
        COLOR_SUPPORT = True
    elif args.colors == 'off':
        COLOR_SUPPORT = False

    if DEBUG_OPTIONS in args_debug:
        defaultargs = parser.parse_args([])
        parts = []
        for key, defaultvalue in sorted(defaultargs.__dict__.items()):
            value = getattr(args, key)
            if isinstance(value, list):
                value = '[' + ', '.join(value) + ']'
            if isinstance(defaultvalue, list):
                defaultvalue = '[' + ', '.join(defaultvalue) + ']'
            if value != defaultvalue:
                key = green(key)
                value = yellow(value)
            parts.append('%s: %s' % (key, value))
        dprint(DEBUG_OPTIONS, ', '.join(parts))

    filenames = args.filename

    NO_PROGRESS = args.noprogress
    if args.cachepath:
        cachepath = args.cachepath
    else:
        cachepath = default_cachepath()

    if args.usage_examples:
        with pagercontext(not args.nopager) as fp:
            outline(__doc__, fp=fp)
        return OK

    cache = None
    if sqlite3:
        if args.cache == 'disk':
            cache = Cache(cachepath + '.sqlite')
        elif args.cache == 'memory':
            cache = Cache(':memory:')
    if args.cache == 'dirs' or (args.cache == 'disk' and not sqlite3):
        cache = HexCache(cachepath)

    if args.deletecache and cache:
        return cache.deletecache()

    if args.stderr:
        STDERR_OUTPUT = True

    if args.stdindiff:
        if len(filenames) != 1:
            parser.error('--stdindiff needs exactly one filename')
        exit_code, diff = unified_diff(filenames[0])
        return exit_code

    fmtpath = args.formatter
    if fmtpath is None:
        inpath = choose_formatters(filenames, report=not args.supported)
        if args.supported:
            for path in inpath:
                outline(path)
            return OK
        if not inpath:
            return ERROR
        fmtpath = inpath[0]
    if fmtpath is None:
        return ERROR
    fmtpath = exename(fmtpath)

    if not filenames:
        if args.references:
            parser.error('When using --references please specify at least two files')
        else:
            parser.error('Please specify at least one source file')

    if args.mode == MODE_STYLEDIFF and not args.references:
        parser.error('When using mode stylediff you need to use --references as well')

    if args.references and len(filenames) % 2 != 0:
        parser.error('--references only works with an even number of '
                     'files, e.g. file1 reffile1 file2 reffile2 ...')

    # Check if all files can be read
    for filename in filenames:
        try:
            get_cached_file(filename)
        except IOError as exc:
            reporterror('Error: unable to read file "%s": %s' % (filename, exc.strerror))
            return ERROR

    if args.savemetric and not args.output:
        reporterror('Error: please specify --output filename with --savemetric')
        return ERROR

    try:
        try:
            version_string = formatter_version(fmtpath)
        except FormatterFailedError as exc:
            reporterror("Error: Unable to run formatter: %s" % str(exc))
            return ERROR

        for difftool in diff_tools(args.difftool):
            break
        else:
            reporterror('Error: We have not found one usable diff tool')
            return ERROR

        fmtclass = formatterclass(version_string)
        if fmtclass is None:
            reporterror('Unknown formatter type %s %s' % (fmtpath, version_string))
            supported = ', '.join([fmt for fmt, _ in SUPPORTED_EXTS])
            reporterror('Currently only these formatters are supported: %s' % supported)
            return ERROR
        formatter = fmtclass(fmtpath, cache=cache)
        iprint(INFO_USER, '-----------------------\nRunning whatstyle ' + __version__)
        iprint(INFO_USER, 'Using formatter %s (%s)' % (green(formatter.exe), version_string))
        diffcmd = ' '.join([difftool[1]] + difftool[2])
        iprint(INFO_USER, 'Using this for comparing files: %s' % diffcmd)
        formatter.use_startstyle(args.startstyle)
        formatter.allow_encoding_change = args.allow_encoding_change
        formatter.keeptempfiles = args.keeptempfiles
        formatter.register_options()

        ignoreopts = args.ignoreopts.split(',') if args.ignoreopts else []
        params = ParameterSet(formatter, difftool, args.mode, args.sourcefactor,
                              args.variantsfactor, args.references, args.maxrounds,
                              ignoreopts, args.bestofround, args.concat, args.concurrent)
        result = find_style(params, filenames, language=args.language)
        exit_code = handle_results(args,
                                   formatter,
                                   filenames,
                                   args.mode,
                                   args.references,
                                   ignoreopts,
                                   result,
                                   args.diff,
                                   args.uncondensed,
                                   output=args.output,
                                   savemetric=args.savemetric)
    finally:
        if cache is not None:
            cache.close()
    return exit_code


def show_diffs(formatter,          # type: CodeFormatter
               filenames,          # type: List[str]
               style,              # type: Style
               ansi,               # type: bool
               ansihtml,           # type: bool
               html,               # type: bool
               nopager,            # type: bool
               numlines,           # type: int
               wrapcolumn=0,       # type: int
               linenumbers=False,  # type: bool
               enc='utf-8'         # type: str
               ):
    # type: (...) -> None
    """Show the differences between the current and reformatted sources.
    """
    if not ansi and not html:
        if supports_color():
            ansi = True
        else:
            html = True
    pairs = []
    for filename in filenames:
        sourcedata = get_cached_file(filename)
        content = formatter.formatcode(style, sourcedata, filename=filename)
        pairs.append((sourcedata, content))

    unifilenames = '\n'.join([unifilename(f) for f in filenames]) + '\n'
    htmldiffer = HtmlMultiDiff(tabsize=4, wrapcolumn=wrapcolumn)
    table = htmldiffer.table_from_pairs(pairs,
                                        enc,
                                        fromdesc='',
                                        todesc='',
                                        context=True,
                                        numlines=numlines)
    headerhtml = '<pre>\n' + unifilenames + '</pre>'
    customhtml = make_custom_html(htmldiffer, headerhtml, [table], enc=enc)
    htmldata = unescape_ill_surrencode(customhtml, enc=enc)
    htmldata = translate_non_sgml_chars(htmldata, enc=enc)
    headerdata = yellow(unifilenames)
    if html or ansihtml:
        if ansihtml:
            htmldata = html2ansihtml(
                surrdecode(htmldata, enc=enc),
                header=headerdata,
                enc=enc,
                linenumbers=linenumbers)
        fd, tempname = tempfile.mkstemp(suffix='.html', prefix='whatstyle_')
        os.write(fd, htmldata)
        os.close(fd)
        url = urljoin('file:', pathname2url(tempname))
        webbrowser.open(url)
    elif ansi:
        with pagercontext(not nopager) as fp:
            outline(headerdata, fp=fp)
            htmldiff2ansi(customhtml, enc, linenumbers=linenumbers, fp=fp)


def metricfile(filename):
    # type: (str) -> str
    """Returns the filename of the metric file next to the config file.
    """
    return os.path.join(os.path.dirname(filename), 'wsmetric.txt')


def handle_results(args,             # type: argparse.Namespace
                   formatter,        # type: CodeFormatter
                   filenames,        # type: List[str]
                   mode,             # type: str
                   references,       # type: bool
                   ignoreopts,       # type: Sequence[str]
                   result,           # type: Union[StyleDist, Tuple[StyleDist, StyleDist]]
                   showdiff,         # type: bool
                   uncondensed,      # type: bool
                   output=None,      # type: Optional[str]
                   savemetric=False  # type: bool
                   ):
    # type: (...) -> int
    """Prints the result style, differences and variants.
    """
    if mode == MODE_STYLEDIFF:
        _, (beststyle, bestdist) = result
    else:
        beststyle, bestdist = result

    if beststyle is None:
        reporterror("Error: The files could not be reformatted at all")
        return ERROR

    fromfiles, _ = split_reffiles(references, filenames)
    styletext = formatter.styletext(beststyle)
    if output:
        with open(output, 'wb') as fp:
            write(styletext, fp)
            iprint(INFO_USER, 'Result style written to "%s"' % output)
    if savemetric:
        with open(metricfile(output), 'wb') as fp:
            data = bytestr(', '.join([str(x) for x in bestdist]))
            data += bytestr('  # %s\n' % formatter.version_string)
            write(data, fp)
    if mode == MODE_STYLEDIFF:
        (beststyle1, bestdist1), (beststyle, bestdist) = result
        unique_from, unique_to = deep_difference(beststyle1, beststyle)
        if output is not None:
            fp = open(output + '_diff', 'wb')
        else:
            fp = None

        def report_style(colorfunc, text):
            # type: (Callable[[str], str], str) -> None
            iprint(INFO_RESULT, colorfunc(text))
            if fp is not None:
                outline(text, fp=fp)

        report_style(yellow, '# Best style for the from-files')
        report_style(yellow, formatter.styletext(beststyle1))
        report_style(cyan, '# Best style for the to-files')
        report_style(cyan, formatter.styletext(beststyle))
        if not unique_to and not unique_from:
            report_style(red, '# No style difference found.')
        else:
            if unique_from:
                report_style(red, '# These options are unique to the from-files:')
                report_style(red, formatter.styletext(style_make(unique_from)))
            if unique_to:
                report_style(green, '# These options are unique to the to-files:')
                report_style(green, formatter.styletext(style_make(unique_to)))
        if fp is not None:
            fp.close()
    else:
        quality = ''
        if bestdist[0] == 0:
            quality = ' - it perfectly matches your sources.'
        outline('### This style was chosen for your %s%s' %
                (formatter.configfilename, quality))
        write(green(styletext))

    if showdiff:
        sys.stdout.flush()
        show_diffs(formatter, fromfiles, beststyle, args.ansi, args.ansihtml, args.html,
                   args.nopager, args.context, args.wrapcolumn, args.linenumbers,
                   args.charset)

    if args.variants:
        sys.stdout.flush()
        nochoice = not (args.ansi or args.html or args.ansihtml)
        if args.html or (nochoice and not supports_color()):
            display = 'html'
        elif args.ansihtml:
            display = 'ansihtml'
        else:
            display = 'ansi'
        usepager = not args.nopager and display == 'ansi'
        with pagercontext(usepager) as fp:
            show_variants(beststyle,
                          formatter,
                          filenames,
                          ignoreopts=ignoreopts,
                          display=display,
                          enc=args.charset,
                          condensed=not uncondensed,
                          numhunks=args.numhunks,
                          numlines=args.context,
                          wrapcolumn=args.wrapcolumn,
                          linenumbers=args.linenumbers,
                          savehtml=args.savehtml,
                          outputfile=args.output,
                          fp=fp,
                          ccmode=args.concurrent)
    return OK

# ----------------------------------------------------------------------


def cmdline_parser(parserclass=argparse.ArgumentParser):
    # type: (type) -> argparse.ArgumentParser
    """The option to change the parserclass is used for testing the
    command line options.

    The options with suppressed help messages should only be used by
    whatstyle itself or for testing.
    """
    parser = parserclass(description='Find the best matching code formatter style for '
                         'given source code files',
                         formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('filename', nargs='*', help='source filename')
    parser.add_argument('--version', action='version', version='whatstyle ' + __version__)
    parser.add_argument('--usage-examples',
                        action='store_true',
                        help='show some scenarious where whatstyle could be useful')
    parser.add_argument('--supported',
                        action='store_true',
                        help='list the supported and installed formatters (for the\n'
                        'filenames or extensions specified or all if unspecified)')
    parser.add_argument('--formatter',
                        '-f',
                        help='name or path of the formatter (e.g. yapf or\n'
                        '/usr/local/bin/clang-format)')
    parser.add_argument('--diff',
                        action='store_true',
                        help='also show how the reformatted sources look\n'
                        'compared to the current sources')
    parser.add_argument('--variants',
                        action='store_true',
                        help='show how different options transform the source code')
    parser.add_argument('--uncondensed',
                        action='store_true',
                        help='do not group option values when displaying variants')
    parser.add_argument('--numhunks',
                        type=int,
                        default=1,
                        help='number of hunks to display per diff in variants\n'
                        ' -1 means unlimited (default: 1)')
    parser.add_argument('--context',
                        '-C',
                        type=int,
                        default=CONTEXTLINES,
                        help='number of diff context lines to show (default: %s)' %
                        CONTEXTLINES)
    parser.add_argument('--wrapcolumn',
                        '-W',
                        type=int,
                        default=0,
                        help='column number where lines are broken and wrapped\n'
                        '(default: don\'t wrap)')
    parser.add_argument('--linenumbers',
                        action='store_true',
                        help='display line numbers in diffs')
    parser.add_argument('--ansi', action='store_true', help='display differences in ANSI')
    parser.add_argument('--ansihtml',
                        action='store_true',
                        help='display differences in HTML resembling the ANSI output')
    parser.add_argument('--html', action='store_true', help='display differences in HTML')
    parser.add_argument('--savehtml',
                        action='store_true',
                        help='save html without opening a browser')
    parser.add_argument('--output', '-o', help='filename of the result style or html file')
    parser.add_argument('--savemetric', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--startstyle',
                        help='style to start the search with (optional)\n'
                        'e.g. "{based_on_style: pep8, column_limit: 79}",\n'
                        'the specific options must be known by the formatter')
    parser.add_argument('--ignoreopts',
                        help='comma-separated list of options to ignore\n'
                        'e.g. indent_width,column_limit')
    parser.add_argument('--language',
                        '-l',
                        help='explicitly tell the formatter the language to expect')
    parser.add_argument('--mode',
                        choices=[MODE_NORMAL, MODE_MAXDIFF, MODE_MINIMIZE, MODE_MAXIMIZE,
                                 MODE_RESILIENT, MODE_STYLEDIFF],
                        default=MODE_NORMAL,
                        help='normal: simply minimize the difference\n'
                        'minimize: make the files short (save diskspace and paper)\n'
                        'maximize: make the files long (make room for annotations)\n'
                        'maxdiff: make the files as different as possible\n'
                        'resilient: make the style resilient against drastic\n'
                        ' reformatting changes(default: normal)')
    parser.add_argument('--sourcefactor',
                        type=int,
                        default=None,
                        help='source metric factor for the resilient mode')
    parser.add_argument('--variantsfactor',
                        type=int,
                        default=None,
                        help='variants metric factor for the resilient mode')
    parser.add_argument('--references',
                        '-r',
                        action='store_true',
                        help='compare the reformatted files with reference '
                        'files as in:\n'
                        '  --references file1 reffile1 file2 reffile2 ...')
    parser.add_argument('--maxrounds',
                        type=int,
                        default=UNLIMITED,
                        help='limit number of optimization rounds\n'
                        ' -1 means unlimited (default: -1)')
    parser.add_argument('--bestofround', type=int, default=1, help=argparse.SUPPRESS)
    parser.add_argument('--concat',
                        action='store_true',
                        help='reformat concatenated input files (faster but less precise)')
    parser.add_argument('--allow-encoding-change',
                        action='store_true',
                        help='allow HTML tidy to change the encoding')
    parser.add_argument('--charset',
                        default='utf-8',
                        help='input source code encoding (default: utf-8)')
    parser.add_argument('--difftool',
                        choices=['difflib', 'diff', 'gitdiff', 'auto'],
                        default='auto',
                        help='difftool to use (default: auto, the first working one\n'
                        'which should be difflib)')
    parser.add_argument('--stdindiff', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--cache',
                        choices=['disk', 'memory', 'dirs', 'off'],
                        default='disk',
                        help='type of cache to use'
                        ' (default: disk)')
    parser.add_argument('--cachepath', help=argparse.SUPPRESS)
    parser.add_argument('--deletecache', action='store_true', help='delete the cache')
    parser.add_argument('--keeptempfiles',
                        action='store_true',
                        help='do not delete the temp files')
    parser.add_argument('--concurrent',
                        choices=['off', 'threads', 'processes'],
                        default='processes',
                        help='mode of concurrent execution  (default: processes)')
    parser.add_argument('--colors',
                        choices=['on', 'off', 'auto'],
                        default='auto',
                        help='use colorized output (default: auto)')
    parser.add_argument('--nopager',
                        action='store_true',
                        help='do not pipe the output into a pager')
    parser.add_argument('--noprogress',
                        action='store_true',
                        help='do not report the progress of the executables')
    # The info and debug options were inspired by rsync.
    parser.add_argument('--info', help='fine-grained informational verbosity')
    parser.add_argument('--debug', help='fine-grained debug verbosity')
    parser.add_argument('--logfile', help=argparse.SUPPRESS)
    parser.add_argument('--logsplitdir', help=argparse.SUPPRESS)
    parser.add_argument('--stderr',
                        action='store_true',
                        help='write informational messages to standard error')
    parser.add_argument('--profile', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--quiet', '-q', action='store_true', help='only show the results')
    parser.add_argument('--verbose', '-v', action='count')
    return parser


def resolve_implicits(implies, opts):
    # type: (Dict[str, List[str]], List[str]) -> Set[str]
    """Adds implied logging options recursively so that specifying e.g.
    --debug=popenio results in --debug=popenio,popen.
    """
    optset = set(opts)
    last_num_opts = None
    num_opts = len(optset)
    while last_num_opts != num_opts:
        for opt, impliedopts in implies.items():
            if opt in optset:
                optset.update(impliedopts)
        last_num_opts = num_opts
        num_opts = len(optset)
    return optset


def set_logging_parameters(args, parser):
    # type: (argparse.Namespace, argparse.ArgumentParser) -> None
    for attr, catset, defaults, implies in (('info', args_info, INFO_ALL, INFO_IMPLIES_DICT),
                                            ('debug', args_debug, DEBUG_ALL,
                                             DEBUG_IMPLIES_DICT)):
        option = getattr(args, attr)
        if option:
            opts = [x.lower() for x in option.split(',')]
            for opt in opts:
                if opt not in (defaults + ['all']):
                    parser.error('unknown --%s category "%s"' % (attr, opt))

            catset.update(resolve_implicits(implies, opts))
            if 'all' in catset:
                catset.update(defaults)

    verbose = args.verbose or 0
    if not args.quiet:
        for level in sorted(verbose_categories):
            if verbose >= level:
                for category in verbose_categories[level]:
                    if category in INFO_ALL:
                        args_info.add(category)
                    elif category in DEBUG_ALL:
                        args_debug.add(category)
    args_info.add(INFO_RESULT)


def profile(sourcecode, p_locals, p_globals):
    import cProfile
    import pstats
    prof_filename = os.path.join(tempfile.gettempdir(), "%s.prof" % __file__)
    try:
        cProfile.runctx(sourcecode, p_locals, p_globals, prof_filename)
        p = pstats.Stats(prof_filename)
        p.sort_stats('cumulative').print_stats(40)
    finally:
        os.remove(prof_filename)


def run_with_args(args, parser):
    # type: (argparse.Namespace, argparse.ArgumentParser) -> int
    set_logging_parameters(args, parser)
    start_time = time.time()
    ret = OK
    try:
        if args.profile:
            outline("Profiling...")
            profile("ret = whatstyle(args, parser)", locals(), globals())
        else:
            ret = whatstyle(args, parser)
    except IOError as exc:
        # If the output is piped into a pager like 'less' we get a broken pipe when
        # the pager is quit early and that is ok.
        if exc.errno == errno.EPIPE:
            pass
        elif str(exc) == 'Stream closed':
            pass
        else:
            raise
        if not PY2:
            sys.stderr.close()
    iprint(INFO_TIME, 'Run time: %s seconds' % (time.time() - start_time))
    return ret


def main():
    # type: () -> int
    try:
        parser = cmdline_parser()
        args = parser.parse_args()
        return run_with_args(args, parser)
    except KeyboardInterrupt:
        return ERROR


if __name__ == '__main__':
    if sys.version_info[0] < 3 and os.name == 'nt':
        # Enable unicode arguments on Windows
        sys.argv = win32_unicode_argv()
    sys.exit(main())
