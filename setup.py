import setuptools

with open("README.md", "rt") as f:
    long_description = f.read()

setuptools.setup(
    name="dotted_notation",
    version="0.43.15",
    author="Frey Waid",
    author_email="logophage1@gmail.com",
    description="Dotted notation for safe nested data traversal with optional chaining, pattern matching, and transforms",
    license="MIT",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/freywaid/dotted",
    project_urls={
        "Changelog": "https://github.com/freywaid/dotted/blob/master/CHANGELOG.md",
        "Source":    "https://github.com/freywaid/dotted",
    },
    keywords="dotted nested path pattern-matching json jsonb sql traversal",
    packages=setuptools.find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: Utilities",
    ],
    python_requires='>=3.6',
    install_requires=['pyparsing>=3.0'],
    entry_points={
        'console_scripts': [
            'dq=dotted.cli.main:main',
        ],
    },
    extras_require={
        'yaml': ['PyYAML>=5.0'],
        'toml': ['tomli>=1.0;python_version<"3.11"', 'tomli_w>=1.0'],
        'all': [
            'PyYAML>=5.0',
            'tomli>=1.0;python_version<"3.11"',
            'tomli_w>=1.0',
        ],
    },
)
