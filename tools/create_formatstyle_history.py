#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# This script uses a repurposed version of docs/tools/dump_format_style.py
# from http://llvm.org/git/clang.git
# to generate a version history of clang-format options for all commits.
#
# The file include/clang/Format/Format.h contains the ground truth about
# the options for each clang-format version.
#
# This script takes a clang repository as only argument, e.g. llvm/tools/clang
# and needs 'git' in the PATH.
#
# The output is a diff-like representation that can be copied into whatstyle.py
# under CLANG_FORMAT_EVOLUTION to support new clang-formats options.
# The output looks like this:
# ---------------------------------------------------------------------
# # Clang bac016bd3f67ca2f4db1ddc619e611759352b84d
# + BasedOnStyle string
#         LLVM
#         Google
# + AccessModifierOffset int
# + ColumnLimit unsigned
# + MaxEmptyLinesToKeep unsigned
# + PointerAndReferenceBindToType bool
# + SplitTemplateClosingGreater bool
# # Clang 15757316d67cb7a854d53a0402d67ad58347600a
# + IndentCaseLabels bool
# # Clang 3.3
# + SpacesBeforeTrailingComments unsigned
# ...
# ---------------------------------------------------------------------

from __future__ import print_function

import sys

if (((sys.version_info[0] == 2) and (sys.version_info[1] < 7)) or (
    (sys.version_info[0] == 3) and (sys.version_info[1] < 2))):
    sys.stderr.write('Error: Python 2.7 or when running on Python 3 at least Python 3.2'
                     ' is required to run whatstyle\n')
    sys.exit(1)

import argparse
import codecs
import re
import subprocess
from collections import OrderedDict, namedtuple
from pprint import pprint

import dumpformatoptions

FIRST_SUPPORTED_VERSION = '3.5'
# Uncomment the next line to support ancient clang versions.
# FIRST_SUPPORTED_VERSION = None

PY2 = sys.version_info[0] == 2
PY3 = sys.version_info[0] == 3

if PY2:
    text_type = unicode
    binary_type = str
else:
    text_type = str
    binary_type = bytes


def unistr(text):
    if not isinstance(text, text_type):
        text = text.decode('utf-8')
    return text


def bytestr(text):
    if not isinstance(text, binary_type):
        text = text.encode('utf-8')
    return text


def outline(s, fp=None):
    if fp is None:
        fp = sys.stdout
    fp.write(unistr(s + '\n'))


reportmessage = outline


def reporterror(s, fp=None):
    if fp is None:
        fp = sys.stderr
    reportmessage(s, fp=fp)


reportwarning = reporterror

ExeResult = namedtuple('ExeResult', ['returncode', 'stdout', 'stderr'])


def run_executable(executable, cmdargs, stdindata=None):
    proc = subprocess.Popen([executable] + cmdargs,
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    stdout, stderr = proc.communicate(input=stdindata)
    return ExeResult(proc.returncode, stdout, stderr)


def gitcmd(cmdargs):
    return run_executable('git', cmdargs)


def gitcat(cwd, commit, filename):
    """Returns the file content for a commit.
    """
    ret, stdout, stderr = gitcmd(['-C', cwd, 'show', '%s:%s' % (commit, unistr(filename))])
    return stdout


def git_commits(cwd, *relpaths):
    """Return a list of commit hashes for relpath sorted from newest to oldest.
    """
    args = ['-C', cwd, 'log', '--pretty=oneline']
    args.extend(relpaths)
    ret, stdout, stderr = gitcmd(args)
    lines = unistr(stdout).splitlines()
    return list([l.split()[0] for l in lines])


def git_format_commits(cwd):
    """Yields a triple of
    (commithash, content of Format.h, content of docs/conf.py)
    for each commit of Format.h.
    """
    relpaths = 'include/clang/Format/Format.h include/clang/Tooling/Inclusions/IncludeStyle.h'.split()
    for commit in reversed(git_commits(cwd, *relpaths)):
        format_h = unistr(gitcat(cwd, commit, relpaths[0]))
        includestyle_h = unistr(gitcat(cwd, commit, relpaths[1]))
        conf_py = unistr(gitcat(cwd, commit, 'docs/conf.py'))
        yield commit, format_h, includestyle_h, conf_py


def parse_options(format_h_lines, includestyle_h):
    """Parses the options from the lines of Format.h
    by using a modified version of clangs dumpformatoption.py.

    Returns the options and a list of unknown option types.
    """
    unknown_optiontypes = []

    def isknownoptiontype(optiontype):
        is_known_type = optiontype in [
            'bool', 'unsigned', 'int', 'std::string', 'std::vector<std::string>',
            'std::vector<IncludeCategory>', 'std::vector<RawStringFormat>',
            'std::vector<std::pair<std::string, unsigned>>'
        ]
        if is_known_type:
            return True
        elif '::' in optiontype:
            # An attempt at future-proofing this code...
            unknown_optiontypes.append(optiontype)
            return True
        return False

    options = dumpformatoptions.read_options(format_h_lines, isknownoptiontype)
    try:
        options += dumpformatoptions.read_options(includestyle_h, isknownoptiontype)
    except Exception as exc:
        pass
    options = sorted(options, key=lambda x: x.name)
    return options, unknown_optiontypes


def parse_styles(clangworkdir):
    """Returns a list of style definitions for every commit of Format.h in the
     following style, this example is abbreviated and modified for clarity.
    [
    (u'3.3',
     [u'LLVM', u'Google', u'Chromium', u'Mozilla'],
     [('AccessModifierOffset', ('int', [])),
      ('PointerBindsToType', ('bool', []))]),
    (u'3.4',
     [u'LLVM', u'Google', u'Chromium', u'Mozilla', u'Predefined'],
     [('AccessModifierOffset', ('int', [])),
      ('BreakBeforeBraces', ('BraceBreakingStyle', [u'Linux', u'Allman'])),
      ('PointerBindsToType', ('bool', []))])
    ]
    """
    unknown_types = set()

    style_versions = []
    for commit, format_h, includestyle_h, conf_py in git_format_commits(clangworkdir):
        base_formats = []
        release = commit
        # Use the clang version number instead of the commithash
        # if it can be found in clangs conf.py.
        for line in conf_py.splitlines():
            m = re.match("release = '(.*)'", line)
            if m:
                release = m.group(1)

        format_h_lines = format_h.splitlines()
        # Record the format style names
        # e.g. 'FormatStyle getChromiumStyle();' => 'Chromium'
        for line in format_h_lines:
            m = re.match('\s*FormatStyle\s*get(\w+)Style\([^\)]*\);\s*', line)
            if m:
                formatname = m.group(1)
                if formatname != 'No':
                    # NoStyle is not a style named No.
                    base_formats.append(formatname)

        try:
            options, unknown_optiontypes = parse_options(format_h_lines, includestyle_h)
        except Exception:
            continue
        for t in unknown_optiontypes:
            unknown_types.add(t)
        style_options = []
        for opt in options:
            configs = []
            if opt.enum:
                for enumvalue in opt.enum.values:
                    configs.append(re.sub('.*_', '', enumvalue.name))
            elif opt.nested_struct:
                for nestedoption in opt.nested_struct.values:
                    configs.append(nestedoption.name)
            style_options.append((opt.name, (opt.type, configs)))
        style_versions.append((release, base_formats, style_options))

    return style_versions


def generate_style_history(clangworkdir):
    """Prints the style available style configurations
    following the commit history, e.g.:
    # Clang 3.4
    + BasedOnStyle string
            LLVM
            WebKit
    + SpaceAfterControlStatementKeyword bool
    # Clang 3.4
    + TabWidth unsigned
            Never
            ForIndentation
            Always
    # Clang 3.5
    - SpaceAfterControlStatementKeyword bool
    + AllowShortFunctionsOnASingleLine bool
    """
    prev_styles = OrderedDict([])
    changelines = []
    unknown_types = set()

    style_versions = parse_styles(clangworkdir)
    if not style_versions:
        return
    _, latest_base_formats, _ = style_versions[-1]

    supported = FIRST_SUPPORTED_VERSION is None
    for release, base_formats, options in style_versions:
        if release == FIRST_SUPPORTED_VERSION:
            supported = True
        # To keep the history small skip ancient clang versions.
        if not supported:
            continue

        # In old clang-format versions only use base format styles that
        # are still supported in the most recent version.
        base_formats = [b for b in base_formats if b in latest_base_formats]
        options = [('BasedOnStyle', ('string', base_formats))] + options
        options = OrderedDict(options)
        actions = []
        # Delete options that no longer exist since this commit
        for optionname, optiondef in prev_styles.items():
            if optionname not in options:
                actions.append("- %s" % option_rep(optionname, optiondef))

        # Add new options from this commit
        for optionname, optiondef in options.items():
            oldstyledef = prev_styles.get(optionname)
            if optiondef != oldstyledef:
                actions.append("+ %s" % option_rep(optionname, optiondef))
        if actions:
            changelines.append("# Clang " + release)
            changelines.extend(actions)
        prev_styles = options

    outline("\n".join(changelines))
    sys.stdout.flush()
    if unknown_types:
        reportwarning("\nWarning: Not all of the encountered option types could be"
                      " recognized"
                      "\n         while building the history of known clang-format"
                      " options."
                      "\n         This should not be a problem but here they are:")
        for u in unknown_types:
            reportwarning("Unknown option type: %s" % u)


def option_rep(optionname, optiondef):
    """Returns a textual representation of an option.

    option_rep('IndentCaseLabels', ('bool', []))
    => 'IndentCaseLabels bool'

    option_rep('PointerAlignment', ('PointerAlignmentStyle',
               [u'Left', u'Right', u'Middle']))
    => 'PointerAlignment PointerAlignmentStyle
               Left
               Right
               Middle'
    """
    optiontype, configs = optiondef
    fragments = [optionname + ' ' + optiontype]
    for c in configs:
        fragments.append(" " * 8 + c)
    rep = "\n".join(fragments)
    return rep


def main():
    parser = argparse.ArgumentParser(
        description='Create clang-format format history from a clang git repo.')
    parser.add_argument('clangrepo', nargs=1, help='path of a clang repository')
    args = parser.parse_args()
    generate_style_history(args.clangrepo[0])


if __name__ == '__main__':
    if PY3:
        sys.stdout = codecs.getwriter('utf8')(sys.stdout.buffer)
        sys.stderr = codecs.getwriter('utf8')(sys.stderr.buffer)
    sys.exit(main())
