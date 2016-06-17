#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# The code formatter 'indent' exists in several variants with similar
# options.
# The script finds valid command line options given the ASCII manpages
# which must reside in a subdirectory called 'indenters'.
# To get the manpages in ASCII we use: man indent | col -bx
# ---------------------------------------------------------------------

from __future__ import print_function

import os
import re
import sys
from os.path import abspath, dirname, join
from pprint import pprint

PY2 = sys.version_info[0] == 2
PY3 = sys.version_info[0] == 3


def get_option_pairs(filename):
    """Returns a list of option pairs in the file.
    Each option pair falls into three categories:
    1) (-opt, None) : This is a simple option
    2) (-opt, -negative-opt): An option and its opposite option.
    3) (-opt, --longopt): An option with its longopt equivalent (only used by GNU indent).
    """
    re_dash = re.compile(r'^(\s+)(-[a-z0-9]+)(?:, (-[\w-]+))?')
    pairs = []
    with open(filename) as fp:
        for line in fp.readlines():
            line = line.rstrip()
            m = re_dash.match(line)
            if not m:
                continue
            spc, opt1, opt2 = m.groups()
            if len(spc) >= 10:
                # option is part of the description block
                continue
            if opt1 == '-v':
                # -v sort of is the last option for all indent variants.
                break
            pairs.append((opt1, opt2))
    return pairs


def classify_options(optset):
    handled = set()
    pairs = []
    for pair in ['br bl', 'brs bls', 'brf blf', 'bbb nbbb', 'bfde nbfde', 'lps nlps']:
        a, b = pair.split()
        pairs.append((a, b))
        handled.add(a)
        handled.add(b)
    # Ignore these
    handled.update(set('pmt troff ta v nv'.split()))
    # Ignore the predefined styles
    handled.update(set('orig linux kr gnu'.split()))

    optnames = set([opt[1:] for opt in optset])
    options = []
    for optname in optnames:
        if optname in handled:
            continue
        numeric = optname.endswith('n')
        isopposite = optname.startswith('n')
        theopposite = optname[1:] if isopposite else 'n' + optname
        has_opposite = theopposite in optnames
        if has_opposite:
            if not isopposite:
                pairs.append((optname, theopposite))
                handled.add(optname)
                handled.add(theopposite)
        elif numeric:
            optname = optname[:-1]
            options.append((optname, 'int', tuple()))
            handled.add(optname)
        else:
            options.append((optname, 'enum', (optname, )))
            handled.add(optname)
    for a, b in pairs:
        options.append((a, 'enum', (a, b)))

    return options


def parse_options():
    srcdir = join(dirname(abspath(__file__)), 'indenters')
    manpages = reversed(os.listdir(srcdir))
    opts = set()
    for filename in manpages:
        pairs = get_option_pairs(join(srcdir, filename))
        for opt1, opt2 in pairs:
            opts.add(opt1)
            if opt2 is not None and not opt2.startswith('--'):
                opts.add(opt2)
        copts = classify_options(opts)
    pprint(sorted(copts))

# This is the result for the man pages of indent for OS X, OpenBSD, FreeBSD and GNU indent:
# [('bacc', 'enum', ('bacc', 'nbacc')),
#  ('bad', 'enum', ('bad', 'nbad')),
#  ('bap', 'enum', ('bap', 'nbap')),
#  ('bbb', 'enum', ('bbb', 'nbbb')),
#  ('bbo', 'enum', ('bbo', 'nbbo')),
#  ('bc', 'enum', ('bc', 'nbc')),
#  ('bfda', 'enum', ('bfda', 'nbfda')),
#  ('bfde', 'enum', ('bfde', 'nbfde')),
#  ('bli', 'int', ()),
#  ('br', 'enum', ('br', 'bl')),
#  ('brf', 'enum', ('brf', 'blf')),
#  ('brs', 'enum', ('brs', 'bls')),
#  ('bs', 'enum', ('bs',)),
#  ('c', 'int', ()),
#  ('cbi', 'int', ()),
#  ('cd', 'int', ()),
#  ('cdb', 'enum', ('cdb', 'ncdb')),
#  ('cdw', 'enum', ('cdw', 'ncdw')),
#  ('ce', 'enum', ('ce', 'nce')),
#  ('ci', 'int', ()),
#  ('cli', 'int', ()),
#  ('cp', 'int', ()),
#  ('cs', 'enum', ('cs', 'ncs')),
#  ('d', 'int', ()),
#  ('di', 'int', ()),
#  ('dj', 'enum', ('dj', 'ndj')),
#  ('djn', 'enum', ('djn', 'ndjn')),
#  ('eei', 'enum', ('eei', 'neei')),
#  ('ei', 'enum', ('ei', 'nei')),
#  ('fbs', 'enum', ('fbs', 'nfbs')),
#  ('fc1', 'enum', ('fc1', 'nfc1')),
#  ('fca', 'enum', ('fca', 'nfca')),
#  ('fcb', 'enum', ('fcb', 'nfcb')),
#  ('hnl', 'enum', ('hnl', 'nhnl')),
#  ('i', 'int', ()),
#  ('il', 'int', ()),
#  ('ip', 'int', ()),
#  ('l', 'int', ()),
#  ('lc', 'int', ()),
#  ('ldi', 'int', ()),
#  ('lp', 'enum', ('lp', 'nlp')),
#  ('lps', 'enum', ('lps', 'nlps')),
#  ('npro', 'enum', ('npro',)),
#  ('pcs', 'enum', ('pcs', 'npcs')),
#  ('pi', 'int', ()),
#  ('ppi', 'int', ()),
#  ('prs', 'enum', ('prs', 'nprs')),
#  ('psl', 'enum', ('psl', 'npsl')),
#  ('saf', 'enum', ('saf', 'nsaf')),
#  ('sai', 'enum', ('sai', 'nsai')),
#  ('saw', 'enum', ('saw', 'nsaw')),
#  ('sbi', 'int', ()),
#  ('sc', 'enum', ('sc', 'nsc')),
#  ('sob', 'enum', ('sob', 'nsob')),
#  ('ss', 'enum', ('ss', 'nss')),
#  ('st', 'enum', ('st',)),
#  ('ts', 'int', ()),
#  ('ut', 'enum', ('ut', 'nut'))]


def main():
    parse_options()


if __name__ == '__main__':
    sys.exit(main())
