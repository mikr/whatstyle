#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# This script deletes all tests/examples/result_* directories.
# ---------------------------------------------------------------------

from __future__ import print_function

import os
import sys
from os.path import abspath, basename, dirname, join

PY2 = sys.version_info[0] == 2
PY3 = sys.version_info[0] == 3


def main():
    exdir = join(dirname(dirname(abspath(__file__))), 'tests', 'examples')
    print(exdir)
    for path, dirs, files in os.walk(exdir):
        base = basename(path)
        if not base.startswith('result_'):
            continue
        for name in files:
            filename = os.path.join(path, name)
            print("Deleting %s" % filename)
            os.remove(filename)
        print("Deleting %s" % path)
        os.rmdir(path)


if __name__ == '__main__':
    sys.exit(main())
