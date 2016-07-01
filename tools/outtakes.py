#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# This script extracts program crashes from a dump file that was recorded with:
# whatstyle.py --color on --keeptempfiles --debug popenio ...
# The "--color on" is necessary so that it can be distinguished if a space character
# seperates two arguments or is part of the argument itself.
#
# Running 'outtakes.py < dump.txt' will create nested directories like
# outtakes/c_-4_396 that contain the returncode, standard input, output and error and
# an eventually a configfile from the dump.
# It should be sufficient to enter one of these directories and call ./callfmt.sh
# to reproduce a crash.

from __future__ import print_function

import sys

if (((sys.version_info[0] == 2) and (sys.version_info[1] < 7)) or (
    (sys.version_info[0] == 3) and (sys.version_info[1] < 2))):
    sys.stderr.write('Error: Python 2.7 or when running on Python 3 at least Python 3.2'
                     ' is required to run whatstyle\n')
    sys.exit(1)

import argparse
import errno
import os
import re
import shutil
try:
    from shlex import quote as shellquote
except ImportError:
    from pipes import quote as shellquote

from collections import Counter

PY2 = sys.version_info[0] == 2
PY3 = sys.version_info[0] == 3

if PY2:
    text_type = unicode
    binary_type = str
else:
    text_type = str
    binary_type = bytes

DESTDIR = 'outtakes'

HAS_FSCODEC = hasattr(os, 'fsdecode')


def unistr(text, errors='strict'):
    if isinstance(text, text_type):
        return text
    try:
        return text.decode('utf-8', errors=errors)
    except UnicodeDecodeError:
        if HAS_FSCODEC:
            return os.fsdecode(text)
        raise


def bytestr(text):
    if isinstance(text, binary_type):
        return text
    try:
        return text.encode('utf-8')
    except UnicodeEncodeError:
        if HAS_FSCODEC:
            return os.fsencode(text)
        raise


def rawstream(fp):
    if PY3:
        try:
            return fp.buffer
        except AttributeError:
            # There might be a BytesIO behind fp.
            pass
    return fp


def write(s, fp=None):
    """Write s to the binary stream fp (default is stdout).
    """
    efp = fp if fp is not None else sys.stdout
    rawstream(efp).write(bytestr(s))


def outline(s=b'', end=b'\n', fp=None):
    write(bytestr(s) + bytestr(end), fp=fp)


re_ansi = re.compile(br'\x1b\[(?:\d*(?:;\d+)*)m')


def extract_outtakes(filename, maxpercode=0):
    re_lengths = re.compile(br'debug_popen: len\(stdin\):(\d+) => returncode:(-?\d+)'
                            br' len\(stdout\):(\d+) len\(stderr\):(\d+)')
    valid_retcodes = set(range(100))
    retcounters = Counter()
    with open(filename, 'rb') as fp:
        count = 0
        cfgfile = None
        command = None
        while True:
            line = fp.readline()
            if not line:
                break
            prefix = b'debug_popen: '
            m = re_lengths.match(line)
            if m:
                lin, retcode, lout, lerr = [int(x) for x in m.groups()]
                if retcode in valid_retcodes:
                    continue
                if 1 <= maxpercode <= retcounters[retcode]:
                    continue
                retcounters[retcode] += 1
                otdir = os.path.join(DESTDIR, "c_%s_%s" % (str(retcode), str(count)))
                try:
                    os.makedirs(otdir)
                except OSError as exc:
                    if exc.errno != errno.EEXIST:
                        raise
                if command:
                    fname = os.path.join(otdir, 'callfmt.sh')
                    with open(fname, 'wb') as cfg:
                        if lin > 0:
                            command = command + b' < stdin.txt'
                        cfg.write(command + b'\n')
                    os.chmod(fname, 0o755)
                    command = None
                with open(os.path.join(otdir, 'retcode.txt'), 'wb') as cfg:
                    cfg.write(bytestr(str(retcode)) + b'\n')
                if cfgfile:
                    shutil.copyfile(cfgfile, os.path.join(otdir, os.path.basename(cfgfile)))
                    cfgfile = None
                for chan, chanlen in zip(['stdin', 'stdout', 'stderr'], [lin, lout, lerr]):
                    if chanlen == 0:
                        continue
                    line = fp.readline()
                    if line == b'debug_popenio: ' + bytestr(chan) + b':"""\\\n':
                        data = fp.read(chanlen)
                        with open(os.path.join(otdir, '%s.txt' % chan), 'wb') as cfp:
                            cfp.write(data)
                        fp.readline()  # This should be """
            elif line.startswith(prefix):
                line = line[len(prefix):]
                line = line.rstrip(b'\r\n')
                args = re_ansi.split(line)
                cmdargs = []
                if len(args) > 1 and not args[0] and not args[-1]:
                    for idx, arg in enumerate(args[1:-1]):
                        if idx % 2 == 1:
                            if arg == b' ':
                                continue
                            else:
                                write(b"Unexpected debug_popen line: " + line, fp=sys.stderr)
                        uarg = arg.decode('raw-unicode-escape')
                        if idx > 0 and os.path.abspath(arg) and os.path.isfile(arg):
                            cfgfile = uarg
                            uarg = os.path.basename(uarg)
                        cmdargs.append(shellquote(uarg).encode('raw-unicode-escape'))
                if cmdargs:
                    command = b' '.join(cmdargs)
                    count += 1
    return 0


def main():
    parser = argparse.ArgumentParser(description='Extract data from formatter crashes')
    parser.add_argument('filename', help='input dump filename')
    parser.add_argument('--maxpercode',
                        type=int,
                        default=-1,
                        help='only extract this many calls per returncode\n'
                        ' 0 means unlimited (default: 10)')
    args = parser.parse_args()

    if not args.filename:
        parser.error('Please specify the input dump filename')

    return extract_outtakes(args.filename, maxpercode=args.maxpercode)


if __name__ == '__main__':
    sys.exit(main())
