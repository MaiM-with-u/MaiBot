from setuptools import setup, Extension
from Cython.Build import cythonize

ext_modules = [
    Extension(
        "pagerank",
        sources=["pagerank.pyx", "pr.c"],
        include_dirs=["."],
        libraries=[],
        language="c",
    )
]

setup(
    name="quick_algo",
    ext_modules=cythonize(ext_modules, gdb_debug=True),
)
