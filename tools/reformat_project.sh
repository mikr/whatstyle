#!/bin/bash
#
# Reformats the source file of the whatstyle project.
#

set -eEu -o pipefail
shopt -s extdebug
IFS=$'\n\t'

TOOLSDIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$TOOLSDIR/.."

while read filename ; do
    set +e
    ( set -x ; yapf --in-place "$filename" )
done <<-ENDOFFILES
tests/test_examples.py
tests/test_whatstyle.py
tools/create_formatstyle_history.py
tools/indent_options.py
tools/outtakes.py
tools/remove_resultdirs.py
tools/show3diffs.py
whatstyle.py
ENDOFFILES
