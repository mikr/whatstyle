whatstyle
=============

whatstyle finds a code format style that fits given source files.

Code formatters like clang-format or uncrustify usually need a specific style
definition how to reformat the code.
This program looks at your source code and generates a style definition
that the reformatted source fits its original formatting as closely
as possible.

It should help programmers to begin using a formatting tool right away
without the need to invest hours of reading the formatting tool
documentation.

The currently supported formatters are `clang-format`_, YAPF_, Tidy_, indent_,
`Artistic Style`_, Uncrustify_, scalariform_, scalafmt_, rfmt_ and rustfmt_.

.. _`clang-format`: http://clang.llvm.org/docs/ClangFormat.html
.. _YAPF: https://github.com/google/yapf
.. _Tidy: http://www.html-tidy.org
.. _indent: http://www.freebsd.org/cgi/man.cgi?query=indent
.. _Artistic Style: http://astyle.sourceforge.net
.. _Uncrustify: https://github.com/uncrustify/uncrustify
.. _scalariform: https://github.com/scala-ide/scalariform
.. _scalafmt: https://github.com/olafurpg/scalafmt
.. _rfmt: https://github.com/google/rfmt
.. _rustfmt: https://github.com/rust-lang-nursery/rustfmt

.. contents::

How to run
------------------

First you choose one or more of your source files whose style you find
representative of the style you want to keep.
The source files should cover a wide range of language constructs and
to keep runtime down not exceed a few thousand lines if possible.

You specify the formatter and source files as follows:

.. code-block:: bash

    $ whatstyle.py -f clang-format tests/examples/gumbo-parser/utf8.c

whatstyle will then try different options while reporting intermediate results.
After a while you get back a result like this:

.. code-block:: bash

    ### This style was chosen for your .clang-format - it perfectly matches your sources.
    BasedOnStyle: Google
    AlignAfterOpenBracket: DontAlign
    SpaceAfterCStyleCast: true

Adding the option ``--mode resilient`` will usually add more options to your style so
that a heavily out of shape version of your sources can be better retransformed into your
style.

Style difference effects vs options documentation
-------------------------------------------------

Reading the documention of the invidual options of a formatter takes time and does not
necessarily make clear how an option influences the formatting.
You can try something like this instead:

.. code-block:: bash

    $ whatstyle.py --variants tests/examples/xv6/printf.c

First the best matching style is chosen and then every option is replaced or augmented by
every possible value. All combinations that actually made a difference are grouped and
displayed side by side.
The variant on the left is the original from the best style, on the right is another
option setting that usually makes things worse.
Below the option values, differing code fragments are shown, you can use ``--numhunks``
to show more diff hunks per variation.
Use ``--ansi`` to display the variants table in an ANSI terminal, ``--html`` to open it
in a browser or ``--ansihtml`` for a darker look in a browser.

.. image:: https://raw.github.com/wiki/mikr/whatstyle/images/variants.png
    :target: https://raw.github.com/wiki/mikr/whatstyle/images/variants.png
    :alt: Format Style Variants

Usage scenarios
---------------

For information about some useful scenarios run:

.. code-block:: bash

    $ whatstyle.py --usage-examples

or read the text at the beginning of whatstyle.py.

Requirements
------------------

whatstyle needs at least Python 2.7 and it works as well with
Python 3.2, 3.3, 3.4 and 3.5. Jython and pypy are supported.

Also whatstyle needs at least one code formatter in the current ``PATH``.

The presence of either ``diff`` or ``git`` is optional but useful because the diff quality of
both of them may be better than Python's difflib and this results in a different and usually
better matching style.

This program should work on OS X, Windows, Linux, FreeBSD and OpenBSD.

How it works
------------------

The program basically works by reformatting the source with many
combinations of options and running a diff between the original
and the reformatted source code.

First the major standard styles (e.g. WebKit, GNU, LLVM) are evaluated
and the closest one chosen as a baseline.

Successively every choice of every style option is added to test
if the additional option further reduces the differences.
When no more option settings can improve the result the most appropriate
style has been found.

Among a number of candidate styles with the same diff quality
the one with the least number of explicit options is chosen
to keep the style definition brief.

Author
------

whatstyle was written by `Michael Krause <http://krause-software.com>`_.

License
-------

whatstyle is available under the `MIT license <http://opensource.org/licenses/MIT>`_. See the LICENSE file for more info.

The project specific Open Source licenses of the source codes in tests/examples are present in their respective directories.

See also
--------

`Unformat: Python3 utility to generate a .clang-format file from example code-base <https://github.com/johnmcfarlane/unformat>`_.
