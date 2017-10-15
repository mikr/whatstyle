#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (c) 2016 Michael Krause ( http://krause-software.com/ ).

# You are free to use this code under the MIT license:
# http://opensource.org/licenses/MIT
"""Tests for whatstyle."""

from __future__ import print_function

import argparse
import json
import os
import re
import sys
import tempfile
import unittest
from collections import OrderedDict
from io import BytesIO
from os.path import abspath, dirname
from pprint import pprint

# Set up the Python path so we find the whatstyle module in the
# parent directory relative to this file.
sys.path.insert(1, dirname(dirname(abspath(__file__))))

import whatstyle
from whatstyle import bytestr, unistr


def run_args(*args):
    outbuf, errbuf = BytesIO(), BytesIO()
    with RedirectStdStreams(stdout=outbuf, stderr=errbuf):
        parser = whatstyle.cmdline_parser(ErrorRecordingArgumentParser)
        args = parser.parse_args(*args)
        ret = whatstyle.run_with_args(args, parser)
    return ret, outbuf.getvalue(), errbuf.getvalue()

# ---------------------------------------------------------------
# This is RedirectStdStreams from an answer by Rob Cowie on
# http://stackoverflow.com/questions/6796492/python-temporarily-redirect-stdout-stderr/6796752#6796752
# which is used to record the output of whatstyle as if called from the
# command line.


class RedirectStdStreams(object):

    def __init__(self, stdout=None, stderr=None):
        self._stdout = stdout or sys.stdout
        self._stderr = stderr or sys.stderr

    def __enter__(self):
        self.old_stdout, self.old_stderr = sys.stdout, sys.stderr
        self.old_stdout.flush()
        self.old_stderr.flush()
        sys.stdout, sys.stderr = self._stdout, self._stderr

    def __exit__(self, exc_type, exc_value, traceback):
        self._stdout.flush()
        self._stderr.flush()
        sys.stdout = self.old_stdout
        sys.stderr = self.old_stderr

# ---------------------------------------------------------------


class ErrorRecordingArgumentParser(argparse.ArgumentParser):

    def __init__(self, *args, **kwargs):
        super(ErrorRecordingArgumentParser, self).__init__(*args, **kwargs)
        self.testerrors = []

    def error(self, message):
        self.testerrors.append(message)

# ---------------------------------------------------------------


def tmpfile_with_data(data, **kwargs):
    fd, filename = tempfile.mkstemp(**kwargs)
    os.write(fd, bytestr(data))
    os.close(fd)
    return filename


def defaultcache():
    if whatstyle.sqlite3:
        return whatstyle.Cache(whatstyle.default_cachepath() + '.sqlite')
    return None


class ParsehistoryTestCase(unittest.TestCase):

    def test_tabcombo(self):
        history = whatstyle.parse_style_history()
        self.assertEqual(len(history), 62)


class DiffTestCase(unittest.TestCase):

    def setUp(self):
        self.cache = defaultcache()

    def test_builtin_diff(self):
        source = "a\nb\nc\n"
        content2 = bytestr("a\ne\nc\n")
        bn = "whatstyle_diff_"
        filename = tmpfile_with_data(source, suffix=bn)
        try:
            code, diff = whatstyle.compute_unified_diff(filename, content2)
            diffdata = ''.join(diff)
            diffdata = bytestr(diffdata)
            metric = whatstyle.metric_for_mindiff(diffdata)
            self.assertEqual(metric, 4)
        finally:
            os.remove(filename)

    def test_pathological_diffs(self):
        """Assure that a diff of a file with a different indentation or
         a few added lines has a better diff metric than a file that
         is basically empty.
        """
        difftool = whatstyle.BUILTIN_DIFF
        origlines = []
        newlines = []
        # The original file has 200 lines with numbers and a 4 space indent.
        for i in range(1, 201):
            origlines.append('    %d' % i)
            # The file to compare with has a 2 space indent.
            newlines.append('  %d' % i)
        source = '\n'.join(origlines) + '\n'
        mod1 = '\n'.join(newlines) + '\n'
        mod2 = mod1 + '\n\n\n\n\n\n\n\n'
        mod3 = ''
        mod4 = '\n'

        fd, tmpfilename = tempfile.mkstemp(suffix='.c', prefix='whatstyle_test_')
        os.write(fd, bytestr(source))
        os.close(fd)
        try:
            dists = whatstyle.distances_from_diffs(difftool,
                                                   [(tmpfilename, mod1), (tmpfilename, mod2),
                                                    (tmpfilename, mod3), (tmpfilename, mod4)])
            dists = list(dists)
            self.assertLess(dists[0], dists[2])
            self.assertLess(dists[0], dists[3])
            self.assertLess(dists[1], dists[2])
            self.assertLess(dists[1], dists[3])
        finally:
            os.remove(tmpfilename)

    def tearDown(self):
        if self.cache is not None:
            self.cache.close()


class CmdlineTestCase(unittest.TestCase):

    source = """\
def func():

  def nestedfunc(): # type: () -> None
    pass

  print("Hello World!")
"""

    expected = """\
### This style was chosen for your .style.yapf - it perfectly matches your sources.
[style]
based_on_style = chromium
spaces_before_comment = 1
"""

    def test_convert(self):
        lines = self.source.splitlines()
        data = os.linesep.join(lines) + os.linesep
        filename = tmpfile_with_data(data, suffix='.py', prefix='whatstyle_')
        try:
            ret, outtxt, errtxt = run_args(['--quiet', '--color=off', '--stderr', filename])
            self.assertEqual(ret, 0)
            self.assertEqual(outtxt, bytestr(self.expected))
        finally:
            os.remove(filename)


NESTED_VARS_DUMP = """\
---
Language:        Cpp
# BasedOnStyle:  LLVM
AlignTrailingComments: true
DerivePointerAlignment: false
PointerAlignment: Right
TabWidth:        8
UseTab:          Never
...

"""

JQUERY_TABS = """\
jQuery.noConflict = function(deep) {
\treturn jQuery;
};
"""


class VariantTestCase(unittest.TestCase):

    def setUp(self):
        pass

    def test_quoting(self):
        a = bytestr(r'\x41\u0042\U000043')
        b = whatstyle.surrdecode(a)
        b = whatstyle.escape_illegal_xmlchars(b)
        b = whatstyle.unescape_illegal_xmlchars(b)
        b = whatstyle.surrencode(b)
        self.assertEqual(a, b)

    def test_tableiter(self):
        bindata = []
        text = 'IŋƫƐrnætiønæl'
        text_a = bytestr(text) + b'\0\1\2\n'
        text_b = bytestr(r'\x41\u0042\U000043')
        for i in range(256):
            fragments = [bytestr('%0x %d ' % (i, i))]
            h = int(i / 256)
            l = i % 256
            if h:
                fragments.append(whatstyle.int2byte(h))
            fragments.append(whatstyle.int2byte(l))
            data = b''.join(fragments)
            udata = data.decode('utf-8', 'replace')
            ulen = whatstyle.unilen(udata)
            data = b' ' * (16 - ulen) + data + b'\n'
            bindata.append(data)
        text_b += b''.join(bindata)
        try:
            bodies = []
            for tablestart, tbody, tableend in whatstyle.table_iter([(text_a, text_b)],
                                                                    1,
                                                                    fromdesc='',
                                                                    todesc=''):
                bodies.append(tbody)
            html = '\n'.join(bodies)
            buf = BytesIO()
            html = '<html><body><table>' + html + '</table></body></html>'
            whatstyle.htmldiff2ansi(html, enc='utf-8', fp=buf)
            ansi = buf.getvalue()
            # whatstyle.outline(ansi)
            self.assertEqual(len(re.findall(bytestr(text) + b'\0\1\2', ansi)), 1)
        except whatstyle.ETree.ParseError:
            raise

    def tearDown(self):
        pass


class OptioncomboTestCase(unittest.TestCase):

    def setUp(self):
        clangformat = 'clang-format'
        self.cache = defaultcache()
        formatters = whatstyle.installed_formatters([clangformat])
        if not formatters:
            self.fail('%s is not installed' % clangformat)
        self.formatter = whatstyle.ClangFormatter(clangformat, cache=self.cache)
        self.formatter.register_options()
        self.assertIsNotNone(self.formatter.styledefinition)

    def test_bools(self):
        option = whatstyle.option_make('IndentCaseLabels', 'bool', ())
        result = self.formatter.variants_for(option)
        self.assertEqual(result, [{'IndentCaseLabels': True}, {'IndentCaseLabels': False}])

    def test_tabcombo(self):
        v = self.formatter.variants_for
        res = v(whatstyle.option_make('UseTab', 'UseTabStyle', ('Never', 'ForIndentation',
                                                                'Always')))
        # yapf: disable
        self.assertEqual(
            res, [{'UseTab': 'Never'},
                  {'UseTab': 'ForIndentation', 'TabWidth': 1},
                  {'UseTab': 'ForIndentation', 'TabWidth': 2},
                  {'UseTab': 'ForIndentation', 'TabWidth': 3},
                  {'UseTab': 'ForIndentation', 'TabWidth': 4},
                  {'UseTab': 'ForIndentation', 'TabWidth': 5},
                  {'UseTab': 'ForIndentation', 'TabWidth': 6},
                  {'UseTab': 'ForIndentation', 'TabWidth': 7},
                  {'UseTab': 'ForIndentation', 'TabWidth': 8},
                  {'UseTab': 'Always', 'TabWidth': 1},
                  {'UseTab': 'Always', 'TabWidth': 2},
                  {'UseTab': 'Always', 'TabWidth': 3},
                  {'UseTab': 'Always', 'TabWidth': 4},
                  {'UseTab': 'Always', 'TabWidth': 5},
                  {'UseTab': 'Always', 'TabWidth': 6},
                  {'UseTab': 'Always', 'TabWidth': 7},
                  {'UseTab': 'Always', 'TabWidth': 8}])
        # yapf: enable

    def test_nestedoption(self):
        style = self.formatter.styledefinition
        option = whatstyle.styledef_option(style, 'BraceWrapping')
        if option is None:
            # This clang-format is probably too old.
            return
        res = self.formatter.variants_for(option)
        # We expect 11 nested options with true and false values.
        self.assertEqual(len(res), 22)

    def tearDown(self):
        if self.cache is not None:
            self.cache.close()


class StylecompareTestCase(unittest.TestCase):

    maxDiff = None
    jenc = json.JSONEncoder(sort_keys=True).encode

    def setUp(self):
        clangformat = 'clang-format'
        self.cache = defaultcache()
        formatters = whatstyle.installed_formatters([clangformat])
        if not formatters:
            self.fail('%s is not installed' % clangformat)
        self.formatter = whatstyle.ClangFormatter(clangformat, cache=self.cache)
        self.formatter.register_options()
        self.assertIsNotNone(self.formatter.styledefinition)

    def test_enum(self):
        estyle = self.formatter.effective_style(whatstyle.style_make())
        self.assertEqual(estyle.get('UseTab'), 'Never')

    def test_yaml(self):
        res = whatstyle.parse_miniyaml("""\
---
Language:        Cpp
# BasedOnStyle:  LLVM
...
""")
        self.assertEqual(res, OrderedDict([('Language', 'Cpp'), ('BasedOnStyle', 'LLVM')]))

    def test_yaml_2(self):
        res = whatstyle.parse_miniyaml("""\
---
IncludeCategories:
  - Regex:           '^"(llvm|llvm-c|clang|clang-c)/'
    Priority:        2
""")
        self.assertEqual(res,
                         OrderedDict([('IncludeCategories', [
                             OrderedDict([('Regex', '^"(llvm|llvm-c|clang|clang-c)/'),
                                          ('Priority', 2)])
                         ])]))

    def test_yaml_3(self):
        res = whatstyle.parse_miniyaml("""\
---
IncludeCategories:
  - Regex:           '^"(llvm|llvm-c|clang|clang-c)/'
    Priority:        2
IndentCaseLabels: false
""")
        self.assertEqual(res,
                         OrderedDict([('IncludeCategories', [
                             OrderedDict([('Regex', '^"(llvm|llvm-c|clang|clang-c)/'),
                                          ('Priority', 2)])
                         ]), ('IndentCaseLabels', False)]))

    def test_yaml_map_list(self):
        res = whatstyle.parse_miniyaml("""\
---
Language:        Cpp
# BasedOnStyle:  LLVM
AccessModifierOffset: -2
BraceWrapping:
  AfterClass:      false
  AfterControlStatement: false
IncludeCategories:
  - Regex:           '^"(llvm|llvm-c|clang|clang-c)/'
    Priority:        2
  - Regex:           '.*'
    Priority:        1
IndentCaseLabels: false
...
""")
        # yapf: disable
        expected = OrderedDict([('Language', 'Cpp'),
                                ('BasedOnStyle', 'LLVM'),
                                ('AccessModifierOffset', -2),
                                ('BraceWrapping', OrderedDict([
                                    ('AfterClass', False),
                                    ('AfterControlStatement', False)])),
                                ('IncludeCategories',
                                 [OrderedDict([(
                                     'Regex', '^"(llvm|llvm-c|clang|clang-c)/'),
                                     ('Priority', 2)]),
                                  OrderedDict([(
                                     'Regex', '.*'),
                                     ('Priority', 1)])]),
                                ('IndentCaseLabels', False)])
        # yapf: enable
        self.assertMultiLineEqual(self.jenc(res), self.jenc(expected))

    def test_yaml_inline_list(self):
        res = whatstyle.parse_miniyaml("""\
ForEachMacros:   [ foreach, Q_FOREACH, BOOST_FOREACH ]
""")
        # yapf: disable
        expected = OrderedDict([('ForEachMacros', ["foreach", "Q_FOREACH", "BOOST_FOREACH"])])
        # yapf: enable
        self.assertMultiLineEqual(self.jenc(res), self.jenc(expected))

    def test_yaml_plain_list(self):
        res = whatstyle.parse_miniyaml("""\
---
ForEachMacros:
  - foreach
  - Q_FOREACH
  - BOOST_FOREACH
...
""")
        # yapf: disable
        expected = OrderedDict([('ForEachMacros', ["foreach", "Q_FOREACH", "BOOST_FOREACH"])])
        # yapf: enable
        self.assertMultiLineEqual(self.jenc(res), self.jenc(expected))


# ---------------------------------------------------------------

if __name__ == '__main__':
    unittest.main()
