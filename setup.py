#!/usr/bin/env python

try:
    from setuptools import setup, Command
except ImportError:
    from distutils.core import setup, Command


class RunTest(Command):

    user_options = []

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        import sys
        import subprocess
        errno = subprocess.call([sys.executable, 'tests/test_whatstyle.py'])
        raise SystemExit(errno)


with open('README.rst') as f:
    readme = f.read()

setup(
    name='whatstyle',
    version='0.1.8',
    url='https://github.com/mikr/whatstyle',
    license='MIT',
    author='Michael Krause',
    author_email='michael@krause-software.com',
    description='whatstyle finds a code format style that fits given source files.',
    long_description=readme,
    py_modules=['whatstyle'],
    cmdclass={'test': RunTest},
    zip_safe=False,
    platforms='any',
    keywords='formatter beautifier clang-format yapf tidy indent astyle uncrustify '
             'scalariform scalafmt rfmt rustfmt',
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Environment :: Console',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Natural Language :: English',
        'Operating System :: OS Independent',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3.2',
        'Programming Language :: Python :: 3.3',
        'Programming Language :: Python :: 3.4',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Topic :: Software Development :: Libraries :: Python Modules',
        'Topic :: Software Development :: Quality Assurance',
        'Topic :: Utilities',
    ],
    entry_points={
        'console_scripts': [
            'whatstyle = whatstyle:main'
        ]
    },
)
