#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# This script opens a 3-way file comparison of files of which one is pulled out of git.
#
# If for example the argument is
# tests/examples/foundation6/result_tidy/index.html, then:
#
# 1) tests/examples/foundation6/index.html is the basefile,
# 2) the latest version of the argument in git is the second file to compare and
# 3) the argument itself is the third file to compare.
#
# The option '--results' opens diffs for all modified source code result files.
#
# The results from test_examples.py are present in the git repo.
# When changing formatters, algorithms or diff metrics we can run remove_resultdirs.py
# and then test_examples.py.
# Then we can use this script to get an overview if the changes have influenced the
# reformatted source code test files for better or worse by looking at the 3-way diff
# between the original source, the previous test result and the new test result.
# The merge conflict hunks show how the test results differ in context with the original.

from __future__ import print_function

import sys

if (((sys.version_info[0] == 2) and (sys.version_info[1] < 7)) or (
    (sys.version_info[0] == 3) and (sys.version_info[1] < 2))):
    sys.stderr.write('Error: Python 2.7 or when running on Python 3 at least Python 3.2'
                     ' is required to run whatstyle\n')
    sys.exit(1)

import argparse
import threading
import os
import subprocess
import tempfile

from collections import namedtuple
from os.path import abspath, dirname, basename, join

DIFF3_VARNAME = 'WHATSTYLE_DIFF3'

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


def sysfilename(filename):
    if not isinstance(filename, text_type):
        return filename
    try:
        return filename.encode(sys.getfilesystemencoding())
    except UnicodeEncodeError:
        return filename.encode('utf-8')


ExeResult = namedtuple('ExeResult', ['returncode', 'stdout', 'stderr'])


def call_executable(executable, cmdargs):
    cmdargs = [sysfilename(x) for x in cmdargs]
    subprocess.call([executable] + cmdargs)


def run_executable(executable, cmdargs, stdindata=None):
    cmdargs = [sysfilename(x) for x in cmdargs]
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


def git_commits(cwd, relpath):
    """Return a list of commit hashes for relpath sorted from newest to oldest.
    """
    ret, stdout, stderr = gitcmd(['-C', cwd, 'log', '--pretty=oneline', relpath])
    lines = unistr(stdout).splitlines()
    return list([l.split()[0] for l in lines])


def open_for_filename(tool, filename):
    """Creates a temporary file from a git revision of a result file and runs the diff
    tool with it and the parent and other sibling file.
    """
    base = basename(filename)
    parts = [base]
    workdir = dirname(filename)
    firstworkdir = workdir
    prev = None
    while workdir and workdir != prev:
        if os.path.isdir(join(workdir, b'.git')):
            break
        parts.insert(0, basename(workdir))
        prev = workdir
        workdir = dirname(workdir)
    else:
        print('error: did not find .git in %s or its parent directories' % firstworkdir)
        return 2

    relpath = bytestr(os.sep).join(parts)
    commits = git_commits(workdir, relpath)
    content = gitcat(workdir, commits[0], relpath)
    td = sysfilename(tempfile.gettempdir())
    tmpfile = os.path.join(td, b'whatstyle_3gdiff_' + base)
    with open(tmpfile, 'wb') as fp:
        fp.write(content)

    file1 = join(dirname(dirname(filename)), base)
    file2 = tmpfile
    file3 = filename

    t = threading.Thread(target=call_executable, args=[tool, [file1, file2, file3]])
    t.start()
    return t


def modified_results(tool):
    workdir = dirname(dirname(abspath(__file__)))
    exeresult = run_executable('git', ['-C', workdir, 'ls-files', '-z', '-m',
                                       'tests/examples/*/result_*'])
    files = exeresult.stdout.split(b'\x00')
    ignoreexts = bytestr('.cfg .cfg_diff .conf .pro .pro_diff .txt').split()
    result_filenames = []
    for f in files:
        if not f:
            continue
        filename = join(bytestr(workdir), f)
        _, ext = os.path.splitext(filename)
        if not ext or ext in ignoreexts:
            continue
        if not os.path.exists(filename):
            continue
        result_filenames.append(filename)
    diff_for_files(tool, result_filenames)


def diff_for_files(tool, filenames):
    threads = [open_for_filename(tool, f) for f in filenames]
    for t in threads:
        t.join()


def main():
    parser = argparse.ArgumentParser(description='Open a diff tool with three files')
    parser.add_argument('filename', nargs='*', help='input filename')
    # The info and debug options were inspired by rsync.
    parser.add_argument('--tool', help='path of diff tool')
    parser.add_argument('--results', action='store_true', help='show results diffs')

    args = parser.parse_args()
    tool = args.tool
    if tool is None:
        tool = os.environ.get(DIFF3_VARNAME)
    if tool is None:
        parser.error('Please specify the 3-way file comparison tool with --tool or set %s' %
                     DIFF3_VARNAME)
    if args.results:
        return modified_results(tool)
    filenames = args.filename
    if not filenames:
        parser.error('Please specify at least one source file')

    filenames = [os.path.abspath(f) for f in filenames]
    diff_for_files(tool, filenames)
    return 0


if __name__ == '__main__':
    sys.exit(main())
