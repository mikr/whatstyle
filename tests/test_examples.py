#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# This script runs whatstyle on a number of source code
# examples and saves the resulting style and the reformatted
# source to a result directory. The metric of the style is also
# saved and compared to the last known good metric.
# The slightest difference might be caused by an error, a performance
# improvement because the same style was found earlier or a change
# of the formatter version string.
#
# This script is used for regression testing in a virtual machine defined
# in Vagrantfile because the metric comparison requires exactly the same
# formatter versions.
#
# When running these example tests on a machine where different results
# expected, this sequence can be used from a checked out whatstyle git repo:
#   $ ./tools/remove_resultdirs.py   # this removes the last known good results
#   $ python tests/test_examples.py
#   $ git diff                       # look at the differences
#
# Testing only some formatters can be as follows:
#   $ WHATSTYLE_TEST_FORMATTERS="uncrustify astyle" \
#       python tests/test_examples.py ExamplesTestCase.test_xv6

from __future__ import print_function

import sys

if (((sys.version_info[0] == 2) and (sys.version_info[1] < 7)) or (
    (sys.version_info[0] == 3) and (sys.version_info[1] < 2))):
    sys.stderr.write('Error: Python 2.7 or when running on Python 3 at least Python 3.2'
                     ' is required to run whatstyle\n')
    sys.exit(1)

import errno
import logging
import os
import subprocess
import unittest

from os.path import abspath, basename, dirname, exists, join

# Set up the Python path so we find the whatstyle module in the
# parent directory relative to this file.
sys.path.insert(1, dirname(dirname(abspath(__file__))))

import whatstyle
from whatstyle import sysfilename, unifilename, unistr, bytestr

PY2 = sys.version_info[0] == 2
PY3 = sys.version_info[0] == 3

if PY2:
    text_type = unicode
    binary_type = str
else:
    text_type = str

WHATSTYLE_VIA_SUBPROCESS = False

logging.basicConfig(stream=sys.stdout)
logging.getLogger().setLevel(logging.DEBUG)

# ---------------------------------------------------------------------


def here():
    return dirname(abspath(unifilename(__file__)))


def exampledir():
    return join(here(), 'examples')


def setUpModule():
    pass


def tearDownModule():
    pass

# ---------------------------------------------------------------------

CURRENT_TEST = None


class ExamplesTestCase(unittest.TestCase):

    maxDiff = None

    def setUp(self):
        global CURRENT_TEST
        CURRENT_TEST = self.id()

    def test_python(self):
        self.assertMultiLineEqual('', run_spec({'files': [
            'bottle_sqlalchemy/bottle_sqlalchemy.py'
        ]}))

    def test_html(self):
        self.assertMultiLineEqual('', run_spec({'files': ['foundation6/index.html']}))

    def test_gumbo(self):
        self.assertMultiLineEqual('', run_spec({'files': ['gumbo-parser/utf8.c'],
                                                'formatters':
                                                ['clang-format', 'uncrustify']}))

    def test_objc(self):
        self.assertMultiLineEqual('', run_spec(
            {'files': ['HexFiend/HFIndexSet.h', 'HexFiend/HFIndexSet.m'],
             'formatters': ['clang-format', 'uncrustify']}))

    def test_javascript(self):
        self.assertMultiLineEqual('', run_spec({'files': ['jasmine/Clock.js'],
                                                'formatters': ['clang-format']}))

    def test_resilient_cpp(self):
        self.assertMultiLineEqual('', run_spec({'files': ['lodepng/pngdetail.cpp'],
                                                'formatters': ['clang-format', 'indent'],
                                                'mode': 'resilient'}))

    def test_international(self):
        self.assertMultiLineEqual('', run_spec({'files': ['international/IŋƫƐrnætiønæl.c']}))

    def test_resilient_c(self):
        self.assertMultiLineEqual('', run_spec({'files': ['lua/lstate.h', 'lua/lstate.c'],
                                                'mode': 'resilient'}))

    def test_nginx(self):
        self.assertMultiLineEqual('', run_spec({'files':
                                                ['nginx/ngx_buf.h', 'nginx/ngx_buf.c'],
                                                'mode': 'resilient'}))

    def test_java(self):
        self.assertMultiLineEqual('',
                                  run_spec({'files': ['maven/ProjectModelResolver.java']}))

    def test_stylediff_noalign(self):
        self.assertMultiLineEqual('', run_spec(
            {'files': ['ref_no_align/noalign.c', 'ref_no_align/noalign_ref.c'],
             'mode': 'stylediff',
             'references': True}))

    def test_stylediff_space_brace(self):
        self.assertMultiLineEqual('', run_spec(
            {'files': ['ref_space_brace/hello.c', 'ref_space_brace/hello_ref.c'],
             'mode': 'stylediff',
             'references': True}))

    def test_xv6(self):
        self.assertMultiLineEqual('', run_spec({'files': ['xv6/printf.c']}))

    def test_scala(self):
        filename = 'spark/SparkLR.scala'
        fmts = whatstyle.choose_formatters([filename], report=False)
        if not fmts:
            self.skipTest('no formatters for scala installed')
            return
        ignore = '--ignoreopts=alignSingleLineCaseStatements.maxArrowIndent'
        self.assertMultiLineEqual('', run_spec({'files': [filename], 'extraargs': [ignore]}))

    def test_rfmt(self):
        filename = 'rfmt/test.R'
        fmts = whatstyle.choose_formatters([filename], report=False)
        if not fmts:
            self.skipTest('no formatters for R installed')
            return
        # Use a very short column limit to give the cost functions something to work with.
        shortlines = '--startstyle={margin1: 30}'
        self.assertMultiLineEqual('', run_spec({'files': [filename],
                                                'extraargs': [shortlines]}))

    def test_polycode(self):
        filename = 'polydraw/intersection.rs'
        fmts = whatstyle.choose_formatters([filename], report=False)
        if not fmts:
            self.skipTest('no formatters for Rust installed')
            return
        self.assertMultiLineEqual('', run_spec({'files': [filename]}))

    def test_rustfmt(self):
        filename = 'rustfmt/checkstyle.rs'
        fmts = whatstyle.choose_formatters([filename], report=False)
        if not fmts:
            self.skipTest('no formatters for Rust installed')
            return
        self.assertMultiLineEqual('', run_spec({'files': [filename]}))

    def tearDown(self):
        global CURRENT_TEST
        CURRENT_TEST = None

# ---------------------------------------------------------------------


def logger():
    return logging.getLogger(CURRENT_TEST)


def files_equal(refdir, testfiles):
    diffs = []
    for f in testfiles:
        base = basename(f)
        reffile = join(refdir, base)
        if not os.path.exists(reffile):
            diffs.append("# Reference file %s does not exist" % reffile)
            continue
        if not os.path.exists(f):
            diffs.append("# Result file %s does not exist" % reffile)
            continue
        with open(f, 'rb') as fp:
            data = fp.read()
            exit_code, diff = whatstyle.compute_unified_diff(reffile,
                                                             data,
                                                             fromfile=reffile,
                                                             tofile=f,
                                                             n=2)
            diff = list(diff)
            if not diff:
                continue
            metricfile = whatstyle.metricfile(reffile)
            if metricfile == reffile:
                with open(reffile, 'rb') as fp:
                    data = bytestr(data)
                    refdata = fp.read()
                    commentpos = refdata.find(b'#')
                    if commentpos >= 0 and data[:commentpos] == refdata[:commentpos]:
                        # Only the formatter name or version is different and that
                        # is no reason to fail the test.
                        continue
            diffs.extend(diff)
    return diffs


def run_spec(spec):
    inputfiles = []
    for rp in spec['files']:
        rp = unistr(rp)
        components = rp.split('/')
        inputfile = join(exampledir(), *components)
        if not exists(inputfile):
            print('Skipping %s which does not exist.' % inputfile)
            continue
        inputfiles.append(inputfile)

    selected_formatters = os.getenv('WHATSTYLE_TEST_FORMATTERS', '').split()

    inputfiles = [unifilename(f) for f in inputfiles]
    exdir = dirname(inputfiles[0])
    formatters = whatstyle.choose_formatters(inputfiles)
    handled_formatters = set()
    alldiffs = []
    for formatter in formatters:
        fmtbasename = basename(os.path.splitext(formatter)[0])
        fmtclass = whatstyle.formatterclass(fmtbasename)
        fmtname = fmtclass.shortname
        if selected_formatters and fmtname not in selected_formatters:
            continue
        spec_fmts = spec.get('formatters')
        if spec_fmts is not None and fmtname not in spec_fmts:
            continue
        if fmtname in handled_formatters:
            # Do not run the same formatter class several times.
            continue
        else:
            handled_formatters.add(fmtname)
        resultdir = join(exdir, unifilename('result_%s' % fmtname))
        if os.path.isdir(resultdir):
            new_resultdir = unifilename(resultdir) + unifilename('_test')
        else:
            new_resultdir = resultdir
        try:
            os.makedirs(new_resultdir)
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise
        resultfiles = run_whatstyle(formatter, spec, inputfiles, new_resultdir)
        if resultdir == new_resultdir:
            continue
        # Compare results against the test reference files
        diffs = files_equal(resultdir, resultfiles)
        if not diffs:
            for f in resultfiles:
                os.remove(f)
            os.rmdir(new_resultdir)
        else:
            alldiffs.append('# Formatter: %s\n' % formatter)
            alldiffs.extend(diffs)
    return ''.join(alldiffs)

# ---------------------------------------------------------------------


def run_whatstyle(formatter, spec, inputfiles, resultdir):
    prog = abspath(join(here(), '..', 'whatstyle.py'))
    fmtname = basename(formatter)
    fmtclass = whatstyle.formatterclass(fmtname)
    fmtinst = fmtclass(formatter)
    stylefile = fmtclass.configfilename

    configfile = join(resultdir, stylefile)

    cmdargs = [
        prog,
        # '-v', '-v', '-v',
        # '--cache=off',
        # '--concurrent=off',
        # '--showdiff',
        # '--color=off',
        # '--debug=all',
        # '--info=all',
        '--noprogress',
        '--difftool=difflib',
        '--formatter',
        formatter,
        '--savemetric',
        '--output',
        configfile
    ]
    references = spec.get('references')
    if references:
        cmdargs.append('--references')
    mode = spec.get('mode')
    if mode:
        cmdargs.append('--mode')
        cmdargs.append(mode)
    extraargs = spec.get('extraargs')
    if extraargs:
        cmdargs.extend(extraargs)

    cmdargs.extend(inputfiles)
    if WHATSTYLE_VIA_SUBPROCESS:
        proc = subprocess.Popen(cmdargs)
        proc.communicate()
        returncode = proc.returncode
    else:
        cmdargs = cmdargs[1:]
        parser = whatstyle.cmdline_parser()
        args = parser.parse_args(cmdargs)
        returncode = whatstyle.run_with_args(args, parser)

    resultfiles = [configfile, whatstyle.metricfile(configfile)]
    if references:
        resultfiles.append(configfile + '_diff')
    srcs, _ = whatstyle.split_reffiles(references, inputfiles)
    for src in srcs:
        inputbase = basename(src)
        # Copy the input source to the result directory
        sourceoutput = join(resultdir, inputbase)
        resultfiles.append(sourceoutput)
        try:
            os.remove(sourceoutput)
        except OSError as exc:
            if exc.errno != errno.ENOENT:
                raise
        if returncode == 0:
            fmtinst.reformat(src, sourceoutput, configfile)
    return resultfiles


def main():
    unittest.main()


if __name__ == '__main__':
    main()
